"""Persisted private web provenance and the question-time online fallback path."""

import pytest

from app.db import json_value, one, transaction
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
