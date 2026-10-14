"""COS 协议分支回归；预签名使用真实 SDK，网络读写通过受控对象 mock。"""

import io
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from PIL import Image
from qcloud_cos.cos_exception import CosClientError, CosServiceError

from app.config import settings
from app.db import one, transaction
from app.errors import DomainError
from app.storage import Storage
from tests.conftest import register_login
from tests.test_api import post


@pytest.fixture
def cos_settings(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "cos")
    monkeypatch.setenv("COS_SECRET_ID", "test-only-id")
    monkeypatch.setenv("COS_SECRET_KEY", "test-only-key")
    monkeypatch.setenv("COS_BUCKET", "test-bucket-1234567890")
    settings.cache_clear()
    yield
    settings.cache_clear()


def test_real_sdk_signs_only_temporary_put_and_confirmed_get(cos_settings):
    storage = Storage()
    asset = {
        "id": uuid4(),
        "object_key": "temporary/unit-test",
        "expires_at": datetime.now(UTC) + timedelta(minutes=15),
    }
    signed, _expires = storage.url(asset, write=True)
    parts = urlsplit(signed)
    assert parts.scheme == "https"
    assert parts.hostname == "test-bucket-1234567890.cos.ap-beijing.myqcloud.com"
    assert parts.path == "/temporary/unit-test"
    assert "q-signature" in parse_qs(parts.query)
    read, _expires = storage.url({**asset, "object_key": "private/immutable"})
    assert urlsplit(read).path == "/private/immutable"
    assert read != signed


def test_cos_read_caps_bytes_and_closes_stream(cos_settings):
    storage = Storage()
    stream = io.BytesIO(b"123456")
    storage.client = Mock()
    storage.client.get_object.return_value = {"Body": Mock(get_raw_stream=lambda: stream)}
    with pytest.raises(DomainError) as error:
        storage.read("temporary/unit-test", maximum=3)
    assert error.value.status == 413 and stream.closed


def test_reissued_put_signature_cannot_outlive_original_upload(cos_settings):
    original_expiry = datetime.now(UTC) + timedelta(seconds=7)
    signed, reported_expiry = Storage().url(
        {"id": uuid4(), "object_key": "temporary/unit-test", "expires_at": original_expiry},
        write=True,
    )
    start, end = parse_qs(urlsplit(signed).query)["q-sign-time"][0].split(";")
    # SDK 将起始时间回拨 60 秒容忍时钟差；真正的失效边界是结束时间。
    assert int(start) < int(end) <= int(original_expiry.timestamp())
    assert datetime.fromisoformat(reported_expiry) <= original_expiry


@pytest.mark.parametrize("operation", ["read", "put", "delete"])
def test_cos_failures_are_redacted(cos_settings, operation):
    storage = Storage()
    storage.client = Mock()
    for method in ("get_object", "put_object", "delete_object"):
        getattr(storage.client, method).side_effect = CosClientError("sensitive-sdk-message")
    with pytest.raises(DomainError) as error:
        if operation == "put":
            storage.put("private/unit-test", b"data", "image/jpeg")
        else:
            getattr(storage, operation)("private/unit-test")
    assert error.value.status == 503 and "sensitive" not in str(error.value)


@pytest.mark.postgres
def test_cos_upload_missing_object_and_immutable_confirmation(client, cos_settings, monkeypatch):
    register_login(client)
    output = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(output, "PNG")
    raw = output.getvalue()
    storage = Storage()
    signed = storage.client  # 签名算法仍使用 SDK；仅替换网络对象操作。
    objects = {}

    def get_object(**kwargs):
        if kwargs["Key"] not in objects:
            raise CosServiceError("GET", {"code": "NoSuchKey", "message": "private-detail"}, 404)
        return {"Body": Mock(get_raw_stream=lambda: io.BytesIO(objects[kwargs["Key"]]))}

    def put_object(**kwargs):
        objects[kwargs["Key"]] = kwargs["Body"]

    storage.client = Mock(
        get_object=get_object, put_object=put_object, get_presigned_url=signed.get_presigned_url
    )
    monkeypatch.setattr("app.services.uploads.Storage", lambda: storage)
    request = {
        "filename": "device.png",
        "mimeType": "image/png",
        "sizeBytes": len(raw),
        "kind": "product_photo",
    }
    response = post(client, "/assets/presign", request)
    assert response.status_code == 201
    asset_id = response.json()["assetId"]
    missing = post(client, f"/assets/{asset_id}/confirm", {})
    assert missing.status_code == 409 and "private-detail" not in missing.text
    with transaction() as connection:
        pending = one(connection, "SELECT * FROM assets WHERE id=:id", id=asset_id)
    objects[pending["object_key"]] = raw
    confirmed = post(client, f"/assets/{asset_id}/confirm", {})
    assert confirmed.status_code == 200 and confirmed.json()["mimeType"] == "image/jpeg"
    with transaction() as connection:
        asset = one(connection, "SELECT * FROM assets WHERE id=:id", id=asset_id)
    assert asset["object_key"].startswith("private/")
    assert asset["object_key"] != pending["object_key"]
    immutable = objects[asset["object_key"]]
    # 旧 PUT 签名只作用于临时键；重复确认不能重新读取其被替换的内容。
    objects[pending["object_key"]] = b"later overwrite"
    assert post(client, f"/assets/{asset_id}/confirm", {}).status_code == 200
    assert objects[asset["object_key"]] == immutable
