"""A model reference uses the configured model and cannot manufacture source metadata."""

import json
import time

import httpx
import pytest

from app.adapters import ModelGateway
from app.config import Settings
from app.deadlines import request_scope
from app.errors import DomainError

MODEL = dict(
    brand_name="ROSESELSA", model_code="CERAMICS Ultra", region=None, hardware_revision=None
)


def gateway(monkeypatch, content):
    requests = []

    def reply(request):
        requests.append(json.loads(request.content))
        actual = content if len(requests) == 1 else '{"hasSourceClaims":false}'
        return httpx.Response(200, json={"choices": [{"message": {"content": actual}}]})

    g = ModelGateway(
        httpx.Client(transport=httpx.MockTransport(reply)),
        configuration=Settings(
            model_provider="dashscope", model_api_key="test-reference-key", v2_enabled=False
        ),
    )
    monkeypatch.setattr(g, "budget", lambda _subject: None)
    return g, requests


def test_reference_generation_receives_the_actual_model_and_current_question(monkeypatch):
    g, requests = gateway(
        monkeypatch,
        json.dumps(
            {
                "answer": "请在手机中确认耳机配对状态。",
                "uncertainty": "当前固件和手机系统尚未确认。",
            }
        ),
    )
    try:
        answer = g.reference_answer("怎么连手机？", MODEL, "reference-test")
        assert answer.answer == "请在手机中确认耳机配对状态。"
        actual = json.loads(requests[0]["messages"][-1]["content"])
        assert actual["product"]["model_code"] == "CERAMICS Ultra"
        assert actual["question"] == "怎么连手机？" and "evidence" not in actual
    finally:
        g.close()


@pytest.mark.parametrize(
    "content",
    [
        '{"answer":"这是官方步骤[1]","uncertainty":"未知"}',
        '{"answer":"下载 https://made-up.example/tool","uncertainty":"未知"}',
        '{"answer":"参考","uncertainty":"未知","sources":["fake"]}',
        '{"answer":"参考"}',
        '{"answer":"根据官方手册【1】，请访问 '
        '[厂商说明](//support.example.invalid/doc)。","uncertainty":"未知"}',
        '{"answer":"请访问[说明书](support.example.invalid/doc)。","uncertainty":"未知"}',
        "not JSON",
    ],
)
def test_bad_reference_content_does_not_become_a_cited_or_successful_answer(monkeypatch, content):
    g, _ = gateway(monkeypatch, content)
    try:
        with pytest.raises(DomainError) as caught:
            g.reference_answer("怎么连手机？", MODEL, "reference-test")
        assert caught.value.code == "UPSTREAM_MODEL_ERROR" and "made-up" not in caught.value.message
    finally:
        g.close()


def test_reference_source_claims_are_assessed_semantically_not_by_official_keyword(monkeypatch):
    content = json.dumps(
        {"answer": "建议你之后核对官方手册。", "uncertainty": "当前未读取任何资料。"}
    )
    g, requests = gateway(monkeypatch, content)
    try:
        assert g.reference_answer("怎么连接？", MODEL, "test").answer
        assert len(requests) == 2
    finally:
        g.close()


def test_reference_claiming_to_have_read_a_source_is_rejected_by_semantic_assessment(monkeypatch):
    calls = []

    def reply(_request):
        calls.append(True)
        content = (
            json.dumps(
                {
                    "answer": "根据我查阅的官方手册，本机已确认支持此功能。",
                    "uncertainty": "手机版本未知。",
                }
            )
            if len(calls) == 1
            else '{"hasSourceClaims":true}'
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    g = ModelGateway(
        httpx.Client(transport=httpx.MockTransport(reply)),
        configuration=Settings(
            model_provider="dashscope", model_api_key="test-only-key", v2_enabled=False
        ),
    )
    monkeypatch.setattr(g, "budget", lambda _s: None)
    try:
        with pytest.raises(DomainError):
            g.reference_answer("支持此功能吗？", MODEL, "test")
    finally:
        g.close()


@pytest.mark.parametrize("late_call", [1, 2])
def test_late_model_reference_cannot_escape_the_shared_deadline(monkeypatch, late_call):
    calls = []

    class SlowStream(httpx.SyncByteStream):
        def __init__(self, body):
            self.body = body

        def __iter__(self):
            for part in (self.body[:12], self.body[12:]):
                time.sleep(0.025)
                yield part

    def reply(_request):
        calls.append(True)
        content = (
            json.dumps({"answer": "请确认配对状态。", "uncertainty": "手机系统未知。"})
            if len(calls) == 1
            else '{"hasSourceClaims":false}'
        )
        body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        return (
            httpx.Response(200, stream=SlowStream(body))
            if len(calls) == late_call
            else httpx.Response(200, content=body)
        )

    g = ModelGateway(
        httpx.Client(transport=httpx.MockTransport(reply)),
        configuration=Settings(
            model_provider="dashscope", model_api_key="test-only-key", v2_enabled=False
        ),
    )
    monkeypatch.setattr(g, "budget", lambda _s: None)
    try:
        with request_scope(time.monotonic() + 0.035), pytest.raises(DomainError) as caught:
            g.reference_answer("怎么配对？", MODEL, "test")
        assert caught.value.code == "ANSWER_TIMEOUT"
    finally:
        g.close()
