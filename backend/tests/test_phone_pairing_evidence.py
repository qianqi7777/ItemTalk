"""Chinese phone pairing must survive the complete answer policy and persistence path."""

import pytest

from app.adapters import ModelGateway
from app.config import settings
from app.grounding import AnswerSupport
from app.services import web_answer
from tests.test_api import post
from tests.test_supplemental_index import build_supplement

pytestmark = pytest.mark.postgres

PAIRING = (
    "ROSESELSA CERAMICS Ultra\nWireless Pairing\n"
    "Open the charging case cover, remove the earbuds, and wear them. "
    "The earbuds automatically enter wireless pairing mode. "
    "Open the wireless settings on your phone, select ROSE Ceramics U to connect."
)


def test_phone_pairing_keeps_supported_third_party_steps_and_citations(client, monkeypatch):
    product, _, _ = build_supplement(client, monkeypatch, PAIRING)
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    settings.cache_clear()
    monkeypatch.setattr(
        web_answer, "research", lambda *_a, **_kw: pytest.fail("Use indexed manual")
    )
    monkeypatch.setattr(
        ModelGateway,
        "answer",
        lambda *_a, **_kw: (
            "打开充电盒上盖，取出耳机并佩戴；耳机自动进入配对模式。"
            "打开手机蓝牙设置，选择 ROSE Ceramics U 连接。[1]"
        ),
    )
    monkeypatch.setattr(
        ModelGateway,
        "assess_answer",
        lambda *_a, **_kw: AnswerSupport(
            fullySupported=True,
            citations=[
                dict(
                    rank=1,
                    supported=True,
                    quote="Open the charging case cover, remove the earbuds, and wear them.",
                )
            ],
        ),
    )
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()
    path = f"/conversations/{conversation['id']}/messages"
    response = post(client, path, {"content": "怎么连手机？"})
    assert response.status_code == 201
    value = response.json()
    assert value["answerBasis"] == "web"
    assert value["webLookup"]["lookupMode"] == "supplemental_index"
    assert value["webSources"][0]["sourceKind"] == "third_party"
    assert "ROSE Ceramics U" in value["content"] and "手机" in value["content"]
    assert "通用建议" not in value["content"] and "电脑" not in value["content"]
    saved = client.get("/api/v1" + path).json()["items"][-1]
    assert saved["answerBasis"] == "web" and saved["webSources"]


def test_requested_reset_can_use_supported_third_party_instructions(client, monkeypatch):
    text = (
        PAIRING + "\nFactory reset: press the case reset button for ten seconds. "
        "This clears paired device records."
    )
    product, _, _ = build_supplement(client, monkeypatch, text)
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    settings.cache_clear()
    monkeypatch.setattr(
        web_answer, "research", lambda *_a, **_kw: pytest.fail("Use indexed manual")
    )
    monkeypatch.setattr(
        ModelGateway,
        "answer",
        lambda *_a, **_kw: "长按复位按钮十秒恢复出厂设置，会清除已配对记录。[1]",
    )
    monkeypatch.setattr(
        ModelGateway,
        "assess_answer",
        lambda *_a, **_kw: AnswerSupport(
            fullySupported=True,
            citations=[
                dict(
                    rank=1,
                    supported=True,
                    quote="Factory reset: press the case reset button for ten seconds.",
                )
            ],
        ),
    )
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()
    saved = post(
        client, f"/conversations/{conversation['id']}/messages", {"content": "怎么恢复出厂设置？"}
    ).json()
    assert saved["answerBasis"] == "web" and saved["webSources"]
    assert saved["webSources"][0]["sourceKind"] == "third_party"


def test_insufficient_local_index_searches_live_sources_before_model_inference(client, monkeypatch):
    from app.errors import DomainError

    product, _, _ = build_supplement(client, monkeypatch, PAIRING)
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    settings.cache_clear()
    body = "ROSESELSA CERAMICS Ultra 音量操作：五次点按左耳增加音量。"

    def generated(_g, _q, evidence, *_a, **_kw):
        return (
            "五次点按左耳增加音量。[1]"
            if body in evidence[0]["content"]
            else "现有资料不足，无法回答。"
        )

    monkeypatch.setattr(ModelGateway, "answer", generated)
    monkeypatch.setattr(
        ModelGateway,
        "assess_answer",
        lambda *_a, **_kw: AnswerSupport(
            fullySupported=True, citations=[dict(rank=1, supported=True, quote=body)]
        ),
    )
    monkeypatch.setattr(
        web_answer,
        "research",
        lambda *_a, **_kw: dict(
            status="found",
            reason=None,
            queryKey="volume",
            searchedAt="2026-11-07T00:00:00Z",
            cached=False,
            sources=[
                dict(
                    id="live-volume",
                    rank=1,
                    title="CERAMICS Ultra 使用资料",
                    url="https://guide.example/ceramics-ultra",
                    sourceKind="third_party",
                    fetchedAt="2026-11-07T00:00:00Z",
                    contentSha256="a" * 64,
                    excerpt=body,
                    pageStart=None,
                    pageEnd=None,
                    modelMatch="exact",
                )
            ],
        ),
    )

    def no_guess(*_a, **_kw):
        raise DomainError(
            502, "UPSTREAM_MODEL_ERROR", "Live evidence should be attempted before inference"
        )

    monkeypatch.setattr(ModelGateway, "reference_answer", no_guess)
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()
    value = post(
        client, f"/conversations/{conversation['id']}/messages", {"content": "怎么增加音量？"}
    )
    assert value.status_code == 201
    assert (
        value.json()["answerBasis"] == "web"
        and value.json()["webSources"][0]["id"] == "live-volume"
    )


@pytest.mark.parametrize("failure", ["embedding", "index_verifier"])
def test_live_sources_are_tried_when_an_index_dependency_fails(client, monkeypatch, failure):
    from app.errors import DomainError

    product, _, _ = build_supplement(client, monkeypatch, PAIRING)
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    settings.cache_clear()
    body = "ROSESELSA CERAMICS Ultra 音量操作：五次点按左耳增加音量。"
    if failure == "embedding":

        def broken_embedding(*_a, **_kw):
            raise DomainError(502, "UPSTREAM_MODEL_ERROR", "Vector service unavailable")

        monkeypatch.setattr(ModelGateway, "embed", broken_embedding)
    monkeypatch.setattr(ModelGateway, "answer", lambda *_a, **_kw: "五次点按左耳增加音量。[1]")

    def assessment(_g, _q, _answer, evidence, *_a, **_kw):
        if body not in evidence[0]["content"]:
            raise DomainError(502, "UPSTREAM_MODEL_ERROR", "Invalid index verifier response")
        return AnswerSupport(
            fullySupported=True, citations=[dict(rank=1, supported=True, quote=body)]
        )

    monkeypatch.setattr(ModelGateway, "assess_answer", assessment)
    monkeypatch.setattr(
        web_answer,
        "research",
        lambda *_a, **_kw: dict(
            status="found",
            reason=None,
            queryKey="volume",
            searchedAt="2026-11-07T00:00:00Z",
            cached=False,
            sources=[
                dict(
                    id="healthy-live",
                    rank=1,
                    title="CERAMICS Ultra 资料",
                    url="https://guide.example/volume",
                    sourceKind="third_party",
                    fetchedAt="2026-11-07T00:00:00Z",
                    contentSha256="b" * 64,
                    excerpt=body,
                    pageStart=None,
                    pageEnd=None,
                    modelMatch="exact",
                )
            ],
        ),
    )
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()
    response = post(
        client, f"/conversations/{conversation['id']}/messages", {"content": "怎么增加音量？"}
    )
    assert response.status_code == 201
    assert (
        response.json()["answerBasis"] == "web"
        and response.json()["webSources"][0]["id"] == "healthy-live"
    )
