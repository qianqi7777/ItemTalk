"""离线验证模板权限边界及SDK参数；不代替CAM服务端验证，不访问COS。"""

import fnmatch
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import backup_jobs
from app.backup_jobs import BackupDestination, configured_destination

ROOT = Path(__file__).resolve().parents[2]
BUCKET_RESOURCE = "qcs::cos:ap-nanjing:uid/1314964135:itemtalk-backup-1314964135/"


def policy():
    return json.loads((ROOT / "infra/cos-backup-policy.example.json").read_text())


def mock_allows(action, resource, headers=None):
    """只模拟此模板使用的action/resource/string_equal子集，不声称是完整CAM引擎。"""
    allow = False
    for rule in policy()["statement"]:
        if not any(fnmatch.fnmatchcase(action, name) for name in rule["action"]):
            continue
        if not any(fnmatch.fnmatchcase(resource, name) for name in rule["resource"]):
            continue
        conditions = rule.get("condition", {}).get("string_equal", {})
        if any((headers or {}).get(key) != value for key, value in conditions.items()):
            continue
        if rule["effect"] == "deny":
            return False
        allow = True
    return allow


@pytest.mark.parametrize("action", ["GetBucketACL", "GetBucketPolicy", "GetBucketVersioning"])
def test_only_required_bucket_read_checks_are_allowed(action):
    assert mock_allows("name/cos:" + action, BUCKET_RESOURCE + "*")
    assert not mock_allows(
        "name/cos:" + action, BUCKET_RESOURCE.replace("ap-nanjing", "ap-beijing") + "*"
    )


@pytest.mark.parametrize(
    "action",
    [
        "DeleteObject",
        "DeleteMultipleObjects",
        "DeleteBucket",
        "PutBucketLifecycle",
        "PutBucketACL",
        "PutBucketPolicy",
        "PutBucketVersioning",
        "PutObjectACL",
        "GetService",
        "GetBucket",
    ],
)
def test_destructive_and_unrelated_actions_are_not_authorized(action):
    assert not mock_allows(
        "name/cos:" + action, BUCKET_RESOURCE + "itemtalk/postgresql/sample.dump"
    )


def test_object_read_scope_and_upload_conditions():
    key = BUCKET_RESOURCE + "itemtalk/postgresql/sample.dump"
    assert mock_allows("name/cos:GetObject", key)
    headers = {"cos:x-cos-acl": "private", "cos:x-cos-forbid-overwrite": "true"}
    assert mock_allows("name/cos:PutObject", key, headers)
    assert not mock_allows("name/cos:PutObject", key)
    assert not mock_allows("name/cos:PutObject", key, {**headers, "cos:x-cos-acl": "public-read"})
    assert not mock_allows(
        "name/cos:PutObject", key, {**headers, "cos:x-cos-forbid-overwrite": "false"}
    )
    for other in ("private/sample", "other/sample", "itemtalk/postgresql-other/sample"):
        assert not mock_allows("name/cos:GetObject", BUCKET_RESOURCE + other)
        assert not mock_allows("name/cos:PutObject", BUCKET_RESOURCE + other, headers)


def test_upload_adapter_satisfies_policy_without_extra_cloud_operations(tmp_path):
    path = tmp_path / "sample.dump"
    path.write_bytes(b"synthetic backup")
    calls = []

    class Client:
        def put_object(self, **kwargs):
            assert kwargs["Bucket"] == "itemtalk-backup-1314964135"
            headers = {
                "cos:x-cos-acl": kwargs["ACL"],
                "cos:x-cos-forbid-overwrite": kwargs["Metadata"]["x-cos-forbid-overwrite"],
            }
            assert mock_allows("name/cos:PutObject", BUCKET_RESOURCE + kwargs["Key"], headers)
            calls.append(kwargs["Body"].read())

    storage = SimpleNamespace(
        client=Client(), config=SimpleNamespace(cos_bucket="itemtalk-backup-1314964135")
    )
    BackupDestination(storage).create_file("itemtalk/postgresql/synthetic.dump", path)
    assert calls == [b"synthetic backup"]


def test_independent_credentials_override_app_fields_without_network(monkeypatch):
    for name, value in {
        "DB_BACKUP_COS_BUCKET": "itemtalk-backup-1314964135",
        "DB_BACKUP_COS_REGION": "ap-nanjing",
        "DB_BACKUP_COS_SECRET_ID": "mock-backup-id",
        "DB_BACKUP_COS_SECRET_KEY": "mock-backup-key",
        "BACKUP_SOURCE_REGION": "ap-beijing",
    }.items():
        monkeypatch.setenv(name, value)
    from app.config import Settings

    config = Settings(
        _env_file=None,
        environment="test",
        model_provider="mock",
        cos_bucket="application-bucket",
        cos_secret_id="mock-app-id",
        cos_secret_key="mock-app-key",
    )
    monkeypatch.setattr(backup_jobs, "settings", lambda: config)
    captured = []

    def storage_factory(value, **kwargs):
        captured.append((value, kwargs))
        return SimpleNamespace(client=object(), config=value)

    monkeypatch.setattr(backup_jobs, "Storage", storage_factory)
    configured_destination()
    destination, options = captured[0]
    assert destination.cos_bucket == "itemtalk-backup-1314964135"
    assert destination.cos_secret_id.get_secret_value() == "mock-backup-id"
    assert destination.cos_secret_key.get_secret_value() == "mock-backup-key"
    assert config.cos_bucket == "application-bucket" and options == {
        "cos_retries": 0,
        "cos_timeout": 60,
    }
