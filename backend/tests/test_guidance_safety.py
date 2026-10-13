"""模型输出不能解除风险规则，私有照片和幂等重放仍须遵守证据权限。"""

import io
from uuid import UUID

import pytest
from PIL import Image

from app.db import execute, many, transaction
from app.schemas import GuidancePlan
from app.services import guidance
from app.worker import run_once
from tests.conftest import register_login
from tests.test_api import post
from tests.test_guidance import create_session, feedback
from tests.test_v1_content import ready_product

pytestmark = pytest.mark.postgres


def feedback_image(client):
    buffer = io.BytesIO()
    Image.new("RGB", (12, 12), "green").save(buffer, "PNG")
    signed = post(
        client,
        "/assets/presign",
        {
            "filename": "feedback.png",
            "mimeType": "image/png",
            "sizeBytes": len(buffer.getvalue()),
            "kind": "feedback",
        },
    ).json()
    assert client.put(signed["uploadUrl"], content=buffer.getvalue()).status_code == 204
    return post(client, f"/assets/{signed['assetId']}/confirm", {}).json()["id"]


def test_low_label_does_not_allow_model_generated_high_risk_step(client, monkeypatch):
    register_login(client)
    product = ready_product(client)
    unsafe = GuidancePlan.model_validate(
        {
            "riskLevel": "low",
            "steps": [
                {
                    "instruction": "短接安全保护线路后再试",
                    "successCriteria": "继续",
                    "riskLevel": "low",
                    "citationRanks": [1],
                    "safetyNotes": [],
                    "preconditions": [],
                }
            ],
        }
    )
    monkeypatch.setattr(guidance, "plan", lambda *_: (unsafe, []))
    session = create_session(client, product)
    assert session["status"] == "blocked_unsafe" and session["currentStep"] is None
    assert guidance.risk_of("不要带电拆机；然后短接保护", warning=True) == "high"


def test_feedback_photos_are_scoped_and_multiple_uploads_are_preserved(client):
    register_login(client)
    product = ready_product(client)
    session = create_session(client, product)
    first = feedback_image(client)
    second = feedback_image(client)
    value = feedback(client, session, "unclear", assetIds=[first]).json()
    result = feedback(client, value, "unclear", assetIds=[second])
    assert result.status_code == 200, result.text
    with transaction() as connection:
        rows = many(
            connection,
            "SELECT asset_id,sort_order FROM guidance_step_attachments ORDER BY sort_order",
        )
        assert [row["asset_id"] for row in rows] == [UUID(first), UUID(second)]
        assert [row["sort_order"] for row in rows] == [0, 1]


def test_replayed_feedback_updates_revoked_citation_labels(client):
    register_login(client)
    product = ready_product(client)
    session = create_session(client, product)
    key = "same-feedback-key"
    first = feedback(client, session, "completed", key).json()
    assert first["currentStep"]["citations"][0]["verificationStatus"] == "verified"
    with transaction() as connection:
        execute(connection, "UPDATE sources SET verification_status='revoked'")
    repeated = feedback(client, session, "completed", key).json()
    assert repeated["version"] == first["version"]
    assert repeated["currentStep"]["citations"][0]["verificationStatus"] == "revoked"
    assert "失效" in repeated["notice"]


def test_risky_feedback_and_photo_stop_before_planning(client, monkeypatch):
    register_login(client)
    product = ready_product(client)
    first = create_session(client, product)
    second = create_session(client, product)
    photo = feedback_image(client)
    monkeypatch.setattr(
        guidance,
        "plan",
        lambda *_args, **_kwargs: pytest.fail("Risky feedback must not invoke planner"),
    )
    result = feedback(client, first, "failed", note="准备带电拆开电源").json()
    assert result["status"] == "blocked_unsafe" and result["currentStep"] is None
    monkeypatch.setattr(
        guidance,
        "observe_assets",
        lambda *_: {"observation": "看到裸露高压线路", "uncertainty": "不确定"},
    )
    observed = feedback(client, second, "unclear", assetIds=[photo]).json()
    assert observed["status"] == "blocked_unsafe" and observed["currentStep"] is None


def test_retention_preserves_paused_session_and_deletes_only_old_terminal_threads(client):
    register_login(client)
    product = ready_product(client)
    terminal = create_session(client, product)
    paused = feedback(client, create_session(client, product), "paused").json()
    terminal = feedback(client, terminal, "completed").json()
    terminal = feedback(client, terminal, "completed").json()
    with transaction() as connection:
        job = many(connection, "SELECT * FROM jobs WHERE job_type='checkpoint_cleanup'")
        assert len(job) == 1 and job[0]["status"] == "queued"
        assert job[0]["available_at"] > job[0]["created_at"]
        # 正式数据库更新有touch触发器；测试模拟终态的七天保留期经过。
        execute(connection, "ALTER TABLE guidance_sessions DISABLE TRIGGER touch_guidance_session")
        execute(
            connection,
            "UPDATE guidance_sessions SET updated_at=now()-interval '8 days' WHERE id=:id",
            id=UUID(terminal["id"]),
        )
        execute(connection, "ALTER TABLE guidance_sessions ENABLE TRIGGER touch_guidance_session")
        execute(connection, "UPDATE jobs SET available_at=now() WHERE id=:id", id=job[0]["id"])
    assert run_once(job[0]["id"])
    with transaction() as connection:
        dead = many(
            connection,
            "SELECT thread_id FROM checkpoints WHERE thread_id LIKE :prefix",
            prefix=f"guidance%:{terminal['id']}%",
        )
        live = many(
            connection,
            "SELECT thread_id FROM checkpoints WHERE thread_id LIKE :prefix",
            prefix=f"guidance%:{paused['id']}%",
        )
        assert not dead and live
    restored = post(
        client, f"/guidance-sessions/{paused['id']}/resume", {"expectedVersion": paused["version"]}
    )
    assert restored.status_code == 200 and restored.json()["currentStepNo"] == 1
