"""真实 PostgreSQL + mock 外部服务，覆盖注册到带引用问答完整闭环。"""

import io
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.db import execute, many, one, transaction
from app.main import app
from app.services.products import build_for
from app.services.queue import claim, fenced, recover
from app.worker import run_once
from tests.conftest import HEADERS, register_login

pytestmark = pytest.mark.postgres


def post(client, path, body, key=None, retry=None):
    return client.post(
        "/api/v1" + path,
        json=body,
        headers={"Idempotency-Key": key or str(uuid4()), **({"Retry-Of": retry} if retry else {})},
    )


def identified_product(client):
    attempt = post(client, "/products/identify", {"inputText": "DEMO-100 CN"}).json()
    assert run_once()
    result = client.get(f"/api/v1/identifications/{attempt['id']}").json()
    assert result["status"] == "waiting_for_user"
    response = post(
        client,
        f"/identifications/{attempt['id']}/confirm",
        {"productModelId": result["candidates"][0]["productModelId"]},
    )
    assert response.status_code == 200
    return response.json()


def test_register_upload_identify_research_chat_sse(client):
    register_login(client)
    data = io.BytesIO()
    Image.new("RGB", (12, 12), "blue").save(data, "PNG")
    signed = post(
        client,
        "/assets/presign",
        {
            "filename": "device.png",
            "mimeType": "image/png",
            "sizeBytes": len(data.getvalue()),
            "kind": "product_photo",
        },
    ).json()
    assert client.put(signed["uploadUrl"], content=data.getvalue()).status_code == 204
    asset = post(client, f"/assets/{signed['assetId']}/confirm", {}).json()
    assert asset["status"] == "available"
    assert client.put(signed["uploadUrl"], content=data.getvalue()).status_code == 409
    attempt = post(client, "/products/identify", {"assetIds": [asset["id"]]}).json()
    assert run_once()
    result = client.get(f"/api/v1/identifications/{attempt['id']}").json()
    product = post(
        client,
        f"/identifications/{attempt['id']}/confirm",
        {"productModelId": result["candidates"][0]["productModelId"]},
    ).json()
    assert product["identityStatus"] == "user_confirmed" and product["knowledgeStatus"] == "queued"
    assert run_once()
    detail = client.get(f"/api/v1/me/products/{product['id']}").json()
    assert detail["identityStatus"] == "verified" and detail["knowledgeStatus"] == "ready"
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()
    response = post(
        client, f"/conversations/{conversation['id']}/messages", {"content": "如何开机？"}
    )
    assert response.status_code == 201, response.text
    assert response.json()["citations"][0]["pageStart"] == 1
    streamed = post(
        client, f"/conversations/{conversation['id']}/stream", {"content": "开机需要多久？"}
    )
    assert streamed.headers["content-type"].startswith("text/event-stream")
    assert all(
        f"event: {event}" in streamed.text for event in ("status", "token", "citation", "done")
    )
    assert "reasoning" not in streamed.text


def test_ownership_idempotency_and_run_isolation(client):
    register_login(client)
    key = str(uuid4())
    first = post(client, "/products/identify", {"inputText": "DEMO-100"}, key)
    same = post(client, "/products/identify", {"inputText": "DEMO-100"}, key)
    assert same.json()["id"] == first.json()["id"]
    assert post(client, "/products/identify", {"inputText": "DEMO-200"}, key).status_code == 409
    assert run_once()
    result = client.get(f"/api/v1/identifications/{first.json()['id']}").json()
    product = post(
        client,
        f"/identifications/{result['id']}/confirm",
        {"productModelId": result["candidates"][0]["productModelId"]},
    ).json()
    with TestClient(app, base_url="http://localhost:5173", headers=HEADERS) as other:
        register_login(other, "other@example.com")
        for path in (
            f"/identifications/{result['id']}",
            f"/me/products/{product['id']}",
            f"/jobs/{product['knowledgeJobId']}",
        ):
            assert other.get("/api/v1" + path).status_code == 404
        assert (
            post(other, "/conversations", {"userProductInstanceId": product["id"]}).status_code
            == 404
        )
    job = client.get(f"/api/v1/jobs/{product['knowledgeJobId']}").json()
    assert not ({"payload", "leaseToken", "userId"} & job.keys())


def test_parallel_public_builds_deduplicated(client):
    user = register_login(client)
    product = identified_product(client)

    def request_build(_):
        with transaction() as connection:
            return build_for(
                connection, UUID(product["productModelId"]), UUID(product["id"]), UUID(user["id"])
            )["id"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(request_build, range(8)))
    assert len(set(ids)) == 1
    with transaction() as connection:
        assert one(connection, "SELECT count(*) AS n FROM public_builds")["n"] == 1


def test_expired_worker_cannot_commit_and_build_status_synced(client):
    register_login(client)
    product = identified_product(client)
    job = claim()
    with transaction() as connection:
        execute(
            connection,
            ("UPDATE jobs SET lease_until=now()-interval '1 second' WHERE id=:id"),
            id=job["id"],
        )
        recover(connection)
    with transaction() as connection:
        with pytest.raises(Exception, match="租约"):
            fenced(connection, job)
        build = one(
            connection,
            "SELECT status FROM public_builds WHERE job_id=:id",
            id=UUID(product["knowledgeJobId"]),
        )
        assert build["status"] == "queued"


def test_retry_reuses_input_and_cancel_terminal(client):
    register_login(client)
    product = identified_product(client)
    assert run_once()
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()
    response = post(client, f"/conversations/{conversation['id']}/messages", {"content": "开机"})
    run_id = response.json()["runId"]
    cancel = post(client, f"/conversations/{conversation['id']}/runs/{run_id}/cancel", {})
    assert cancel.json()["status"] == "completed"
    with transaction() as connection:
        execute(
            connection,
            "DELETE FROM message_citations WHERE message_id=:id",
            id=UUID(response.json()["id"]),
        )
        execute(connection, "DELETE FROM messages WHERE id=:id", id=UUID(response.json()["id"]))
        execute(
            connection,
            "UPDATE assistant_runs SET status='interrupted' WHERE id=:id",
            id=UUID(run_id),
        )
    retried = post(
        client, f"/conversations/{conversation['id']}/messages", {"content": "开机"}, retry=run_id
    )
    assert retried.status_code == 201
    with transaction() as connection:
        inputs = many(
            connection,
            ("SELECT id FROM messages WHERE role='user' AND conversation_id=:c"),
            c=UUID(conversation["id"]),
        )
        assert len(inputs) == 1


def test_origin_guard_and_password_redaction(client):
    assert (
        client.post(
            "/api/v1/auth/register",
            json={"email": "bad", "password": "secret"},
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    response = post(client, "/auth/register", {"email": "bad", "password": "secret"})
    assert response.status_code == 422 and "secret" not in response.text


def test_login_normalizes_email_but_preserves_password_spaces(client):
    credentials = {"email": " User@Example.com ", "password": " space-password-123 "}
    assert post(client, "/auth/register", credentials).status_code == 201
    assert (
        post(
            client, "/auth/login", {"email": "user@example.com", "password": "space-password-123"}
        ).status_code
        == 400
    )
    assert (
        post(
            client,
            "/auth/login",
            {"email": "user@example.com", "password": credentials["password"]},
        ).status_code
        == 204
    )
