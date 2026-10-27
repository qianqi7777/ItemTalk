"""Real PostgreSQL role routing, immutable snapshots and administrator boundaries."""

import json

import httpx
import pytest

from app import operations
from app.adapters import ModelGateway
from app.db import transaction
from tests import test_model_configs_api as baseline

admin = baseline.admin
v2 = baseline.v2
body, save, update = baseline.body, baseline.save, baseline.update

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize(
    "role,protocol",
    [
        ("vision", "openai_chat"),
        ("stt", "openai_transcription"),
        ("embedding", "dashscope_embeddings"),
    ],
)
def test_roles_activate_independently_and_preserve_on_legacy_patch(admin, role, protocol):
    changes = {"role": role, "protocol": protocol}
    if role == "embedding":
        changes.update(model="text-embedding-v4", revision="initial", dimensions=1024)
    response = save(admin, **changes)
    assert response.status_code == 201, response.text
    profile = response.json()["id"]
    assert response.json()["role"] == role
    activated = update(admin, **{role + "Profile": profile})
    assert activated.status_code == 200, activated.text
    runtime = activated.json()
    assert runtime[role + "Profile"] == profile and runtime["chatProfile"] == "current"
    assert update(admin, expectedVersion=2).json()[role + "Profile"] == profile
    listing = admin.get("/api/v1/admin/model-configs").json()
    assert listing["activeProfiles"][role] == profile
    assert listing["presets"]["embedding"]["dimensions"] == 1024
    assert update(admin, expectedVersion=3, **{role + "Profile": "current"}).status_code == 200


def test_cannot_activate_config_for_wrong_role(admin):
    saved = save(admin, role="vision").json()["id"]
    assert update(admin, chatProfile=saved).status_code == 422
    assert update(admin, sttProfile=saved).status_code == 422
    assert admin.get("/api/v1/admin/runtime-config").json()["version"] == 1


def test_vision_snapshot_uses_original_key_after_switch(admin):
    first = save(admin, role="vision").json()["id"]
    assert update(admin, visionProfile=first).status_code == 200
    with transaction() as connection:
        old = operations.capture(connection)
    second = save(
        admin, role="vision", baseUrl="https://second.example.com/v1", apiKey="second-role-key"
    ).json()["id"]
    assert update(admin, expectedVersion=2, visionProfile=second).status_code == 200
    seen = []

    def respond(request):
        seen.append((str(request.url), request.headers["Authorization"]))
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with operations.snapshot(old):
            ModelGateway(client).request("vision", "/chat/completions", {})
        ModelGateway(client).request("vision", "/chat/completions", {})
    assert seen == [
        ("https://models.example.com/v1/chat/completions", "Bearer test-only-new-api-key"),
        ("https://second.example.com/v1/chat/completions", "Bearer second-role-key"),
    ]
    assert "key" not in json.dumps(old)


def test_pre_upgrade_snapshot_never_inherits_new_vision_profile(admin):
    saved = save(admin, role="vision").json()["id"]
    assert update(admin, visionProfile=saved).status_code == 200
    with operations.snapshot(
        {"version": 1, "chatProfile": "current", "chatModel": "original-model"}
    ):
        gateway = ModelGateway()
        assert gateway.config.vision_base_url != "https://models.example.com/v1"
        gateway.close()


def test_vector_configuration_cannot_change_model_through_api(admin):
    response = admin.post(
        "/api/v1/admin/model-configs/test",
        json=body(
            role="embedding",
            protocol="dashscope_embeddings",
            model="different",
            revision="initial",
            dimensions=1024,
        ),
    )
    assert response.status_code == 422


@pytest.mark.parametrize("counter,expected", [("model:global", "全站"), ("model:user", "个人")])
def test_exhausted_site_budget_identifies_scope(admin, counter, expected):
    from app.errors import DomainError
    from app.security import consume

    with transaction() as connection:
        consume(connection, counter, "test-day", 1)
    with pytest.raises(DomainError) as failure, transaction() as connection:
        consume(connection, counter, "test-day", 1)
    assert expected in failure.value.message


def test_legacy_list_keeps_only_text_configs_for_application_rollback(admin):
    from app.db import actor, one

    chat = save(admin).json()["id"]
    vision = save(admin, role="vision").json()["id"]
    user = admin.get("/api/v1/me").json()["id"]
    with actor(user), transaction() as connection:
        legacy = one(connection, "SELECT itemtalk_model_config_list() AS value")["value"]
    assert [row["id"] for row in legacy] == [chat]
    assert {row["id"] for row in admin.get("/api/v1/admin/model-configs").json()["items"]} == {
        chat,
        vision,
    }


def test_vector_identity_drift_rejects_activation_without_changing_runtime(admin, monkeypatch):
    from app.config import settings

    profile = save(
        admin,
        role="embedding",
        protocol="dashscope_embeddings",
        model="text-embedding-v4",
        revision="initial",
        dimensions=1024,
    ).json()["id"]
    monkeypatch.setenv("EMBEDDING_REVISION", "changed-on-server")
    settings.cache_clear()
    response = update(admin, embeddingProfile=profile)
    assert response.status_code == 503, response.text
    assert response.json()["code"] == "MODEL_CONFIG_UNAVAILABLE"
    assert admin.get("/api/v1/admin/runtime-config").json()["version"] == 1
