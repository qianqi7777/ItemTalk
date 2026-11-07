"""Labeled model fallback; no retrieved citation can be attached to an inference."""

from datetime import UTC, datetime

from app.db import one, transaction
from app.reference_answers import REFERENCE_LABEL


def answer(run, instance, question, gateway, previous_lookup, memories=None):
    with transaction() as c:
        model = one(
            c,
            "SELECT m.*,b.name AS brand_name FROM product_models m "
            "JOIN brands b ON b.id=m.brand_id WHERE m.id=:m",
            m=instance["product_model_id"],
        )
    reference = gateway.reference_answer(question, model, run["user_id"], memories=memories)
    content = REFERENCE_LABEL + "\n\n" + reference.answer + "\n\n尚未确认：" + reference.uncertainty
    lookup = {
        **(previous_lookup or {}),
        "status": "unverified",
        "lookupMode": "model_reference",
        "reason": (previous_lookup or {}).get("reason") or "no_results",
        "searchedAt": (previous_lookup or {}).get("searchedAt") or datetime.now(UTC).isoformat(),
        "queryKey": (previous_lookup or {}).get("queryKey") or "",
        "cached": False,
        "sources": [],
    }
    return content, lookup
