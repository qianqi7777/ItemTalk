"""引导必须同时满足状态机、证据、安全、幂等和持久化恢复约束。"""

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.db import execute, one, transaction
from app.guidance_agent import review_graph
from app.main import app
from app.schemas import GuidanceFeedback
from app.services import guidance
from app.services.guidance import graph_config
from tests.conftest import HEADERS, register_login
from tests.test_api import post
from tests.test_v1_content import ready_product

pytestmark = pytest.mark.postgres


def create_session(client, product, topic="如何开机"):
    response = post(
        client, "/guidance-sessions", {"userProductInstanceId": product["id"], "topic": topic}
    )
    assert response.status_code == 201, response.text
    return response.json()


def feedback(client, session, result, key=None, **extra):
    return post(
        client,
        f"/guidance-sessions/{session['id']}/feedback",
        {
            "currentStepNo": session["currentStepNo"],
            "expectedVersion": session["version"],
            "result": result,
            **extra,
        },
        key=key,
    )


def test_guidance_create_pause_rebuild_resume_and_complete(client):
    user = register_login(client)
    product = ready_product(client)
    session = create_session(client, product)
    assert session["status"] == "active" and session["version"] == 1
    assert session["currentStep"]["citations"][0]["verificationStatus"] == "verified"
    paused = feedback(client, session, "paused").json()
    assert paused["status"] == "paused" and paused["currentStepNo"] == 1
    # 重新构建graph并从数据库读取，不能只证明同进程内存中的“恢复”。
    with transaction() as connection:
        row = guidance.selected_session(connection, UUID(session["id"]), UUID(user["id"]))
        state = review_graph(connection).get_state(graph_config(row))
        assert state.values["status"] == "paused" and state.values["version"] == 2
    restored = post(
        client, f"/guidance-sessions/{session['id']}/resume", {"expectedVersion": paused["version"]}
    ).json()
    assert restored["status"] == "active" and restored["currentStepNo"] == 1
    advanced = feedback(client, restored, "completed").json()
    assert advanced["currentStepNo"] == 2
    finished = feedback(client, advanced, "completed").json()
    assert finished["status"] == "completed" and finished["currentStep"] is None
    assert (
        post(
            client,
            f"/guidance-sessions/{session['id']}/resume",
            {"expectedVersion": finished["version"]},
        ).status_code
        == 409
    )


def test_guidance_idempotency_and_version_conflicts(client):
    register_login(client)
    product = ready_product(client)
    body = {"userProductInstanceId": product["id"], "topic": "如何开机"}
    key = str(uuid4())
    first = post(client, "/guidance-sessions", body, key=key).json()
    same = post(client, "/guidance-sessions", body, key=key).json()
    assert same["id"] == first["id"]
    assert (
        post(client, "/guidance-sessions", {**body, "topic": "不同问题"}, key=key).status_code
        == 409
    )
    action_key = str(uuid4())
    changed = feedback(client, first, "completed", action_key)
    repeated = feedback(client, first, "completed", action_key)
    assert changed.json() == repeated.json()
    assert feedback(client, first, "completed").status_code == 409
    with transaction() as connection:
        assert (
            one(connection, "SELECT count(*) AS n FROM guidance_steps WHERE status='completed'")[
                "n"
            ]
            == 1
        )


def test_high_risk_has_no_steps_no_tools_and_no_resume(client, monkeypatch):
    register_login(client)
    product = ready_product(client)
    monkeypatch.setattr(
        guidance,
        "plan",
        lambda *_args, **_kwargs: pytest.fail("High risk must not invoke model tools"),
    )
    session = create_session(client, product, "如何带电拆开电源并短接保护")
    assert session["status"] == "blocked_unsafe" and session["currentStep"] is None
    assert "停止" in session["notice"]
    assert (
        post(
            client, f"/guidance-sessions/{session['id']}/resume", {"expectedVersion": 1}
        ).status_code
        == 409
    )
    assert feedback(client, {**session, "currentStepNo": 1}, "completed").status_code == 409
    with transaction() as connection:
        assert one(connection, "SELECT count(*) AS n FROM guidance_steps")["n"] == 0


def test_medium_requires_explicit_preconditions_and_unclear_does_not_advance(client):
    register_login(client)
    product = ready_product(client)
    session = create_session(client, product, "如何连接电源后开机")
    assert session["currentStep"]["riskLevel"] == "medium"
    assert feedback(client, session, "completed").status_code == 422
    clarification = feedback(client, session, "unclear", note="没有看明白按多久")
    assert clarification.status_code == 200, clarification.text
    value = clarification.json()
    assert value["currentStepNo"] == 1 and value["version"] == 2
    assert value["currentStep"]["riskLevel"] == "medium"
    assert "3秒" in value["currentStep"]["instruction"]
    assert feedback(client, value, "completed", preconditionsConfirmed=True).status_code == 200


def test_failure_adds_branch_and_revocation_stops_resume(client):
    register_login(client)
    product = ready_product(client)
    session = create_session(client, product)
    branch = feedback(client, session, "failed", note="没有响应").json()
    assert branch["currentStepNo"] == 3
    assert branch["currentStep"]["failureOfStepId"] == session["currentStep"]["id"]
    with transaction() as connection:
        assert (
            one(
                connection,
                "SELECT status FROM guidance_steps WHERE session_id=:s AND step_no=2",
                s=UUID(session["id"]),
            )["status"]
            == "skipped"
        )
    paused = feedback(client, branch, "paused").json()
    with transaction() as connection:
        execute(connection, "UPDATE sources SET verification_status='revoked'")
    result = post(
        client, f"/guidance-sessions/{session['id']}/resume", {"expectedVersion": paused["version"]}
    ).json()
    assert result["status"] == "failed" and result["currentStep"] is None
    assert "失效" in result["notice"]


def test_foreign_session_never_reads_checkpoint(client, monkeypatch):
    register_login(client)
    product = ready_product(client)
    session = create_session(client, product)
    with TestClient(app, base_url="http://localhost:5173", headers=HEADERS) as other:
        register_login(other, "foreign@example.com")
        monkeypatch.setattr(
            guidance, "review_graph", lambda *_: pytest.fail("Foreign checkpoint access")
        )
        assert other.get(f"/api/v1/guidance-sessions/{session['id']}").status_code == 404
        assert feedback(other, session, "paused").status_code == 404
        assert (
            post(
                other, f"/guidance-sessions/{session['id']}/resume", {"expectedVersion": 1}
            ).status_code
            == 404
        )
        assert other.get("/api/v1/guidance-sessions").json()["items"] == []


def test_two_concurrent_feedbacks_advance_only_once(client):
    user = register_login(client)
    product = ready_product(client)
    session = create_session(client, product)
    body = GuidanceFeedback(currentStepNo=1, expectedVersion=1, result="completed")

    def submit(_):
        try:
            with transaction() as connection:
                return guidance.feedback(
                    connection, UUID(session["id"]), UUID(user["id"]), body, str(uuid4())
                )["version"]
        except Exception as error:
            return getattr(error, "status", None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert sorted(results) == [2, 409]


def test_checkpoint_rolls_back_with_failed_feedback_transaction(client):
    user = register_login(client)
    product = ready_product(client)
    session = create_session(client, product)
    with pytest.raises(RuntimeError), transaction() as connection:
        guidance.feedback(
            connection,
            UUID(session["id"]),
            UUID(user["id"]),
            GuidanceFeedback(currentStepNo=1, expectedVersion=1, result="completed"),
            str(uuid4()),
        )
        raise RuntimeError("Simulated failure after checkpoint update")
    with transaction() as connection:
        row = guidance.selected_session(connection, UUID(session["id"]), UUID(user["id"]))
        snapshot = review_graph(connection).get_state(graph_config(row))
        assert row["version"] == 1 and snapshot.values["version"] == 1
        assert snapshot.next
    assert feedback(client, session, "completed").status_code == 200
