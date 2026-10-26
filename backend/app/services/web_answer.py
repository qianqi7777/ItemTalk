"""Generate only supported answers from private, current-question web evidence."""

import re
import time

from app.db import many, one, transaction
from app.deadlines import remaining
from app.errors import DomainError
from app.grounding import evidence_insufficient, grounded_answer
from app.services.general_advice import requires_evidence
from app.services.web_research import research


def answer_from_web(run, instance, question, gateway, cancelled, on_status=None):
    with transaction() as connection:
        model = one(
            connection,
            "SELECT m.*,b.name AS brand_name,b.official_domains FROM product_models m "
            "JOIN brands b ON b.id=m.brand_id WHERE m.id=:id",
            id=instance["product_model_id"],
        )
        model["blocked_urls"] = [
            r["url"]
            for r in many(
                connection,
                "SELECT url FROM sources WHERE verification_status IN ('revoked','rejected') "
                "ORDER BY url LIMIT 1000",
            )
        ]
        previous = many(
            connection,
            "SELECT m.web_lookup FROM messages m JOIN conversations c ON c.id=m.conversation_id "
            "WHERE m.user_id=:u AND c.user_product_instance_id=:i AND c.deleted_at IS NULL "
            "AND m.web_lookup IS NOT NULL ORDER BY m.created_at DESC LIMIT 20",
            u=run["user_id"],
            i=instance["id"],
        )
    from app.services.web_research import cache_valid, identity, queries

    _, key, _ = queries(model, question)
    _, domains = identity(model)
    cached = next(
        (r["web_lookup"] for r in previous if cache_valid(r["web_lookup"], key, domains)), None
    )
    lookup = research(
        model,
        question,
        user=run["user_id"],
        cancelled=cancelled,
        deadline=time.monotonic() + max(0, remaining(45) - 12),
        cached=cached,
        force=run.get("force_web_search", False),
        on_status=on_status,
    )
    if lookup["status"] == "identity_conflict":
        title = lookup["conflict"]["title"]
        return (
            f"联网找到的资料《{title}》与当前记录的品牌/型号不一致。请先核对设备底部铭牌、品牌标志和完整型号；确认前不会套用另一款产品的操作步骤。",
            "clarification",
            lookup,
        )
    if not lookup["sources"]:
        return None, None, lookup
    evidence = [{"rank": s["rank"], "content": s["excerpt"]} for s in lookup["sources"]]
    try:
        if on_status:
            on_status("web_answering")
        answer = gateway.answer(question, evidence, run["user_id"])
        if evidence_insufficient(answer):
            lookup.update(status="empty", reason="unsupported", sources=[])
            return None, None, lookup
        markers = re.findall(r"\[(\d+)\]", answer)
        if not markers or any(len(value) > 4 for value in markers):
            lookup.update(status="empty", reason="unsupported", sources=[])
            return None, None, lookup
        cited = {int(value) for value in markers}
        referenced = [row for row in evidence if row["rank"] in cited]
        if cited != {row["rank"] for row in referenced}:
            lookup.update(status="empty", reason="unsupported", sources=[])
            return None, None, lookup
        assessment = gateway.assess_answer(question, answer, referenced, run["user_id"])
        supported, retained = grounded_answer(answer, referenced, assessment)
        if supported is None or not retained:
            lookup.update(status="empty", reason="unsupported", sources=[])
            return None, None, lookup
        lookup["sources"] = [s for s in lookup["sources"] if s["rank"] in retained]
        if requires_evidence(supported) and any(
            s["sourceKind"] != "official" for s in lookup["sources"]
        ):
            lookup.update(status="empty", reason="official_required", sources=[])
            return None, None, lookup
        return supported, "web", lookup
    except DomainError as error:
        if error.status == 409:
            raise
        lookup.update(
            status="empty",
            reason="quota"
            if error.status == 429
            else "timeout"
            if error.code == "ANSWER_TIMEOUT"
            else "model_unavailable",
            sources=[],
        )
        return None, None, lookup
