"""知识缺口只保存去身份化意图，按型号聚合并限频补充研究。"""

import re
from datetime import UTC, datetime, timedelta

from app.db import one
from app.errors import DomainError
from app.security import digest, normalize
from app.services.products import build_for

SUMMARIES = {
    "setup": "产品初始设置的核验资料不足",
    "power": "产品开关机与供电的核验资料不足",
    "connectivity": "产品连接与配对的核验资料不足",
    "features": "产品功能与使用方式的核验资料不足",
    "maintenance": "产品清洁与维护的核验资料不足",
    "troubleshooting": "产品故障排查的核验资料不足",
    "other": "产品使用问题的核验资料不足",
}


def redacted_question(question):
    value = normalize(question)
    value = re.sub(r"[^\s@]+@[^\s@]+\.[^\s@]+", "[联系方式]", value)
    value = re.sub(r"(?:\+?\d[\s-]*){7,}", "[编号]", value)
    value = re.sub(r"\b(?=[a-z\d_-]{8,}\b)(?=[a-z\d_-]*\d)[a-z\d_-]+\b", "[编号]", value)
    return value[:2000]


def summary_for(gateway, question, user):
    try:
        intent = gateway.gap_intent(redacted_question(question), user)
        return SUMMARIES[intent.category]
    except DomainError:
        # 分类服务/预算失败不能使已经产生的澄清答案丢失。
        return SUMMARIES["other"]


def record(connection, model_id, summary):
    if summary not in SUMMARIES.values():
        raise ValueError("Public gap summaries must come from the anonymized dictionary")
    model = one(connection, "SELECT * FROM product_models WHERE id=:id FOR UPDATE", id=model_id)
    gap = one(
        connection,
        "INSERT INTO knowledge_gaps(product_model_id,question_summary,norma"
        "lized_hash) VALUES (:m,:summary,:hash) "
        "ON CONFLICT(product_model_id,normalized_hash) DO UPDATE SET occurr"
        "ence_count=knowledge_gaps.occurrence_count+1 "
        "RETURNING *",
        m=model_id,
        summary=summary,
        hash=digest(summary),
    )
    if gap["status"] in {"dismissed", "resolved"} or gap["research_job_id"]:
        return gap
    build = one(
        connection,
        "SELECT j.* FROM public_builds b JOIN jobs j ON j.id=b.job_id "
        "WHERE b.product_model_id=:m ORDER BY b.build_revision DESC LIMIT 1",
        m=model_id,
    )
    active = build and build["status"] in {"queued", "running", "waiting_for_user"}
    recent = (
        build
        and build["finished_at"]
        and build["finished_at"] > datetime.now(UTC) - timedelta(hours=6)
    )
    if active:
        job = build
    elif model["identity_status"] == "verified" and not recent:
        # 缺口不能让每次问答都重跑付费搜索，复用活动任务并限制六小时频率。
        job = build_for(connection, model_id, None, None, force=True)
    else:
        return gap
    return one(
        connection,
        (
            "UPDATE knowledge_gaps SET research_job_id=:j,status='researching' "
            "WHERE id=:id RETURNING *"
        ),
        j=job["id"],
        id=gap["id"],
    )
