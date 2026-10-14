"""上传资产服务：临时签名只写临时对象，确认后切换至不可变对象键。"""

from datetime import UTC, datetime

from app.db import execute, one
from app.errors import DomainError
from app.services.products import new_job, owned
from app.storage import Storage, immutable_key, sanitize_image, sha256


def asset_view(asset):
    url, expiry = (None, None)
    if asset["status"] == "available" and not asset["deleted_at"]:
        url, expiry = Storage().url(asset)
    return {
        "id": str(asset["id"]),
        "mimeType": asset["mime_type"],
        "sizeBytes": asset["size_bytes"],
        "kind": asset["kind"],
        "status": asset["status"],
        "readUrl": url,
        "readUrlExpiresAt": expiry,
    }


def presign_view(asset):
    if asset["status"] != "pending" or asset["expires_at"] < datetime.now(UTC):
        raise DomainError(409, "RESOURCE_CONFLICT", "上传已确认或过期，请创建新上传")
    url, expiry = Storage().url(asset, write=True)
    return {"assetId": str(asset["id"]), "uploadUrl": url, "expiresAt": expiry}


def create_upload(connection, request, user):
    limit = 10 * 1024 * 1024 if request.mimeType.startswith("image/") else 25 * 1024 * 1024
    if request.sizeBytes > limit:
        raise DomainError(413, "FILE_TOO_LARGE", "文件超过大小限制")
    asset = one(
        connection,
        (
            "INSERT INTO "
            "assets(owner_user_id,scope,object_key,mime_type,size_bytes,kind,expires_at)"
            " VALUES (:u,'private',:key,:mime,:size,:kind,now()+interval '15 "
            "minutes') RETURNING *"
        ),
        u=user,
        key=immutable_key("temporary"),
        mime=request.mimeType,
        size=request.sizeBytes,
        kind=request.kind,
    )
    from app.services.lifecycle import track_upload

    track_upload(connection, asset)
    # 签名过期后清理临时键，避免删除后仍被有效 PUT 签名重新创建。
    cleanup = new_job(
        connection,
        "asset_cleanup",
        {"assetId": str(asset["id"]), "temporaryKey": asset["object_key"]},
        user=user,
        key=f"cleanup:{asset['id']}",
    )
    execute(
        connection,
        ("UPDATE jobs SET available_at=now()+interval '25 minutes' WHERE id=:id"),
        id=cleanup["id"],
    )
    return asset


def confirm_upload(connection, asset_id, user):
    owned(connection, "assets", asset_id, user)
    asset = one(connection, "SELECT * FROM assets WHERE id=:id FOR UPDATE", id=asset_id)
    if asset["status"] == "available":
        return asset_view(asset)
    if asset["status"] != "pending" or asset["expires_at"] < datetime.now(UTC):
        raise DomainError(409, "RESOURCE_CONFLICT", "上传已过期")
    store = Storage()
    try:
        data = store.read(asset["object_key"], 25 * 1024 * 1024)
    except FileNotFoundError:
        raise DomainError(409, "RESOURCE_CONFLICT", "上传尚未完成") from None
    if len(data) != asset["size_bytes"]:
        raise DomainError(422, "VALIDATION_ERROR", "实际文件大小不匹配")
    mime = asset["mime_type"]
    if mime.startswith("image/"):
        data, mime = sanitize_image(data, mime)
    else:
        valid = {
            "audio/wav": data.startswith(b"RIFF") and data[8:12] == b"WAVE",
            "audio/mpeg": data.startswith(b"ID3")
            or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"),
            "audio/webm": data.startswith(b"\x1a\x45\xdf\xa3"),
            "audio/mp4": data[4:8] == b"ftyp",
        }
        if not valid.get(mime):
            raise DomainError(422, "FILE_TYPE_NOT_ALLOWED", "音频格式无效")
    key = immutable_key("private")
    from app.services.lifecycle import reserve_object

    reserve_object(asset, key)
    store.put(key, data, mime)
    updated = one(
        connection,
        (
            "UPDATE assets SET "
            "object_key=:key,sha256=:hash,mime_type=:mime,size_bytes=:size, "
            "status='available',expires_at=NULL WHERE id=:id RETURNING *"
        ),
        key=key,
        hash=sha256(data),
        mime=mime,
        size=len(data),
        id=asset_id,
    )
    # 临时键清理由定时 Worker 完成；旧 PUT 签名永远不能覆盖确认后的对象。
    return asset_view(updated)
