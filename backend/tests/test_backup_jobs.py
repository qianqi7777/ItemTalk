"""COS协议使用Mock，真实测试PG执行导出/隔离恢复；不属于真实云验收。"""

import hashlib
import io
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import psycopg
import pytest

from app import backup_jobs
from app.backup_jobs import (
    BackupDestination,
    bundle_files,
    configured_destination,
    daily_backup,
    prune_local,
    weekly_restore,
)
from app.errors import DomainError
from app.restore_check import manifest
from tests.test_backup_assets import pg_bin
from tests.test_backup_cos import install_cos_objects

NOW = datetime(2026, 10, 14, tzinfo=UTC)


class MockCOS:
    def __init__(self):
        self.objects = {}
        self.calls = []
        self.corrupt = False
        self.fail_put = None

    def get_bucket_versioning(self, **kwargs):
        return {}

    def get_bucket_acl(self, **kwargs):
        return {"Owner": {"ID": "owner"}, "Grant": [{"Grantee": {"ID": "owner"}}]}

    def get_bucket_policy(self, **kwargs):
        return None

    def put_object(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail_put == len(self.calls):
            raise DomainError(503, "INTERNAL_ERROR", "synthetic failure")
        assert kwargs["ACL"] == "private"
        assert kwargs["Metadata"]["x-cos-forbid-overwrite"] == "true"
        key = kwargs["Key"]
        if key in self.objects:
            raise DomainError(409, "RESOURCE_CONFLICT", "synthetic conflict")
        self.objects[key] = kwargs["Body"].read()

    def get_object(self, **kwargs):
        data = self.objects[kwargs["Key"]]
        stream = io.BytesIO(b"X" * len(data) if self.corrupt else data)
        return {"Body": SimpleNamespace(get_raw_stream=lambda: stream)}


def destination():
    client = MockCOS()
    storage = SimpleNamespace(
        client=client,
        config=SimpleNamespace(cos_bucket="mock-backup-bucket", cos_region="ap-nanjing"),
    )
    return BackupDestination(storage), client


def fake_export(pg_bin, root, **kwargs):
    assert kwargs == {"cos_assets": True, "online_immutable_assets": True, "verify_restore": False}
    backup = root / f"restore-drill-{uuid4().hex}.dump"
    names = {
        backup.name: b"synthetic dump, not a restore proof",
        backup.name + ".assets.tar.gz": b"synthetic archive",
        backup.name + ".manifest.json": b'{"formatVersion":2,"assets":{}}',
    }
    for name, data in names.items():
        (root / name).write_bytes(data)
    (root / (backup.name + ".sha256")).write_text(
        "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in names.items()),
        encoding="utf-8",
    )
    return {"status": "exported", "restoreVerified": False, "backup": str(backup)}


def test_daily_reads_back_every_file_and_weekly_downloads_cloud_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_jobs, "restore_drill", fake_export)
    target, cloud = destination()
    result = daily_backup(tmp_path, target, now=NOW)
    assert result["remoteFilesVerified"] == 4 and not result["restoreVerified"]
    receipt_path = next(tmp_path.glob("completed-*.json"))
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] == "uploaded-verified" and len(cloud.objects) == 4
    # 删除本地文件也能从COS下载恢复，不能重用本地内容冒充远端下载验证。
    for name in receipt["files"]:
        (tmp_path / name).unlink()
    verified = []

    def restore(path, pg_bin):
        verified.append(bundle_files(path))
        return {"status": "passed", "restoreVerified": True}

    monkeypatch.setattr(backup_jobs, "restore_saved_bundle", restore)
    assert weekly_restore(tmp_path, target)["restoreVerified"]
    assert verified == [receipt["files"]] and not list(tmp_path.glob("itemtalk_weekly_*"))
    assert len(cloud.calls) == 4  # 恢复期间只下载，绝不改写COS对象。


@pytest.mark.parametrize("failure", ["second-upload", "remote-checksum"])
def test_upload_or_readback_failure_has_no_completion_receipt(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(backup_jobs, "restore_drill", fake_export)
    target, cloud = destination()
    if failure == "second-upload":
        cloud.fail_put = 2
    else:
        cloud.corrupt = True
    with pytest.raises((DomainError, ValueError)):
        daily_backup(tmp_path, target, now=NOW)
    assert not list(tmp_path.glob("completed-*.json"))
    assert list(tmp_path.glob("*.dump"))  # 失败保留唯一副本，无云删除接口。


def test_remote_corruption_stops_weekly_before_database_creation(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_jobs, "restore_drill", fake_export)
    target, cloud = destination()
    daily_backup(tmp_path, target, now=NOW)
    cloud.corrupt = True
    restore = Mock()
    monkeypatch.setattr(backup_jobs, "restore_saved_bundle", restore)
    with pytest.raises(ValueError, match="checksum"):
        weekly_restore(tmp_path, target)
    restore.assert_not_called()
    assert not list(tmp_path.glob("weekly-*.json")) and not list(tmp_path.glob("itemtalk_weekly_*"))


def test_retention_seven_days_only_completed_local_bundles(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_jobs, "restore_drill", fake_export)
    target, cloud = destination()
    daily_backup(tmp_path, target, now=NOW)
    original = dict(cloud.objects)
    unknown = tmp_path / "old-maintenance.dump"
    unknown.write_bytes(b"unique old production restore point")
    assert prune_local(tmp_path, NOW + timedelta(days=7)) == 0
    assert prune_local(tmp_path, NOW + timedelta(days=7, seconds=1)) == 1
    assert list(tmp_path.glob("*.dump*")) == [unknown] and cloud.objects == original
    assert not list(tmp_path.glob("completed-*.json"))


def test_retention_tampered_bundle_aborts_before_any_file_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_jobs, "restore_drill", fake_export)
    target, _ = destination()
    daily_backup(tmp_path, target, now=NOW)
    dump = next(tmp_path.glob("*.dump"))
    dump.write_bytes(b"changed")
    before = set(tmp_path.iterdir())
    with pytest.raises(ValueError, match="changed"):
        prune_local(tmp_path, NOW + timedelta(days=8))
    assert set(tmp_path.iterdir()) == before


@pytest.mark.parametrize(
    "name", ["../outside.dump", "other.dump", "restore-drill-" + "a" * 32 + ".dump"]
)
def test_untrusted_receipt_paths_cannot_restore_or_delete(tmp_path, monkeypatch, name):
    monkeypatch.setattr(backup_jobs, "restore_drill", fake_export)
    target, _ = destination()
    daily_backup(tmp_path, target, now=NOW)
    path = next(tmp_path.glob("completed-*.json"))
    receipt = json.loads(path.read_text())
    receipt["files"][name] = next(iter(receipt["files"].values()))
    path.write_text(json.dumps(receipt))
    before = set(tmp_path.iterdir())
    with pytest.raises(ValueError, match="receipt"):
        prune_local(tmp_path, NOW + timedelta(days=8))
    with pytest.raises(ValueError, match="receipt"):
        weekly_restore(tmp_path, target)
    assert set(tmp_path.iterdir()) == before


def test_backup_credentials_never_fall_back_to_application_bucket(monkeypatch):
    for name in (
        "DB_BACKUP_COS_BUCKET",
        "DB_BACKUP_COS_REGION",
        "DB_BACKUP_COS_SECRET_ID",
        "DB_BACKUP_COS_SECRET_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("COS_BUCKET", "application-bucket")
    with pytest.raises(ValueError, match="Dedicated"):
        configured_destination()
    for name, value in {
        "DB_BACKUP_COS_BUCKET": "backup-bucket",
        "DB_BACKUP_COS_REGION": "ap-beijing",
        "DB_BACKUP_COS_SECRET_ID": "mock-id",
        "DB_BACKUP_COS_SECRET_KEY": "mock-key",
        "BACKUP_SOURCE_REGION": "ap-beijing",
    }.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match="different region"):
        configured_destination()


def test_cli_requires_real_opt_in_and_persists_failure_event(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["backup", "daily", "--output-directory", str(tmp_path)])
    with pytest.raises(SystemExit) as error:
        backup_jobs.main()
    assert error.value.code == 2 and not list(tmp_path.iterdir())
    monkeypatch.setattr(
        "sys.argv", ["backup", "weekly", "--real-cos", "--output-directory", str(tmp_path)]
    )
    monkeypatch.setattr(backup_jobs, "configured_destination", lambda: destination()[0])
    with pytest.raises(SystemExit) as error:
        backup_jobs.main()
    assert error.value.code == 1
    event = json.loads(next((tmp_path / "alerts").glob("event-*.json")).read_text())
    assert event["kind"] == "weekly-failed" and event["state"] == "pending"
    assert not event["notificationVerified"]
    assert "mock-key" not in capsys.readouterr().err


@pytest.mark.postgres
def test_mock_cloud_roundtrip_restores_real_pg_snapshot_without_changing_source(
    client, tmp_path, monkeypatch
):
    source_assets, args = install_cos_objects(client, monkeypatch)
    target, cloud = destination()
    with psycopg.connect(**args) as source:
        before = manifest(source)
    result = daily_backup(tmp_path, target, pg_bin(), now=NOW)
    assert not result["restoreVerified"]
    with psycopg.connect(**args) as source:
        assert manifest(source) == before
        assert (
            source.execute(
                "SELECT count(*) FROM pg_database WHERE datname LIKE 'itemtalk_restore_%'"
            ).fetchone()[0]
            == 0
        )
        source.execute("UPDATE users SET display_name='new writes after backup'")
    with psycopg.connect(**args) as source:
        after_write = manifest(source)
    result = weekly_restore(tmp_path, target, pg_bin())
    assert result == {"status": "passed", "restoreVerified": True, "target": "isolated-database"}
    assert len(cloud.calls) == 4 and source_assets.objects
    with psycopg.connect(**args) as source:
        assert manifest(source) == after_write
        assert (
            source.execute(
                "SELECT count(*) FROM pg_database WHERE datname LIKE 'itemtalk_restore_%'"
            ).fetchone()[0]
            == 0
        )


def test_explicit_backup_environment_whitelists_fields_and_clears_cache(tmp_path, monkeypatch):
    config = {
        "DB_HOST": "test.invalid",
        "DB_NAME": "itemtalk_acceptance_test",
        "DB_PASSWORD": None,
        "DB_BACKUP_COS_BUCKET": "mock-backup-bucket",
        "PYTHONPATH": "untrusted-config-path",
    }
    for name in (*config, "ENVIRONMENT", "MODEL_PROVIDER"):
        monkeypatch.setenv(name, "before-test")
    observed = {}
    monkeypatch.setattr(backup_jobs, "configuration", lambda path: config)

    def destination_stub():
        import os

        observed.update(
            {name: os.environ.get(name) for name in (*config, "ENVIRONMENT", "MODEL_PROVIDER")}
        )
        return None

    monkeypatch.setattr(backup_jobs, "configured_destination", destination_stub)
    monkeypatch.setattr(backup_jobs, "weekly_restore", lambda *args: {"status": "mock-only"})
    monkeypatch.setattr(
        "sys.argv", ["backup", "weekly", "--real-cos", "--env-file", str(tmp_path / "explicit.env")]
    )
    backup_jobs.main()
    assert observed["DB_NAME"] == "itemtalk_acceptance_test" and observed["DB_PASSWORD"] == ""
    assert observed["PYTHONPATH"] == "before-test"
    assert observed["ENVIRONMENT"] == "backup" and observed["MODEL_PROVIDER"] == "mock"
