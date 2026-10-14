"""V1说明书、反馈、缺口与历史列表的真实数据库/API回归。"""

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError

from app.adapters import ModelGateway
from app.db import execute, many, one, transaction
from app.main import app
from app.services import manuals
from app.services.gaps import SUMMARIES, redacted_question
from app.services.knowledge import persist_document, publish_chunks
from app.worker import run_once
from tests.conftest import HEADERS, register_login
from tests.test_api import identified_product, post

pytestmark = pytest.mark.postgres


def ready_product(client):
    product = identified_product(client)
    assert run_once(UUID(product["knowledgeJobId"]))
    return product


def test_manual_generation_and_immutable_versions(client):
    register_login(client)
    product = ready_product(client)
    path = f"/products/{product['productModelId']}/manual"
    initial = client.get("/api/v1" + path).json()
    assert initial["status"] == "pending"
    assert len(initial["sections"]) == 4
    assert initial["sections"][-1]["sectionKey"].startswith("source-")
    assert run_once(UUID(initial["jobId"]))
    manual = client.get("/api/v1" + path).json()
    assert manual["status"] == "ready" and manual["version"] == 1
    assert all(
        section["generationStatus"] == "ready" and section["citations"]
        for section in manual["sections"]
    )
    chapter = client.get(f"/api/v1/manuals/{manual['id']}/sections/{manual['sections'][0]['id']}")
    assert chapter.status_code == 200
    assert chapter.json()["citations"][0]["pageStart"] == 1
    with pytest.raises(DBAPIError), transaction() as connection:
        execute(
            connection,
            "UPDATE manual_sections SET body='changed' WHERE id=:id",
            id=UUID(chapter.json()["id"]),
        )
    with pytest.raises(DBAPIError), transaction() as connection:
        execute(
            connection,
            "DELETE FROM manual_section_citations WHERE manual_section_id=:id",
            id=UUID(chapter.json()["id"]),
        )
    with transaction() as connection:
        assert manuals.schedule(connection, UUID(product["productModelId"]))["id"] == UUID(
            manual["id"]
        )
        assert one(connection, "SELECT count(*) AS n FROM jobs WHERE job_type='manual'")["n"] == 1


def test_revoked_evidence_and_private_manual_selection(client, monkeypatch):
    person = register_login(client)
    product = ready_product(client)
    public = client.get(f"/api/v1/products/{product['productModelId']}/manual").json()
    original = ModelGateway.answer

    def revoke(gateway, *args):
        answer = original(gateway, *args)
        with transaction() as connection:
            execute(connection, "UPDATE sources SET verification_status='revoked'")
        return answer

    monkeypatch.setattr(ModelGateway, "answer", revoke)
    assert run_once(UUID(public["jobId"]))
    current = client.get(f"/api/v1/products/{product['productModelId']}/manual").json()
    assert not any(section["generationStatus"] == "ready" for section in current["sections"])
    with transaction() as connection:
        private = one(
            connection,
            "INSERT INTO manuals(product_model_id,user_product_instance_id,owne"
            "r_user_id,visibility,version,status,evidence_hash) "
            "VALUES (:m,:i,:u,'private',1,'pending',:hash) RETURNING id",
            m=UUID(product["productModelId"]),
            i=UUID(product["id"]),
            u=UUID(person["id"]),
            hash="f" * 64,
        )
    own = client.get(
        f"/api/v1/products/{product['productModelId']}/manual?instanceId={product['id']}"
    )
    assert own.json()["id"] == str(private["id"])
    with TestClient(app, base_url="http://localhost:5173", headers=HEADERS) as other:
        register_login(other, "reader@example.com")
        assert other.get(f"/api/v1/manuals/{private['id']}/sections/{uuid4()}").status_code == 404
        assert (
            other.get(
                f"/api/v1/products/{product['productModelId']}/manual?instanceId={product['id']}"
            ).status_code
            == 404
        )


def test_new_partial_version_keeps_old_ready_manual(client, monkeypatch):
    register_login(client)
    product = ready_product(client)
    path = f"/api/v1/products/{product['productModelId']}/manual"
    first = client.get(path).json()
    assert run_once(UUID(first["jobId"]))
    old = client.get(path).json()
    text = "DEMO-100 CN\n新增蓝牙配对说明。"
    vector = ModelGateway().embed([text], "manual-version-test")
    with transaction() as connection:
        model = one(
            connection,
            "SELECT * FROM product_models WHERE id=:id",
            id=UUID(product["productModelId"]),
        )
        version = persist_document(
            connection,
            model,
            "https://example.invalid/itemtalk-demo",
            "新的演示资料",
            "text/plain",
            text.encode(),
            [(1, text)],
            True,
        )
        publish_chunks(connection, model, version, [(1, text)], vector)
        newer = manuals.schedule(connection, model["id"])
    pending = client.get(path).json()
    assert pending["id"] == old["id"] and pending["latestBuild"]["version"] == 2
    monkeypatch.setattr(ModelGateway, "answer", lambda *_: "没有有效编号的回答")
    assert run_once(newer["job_id"])
    partial = client.get(path).json()
    assert partial["id"] == old["id"] and partial["status"] == "ready"
    assert partial["latestBuild"]["status"] == "partial"
    assert partial["sections"][0]["body"] == old["sections"][0]["body"]


def test_manual_tree_rejects_cross_manual_parent_and_cycles(client):
    person = register_login(client)
    product = ready_product(client)
    with transaction() as connection:
        manuals_ids = []
        for version in (1, 2):
            row = one(
                connection,
                "INSERT INTO manuals(product_model_id,user_product_instance_id,owner_user_id,"
                "visibility,version,status,evidence_hash) "
                "VALUES (:m,:i,:u,'private',:v,'pending',:h) "
                "RETURNING id",
                m=UUID(product["productModelId"]),
                i=UUID(product["id"]),
                u=UUID(person["id"]),
                v=version,
                h=str(version) * 64,
            )
            manuals_ids.append(row["id"])
        ids = []
        for number, manual_id in enumerate([manuals_ids[0], manuals_ids[0], manuals_ids[1]]):
            node = one(
                connection,
                "INSERT INTO manual_sections(manual_id,section_key,title,sort_order,"
                "generation_status) "
                "VALUES (:m,:key,'章节',:n,'pending') RETURNING id",
                m=manual_id,
                key=f"node-{number}",
                n=number,
            )
            ids.append(node["id"])
    with pytest.raises(DBAPIError) as cross, transaction() as connection:
        execute(
            connection,
            "UPDATE manual_sections SET parent_id=:parent WHERE id=:id",
            parent=ids[2],
            id=ids[0],
        )
    assert cross.value.orig.sqlstate == "23503"
    with pytest.raises(DBAPIError) as cyclic, transaction() as connection:
        execute(
            connection,
            "UPDATE manual_sections SET parent_id=:parent WHERE id=:id",
            parent=ids[1],
            id=ids[0],
        )
        execute(
            connection,
            "UPDATE manual_sections SET parent_id=:parent WHERE id=:id",
            parent=ids[0],
            id=ids[1],
        )
    assert cyclic.value.orig.sqlstate == "P0001"


def test_feedback_is_owned_upsert_and_rejects_user_messages(client):
    register_login(client)
    product = ready_product(client)
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()
    response = post(
        client, f"/conversations/{conversation['id']}/messages", {"content": "如何开机？"}
    ).json()
    path = f"/messages/{response['id']}/feedback"
    assert post(client, path, {"feedbackType": "helpful"}).status_code == 204
    assert (
        post(client, path, {"feedbackType": "incorrect", "note": "不是这个按键"}).status_code == 204
    )
    messages = client.get(f"/api/v1/conversations/{conversation['id']}/messages").json()["items"]
    output = next(m for m in messages if m["id"] == response["id"])
    assert output["feedback"] == {"feedbackType": "incorrect", "note": "不是这个按键"}
    user_message = next(m for m in messages if m["role"] == "user")
    assert (
        post(
            client, f"/messages/{user_message['id']}/feedback", {"feedbackType": "helpful"}
        ).status_code
        == 422
    )
    with TestClient(app, base_url="http://localhost:5173", headers=HEADERS) as other:
        register_login(other, "other@example.com")
        assert post(other, path, {"feedbackType": "helpful"}).status_code == 404
    with transaction() as connection:
        assert one(connection, "SELECT count(*) AS n FROM message_feedback")["n"] == 1


def test_history_search_pagination_and_soft_deletion(client):
    register_login(client)
    product = ready_product(client)
    for title in ("蓝牙问题", "开机记录", "使用备忘"):
        post(client, "/conversations", {"userProductInstanceId": product["id"], "title": title})
    base = f"/api/v1/conversations?instanceId={product['id']}&limit=2"
    first = client.get(base).json()
    assert len(first["items"]) == 2 and first["nextCursor"]
    second = client.get(base + "&cursor=" + first["nextCursor"]).json()
    assert len(second["items"]) == 1 and second["nextCursor"] is None
    assert len(client.get(base + "&q=开机").json()["items"]) == 1
    target = first["items"][0]["id"]
    assert client.delete(f"/api/v1/conversations/{target}").status_code == 204
    assert client.get(f"/api/v1/conversations/{target}").status_code == 404
    assert client.get(f"/api/v1/conversations/{target}/messages").status_code == 404
    assert len(client.get("/api/v1/conversations").json()["items"]) == 2


def test_missing_evidence_records_only_anonymized_intent(client):
    register_login(client)
    product = ready_product(client)
    with transaction() as connection:
        execute(connection, "UPDATE document_versions SET verification_status='pending'")
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()
    question = "我叫张三，邮箱 person@example.com，电话13800138000，序列号SN123456789，如何开机？"
    for _ in range(2):
        answer = post(
            client, f"/conversations/{conversation['id']}/messages", {"content": question}
        )
        assert answer.status_code == 201 and not answer.json()["citations"]
    with transaction() as connection:
        gaps = many(connection, "SELECT * FROM knowledge_gaps")
        assert len(gaps) == 1 and gaps[0]["occurrence_count"] == 2
        assert gaps[0]["question_summary"] in SUMMARIES.values()
        assert not any(
            part in gaps[0]["question_summary"] for part in ["张三", "person@", "138001", "SN123"]
        )
        assert one(connection, "SELECT count(*) AS n FROM public_builds")["n"] == 1
    value = redacted_question(question)
    assert "person@example.com" not in value and "13800138000" not in value
