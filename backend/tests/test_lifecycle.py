"""公共证据撤销、跨用户构建与临时对象清理的生命周期回归。"""

from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.db import execute, many, one, transaction
from app.main import app
from app.services.chat import retrieve
from app.services.products import create_instance
from app.storage import Storage
from app.worker import run_once
from tests.conftest import HEADERS, register_login
from tests.test_api import identified_product, post

pytestmark = pytest.mark.postgres


def test_users_share_build_but_not_private_requests(client):
    register_login(client)
    product = identified_product(client)
    with TestClient(app, base_url="http://localhost:5173", headers=HEADERS) as other:
        user = register_login(other, "second@example.com")
        with transaction() as connection:
            instance = create_instance(
                connection, UUID(product["productModelId"]), UUID(user["id"])
            )
            assert instance["user_id"] == UUID(user["id"])
            assert one(connection, "SELECT count(*) AS n FROM public_builds")["n"] == 1
            assert one(connection, "SELECT count(*) AS n FROM job_request_links")["n"] == 2
        assert other.get(f"/api/v1/jobs/{product['knowledgeJobId']}").status_code == 200


def test_revocation_blocks_new_retrieval_and_preserves_old_citations(client):
    register_login(client)
    product = identified_product(client)
    assert run_once()
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()
    answer = post(
        client, f"/conversations/{conversation['id']}/messages", {"content": "如何开机"}
    ).json()
    with transaction() as connection:
        execute(connection, "UPDATE sources SET verification_status='revoked'")
    assert not retrieve(UUID(product["productModelId"]), "开机", [1.0] + [0.0] * 1023)
    history = client.get(f"/api/v1/conversations/{conversation['id']}/messages").json()["items"]
    original = next(message for message in history if message["id"] == answer["id"])
    assert original["citations"][0]["verificationStatus"] == "revoked"
    post(
        client,
        f"/products/{product['productModelId']}/knowledge-jobs",
        {},
        retry=product["knowledgeJobId"],
    )
    assert run_once()
    with transaction() as connection:
        assert (
            one(connection, "SELECT verification_status FROM sources")["verification_status"]
            == "revoked"
        )


def test_unverified_pages_cannot_be_searched(client):
    register_login(client)
    product = identified_product(client)
    assert run_once()
    with transaction() as connection:
        execute(connection, "UPDATE document_versions SET verification_status='pending'")
        assert many(connection, "SELECT extracted_text FROM document_pages")
    assert not retrieve(UUID(product["productModelId"]), "开机", [1.0] + [0.0] * 1023)


def test_expired_upload_cleanup_deletes_only_temporary_object(client):
    register_login(client)
    signed = post(
        client,
        "/assets/presign",
        {"filename": "draft.png", "mimeType": "image/png", "sizeBytes": 3, "kind": "product_photo"},
    ).json()
    assert client.put(signed["uploadUrl"], content=b"bad").status_code == 204
    with transaction() as connection:
        asset = one(connection, "SELECT * FROM assets WHERE id=:id", id=UUID(signed["assetId"]))
        execute(connection, ("UPDATE jobs SET available_at=now() WHERE job_type='asset_cleanup'"))
    assert Storage().path(asset["object_key"]).exists()
    assert run_once()
    assert not Storage().path(asset["object_key"]).exists()
    assert client.get(f"/api/v1/assets/{signed['assetId']}").status_code == 404
