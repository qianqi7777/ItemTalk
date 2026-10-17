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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["grant", "revoke"])
    parser.add_argument("--user-id", type=UUID, required=True)
    args = parser.parse_args()
    try:
        provision(args.user_id, args.action == "grant")
    except Exception:
        parser.exit(
            1, "Administrator provisioning failed; use the restricted migration identity.\n"
        )
    print("Administrator membership updated")


if __name__ == "__main__":
    main()
