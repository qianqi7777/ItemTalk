"""API/Worker资料上的引用核验；删去多余引用、拒绝无依据结论并重查撤销。"""

from uuid import UUID

import pytest

from app.adapters import ModelGateway
from app.db import execute, many, one, transaction
from app.services import chat
from app.services.knowledge import persist_document, publish_chunks
from tests.conftest import register_login
from tests.test_api import post
from tests.test_v1_content import ready_product

pytestmark = pytest.mark.postgres
BODY = "按住电源2秒，直到蓝灯熄灭[1][2]。"
PAGES = [(1, "打开设备：按住电源2秒，直到蓝灯闪烁。"), (2, "关闭设备：按住电源2秒，直到蓝灯熄灭。")]


def answer_case(client, monkeypatch):
    register_login(client)
    product = ready_product(client)
    with transaction() as connection:
        model = one(
            connection,
            "SELECT * FROM product_models WHERE id=:id",
            id=UUID(product["productModelId"]),
        )
        version = persist_document(
            connection,
            model,
            "https://example.invalid/action-grounding",
            "action grounding",
            "text/plain",
            b"two-action-fixture",
            PAGES,
            True,
        )
        publish_chunks(connection, model, version, PAGES, [[1.0] + [0.0] * 1023] * 2)
        rows = many(
            connection,
            "SELECT k.*,v.version_label,v.verified_at,d.title,s.url,s.id AS source_id "
            "FROM knowledge_chunks k JOIN document_versions v ON v.id=k.document_version_id "
            "JOIN documents d ON d.id=v.document_id JOIN sources s ON s.id=d.source_id "
            "WHERE v.id=:v ORDER BY k.chunk_no",
            v=version["id"],
        )
    monkeypatch.setattr(chat, "retrieve", lambda *_args, **_kwargs: rows)
    monkeypatch.setattr(ModelGateway, "answer", lambda *_args, **_kwargs: BODY)
    monkeypatch.setattr(ModelGateway, "answer_stream", lambda *_args, **_kwargs: iter([BODY]))
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()
    return conversation, rows


def assessment():
    return {
        "fullySupported": True,
        "citations": [
            {"rank": 1, "supported": False, "quote": ""},
            {"rank": 2, "supported": True, "quote": PAGES[1][1]},
        ],
    }


def latest(client, conversation):
    return client.get(f"/api/v1/conversations/{conversation['id']}/messages").json()["items"]


@pytest.mark.parametrize("stream", [False, True])
def test_only_supported_references_reach_final_answer_and_history(client, monkeypatch, stream):
    conversation, _ = answer_case(client, monkeypatch)
    calls = []

    def assess(_self, question, answer, evidence, subject, memories=None):
        calls.append(evidence)
        assert answer == BODY and [row["rank"] for row in evidence] == [1, 2]
        return assessment()

    monkeypatch.setattr(ModelGateway, "assess_answer", assess)
    endpoint = "stream" if stream else "messages"
    response = post(
        client, f"/conversations/{conversation['id']}/{endpoint}", {"content": "如何关闭设备？"}
    )
    assert response.status_code == (200 if stream else 201)
    message = latest(client, conversation)[-1]
    assert message["content"] == "按住电源2秒，直到蓝灯熄灭[2]。"
    assert [row["rank"] for row in message["citations"]] == [2]
    assert message["citations"][0]["pageStart"] == 2 and len(calls) == 1
    if stream:
        assert "event: done" in response.text


@pytest.mark.parametrize("stream", [False, True])
def test_partial_fact_support_becomes_insufficiency_not_a_verified_answer(
    client, monkeypatch, stream
):
    conversation, _ = answer_case(client, monkeypatch)
    monkeypatch.setattr(
        ModelGateway,
        "assess_answer",
        lambda *_args, **_kwargs: {**assessment(), "fullySupported": False},
    )
    endpoint = "stream" if stream else "messages"
    response = post(
        client,
        f"/conversations/{conversation['id']}/{endpoint}",
        {"content": "还包括哪些未知功能？"},
    )
    assert response.status_code == (200 if stream else 201)
    message = latest(client, conversation)[-1]
    assert "资料不足" in message["content"] and message["citations"] == []


@pytest.mark.parametrize("stream", [False, True])
def test_invented_verification_quote_cannot_publish_assistant_message(client, monkeypatch, stream):
    conversation, _ = answer_case(client, monkeypatch)
    value = assessment()
    value["citations"][1]["quote"] = "虚构的支持原文"
    monkeypatch.setattr(ModelGateway, "assess_answer", lambda *_args, **_kwargs: value)
    endpoint = "stream" if stream else "messages"
    response = post(
        client, f"/conversations/{conversation['id']}/{endpoint}", {"content": "如何关闭设备？"}
    )
    assert response.status_code == (200 if stream else 502)
    assert "虚构的支持原文" not in response.text
    assert all(message["role"] != "assistant" for message in latest(client, conversation))


@pytest.mark.parametrize("target", ["source", "chunk"])
def test_revocation_during_verification_stops_new_verified_message(client, monkeypatch, target):
    conversation, rows = answer_case(client, monkeypatch)

    def revoke(*_args, **_kwargs):
        with transaction() as connection:
            if target == "source":
                execute(
                    connection,
                    "UPDATE sources SET verification_status='revoked' WHERE id=:id",
                    id=rows[1]["source_id"],
                )
            else:
                execute(
                    connection,
                    "UPDATE knowledge_chunks SET verification_status='revoked' WHERE id=:id",
                    id=rows[1]["id"],
                )
        return assessment()

    monkeypatch.setattr(ModelGateway, "assess_answer", revoke)
    response = post(
        client, f"/conversations/{conversation['id']}/messages", {"content": "如何关闭设备？"}
    )
    assert response.status_code == 409
    assert all(message["role"] != "assistant" for message in latest(client, conversation))


def test_verification_budget_is_charged_once_and_idempotent_replay_is_free(client):
    register_login(client)
    product = ready_product(client)
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()

    def amount():
        with transaction() as connection:
            return one(
                connection, "SELECT amount FROM usage_counters WHERE subject='model:global'"
            )["amount"]

    before = amount()
    first = post(
        client,
        f"/conversations/{conversation['id']}/messages",
        {"content": "如何开机？"},
        key="grounding-budget",
    )
    assert first.status_code == 201 and amount() - before == 3
    replay = post(
        client,
        f"/conversations/{conversation['id']}/messages",
        {"content": "如何开机？"},
        key="grounding-budget",
    )
    assert replay.status_code == 201 and amount() - before == 3


def test_cancelled_generation_does_not_start_paid_citation_verification(client, monkeypatch):
    conversation, _ = answer_case(client, monkeypatch)

    def cancelled(_self, _question, _evidence, subject, **_kwargs):
        with transaction() as connection:
            execute(
                connection,
                "UPDATE assistant_runs SET status='cancelled',finished_at=now() "
                "WHERE user_id=:u AND status='running'",
                u=subject,
            )
        return BODY

    monkeypatch.setattr(ModelGateway, "answer", cancelled)
    monkeypatch.setattr(
        ModelGateway,
        "assess_answer",
        lambda *_args, **_kwargs: pytest.fail("Cancelled run must not start verification"),
    )
    response = post(
        client, f"/conversations/{conversation['id']}/messages", {"content": "如何关闭设备？"}
    )
    assert response.status_code == 409
    assert all(message["role"] != "assistant" for message in latest(client, conversation))
