"单步操作引导：安全/权限由领域服务决定，模型和检查点不能自行推进用户动作。"

from copy import deepcopy
from uuid import UUID, uuid4

from langgraph.types import Command

from app.db import execute, json_value, many, one
from app.errors import DomainError, missing
from app.guidance_agent import plan, review_graph
from app.safety import risk_of
from app.security import idempotent, remember
from app.services.chat import citation_for
from app.services.products import new_job, owned, require_assets

STOP = "该操作风险较高，请停止操作并查阅官方安全说明或联系专业人员。"
STALE = "核验资料已失效，引导已停止。请更新资料后重新创建引导。"


def selected_session(connection, session_id, user, lock=False):
    account = one(
        connection,
        ("SELECT id FROM users WHERE id=:u AND status='active' AND deleted_at IS NULL"),
        u=user,
    )
    if not account:
        raise DomainError(401, "AUTH_REQUIRED", "请重新登录")
    query = (
        "SELECT g.* FROM guidance_sessions g JOIN "
        "user_product_instances i ON i.id=g.user_product_instance_id "
        "WHERE g.id=:id AND g.user_id=:u AND g.deleted_at IS NULL "
        "AND i.deleted_at IS NULL"
    )
    if lock:
        # 固定锁语句仅由服务端布尔开关选择，外部值全部通过绑定参数传入。
        query += " FOR UPDATE OF g"  # noqa: S608
    session = one(connection, query, id=session_id, u=user)
    if not session:
        missing()
    return session


def chunk_rows(connection, ids):
    return many(
        connection,
        (
            "SELECT k.*,d.title,v.version_label,v.verified_at,s.url,"
            "k.verification_status AS chunk_status, "
            "v.verification_status AS version_status,"
            "s.verification_status AS source_status, "
            "m.verification_status AS model_status,l.match_type FROM "
            "knowledge_chunks k JOIN document_versions v ON "
            "v.id=k.document_version_id JOIN documents d ON "
            "d.id=v.document_id JOIN sources s ON s.id=d.source_id JOIN "
            "product_models m ON m.id=k.product_model_id JOIN "
            "document_model_links l ON l.document_version_id=v.id AND "
            "l.product_model_id=m.id WHERE k.id=ANY(CAST(:ids AS "
            "uuid[])) FOR SHARE OF k,v,s,m,l"
        ),
        ids=ids,
    )


def valid_chunk(row):
    return row["match_type"] == "exact" and all(
        row[name] == "verified"
        for name in ("chunk_status", "version_status", "source_status", "model_status")
    )


def step_citations(connection, step_id):
    links = many(
        connection,
        ("SELECT * FROM guidance_step_citations WHERE guidance_step_id=:id ORDER BY rank"),
        id=step_id,
    )
    rows = {
        row["id"]: row
        for row in chunk_rows(connection, [link["knowledge_chunk_id"] for link in links])
    }
    views = []
    for link in links:
        row = rows[link["knowledge_chunk_id"]]
        view = citation_for(row, link["rank"])
        if not valid_chunk(row):
            states = [
                row[name]
                for name in ("chunk_status", "version_status", "source_status", "model_status")
            ]
            view["verificationStatus"] = next(
                (s for s in ("revoked", "rejected", "pending") if s in states), "pending"
            )
        views.append(view)
    return views


def view(connection, session):
    current = None
    if session["status"] in {"active", "paused"}:
        step = one(
            connection,
            "SELECT * FROM guidance_steps WHERE session_id=:s AND step_no=:n",
            s=session["id"],
            n=session["current_step_no"],
        )
        if step:
            current = {
                "id": str(step["id"]),
                "stepNo": step["step_no"],
                "instruction": step["instruction"],
                "successCriteria": step["success_criteria"],
                "riskLevel": step["risk_level"],
                "preconditions": step["preconditions"],
                "safetyNotes": step["safety_notes"],
                "failureOfStepId": str(step["failure_of_step_id"])
                if step["failure_of_step_id"]
                else None,
                "status": step["status"],
                "citations": step_citations(connection, step["id"]),
            }
    return {
        "id": str(session["id"]),
        "topic": session["topic"],
        "status": session["status"],
        "riskLevel": session["risk_level"],
        "version": session["version"],
        "currentStepNo": session["current_step_no"],
        "currentStep": current,
        "notice": session["resume_context"].get("notice"),
    }


def graph_config(session):
    # thread_id由已授权的数据库行生成，客户端不能传任意checkpoint ID。
    return {"configurable": {"thread_id": f"guidance:{session['user_id']}:{session['id']}"}}


def graph_state(session):
    return {
        "userId": str(session["user_id"]),
        "sessionId": str(session["id"]),
        "status": session["status"],
        "version": session["version"],
        "currentStepNo": session["current_step_no"],
        "lastFeedback": None,
    }


def record_wait(connection, session):
    review_graph(connection).invoke(graph_state(session), graph_config(session), durability="sync")
    if session["status"] in {"completed", "failed", "blocked_unsafe"}:
        schedule_cleanup(connection, session)


def schedule_cleanup(connection, session):
    key = f"checkpoint:{session['id']}"
    if one(
        connection,
        "SELECT id FROM jobs WHERE job_type='checkpoint_cleanup' "
        "AND user_id=:u AND idempotency_key=:key",
        u=session["user_id"],
        key=key,
    ):
        return
    job = new_job(
        connection,
        "checkpoint_cleanup",
        {"sessionId": str(session["id"])},
        user=session["user_id"],
        key=key,
    )
    execute(
        connection,
        "UPDATE jobs SET available_at=now()+interval '7 days' WHERE id=:id",
        id=job["id"],
    )


def accept_interrupt(connection, session, decision):
    graph = review_graph(connection)
    snapshot = graph.get_state(graph_config(session))
    state = snapshot.values
    if not snapshot.next or not checkpoint_matches(state, session):
        raise DomainError(409, "RESOURCE_CONFLICT", "引导恢复点与当前版本不一致，请重新加载")
    graph.invoke(Command(resume=decision), graph_config(session), durability="sync")


def checkpoint_matches(state, session):
    return all(
        state.get(key) == value
        for key, value in graph_state(session).items()
        if key != "lastFeedback"
    )


def update_session(connection, session, status, step_no, notice=None):
    return one(
        connection,
        (
            "UPDATE guidance_sessions SET status=CAST(:status AS varchar),"
            "current_step_no=:n,version=version+1, risk_level=CASE WHEN "
            "CAST(:status AS varchar)='blocked_unsafe' THEN 'high' ELSE risk_level END, "
            "resume_context=CAST(:context AS jsonb) WHERE id=:id "
            "RETURNING *"
        ),
        status=status,
        n=step_no,
        context=json_value({"notice": notice}),
        id=session["id"],
    )


def safe_plan(connection, session, output, chunks):
    rank = risk_of(session["topic"])
    for step in output.steps:
        levels = [risk_of(step.instruction)] + [
            risk_of(note, warning=True) for note in step.safetyNotes
        ]
        if "high" in levels:
            rank = "high"
        elif "medium" in levels and rank != "high":
            rank = "medium"
    if rank == "high" or output.riskLevel == "high":
        return "high"
    if not chunks:
        raise DomainError(422, "EVIDENCE_INSUFFICIENT", "没有足够的核验资料支持操作引导")
    instance = owned(
        connection, "instances", session["user_product_instance_id"], session["user_id"]
    )
    refs = {number for step in output.steps for number in step.citationRanks}
    if any(number > len(chunks) for number in refs):
        raise DomainError(422, "EVIDENCE_INSUFFICIENT", "引导引用与核验资料不匹配")
    selected = [chunks[number - 1]["id"] for number in refs]
    current = chunk_rows(connection, selected)
    if len(current) != len(selected) or any(
        not valid_chunk(row) or row["product_model_id"] != instance["product_model_id"]
        for row in current
    ):
        raise DomainError(422, "EVIDENCE_INSUFFICIENT", "引导证据已失效，请先更新资料")
    return (
        "medium"
        if rank == "medium"
        or output.riskLevel == "medium"
        or any(step.riskLevel == "medium" for step in output.steps)
        else "low"
    )


def store_step(connection, session, item, chunks, number, risk, failure=None, existing=None):
    if risk == "medium" or item.riskLevel == "medium":
        execute(
            connection,
            "UPDATE guidance_sessions SET risk_level='medium' WHERE id=:id AND risk_level='low'",
            id=session["id"],
        )
    values = {
        "s": session["id"],
        "u": session["user_id"],
        "n": number,
        "instruction": item.instruction,
        "criteria": item.successCriteria,
        "risk": "medium" if risk == "medium" else item.riskLevel,
        "pre": json_value(item.preconditions),
        "notes": json_value(item.safetyNotes),
        "failure": failure,
    }
    if existing:
        execute(
            connection,
            "DELETE FROM guidance_step_citations WHERE guidance_step_id=:id",
            id=existing,
        )
        row = one(
            connection,
            (
                "UPDATE guidance_steps SET instruction=:instruction,"
                "success_criteria=:criteria,risk_level=:risk, "
                "preconditions=CAST(:pre AS jsonb),safety_notes=CAST(:notes "
                "AS jsonb) WHERE id=:id RETURNING *"
            ),
            id=existing,
            **values,
        )
    else:
        row = one(
            connection,
            (
                "INSERT INTO guidance_steps(session_id,user_id,step_no,"
                "instruction,success_criteria,risk_level, preconditions,"
                "safety_notes,failure_of_step_id,status) VALUES (:s,:u,:n,"
                ":instruction,:criteria,:risk, CAST(:pre AS jsonb),"
                "CAST(:notes AS jsonb),:failure,'pending') RETURNING *"
            ),
            **values,
        )
    for number in item.citationRanks:
        execute(
            connection,
            (
                "INSERT INTO guidance_step_citations(guidance_step_id,"
                "knowledge_chunk_id,rank) VALUES (:s,:k,:n)"
            ),
            s=row["id"],
            k=chunks[number - 1]["id"],
            n=number,
        )
    return row


def create(connection, user, request, key):
    owned(connection, "instances", request.userProductInstanceId, user)
    body = request.model_dump(mode="json")
    old = idempotent(connection, user, "guidance/create", key, body)
    if old:
        return view(connection, selected_session(connection, old["resource_id"], user))
    risk = risk_of(request.topic)
    session = one(
        connection,
        (
            "INSERT INTO guidance_sessions(id,user_id,"
            "user_product_instance_id,topic,status,risk_level,"
            "resume_context) VALUES (:id,:u,:i,:topic,:status,:risk,"
            "CAST(:context AS jsonb)) RETURNING *"
        ),
        id=uuid4(),
        u=user,
        i=request.userProductInstanceId,
        topic=request.topic,
        status="blocked_unsafe" if risk == "high" else "paused",
        risk=risk,
        context=json_value({"notice": STOP if risk == "high" else None}),
    )
    if risk != "high":
        output, chunks = plan(connection, session)
        risk = safe_plan(connection, session, output, chunks)
        if risk == "high":
            session = one(
                connection,
                (
                    "UPDATE guidance_sessions SET status='blocked_unsafe',"
                    "risk_level='high',resume_context=CAST(:ctx AS jsonb) WHERE "
                    "id=:id RETURNING *"
                ),
                ctx=json_value({"notice": STOP}),
                id=session["id"],
            )
        else:
            for number, item in enumerate(output.steps, 1):
                store_step(connection, session, item, chunks, number, risk)
            execute(
                connection,
                ("UPDATE guidance_steps SET status='active' WHERE session_id=:s AND step_no=1"),
                s=session["id"],
            )
            session = one(
                connection,
                (
                    "UPDATE guidance_sessions SET status='active',"
                    "current_step_no=1,risk_level=:risk WHERE id=:id RETURNING *"
                ),
                risk=risk,
                id=session["id"],
            )
            record_wait(connection, session)
    result = view(connection, session)
    remember(connection, user, "guidance/create", key, body, session["id"], result, 201)
    return result


def feedback(connection, session_id, user, request, key):
    session = selected_session(connection, session_id, user)
    body = request.model_dump(mode="json")
    operation = f"guidance/{session_id}/feedback"
    old = idempotent(connection, user, operation, key, body)
    if old:
        return replay_view(connection, old["response_snapshot"])
    session = selected_session(connection, session_id, user, lock=True)
    if (
        session["status"] != "active"
        or session["version"] != request.expectedVersion
        or session["current_step_no"] != request.currentStepNo
    ):
        raise DomainError(409, "RESOURCE_CONFLICT", "引导状态或步骤版本已变化，请刷新")
    step = one(
        connection,
        "SELECT * FROM guidance_steps WHERE session_id=:s AND step_no=:n",
        s=session_id,
        n=request.currentStepNo,
    )
    assets = require_assets(connection, request.assetIds, user)
    if any(not asset["mime_type"].startswith("image/") for asset in assets):
        raise DomainError(422, "VALIDATION_ERROR", "引导反馈仅接受图片；录音请先转写并确认文字")
    if (
        request.result == "completed"
        and (step["risk_level"] == "medium" or step["preconditions"])
        and not request.preconditionsConfirmed
    ):
        raise DomainError(422, "VALIDATION_ERROR", "请先确认本步骤的安全前提")
    accept_interrupt(connection, session, body)
    # 保存每次用户反馈的图片；序号从已有最大值递增，避免第二次上传
    # 与第一次sort_order=0冲突后被ON CONFLICT静默丢弃。
    start = one(
        connection,
        (
            "SELECT COALESCE(max(sort_order),-1)+1 AS n FROM "
            "guidance_step_attachments WHERE guidance_step_id=:id"
        ),
        id=step["id"],
    )["n"]
    if start + len(assets) > 20:
        raise DomainError(422, "VALIDATION_ERROR", "单步反馈图片数量已达上限")
    for index, asset in enumerate(assets):
        execute(
            connection,
            (
                "INSERT INTO guidance_step_attachments(guidance_step_id,"
                "asset_id,user_id,sort_order) VALUES (:s,:a,:u,:n) ON "
                "CONFLICT DO NOTHING"
            ),
            s=step["id"],
            a=asset["id"],
            u=user,
            n=start + index,
        )
    citations = step_citations(connection, step["id"])
    if risk_of(request.note or "") == "high":
        updated = block_session(connection, session)
    elif request.result != "paused" and (
        not citations or any(row["verificationStatus"] != "verified" for row in citations)
    ):
        updated = update_session(connection, session, "failed", None, STALE)
        execute(connection, "UPDATE guidance_steps SET status='failed' WHERE id=:id", id=step["id"])
    elif request.result == "paused":
        updated = update_session(connection, session, "paused", request.currentStepNo)
    elif request.result == "completed":
        execute(
            connection,
            (
                "UPDATE guidance_steps SET status='completed',"
                "feedback=CAST(:f AS jsonb) WHERE id=:id"
            ),
            f=json_value(body),
            id=step["id"],
        )
        following = one(
            connection,
            (
                "SELECT * FROM guidance_steps WHERE session_id=:s AND "
                "status='pending' ORDER BY step_no LIMIT 1"
            ),
            s=session_id,
        )
        if following:
            execute(
                connection,
                "UPDATE guidance_steps SET status='active' WHERE id=:id",
                id=following["id"],
            )
        updated = update_session(
            connection,
            session,
            "active" if following else "completed",
            following["step_no"] if following else None,
        )
    else:
        failed = one(
            connection,
            ("SELECT count(*) AS n FROM guidance_steps WHERE session_id=:s AND status='failed'"),
            s=session_id,
        )["n"]
        if request.result == "failed" and failed >= 2:
            updated = update_session(
                connection, session, "failed", None, "多次排查仍未完成，请暂停并联系官方支持。"
            )
            execute(
                connection, "UPDATE guidance_steps SET status='failed' WHERE id=:id", id=step["id"]
            )
        else:
            # 模型只能提出分支，不能自动认定动作已经完成。
            observation = observe_assets(assets, session, step) if assets else None
            # 照片只作观察，但发现危险必须先停，不依赖后续模型自行分级。
            if observation and risk_of(observation["observation"]) == "high":
                risk = "high"
            else:
                output, chunks = plan(
                    connection,
                    session,
                    mode="clarify" if request.result == "unclear" else "diagnose",
                    note=request.note,
                    current=step["instruction"],
                    observation=observation,
                )
                risk = safe_plan(connection, session, output, chunks)
            if risk == "high":
                updated = block_session(connection, session)
            elif request.result == "unclear":
                if len(output.steps) != 1:
                    raise DomainError(502, "UPSTREAM_MODEL_ERROR", "澄清必须只针对当前单步动作")
                item = output.steps[0]
                item.preconditions = list(dict.fromkeys(step["preconditions"] + item.preconditions))
                item.safetyNotes = list(dict.fromkeys(step["safety_notes"] + item.safetyNotes))
                # 澄清不能降级当前动作风险，或移除已展示的安全前提。
                risk = "medium" if step["risk_level"] == "medium" else risk
                store_step(
                    connection, session, item, chunks, step["step_no"], risk, existing=step["id"]
                )
                updated = update_session(connection, session, "active", step["step_no"])
            else:
                execute(
                    connection,
                    (
                        "UPDATE guidance_steps SET status='failed',feedback=CAST(:f "
                        "AS jsonb) WHERE id=:id"
                    ),
                    f=json_value(body),
                    id=step["id"],
                )
                execute(
                    connection,
                    (
                        "UPDATE guidance_steps SET status='skipped' WHERE "
                        "session_id=:s AND status='pending'"
                    ),
                    s=session_id,
                )
                number = one(
                    connection,
                    "SELECT max(step_no)+1 AS n FROM guidance_steps WHERE session_id=:s",
                    s=session_id,
                )["n"]
                for index, item in enumerate(output.steps):
                    store_step(
                        connection, session, item, chunks, number + index, risk, failure=step["id"]
                    )
                execute(
                    connection,
                    (
                        "UPDATE guidance_steps SET status='active' WHERE "
                        "session_id=:s AND step_no=:n"
                    ),
                    s=session_id,
                    n=number,
                )
                updated = update_session(connection, session, "active", number)
    record_wait(connection, updated)
    result = view(connection, updated)
    remember(connection, user, operation, key, body, session_id, result, 200)
    return result


def resume(connection, session_id, user, request, key):
    selected_session(connection, session_id, user)
    body = request.model_dump(mode="json")
    operation = f"guidance/{session_id}/resume"
    old = idempotent(connection, user, operation, key, body)
    if old:
        return replay_view(connection, old["response_snapshot"])
    session = selected_session(connection, session_id, user, lock=True)
    if session["status"] != "paused" or session["version"] != request.expectedVersion:
        raise DomainError(409, "RESOURCE_CONFLICT", "只能恢复当前版本的暂停引导")
    snapshot = review_graph(connection).get_state(graph_config(session))
    if not checkpoint_matches(snapshot.values, session):
        raise DomainError(409, "RESOURCE_CONFLICT", "暂停恢复点已缺失，请重新创建引导")
    citations = view(connection, session)["currentStep"]["citations"]
    valid = citations and all(row["verificationStatus"] == "verified" for row in citations)
    updated = update_session(
        connection,
        session,
        "active" if valid else "failed",
        session["current_step_no"] if valid else None,
        None if valid else STALE,
    )
    record_wait(connection, updated)
    result = view(connection, updated)
    remember(connection, user, operation, key, body, session_id, result, 200)
    return result


def block_session(connection, session):
    execute(
        connection,
        "UPDATE guidance_steps SET status='skipped' WHERE session_id=:s "
        "AND status IN ('active','pending')",
        s=session["id"],
    )
    return update_session(connection, session, "blocked_unsafe", None, STOP)


def observe_assets(assets, session, step):
    from app.adapters import ModelGateway
    from app.storage import Storage

    images = [
        (Storage().read(asset["object_key"], 10 * 1024 * 1024), asset["mime_type"])
        for asset in assets
    ]
    return (
        ModelGateway()
        .observe_guidance(images, session["topic"], step["instruction"], session["user_id"])
        .model_dump()
    )


def replay_view(connection, snapshot):
    """重放不再执行动作，但旧响应中的证据核验标签必须反映最新撤销状态。"""
    result = deepcopy(snapshot)
    step = result.get("currentStep")
    if not step:
        return result
    rows = {
        row["id"]: row
        for row in chunk_rows(connection, [UUID(c["knowledgeChunkId"]) for c in step["citations"]])
    }
    for citation in step["citations"]:
        row = rows.get(UUID(citation["knowledgeChunkId"]))
        if not row or not valid_chunk(row):
            citation["verificationStatus"] = (
                "revoked" if row and row["source_status"] == "revoked" else "pending"
            )
            result["notice"] = STALE
    return result


def cleanup_checkpoint(job):
    """仅终态七天后的本人引导可清理；暂停恢复点不能因时间到期丢失。"""
    from app.db import transaction
    from app.services.queue import fenced, finish

    with transaction() as connection:
        fenced(connection, job)
        session = one(
            connection,
            "SELECT * FROM guidance_sessions WHERE id=:id AND user_id=:u FOR UPDATE",
            id=UUID(job["payload"]["sessionId"]),
            u=job["user_id"],
        )
        if not session or session["status"] not in {"completed", "failed", "blocked_unsafe"}:
            finish(connection, job, "cancelled")
            return
        old_enough = one(
            connection,
            "SELECT id FROM guidance_sessions WHERE id=:id AND updated_at<now()-interval '7 days'",
            id=session["id"],
        )
        if not old_enough:
            # 排队时钟或租约恢复不能使清理提前；重新安排而不是删除活跃数据。
            execute(
                connection,
                "UPDATE jobs SET status='queued',available_at=now()+interval '1 day',"
                "lease_token=NULL,lease_until=NULL WHERE id=:id",
                id=job["id"],
            )
            return
        review = f"guidance:{session['user_id']}:{session['id']}"
        planner = f"guidance-plan:{session['user_id']}:{session['id']}:%"
        # 表名是固定白名单；UUID生成的前缀没有LIKE通配符，不能清理其他用户线程。
        for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            execute(
                connection,
                f"DELETE FROM {table} WHERE thread_id=:review OR thread_id LIKE :planner",  # noqa: S608
                review=review,
                planner=planner,
            )
        finish(connection, job)
