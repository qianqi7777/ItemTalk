"""恢复核验接受 PostgreSQL 等价数组转换，仍拒绝约束字典变化。"""

import os
import subprocess
import sys

import psycopg
import pytest
from sqlalchemy.engine import make_url

from app.config import settings
from app.restore_check import canonical_constraint, manifest, postgres_clients


@pytest.mark.skipif(os.name == "nt", reason="Debian executable symlink dispatch is POSIX-specific")
def test_postgresql_wrapper_keeps_original_program_name(tmp_path, monkeypatch):
    wrapper = tmp_path / "pg_wrapper"
    wrapper.write_text(
        f"#!{sys.executable}\nimport os, sys\nprint(os.path.basename(sys.argv[0]))\n"
    )
    wrapper.chmod(0o700)
    for name in ("pg_dump", "pg_restore"):
        (tmp_path / name).symlink_to(wrapper)
    monkeypatch.setattr("app.restore_check.shutil.which", lambda name: str(tmp_path / name))
    # 验证实际执行分派，同时覆盖PATH查找和显式客户端目录，不只比较字符串。
    for binaries in (postgres_clients(), postgres_clients(tmp_path)):
        for name, executable in binaries.items():
            result = subprocess.run(  # noqa: S603
                [executable], capture_output=True, text=True, check=True, timeout=5
            )
            assert result.stdout.strip() == name


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
