"""真实邮件入口只用Mock SMTP测试，永不联网、发送邮件或宣称真实验收。"""

import json
import smtplib
import ssl
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from app import ops_mail
from app.ops_alerts import dispatch_events, emit_event
from app.ops_mail import MailConfig, SmtpSender, configuration, send_test

NOW = datetime(2026, 10, 14, tzinfo=UTC)


def values():
    return {
        "SMTP_HOST": "mock.invalid",
        "SMTP_PORT": "465",
        "SMTP_TLS_MODE": "ssl",
        "SMTP_USERNAME": "test-only-user",
        "SMTP_PASSWORD": "test-only-secret",
        "SMTP_FROM": "sender@example.invalid",
        "SMTP_TO": "recipient@example.invalid",
        "OPS_SMTP_TEST_ENABLED": "true",
        "OPS_ALERTS_ENABLED": "false",
    }


class MockSMTP:
    def __init__(self, error=None, rejected=None):
        self.calls = []
        self.messages = []
        self.error = error
        self.rejected = {} if rejected is None else rejected

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def ehlo(self):
        self.calls.append("ehlo")

    def starttls(self, *, context):
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        self.calls.append("starttls")

    def login(self, username, password):
        self.calls.append("login")
        if self.error:
            raise self.error

    def send_message(self, message, **kwargs):
        self.calls.append("send")
        self.messages.append(message)
        return self.rejected


def install_mock(monkeypatch, client):
    factory = Mock(return_value=client)
    monkeypatch.setattr(ops_mail.smtplib, "SMTP_SSL", factory)
    monkeypatch.setattr(ops_mail.smtplib, "SMTP", factory)
    return factory


@pytest.mark.parametrize("mode", ["ssl", "starttls"])
def test_tls_precedes_auth_and_submission_never_claims_delivery(monkeypatch, mode):
    config = values()
    config["SMTP_TLS_MODE"] = mode
    client = MockSMTP()
    factory = install_mock(monkeypatch, client)
    ack = SmtpSender(MailConfig.load(config))(
        {"eventId": "a" * 32, "kind": "notification-test", "createdAt": NOW.isoformat()}
    )
    assert ack == {"submitted": True, "notificationVerified": False}
    assert factory.call_args.kwargs["timeout"] == 15
    if mode == "ssl":
        context = factory.call_args.kwargs["context"]
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        assert client.calls == ["login", "send"]
    else:
        assert client.calls == ["ehlo", "starttls", "ehlo", "login", "send"]
    assert "test-only-secret" not in client.messages[0].as_string()
    assert "test-only-user" not in client.messages[0].as_string()


def test_smtp_failure_is_recorded_redacted_and_retry_keeps_message_id(
    tmp_path, monkeypatch, caplog
):
    client = MockSMTP(error=smtplib.SMTPAuthenticationError(535, b"SECRET synthetic credential"))
    install_mock(monkeypatch, client)
    sender = SmtpSender(MailConfig.load(values()))
    emit_event(tmp_path, "daily-failed", NOW)
    result = dispatch_events(tmp_path, sender, enabled=True, now=NOW)
    path = next(tmp_path.glob("event-*.json"))
    assert result["failed"] == 1 and json.loads(path.read_text())["state"] == "retry-wait"
    assert "SECRET" not in caplog.text and "SECRET" not in path.read_text()
    client.error = None
    client.rejected = {"recipient@example.invalid": (450, b"SECRET temporary reject")}
    assert (
        dispatch_events(tmp_path, sender, enabled=True, now=NOW + timedelta(seconds=60))["failed"]
        == 1
    )
    first_id = client.messages[0]["Message-ID"]
    client.rejected = {}
    result = dispatch_events(tmp_path, sender, enabled=True, now=NOW + timedelta(seconds=180))
    assert result["submitted"] == 1 and not result["notificationVerified"]
    assert client.messages[1]["Message-ID"] == first_id
    assert json.loads(path.read_text())["attempts"] == 3


@pytest.mark.parametrize(
    "field,value",
    [
        ("SMTP_TLS_MODE", "none"),
        ("SMTP_HOST", "smtp://bad"),
        ("SMTP_FROM", "sender@example.invalid\r\nBcc: bad"),
        ("SMTP_TO", "a@example.invalid,b@example.invalid"),
    ],
)
def test_invalid_configuration_never_connects(monkeypatch, field, value):
    factory = install_mock(monkeypatch, MockSMTP())
    config = values()
    config[field] = value
    with pytest.raises(ValueError):
        MailConfig.load(config)
    factory.assert_not_called()


def test_default_check_and_all_opt_in_gates_send_nothing(tmp_path, monkeypatch, capsys):
    factory = install_mock(monkeypatch, MockSMTP())
    monkeypatch.setattr(ops_mail, "configuration", lambda env: values())
    monkeypatch.setattr("sys.argv", ["mail"])
    ops_mail.main()
    assert json.loads(capsys.readouterr().out)["status"] == "configuration-only"
    for args in (["--send-test"], ["--send-test", "--real-email"], ["--real-email"]):
        monkeypatch.setattr("sys.argv", ["mail", *args])
        with pytest.raises(SystemExit):
            ops_mail.main()
    config = values()
    config["OPS_SMTP_TEST_ENABLED"] = "false"
    monkeypatch.setattr(ops_mail, "configuration", lambda env: config)
    monkeypatch.setattr(
        "sys.argv", ["mail", "--send-test", "--real-email", "--report", str(tmp_path / "test.json")]
    )
    with pytest.raises(SystemExit):
        ops_mail.main()
    factory.assert_not_called()
    assert not list(tmp_path.iterdir())


def test_explicit_mock_cli_reserves_report_and_does_not_drain_production_queue(
    tmp_path, monkeypatch
):
    client = MockSMTP()
    factory = install_mock(monkeypatch, client)
    monkeypatch.setattr(ops_mail, "configuration", lambda env: values())
    root = tmp_path / "events"
    production_event = emit_event(root, "weekly-failed")
    report = tmp_path / "test.json"
    monkeypatch.setattr(
        "sys.argv",
        ["mail", "--send-test", "--real-email", "--report", str(report), "--directory", str(root)],
    )
    ops_mail.main()
    result = json.loads(report.read_text())
    assert result["status"] == "submitted" and not result["notificationVerified"]
    assert json.loads(next(root.glob("event-*.json")).read_text()) == production_event
    assert len(client.messages) == 1 and "notification-test" in client.messages[0]["Subject"]
    assert factory.call_count == 1
    with pytest.raises(SystemExit):
        ops_mail.main()
    assert factory.call_count == 1  # 已有报告导致发送前停止。


def test_failure_report_is_not_false_success(tmp_path, monkeypatch):
    client = MockSMTP(error=TimeoutError("SECRET network response"))
    install_mock(monkeypatch, client)
    monkeypatch.setattr(ops_mail, "configuration", lambda env: values())
    report = tmp_path / "test.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "mail",
            "--send-test",
            "--real-email",
            "--report",
            str(report),
            "--directory",
            str(tmp_path / "events"),
        ],
    )
    with pytest.raises(SystemExit) as error:
        ops_mail.main()
    assert error.value.code == 1
    result = json.loads(report.read_text())
    assert (
        result["status"] == "failed"
        and result["failed"] == 1
        and not result["notificationVerified"]
    )
    assert "SECRET" not in report.read_text()
    event = next((tmp_path / "events").glob("mail-test-*/event-*.json"))
    assert json.loads(event.read_text())["state"] == "retry-wait"


def test_explicit_environment_file_disables_stale_shell_gate_and_does_not_expand_secrets(
    tmp_path, monkeypatch
):
    path = tmp_path / "restricted.env"
    path.write_text("OPS_SMTP_TEST_ENABLED=false\nSMTP_PASSWORD='${SECRET_TEST_VALUE}'\n")
    path.chmod(0o600)
    monkeypatch.setenv("OPS_SMTP_TEST_ENABLED", "true")
    monkeypatch.setenv("SECRET_TEST_VALUE", "must-not-expand")
    config = configuration(path)
    assert config["OPS_SMTP_TEST_ENABLED"] == "false"
    assert config["SMTP_PASSWORD"] == "${SECRET_TEST_VALUE}"


def test_send_test_disabled_before_directory_or_network(tmp_path, monkeypatch):
    factory = install_mock(monkeypatch, MockSMTP())
    with pytest.raises(ValueError, match="disabled"):
        send_test(tmp_path, {"OPS_SMTP_TEST_ENABLED": "false"})
    assert not list(tmp_path.iterdir())
    factory.assert_not_called()


def test_retry_isolated_test_respects_backoff_and_never_sends_twice_after_acceptance(
    tmp_path, monkeypatch
):
    client = MockSMTP(error=TimeoutError("synthetic timeout"))
    factory = install_mock(monkeypatch, client)
    first = send_test(tmp_path, values())
    assert first["status"] == "failed" and factory.call_count == 1
    waiting = send_test(tmp_path, values(), first["testRunId"])
    assert waiting["status"] == "waiting" and factory.call_count == 1
    event_path = next((tmp_path / ("mail-test-" + first["testRunId"])).glob("event-*.json"))
    event = json.loads(event_path.read_text())
    event["nextAttemptAt"] = NOW.isoformat()
    event_path.write_text(json.dumps(event))
    client.error = None
    result = send_test(tmp_path, values(), first["testRunId"])
    assert result["status"] == "submitted" and factory.call_count == 2
    assert send_test(tmp_path, values(), first["testRunId"])["status"] == "submitted"
    assert factory.call_count == 2
