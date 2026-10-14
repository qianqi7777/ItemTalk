"""COS源只读、对象清单及实际DB/归档恢复回归；对象网络由内存替身控制。"""

import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import psycopg
import pytest
from sqlalchemy.engine import make_url

from app.backup_assets import file_manifest
from app.backup_cos import MAX_OBJECT_BYTES, object_keys, snapshot_objects
from app.config import settings
from app.restore_check import manifest, restore_drill
from tests.conftest import register_login
from tests.test_api import post
from tests.test_backup_assets import pg_bin
from tests.test_private_cleanup import upload_image


class ReadOnlyObjects:
    """没有写入/删除接口，任何源对象变动仅用于模拟停写失效。"""

    def __init__(self, objects):
        self.objects = objects
        self.reads = []

    def read(self, key, maximum=MAX_OBJECT_BYTES):
        self.reads.append(key)
        if key not in self.objects:
            raise FileNotFoundError
        return self.objects[key]


def test_object_snapshot_records_missing_and_preserves_exact_bytes(tmp_path):
    key, missing = "private/" + "a" * 32, "temporary/" + "b" * 32
    data = b"private synthetic sample"
    storage = ReadOnlyObjects({key: data})
    result = snapshot_objects(storage, [key, missing], tmp_path)
    assert result == {
        key: {"sizeBytes": len(data), "sha256": hashlib.sha256(data).hexdigest()},
        missing: None,
    }
    assert (tmp_path / key).read_bytes() == data
    assert not (tmp_path / missing).exists()
    assert snapshot_objects(storage, [key, missing]) == result
    storage.objects[missing] = b"late upload"
    assert snapshot_objects(storage, [key, missing]) != result


@pytest.mark.parametrize("key", ["../escape", "private/../../escape", "other/" + "a" * 32])
def test_unsafe_database_and_snapshot_keys_fail_before_storage_read(key, tmp_path):
    connection = Mock()
    connection.execute.return_value.fetchall.return_value = [(key,)]
    with pytest.raises(ValueError, match="Unsupported"):
        object_keys(connection)
    storage = ReadOnlyObjects({})
    with pytest.raises(ValueError, match="Unsupported"):
        snapshot_objects(storage, [key], tmp_path)
    assert storage.reads == [] and not list(tmp_path.iterdir())


def test_object_snapshot_refuses_oversized_bytes_without_writing(tmp_path):
    key = "private/" + "a" * 32
    storage = ReadOnlyObjects({key: b"x" * (MAX_OBJECT_BYTES + 1)})
    with pytest.raises(ValueError, match="limit"):
        snapshot_objects(storage, [key], tmp_path)
    assert not list(tmp_path.iterdir())


def test_cos_mode_requires_explicit_pause_and_complete_exclusive_configuration(monkeypatch):
    with pytest.raises(ValueError, match="paused"):
        restore_drill(cos_assets=True)
    with pytest.raises(ValueError, match="mutually"):
        restore_drill(assets_directory="unused", cos_assets=True, writers_paused=True)
    with pytest.raises(ValueError, match="COS"):
        restore_drill(cos_assets=True, writers_paused=True)


def install_cos_objects(client, monkeypatch):
    register_login(client)
    upload_image(client)
    assert (
        post(
            client,
            "/assets/presign",
            {
                "filename": "pending.png",
                "mimeType": "image/png",
                "sizeBytes": 80,
                "kind": "feedback",
            },
        ).status_code
        == 201
    )
    root = Path(settings().storage_directory)
    storage = ReadOnlyObjects({key: (root / key).read_bytes() for key in file_manifest(root)})
    monkeypatch.setenv("STORAGE_BACKEND", "cos")
    monkeypatch.setenv("COS_BUCKET", "test-only-bucket")
    monkeypatch.setenv("COS_SECRET_ID", "test-only-id")
    monkeypatch.setenv("COS_SECRET_KEY", "test-only-key")
    settings.cache_clear()
    monkeypatch.setattr("app.restore_check.Storage", lambda: storage)
    args = make_url(settings().db_url()).translate_connect_args(username="user", database="dbname")
    return storage, args


@pytest.mark.postgres
def test_cos_registry_paired_with_real_database_restores_exact_bytes(client, tmp_path, monkeypatch):
    storage, args = install_cos_objects(client, monkeypatch)
    objects_before = dict(storage.objects)
    with psycopg.connect(**args) as source:
        before = manifest(source)
        keys = object_keys(source)
    output = tmp_path / "backup"
    result = restore_drill(pg_bin(), output, writers_paused=True, cos_assets=True)
    assert result["status"] == "passed" and result["referencedAssets"] == 1
    assert result["assetSource"] == "cos" and result["assetRestoreTarget"] == "local-isolated"
    assert result["inventoriedObjects"] == 3 and result["missingOptionalObjects"] == 1
    assert result["assetFiles"] == 2 and result["assetBytes"] == sum(
        map(len, objects_before.values())
    )
    assert storage.objects == objects_before and storage.reads == keys + keys
    bundle = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    assert bundle["formatVersion"] == 2 and len(bundle["cosCurrentObjects"]) == 3
    assert bundle["assets"] == {
        key: value for key, value in bundle["cosCurrentObjects"].items() if value
    }
    assert not list(output.glob("itemtalk_cos_*")) and not list(output.glob("itemtalk_assets_*"))
    with psycopg.connect(**args) as source:
        assert manifest(source) == before
        assert (
            source.execute(
                "SELECT count(*) FROM pg_database WHERE datname LIKE 'itemtalk_restore_%'"
            ).fetchone()[0]
            == 0
        )


@pytest.mark.postgres
def test_missing_confirmed_cos_object_cannot_pass_backup(client, tmp_path, monkeypatch):
    storage, args = install_cos_objects(client, monkeypatch)
    confirmed = next(key for key in storage.objects if key.startswith("private/"))
    del storage.objects[confirmed]
    with pytest.raises(ValueError, match="missing"):
        restore_drill(pg_bin(), tmp_path / "backup", writers_paused=True, cos_assets=True)
    assert not list((tmp_path / "backup").glob("itemtalk_cos_*"))
    with psycopg.connect(**args) as source:
        assert (
            source.execute(
                "SELECT count(*) FROM pg_database WHERE datname LIKE 'itemtalk_restore_%'"
            ).fetchone()[0]
            == 0
        )


@pytest.mark.postgres
def test_changed_cos_bytes_abort_before_database_restore(client, tmp_path, monkeypatch):
    storage, args = install_cos_objects(client, monkeypatch)
    original = storage.read
    changed = next(iter(storage.objects))

    def read(key, maximum=MAX_OBJECT_BYTES):
        if key == changed and key in storage.reads:
            return b"changed after snapshot"
        return original(key, maximum)

    monkeypatch.setattr(storage, "read", read)
    with pytest.raises(ValueError, match="COS objects changed"):
        restore_drill(pg_bin(), tmp_path / "backup", writers_paused=True, cos_assets=True)
    assert not list((tmp_path / "backup").glob("itemtalk_cos_*"))
    with psycopg.connect(**args) as source:
        assert (
            source.execute(
                "SELECT count(*) FROM pg_database WHERE datname LIKE 'itemtalk_restore_%'"
            ).fetchone()[0]
            == 0
        )
