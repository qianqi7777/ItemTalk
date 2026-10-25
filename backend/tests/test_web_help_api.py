"""Persisted private web provenance and the question-time online fallback path."""

import pytest

from app.adapters import ModelGateway
from app.db import json_value, one, transaction
from app.services import web_research
from tests.conftest import register_login
from tests.test_api import identified_product, post

pytestmark = pytest.mark.postgres


def test_web_lookup_schema_and_history_snapshot_are_private(client):
    user = register_login(client)
    product = identified_product(client)
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()
    lookup = {
        "status": "found",
        "reason": None,
        "searchedAt": "2026-10-25T10:00:00+00:00",
        "queryKey": "safe-key",
        "cached": False,
        "sources": [
            {
                "id": "source-1",
                "rank": 1,
                "title": "产品教程",
                "url": "https://guide.example/help",
                "sourceKind": "third_party",
                "fetchedAt": "2026-10-25T10:00:00+00:00",
                "excerpt": "private bounded evidence",
                "contentSha256": "a" * 64,
                "pageStart": None,
                "pageEnd": None,
            }
        ],
    }
    with transaction() as connection:
        row = one(
            connection,
            "INSERT INTO messages(conversation_id,user_id,role,content,answer_basis,web_lookup) "
            "VALUES (:c,:u,'assistant','联网回答[1]','web',CAST(:lookup AS jsonb)) RETURNING id",
            c=conversation["id"],
            u=user["id"],
            lookup=json_value(lookup),
        )
    saved = client.get(f"/api/v1/conversations/{conversation['id']}/messages").json()["items"][-1]
    assert saved["id"] == str(row["id"]) and saved["answerBasis"] == "web"
    assert saved["webSources"][0]["sourceKind"] == "third_party"
    assert "excerpt" not in saved["webSources"][0]
    assert saved["webLookup"]["searchedAt"] == lookup["searchedAt"]
    assert (
        post(
            client,
            f"/conversations/{conversation['id']}/messages",
            {"content": "问", "webLookup": lookup},
        ).status_code
        == 422
    )
    assert client.delete("/api/v1/me").status_code == 202


def test_force_web_search_is_an_explicit_validated_request_field(client):
    register_login(client)
    product = identified_product(client)
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()
    response = post(
        client,
        f"/conversations/{conversation['id']}/messages",
        {"content": "蓝牙连接不上？", "forceWebSearch": True},
    )
    assert response.status_code == 201


@pytest.fixture
def online(monkeypatch):
    calls = []
    body = "演示设备 DEMO-100 CN 蓝牙连接教程：在电脑蓝牙设置中选择 DEMO-100，确认连接。"

    class Search:
        def search(self, query, **kwargs):
            calls.append(query)
            return [
                {"url": "https://guide.example/demo-100", "title": "演示设备 DEMO-100 CN 蓝牙教程"}
            ]

    monkeypatch.setattr(web_research, "SearchGateway", Search)
    monkeypatch.setattr(
        web_research,
        "fetch_public",
        lambda *_args, **_kwargs: ("https://guide.example/demo-100", "text/html", body.encode()),
    )
    monkeypatch.setattr(web_research, "parse_document", lambda *_args, **_kwargs: [(1, body)])
    monkeypatch.setattr(
        ModelGateway,
        "answer",
        lambda *_args, **_kwargs: "在电脑蓝牙设置中选择 DEMO-100，确认连接。[1]",
    )
    monkeypatch.setattr(
        ModelGateway,
        "assess_answer",
        lambda *_args, **_kwargs: {
            "fullySupported": True,
            "citations": [{"rank": 1, "supported": True, "quote": body}],
        },
    )
    return calls


@pytest.mark.parametrize("stream", [False, True])
def test_no_local_evidence_searches_now_and_persists_real_web_sources(client, online, stream):
    register_login(client)
    product = identified_product(client)
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()
    response = post(
        client,
        f"/conversations/{conversation['id']}/{'stream' if stream else 'messages'}",
        {"content": "蓝牙连接不上怎么办？"},
    )
    assert response.status_code == (200 if stream else 201)
    assert online, "must actually search rather than directly emit a generic template"
    saved = client.get(f"/api/v1/conversations/{conversation['id']}/messages").json()["items"][-1]
    assert saved["answerBasis"] == "web" and "通用建议" not in saved["content"]
    assert saved["webSources"][0]["sourceKind"] == "third_party" and saved["citations"] == []
    if stream:
        assert '"status": "web_searching"' in response.text


def test_cached_lookup_reused_and_force_bypasses_without_mutating_old_answer(client, online):
    register_login(client)
    product = identified_product(client)
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()
    path = f"/conversations/{conversation['id']}/messages"
    first = post(client, path, {"content": "蓝牙连接不上？"}).json()
    count = len(online)
    second = post(client, path, {"content": "怎么蓝牙配对？"}).json()
    assert first["answerBasis"] == second["answerBasis"] == "web"
    assert second["webLookup"]["cached"] and len(online) == count
    forced = post(client, path, {"content": "蓝牙连接不上？", "forceWebSearch": True}).json()
    assert not forced["webLookup"]["cached"] and len(online) > count
    assert first["id"] != forced["id"]


def test_search_failure_falls_back_with_truthful_reason(client, monkeypatch):
    register_login(client)
    product = identified_product(client)
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()
    from app.errors import DomainError

    monkeypatch.setattr(
        web_research.SearchGateway,
        "search",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            DomainError(503, "UPSTREAM_SEARCH_ERROR", "不可用")
        ),
    )
    saved = post(
        client, f"/conversations/{conversation['id']}/messages", {"content": "蓝牙连接不上？"}
    ).json()
    assert (
        saved["answerBasis"] == "general" and saved["webLookup"]["reason"] == "search_unavailable"
    )


def test_third_party_never_enables_firmware_steps(client, online):
    register_login(client)
    product = identified_product(client)
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()
    saved = post(
        client, f"/conversations/{conversation['id']}/messages", {"content": "如何刷固件？"}
    ).json()
    assert online and saved["answerBasis"] == "insufficient" and not saved["webSources"]
