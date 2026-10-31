"""Responses requests exercise the actual gateway, without a supplier or database."""

import json

import httpx
import pytest

from app.adapters import ModelGateway
from app.config import Settings
from app.errors import DomainError


def envelope(text='{"ok":true}', **changes):
    return {
        "status": "completed",
        "output": [
            {"type": "reasoning", "summary": [{"text": "private thought"}]},
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text}],
            },
        ],
        "usage": {"input_tokens": 12, "output_tokens": 4},
        **changes,
    }


def make_gateway(monkeypatch, reply, **changes):
    config = Settings(
        model_provider="dashscope",
        model_api_key="test-only-key",
        vision_provider="dashscope",
        vision_api_key="test-only-vision-key",
        chat_api_protocol="responses",
        vision_api_protocol="responses",
        **changes,
    )
    gateway = ModelGateway(httpx.Client(transport=httpx.MockTransport(reply)), configuration=config)
    monkeypatch.setattr(gateway, "budget", lambda subject: None)
    monkeypatch.setattr("app.operations.metric", lambda *args, **kwargs: None)
    return gateway


def test_responses_converts_images_json_and_usage_without_exposing_reasoning(monkeypatch):
    def reply(request):
        assert request.url.path.endswith("/responses")
        payload = json.loads(request.content)
        assert "messages" not in payload and "max_tokens" not in payload
        assert payload["max_output_tokens"] == 100
        assert payload["store"] is False
        assert payload["text"]["format"] == {"type": "json_object"}
        assert payload["input"][1]["content"] == [
            {"type": "input_text", "text": "JSON"},
            {"type": "input_image", "image_url": "data:image/png;base64,AA=="},
        ]
        return httpx.Response(200, json=envelope())

    gateway = make_gateway(monkeypatch, reply)
    result = gateway.request(
        "vision",
        "/chat/completions",
        {
            "model": "qwen3.8-max",
            "max_tokens": 100,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "Return JSON"},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "JSON"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
                    ],
                },
            ],
        },
    )
    assert result["choices"][0]["message"]["content"] == '{"ok":true}'
    assert result["usage"]["input_tokens"] == 12


def test_responses_tool_round_trip_preserves_call_id_and_forced_tool(monkeypatch):
    def reply(request):
        payload = json.loads(request.content)
        assert payload["tools"] == [
            {
                "type": "function",
                "name": "read_evidence",
                "parameters": {"type": "object", "properties": {}},
            }
        ]
        assert payload["tool_choice"] == {"type": "function", "name": "read_evidence"}
        assert payload["input"][-2:] == [
            {
                "type": "function_call",
                "call_id": "call-1",
                "name": "read_evidence",
                "arguments": "{}",
            },
            {"type": "function_call_output", "call_id": "call-1", "output": "verified text"},
        ]
        return httpx.Response(
            200,
            json=envelope(
                output=[
                    {
                        "type": "function_call",
                        "id": "fc-1",
                        "call_id": "call-2",
                        "name": "read_evidence",
                        "arguments": "{}",
                        "status": "completed",
                    },
                ]
            ),
        )

    gateway = make_gateway(monkeypatch, reply)
    result = gateway.request(
        "chat",
        "/chat/completions",
        {
            "model": "qwen3.8-max",
            "max_tokens": 100,
            "messages": [
                {"role": "user", "content": "hi"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "read_evidence", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call-1", "content": "verified text"},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "read_evidence",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            "tool_choice": {"type": "function", "function": {"name": "read_evidence"}},
        },
    )
    assert result["choices"][0]["message"]["tool_calls"][0]["id"] == "call-2"


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "failed", "error": {"message": "PRIVATE-SUPPLIER-BODY"}},
        {"status": "in_progress"},
        {"output": [{"type": "message", "content": [{"type": "refusal", "refusal": "secret"}]}]},
        {"output": [{"type": "reasoning", "summary": [{"text": "not an answer"}]}]},
    ],
)
def test_responses_rejects_failed_refused_or_missing_output(monkeypatch, changes):
    gateway = make_gateway(
        monkeypatch, lambda request: httpx.Response(200, json=envelope(**changes))
    )
    with pytest.raises(DomainError) as error:
        gateway.request(
            "chat", "/chat/completions", {"messages": [{"role": "user", "content": "JSON"}]}
        )
    assert "PRIVATE-SUPPLIER-BODY" not in str(error.value)


def test_responses_incomplete_json_does_not_pass_as_valid_ocr(monkeypatch):
    from app.document_ocr import transcribe_page

    gateway = make_gateway(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json=envelope(
                '{"text":"part","unreadable":false}',
                status="incomplete",
                incomplete_details={"reason": "max_output_tokens"},
            ),
        ),
    )
    with pytest.raises(DomainError) as error:
        transcribe_page(gateway, b"image", 1, "user")
    assert error.value.code == "SOURCE_UNVERIFIED"


def test_incomplete_but_valid_json_cannot_be_accepted_by_business_consumer(monkeypatch):
    gateway = make_gateway(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json=envelope(
                '{"category":"power"}',
                status="incomplete",
                incomplete_details={"reason": "max_output_tokens"},
            ),
        ),
    )
    with pytest.raises(DomainError):
        gateway.gap_intent("How do I turn it on?", "user")


def events(*items):
    return "".join("data: " + json.dumps(item) + "\n\n" for item in items)


def test_responses_stream_only_yields_text_and_requires_completed_event(monkeypatch):
    def reply(request):
        assert request.url.path.endswith("/responses")
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(
            200,
            text=events(
                {"type": "response.reasoning_summary_text.delta", "delta": "private"},
                {"type": "response.output_text.delta", "delta": "中文"},
                {"type": "response.completed", "response": envelope("中文")},
            ),
        )

    gateway = make_gateway(monkeypatch, reply)
    assert list(gateway.answer_stream("question", [], "user")) == ["中文"]


@pytest.mark.parametrize("ending", [None, "response.failed", "response.incomplete", "error"])
def test_responses_stream_disconnect_never_replays_partial_text(monkeypatch, ending):
    calls = []

    def reply(request):
        calls.append(request)
        stream = [{"type": "response.output_text.delta", "delta": "part"}]
        if ending:
            stream.append({"type": ending, "response": {"status": "failed"}})
        return httpx.Response(200, text=events(*stream))

    gateway = make_gateway(monkeypatch, reply)
    chunks = gateway.answer_stream("question", [], "user")
    assert next(chunks) == "part"
    with pytest.raises(DomainError):
        list(chunks)
    assert len(calls) == 1


def test_embedding_never_uses_responses(monkeypatch):
    def reply(request):
        assert request.url.path.endswith("/embeddings")
        assert json.loads(request.content)["input"] == ["hello"]
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] + [0.0] * 1023}]})

    gateway = make_gateway(monkeypatch, reply, embedding_api_key="test-embedding-key")
    assert len(gateway.embed(["hello"], "user")[0]) == 1024


def test_compatible_qwen_orphan_thinking_marker_does_not_break_json(monkeypatch):
    gateway = make_gateway(
        monkeypatch, lambda request: httpx.Response(200, json=envelope('</think>{"ok":true}'))
    )
    result = gateway.request(
        "chat", "/chat/completions", {"messages": [{"role": "user", "content": "JSON"}]}
    )
    assert json.loads(result["choices"][0]["message"]["content"]) == {"ok": True}


@pytest.mark.parametrize("json_mode", [True, False])
def test_complete_json_fence_is_normalized_only_for_structured_tasks(monkeypatch, json_mode):
    fenced = '```json\n{"ok":true}\n```'
    gateway = make_gateway(monkeypatch, lambda request: httpx.Response(200, json=envelope(fenced)))
    payload = {"messages": [{"role": "user", "content": "JSON"}]}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    result = gateway.request("chat", "/chat/completions", payload)
    content = result["choices"][0]["message"]["content"]
    assert content == ('{"ok":true}' if json_mode else fenced)


def test_opaque_reasoning_function_state_is_rejected_instead_of_executed(monkeypatch):
    gateway = make_gateway(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json=envelope(
                output=[
                    {
                        "type": "reasoning",
                        "id": "rs-private",
                        "encrypted_content": "opaque-private-state",
                    },
                    {
                        "type": "function_call",
                        "call_id": "call-1",
                        "name": "read_evidence",
                        "arguments": "{}",
                    },
                ]
            ),
        ),
    )
    with pytest.raises(DomainError):
        gateway.request(
            "chat", "/chat/completions", {"messages": [{"role": "user", "content": "test"}]}
        )


def test_responses_never_exposes_embedded_thinking_as_answer(monkeypatch):
    gateway = make_gateway(
        monkeypatch,
        lambda request: httpx.Response(200, json=envelope("<think>private</think>answer")),
    )
    with pytest.raises(DomainError):
        gateway.request(
            "chat", "/chat/completions", {"messages": [{"role": "user", "content": "hello"}]}
        )


@pytest.mark.parametrize("prefix", ["<think>private", "</think>answer"])
def test_stream_never_exposes_thinking_markup(monkeypatch, prefix):
    gateway = make_gateway(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            text=events(
                {"type": "response.output_text.delta", "delta": prefix},
                {"type": "response.completed", "response": envelope("answer")},
            ),
        ),
    )
    if prefix.startswith("<think>"):
        with pytest.raises(DomainError):
            list(gateway.answer_stream("question", [], "user"))
    else:
        assert list(gateway.answer_stream("question", [], "user")) == ["answer"]


def test_split_thinking_marker_is_not_rendered_before_the_answer(monkeypatch):
    gateway = make_gateway(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            text=events(
                {"type": "response.output_text.delta", "delta": "</th"},
                {"type": "response.output_text.delta", "delta": "ink>"},
                {"type": "response.output_text.delta", "delta": "answer"},
                {"type": "response.completed", "response": envelope("answer")},
            ),
        ),
    )
    assert list(gateway.answer_stream("question", [], "user")) == ["answer"]
