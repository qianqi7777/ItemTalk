"""供应商 SSE 正文与推理隔离、提前断线及输出维度协议测试。"""

import httpx
import pytest

from app.adapters import ModelGateway
from app.config import Settings
from app.errors import DomainError


def gateway_for(monkeypatch, data):
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=data))
    )
    gateway = ModelGateway(client)
    gateway.config = Settings(model_provider="dashscope", model_api_key="test-key")
    monkeypatch.setattr(gateway, "budget", lambda subject: None)
    return gateway


def test_stream_only_exposes_content(monkeypatch):
    gateway = gateway_for(
        monkeypatch,
        'data: {"choices":[{"delta":{"reasoning_content":"private reasoning"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"中文"}}]}\n\n'
        "data: [DONE]\n\n",
    )
    assert list(gateway.answer_stream("问题", [], "user")) == ["中文"]


def test_premature_stream_end_is_failure(monkeypatch):
    gateway = gateway_for(monkeypatch, 'data: {"choices":[{"delta":{"content":"片段"}}]}\n\n')
    with pytest.raises(DomainError):
        list(gateway.answer_stream("问题", [], "user"))
