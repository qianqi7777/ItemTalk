"""真实SDK签名+受控COS边界；验证私有性、归属保护、恢复和失败脱敏。"""

import io
import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from qcloud_cos import CosConfig, CosS3Client
from qcloud_cos.cos_exception import CosServiceError
from requests import Timeout

from app.config import Settings, settings
from app.cos_probe import OWNER_HEADER, main, probe
from app.errors import DomainError
from app.storage import Storage


class ObjectBoundary:
    def __init__(self):
        self.objects = {}
        self.deleted = []
        self.public = False
        self.wrong_cors = False
        self.replace_owner = False
        self.streams = []

    def get_bucket_versioning(self, **kwargs):
        return {}

    def get_bucket_acl(self, **kwargs):
        return {"Owner": {"ID": "owner"}, "Grant": [{"Grantee": {"ID": "owner"}}]}

    def get_bucket_policy(self, **kwargs):
        return None

    def get_object(self, *, Key, **kwargs):
        if Key not in self.objects:
            raise CosServiceError("GET", {"code": "NoSuchKey", "message": "private detail"}, 404)
        data, nonce = self.objects[Key]
        stream = io.BytesIO(data)
        self.streams.append(stream)
        return {OWNER_HEADER: nonce, "Body": SimpleNamespace(get_raw_stream=lambda: stream)}

    def put_object(self, *, Key, Body, Metadata, **kwargs):
        if Key in self.objects and Metadata.get("x-cos-forbid-overwrite") == "true":
            raise CosServiceError("PUT", {"code": "ObjectAlreadyExists"}, 409)
        self.objects[Key] = (Body, Metadata[OWNER_HEADER])

    def delete_object(self, *, Key, **kwargs):
        self.deleted.append(Key)
        self.objects.pop(Key, None)

    def http(self, request):
        parts = urlsplit(str(request.url))
        key = parts.path.lstrip("/")
        if request.method == "OPTIONS":
            return httpx.Response(
                200,
                headers={
                    "Access-Control-Allow-Origin": "https://other.invalid"
                    if self.wrong_cors
                    else request.headers["origin"],
                    "Access-Control-Allow-Methods": "GET,PUT",
                    "Access-Control-Allow-Headers": "content-type",
                },
            )
        if request.method == "PUT":
            signed_headers = parse_qs(parts.query)["q-header-list"][0].split(";")
            assert OWNER_HEADER in signed_headers and "content-type" in signed_headers
            if key in self.objects and request.headers.get("x-cos-forbid-overwrite") == "true":
                return httpx.Response(409)
            self.objects[key] = (request.read(), request.headers[OWNER_HEADER])
            return httpx.Response(200)
        if not parts.query:
            if self.replace_owner:
                self.objects[key] = (b"other owner object", "another-run")
            return httpx.Response(200 if self.public else 403)
        return httpx.Response(200, content=self.objects[key][0])


def boundary():
    config = Settings(
        cos_bucket="probe-bucket-1234567890",
        cos_secret_id="test-id",
        cos_secret_key="test-key",
        storage_backend="cos",
    )
    storage = Storage(config, cos_retries=0, cos_timeout=15)
    signing = storage.client
    objects = ObjectBoundary()
    objects.get_presigned_url = signing.get_presigned_url
    storage.client = objects
    return (
        storage,
        objects,
        httpx.Client(transport=httpx.MockTransport(objects.http), follow_redirects=False),
    )


def test_probe_validates_synthetic_bytes_and_recovers_own_archived_object(tmp_path):
    storage, objects, client = boundary()
    plans = []
    with client:
        result = probe(storage, client, "https://49.233.65.195", tmp_path, plans.append)
    assert result["status"] == "passed" and not result["cleanupIncomplete"]
    assert result["checks"] == [
        "bucket_privacy",
        "cors",
        "signed_upload",
        "immutable_copy",
        "private_read",
        "archive_recovery",
    ]
    assert result["modelCalls"] == 0 and result["databaseVerified"] is False
    assert objects.objects == {} and all(stream.closed for stream in objects.streams)
    assert len(objects.deleted) == 3 and plans[0]["probeRunId"] == result["probeRunId"]
    assert plans[0]["status"] == "running" and len(plans[0]["candidateKeys"]) == 2
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("failure", ["public", "wrong_cors"])
def test_bad_privacy_or_cors_cannot_pass_and_own_objects_are_cleaned(tmp_path, failure):
    storage, objects, client = boundary()
    setattr(objects, failure, True)
    with client:
        result = probe(storage, client, "https://49.233.65.195", tmp_path)
    assert result["status"] == "failed" and objects.objects == {}
    assert result["failedStage"] == ("cors" if failure == "wrong_cors" else "private_read")


def test_foreign_nonce_prevents_cleanup_even_after_probe_failure(tmp_path):
    storage, objects, client = boundary()
    objects.public = objects.replace_owner = True
    with client:
        result = probe(storage, client, "https://49.233.65.195", tmp_path)
    assert result["status"] == "failed" and result["cleanupIncomplete"]
    assert len(result["remainingCandidateKeys"]) == 1
    key = result["remainingCandidateKeys"][0]
    assert objects.objects == {key: (b"other owner object", "another-run")}
    assert key not in objects.deleted


def test_storage_sdk_diagnostics_do_not_expose_raw_requests(caplog):
    storage, _objects, _client = boundary()
    session = Mock()
    session.get.side_effect = Timeout("sensitive-sdk-detail")
    storage.client = CosS3Client(
        CosConfig(Region="ap-beijing", SecretId="test-id", SecretKey="test-key", Scheme="https"),
        retry=0,
        session=session,
    )
    with caplog.at_level(logging.DEBUG), pytest.raises(DomainError):
        storage.read("private/test-only")
    assert "sensitive" not in caplog.text
    assert "probe-bucket" not in caplog.text and session.get.call_count == 1


def test_missing_config_stops_before_storage_construction_and_report_is_exclusive(
    tmp_path, monkeypatch, capsys
):
    for name in ("COS_BUCKET", "COS_SECRET_ID", "COS_SECRET_KEY"):
        monkeypatch.setenv(name, "")
    settings.cache_clear()
    report = tmp_path / "report.json"
    monkeypatch.setattr("sys.argv", ["cos_probe", "--real-cos", "--report", str(report)])
    monkeypatch.setattr(
        "app.cos_probe.Storage", lambda *a, **kw: pytest.fail("No network object may be created")
    )
    with pytest.raises(SystemExit) as error:
        main()
    assert (
        error.value.code == 1 and json.loads(report.read_text())["failedStage"] == "configuration"
    )
    before = report.read_bytes()
    with pytest.raises(SystemExit):
        main()
    assert report.read_bytes() == before
    assert "signature" not in capsys.readouterr().out


def test_real_flag_is_required_before_report_or_provider_access(tmp_path, monkeypatch):
    report = tmp_path / "report.json"
    monkeypatch.setattr("sys.argv", ["cos_probe", "--report", str(report)])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2 and not report.exists()


def test_complete_cli_persists_plan_before_creation_and_preserves_local_configuration(
    tmp_path, monkeypatch, capsys
):
    storage, objects, client = boundary()
    config = storage.config.model_copy(update={"storage_backend": "local"})
    report = tmp_path / "report.json"
    write = objects.put_object
    witnessed = []

    def guarded_create(**kwargs):
        state = json.loads(report.read_text())
        assert state["status"] == "running" and kwargs["Key"] in state["candidateKeys"]
        assert state["probeRunId"] == kwargs["Metadata"][OWNER_HEADER]
        witnessed.append(kwargs["Key"])
        return write(**kwargs)

    def make_storage(explicit, **kwargs):
        assert explicit.storage_backend == "cos" and config.storage_backend == "local"
        assert kwargs == {"cos_retries": 0, "cos_timeout": 15}
        return storage

    monkeypatch.setattr(objects, "put_object", guarded_create)
    monkeypatch.setattr("app.cos_probe.settings", lambda: config)
    monkeypatch.setattr("app.cos_probe.Storage", make_storage)
    monkeypatch.setattr("app.cos_probe.httpx.Client", lambda **kwargs: client)
    monkeypatch.setattr("sys.argv", ["cos_probe", "--real-cos", "--report", str(report)])
    main()
    result = json.loads(report.read_text())
    assert result["status"] == "passed" and len(witnessed) == 3
    assert objects.objects == {} and "q-signature" not in capsys.readouterr().out
