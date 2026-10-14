"""只验证事件/Mock投递/重试，不发送真实告警，不宣称真实通知已验收。"""

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.ops_alerts import MAX_ATTEMPTS, dispatch_events, emit_event

NOW = datetime(2026, 10, 14, tzinfo=UTC)


def read_event(root):
    return json.loads(next(root.glob("event-*.json")).read_text())


def test_disabled_channel_preserves_pending_events_without_sending(tmp_path):
    event = emit_event(tmp_path, "daily-failed", NOW)
    sender = Mock()
    result = dispatch_events(tmp_path, sender, now=NOW)
    assert result["pending"] == 1 and not result["notificationVerified"]
    assert read_event(tmp_path) == event
    sender.assert_not_called()


def test_failed_submission_redacts_error_and_retries_after_backoff(tmp_path, caplog):
    emit_event(tmp_path, "weekly-failed", NOW)
    sender = Mock(side_effect=RuntimeError("SECRET synthetic sensitive URL"))
    result = dispatch_events(tmp_path, sender, enabled=True, now=NOW)
    event = read_event(tmp_path)
    assert result["failed"] == 1 and event["state"] == "retry-wait"
    assert event["attempts"] == 1 and not event["notificationVerified"]
    assert "SECRET" not in caplog.text and "SECRET" not in json.dumps(event)
    dispatch_events(tmp_path, sender, enabled=True, now=NOW + timedelta(seconds=59))
    assert sender.call_count == 1
    sender.side_effect = None
    sender.return_value = {"submitted": True, "notificationVerified": False}
    result = dispatch_events(tmp_path, sender, enabled=True, now=NOW + timedelta(seconds=60))
    assert result["submitted"] == 1 and not result["notificationVerified"]
    assert read_event(tmp_path)["state"] == "submitted"
    dispatch_events(tmp_path, sender, enabled=True, now=NOW + timedelta(days=1))
    assert sender.call_count == 2


@pytest.mark.parametrize("response", [None, True, {"ok": True}, {"submitted": False}])
def test_ambiguous_ack_is_failure_not_success(tmp_path, response):
    emit_event(tmp_path, "health-failed", NOW)
    result = dispatch_events(tmp_path, lambda event: response, enabled=True, now=NOW)
    assert result["failed"] == 1 and result["submitted"] == 0
    assert read_event(tmp_path)["state"] == "retry-wait"


def test_missing_adapter_and_exhausted_retries_keep_failure_evidence(tmp_path):
    emit_event(tmp_path, "notification-test", NOW)
    for attempt in range(MAX_ATTEMPTS):
        dispatch_events(tmp_path, enabled=True, now=NOW + timedelta(hours=attempt))
    event = read_event(tmp_path)
    assert event["attempts"] == MAX_ATTEMPTS and event["state"] == "exhausted"
    sender = Mock()
    result = dispatch_events(tmp_path, sender, enabled=True, now=NOW + timedelta(days=1))
    assert result["exhausted"] == 1 and not result["notificationVerified"]
    sender.assert_not_called()


def test_malformed_kind_is_not_saved(tmp_path):
    with pytest.raises(ValueError):
        emit_event(tmp_path, "https://secret.invalid")
    assert not list(tmp_path.iterdir())


def test_host_failure_recorder_works_without_container_and_matches_event_contract(tmp_path):
    path = Path(__file__).resolve().parents[2] / "infra" / "record_ops_failure.py"
    spec = importlib.util.spec_from_file_location("host_ops_failure", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    event = module.record_failure(tmp_path, "itemtalk-health")
    assert event == read_event(tmp_path)
    assert event["kind"] == "health-failed" and event["state"] == "pending"
    assert dispatch_events(tmp_path)["pending"] == 1
