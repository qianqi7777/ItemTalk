"""阶梯/生效日期与缺失usage不能导致伪造零用量或错误费用估算。"""

import json
from decimal import Decimal

from app.config import settings
from app.operations import token_cost


def test_pricing_uses_effective_input_tier_and_preserves_unknown_output(monkeypatch):
    prices = [
        {
            "provider": "dashscope",
            "model": "sample",
            "effective_from": "2026-01-01",
            "input_per_million": "1",
            "output_per_million": "2",
            "max_input_tokens": 100,
        },
        {
            "provider": "dashscope",
            "model": "sample",
            "effective_from": "2026-01-01",
            "input_per_million": "3",
            "output_per_million": "4",
            "min_input_tokens": 101,
        },
    ]
    monkeypatch.setenv("MODEL_PRICES", json.dumps(prices))
    settings.cache_clear()
    try:
        assert token_cost("dashscope", "sample", {"prompt_tokens": 100, "completion_tokens": 10})[
            2
        ] == Decimal("0.00012")
        assert token_cost("dashscope", "sample", {"prompt_tokens": 101, "completion_tokens": 10})[
            2
        ] == Decimal("0.000343")
        assert token_cost("dashscope", "sample", {"prompt_tokens": 100})[2] is None
    finally:
        settings.cache_clear()


def test_input_only_rate_does_not_invent_output_tokens(monkeypatch):
    monkeypatch.setenv(
        "MODEL_PRICES",
        json.dumps(
            [
                {
                    "provider": "dashscope",
                    "model": "vector-sample",
                    "effective_from": "2026-01-01",
                    "input_per_million": "1",
                    "output_per_million": "0",
                }
            ]
        ),
    )
    settings.cache_clear()
    try:
        it, ot, cost, currency, _ = token_cost("dashscope", "vector-sample", {"prompt_tokens": 100})
        assert it == 100 and ot is None and cost == Decimal("0.0001") and currency == "CNY"
        assert token_cost("dashscope", "vector-sample", {})[2] is None
    finally:
        settings.cache_clear()
