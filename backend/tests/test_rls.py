"""使用真实非所有者角色，省略业务 WHERE 条件仍不得读取、改写他人私有数据。"""

import io
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.config import settings
from app.db import actor, engine, execute, many, one, transaction, validate_runtime
from app.main import app
from app.services.queue import claim
from app.worker import run_once
from tests.conftest import HEADERS, register_login
from tests.test_api import post
from tests.test_guidance import create_session, feedback

pytestmark = pytest.mark.postgres


@pytest.fixture
def runtime(database, monkeypatch):
    admin_url = settings().db_url()

    @contextmanager
    def role(name):
        previous = settings().db_url()
        # 测试角色由管理员预置；只换登录用户，测试连接仍是同一隔离数据库。
        url = make_url(admin_url).set(username=f"itemtalk_{name}")
        engine().dispose()
        engine.cache_clear()
        monkeypatch.setenv("DATABASE_URL", url.render_as_string(hide_password=False))
        settings.cache_clear()
        try:
            yield
        finally:
            engine().dispose()
            engine.cache_clear()
            monkeypatch.setenv("DATABASE_URL", str(previous))
            settings.cache_clear()

    return role


def own_product(client, runtime):
    with runtime("app"):
        attempt = post(client, "/products/identify", {"inputText": "DEMO-100 CN"}).json()
    with runtime("worker"):
        assert run_once(UUID(attempt["jobId"]))
    with runtime("app"):
        result = client.get(f"/api/v1/identifications/{attempt['id']}").json()
        product = post(
            client,
            f"/identifications/{attempt['id']}/confirm",
            {"productModelId": result["candidates"][0]["productModelId"]},
        ).json()
    with runtime("worker"):
        assert run_once(UUID(product["knowledgeJobId"]))
    return product


def test_nonowner_auth_upload_chat_manual_guidance_recovery(runtime):
    with TestClient(app, base_url="http://localhost:5173", headers=HEADERS) as client:
        with runtime("app"):
            validate_runtime("app")
            person = register_login(client)
            with transaction() as connection:
                role = one(
                    connection,
                    "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user",
                )
                assert not role["rolsuper"] and not role["rolbypassrls"]
                assert one(connection, "SELECT count(*) AS n FROM users")["n"] == 0
            data = io.BytesIO()
            Image.new("RGB", (12, 12), "blue").save(data, "PNG")
            signed_response = post(
                client,
                "/assets/presign",
                {
                    "filename": "device.png",
                    "mimeType": "image/png",
                    "sizeBytes": len(data.getvalue()),
                    "kind": "product_photo",
                },
            )
            assert signed_response.status_code == 201, signed_response.text
            signed = signed_response.json()
            assert client.put(signed["uploadUrl"], content=data.getvalue()).status_code == 204
            assert client.post(f"/api/v1/assets/{signed['assetId']}/confirm").status_code == 200
            asset = client.get(f"/api/v1/assets/{signed['assetId']}").json()
            assert client.get(asset["readUrl"]).status_code == 200
        product = own_product(client, runtime)
        with runtime("app"):
            path = f"/api/v1/products/{product['productModelId']}/manual"
            manual = client.get(path).json()
        with runtime("worker"):
            validate_runtime("worker")
            assert run_once(UUID(manual["jobId"]))
        with runtime("app"):
            assert client.get(path).json()["status"] == "ready"
            conversation = post(
                client, "/conversations", {"userProductInstanceId": product["id"]}
            ).json()
            response = post(
                client, f"/conversations/{conversation['id']}/stream", {"content": "如何开机"}
            )
            assert response.status_code == 200 and "event: done" in response.text
            session = create_session(client, product)
            paused = feedback(client, session, "paused").json()
            # 新请求从数据库 checkpoint 加载，验证中间件、线程和图工具身份传递。
            resumed = post(
                client,
                f"/guidance-sessions/{session['id']}/resume",
                {"expectedVersion": paused["version"]},
            )
            assert resumed.status_code == 200 and resumed.json()["status"] == "active"
            with actor(person["id"]), transaction() as connection:
                assert one(connection, "SELECT count(*) AS n FROM checkpoints")["n"] > 0
            assert client.post("/api/v1/auth/logout").status_code == 204
            assert client.get("/api/v1/me").status_code == 401


def test_private_rows_hidden_and_cross_owner_writes_rejected(runtime):
    with TestClient(app, base_url="http://localhost:5173", headers=HEADERS) as first:
        with runtime("app"):
            alice = register_login(first, "alice@example.com")
        a = own_product(first, runtime)
        with runtime("app"):
            session = create_session(first, a)
    with TestClient(app, base_url="http://localhost:5173", headers=HEADERS) as second:
        with runtime("app"):
            bob = register_login(second, "bob@example.com")
            assert second.get(f"/api/v1/guidance-sessions/{session['id']}").status_code == 404
            with actor(bob["id"]), transaction() as connection:
                assert many(connection, "SELECT * FROM user_product_instances") == []
                assert many(connection, "SELECT * FROM guidance_sessions") == []
                assert many(connection, "SELECT * FROM checkpoints") == []
                assert (
                    execute(connection, "UPDATE guidance_sessions SET topic='changed'").rowcount
                    == 0
                )
                assert execute(connection, "DELETE FROM identification_attempts").rowcount == 0
            with pytest.raises(DBAPIError), actor(bob["id"]), transaction() as connection:
                execute(
                    connection,
                    "INSERT INTO user_product_instances(user_id,product_model_id) VALUES(:u,:m)",
                    u=UUID(alice["id"]),
                    m=UUID(a["productModelId"]),
                )
            with actor(alice["id"]), transaction() as connection:
                assert len(many(connection, "SELECT * FROM user_product_instances")) == 1
            # SET LOCAL 在提交/回滚后清空，连接池下一请求没有上一用户的权限。
            with transaction() as connection:
                assert many(connection, "SELECT * FROM user_product_instances") == []
                assert many(connection, "SELECT * FROM users") == []


def test_worker_queue_entry_permissions_and_tenant_context(runtime):
    with TestClient(app, base_url="http://localhost:5173", headers=HEADERS) as client:
        with runtime("app"):
            alice = register_login(client)
            attempt = post(client, "/products/identify", {"inputText": "私人识别备注"}).json()
            with pytest.raises(DBAPIError), transaction() as connection:
                execute(connection, "SELECT * FROM itemtalk_queue_claim(NULL,180)")
            with pytest.raises(DBAPIError), transaction() as connection:
                execute(connection, "SET ROLE itemtalk_guard")
            with pytest.raises(DBAPIError), transaction() as connection:
                execute(connection, "TRUNCATE users CASCADE")
        with runtime("worker"):
            with transaction() as connection:
                assert many(connection, "SELECT * FROM identification_attempts") == []
                assert many(connection, "SELECT * FROM jobs WHERE user_id IS NOT NULL") == []
            job = claim(UUID(attempt["jobId"]))
            assert job["user_id"] == UUID(alice["id"])
            with actor(job["user_id"]), transaction() as connection:
                assert len(many(connection, "SELECT * FROM identification_attempts")) == 1
            with actor(uuid4()), transaction() as connection:
                assert many(connection, "SELECT * FROM identification_attempts") == []


def test_parallel_contexts_do_not_mix_on_pooled_connections(runtime):
    with TestClient(app, base_url="http://localhost:5173", headers=HEADERS) as client:
        with runtime("app"):
            alice = register_login(client, "one@example.com")
            bob = register_login(client, "two@example.com")

            def read(person):
                with actor(person["id"]), transaction() as connection:
                    return many(connection, "SELECT id FROM users")

            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(read, [alice, bob] * 10))
            assert all(
                result == [{"id": UUID(person["id"])}]
                for result, person in zip(results, [alice, bob] * 10, strict=True)
            )
