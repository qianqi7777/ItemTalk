"""独立COS供应商验收：只写本轮合成对象，按nonce验证归属后清理，不接数据库/模型。"""

import argparse
import io
import json
import logging
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
from PIL import Image

from app.backup_assets import archive_assets, file_manifest, restore_assets
from app.config import settings
from app.storage import Storage, sanitize_image, sha256
from app.storage_transfer import CosDestination

MAX_BYTES = 4096
OWNER_HEADER = "x-cos-meta-itemtalk-probe"


def response(client, method, url, **kwargs):
    """签名地址不进入报告；拒绝重定向，所有响应读取均有界。"""
    with client.stream(method, url, **kwargs) as result:
        body = bytearray()
        for chunk in result.iter_bytes(chunk_size=MAX_BYTES + 1):
            body.extend(chunk)
            if len(body) > MAX_BYTES:
                raise ValueError("Probe response exceeds limit")
        return result.status_code, result.headers, bytes(body)


def owned_read(storage, key, nonce):
    result = storage._cos_call("get_object", Bucket=storage.config.cos_bucket, Key=key)
    stream = result["Body"].get_raw_stream()
    try:
        data = stream.read(MAX_BYTES + 1)
    finally:
        stream.close()
    headers = {name.lower(): value for name, value in result.items()}
    if headers.get(OWNER_HEADER) != nonce or len(data) > MAX_BYTES:
        raise ValueError("Probe ownership or size mismatch; object preserved")
    return data


def create(destination, key, data, mime, nonce):
    destination.call(
        "put_object",
        Key=key,
        Body=data,
        ContentType=mime,
        ACL="private",
        EnableMD5=True,
        Metadata={"x-cos-forbid-overwrite": "true", OWNER_HEADER: nonce},
    )


def probe(storage, client, origin, work_directory=None, progress=None):
    parsed = urlsplit(origin)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("A bare HTTP(S) origin is required")
    nonce = uuid4().hex
    keys = {"temporary": "temporary/" + uuid4().hex, "private": "private/" + uuid4().hex}
    attempted, checks = set(), []
    stage = "bucket_privacy"
    result = {
        "status": "failed",
        "syntheticImages": True,
        "modelCalls": 0,
        "databaseVerified": False,
        "probeRunId": nonce,
    }
    destination = CosDestination(storage)
    try:
        destination.validate()
        checks.append(stage)
        upload, _expiry = storage.url(
            {
                "id": uuid4(),
                "object_key": keys["temporary"],
                "expires_at": datetime.now(UTC) + timedelta(minutes=5),
            },
            write=True,
            headers={"Content-Type": "image/png", OWNER_HEADER: nonce},
        )
        stage = "cors"
        for method in ("PUT", "GET"):
            status, headers, _body = response(
                client,
                "OPTIONS",
                upload,
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": method,
                    "Access-Control-Request-Headers": "content-type",
                },
            )
            methods = {
                item.strip().upper()
                for item in headers.get("access-control-allow-methods", "").split(",")
            }
            allowed = {
                item.strip().lower()
                for item in headers.get("access-control-allow-headers", "").split(",")
            }
            if (
                not 200 <= status < 300
                or headers.get("access-control-allow-origin") not in {origin, "*"}
                or method not in methods
                or not allowed.intersection({"content-type", "*"})
            ):
                raise ValueError("CORS does not allow the application origin/headers/method")
        checks.append(stage)
        images = []
        for colour in ("blue", "red"):
            output = io.BytesIO()
            Image.new("RGB", (16, 16), colour).save(output, "PNG")
            images.append(output.getvalue())
        stage = "signed_upload"
        if progress is not None:
            # 写入前持久化候选键/nonce；进程被强制结束时仍有人工核对归属的依据。
            progress({**result, "status": "running", "candidateKeys": list(keys.values())})
        attempted.add(keys["temporary"])
        # 先条件创建本轮空对象，再用同一签名PUT两次；既保护已有键，也把nonce
        # 签入自定义请求头，避免把未签名的x-cos头加入上传请求。
        create(destination, keys["temporary"], b"", "image/png", nonce)
        status, _headers, _body = response(
            client,
            "PUT",
            upload,
            content=images[0],
            headers={
                "Content-Type": "image/png",
                OWNER_HEADER: nonce,
            },
        )
        if not 200 <= status < 300 or owned_read(storage, keys["temporary"], nonce) != images[0]:
            raise ValueError("Signed upload/readback failed")
        checks.append(stage)
        stage = "immutable_copy"
        clean, mime = sanitize_image(images[0], "image/png")
        attempted.add(keys["private"])
        create(destination, keys["private"], clean, mime, nonce)
        status, _headers, _body = response(
            client,
            "PUT",
            upload,
            content=images[1],
            headers={"Content-Type": "image/png", OWNER_HEADER: nonce},
        )
        if (
            not 200 <= status < 300
            or owned_read(storage, keys["temporary"], nonce) != images[1]
            or owned_read(storage, keys["private"], nonce) != clean
        ):
            raise ValueError("Temporary overwrite affected confirmed bytes")
        checks.append(stage)
        stage = "private_read"
        read, _expiry = storage.url({"id": uuid4(), "object_key": keys["private"]})
        status, _headers, body = response(client, "GET", read, headers={"Origin": origin})
        if status != 200 or body != clean:
            raise ValueError("Signed read failed")
        parts = urlsplit(read)
        unsigned = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        status, _headers, _body = response(client, "GET", unsigned)
        if status not in {403, 404}:
            raise ValueError("Anonymous read did not reject the private object")
        checks.append(stage)
        stage = "archive_recovery"
        with tempfile.TemporaryDirectory(
            prefix="itemtalk_cos_probe_", dir=work_directory
        ) as temporary:
            root = Path(temporary) / "source"
            source = root / keys["private"]
            source.parent.mkdir(parents=True)
            source.write_bytes(clean)
            files = file_manifest(root)
            archive = Path(temporary) / "probe.tar.gz"
            archive_assets(root, archive, files)
            # 删除前再次检查nonce，只恢复本轮新对象，绝不作用于已有用户文件。
            if owned_read(storage, keys["private"], nonce) != clean:
                raise ValueError("Source changed before recovery")
            storage.delete(keys["private"])
            try:
                owned_read(storage, keys["private"], nonce)
            except FileNotFoundError:
                pass
            else:
                raise ValueError("Deleted probe object is still readable")
            restored = Path(temporary) / "restored"
            restored.mkdir()
            restore_assets(archive, restored, files)
            create(
                destination, keys["private"], (restored / keys["private"]).read_bytes(), mime, nonce
            )
            if sha256(owned_read(storage, keys["private"], nonce)) != sha256(clean):
                raise ValueError("Restored COS object differs")
        checks.append(stage)
        result["status"] = "passed"
    except (Exception, KeyboardInterrupt):
        result["failedStage"] = stage
    finally:
        remaining = []
        for key in sorted(attempted):
            try:
                owned_read(storage, key, nonce)
                storage.delete(key)
                try:
                    owned_read(storage, key, nonce)
                except FileNotFoundError:
                    continue
                raise ValueError("Deleted probe object remains")
            except FileNotFoundError:
                continue
            except Exception:
                remaining.append(key)
        if remaining:
            result.update(status="failed", cleanupIncomplete=True, remainingCandidateKeys=remaining)
        else:
            result["cleanupIncomplete"] = False
        result["checks"] = checks
    return result


def main():
    parser = argparse.ArgumentParser(description="Verify COS using new synthetic probe objects")
    parser.add_argument("--real-cos", action="store_true")
    parser.add_argument("--origin", default="https://49.233.65.195")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if not args.real_cos:
        parser.error("Explicit --real-cos required for object creation/deletion")
    # HTTPX可能在INFO打印签名URL，CLI仅保留自己的受控结果，不输出传输诊断。
    logger_levels = {
        logging.getLogger(name): logging.getLogger(name).level
        for name in ("httpx", "httpcore", "qcloud_cos")
    }
    for logger in logger_levels:
        logger.setLevel(logging.CRITICAL)
    try:
        descriptor = os.open(args.report, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as report:
            result = {"status": "failed", "failedStage": "configuration"}
            try:
                config = settings()
                fields = {
                    "COS_BUCKET": config.cos_bucket,
                    "COS_REGION": config.cos_region,
                    "COS_SECRET_ID": config.cos_secret_id.get_secret_value(),
                    "COS_SECRET_KEY": config.cos_secret_key.get_secret_value(),
                }
                result["missingVariables"] = [name for name, value in fields.items() if not value]
                if result["missingVariables"]:
                    raise ValueError("COS configuration incomplete")
                storage = Storage(
                    config.model_copy(update={"storage_backend": "cos"}),
                    cos_retries=0,
                    cos_timeout=15,
                )
                with httpx.Client(timeout=15, follow_redirects=False) as client:

                    def save_progress(state):
                        report.seek(0)
                        report.truncate()
                        json.dump(state, report)
                        report.flush()
                        os.fsync(report.fileno())

                    result = probe(storage, client, args.origin, args.report.parent, save_progress)
            except (Exception, KeyboardInterrupt):
                result.update(
                    status="failed", failedStage=result.get("failedStage", "client_lifecycle")
                )
            report.seek(0)
            report.truncate()
            json.dump(result, report)
        print(
            json.dumps(
                {key: value for key, value in result.items() if key != "remainingCandidateKeys"}
            )
        )
        if result["status"] != "passed":
            parser.exit(
                1, "COS probe incomplete; inspect restricted report. No upstream details printed.\n"
            )
    except OSError:
        parser.exit(1, "Report unavailable; probe not confirmed. No existing report overwritten.\n")
    finally:
        for logger, level in logger_levels.items():
            logger.setLevel(level)


if __name__ == "__main__":
    main()
