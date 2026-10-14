"""删除覆盖真实对象、所有私有子表、公开知识保留、权限及失败/并发恢复。"""

import io
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.db import actor, execute, many, one, transaction
from app.errors import DomainError
from app.main import app
from app.schemas import MessageRequest
from app.services import chat
from app.storage import Storage
from app.worker import run_once
from tests.conftest import HEADERS, register_login
from tests.test_api import post
from tests.test_guidance import create_session, feedback
from tests.test_rls import own_product
from tests.test_rls import runtime as runtime
from tests.test_v1_content import ready_product

pytestmark = pytest.mark.postgres


def upload_image(client):
    data = io.BytesIO()
    Image.new("RGB", (12, 12), "green").save(data, "PNG")
    signed = post(
        client,
        "/assets/presign",
        {
            "filename": "photo.png",
            "mimeType": "image/png",
            "sizeBytes": len(data.getvalue()),
            "kind": "feedback",
        },
    ).json()
    assert client.put(signed["uploadUrl"], content=data.getvalue()).status_code == 204
    result = post(client, f"/assets/{signed['assetId']}/confirm", {})
    assert result.status_code == 200
    return result.json(), signed


def expires_passed(user):
    with transaction() as connection:
        execute(
            connection,
            (
                "UPDATE private_objects SET delete_after=now()-interval '1 second' "
                "WHERE owner_user_id=:u"
            ),
            u=UUID(user["id"]),
        )
        execute(
            connection,
            (
                "UPDATE jobs SET available_at=now() WHERE user_id=:u "
                "AND job_type IN ('user_cleanup','asset_cleanup')"
            ),
            u=UUID(user["id"]),
        )


def cleanup_id(user, scope):
    with transaction() as connection:
        return one(
            connection,
            (
                "SELECT id FROM jobs WHERE user_id=:u AND job_type='user_cleanup' "
                "AND payload->>'scope'=:scope ORDER BY created_at DESC LIMIT 1"
            ),
            u=UUID(user["id"]),
            scope=scope,
        )["id"]


def drain(job_id, maximum=30):
    for _ in range(maximum):
        assert run_once(job_id)
        with transaction() as connection:
            row = one(connection, "SELECT * FROM jobs WHERE id=:id", id=job_id)
            assert row["status"] != "failed", row["last_error"]
            if row["status"] == "succeeded":
                return row
    pytest.fail("Cleanup did not finish within the bounded test workset")


def test_asset_delete_waits_for_put_expiry_then_erases_files_and_receipt(client):
    user = register_login(client)
    asset, signed = upload_image(client)
    with transaction() as connection:
        objects = many(
            connection, "SELECT * FROM private_objects WHERE asset_id=:a", a=UUID(asset["id"])
        )
    assert len(objects) == 2
    assert client.delete(f"/api/v1/assets/{asset['id']}").status_code == 204
    assert client.delete(f"/api/v1/assets/{asset['id']}").status_code == 204
    assert client.get(f"/api/v1/assets/{asset['id']}").status_code == 404
    assert client.get(asset["readUrl"]).status_code == 404
    assert client.put(signed["uploadUrl"], content=b"rewrite").status_code == 409
    job_id = cleanup_id(user, "asset")
    assert run_once(job_id)
    assert run_once(job_id)
    with transaction() as connection:
        row = one(connection, "SELECT * FROM jobs WHERE id=:id", id=job_id)
        assert row["status"] == "queued" and row["available_at"] > datetime.now(UTC)
    expires_passed(user)
    receipt = drain(job_id)
    assert receipt["user_id"] is None and receipt["payload"] == {}
    assert all(not Storage().path(row["object_key"]).exists() for row in objects)
    with transaction() as connection:
        assert not one(connection, "SELECT id FROM assets WHERE id=:a", a=UUID(asset["id"]))
        assert not many(connection, "SELECT * FROM private_objects")


def test_shared_asset_conflict_and_scoped_instance_cleanup_preserve_other_usage(client):
    user = register_login(client)
    product = ready_product(client)
    other = post(client, "/me/products", {"productModelId": product["productModelId"]}).json()
    asset, _ = upload_image(client)
    conversations = []
    for item in [product, other]:
        conversation = post(client, "/conversations", {"userProductInstanceId": item["id"]}).json()
        conversations.append(conversation)
        assert (
            post(
                client,
                f"/conversations/{conversation['id']}/messages",
                {"content": "如何开机", "assetIds": [asset["id"]]},
            ).status_code
            == 201
        )
    assert client.delete(f"/api/v1/assets/{asset['id']}").status_code == 409
    session = create_session(client, product)
    feedback(client, session, "paused")
    with transaction() as connection:
        public = one(connection, "SELECT count(*) AS n FROM knowledge_chunks")["n"]
    assert client.delete(f"/api/v1/me/products/{product['id']}").status_code == 204
    assert client.get(f"/api/v1/conversations/{conversations[0]['id']}").status_code == 404
    assert client.get(f"/api/v1/guidance-sessions/{session['id']}").status_code == 404
    receipt = drain(cleanup_id(user, "instance"))
    assert receipt["payload"] == {}
    assert client.get(f"/api/v1/assets/{asset['id']}").status_code == 200
    assert client.get(f"/api/v1/conversations/{conversations[1]['id']}/messages").json()["items"]
    with transaction() as connection:
        assert one(connection, "SELECT count(*) AS n FROM knowledge_chunks")["n"] == public
        assert not many(
            connection,
            "SELECT * FROM checkpoints WHERE split_part(thread_id,':',3)=:s",
            s=session["id"],
        )
        assert not one(
            connection, "SELECT id FROM user_product_instances WHERE id=:id", id=UUID(product["id"])
        )


def test_storage_failure_retries_without_erasing_private_rows(client, monkeypatch):
    user = register_login(client)
    asset, _ = upload_image(client)
    assert client.delete(f"/api/v1/assets/{asset['id']}").status_code == 204
    expires_passed(user)
    job_id = cleanup_id(user, "asset")
    original = Storage.delete

    def failure(*_args):
        raise DomainError(503, "INTERNAL_ERROR", "对象存储暂时不可用")

    monkeypatch.setattr(Storage, "delete", failure)
    assert run_once(job_id)
    with transaction() as connection:
        assert one(connection, "SELECT id FROM assets WHERE id=:id", id=UUID(asset["id"]))
        assert (
            one(connection, "SELECT status FROM jobs WHERE id=:id", id=job_id)["status"] == "queued"
        )
    monkeypatch.setattr(Storage, "delete", original)
    expires_passed(user)
    assert drain(job_id)["status"] == "succeeded"


def test_nonowner_account_cleanup_deletes_private_data_and_preserves_public(runtime):
    with TestClient(app, base_url="http://localhost:5173", headers=HEADERS) as client:
        with runtime("app"):
            user = register_login(client)
            asset, _ = upload_image(client)
        product = own_product(client, runtime)
        with runtime("app"):
            conversation = post(
                client, "/conversations", {"userProductInstanceId": product["id"]}
            ).json()
            answer = post(
                client,
                f"/conversations/{conversation['id']}/messages",
                {"content": "请记住我喜欢简短中文，如何开机？", "assetIds": [asset["id"]]},
            ).json()
            assert (
                post(
                    client,
                    f"/messages/{answer['id']}/feedback",
                    {"feedbackType": "helpful", "note": "私人备注"},
                ).status_code
                == 204
            )
            session = create_session(client, product)
            assert feedback(client, session, "paused").status_code == 200
        with transaction() as connection:
            public_counts = {
                table: one(connection, f"SELECT count(*) AS n FROM {table}")["n"]  # noqa: S608
                for table in ["knowledge_chunks", "sources", "document_versions", "product_models"]
            }  # noqa: S608
        # 多于 max_attempts 份文件，成功分批必须仍能全部完成。
        with runtime("app"):
            for _ in range(2):
                upload_image(client)
            with actor(user["id"]), transaction() as connection:
                objects = many(connection, "SELECT object_key FROM private_objects")
            response = client.delete("/api/v1/me")
            assert response.status_code == 202
            job_id = UUID(response.json()["cleanupJobId"])
            assert client.get("/api/v1/me").status_code == 401
            assert (
                client.post(
                    "/api/v1/auth/login",
                    json={"email": user["email"], "password": "test-password-123"},
                ).status_code
                == 400
            )
        expires_passed(user)
        with runtime("worker"), actor(user["id"], allow_deleted=True):
            receipt = drain(job_id)
        assert receipt["user_id"] is None and receipt["payload"] == {}
        with transaction() as connection:
            for table, column in [
                ("users", "id"),
                ("assets", "owner_user_id"),
                ("private_objects", "owner_user_id"),
                ("user_product_instances", "user_id"),
                ("messages", "user_id"),
                ("user_memories", "user_id"),
                ("guidance_sessions", "user_id"),
                ("jobs", "user_id"),
            ]:
                assert not many(
                    connection,
                    f"SELECT * FROM {table} WHERE {column}=:u",  # noqa: S608 - 固定白名单
                    u=UUID(user["id"]),  # noqa: S608
                )  # noqa: S608
            assert many(connection, "SELECT * FROM checkpoints") == []
            assert all(
                one(connection, f"SELECT count(*) AS n FROM {table}")["n"] == count  # noqa: S608
                for table, count in public_counts.items()
            )  # noqa: S608
        assert all(not Storage().path(obj["object_key"]).exists() for obj in objects)


def test_confirmation_rollback_orphan_is_tracked_and_eventually_purged(client, monkeypatch):
    user = register_login(client)
    original = Storage.put

    def after_write(store, key, data, mime):
        original(store, key, data, mime)
        if key.startswith("private/"):
            raise DomainError(503, "INTERNAL_ERROR", "确认失败")

    monkeypatch.setattr(Storage, "put", after_write)
    with pytest.raises(AssertionError):
        upload_image(client)
    with transaction() as connection:
        obj = one(connection, "SELECT * FROM private_objects WHERE object_key LIKE 'private/%'")
        job = one(connection, "SELECT * FROM jobs WHERE payload->>'objectId'=:o", o=str(obj["id"]))
    assert Storage().path(obj["object_key"]).exists()
    expires_passed(user)
    assert run_once(job["id"])
    assert not Storage().path(obj["object_key"]).exists()


def test_nested_transactions_do_not_deadlock_with_queued_deletion(client):
    user = register_login(client)
    waiting = Event()
    acquired = Event()

    def deletion():
        with actor(user["id"]):
            waiting.set()
            with transaction(exclusive=True):
                acquired.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with actor(user["id"]), transaction():
            future = pool.submit(deletion)
            assert waiting.wait(2)
            assert not acquired.wait(0.1)
            with transaction() as connection:
                assert one(connection, "SELECT 1 AS n")["n"] == 1
        future.result(timeout=5)
        assert acquired.is_set()


def test_deleted_instance_cancels_late_answer_and_foreign_deletion_is_hidden(client):
    user = register_login(client)
    product = ready_product(client)
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()
    with actor(user["id"]), transaction() as connection:
        run, _ = chat.start_run(
            connection,
            UUID(conversation["id"]),
            MessageRequest(content="如何开机"),
            UUID(user["id"]),
            "late-answer",
        )
    with TestClient(app, base_url="http://localhost:5173", headers=HEADERS) as other:
        register_login(other, "other@example.com")
        assert other.delete(f"/api/v1/me/products/{product['id']}").status_code == 404
    assert client.delete(f"/api/v1/me/products/{product['id']}").status_code == 204
    with pytest.raises(DomainError), actor(user["id"]):
        chat.complete_run(run)
    drain(cleanup_id(user, "instance"))
    with transaction() as connection:
        assert not many(
            connection,
            "SELECT * FROM messages WHERE conversation_id=:c",
            c=UUID(conversation["id"]),
        )


def test_account_mark_blocks_a_previously_authenticated_transaction(client):
    user = register_login(client)
    assert client.delete("/api/v1/me").status_code == 202
    # HTTP 依赖已读到的身份不能授权删除标记之后的第二次事务。
    with pytest.raises(DomainError, match="账号不可用"), actor(user["id"]), transaction():
        pytest.fail("An inactive identity opened a private business transaction")


def test_instance_cleanup_removes_ready_private_manual_tree_and_preserves_other_user(client):
    user = register_login(client)
    product = ready_product(client)
    with TestClient(app, base_url="http://localhost:5173", headers=HEADERS) as other:
        person = register_login(other, "remaining@example.com")
        other_product = post(
            other, "/me/products", {"productModelId": product["productModelId"]}
        ).json()
        preserved = create_session(other, other_product)
        other_asset, _ = upload_image(other)
    with transaction() as connection:
        manual = one(
            connection,
            (
                "INSERT INTO manuals(product_model_id,user_product_instance_id,owner_user_id,"
                "visibility,version,status,evidence_hash) VALUES(:m,:i,:u,'private',1,'ready',:h) "
                "RETURNING id"
            ),
            m=UUID(product["productModelId"]),
            i=UUID(product["id"]),
            u=UUID(user["id"]),
            h="a" * 64,
        )
        root = one(
            connection,
            (
                "INSERT INTO manual_sections(manual_id,section_key,title,body,sort_order,"
                "generation_status) VALUES(:m,'root','私有目录','个人设置',0,'pending') "
                "RETURNING id"
            ),
            m=manual["id"],
        )
        child = one(
            connection,
            (
                "INSERT INTO manual_sections(manual_id,parent_id,section_key,title,body,sort_order,"
                "generation_status) VALUES(:m,:p,'child','私有章节','个人设置',0,'pending') "
                "RETURNING id"
            ),
            m=manual["id"],
            p=root["id"],
        )
        chunk = one(connection, "SELECT id FROM knowledge_chunks LIMIT 1")
        execute(
            connection,
            "INSERT INTO manual_section_citations VALUES(:s,:k,1,now())",
            s=child["id"],
            k=chunk["id"],
        )
        execute(
            connection,
            "UPDATE manual_sections SET generation_status='ready' WHERE manual_id=:m",
            m=manual["id"],
        )
    assert client.delete(f"/api/v1/me/products/{product['id']}").status_code == 204
    drain(cleanup_id(user, "instance"))
    with transaction() as connection:
        assert not one(connection, "SELECT id FROM manuals WHERE id=:m", m=manual["id"])
        assert not one(connection, "SELECT id FROM manual_sections WHERE id=:s", s=child["id"])
        assert one(connection, "SELECT id FROM users WHERE id=:u", u=UUID(person["id"]))
        assert one(
            connection, "SELECT id FROM guidance_sessions WHERE id=:g", g=UUID(preserved["id"])
        )
        assert many(
            connection,
            "SELECT * FROM checkpoints WHERE split_part(thread_id,':',2)=:u",
            u=person["id"],
        )
        row = one(connection, "SELECT * FROM assets WHERE id=:a", a=UUID(other_asset["id"]))
    assert Storage().path(row["object_key"]).exists()
