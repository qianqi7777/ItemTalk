"""持久化告警事件及有界重试。渠道默认未启用，提交成功不等于邮件送达。"""

import argparse
import json
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from app.backup_assets import regular_file

KINDS = {"daily-failed", "weekly-failed", "health-failed", "notification-test"}
LOGGER = logging.getLogger(__name__)
MAX_ATTEMPTS = 5


def event_root(directory):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    # systemd失败记录入口也运行在宿主Python上；Ubuntu旧版Path没有is_junction。
    if root.is_symlink() or getattr(root, "is_junction", lambda: False)() or not root.is_dir():
        raise ValueError("Alert directory must be a real directory")
    return root.resolve()


def save_event(path, value, *, replace=False):
    temporary = path.with_name(f".event-{uuid4().hex}.tmp") if replace else path
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False, sort_keys=True)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        if replace:
            regular_file(path)
            os.replace(temporary, path)
    finally:
        if replace and temporary.exists():
            temporary.unlink()


def emit_event(directory, kind, now=None):
    if kind not in KINDS:
        raise ValueError("Unknown alert type")
    now = now or datetime.now(UTC)
    identifier = uuid4().hex
    event = {
        "formatVersion": 1,
        "eventId": identifier,
        "kind": kind,
        "createdAt": now.isoformat(),
        "state": "pending",
        "attempts": 0,
        "nextAttemptAt": now.isoformat(),
        "notificationVerified": False,
    }
    save_event(event_root(directory) / f"event-{identifier}.json", event)
    # 固定字段方便腾讯云日志采集，不输出异常、生产内容、收件人或凭据。
    LOGGER.error("ItemTalk alert generated kind=%s eventId=%s state=pending", kind, identifier)
    return event


def dispatch_events(directory, sender=None, *, enabled=False, now=None):
    """sender返回已提交或已确认送达；关闭渠道不消耗次数、不丢事件。

    实际腾讯云渠道须在联系人和规则配置后接入；此函数不自行创建/启用规则。
    调用方必须串行执行（systemd oneshot或备份锁），避免重复发送。
    """
    now = now or datetime.now(UTC)
    result = {
        "submitted": 0,
        "failed": 0,
        "pending": 0,
        "exhausted": 0,
        "notificationVerified": False,
        "channelEnabled": enabled,
    }
    for path in sorted(event_root(directory).glob("event-*.json")):
        regular_file(path)
        if not re.fullmatch(r"event-[a-f0-9]{32}\.json", path.name) or path.stat().st_size > 4096:
            raise ValueError("Invalid alert file")
        event = json.loads(path.read_text(encoding="utf-8"))
        if (
            event.get("formatVersion") != 1
            or event.get("kind") not in KINDS
            or path.name != f"event-{event.get('eventId')}.json"
            or event.get("state")
            not in {"pending", "retry-wait", "exhausted", "submitted", "delivered"}
            or type(event.get("attempts")) is not int
            or not 0 <= event["attempts"] <= MAX_ATTEMPTS
        ):
            raise ValueError("Invalid alert event")
        if event["state"] in {"submitted", "delivered"}:
            continue
        if event["attempts"] >= MAX_ATTEMPTS:
            result["exhausted"] += 1
            continue
        due = datetime.fromisoformat(event["nextAttemptAt"])
        if due.tzinfo is None:
            raise ValueError("Alert timestamp must include timezone")
        if not enabled or due > now:
            result["pending"] += 1
            continue
        event["attempts"] += 1
        try:
            if sender is None:
                raise ValueError("Channel adapter is not configured")
            ack = sender({key: event[key] for key in ("eventId", "kind", "createdAt")})
            # 不接受空返回、HTTP 200的字符串或仅已排队之类的不明确回执。
            if not isinstance(ack, dict) or ack.get("submitted") is not True:
                raise ValueError("Channel did not acknowledge submission")
            delivered = ack.get("notificationVerified") is True
            event.update(
                state="delivered" if delivered else "submitted",
                notificationVerified=delivered,
                lastErrorCode=None,
            )
            result["submitted"] += 1
            result["notificationVerified"] |= delivered
        except Exception:
            # 只记录受控码；第三方异常文本可能包含令牌、请求内容或邮件地址。
            event.update(
                state="exhausted" if event["attempts"] >= MAX_ATTEMPTS else "retry-wait",
                lastErrorCode="CHANNEL_SUBMISSION_FAILED",
                notificationVerified=False,
                nextAttemptAt=(
                    now + timedelta(seconds=min(60 * 2 ** (event["attempts"] - 1), 3600))
                ).isoformat(),
            )
            result["failed"] += 1
            LOGGER.error(
                "ItemTalk alert submission failed eventId=%s attempts=%s",
                event["eventId"],
                event["attempts"],
            )
        save_event(path, event, replace=True)
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Persist an alert; notification channel stays disabled"
    )
    parser.add_argument("--directory", type=Path, default=Path("/backups/alerts"))
    parser.add_argument("--emit", choices=sorted(KINDS))
    args = parser.parse_args()
    try:
        if args.emit:
            emit_event(args.directory, args.emit)
        print(json.dumps(dispatch_events(args.directory)))
    except (OSError, ValueError, KeyError, TypeError):
        parser.exit(1, "Alert state update failed; channel remains unverified.\n")


if __name__ == "__main__":
    main()
