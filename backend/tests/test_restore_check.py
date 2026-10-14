"""恢复核验接受 PostgreSQL 等价数组转换，仍拒绝约束字典变化。"""

import psycopg
import pytest
from sqlalchemy.engine import make_url

from app.config import settings
from app.restore_check import canonical_constraint, manifest


def test_dump_array_cast_normalization_preserves_allowed_values():
    original = (
        "CHECK (state = ANY ((ARRAY['ready'::character varying, "
        "'failed'::character varying])::text[]))"
    )
    restored = (
        "CHECK (state = ANY (ARRAY[('ready'::character varying)::text, "
        "('failed'::character varying)::text]))"
    )
    assert canonical_constraint(original) == restored
    changed = restored.replace("'failed'", "'unsafe'")
    assert canonical_constraint(original) != canonical_constraint(changed)


def test_other_constraint_changes_remain_visible():
    assert canonical_constraint("CHECK (page_no >= 1)") != canonical_constraint(
        "CHECK (page_no >= 0)"
    )


@pytest.mark.postgres
def test_restore_manifest_detects_rls_and_column_permission_changes(database):
    args = make_url(settings().db_url()).translate_connect_args(username="user", database="dbname")
    with psycopg.connect(**args) as connection:
        before = manifest(connection)
        connection.execute("ALTER TABLE guidance_sessions NO FORCE ROW LEVEL SECURITY")
        assert manifest(connection)["securityTables"] != before["securityTables"]
        connection.execute("REVOKE UPDATE(display_name) ON users FROM itemtalk_app")
        assert manifest(connection)["columnGrants"] != before["columnGrants"]
        connection.execute("ALTER TABLE manuals DISABLE TRIGGER protect_manual_generation")
        assert manifest(connection)["triggers"] != before["triggers"]
        # 本测试只检查演练的检测能力，不把降级后的测试权限持久化。
        connection.rollback()
