"""维护窗口的本地资产→COS复制；不提供HTTP/Agent入口，不删除或改写源数据。"""

import argparse
import hashlib
import json
import os
import re
from pathlib import Path

import psycopg
from pydantic import ValidationError
from qcloud_cos.cos_exception import CosException, CosServiceError
from sqlalchemy.engine import make_url

from app.backup_assets import file_manifest, regular_file, verify_references
from app.config import settings
from app.errors import DomainError
from app.restore_check import manifest
from app.storage import Storage, sha256

MAX_OBJECT_BYTES = 50 * 1024 * 1024


class CosDestination:
    """限定私有、未启用版本控制的目标，确保禁止覆盖与物理删除语义可用。"""

    def __init__(self, storage):
        if storage.client is None:
            raise ValueError("COS destination is required")
        self.storage = storage

    def call(self, operation, **kwargs):
        if operation not in {
            "get_bucket_versioning",
            "get_bucket_acl",
            "get_bucket_policy",
            "put_object",
        }:
            raise ValueError("Unsupported transfer operation")
        try:
            return getattr(self.storage.client, operation)(
                Bucket=self.storage.config.cos_bucket, **kwargs
            )
        except CosServiceError as error:
            if (
                operation == "get_bucket_policy"
                and error.get_status_code() == 404
                and error.get_error_code() == "NoSuchBucketPolicy"
            ):
                return None
            if operation == "put_object" and error.get_status_code() == 409:
                raise DomainError(
                    409, "RESOURCE_CONFLICT", "目标对象已存在，迁移没有覆盖它"
                ) from None
            raise DomainError(503, "INTERNAL_ERROR", "COS迁移操作失败，源数据保留") from None
        except CosException:
            raise DomainError(503, "INTERNAL_ERROR", "COS迁移操作失败，源数据保留") from None

    def validate(self):
        versioning = self.call("get_bucket_versioning")
        if not isinstance(versioning, dict):
            raise ValueError("Invalid destination versioning response")
        if versioning.get("Status") not in (None, ""):
            raise ValueError("Versioned destinations require a separate migration/deletion plan")
        acl = self.call("get_bucket_acl")
        if not isinstance(acl, dict) or not isinstance(acl.get("Owner"), dict):
            raise ValueError("Invalid destination ACL response")
        owner = acl.get("Owner", {}).get("ID")
        grants = acl.get("Grant", [])
        grants = [grants] if isinstance(grants, dict) else grants
        if (
            not owner
            or not isinstance(grants, list)
            or any(
                not isinstance(g, dict)
                or not isinstance(g.get("Grantee"), dict)
                or g["Grantee"].get("ID") != owner
                or g["Grantee"].get("URI")
                for g in grants
            )
        ):
            raise ValueError("Destination ACL must be private to the owner")
        policy = self.call("get_bucket_policy")
        if policy is not None and not isinstance(policy, dict):
            raise ValueError("Invalid destination policy response")
        if policy:
            rules = policy.get("statement", policy.get("Statement", []))
            if (
                not isinstance(rules, list)
                or not rules
                or any(
                    not isinstance(rule, dict)
                    or str(rule.get("effect", rule.get("Effect", ""))).lower() != "deny"
                    for rule in rules
                )
            ):
                # 限制性Deny策略不会扩大私有ACL；含Allow的策略需独立访问审查，
                # 工具不会要求用户删除安全策略或自行更改桶权限。
                raise ValueError("Destination policies require separate access verification")

    def read(self, key, maximum):
        return self.storage.read(key, maximum=maximum)

    def create(self, key, data):
        # 此SDK没有命名的ForbidOverwrite参数；Metadata会传递固定原始请求头。
        # 不能仅先GET再PUT：服务端禁止覆盖头部同时拒绝读取后的并发同名创建。
        self.call(
            "put_object",
            Key=key,
            Body=data,
            ContentType="application/octet-stream",
            ACL="private",
            Metadata={"x-cos-forbid-overwrite": "true"},
            EnableMD5=True,
        )


def copy_assets(directory, expected, destination=None, *, apply=False, writers_paused=False):
    """同键同字节复用，冲突停止；每次都重读目标核验，不靠旧日志跳过检查。"""
    if not writers_paused:
        raise ValueError("Writers and cleanup workers must be paused")
    root = Path(directory).resolve()
    if file_manifest(directory) != expected:
        raise ValueError("Source differs from the migration manifest")
    for key, item in expected.items():
        if not re.fullmatch(r"(?:private|public|temporary)/[a-f0-9]{32}", key):
            raise ValueError("Unsupported object key in source inventory")
        if item["sizeBytes"] > MAX_OBJECT_BYTES:
            raise ValueError("Source object exceeds the application file limit")
    result = {"status": "planned", "files": len(expected), "copied": 0, "reused": 0}
    if not apply:
        return result
    if destination is None:
        raise ValueError("COS destination is required")
    destination.validate()
    for key, item in expected.items():
        with regular_file(root / key).open("rb") as source:
            data = source.read(MAX_OBJECT_BYTES + 1)
        if len(data) != item["sizeBytes"] or sha256(data) != item["sha256"]:
            raise ValueError("Source object changed during the migration")
        try:
            target = destination.read(key, item["sizeBytes"])
        except FileNotFoundError:
            destination.create(key, data)
            target = destination.read(key, item["sizeBytes"])
            result["copied"] += 1
        else:
            result["reused"] += 1
        if len(target) != item["sizeBytes"] or sha256(target) != item["sha256"]:
            raise ValueError("Target object differs; migration stopped without replacing it")
    if file_manifest(directory) != expected:
        raise ValueError("Source inventory changed during the migration")
    return {**result, "status": "verified"}


def transfer(directory, *, apply=False, writers_paused=False):
    if not writers_paused:
        raise ValueError("Writers and cleanup workers must be paused")
    config = settings()
    if apply and (
        config.storage_backend != "cos"
        or not all(
            (
                config.cos_bucket,
                config.cos_secret_id.get_secret_value(),
                config.cos_secret_key.get_secret_value(),
            )
        )
    ):
        raise ValueError("Complete COS configuration is required")
    url = config.db_url()
    url = make_url(url) if isinstance(url, str) else url
    connection_args = url.translate_connect_args(username="user", database="dbname")
    connection_args.update(url.query)
    connection_args["connect_timeout"] = 10
    with psycopg.connect(**connection_args) as source:
        source.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        source.execute("SET LOCAL row_security=off")
        before = manifest(source)
        files = file_manifest(directory)
        references = verify_references(source, files)
    result = copy_assets(
        directory,
        files,
        CosDestination(Storage()) if apply else None,
        apply=apply,
        writers_paused=writers_paused,
    )
    with psycopg.connect(**connection_args) as source:
        source.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        source.execute("SET LOCAL row_security=off")
        if manifest(source) != before:
            raise ValueError("Database changed during the migration; keep maintenance enabled")
    result["referencedAssets"] = references
    result["inventorySha256"] = hashlib.sha256(
        json.dumps(files, sort_keys=True).encode()
    ).hexdigest()
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Plan or copy local assets to a private COS bucket"
    )
    parser.add_argument("--assets-directory", type=Path, required=True)
    parser.add_argument("--writers-paused", action="store_true")
    parser.add_argument(
        "--apply", action="store_true", help="Actually write to COS; remote approval required"
    )
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        # 报告本身也会改变文件清单，不能写入待迁移的源目录。
        if args.report.resolve().is_relative_to(args.assets_directory.resolve()):
            raise ValueError("Report must be outside the source asset directory")
        # 报告目标不可用就不开始网络/复制；成功和失败都不覆盖既有记录。
        descriptor = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as report:
            try:
                result = transfer(
                    args.assets_directory, apply=args.apply, writers_paused=args.writers_paused
                )
            except (ValueError, OSError, psycopg.Error, DomainError, ValidationError):
                json.dump({"status": "failed", "sourcePreserved": True}, report)
                raise
            json.dump(result, report)
        print(json.dumps(result))
    except (ValueError, OSError, psycopg.Error, DomainError, ValidationError):
        parser.exit(1, "Storage migration failed; source data preserved. No credentials printed.\n")


if __name__ == "__main__":
    main()
