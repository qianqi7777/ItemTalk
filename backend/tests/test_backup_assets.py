"""配对备份的文件字节、目录隔离、损坏/链接归档及实际DB恢复回归。"""

import hashlib
import io
import json
import os
import shutil
import tarfile
from pathlib import Path

import psycopg
import pytest
from sqlalchemy.engine import make_url

from app import restore_check
from app.backup_assets import (
    archive_assets,
    file_manifest,
    restore_assets,
    safe_name,
    verify_references,
)
from app.config import settings
from app.restore_check import manifest, restore_drill
from tests.conftest import register_login
from tests.test_private_cleanup import upload_image


@pytest.fixture
def assets(tmp_path):
    root = tmp_path / "assets"
    root.mkdir()
    for key, data in {"private/confirmed": b"confirmed", "temporary/pending": b"pending"}.items():
        target = root / key
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(data)
    return root


def write_tar(path, entries):
    with tarfile.open(path, "w:gz") as archive:
        for name, data, kind in entries:
            info = tarfile.TarInfo(name)
            if kind == "file":
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
            else:
                info.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
                info.linkname = "../outside"
                archive.addfile(info)


def test_roundtrip_preserves_all_file_bytes_and_leaves_source_intact(assets, tmp_path):
    expected = file_manifest(assets)
    archive = tmp_path / "pair.tar.gz"
    archive_assets(assets, archive, expected)
    restored = tmp_path / "restored"
    restored.mkdir()
    restore_assets(archive, restored, expected)
    assert file_manifest(restored) == expected == file_manifest(assets)
    assert (restored / "private/confirmed").read_bytes() == b"confirmed"
    assert (restored / "temporary/pending").read_bytes() == b"pending"


@pytest.mark.parametrize("key", ["../outside", "/outside", "x/../file", "x\\file", "C:/x", "x//y"])
def test_unsafe_names_are_rejected(key):
    with pytest.raises(ValueError):
        safe_name(key)


@pytest.mark.parametrize(
    "entries",
    [
        [("../outside", b"secret", "file")],
        [("private/confirmed", b"", "symlink")],
        [("private/confirmed", b"", "hardlink")],
        [("private/confirmed", b"corrupted", "file")],
        [("private/confirmed", b"oversized-data", "file")],
        [("private/confirmed", b"confirmed", "file")] * 2,
        [("unknown", b"confirmed", "file")],
        [],
    ],
)
def test_malicious_or_damaged_archive_never_writes_outside_target(assets, tmp_path, entries):
    restored = tmp_path / "restored"
    restored.mkdir()
    archive = tmp_path / "bad.tar.gz"
    write_tar(archive, entries)
    with pytest.raises(ValueError):
        restore_assets(archive, restored, file_manifest(assets))
    assert not (tmp_path / "outside").exists()
    assert (assets / "private/confirmed").read_bytes() == b"confirmed"


def test_source_changes_and_existing_targets_are_rejected(assets, tmp_path):
    expected = file_manifest(assets)
    (assets / "private/confirmed").write_bytes(b"different")
    with pytest.raises(ValueError, match="changed"):
        archive_assets(assets, tmp_path / "changed.tar.gz", expected)
    with pytest.raises(ValueError, match="outside"):
        archive_assets(assets, assets / "backup.tar.gz", expected)
    with pytest.raises(ValueError, match="empty"):
        restore_assets(tmp_path / "changed.tar.gz", assets, expected)


def test_paired_mode_requires_paused_writers_and_local_backend(assets, tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="paused"):
        restore_drill(output_directory=tmp_path, assets_directory=assets)
    monkeypatch.setenv("STORAGE_BACKEND", "cos")
    settings.cache_clear()
    try:
        with pytest.raises(ValueError, match="COS"):
            restore_drill(output_directory=tmp_path, assets_directory=assets, writers_paused=True)
    finally:
        settings.cache_clear()


def pg_bin():
    value = os.environ.get("TEST_PG_BIN")
    if value:
        return Path(value)
    if not all(shutil.which(name) for name in ("pg_dump", "pg_restore")):
        pytest.skip("PostgreSQL client binaries are required for the paired restore drill")
    return None


@pytest.mark.postgres
def test_real_paired_database_and_file_restore(client, tmp_path, monkeypatch):
    register_login(client)
    upload_image(client)
    # 没有PUT的pending对象允许没有文件；确认后的私有文件必须匹配元数据。
    from tests.test_api import post

    assert (
        post(
            client,
            "/assets/presign",
            {"filename": "new.png", "mimeType": "image/png", "sizeBytes": 80, "kind": "feedback"},
        ).status_code
        == 201
    )
    root = Path(settings().storage_directory)
    expected_files = file_manifest(root)
    args = make_url(settings().db_url()).translate_connect_args(username="user", database="dbname")
    with psycopg.connect(**args) as source:
        before = manifest(source)
        assert verify_references(source, expected_files) == 1
    result = restore_drill(pg_bin(), tmp_path / "backup", root, writers_paused=True)
    assert result["status"] == "passed" and result["referencedAssets"] == 1
    assert result["assetFiles"] == 2
    assert result["assetBytes"] == sum(file["sizeBytes"] for file in expected_files.values())
    bundle = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    assert bundle["assets"] == expected_files and bundle["formatVersion"] == 1
    checksum_file = Path(result["backup"] + ".sha256")
    for entry in checksum_file.read_text().splitlines():
        checksum, name = entry.split("  ")
        assert hashlib.sha256((checksum_file.parent / name).read_bytes()).hexdigest() == checksum
    assert not list((tmp_path / "backup").glob("itemtalk_assets_*"))
    with psycopg.connect(**args) as source:
        assert manifest(source) == before
        assert (
            source.execute(
                "SELECT count(*) FROM pg_database WHERE datname LIKE 'itemtalk_restore_%'"
            ).fetchone()[0]
            == 0
        )
        missing = {
            key: value for key, value in expected_files.items() if key.startswith("temporary/")
        }
        with pytest.raises(ValueError, match="missing"):
            verify_references(source, missing)
    # 错误归档也必须清理本次临时数据库，不能仅验证源文件哈希后跳过实际恢复。
    original = restore_check.archive_assets

    def corrupt_archive(directory, archive, expected):
        original(directory, archive, expected)
        entries = [(key, b"X" * value["sizeBytes"], "file") for key, value in expected.items()]
        write_tar(archive, entries)

    monkeypatch.setattr(restore_check, "archive_assets", corrupt_archive)
    with pytest.raises(ValueError, match="checksum"):
        restore_drill(pg_bin(), tmp_path / "corrupt", root, writers_paused=True)
    with psycopg.connect(**args) as source:
        assert manifest(source) == before
        assert (
            source.execute(
                "SELECT count(*) FROM pg_database WHERE datname LIKE 'itemtalk_restore_%'"
            ).fetchone()[0]
            == 0
        )
    assert file_manifest(root) == expected_files
