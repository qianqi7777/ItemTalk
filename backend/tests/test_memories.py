"""记忆候选、用户确认、实例隔离、过期/删除和模型上下文回归。"""

from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError

from app.adapters import ModelGateway
from app.db import execute, many, one, transaction
from app.main import app
from app.schemas import MemoryExtraction
from app.services.memories import confirmed
from app.worker import run_once
from tests.conftest import HEADERS, register_login
from tests.test_api import post
from tests.test_v1_content import ready_product

pytestmark = pytest.mark.postgres


def ask_and_extract(client, product, text):
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()
    response = post(client, f"/conversations/{conversation['id']}/messages", {"content": text})
    assert response.status_code == 201, response.text
    with transaction() as connection:
        job = one(
            connection,
            "SELECT id FROM jobs WHERE job_type='memory_extract' ORDER BY created_at DESC LIMIT 1",
        )
    assert run_once(job["id"])
    return client.get(f"/api/v1/me/memories?instanceId={product['id']}").json()["items"]


def test_candidate_not_used_until_confirmation_and_delete_excludes_context(client, monkeypatch):
    user = register_login(client)
    product = ready_product(client)
    candidates = ask_and_extract(client, product, "请记住我喜欢简短中文，如何开机？")
    assert len(candidates) == 1 and candidates[0]["consentStatus"] == "pending"
    with transaction() as connection:
        assert confirmed(connection, UUID(user["id"]), UUID(product["id"])) == []
    memory = candidates[0]
    response = post(client, f"/me/memories/{memory['id']}/confirm", {})
    assert response.status_code == 200 and response.json()["confirmedAt"]
    original = ModelGateway.answer
    contexts = []

    def capture(gateway, question, evidence, subject, memories=None):
        contexts.append(memories)
        return original(gateway, question, evidence, subject, memories=memories)

    monkeypatch.setattr(ModelGateway, "answer", capture)
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()
    assert (
        post(
            client, f"/conversations/{conversation['id']}/messages", {"content": "如何开机"}
        ).status_code
        == 201
    )
    assert contexts[-1] == [{"memoryType": "preference", "content": memory["content"]}]
    assert client.delete(f"/api/v1/me/memories/{memory['id']}").status_code == 204
    assert post(client, f"/me/memories/{memory['id']}/confirm", {}).status_code == 404
    assert (
        post(
            client, f"/conversations/{conversation['id']}/messages", {"content": "如何开机"}
        ).status_code
        == 201
    )
    assert contexts[-1] == []


def test_instance_fact_never_crosses_to_another_device_or_user(client, monkeypatch):
    user = register_login(client)
    product = ready_product(client)
    monkeypatch.setattr(
        ModelGateway,
        "extract_memories",
        lambda *_: MemoryExtraction.model_validate(
            {"memories": [{"memoryType": "product_fact", "content": "本人设备已完成蓝牙配对"}]}
        ),
    )
    candidates = ask_and_extract(client, product, "请记住，这台设备已经配对，如何开机？")
    memory = candidates[0]
    assert memory["scope"] == "instance" and memory["userProductInstanceId"] == product["id"]
    assert post(client, f"/me/memories/{memory['id']}/confirm", {}).status_code == 200
    other_product = post(
        client, "/me/products", {"productModelId": product["productModelId"], "nickname": "另一台"}
    ).json()
    with transaction() as connection:
        assert confirmed(connection, UUID(user["id"]), UUID(product["id"]))
        assert not confirmed(connection, UUID(user["id"]), UUID(other_product["id"]))
    with pytest.raises(DBAPIError), transaction() as connection:
        source = one(
            connection,
            "SELECT source_message_id FROM user_memories WHERE id=:id",
            id=UUID(memory["id"]),
        )
        execute(
            connection,
            "INSERT INTO user_memories(user_id,memory_type,scope,user_product_instance_id,"
            "source_message_id,content,content_hash) "
            "VALUES (:u,'product_fact','instance',:i,:message,'wrong instance',:hash)",
            u=UUID(user["id"]),
            i=UUID(other_product["id"]),
            message=source["source_message_id"],
            hash="0" * 64,
        )
    with TestClient(app, base_url="http://localhost:5173", headers=HEADERS) as other:
        register_login(other, "other@example.com")
        assert other.get("/api/v1/me/memories").json()["items"] == []
        assert post(other, f"/me/memories/{memory['id']}/confirm", {}).status_code == 404
        assert other.delete(f"/api/v1/me/memories/{memory['id']}").status_code == 404


def test_expired_memory_is_not_confirmable_or_used(client):
    user = register_login(client)
    product = ready_product(client)
    memory = ask_and_extract(client, product, "请记住我喜欢中文，如何开机？")[0]
    with transaction() as connection:
        execute(
            connection,
            "UPDATE user_memories SET expires_at=now()-interval '1 second' WHERE id=:id",
            id=UUID(memory["id"]),
        )
        assert not confirmed(connection, UUID(user["id"]), UUID(product["id"]))
    assert post(client, f"/me/memories/{memory['id']}/confirm", {}).status_code == 409
    assert not client.get("/api/v1/me/memories").json()["items"]


def test_secret_candidate_is_discarded_and_source_instance_constraint_holds(client, monkeypatch):
    user = register_login(client)
    product = ready_product(client)
    monkeypatch.setattr(
        ModelGateway,
        "extract_memories",
        lambda *_: MemoryExtraction.model_validate(
            {"memories": [{"memoryType": "preference", "content": "请记住密码 fake-secret"}]}
        ),
    )
    assert not ask_and_extract(client, product, "请记住我的偏好，如何开机？")
    with transaction() as connection:
        assert not many(connection, "SELECT * FROM user_memories")
        assert one(connection, "SELECT user_id FROM jobs WHERE job_type='memory_extract'")[
            "user_id"
        ] == UUID(user["id"])
