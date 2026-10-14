"""迁移仅复制已核对字节；同键冲突、并发创建、公开桶及版本桶都不能被默默覆盖。"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from qcloud_cos.cos_comm import mapped
from qcloud_cos.cos_exception import CosClientError, CosServiceError

from app.backup_assets import file_manifest
from app.config import settings
from app.errors import DomainError
from app.storage_transfer import CosDestination, copy_assets, main, transfer
from tests.conftest import register_login
from tests.test_private_cleanup import upload_image


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    for scope, character in (("private", "a"), ("public", "b"), ("temporary", "c")):
        path = root / scope / (character * 32)
        path.parent.mkdir(parents=True)
        path.write_bytes((scope + " bytes").encode())
    return root


class MemoryDestination:
    def __init__(self, objects=None):
        self.objects = objects or {}
        self.calls = []

    def validate(self):
        self.calls.append("validate")

    def read(self, key, maximum):
        self.calls.append(("read", key))
        if key not in self.objects:
            raise FileNotFoundError
        return self.objects[key]

    def create(self, key, data):
        self.calls.append(("create", key))
        assert key not in self.objects
        self.objects[key] = data


def test_default_plan_never_contacts_or_writes_destination(source):
    target = MemoryDestination()
    before = file_manifest(source)
    result = copy_assets(source, before, target, writers_paused=True)
    assert result == {"status": "planned", "files": 3, "copied": 0, "reused": 0}
    assert target.calls == [] and file_manifest(source) == before
    with pytest.raises(ValueError, match="paused"):
        copy_assets(source, before, target, apply=True)


def test_copy_and_restart_verify_every_object_without_changing_source(source):
    target = MemoryDestination()
    before = file_manifest(source)
    result = copy_assets(source, before, target, apply=True, writers_paused=True)
    assert result["status"] == "verified" and result["copied"] == 3
    assert all((source / key).read_bytes() == value for key, value in target.objects.items())
    resumed = copy_assets(source, before, target, apply=True, writers_paused=True)
    assert resumed["copied"] == 0 and resumed["reused"] == 3
    assert file_manifest(source) == before


def test_existing_conflict_is_never_overwritten(source):
    expected = file_manifest(source)
    key = next(iter(expected))
    target = MemoryDestination({key: b"unrelated existing object"})
    with pytest.raises(ValueError, match="differs"):
        copy_assets(source, expected, target, apply=True, writers_paused=True)
    assert not any(c[0] == "create" for c in target.calls if isinstance(c, tuple))
    assert target.objects[key] == b"unrelated existing object"
    assert file_manifest(source) == expected


def test_changed_source_is_rejected_before_any_destination_call(source):
    expected = file_manifest(source)
    (source / next(iter(expected))).write_bytes(b"changed")
    target = MemoryDestination()
    with pytest.raises(ValueError, match="manifest"):
        copy_assets(source, expected, target, apply=True, writers_paused=True)
    assert target.calls == []


def test_report_inside_source_is_rejected_without_creating_file(source, monkeypatch):
    before = file_manifest(source)
    report = source / "migration.json"
    monkeypatch.setattr(
        "sys.argv",
        ["storage_transfer", "--assets-directory", str(source), "--report", str(report)],
    )
    monkeypatch.setattr(
        "app.storage_transfer.transfer", lambda *a, **kw: pytest.fail("No migration may start")
    )
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1 and not report.exists()
    assert file_manifest(source) == before


def destination(client=None):
    client = client or Mock()
    client.get_bucket_versioning.return_value = {}
    client.get_bucket_acl.return_value = {
        "Owner": {"ID": "owner"},
        "Grant": [{"Grantee": {"ID": "owner"}, "Permission": "FULL_CONTROL"}],
    }
    client.get_bucket_policy.side_effect = CosServiceError(
        "GET", {"code": "NoSuchBucketPolicy", "message": "private detail"}, 404
    )
    return CosDestination(
        SimpleNamespace(client=client, config=SimpleNamespace(cos_bucket="test-bucket"))
    )


def test_conditional_private_create_passes_real_sdk_header_mapping():
    target = destination()
    target.validate()
    target.create("private/" + "a" * 32, b"data")
    request = target.storage.client.put_object.call_args.kwargs
    headers = mapped(
        {k: v for k, v in request.items() if k not in {"Bucket", "Key", "Body", "EnableMD5"}}
    )
    assert headers["x-cos-forbid-overwrite"] == "true" and headers["x-cos-acl"] == "private"
    assert request["EnableMD5"] is True


@pytest.mark.parametrize("status", ["Enabled", "Suspended", "unknown"])
def test_versioned_or_unknown_destination_is_not_modified(status):
    target = destination()
    target.storage.client.get_bucket_versioning.return_value = {"Status": status}
    with pytest.raises(ValueError):
        target.validate()
    target.storage.client.put_object.assert_not_called()


def test_public_grants_and_allow_policies_require_separate_review():
    target = destination()
    target.storage.client.get_bucket_acl.return_value["Grant"].append(
        {"Grantee": {"URI": "AllUsers"}}
    )
    with pytest.raises(ValueError, match="private"):
        target.validate()
    target = destination()
    target.storage.client.get_bucket_policy.side_effect = None
    target.storage.client.get_bucket_policy.return_value = {
        "statement": [{"effect": "allow", "principal": "*"}]
    }
    with pytest.raises(ValueError, match="policies"):
        target.validate()
    target.storage.client.put_object.assert_not_called()
    target.storage.client.get_bucket_policy.return_value = {
        "statement": [{"effect": "deny", "principal": "*"}]
    }
    target.validate()


def test_racing_same_name_is_rejected_by_server_without_exception_details():
    target = destination()
    target.storage.client.put_object.side_effect = CosServiceError(
        "PUT", {"code": "ObjectAlreadyExists", "message": "private detail"}, 409
    )
    with pytest.raises(DomainError) as error:
        target.create("private/" + "a" * 32, b"data")
    assert error.value.status == 409 and "private detail" not in str(error.value)
    target.storage.client.get_bucket_acl.side_effect = CosClientError("sensitive credential detail")
    with pytest.raises(DomainError) as error:
        target.validate()
    assert error.value.status == 503 and "sensitive" not in str(error.value)


@pytest.mark.postgres
def test_real_database_dry_run_preserves_references_without_cos(client, monkeypatch):
    register_login(client)
    upload_image(client)
    root = settings().storage_directory
    before = file_manifest(root)
    monkeypatch.setattr(
        "app.storage_transfer.Storage", lambda: pytest.fail("Dry run must not use COS")
    )
    result = transfer(root, writers_paused=True)
    assert result["status"] == "planned" and result["files"] == 2
    assert result["referencedAssets"] == 1 and len(result["inventorySha256"]) == 64
    assert file_manifest(root) == before
