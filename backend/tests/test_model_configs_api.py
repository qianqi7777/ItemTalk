"""Actual PostgreSQL administrator/worker model-switch boundaries; HTTP is synthetic."""

import json
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy.exc import DBAPIError

from app import model_configs, operations
from app.adapters import ModelGateway
from app.config import settings
from app.db import actor, execute, one, transaction
from tests import test_v2_operations as baseline
from tests.conftest import register_login

v2 = baseline.v2
administrator = baseline.administrator


def update(client, **changes):
    return client.patch(
        "/api/v1/admin/runtime-config",
        json={
            "expectedVersion": 1,
            "globalDailyLimit": 10000,
            "subjectDailyLimit": 10000,
            "backgroundPaused": False,
            "chatProfile": "current",
            **changes,
        },
        headers={"Idempotency-Key": str(uuid4())},
    )


pytestmark = pytest.mark.postgres


@pytest.fixture
def admin(v2, monkeypatch):
    monkeypatch.setenv("MODEL_CONFIG_ENCRYPTION_KEY", Fernet.generate_key().decode())
    settings.cache_clear()
    administrator(v2)
    monkeypatch.setattr(
        model_configs,
        "probe",
        lambda *a, **kw: {"checks": ["text", "json", "stream"], "durationMs": 4},
    )
    return v2


def body(**changes):
    return {
        "name": "备用文本服务",
        "baseUrl": "https://models.example.com/v1",
        "protocol": "openai_chat",
        "model": "vendor/model-v2",
        "apiKey": "test-only-new-api-key",
        **changes,
    }


def save(client, **changes):
    draft = body(**changes)
    tested = client.post("/api/v1/admin/model-configs/test", json=draft)
    assert tested.status_code == 200, tested.text
    return client.post(
        "/api/v1/admin/model-configs",
        json={**draft, "testToken": tested.json()["testToken"]},
        headers={"Idempotency-Key": str(uuid4())},
    )


def test_model_config_endpoints_require_active_admin(v2):
    register_login(v2)
    assert v2.get("/api/v1/admin/model-configs").status_code == 403
    assert v2.post("/api/v1/admin/model-configs/test", json=body()).status_code == 403
    assert (
        v2.post(
            "/api/v1/admin/model-configs",
            json={**body(), "testToken": "invalid"},
            headers={"Idempotency-Key": "ordinary"},
        ).status_code
        == 403
    )


def test_test_and_save_are_separate_from_activation_and_never_return_secret(admin):
    saved = save(admin)
    assert saved.status_code == 201, saved.text
    profile = saved.json()["id"]
    listing = admin.get("/api/v1/admin/model-configs").json()
    assert listing["encryptionReady"] and listing["activeProfile"] == "current"
    assert listing["items"][0]["id"] == profile
    assert "test-only-new-api-key" not in saved.text + json.dumps(listing)
    with transaction() as connection:
        stored = one(connection, "SELECT encrypted_key FROM model_configs WHERE id=:p", p=profile)
        assert "test-only-new-api-key" not in stored["encrypted_key"]
        audit = one(
            connection, "SELECT after_value FROM runtime_config_audit ORDER BY id DESC LIMIT 1"
        )
        assert "test-only-new-api-key" not in json.dumps(audit)
    assert update(admin, chatProfile=profile).status_code == 200
    assert admin.get("/api/v1/admin/runtime-config").json()["chatProfile"] == profile


def test_edited_or_forged_test_proof_cannot_be_saved(admin):
    tested = admin.post("/api/v1/admin/model-configs/test", json=body()).json()
    response = admin.post(
        "/api/v1/admin/model-configs",
        json={**body(model="untested"), "testToken": tested["testToken"]},
        headers={"Idempotency-Key": "changed-draft"},
    )
    assert response.status_code == 422
    assert admin.get("/api/v1/admin/model-configs").json()["items"] == []


def test_save_is_idempotent_and_changed_secret_conflicts(admin):
    tested = admin.post("/api/v1/admin/model-configs/test", json=body()).json()
    payload = {**body(), "testToken": tested["testToken"]}
    headers = {"Idempotency-Key": "one-config"}
    first = admin.post("/api/v1/admin/model-configs", json=payload, headers=headers)
    assert first.status_code == 201
    assert (
        admin.post("/api/v1/admin/model-configs", json=payload, headers=headers).json()
        == first.json()
    )
    changed = body(apiKey="different-test-key")
    proof = admin.post("/api/v1/admin/model-configs/test", json=changed).json()["testToken"]
    assert (
        admin.post(
            "/api/v1/admin/model-configs", json={**changed, "testToken": proof}, headers=headers
        ).status_code
        == 409
    )
    assert len(admin.get("/api/v1/admin/model-configs").json()["items"]) == 1


def test_switch_freezes_entire_configuration_for_old_runs_and_does_not_reset_quota(admin):
    first = save(admin).json()["id"]
    assert update(admin, chatProfile=first).status_code == 200
    with transaction() as connection:
        old = operations.capture(connection)
    second = save(
        admin,
        baseUrl="https://second.example.com/v1",
        apiKey="second-test-key",
        model="second-model",
    ).json()["id"]
    assert update(admin, expectedVersion=2, chatProfile=second).status_code == 200
    seen = []

    def reply(request):
        seen.append(
            (
                str(request.url),
                request.headers["Authorization"],
                json.loads(request.content)["model"],
            )
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    client = httpx.Client(transport=httpx.MockTransport(reply))
    with operations.snapshot(old):
        gateway = ModelGateway(client)
        gateway.request("chat", "/chat/completions", {})
    current = ModelGateway(client)
    current.request("chat", "/chat/completions", {})
    assert seen == [
        (
            "https://models.example.com/v1/chat/completions",
            "Bearer test-only-new-api-key",
            "vendor/model-v2",
        ),
        (
            "https://second.example.com/v1/chat/completions",
            "Bearer second-test-key",
            "second-model",
        ),
    ]
    assert "test-only" not in json.dumps(old)
    assert update(admin, expectedVersion=2, chatProfile="current").status_code == 409
    assert admin.get("/api/v1/admin/runtime-config").json()["chatProfile"] == second


def test_worker_resolves_saved_profile_but_cannot_create_or_modify_config(admin):
    profile = save(admin).json()["id"]
    with transaction() as connection:
        execute(connection, "SET LOCAL ROLE itemtalk_worker")
        config = model_configs.get_config(connection, profile)
        assert config["model"] == "vendor/model-v2"
    for statement in [
        "SELECT * FROM model_configs",
        "UPDATE model_configs SET name='changed'",
        "DELETE FROM model_configs",
    ]:
        with pytest.raises(DBAPIError), transaction() as connection:
            execute(connection, "SET LOCAL ROLE itemtalk_worker")
            execute(connection, statement)


def test_api_runtime_role_only_creates_through_admin_guard(admin):
    user = admin.get("/api/v1/me").json()
    with actor(user["id"]), transaction() as connection:
        execute(connection, "SET LOCAL ROLE itemtalk_app")
        result = model_configs.save_config(connection, model_configs.ModelConfigInput(**body()))
        assert result["model"] == "vendor/model-v2"
    with actor(None), transaction() as connection:
        execute(connection, "SET LOCAL ROLE itemtalk_app")
        with pytest.raises(DBAPIError):
            model_configs.save_config(connection, model_configs.ModelConfigInput(**body()))


def test_missing_encryption_key_prevents_testing_and_activation_without_breaking_existing(
    admin, monkeypatch
):
    profile = save(admin).json()["id"]
    monkeypatch.setenv("MODEL_CONFIG_ENCRYPTION_KEY", "")
    settings.cache_clear()
    assert not admin.get("/api/v1/admin/model-configs").json()["encryptionReady"]
    assert admin.post("/api/v1/admin/model-configs/test", json=body()).status_code == 503
    assert update(admin, chatProfile=profile).status_code == 503
    assert admin.get("/api/v1/admin/runtime-config").json()["chatProfile"] == "current"
