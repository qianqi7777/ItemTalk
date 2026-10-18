"""管理员授权只供迁移/运维身份执行，HTTP运行身份没有成员表写权限。"""

import argparse
from uuid import UUID

from app.db import actor, execute, one, transaction


def provision(user_id, enabled):
    with actor(user_id, allow_deleted=True), transaction() as connection:
        if enabled:
            user = one(
                connection,
                "SELECT id FROM users WHERE id=:u AND status='active' AND deleted_at IS NULL",
                u=user_id,
            )
            if not user:
                raise ValueError("Active account required")
            execute(
                connection,
                "INSERT INTO admin_memberships(user_id) VALUES(:u) ON CONFLICT DO NOTHING",
                u=user_id,
            )
        else:
            execute(connection, "DELETE FROM admin_memberships WHERE user_id=:u", u=user_id)


def reset_runtime(user_id, expected_version):
    """回退镜像前先解除暂停并恢复现有环境额度；通过原子审计入口，不清零计数。"""
    from app.config import settings
    from app.operations import RuntimeUpdate, update_runtime

    config = settings()
    with actor(user_id), transaction() as connection:
        return update_runtime(
            connection,
            RuntimeUpdate(
                expectedVersion=expected_version,
                globalDailyLimit=config.daily_global_model_call_limit,
                subjectDailyLimit=config.daily_model_call_limit,
                backgroundPaused=False,
                chatProfile="current",
            ),
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["grant", "revoke", "reset-runtime"])
    parser.add_argument("--user-id", type=UUID, required=True)
    parser.add_argument("--expected-version", type=int)
    args = parser.parse_args()
    if args.action == "reset-runtime" and (
        args.expected_version is None or args.expected_version < 1
    ):
        parser.error("reset-runtime requires --expected-version >= 1")
    try:
        if args.action == "reset-runtime":
            result = reset_runtime(args.user_id, args.expected_version)
            print(f"Runtime configuration restored; version={result['version']}")
            return
        provision(args.user_id, args.action == "grant")
    except Exception:
        parser.exit(
            1,
            "Administrator operation failed; check identity, version "
            "and restricted configuration.\n",
        )
    print("Administrator membership updated")


if __name__ == "__main__":
    main()
