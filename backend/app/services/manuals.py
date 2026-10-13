"""版本化公共手册；只使用核验证据，租约过期和来源撤销均阻止发布。"""

import re
from uuid import UUID

from app.adapters import ModelGateway
from app.db import execute, many, one, transaction
from app.errors import DomainError, missing
from app.security import digest
from app.services.chat import citation_for
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
            "BY v.id,k.chunk_no LIMIT 12 FOR SHARE OF k,m,v,s,l"
        ),
        id=model_id,
    )


def signature_for(chunks):
    return digest(
        [{key: row[key] for key in ("id", "content", "document_version_id")} for row in chunks]
    )


def schedule(connection, model_id, retry=None):
    execute(connection, "SELECT id FROM product_models WHERE id=:id FOR UPDATE", id=model_id)
    chunks = evidence(connection, model_id)
    if not chunks:
        return None
    signature = signature_for(chunks)
    previous = one(
        connection,
        (
            "SELECT * FROM manuals WHERE product_model_id=:m AND "
            "visibility='public' AND evidence_hash=:h"
        ),
        m=model_id,
        h=signature,
    )
    if previous:
        if retry:
            old_job = one(connection, "SELECT * FROM jobs WHERE id=:id", id=retry)
            if (
                previous["job_id"] != retry
                or not old_job
                or old_job["status"] not in {"failed", "partial_success"}
            ):
                raise DomainError(
                    409, "RESOURCE_CONFLICT", "只能重试本版本已失败或部分完成的说明书任务"
                )
            job = new_job(connection, "manual", {"manualId": str(previous["id"])}, retry=retry)
            execute(
                connection,
                "UPDATE manuals SET job_id=:j,status='pending' WHERE id=:id",
                j=job["id"],
                id=previous["id"],
            )
            return {**previous, "job_id": job["id"], "status": "pending"}
        # 同证据版本不重新花费模型额度，任务本身处理受控重试。
        return previous
    if retry:
        raise DomainError(409, "RESOURCE_CONFLICT", "资料版本已变化，请生成新的说明书")
    version = one(
        connection,
        (
            "SELECT COALESCE(max(version),0)+1 AS n FROM manuals WHERE "
            "product_model_id=:m AND visibility='public'"
        ),
        m=model_id,
    )["n"]
    manual = one(
        connection,
        (
            "INSERT INTO manuals(product_model_id,visibility,version,status,"
            "evidence_hash) VALUES (:m,'public',:v,'pending',:h) RETURNING *"
        ),
        m=model_id,
        v=version,
        h=signature,
    )
    for index, (key, title, _) in enumerate(CHAPTERS):
        execute(
            connection,
            (
                "INSERT INTO manual_sections(manual_id,section_key,title,"
                "sort_order,generation_status) VALUES (:m,:key,:title,:n,'pending')"
            ),
            m=manual["id"],
            key=key,
            title=title,
            n=index,
        )
    job = new_job(
        connection, "manual", {"manualId": str(manual["id"])}, key=f"manual:{manual['id']}"
    )
    execute(connection, "UPDATE manuals SET job_id=:j WHERE id=:id", j=job["id"], id=manual["id"])
    return {**manual, "job_id": job["id"]}


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
    }


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
    return {
        "id": str(manual["id"]),
        "version": manual["version"],
        "status": manual["status"],
        "jobId": str(manual["job_id"]) if manual["job_id"] else None,
        "sections": [section_view(connection, section) for section in sections],
        "latestBuild": latest_build(connection, manual),
    }


def latest_build(connection, manual):
    latest = one(
        connection,
        "SELECT id,version,status,job_id FROM manuals WHERE product_model_id=:m "
        "AND visibility=:visibility "
        "AND user_product_instance_id IS NOT DISTINCT FROM CAST(:i AS uuid) "
        "ORDER BY version DESC LIMIT 1",
        m=manual["product_model_id"],
        visibility=manual["visibility"],
        i=manual["user_product_instance_id"],
    )
    if not latest or latest["id"] == manual["id"]:
        return None
    return {
        "id": str(latest["id"]),
        "version": latest["version"],
        "status": latest["status"],
        "jobId": str(latest["job_id"]) if latest["job_id"] else None,
    }


def generate(job):
    """模型调用在事务外执行，每次写入再检查租约与当前证据。

    章节一旦完成就不可改写；补充资料使用新版本，避免旧回答引用漂移。
    部分章节失败时可以重试未完成章节，保留已经核验完成的内容。
    """
    progress(job, "generating", 10)
    manual_id = UUID(job["payload"]["manualId"])
    with transaction() as connection:
        fenced(connection, job)
        manual = one(connection, "SELECT * FROM manuals WHERE id=:id", id=manual_id)
        if not manual:
            raise DomainError(404, "PRODUCT_NOT_FOUND", "说明书不存在")
        chunks = evidence(connection, manual["product_model_id"])
        if signature_for(chunks) != manual["evidence_hash"]:
            raise DomainError(409, "SOURCE_UNVERIFIED", "说明书证据版本已变化，请生成新版本")
        sections = many(
            connection,
            "SELECT * FROM manual_sections WHERE manual_id=:id ORDER BY sort_order",
            id=manual_id,
        )
        execute(connection, "UPDATE manuals SET status='generating' WHERE id=:id", id=manual_id)
    gateway = ModelGateway()
    prompts = {key: question for key, _, question in CHAPTERS}
    any_ready = False
    complete = True
    for section in sections:
        if section["generation_status"] == "ready":
            any_ready = True
            continue
        if not chunks:
            raise DomainError(422, "EVIDENCE_INSUFFICIENT", "说明书证据已失效")
        data = [
            {"rank": index, "content": chunk["content"]} for index, chunk in enumerate(chunks, 1)
        ]
        body = gateway.answer(
            prompts[section["section_key"]], data, f"manual:{manual['product_model_id']}"
        )
        ranks = {int(value) for value in re.findall(r"\[(\d+)\]", body)}
        valid = bool(ranks) and all(1 <= rank <= len(chunks) for rank in ranks)
        with transaction() as connection:
            fenced(connection, job)
            current = evidence(connection, manual["product_model_id"])
            live_ids = {chunk["id"] for chunk in current}
            valid = valid and all(chunks[rank - 1]["id"] in live_ids for rank in ranks)
            if not valid:
                complete = False
                execute(
                    connection,
                    (
                        "UPDATE manual_sections SET body=:body,generation_status='partial' "
                        "WHERE id=:id"
                    ),
                    body="本章证据不足，请等待补充核验资料。",
                    id=section["id"],
                )
                continue
            execute(
                connection,
                "DELETE FROM manual_section_citations WHERE manual_section_id=:id",
                id=section["id"],
            )
            for rank in sorted(ranks):
                execute(
                    connection,
                    (
                        "INSERT INTO manual_section_citations(manual_section_id,"
                        "knowledge_chunk_id,rank) VALUES (:s,:k,:rank)"
                    ),
                    s=section["id"],
                    k=chunks[rank - 1]["id"],
                    rank=rank,
                )
            execute(
                connection,
                ("UPDATE manual_sections SET body=:body,generation_status='ready' WHERE id=:id"),
                body=body,
                id=section["id"],
            )
            any_ready = True
    with transaction() as connection:
        fenced(connection, job)
        execute(
            connection,
            "SELECT id FROM product_models WHERE id=:id FOR UPDATE",
            id=manual["product_model_id"],
        )
        current_version = one(
            connection,
            (
                "SELECT version FROM manuals WHERE product_model_id=:m AND "
                "visibility='public' AND is_current"
            ),
            m=manual["product_model_id"],
        )
        # 生成可能跨多次外部调用：最终切换current前再检查所有已写入引用，
        # 防止某章完成后来源被撤销，却仍把新版本发布成当前可用手册。
        live_ids = {chunk["id"] for chunk in evidence(connection, manual["product_model_id"])}
        published_ids = {
            row["knowledge_chunk_id"]
            for row in many(
                connection,
                "SELECT c.knowledge_chunk_id FROM manual_section_citations c "
                "JOIN manual_sections s ON s.id=c.manual_section_id WHERE s.manual_id=:m",
                m=manual_id,
            )
        }
        evidence_still_valid = bool(published_ids) and published_ids <= live_ids
        complete = complete and evidence_still_valid
        make_current = (
            any_ready
            and (not current_version or current_version["version"] <= manual["version"])
            and evidence_still_valid
            and (complete or not current_version)
        )
        if make_current:
            execute(
                connection,
                (
                    "UPDATE manuals SET is_current=false WHERE product_model_id=:m AND "
                    "visibility='public' AND is_current"
                ),
                m=manual["product_model_id"],
            )
        execute(
            connection,
            "UPDATE manuals SET status=:status,is_current=:current WHERE id=:id",
            status="ready" if complete else "partial",
            current=make_current,
            id=manual_id,
        )
        finish(connection, job, "succeeded" if complete else "partial_success")
