"""V2真实PG边界测试；供应商仅使用HTTPX内存传输，不访问外网。"""

import json
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import httpx
import pytest
from sqlalchemy.exc import DBAPIError

from app import operations
from app.adapters import ModelGateway
from app.admin_cli import provision
from app.config import settings
from app.db import actor, execute, many, one, transaction
from app.errors import DomainError
from app.services.products import new_job
from app.services.queue import claim
from tests.conftest import register_login

pytestmark = pytest.mark.postgres


@pytest.fixture
def v2(client, monkeypatch):
    monkeypatch.setenv("V2_ENABLED", "true")
    monkeypatch.setenv("RUNTIME_GLOBAL_HARD_LIMIT", "10000")
    monkeypatch.setenv("RUNTIME_SUBJECT_HARD_LIMIT", "10000")
    settings.cache_clear()
    with transaction() as connection:
        execute(connection, "TRUNCATE telemetry_events,telemetry_daily,runtime_config_audit")
        execute(
            connection,
            "UPDATE runtime_config SET version=1,global_limit=NULL,subject_limit=NULL,"
            "background_paused=false,chat_profile='current'",
        )
    yield client
    settings.cache_clear()


def administrator(client):
    user = register_login(client)
    provision(UUID(user["id"]), True)
    return user


def update(client, **changes):
    body = {
        "expectedVersion": 1,
        "globalDailyLimit": 10000,
        "subjectDailyLimit": 10000,
        "backgroundPaused": False,
        "chatProfile": "current",
        **changes,
    }
    return client.patch(
        "/api/v1/admin/runtime-config", json=body, headers={"Idempotency-Key": "runtime-test"}
    )


def test_admin_requires_active_membership_and_cannot_bypass_private_rls(v2):
    user = register_login(v2)
    assert not v2.get("/api/v1/capabilities").json()["adminConsole"]
    assert v2.get("/api/v1/admin/overview").status_code == 403
    provision(UUID(user["id"]), True)
    assert v2.get("/api/v1/capabilities").json()["adminConsole"]
    assert v2.get("/api/v1/admin/overview").status_code == 200
    with actor(user["id"]), transaction() as connection:
        execute(
            connection,
            "INSERT INTO users(login,login_normalized,hashed_password) "
            "VALUES('hidden@example.com','hidden@example.com','not-a-login-password')",
        )
        execute(connection, "SET LOCAL ROLE itemtalk_app")
        assert one(connection, "SELECT itemtalk_is_admin() AS value")["value"]
        assert len(many(connection, "SELECT id FROM users")) == 1
    with pytest.raises(DBAPIError), actor(user["id"]), transaction() as connection:
        execute(connection, "SET LOCAL ROLE itemtalk_app")
        execute(connection, "INSERT INTO admin_memberships(user_id) VALUES(:u)", u=UUID(user["id"]))
    provision(UUID(user["id"]), False)
    assert v2.get("/api/v1/admin/overview").status_code == 403


def test_runtime_config_is_idempotent_versioned_and_audited(v2):
    administrator(v2)
    first = update(v2, globalDailyLimit=50)
    assert first.status_code == 200 and first.json()["version"] == 2
    assert update(v2, globalDailyLimit=50).json() == first.json()
    conflict = v2.patch(
        "/api/v1/admin/runtime-config",
        json={
            "expectedVersion": 1,
            "globalDailyLimit": 60,
            "subjectDailyLimit": 20,
            "backgroundPaused": True,
            "chatProfile": "current",
        },
        headers={"Idempotency-Key": "another-key"},
    )
    assert conflict.status_code == 409
    audit = v2.get("/api/v1/admin/audit").json()["items"]
    assert len(audit) == 1 and audit[0]["version"] == 2


def test_unaccepted_profiles_and_hard_limit_are_rejected(v2, monkeypatch):
    administrator(v2)
    monkeypatch.setenv("MODEL_PROFILES", '{"quality":{"model":"qwen3.7-plus","accepted":false}}')
    settings.cache_clear()
    assert update(v2, chatProfile="quality").status_code == 422
    assert update(v2, globalDailyLimit=10001).status_code == 422
    assert not v2.get("/api/v1/admin/audit").json()["items"]


def test_quota_reduction_does_not_reset_counters_and_rolls_back_partial_charge(v2):
    administrator(v2)
    assert update(v2, globalDailyLimit=4, subjectDailyLimit=2).status_code == 200
    gateway = ModelGateway()
    gateway.budget("same-person")
    gateway.budget("same-person")
    with pytest.raises(DomainError):
        gateway.budget("same-person")
    with transaction() as connection:
        assert (
            one(connection, "SELECT amount FROM usage_counters WHERE subject='model:global'")[
                "amount"
            ]
            == 2
        )


def test_running_model_snapshot_survives_manual_switch(v2, monkeypatch):
    user = administrator(v2)
    monkeypatch.setenv("MODEL_PROFILES", '{"quality":{"model":"qwen3.7-plus","accepted":true}}')
    settings.cache_clear()
    with actor(user["id"]), transaction() as connection:
        old = operations.capture(connection)
        job = new_job(connection, "identify", {}, user=UUID(user["id"]))
    assert update(v2, chatProfile="quality").status_code == 200
    with operations.snapshot(old):
        assert ModelGateway().config.chat_model == old["chatModel"]
    assert ModelGateway().config.chat_model == "qwen3.7-plus"
    assert job["runtime_snapshot"]["chatModel"] == old["chatModel"]


def test_pause_stops_business_claims_but_not_cleanup(v2):
    user = administrator(v2)
    with transaction() as connection:
        business = new_job(connection, "identify", {}, user=UUID(user["id"]))
        cleanup = new_job(connection, "user_cleanup", {}, user=UUID(user["id"]))
    assert update(v2, backgroundPaused=True).status_code == 200
    assert claim(business["id"]) is None
    assert claim(cleanup["id"])["id"] == cleanup["id"]


def test_query_cache_is_private_revision_aware_and_deleted_with_account(v2, monkeypatch):
    user = administrator(v2)
    calls = []
    gateway = ModelGateway()
    monkeypatch.setattr(
        gateway, "embed", lambda texts, subject: calls.append(1) or [[1.0] + [0.0] * 1023]
    )
    with actor(user["id"]):
        operations.query_vector(gateway, "私有问题", "test")
        operations.query_vector(gateway, "私有问题", "test")
        assert len(calls) == 1
        monkeypatch.setenv("EMBEDDING_REVISION", "new")
        settings.cache_clear()
        operations.query_vector(gateway, "私有问题", "test")
        assert len(calls) == 2
    other = register_login(v2, "other@example.com")
    with actor(other["id"]):
        operations.query_vector(gateway, "私有问题", "test")
    assert len(calls) == 3
    with actor(other["id"]), transaction() as connection:
        execute(connection, "SET LOCAL ROLE itemtalk_app")
        assert len(many(connection, "SELECT * FROM query_embedding_cache")) == 1
    assert v2.delete("/api/v1/me").status_code == 202
    with transaction() as connection:
        assert not many(
            connection, "SELECT * FROM query_embedding_cache WHERE user_id=:u", u=UUID(other["id"])
        )


def test_unknown_usage_and_unpriced_calls_remain_unknown(v2):
    administrator(v2)
    operations.metric("upstream", "chat", "success", 100, provider="dashscope", model="test")
    groups = v2.get("/api/v1/admin/metrics").json()["groups"]
    row = next(r for r in groups if r["kind"] == "upstream")
    assert row["input_tokens"] is None and row["output_tokens"] is None
    assert row["priced_calls"] == 0 and row["estimated_cost"] is None


def test_stream_final_usage_and_retry_attempts_are_recorded(v2, monkeypatch):
    administrator(v2)
    monkeypatch.setenv("MODEL_PROVIDER", "dashscope")
    monkeypatch.setenv("MODEL_API_KEY", "test-only-key")
    monkeypatch.setenv(
        "MODEL_PRICES",
        json.dumps(
            [
                {
                    "provider": "dashscope",
                    "model": settings().chat_model,
                    "effective_from": "2026-01-01",
                    "input_per_million": "1",
                    "output_per_million": "2",
                }
            ]
        ),
    )
    settings.cache_clear()
    count = 0

    def reply(request):
        nonlocal count
        count += 1
        if count == 1:
            return httpx.Response(429, json={"error": {"message": "not logged"}})
        assert json.loads(request.content)["stream_options"]["include_usage"]
        return httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"正文"}}]}\n\n'
            'data: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":5}}\n\n'
            "data: [DONE]\n\n",
        )

    gateway = ModelGateway(httpx.Client(transport=httpx.MockTransport(reply)))
    monkeypatch.setattr(gateway, "budget", lambda subject: None)
    assert "".join(gateway.answer_stream("不会保存的问题", [], "test")) == "正文"
    with transaction() as connection:
        rows = many(connection, "SELECT * FROM telemetry_events WHERE kind='upstream' ORDER BY id")
        assert len(rows) == 2 and rows[-1]["input_tokens"] == 10
        assert rows[-1]["output_tokens"] == 5 and rows[-1]["estimated_cost"] > 0
        assert rows[0]["input_tokens"] is None
        assert "不会保存" not in json.dumps(rows, default=str, ensure_ascii=False)


def test_retention_rollup_is_atomic_and_idempotent(v2):
    administrator(v2)
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO telemetry_events(kind,operation,outcome,duration_ms,created_at) "
            "VALUES('http','GET /safe','200',10,now()-interval '31 days')",
        )
        execute(connection, "SELECT itemtalk_ops_cleanup()")
        execute(connection, "SELECT itemtalk_ops_cleanup()")
        assert one(connection, "SELECT sum(count) AS n FROM telemetry_daily")["n"] == 1
        assert not many(
            connection, "SELECT * FROM telemetry_events WHERE created_at<now()-interval '30 days'"
        )


def test_parallel_budget_reservations_never_exceed_global_limit(v2):
    administrator(v2)
    assert update(v2, globalDailyLimit=5, subjectDailyLimit=50).status_code == 200

    def reserve(number):
        try:
            ModelGateway().budget(f"concurrent-{number}")
            return True
        except DomainError as error:
            assert error.status == 429
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(reserve, range(12))) == 5
    with transaction() as connection:
        assert (
            one(connection, "SELECT amount FROM usage_counters WHERE subject='model:global'")[
                "amount"
            ]
            == 5
        )


def test_interrupted_stream_records_unknown_usage_and_no_success(v2, monkeypatch):
    administrator(v2)
    monkeypatch.setenv("MODEL_PROVIDER", "dashscope")
    monkeypatch.setenv("MODEL_API_KEY", "test-only-key")
    settings.cache_clear()
    gateway = ModelGateway(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, text='data: {"choices":[{"delta":{"content":"片段"}}]}\n\n'
                )
            )
        )
    )
    monkeypatch.setattr(gateway, "budget", lambda subject: None)
    with pytest.raises(DomainError):
        list(gateway.answer_stream("不落库的正文", [], "test"))
    with transaction() as connection:
        row = one(connection, "SELECT * FROM telemetry_events WHERE kind='upstream'")
        assert row["outcome"] == "interrupted"
        assert row["input_tokens"] is None and row["estimated_cost"] is None


def test_raw_operational_tables_are_not_readable_by_nonadmin_role(v2):
    user = register_login(v2)
    operations.metric("upstream", "chat", "success", 1)
    with actor(user["id"]), transaction() as connection:
        execute(connection, "SET LOCAL ROLE itemtalk_app")
        assert not many(connection, "SELECT * FROM telemetry_events")
        assert not one(connection, "SELECT itemtalk_is_admin() AS value")["value"]
    with pytest.raises(DBAPIError), actor(user["id"]), transaction() as connection:
        execute(connection, "SET LOCAL ROLE itemtalk_app")
        execute(connection, "UPDATE runtime_config SET global_limit=1000000")
