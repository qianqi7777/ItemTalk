"""可选SMTP告警适配及单次真实测试入口；默认不联网、不发送、不宣称送达。"""

import argparse
import json
import os
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from uuid import uuid4

from pydantic import SecretStr

from app.backup_assets import regular_file
from app.ops_alerts import KINDS, dispatch_events, emit_event, event_root
from app.ops_config import configuration

MAIL_NAMES = (
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_TLS_MODE",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    "SMTP_FROM",
    "SMTP_TO",
)


def readiness(values):
    return {
        "missingVariables": [name for name in MAIL_NAMES if not values.get(name)],
        "realTestEnabled": values.get("OPS_SMTP_TEST_ENABLED") == "true",
        "productionEnabled": values.get("OPS_ALERTS_ENABLED") == "true",
        "notificationVerified": False,
    }


def address(value):
    # 只接受单个ASCII邮箱，不接受显示名、CRLF或从模型传来的额外收件人。
    if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+", value):
        raise ValueError("Invalid mail address")
    return value


@dataclass(frozen=True, repr=False)
class MailConfig:
    host: str
    port: int
    tls_mode: str
    username: str
    password: SecretStr
    sender: str
    recipient: str

    @classmethod
    def load(cls, values):
        if readiness(values)["missingVariables"]:
            raise ValueError("Mail configuration is incomplete")
        host = values["SMTP_HOST"]
        port = int(values["SMTP_PORT"])
        mode = values["SMTP_TLS_MODE"]
        if (
            not re.fullmatch(r"[A-Za-z0-9.-]+", host)
            or not 1 <= port <= 65535
            or mode not in {"ssl", "starttls"}
        ):
            raise ValueError("Mail host/port/TLS configuration is invalid")
        return cls(
            host,
            port,
            mode,
            values["SMTP_USERNAME"],
            SecretStr(values["SMTP_PASSWORD"]),
            address(values["SMTP_FROM"]),
            address(values["SMTP_TO"]),
        )


class SmtpSender:
    def __init__(self, config):
        self.config = config

    def __call__(self, event):
        if (
            set(event) != {"eventId", "kind", "createdAt"}
            or event["kind"] not in KINDS
            or not re.fullmatch(r"[a-f0-9]{32}", event["eventId"])
            or not isinstance(event["createdAt"], str)
            or len(event["createdAt"]) > 64
            or any(char in event["createdAt"] for char in "\r\n")
        ):
            raise ValueError("Invalid mail event")
        config = self.config
        message = EmailMessage()
        message["From"] = config.sender
        message["To"] = config.recipient
        message["Subject"] = f"[ItemTalk] {event['kind']}"
        # 同一事件重试保留Message-ID，便于邮箱和运维去重；不保证邮件精确一次。
        message["Message-ID"] = f"<itemtalk-{event['eventId']}@{config.sender.split('@')[1]}>"
        message.set_content(
            f"ItemTalk operational event\nType: {event['kind']}\n"
            f"Event ID: {event['eventId']}\nTime: {event['createdAt']}\n"
            "Check restricted operational records. No private data is included.\n"
        )
        context = ssl.create_default_context()
        if config.tls_mode == "ssl":
            connection = smtplib.SMTP_SSL(config.host, config.port, timeout=15, context=context)
        else:
            connection = smtplib.SMTP(config.host, config.port, timeout=15)
        with connection as client:
            # 不开SMTP调试：其输出会包含AUTH、收件人及整个邮件内容。
            if config.tls_mode == "starttls":
                client.ehlo()
                client.starttls(context=context)
                client.ehlo()
            client.login(config.username, config.password.get_secret_value())
            rejected = client.send_message(
                message, from_addr=config.sender, to_addrs=[config.recipient]
            )
            if not isinstance(rejected, dict) or rejected:
                raise ValueError("Mail server did not accept all recipients")
        # SMTP接受不证明投递到收件箱，不能虚构notificationVerified=true。
        return {"submitted": True, "notificationVerified": False}


def send_test(directory, values, retry_test=None):
    if values.get("OPS_SMTP_TEST_ENABLED") != "true":
        raise ValueError("Real mail test is disabled")
    config = MailConfig.load(values)
    identifier = retry_test or uuid4().hex
    if not re.fullmatch(r"[a-f0-9]{32}", identifier):
        raise ValueError("Invalid mail test run identifier")
    root = event_root(directory) / f"mail-test-{identifier}"
    if retry_test is None:
        root.mkdir(mode=0o700)
        emit_event(root, "notification-test")
    elif root.is_symlink() or root.is_junction() or not root.is_dir():
        raise ValueError("Mail test retry requires an existing isolated directory")
    paths = list(root.glob("event-*.json"))
    if len(paths) != 1 or regular_file(paths[0]).stat().st_size > 4096:
        raise ValueError("Mail test requires exactly one restricted event")
    event = json.loads(paths[0].read_text(encoding="utf-8"))
    if event.get("kind") != "notification-test":
        raise ValueError("Cannot retry a production event through the test entry")
    # 单独测试锁排除并发重试；异常退出遗留锁需审核，不能自动删不明归属锁。
    lock = root / ".send.lock"
    descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    try:
        result = dispatch_events(root, SmtpSender(config), enabled=True)
    finally:
        lock.unlink()
    saved = json.loads(paths[0].read_text(encoding="utf-8"))
    status = (
        "submitted"
        if saved["state"] == "submitted"
        else "waiting"
        if result["pending"]
        else "failed"
    )
    return {
        **result,
        "status": status,
        "testRunId": identifier,
        "eventId": event["eventId"],
        "eventDirectory": str(root),
        "notificationVerified": False,
        "deliveryConfirmationRequired": True,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Check SMTP configuration / explicitly send one test"
    )
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--send-test", action="store_true")
    parser.add_argument("--real-email", action="store_true")
    parser.add_argument("--retry-test", help="Retry only this isolated mail-test run after backoff")
    parser.add_argument("--directory", type=Path, default=Path("/backups/alerts"))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        values = configuration(args.env_file)
        if not args.send_test:
            if args.real_email or args.retry_test:
                raise ValueError("Real mail options also require --send-test")
            print(json.dumps({"status": "configuration-only", **readiness(values)}))
            return
        if not args.real_email or args.report is None:
            raise ValueError("Real test requires --real-email and a new --report")
        if not readiness(values)["realTestEnabled"]:
            raise ValueError("OPS_SMTP_TEST_ENABLED must be true after explicit approval")
        # 报告独占创建、先fsync再联网；不可覆盖已有证据，也不处理生产待发队列。
        descriptor = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as report:
            json.dump({"status": "running", "notificationVerified": False}, report)
            report.flush()
            os.fsync(report.fileno())
            try:
                result = send_test(args.directory, values, args.retry_test)
            except (ValueError, OSError):
                result = {
                    "status": "failed",
                    "notificationVerified": False,
                    "errorCode": "MAIL_TEST_FAILED",
                }
            report.seek(0)
            report.truncate()
            json.dump(result, report)
            report.flush()
            os.fsync(report.fileno())
        print(json.dumps(result))
        if result["status"] != "submitted":
            parser.exit(1, "Mail test failed; inspect restricted event and report.\n")
    except (ValueError, OSError):
        parser.exit(1, "Mail test unavailable; configuration and raw server response suppressed.\n")


if __name__ == "__main__":
    main()
