"""systemd OnFailure仅落本地事件，不依赖Docker/数据库/模型，不发送通知。"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

KINDS = {
    "itemtalk-backup": "daily-failed",
    "itemtalk-restore": "weekly-failed",
    "itemtalk-health": "health-failed",
}


def record_failure(directory, unit):
    """发布包仅含infra，宿主无API依赖；结构由回归测试与ops_alerts保持一致。"""
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink() or getattr(root, "is_junction", lambda: False)() or not root.is_dir():
        raise ValueError("Alert directory must be a real directory")
    now = datetime.now(timezone.utc).isoformat()  # noqa: UP017 - 宿主兼容Python3.10
    identifier = uuid4().hex
    event = {
        "formatVersion": 1,
        "eventId": identifier,
        "kind": KINDS[unit],
        "createdAt": now,
        "state": "pending",
        "attempts": 0,
        "nextAttemptAt": now,
        "notificationVerified": False,
    }
    descriptor = os.open(
        root / f"event-{identifier}.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        json.dump(event, file, sort_keys=True)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    print(f"ItemTalk alert generated kind={event['kind']} eventId={identifier} state=pending")
    return event


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in KINDS:
        raise SystemExit("Unknown operational unit")
    try:
        record_failure(Path("/opt/itemtalk/backups/alerts"), sys.argv[1])
    except (ValueError, OSError):
        raise SystemExit("Alert persistence failed; inspect systemd journal") from None


if __name__ == "__main__":
    main()
