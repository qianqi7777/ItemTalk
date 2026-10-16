"""引用核验的服务端边界：不能靠编造摘录、编号或真假字符串混过核验。"""

import json

import httpx
import pytest
from pydantic import ValidationError

from app.adapters import ModelGateway
from app.config import Settings
from app.errors import DomainError
from app.grounding import AnswerSupport, grounded_answer, quote_options, resolve_choices

EVIDENCE = [
    {"rank": 1, "content": "打开设备：按住电源2秒，直到蓝灯闪烁。"},
    {"rank": 2, "content": "关闭设备：按住电源2秒，直到蓝灯熄灭。"},
]
ASSESSMENT = {
    "fullySupported": True,
    "citations": [
        {"rank": 1, "supported": False, "quote": ""},
        {"rank": 2, "supported": True, "quote": "关闭设备：按住电源2秒，直到蓝灯熄灭。"},
    ],
}
CHOICES = {
    "fullySupported": True,
    "citations": [
        {"rank": 1, "supported": False, "quoteIndex": None},
        {"rank": 2, "supported": True, "quoteIndex": 1},
    ],
}


def test_removes_only_unsupported_reference_without_rewriting_facts():
    body, ranks = grounded_answer("按住电源2秒，直到蓝灯熄灭[1][2]。", EVIDENCE, ASSESSMENT)
    assert body == "按住电源2秒，直到蓝灯熄灭[2]。" and ranks == {2}


@pytest.mark.parametrize("fully_supported", [False, True])
def test_no_supported_citations_cannot_publish_factual_answer(fully_supported):
    assessment = {
        "fullySupported": fully_supported,
        "citations": [{"rank": row["rank"], "supported": False, "quote": ""} for row in EVIDENCE],
    }
    assert grounded_answer("无依据事实[1][2]", EVIDENCE, assessment) == (None, set())


def test_partial_support_is_insufficient_for_the_whole_answer():
    assert grounded_answer(
        "还包括未知事实[1][2]", EVIDENCE, {**ASSESSMENT, "fullySupported": False}
    ) == (None, set())


@pytest.mark.parametrize("quote", ["", "关闭设备：先拆卸电池。"])
def test_empty_or_invented_support_quote_is_rejected(quote):
    assessment = {
        **ASSESSMENT,
        "citations": [ASSESSMENT["citations"][0], {"rank": 2, "supported": True, "quote": quote}],
    }
    with pytest.raises(DomainError, match="引用核验摘录无效"):
        grounded_answer("关闭设备[1][2]", EVIDENCE, assessment)


@pytest.mark.parametrize("ranks", [[1], [1, 3]])
def test_missing_and_extra_assessment_ranks_are_rejected(ranks):
    assessment = {
        "fullySupported": True,
        "citations": [{"rank": rank, "supported": False, "quote": ""} for rank in ranks],
    }
    with pytest.raises(DomainError, match="引用核验结果无效"):
        grounded_answer("关闭设备[1][2]", EVIDENCE, assessment)


def test_long_marker_never_reaches_unbounded_integer_conversion():
    with pytest.raises(DomainError):
        grounded_answer("事实[" + "9" * 5000 + "]", EVIDENCE, ASSESSMENT)


def test_whitespace_and_unicode_normalization_still_require_literal_source_quote():
    body, ranks = grounded_answer(
        "关闭[2]",
        [{"rank": 2, "content": "按住电源\n３秒"}],
        {
            "fullySupported": True,
            "citations": [{"rank": 2, "supported": True, "quote": "按住电源3秒"}],
        },
    )
    assert body == "关闭[2]" and ranks == {2}


@pytest.mark.parametrize(
    "assessment",
    [
        {**ASSESSMENT, "fullySupported": "true"},
        {**ASSESSMENT, "citations": [ASSESSMENT["citations"][0]] * 2},
    ],
)
def test_verifier_schema_rejects_coercion_and_duplicates(assessment):
    with pytest.raises(ValidationError):
        AnswerSupport.model_validate(assessment)


def test_verifier_real_payload_is_bounded_and_contains_only_supplied_context(monkeypatch):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(CHOICES)}}]}
        )

    gateway = ModelGateway(httpx.Client(transport=httpx.MockTransport(respond)))
    gateway.config = Settings(model_provider="dashscope", model_api_key="test-key")
    budgets = []
    monkeypatch.setattr(gateway, "budget", budgets.append)
    result = gateway.assess_answer("如何关闭？", "按住2秒[1][2]", EVIDENCE, "subject")
    assert result.fullySupported and budgets == ["subject"] and len(requests) == 1
    payload = requests[0]
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["enable_thinking"] is False and payload["max_tokens"] <= 1600
    user = json.loads(payload["messages"][1]["content"])
    assert user == {
        "question": "如何关闭？",
        "answer": "按住2秒[1][2]",
        "evidence": quote_options(EVIDENCE),
        "userMemory": [],
    }
    assert result.model_dump() == ASSESSMENT


def test_original_quote_windows_cover_whole_chunk_without_model_rewriting():
    content = "零一二三四五六七八九" * 200
    options = quote_options([{"rank": 2, "content": content}])
    assert len(options[0]["quotes"]) == 7
    assert all(len(row["text"]) <= 400 for row in options[0]["quotes"])
    result = resolve_choices(
        {"fullySupported": True, "citations": [{"rank": 2, "supported": True, "quoteIndex": 7}]},
        options,
    )
    assert result.citations[0].quote == content[1800:]
    assert grounded_answer("事实[2]", [{"rank": 2, "content": content}], result) == ("事实[2]", {2})


@pytest.mark.parametrize(
    "choice",
    [
        {"rank": 2, "supported": True, "quoteIndex": None},
        {"rank": 2, "supported": True, "quoteIndex": 2},
        {"rank": 3, "supported": True, "quoteIndex": 1},
        {"rank": 1, "supported": False, "quoteIndex": 1},
        {"rank": 2, "supported": True, "quoteIndex": "1"},
        {"rank": 2, "supported": True, "quoteIndex": True},
    ],
)
def test_invalid_quote_choice_cannot_select_or_invent_source_text(choice):
    with pytest.raises(ValueError):
        resolve_choices({"fullySupported": True, "citations": [choice]}, quote_options(EVIDENCE))
