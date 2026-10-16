"""问答运行生命周期：先保存用户输入，最后原子提交回答、引用和运行终态。"""

import re
from uuid import UUID, uuid4

from app.adapters import ModelGateway
from app.config import settings
from app.db import execute, json_value, many, one, transaction
from app.errors import DomainError, missing
from app.grounding import grounded_answer
from app.safety import risk_of
from app.security import digest, normalize
from app.services.products import owned, require_assets
from app.storage import Storage


def conversation_view(row):
    return {
        "id": str(row["id"]),
        "title": row["title"],
        "userProductInstanceId": str(row["user_product_instance_id"]),
        "status": row["status"],
        "createdAt": row["created_at"].isoformat(),
    }


def message_view(connection, row):
    citations = many(
        connection,
        (
            "SELECT c.display_meta,s.verification_status FROM message_citations c"
            " JOIN knowledge_chunks k ON k.id=c.knowledge_chunk_id JOIN "
            "document_versions v ON v.id=k.document_version_id JOIN documents d "
            "ON d.id=v.document_id JOIN sources s ON s.id=d.source_id WHERE "
            "c.message_id=:id ORDER BY c.rank"
        ),
        id=row["id"],
    )
    metadata = []
    for citation in citations:
        value = citation["display_meta"]
        if citation["verification_status"] != "verified":
            value["verificationStatus"] = citation["verification_status"]
        metadata.append(value)
    assets = many(
        connection,
        (
            "SELECT a.* FROM message_attachments m JOIN assets a ON "
            "a.id=m.asset_id WHERE m.message_id=:id ORDER BY m.sort_order"
        ),
        id=row["id"],
    )
    from app.services.uploads import asset_view

    return {
        "id": str(row["id"]),
        "role": row["role"],
        "content": row["content"],
        "runId": str(row["run_id"]) if row["run_id"] else None,
        "createdAt": row["created_at"].isoformat(),
        "citations": metadata,
        "assets": [asset_view(asset) for asset in assets],
        "feedback": one(
            connection,
            (
                'SELECT feedback_type AS "feedbackType",note FROM message_feedback '
                "WHERE message_id=:m AND user_id=:u"
            ),
            m=row["id"],
            u=row["user_id"],
        ),
    }


def run_view(connection, run):
    output = one(
        connection, ("SELECT * FROM messages WHERE run_id=:id AND role='assistant'"), id=run["id"]
    )
    return {
        "id": str(run["id"]),
        "conversationId": str(run["conversation_id"]),
        "status": run["status"],
        "startedAt": run["started_at"].isoformat(),
        "finishedAt": run["finished_at"].isoformat() if run["finished_at"] else None,
        "retryOfRunId": str(run["retry_of_run_id"]) if run["retry_of_run_id"] else None,
        "lastError": run["last_error"],
        "message": message_view(connection, output) if output else None,
    }


def get_run(connection, conversation_id, run_id, user):
    owned(connection, "conversations", conversation_id, user)
    run = one(
        connection,
        ("SELECT * FROM assistant_runs WHERE id=:id AND conversation_id=:c AND user_id=:u"),
        id=run_id,
        c=conversation_id,
        u=user,
    )
    if not run:
        missing("RUN_NOT_FOUND")
    return run


def start_run(connection, conversation_id, request, user, key, retry=None):
    """会话行锁串行化请求；重试使用原输入消息，避免历史出现重复提问。"""
    conversation = owned(connection, "conversations", conversation_id, user)
    if conversation["status"] != "active":
        raise DomainError(409, "RESOURCE_CONFLICT", "会话已归档")
    execute(connection, "SELECT id FROM conversations WHERE id=:id FOR UPDATE", id=conversation_id)
    request_hash = digest(
        {"body": request.model_dump(mode="json"), "retry": str(retry) if retry else None}
    )
    old = one(
        connection,
        ("SELECT * FROM assistant_runs WHERE conversation_id=:c AND idempotency_key=:k"),
        c=conversation_id,
        k=key,
    )
    if old:
        if old["request_hash"] != request_hash:
            raise DomainError(409, "IDEMPOTENCY_CONFLICT", "同一请求键已用于不同内容")
        return old, False
    active = one(
        connection,
        ("SELECT id FROM assistant_runs WHERE conversation_id=:c AND status='running'"),
        c=conversation_id,
    )
    if active:
        raise DomainError(409, "RESOURCE_CONFLICT", "已有回答正在生成")
    assets = require_assets(connection, request.assetIds, user)
    if retry:
        previous = get_run(connection, conversation_id, retry, user)
        if previous["status"] not in ("failed", "interrupted", "cancelled"):
            raise DomainError(409, "RESOURCE_CONFLICT", "只能重试失败、取消或中断的回答")
        original = one(
            connection, "SELECT * FROM messages WHERE id=:id", id=previous["input_message_id"]
        )
        attached = many(
            connection,
            ("SELECT asset_id FROM message_attachments WHERE message_id=:id ORDER BY sort_order"),
            id=original["id"],
        )
        if (
            original["content"] != request.content
            or [row["asset_id"] for row in attached] != request.assetIds
        ):
            raise DomainError(409, "IDEMPOTENCY_CONFLICT", "重试必须沿用原始输入")
        input_id = original["id"]
    else:
        input_id = uuid4()
    run = one(
        connection,
        (
            "INSERT INTO "
            "assistant_runs(conversation_id,user_id,status,idempotency_key,requ"
            "est_hash,input_message_id,retry_of_run_id,started_at)"
            " VALUES (:c,:u,'running',:key,:hash,:input,:retry,now()) RETURNING *"
        ),
        c=conversation_id,
        u=user,
        key=key,
        hash=request_hash,
        input=input_id,
        retry=retry,
    )
    if not retry:
        execute(
            connection,
            (
                "INSERT INTO messages(id,conversation_id,user_id,role,content,run_id)"
                " VALUES (:id,:c,:u,'user',:content,:run)"
            ),
            id=input_id,
            c=conversation_id,
            u=user,
            content=request.content,
            run=run["id"],
        )
        for index, asset in enumerate(assets):
            execute(
                connection,
                (
                    "INSERT INTO "
                    "message_attachments(message_id,asset_id,user_id,sort_order) VALUES "
                    "(:m,:a,:u,:n)"
                ),
                m=input_id,
                a=asset["id"],
                u=user,
                n=index,
            )
    return run, True


def lexical_question(question, model):
    """型号已由数据库范围限定；不让每页重复的品牌/型号淹没实际操作意图。"""
    result = normalize(question)
    for value in (model.get("model_code"), model.get("brand_name")):
        if not value or len(value.strip()) < 3:
            continue
        pattern = r"[-_\s]*".join(
            re.escape(part) for part in re.split(r"[-_\s]+", normalize(value))
        )
        result = re.sub(r"(?<![a-z0-9])" + pattern + r"(?![a-z0-9])", " ", result)
    return result.strip() if re.search(r"\w", result) else normalize(question)


def retrieve(model_id, question, vector, limit=5):
    """同模型修订余弦扫描和 trigram 融合；每层证据均须处于 verified 状态。"""
    # 无关键词命中时不按UUID虚构词法贡献；C locale无法识别中文词时也只用有效信号。
    bounded_limit = max(1, min(limit, 10))
    with transaction() as connection:
        model = one(
            connection,
            "SELECT m.model_code,b.name AS brand_name FROM product_models m "
            "JOIN brands b ON b.id=m.brand_id WHERE m.id=:id",
            id=model_id,
        )
        query = lexical_question(question, model) if model else question
        rows = many(
            connection,
            (
                "WITH eligible AS ( SELECT "
                "k.*,v.version_label,v.verified_at,d.title,s.url, "
                "k.embedding <=> CAST(:vector AS vector) AS vector_distance, "
                "word_similarity(:question,k.content) AS text_score FROM "
                "knowledge_chunks k JOIN product_models m ON m.id=k.product_model_id "
                "JOIN document_versions v ON v.id=k.document_version_id JOIN "
                "documents d ON d.id=v.document_id JOIN sources s ON s.id=d.source_id"
                " JOIN document_model_links l ON l.document_version_id=v.id AND "
                "l.product_model_id=m.id WHERE k.product_model_id=:model AND "
                "k.verification_status='verified' AND m.identity_status='verified' "
                "AND m.verification_status='verified' AND "
                "v.verification_status='verified' AND "
                "s.verification_status='verified' AND l.match_type='exact' AND "
                "k.embedding_model=:embedding_model AND "
                "k.embedding_revision=:revision AND k.embedding IS NOT NULL), ranked AS "
                "(SELECT *,row_number() OVER(ORDER BY vector_distance,id) AS vector_rank,"
                "row_number() OVER(ORDER BY text_score DESC,id) AS text_rank FROM eligible) "
                # 长手册重复注意事项容易占满词法排名；保留两个最相关语义候选，
                # 其余名额仍由融合分数选择，不能绕过上面的逐层来源核验。
                "SELECT * FROM ranked ORDER BY CASE WHEN vector_rank<=:semantic_reserve "
                "THEN 0 ELSE 1 END,(1.0/(60+vector_rank)+CASE WHEN text_score>0 "
                "THEN 1.0/(60+text_rank) ELSE 0 END) DESC,id LIMIT :limit"
            ),
            vector=json_value(vector),
            question=query,
            model=model_id,
            embedding_model=settings().embedding_model,
            revision=settings().embedding_revision,
            limit=bounded_limit,
            semantic_reserve=min(2, bounded_limit),
        )
    return rows


def citation_for(chunk, rank):
    return {
        "knowledgeChunkId": str(chunk["id"]),
        "documentVersionId": str(chunk["document_version_id"]),
        "title": chunk["title"] or "官方资料",
        "url": chunk["url"],
        "pageStart": chunk["page_start"],
        "pageEnd": chunk["page_end"],
        "rank": rank,
        "verificationStatus": "verified",
        "versionLabel": chunk["version_label"],
        "verifiedAt": chunk["verified_at"].isoformat() if chunk["verified_at"] else None,
    }


def live_citations(connection, model_id, selected, citations):
    """语义核验可能耗时；最终写回前重查来源状态与被引用正文，不复用过期快照。"""
    if not citations:
        return []
    ids = [UUID(row["knowledgeChunkId"]) for row in citations]
    rows = many(
        connection,
        "SELECT k.*,v.version_label,v.verified_at,d.title,s.url FROM knowledge_chunks k "
        "JOIN product_models m ON m.id=k.product_model_id "
        "JOIN document_versions v ON v.id=k.document_version_id "
        "JOIN documents d ON d.id=v.document_id JOIN sources s ON s.id=d.source_id "
        "JOIN document_model_links l ON l.document_version_id=v.id AND l.product_model_id=m.id "
        "WHERE k.id=ANY(:ids) AND k.product_model_id=:model "
        "AND k.verification_status='verified' AND m.identity_status='verified' "
        "AND m.verification_status='verified' AND v.verification_status='verified' "
        "AND s.verification_status='verified' AND l.match_type='exact' "
        "AND k.embedding_model=:embedding_model AND k.embedding_revision=:revision "
        "AND k.embedding IS NOT NULL",
        ids=ids,
        model=model_id,
        embedding_model=settings().embedding_model,
        revision=settings().embedding_revision,
    )
    fresh = {row["id"]: row for row in rows}
    prior = {row["id"]: row for row in selected}
    if set(fresh) != set(ids) or any(
        any(
            fresh[id][key] != prior[id][key]
            for key in ("content", "document_version_id", "page_start", "page_end")
        )
        for id in ids
    ):
        raise DomainError(409, "SOURCE_UNVERIFIED", "回答引用的资料状态已变化，请重新提问")
    return [citation_for(fresh[UUID(row["knowledgeChunkId"])], row["rank"]) for row in citations]


def complete_run(run, on_token=None):
    """外部调用在事务外执行；结果写回时再次确认账号、实例和运行仍有效。"""
    gateway = ModelGateway()
    gap_summary = None
    try:
        with transaction() as connection:
            conversation = owned(
                connection, "conversations", run["conversation_id"], run["user_id"]
            )
            instance = owned(
                connection, "instances", conversation["user_product_instance_id"], run["user_id"]
            )
            message = one(
                connection, "SELECT * FROM messages WHERE id=:id", id=run["input_message_id"]
            )
            from app.services.memories import confirmed

            user_memories = confirmed(connection, run["user_id"], instance["id"])
            assets = many(
                connection,
                (
                    "SELECT a.* FROM message_attachments m JOIN assets a ON "
                    "a.id=m.asset_id WHERE m.message_id=:id ORDER BY m.sort_order"
                ),
                id=message["id"],
            )
        question = message["content"] or ""
        # 明确危险的文字在读取附件、视觉、向量化或答案生成之前拒绝。
        if assets and risk_of(question) != "high":
            images = [
                (Storage().read(asset["object_key"], 10 * 1024 * 1024), asset["mime_type"])
                for asset in assets
                if asset["mime_type"].startswith("image/")
            ]
            if images:
                observation = gateway.identify(question, images, run["user_id"])
                question += "\n图片描述：" + json_value(observation.model_dump())
        if not question.strip():
            answer, citations = "请先转写录音并确认文字，再发送您的问题。", []
        elif risk_of(question) == "high":
            answer, citations = "此操作存在安全风险，请停止操作并联系官方售后或专业人员。", []
        else:
            vector = gateway.embed([question], run["user_id"])[0]
            chunks = retrieve(instance["product_model_id"], question, vector)
            if not chunks:
                from app.services.gaps import summary_for

                gap_summary = summary_for(gateway, question, run["user_id"])
                answer, citations = (
                    "目前没有足够的已核验资料支持回答。请补充准确型号或等待资料核验完成。",
                    [],
                )
            else:
                evidence = [
                    {"rank": index, "content": chunk["content"]}
                    for index, chunk in enumerate(chunks, 1)
                ]
                if on_token:
                    pieces = []
                    for piece in gateway.answer_stream(
                        question, evidence, run["user_id"], memories=user_memories
                    ):
                        # 取消/超时后不继续向客户端输出，也不提交过期回答。
                        with transaction() as connection:
                            current = one(
                                connection,
                                "SELECT status FROM assistant_runs WHERE id=:id",
                                id=run["id"],
                            )
                            if not current or current["status"] != "running":
                                raise DomainError(409, "RESOURCE_CONFLICT", "回答已停止")
                        pieces.append(piece)
                        on_token(piece)
                    answer = "".join(pieces)
                else:
                    answer = gateway.answer(
                        question, evidence, run["user_id"], memories=user_memories
                    )
                ranks = re.findall(r"\[(\d+)\]", answer)
                # 不让不可信模型输出的超长数字触发 int 上限或引用无关的真实片段。
                cited = {int(number) for number in ranks if len(number) <= 4}
                # “未提及”不能借真实片段变成有证据的否定结论；明确不足时丢弃附会引用。
                insufficient = re.match(
                    r"^(?:[*#>]+\s*)?(?:(?:目前|当前|现有)(?:已核验)?的?)?(?:资料|证据)不足",
                    normalize(answer),
                )
                if not ranks or insufficient:
                    answer, citations = (
                        "现有已核验资料不足以支持这次回答。请补充更具体的问题或相关官方资料。",
                        [],
                    )
                elif any(len(number) > 4 for number in ranks) or any(
                    number < 1 or number > len(chunks) for number in cited
                ):
                    answer, citations = "本次回答未能建立有效证据引用，请重新提问或补充型号。", []
                else:
                    with transaction() as connection:
                        owned(connection, "conversations", run["conversation_id"], run["user_id"])
                        current = one(
                            connection,
                            "SELECT * FROM assistant_runs WHERE id=:id",
                            id=run["id"],
                        )
                        if not current:
                            missing()
                        if current["status"] != "running":
                            return run_view(connection, current)
                    referenced = [evidence[number - 1] for number in sorted(cited)]
                    assessment = gateway.assess_answer(
                        question, answer, referenced, run["user_id"], memories=user_memories
                    )
                    supported, retained = grounded_answer(answer, referenced, assessment)
                    if supported is None:
                        answer, citations = (
                            "现有已核验资料不足以支持这次回答。请补充更具体的问题或相关官方资料。",
                            [],
                        )
                    else:
                        answer = supported
                        citations = [
                            citation_for(chunks[number - 1], number) for number in sorted(retained)
                        ]
        with transaction() as connection:
            owned(connection, "conversations", run["conversation_id"], run["user_id"])
            current = one(
                connection, "SELECT * FROM assistant_runs WHERE id=:id FOR UPDATE", id=run["id"]
            )
            if current["status"] != "running":
                return run_view(connection, current)
            citations = live_citations(
                connection, instance["product_model_id"], chunks if citations else [], citations
            )
            if gap_summary:
                from app.services.gaps import record

                record(connection, instance["product_model_id"], gap_summary)
            from app.services.memories import schedule as schedule_memories

            schedule_memories(connection, message, instance["id"])
            output = one(
                connection,
                (
                    "INSERT INTO "
                    "messages(conversation_id,user_id,role,content,run_id,model_id) "
                    "VALUES (:c,:u,'assistant',:content,:run,:model) RETURNING *"
                ),
                c=run["conversation_id"],
                u=run["user_id"],
                content=answer,
                run=run["id"],
                model=settings().chat_model,
            )
            for citation in citations:
                execute(
                    connection,
                    (
                        "INSERT INTO "
                        "message_citations(message_id,knowledge_chunk_id,rank,display_meta) "
                        "VALUES (:m,:k,:n,CAST(:meta AS jsonb))"
                    ),
                    m=output["id"],
                    k=UUID(citation["knowledgeChunkId"]),
                    n=citation["rank"],
                    meta=json_value(citation),
                )
            execute(
                connection,
                (
                    "UPDATE assistant_runs SET "
                    "status='completed',finished_at=now(),model_id=:model WHERE id=:id"
                ),
                id=run["id"],
                model=settings().chat_model,
            )
            execute(
                connection,
                (
                    "UPDATE conversations SET title=COALESCE(title,:title),updated_at=n"
                    "ow() WHERE id=:id"
                ),
                title=(message["content"] or "附件问答")[:100],
                id=run["conversation_id"],
            )
            return {
                "status": "completed",
                "message": message_view(connection, output),
                "id": str(run["id"]),
            }
    except Exception as error:
        safe_error = error.message if isinstance(error, DomainError) else "回答生成失败，请稍后重试"
        with transaction() as connection:
            execute(
                connection,
                (
                    "UPDATE assistant_runs SET "
                    "status='failed',finished_at=now(),last_error=:e WHERE id=:id AND "
                    "status='running'"
                ),
                e=safe_error,
                id=run["id"],
            )
        if isinstance(error, DomainError):
            raise
        raise DomainError(502, "UPSTREAM_MODEL_ERROR", safe_error) from None
