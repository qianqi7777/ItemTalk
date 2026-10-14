"""Versioned public/private manuals with immutable evidence and chapter inputs."""

import re
from uuid import UUID

from app.adapters import ModelGateway
from app.db import execute, json_value, many, one, transaction
from app.errors import DomainError, missing
from app.security import digest
from app.services.chat import citation_for
from app.services.manual_plans import PROFILE, generation_signature, plans, style_context
from app.services.products import new_job, owned
from app.services.queue import fenced, finish, progress

CHAPTERS = (
    ("overview", "产品概览", "产品的主要功能和使用范围是什么？"),
    ("getting-started", "开始使用", "如何准备并开始使用这件产品？"),
    ("safety-care", "安全与维护", "使用和日常维护有哪些安全注意事项？"),
)


def evidence(connection, model_id):
    return many(
        connection,
        (
            "SELECT k.*,d.title,v.version_label,v.verified_at,s.url FROM "
            "knowledge_chunks k JOIN product_models m ON "
            "m.id=k.product_model_id JOIN document_versions v ON "
            "v.id=k.document_version_id JOIN documents d ON d.id=v.document_id "
            "JOIN sources s ON s.id=d.source_id JOIN document_model_links l ON "
            "l.product_model_id=m.id AND l.document_version_id=v.id WHERE "
            "m.id=:id AND m.identity_status='verified' AND "
            "m.verification_status='verified' AND "
            "k.verification_status='verified' AND "
            "v.verification_status='verified' AND "
            "s.verification_status='verified' AND l.match_type='exact' ORDER "
            "BY v.id,k.chunk_no FOR SHARE OF k,m,v,s,l"
        ),
        id=model_id,
    )


def signature_for(chunks):
    return digest(
        [{key: row[key] for key in ("id", "content", "document_version_id")} for row in chunks]
    )


def personal_context(connection, user, instance_id):
    from datetime import UTC

    rows = many(
        connection,
        (
            "SELECT id,memory_type,scope,content,confirmed_at,expires_at FROM user_memories "
            "WHERE user_id=:u AND consent_status='confirmed' AND deleted_at IS NULL "
            "AND (expires_at IS NULL OR expires_at>now()) AND "
            "(scope='global' OR user_product_instance_id=:i) ORDER BY id FOR SHARE"
        ),
        u=user,
        i=instance_id,
    )
    return [
        {
            "id": str(row["id"]),
            "memoryType": row["memory_type"],
            "scope": row["scope"],
            "content": row["content"],
            "confirmedAt": row["confirmed_at"].astimezone(UTC).isoformat(),
            "expiresAt": row["expires_at"].astimezone(UTC).isoformat()
            if row["expires_at"]
            else None,
        }
        for row in rows
    ]


def schedule(connection, model_id, retry=None, instance_id=None, user=None):
    visibility = "private" if instance_id else "public"
    if instance_id:
        instance = owned(connection, "instances", instance_id, user)
        if instance["product_model_id"] != model_id:
            missing()
        execute(
            connection,
            "SELECT id FROM user_product_instances WHERE id=:i FOR UPDATE",
            i=instance_id,
        )
    else:
        execute(connection, "SELECT id FROM product_models WHERE id=:id FOR UPDATE", id=model_id)
    chunks = evidence(connection, model_id)
    if not chunks:
        return None
    context = personal_context(connection, user, instance_id) if instance_id else []
    signature = generation_signature(chunks, context)
    scope = {"m": model_id, "v": visibility, "u": user if instance_id else None, "i": instance_id}
    previous = one(
        connection,
        (
            "SELECT * FROM manuals WHERE product_model_id=:m AND visibility=:v "
            "AND owner_user_id IS NOT DISTINCT FROM CAST(:u AS uuid) "
            "AND user_product_instance_id IS NOT DISTINCT FROM CAST(:i AS uuid) AND "
            "evidence_hash=:h"
        ),
        **scope,
        h=signature,
    )
    if previous:
        if retry:
            old = one(connection, "SELECT * FROM jobs WHERE id=:j", j=retry)
            if (
                previous["job_id"] != retry
                or not old
                or old["status"] not in {"failed", "partial_success"}
            ):
                raise DomainError(409, "RESOURCE_CONFLICT", "只能重试本版本失败或部分完成的任务")
            job = enqueue(connection, previous, retry=retry)
            return {**previous, "job_id": job["id"], "status": "pending"}
        if previous["status"] == "ready" and not previous["is_current"]:
            # 设置撤销后可能回到历史输入；复用已完成版本并切回当前，避免留在失效设置。
            execute(
                connection,
                (
                    "UPDATE manuals SET is_current=false WHERE product_model_id=:m "
                    "AND visibility=:v "
                    "AND owner_user_id IS NOT DISTINCT FROM CAST(:u AS uuid) "
                    "AND user_product_instance_id IS NOT DISTINCT FROM CAST(:i AS uuid) "
                    "AND is_current"
                ),
                **scope,
            )
            execute(
                connection, "UPDATE manuals SET is_current=true WHERE id=:id", id=previous["id"]
            )
            return {**previous, "is_current": True}
        return previous
    if retry:
        raise DomainError(409, "RESOURCE_CONFLICT", "生成输入已变化，请创建新的说明书版本")
    version = one(
        connection,
        (
            "SELECT COALESCE(max(version),0)+1 AS n FROM manuals WHERE product_model_id=:m "
            "AND visibility=:v AND user_product_instance_id IS NOT DISTINCT FROM CAST(:i"
            " AS uuid) "
            "AND owner_user_id IS NOT DISTINCT FROM CAST(:u AS uuid)"
        ),
        **scope,
    )["n"]
    manual = one(
        connection,
        (
            "INSERT INTO manuals(product_model_id,visibility,version,status,evidence_hash,"
            "owner_user_id,user_product_instance_id,generation_profile,context_snapshot,context_hash)"
            " "
            "VALUES(:m,:v,:version,'pending',:h,:u,:i,:profile,CAST(:context AS "
            "jsonb),:ch) RETURNING *"
        ),
        **scope,
        version=version,
        h=signature,
        profile=PROFILE,
        context=json_value(context),
        ch=digest(context) if instance_id else None,
    )
    for number, plan in enumerate(plans(chunks, CHAPTERS, context)):
        section = one(
            connection,
            (
                "INSERT INTO "
                "manual_sections(manual_id,section_key,title,sort_order,generation_status,"
                "evidence_ids,generation_prompt,input_hash) VALUES(:id,:key,:title,:n,'pending',"
                ":ids,:prompt,:hash) RETURNING *"
            ),
            id=manual["id"],
            key=plan["key"],
            title=plan["title"],
            n=number,
            ids=[chunk["id"] for chunk in plan["chunks"]],
            prompt=plan["prompt"],
            hash=plan["hash"],
        )
        # 输入未变化的ready章节可跨版本复制，先复制引用再发布，绝不改写原章节。
        prior = one(
            connection,
            (
                "SELECT s.* FROM manual_sections s JOIN manuals m ON m.id=s.manual_id "
                "WHERE m.product_model_id=:m AND m.visibility=:v "
                "AND m.owner_user_id IS NOT DISTINCT FROM CAST(:u AS uuid) "
                "AND m.user_product_instance_id IS NOT DISTINCT FROM CAST(:i AS uuid) "
                "AND s.section_key=:key AND s.input_hash=:hash AND s.generation_status='ready' "
                "ORDER BY m.version DESC LIMIT 1"
            ),
            **scope,
            key=plan["key"],
            hash=plan["hash"],
        )
        if prior:
            execute(
                connection,
                (
                    "INSERT INTO manual_section_citations(manual_section_id,"
                    "knowledge_chunk_id,rank) "
                    "SELECT :new,knowledge_chunk_id,rank FROM manual_section_citations "
                    "WHERE manual_section_id=:old"
                ),
                new=section["id"],
                old=prior["id"],
            )
            execute(
                connection,
                (
                    "UPDATE manual_sections SET "
                    "body=:body,personal_notes=CAST(:notes AS jsonb),"
                    "generation_status='ready',source_section_id=:old "
                    "WHERE id=:id"
                ),
                body=prior["body"],
                notes=json_value(prior["personal_notes"]),
                old=prior["id"],
                id=section["id"],
            )
    job = enqueue(connection, manual)
    return {**manual, "job_id": job["id"]}


def enqueue(connection, manual, retry=None, section_ids=None):
    active = one(
        connection,
        (
            "SELECT id FROM jobs WHERE payload->>'manualId'=:m AND job_type='manual' "
            "AND status IN ('queued','running','waiting_for_user')"
        ),
        m=str(manual["id"]),
    )
    if active:
        raise DomainError(409, "RESOURCE_CONFLICT", "已有章节任务正在生成")
    payload = {"manualId": str(manual["id"])}
    if manual["visibility"] == "private":
        payload["instanceId"] = str(manual["user_product_instance_id"])
    if section_ids:
        payload["sectionIds"] = [str(s) for s in section_ids]
    job = new_job(connection, "manual", payload, user=manual["owner_user_id"], retry=retry)
    execute(
        connection,
        "UPDATE manuals SET job_id=:j,status='pending' WHERE id=:m",
        j=job["id"],
        m=manual["id"],
    )
    return job


def enqueue_sections(connection, manual_id, user, section_ids):
    manual = authorized(connection, manual_id, user)
    lock_scope(connection, manual)
    validate_inputs(connection, manual)
    selected = many(
        connection,
        "SELECT * FROM manual_sections WHERE manual_id=:m AND id=ANY(:ids)",
        m=manual_id,
        ids=section_ids,
    )
    if len(selected) != len(section_ids):
        missing()
    if any(s["generation_status"] == "ready" for s in selected):
        raise DomainError(409, "RESOURCE_CONFLICT", "已完成章节不可改写，请创建新版本")
    return enqueue(connection, manual, section_ids=section_ids)


def lock_scope(connection, manual):
    if manual["visibility"] == "private":
        owned(connection, "instances", manual["user_product_instance_id"], manual["owner_user_id"])
        execute(
            connection,
            "SELECT id FROM user_product_instances WHERE id=:i FOR UPDATE",
            i=manual["user_product_instance_id"],
        )
    else:
        execute(
            connection,
            "SELECT id FROM product_models WHERE id=:m FOR UPDATE",
            m=manual["product_model_id"],
        )


def validate_inputs(connection, manual):
    context = []
    if manual["visibility"] == "private":
        owned(connection, "instances", manual["user_product_instance_id"], manual["owner_user_id"])
        context = personal_context(
            connection, manual["owner_user_id"], manual["user_product_instance_id"]
        )
    chunks = evidence(connection, manual["product_model_id"])
    signature = (
        signature_for(chunks[:12])
        if manual["generation_profile"] == "legacy"
        else generation_signature(chunks, context)
    )
    if not chunks or signature != manual["evidence_hash"]:
        raise DomainError(409, "SOURCE_UNVERIFIED", "资料或个人设置已变化，请生成新的说明书版本")
    return chunks[:12] if manual["generation_profile"] == "legacy" else chunks


def authorized(connection, manual_id, user):
    row = one(
        connection,
        ("SELECT * FROM manuals WHERE id=:id AND (visibility='public' OR owner_user_id=:u)"),
        id=manual_id,
        u=user,
    )
    if not row:
        missing()
    if row["visibility"] == "private":
        owned(connection, "instances", row["user_product_instance_id"], user)
    return row


def section_view(connection, section):
    citations = many(
        connection,
        (
            "SELECT k.*,d.title,v.version_label,v.verified_at,s.url,c.rank, "
            "k.verification_status AS chunk_status,v.verification_status AS "
            "version_status, s.verification_status AS source_status,"
            "m.verification_status AS model_status FROM "
            "manual_section_citations c JOIN knowledge_chunks k ON "
            "k.id=c.knowledge_chunk_id JOIN document_versions v ON "
            "v.id=k.document_version_id JOIN documents d ON d.id=v.document_id "
            "JOIN sources s ON s.id=d.source_id JOIN product_models m ON "
            "m.id=k.product_model_id WHERE c.manual_section_id=:id ORDER BY "
            "c.rank"
        ),
        id=section["id"],
    )
    views = []
    for row in citations:
        citation = citation_for(row, row["rank"])
        states = [
            row[name]
            for name in ("chunk_status", "version_status", "source_status", "model_status")
        ]
        if any(status != "verified" for status in states):
            citation["verificationStatus"] = next(
                (status for status in ("revoked", "rejected", "pending") if status in states),
                "pending",
            )
        views.append(citation)
    return {
        "id": str(section["id"]),
        "parentId": str(section["parent_id"]) if section["parent_id"] else None,
        "sectionKey": section["section_key"],
        "title": section["title"],
        "sortOrder": section["sort_order"],
        "generationStatus": section["generation_status"],
        "body": section["body"],
        "citations": views,
        "reused": section["source_section_id"] is not None,
        "personalNotes": visible_notes(connection, section),
    }


def visible_notes(connection, section):
    if not section["personal_notes"]:
        return []
    manual = one(
        connection,
        "SELECT * FROM manuals WHERE id=:m AND visibility='private'",
        m=section["manual_id"],
    )
    if not manual:
        return []
    current = personal_context(
        connection, manual["owner_user_id"], manual["user_product_instance_id"]
    )
    active = {row["id"]: row for row in current}
    valid = {row["id"] for row in manual["context_snapshot"] if active.get(row["id"]) == row}
    return [
        {"text": note["text"]}
        for note in section["personal_notes"]
        if set(note["memoryIds"]) <= valid
    ]


def get_manual(connection, model_id, user, instance_id=None):
    """实例明确选择私有手册，否则只读公共版本；新版本生成时保留旧可用内容。"""
    if instance_id:
        instance = owned(connection, "instances", instance_id, user)
        if instance["product_model_id"] != model_id:
            missing()
    else:
        model = one(
            connection,
            "SELECT id FROM product_models WHERE id=:id AND identity_status='verified'",
            id=model_id,
        )
        if not model:
            missing()
    manual = None
    if instance_id:
        manual = one(
            connection,
            (
                "SELECT * FROM manuals WHERE user_product_instance_id=:i AND "
                "owner_user_id=:u AND visibility='private' ORDER BY is_current "
                "DESC,version DESC LIMIT 1"
            ),
            i=instance_id,
            u=user,
        )
    if not manual:
        manual = one(
            connection,
            (
                "SELECT * FROM manuals WHERE product_model_id=:m AND "
                "visibility='public' ORDER BY is_current DESC,version DESC LIMIT 1"
            ),
            m=model_id,
        )
    if not manual:
        missing()
    sections = many(
        connection,
        "SELECT * FROM manual_sections WHERE manual_id=:id ORDER BY sort_order,id",
        id=manual["id"],
    )
    context = (
        personal_context(connection, user, instance_id) if manual["visibility"] == "private" else []
    )
    catalog = evidence(connection, model_id)
    expected = generation_signature(catalog, context)
    context_stale = manual["visibility"] == "private" and digest(context) != manual["context_hash"]
    # 撤销/过期记忆不再显示为当前设置；历史正文不可改写，必须提示生成新版本。
    active = {row["id"]: row for row in context}
    personal = [row for row in manual["context_snapshot"] if active.get(row["id"]) == row]
    cited = {
        row["knowledge_chunk_id"]
        for row in many(
            connection,
            (
                "SELECT c.knowledge_chunk_id FROM manual_section_citations c JOIN "
                "manual_sections s "
                "ON s.id=c.manual_section_id WHERE s.manual_id=:m"
            ),
            m=manual["id"],
        )
    }
    return {
        "id": str(manual["id"]),
        "version": manual["version"],
        "status": manual["status"],
        "jobId": str(manual["job_id"]) if manual["job_id"] else None,
        "visibility": manual["visibility"],
        "inputsStale": expected != manual["evidence_hash"],
        "contextStale": context_stale,
        "personalContext": [
            {key: row[key] for key in ("memoryType", "content")} for row in personal
        ],
        "coverage": {
            "totalChunks": len(catalog),
            "citedChunks": len(cited & {c["id"] for c in catalog}),
        },
        "sections": [section_view(connection, section) for section in sections],
        "latestBuild": latest_build(connection, manual, expected),
    }


def latest_build(connection, manual, expected=None):
    latest = one(
        connection,
        "SELECT id,version,status,job_id,evidence_hash FROM manuals WHERE product_model_id=:m "
        "AND visibility=:visibility "
        "AND user_product_instance_id IS NOT DISTINCT FROM CAST(:i AS uuid) "
        "ORDER BY version DESC LIMIT 1",
        m=manual["product_model_id"],
        visibility=manual["visibility"],
        i=manual["user_product_instance_id"],
    )
    if not latest or latest["id"] == manual["id"]:
        return None
    if expected == manual["evidence_hash"] and latest["evidence_hash"] != expected:
        return None
    return {
        "id": str(latest["id"]),
        "version": latest["version"],
        "status": latest["status"],
        "jobId": str(latest["job_id"]) if latest["job_id"] else None,
        "inputsStale": expected is not None and latest["evidence_hash"] != expected,
    }


def generate(job):
    """Only unfinished requested chapters are generated; every write rechecks frozen inputs."""
    from app.services.guidance import risk_of
    from app.upstream import UpstreamError

    progress(job, "generating", 10)
    manual_id = UUID(job["payload"]["manualId"])
    requested = [UUID(value) for value in job["payload"].get("sectionIds", [])]
    with transaction() as connection:
        fenced(connection, job)
        manual = one(connection, "SELECT * FROM manuals WHERE id=:id", id=manual_id)
        if not manual or manual["owner_user_id"] != job["user_id"] or manual["job_id"] != job["id"]:
            raise DomainError(409, "RESOURCE_CONFLICT", "说明书任务归属已变化")
        chunks = validate_inputs(connection, manual)
        sections = many(
            connection,
            "SELECT * FROM manual_sections WHERE manual_id=:m ORDER BY sort_order,id",
            m=manual_id,
        )
        if requested and not set(requested) <= {s["id"] for s in sections}:
            missing()
        execute(connection, "UPDATE manuals SET status='generating' WHERE id=:m", m=manual_id)
    by_id = {row["id"]: row for row in chunks}
    gateway = ModelGateway()
    prompts = {key: question for key, _, question in CHAPTERS}
    subject = job["user_id"] or f"manual:{manual['product_model_id']}"
    style = style_context(manual["context_snapshot"])
    facts = [row for row in manual["context_snapshot"] if row["memoryType"] == "product_fact"]
    for number, section in enumerate(sections):
        if section["generation_status"] == "ready" or (
            requested and section["id"] not in requested
        ):
            continue
        with transaction() as connection:
            fenced(connection, job)
            validate_inputs(connection, manual)
        selected = (
            [by_id[id] for id in section["evidence_ids"]] if section["evidence_ids"] else chunks
        )
        prompt = section["generation_prompt"] or prompts[section["section_key"]]
        coverage = section["section_key"].startswith("source-")
        data = [
            {"rank": index, "content": chunk["content"]} for index, chunk in enumerate(selected, 1)
        ]
        failure = None
        notes = []
        try:
            body = gateway.chapter_answer(
                prompt,
                data,
                subject,
                memories=style if manual["visibility"] == "private" and not coverage else None,
                coverage=coverage,
            )
            ranks = {int(value) for value in re.findall(r"\[(\d+)\]", body)}
            valid = bool(ranks) and all(1 <= rank <= len(selected) for rank in ranks)
            if coverage:
                valid = valid and ranks == set(range(1, len(selected) + 1))
            if risk_of(body, warning=True) == "high":
                valid = False
            if valid and manual["visibility"] == "private" and not coverage and facts:
                with transaction() as connection:
                    fenced(connection, job)
                    validate_inputs(connection, manual)
                settings_input = [
                    {key: row[key] for key in ("id", "memoryType", "content")} for row in facts
                ]
                personalization = gateway.personalize_chapter(body, settings_input, subject)
                allowed = {UUID(row["id"]) for row in facts}
                if any(
                    not set(note.memoryIds) <= allowed or risk_of(note.text, warning=True) == "high"
                    for note in personalization.notes
                ):
                    raise DomainError(
                        502, "UPSTREAM_MODEL_ERROR", "个人补充未通过安全和记忆归属检查"
                    )
                notes = [note.model_dump(mode="json") for note in personalization.notes]
        except (DomainError, UpstreamError) as error:
            if isinstance(error, DomainError) and error.code not in {"UPSTREAM_MODEL_ERROR"}:
                raise
            body, ranks, valid, failure = "本章生成失败，请重试未完成章节。", set(), False, "failed"
        with transaction() as connection:
            fenced(connection, job)
            current = validate_inputs(connection, manual)
            live_ids = {row["id"] for row in current}
            valid = valid and all(selected[rank - 1]["id"] in live_ids for rank in ranks)
            execute(
                connection,
                "DELETE FROM manual_section_citations WHERE manual_section_id=:s",
                s=section["id"],
            )
            if valid:
                for rank in sorted(ranks):
                    execute(
                        connection,
                        (
                            "INSERT INTO manual_section_citations(manual_section_id,"
                            "knowledge_chunk_id,rank) "
                            "VALUES(:s,:k,:rank)"
                        ),
                        s=section["id"],
                        k=selected[rank - 1]["id"],
                        rank=rank,
                    )
            execute(
                connection,
                "UPDATE manual_sections SET body=:body,personal_notes=CAST(:notes AS jsonb),"
                "generation_status=:status WHERE id=:s",
                body=body
                if valid or failure
                else "本章引用不完整或操作风险较高，请补充资料或联系官方专业支持。",
                status="ready" if valid else failure or "partial",
                notes=json_value(notes if valid else []),
                s=section["id"],
            )
        progress(job, "generating", 10 + int(80 * (number + 1) / len(sections)))
        if failure:
            # 供应商失败不继续浪费后续章节额度；已完成章节永久保留，重试只补未完成。
            break
    with transaction() as connection:
        fenced(connection, job)
        lock_scope(connection, manual)
        live = validate_inputs(connection, manual)
        states = many(
            connection,
            "SELECT generation_status FROM manual_sections WHERE manual_id=:m",
            m=manual_id,
        )
        published = {
            row["knowledge_chunk_id"]
            for row in many(
                connection,
                (
                    "SELECT c.knowledge_chunk_id FROM manual_section_citations c JOIN "
                    "manual_sections s "
                    "ON s.id=c.manual_section_id WHERE s.manual_id=:m"
                ),
                m=manual_id,
            )
        }
        live_ids = {row["id"] for row in live}
        all_ready = bool(states) and all(s["generation_status"] == "ready" for s in states)
        complete = all_ready and bool(published) and published <= live_ids
        if manual["generation_profile"] != "legacy":
            complete = complete and live_ids <= published
        any_ready = any(s["generation_status"] == "ready" for s in states)
        prior = one(
            connection,
            (
                "SELECT version FROM manuals WHERE product_model_id=:m AND visibility=:v AND"
                " is_current "
                "AND owner_user_id IS NOT DISTINCT FROM CAST(:u AS uuid) "
                "AND user_product_instance_id IS NOT DISTINCT FROM CAST(:i AS uuid)"
            ),
            m=manual["product_model_id"],
            v=manual["visibility"],
            u=manual["owner_user_id"],
            i=manual["user_product_instance_id"],
        )
        make_current = (
            any_ready
            and published <= live_ids
            and (
                manual["generation_profile"] != "legacy"
                or not prior
                or prior["version"] <= manual["version"]
            )
            and (complete or not prior)
        )
        if make_current:
            execute(
                connection,
                (
                    "UPDATE manuals SET is_current=false WHERE product_model_id=:m "
                    "AND visibility=:v "
                    "AND owner_user_id IS NOT DISTINCT FROM CAST(:u AS uuid) "
                    "AND user_product_instance_id IS NOT DISTINCT FROM CAST(:i AS uuid) "
                    "AND is_current"
                ),
                m=manual["product_model_id"],
                v=manual["visibility"],
                u=manual["owner_user_id"],
                i=manual["user_product_instance_id"],
            )
        status = "ready" if complete else "partial" if any_ready else "failed"
        execute(
            connection,
            "UPDATE manuals SET status=:status,is_current=:current WHERE id=:m",
            status=status,
            current=make_current,
            m=manual_id,
        )
        finish(
            connection,
            job,
            "succeeded" if complete else "partial_success" if any_ready else "failed",
            None if complete else "说明书仍有未完成章节，可受控重试",
        )
