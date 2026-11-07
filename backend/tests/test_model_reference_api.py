"""No evidence permits an honestly labeled model reference, never fake third-party citations."""

from types import SimpleNamespace

import pytest

from app.adapters import ModelGateway
from app.services import web_answer
from tests.conftest import register_login
from tests.test_api import identified_product, post

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("stream", [False, True])
def test_no_sources_generates_model_specific_reference_and_preserves_it_after_reload(
    client, monkeypatch, stream
):
    register_login(client)
    product = identified_product(client)
    monkeypatch.setattr(
        web_answer,
        "research",
        lambda *_a, **_kw: dict(
            status="empty",
            reason="no_results",
            sources=[],
            queryKey="phone",
            searchedAt="2026-11-07T00:00:00Z",
            cached=False,
        ),
    )

    def reference(_g, question, model, subject, memories=None):
        assert question == "怎么连手机？" and model["model_code"] == "DEMO-100"
        return SimpleNamespace(
            answer="针对 DEMO-100，请先确认它支持的手机连接方式，再在手机中查找设备。",
            uncertainty="尚未查到该型号的连接说明，连接方式需要你确认。",
        )

    monkeypatch.setattr(ModelGateway, "reference_answer", reference, raising=False)
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()
    endpoint = "stream" if stream else "messages"
    response = post(
        client, f"/conversations/{conversation['id']}/{endpoint}", {"content": "怎么连手机？"}
    )
    assert response.status_code == (200 if stream else 201)
    value = client.get(f"/api/v1/conversations/{conversation['id']}/messages").json()["items"][-1]
    assert value["webLookup"].get("lookupMode") == "model_reference"
    assert value["answerBasis"] == "general" and not value["webSources"] and not value["citations"]
    assert value["content"].startswith("模型推测 · 未经资料核验\n\n")
    assert "DEMO-100" in value["content"] and "需要你确认" in value["content"]
    assert "通用建议" not in value["content"] and "官方已确认" not in value["content"]


def test_model_reference_failure_is_not_saved_as_a_successful_generic_answer(client, monkeypatch):
    from app.errors import DomainError

    register_login(client)
    product = identified_product(client)
    monkeypatch.setattr(
        web_answer,
        "research",
        lambda *_a, **_kw: dict(
            status="empty",
            reason="no_results",
            sources=[],
            queryKey="phone",
            searchedAt="2026-11-07T00:00:00Z",
            cached=False,
        ),
    )

    def unavailable(*_a, **_kw):
        raise DomainError(502, "UPSTREAM_MODEL_ERROR", "参考回答服务暂时不可用")

    monkeypatch.setattr(ModelGateway, "reference_answer", unavailable, raising=False)
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()
    response = post(
        client, f"/conversations/{conversation['id']}/messages", {"content": "怎么连手机？"}
    )
    assert response.status_code == 502
    messages = client.get(f"/api/v1/conversations/{conversation['id']}/messages").json()["items"]
    assert not any(row["role"] == "assistant" for row in messages)


def test_model_reference_cancelled_during_generation_is_not_committed(client, monkeypatch):
    from app.db import execute, transaction

    register_login(client)
    product = identified_product(client)
    monkeypatch.setattr(
        web_answer,
        "research",
        lambda *_a, **_kw: dict(
            status="empty",
            reason="no_results",
            sources=[],
            queryKey="phone",
            searchedAt="2026-11-07T00:00:00Z",
            cached=False,
        ),
    )

    def cancel(*_a, **_kw):
        with transaction() as c:
            execute(c, "UPDATE assistant_runs SET status='cancelled' WHERE status='running'")
        return SimpleNamespace(answer="不能提交此回答", uncertainty="尚未核验")

    monkeypatch.setattr(ModelGateway, "reference_answer", cancel)
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()
    response = post(
        client, f"/conversations/{conversation['id']}/messages", {"content": "怎么连手机？"}
    )
    assert response.status_code == 409
    messages = client.get(f"/api/v1/conversations/{conversation['id']}/messages").json()["items"]
    assert not any(row["role"] == "assistant" for row in messages)
