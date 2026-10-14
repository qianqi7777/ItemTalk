"""每日备份及每周恢复入口。COS仅新增/读取，不提供删除历史备份的接口。"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import tarfile
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
from pydantic import SecretStr
from qcloud_cos.cos_exception import CosException

from app.backup_assets import regular_file
from app.config import settings
from app.errors import DomainError
from app.ops_config import configuration
from app.restore_check import restore_drill, restore_saved_bundle
from app.storage import Storage
from app.storage_transfer import CosDestination

SUFFIXES = (".dump", ".dump.assets.tar.gz", ".dump.manifest.json", ".dump.sha256")
RECEIPT_NAME = re.compile(r"completed-[a-f0-9]{32}\.json")
MAX_FILE_BYTES = 2 * 1024**3


def restricted_json(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, sort_keys=True)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())


def backup_root(directory):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink() or root.is_junction() or not root.is_dir():
        raise ValueError("Backup directory must be a real directory")
    return root.resolve()


def file_digest(path):
    path = regular_file(path)
    if not 0 < path.stat().st_size <= MAX_FILE_BYTES:
        raise ValueError("Backup file is empty or exceeds the V1 size limit")
    with path.open("rb") as file:
        return {
            "sha256": hashlib.file_digest(file, "sha256").hexdigest(),
            "sizeBytes": path.stat().st_size,
        }


def bundle_files(backup):
    """只接受工具生成的四个固定文件；不信任校验清单中的任意路径。"""
    backup = Path(backup)
    if not re.fullmatch(r"restore-drill-[a-f0-9]{32}\.dump", backup.name):
        raise ValueError("Unknown backup name")
    stem = backup.name.removesuffix(".dump")
    files = {stem + suffix: file_digest(backup.parent / (stem + suffix)) for suffix in SUFFIXES}
    checksum = regular_file(backup.parent / (stem + ".dump.sha256"))
    if checksum.stat().st_size > 1024:
        raise ValueError("Invalid checksum manifest size")
    lines = checksum.read_text(encoding="utf-8").splitlines()
    expected = [f"{files[stem + suffix]['sha256']}  {stem + suffix}" for suffix in SUFFIXES[:3]]
    if lines != expected:
        raise ValueError("Backup checksum manifest differs")
    return files


class BackupDestination(CosDestination):
    """独立备份凭据，不继承应用资产桶。上传后流式下载核对整个对象。"""

    def create_file(self, key, path):
        with regular_file(path).open("rb") as file:
            self.call(
                "put_object",
                Key=key,
                Body=file,
                ACL="private",
                EnableMD5=True,
                Metadata={"x-cos-forbid-overwrite": "true"},
            )

    def download_verified(self, key, expected, path=None):
        stream = None
        target = None
        digest = hashlib.sha256()
        size = 0
        try:
            response = self.storage.client.get_object(
                Bucket=self.storage.config.cos_bucket, Key=key
            )
            stream = response["Body"].get_raw_stream()
            if path is not None:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                target = os.fdopen(descriptor, "wb")
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > expected["sizeBytes"] or size > MAX_FILE_BYTES:
                    raise ValueError("Remote backup exceeds expected size")
                digest.update(chunk)
                if target is not None:
                    target.write(chunk)
            if {"sizeBytes": size, "sha256": digest.hexdigest()} != expected:
                raise ValueError("Remote backup checksum differs")
        finally:
            if stream is not None:
                stream.close()
            if target is not None:
                target.close()


def configured_destination():
    names = (
        "DB_BACKUP_COS_BUCKET",
        "DB_BACKUP_COS_REGION",
        "DB_BACKUP_COS_SECRET_ID",
        "DB_BACKUP_COS_SECRET_KEY",
    )
    values = [os.environ.get(name, "") for name in names]
    if not all(values):
        raise ValueError("Dedicated database backup COS configuration is required")
    if values[1] == os.environ.get("BACKUP_SOURCE_REGION", "ap-beijing"):
        raise ValueError("Database backup must use a different region")
    config = settings().model_copy(
        update={
            "storage_backend": "cos",
            "cos_bucket": values[0],
            "cos_region": values[1],
            "cos_secret_id": SecretStr(values[2]),
            "cos_secret_key": SecretStr(values[3]),
        }
    )
    return BackupDestination(Storage(config, cos_retries=0, cos_timeout=60))


def validate_receipt(path):
    regular_file(path)
    if not RECEIPT_NAME.fullmatch(path.name) or path.stat().st_size > 16384:
        raise ValueError("Unknown completion receipt")
    receipt = json.loads(path.read_text(encoding="utf-8"))
    files = receipt.get("files", {})
    backup = receipt.get("backupName", "")
    if (
        receipt.get("status") != "uploaded-verified"
        or receipt.get("formatVersion") != 1
        or not re.fullmatch(r"restore-drill-[a-f0-9]{32}\.dump", backup)
        or not re.fullmatch(r"itemtalk/postgresql/\d{8}/[a-f0-9]{32}/", receipt.get("prefix", ""))
        or set(files) != {backup.removesuffix(".dump") + suffix for suffix in SUFFIXES}
        or any(
            not isinstance(item, dict)
            or set(item) != {"sizeBytes", "sha256"}
            or type(item["sizeBytes"]) is not int
            or not 0 < item["sizeBytes"] <= MAX_FILE_BYTES
            or not re.fullmatch(r"[a-f0-9]{64}", str(item["sha256"]))
            for item in files.values()
        )
    ):
        raise ValueError("Invalid completion receipt")
    created = datetime.fromisoformat(receipt["completedAt"])
    if created.tzinfo is None:
        raise ValueError("Receipt timestamp must include timezone")
    return receipt, created


def prune_local(root, now):
    """仅清理已上传并验证的自身文件；未知旧备份与失败任务不删除，COS不删除。"""
    removed = 0
    root = backup_root(root)
    for path in sorted(root.glob("completed-*.json")):
        receipt, created = validate_receipt(path)
        if created >= now - timedelta(days=7):
            continue
        # 先核验全部文件再删；缺失/被改写/链接不能触发部分清理或路径跳转。
        candidates = [root / name for name in receipt["files"]]
        if any(file_digest(file) != receipt["files"][file.name] for file in candidates):
            raise ValueError("Local completed backup changed; retention stopped")
        for file in candidates:
            file.unlink()
        path.unlink()
        removed += 1
    return removed


def daily_backup(root, destination, pg_bin=None, now=None):
    root = backup_root(root)
    now = now or datetime.now(UTC)
    destination.validate()
    result = restore_drill(
        pg_bin, root, cos_assets=True, online_immutable_assets=True, verify_restore=False
    )
    backup = Path(result["backup"])
    files = bundle_files(backup)
    identifier = uuid4().hex
    prefix = f"itemtalk/postgresql/{now:%Y%m%d}/{identifier}/"
    # 第一次上传前持久化唯一前缀；半途失败仍能审核远端残留，不扫描/删除历史桶。
    restricted_json(
        root / f"attempt-{identifier}.json",
        {
            "status": "upload-attempt",
            "startedAt": now.isoformat(),
            "prefix": prefix,
            "bucket": destination.storage.config.cos_bucket,
            "region": destination.storage.config.cos_region,
            "files": files,
        },
    )
    for name, expected in files.items():
        destination.create_file(prefix + name, root / name)
        destination.download_verified(prefix + name, expected)
    # 四个对象均读回核对后才落成功凭证；上传一半失败绝不生成completed文件。
    receipt = {
        "formatVersion": 1,
        "status": "uploaded-verified",
        "completedAt": now.isoformat(),
        "bucket": destination.storage.config.cos_bucket,
        "region": destination.storage.config.cos_region,
        "prefix": prefix,
        "backupName": backup.name,
        "files": files,
        "restoreVerified": False,
    }
    restricted_json(root / f"completed-{identifier}.json", receipt)
    removed = prune_local(root, now)
    return {
        "status": "uploaded-verified",
        "restoreVerified": False,
        "localBundlesPruned": removed,
        "remoteFilesVerified": len(files),
    }


def weekly_restore(root, destination, pg_bin=None):
    root = backup_root(root)
    receipts = [(path, *validate_receipt(path)) for path in root.glob("completed-*.json")]
    if not receipts:
        raise ValueError("No completed backup is available")
    _, receipt, _ = max(receipts, key=lambda item: item[2])
    if (receipt["bucket"], receipt["region"]) != (
        destination.storage.config.cos_bucket,
        destination.storage.config.cos_region,
    ):
        raise ValueError("Backup destination differs from completion receipt")
    destination.validate()
    # 从COS重新下载，而非拿本地副本冒充异地恢复；仅依据受限本地成功凭证取键。
    with tempfile.TemporaryDirectory(prefix="itemtalk_weekly_", dir=root) as directory:
        for name, expected in receipt["files"].items():
            destination.download_verified(
                receipt["prefix"] + name, expected, Path(directory) / name
            )
        backup = Path(directory) / receipt["backupName"]
        if bundle_files(backup) != receipt["files"]:
            raise ValueError("Downloaded bundle differs from receipt")
        result = restore_saved_bundle(backup, pg_bin)
    restricted_json(
        root / f"weekly-{uuid4().hex}.json",
        {
            **result,
            "completedAt": datetime.now(UTC).isoformat(),
            "backupPrefix": receipt["prefix"],
        },
    )
    return result


def main():
    parser = argparse.ArgumentParser(description="Daily COS backup / weekly isolated restore")
    parser.add_argument("mode", choices=("daily", "weekly"))
    parser.add_argument("--real-cos", action="store_true", help="Explicit operator network opt-in")
    parser.add_argument("--output-directory", type=Path, default=Path("/backups"))
    parser.add_argument("--pg-bin", type=Path)
    parser.add_argument(
        "--env-file",
        type=Path,
        action="append",
        help="Restricted environment file; repeat for separate source and backup credentials",
    )
    args = parser.parse_args()
    if not args.real_cos:
        parser.error("--real-cos is required; real services remain pending acceptance")
    try:
        for path in args.env_file or []:
            values = configuration(path)
            # 只读运维相关变量，拒绝从配置改变PYTHONPATH等执行环境。
            allowed = {
                "DATABASE_URL",
                "DB_HOST",
                "DB_PORT",
                "DB_NAME",
                "DB_USER",
                "DB_PASSWORD",
                "STORAGE_BACKEND",
                "COS_BUCKET",
                "COS_REGION",
                "COS_SECRET_ID",
                "COS_SECRET_KEY",
                "BACKUP_SOURCE_REGION",
                "DB_BACKUP_COS_BUCKET",
                "DB_BACKUP_COS_REGION",
                "DB_BACKUP_COS_SECRET_ID",
                "DB_BACKUP_COS_SECRET_KEY",
            }
            os.environ.update(
                {
                    name: value if value is not None else ""
                    for name, value in values.items()
                    if name in allowed
                }
            )
        if args.env_file:
            os.environ.update(ENVIRONMENT="backup", MODEL_PROVIDER="mock")
            settings.cache_clear()
        destination = configured_destination()
        job = daily_backup if args.mode == "daily" else weekly_restore
        print(json.dumps(job(args.output_directory, destination, args.pg_bin)))
    except (
        ValueError,
        KeyError,
        OSError,
        DomainError,
        CosException,
        psycopg.Error,
        subprocess.SubprocessError,
        tarfile.TarError,
    ):
        from app.ops_alerts import emit_event

        # 不把异常文本、SDK URL、数据库内容写入告警；即使尚无联系人也保留事件。
        try:
            emit_event(args.output_directory / "alerts", f"{args.mode}-failed")
        finally:
            parser.exit(
                1, "Backup job failed; inspect restricted state and pending alert events.\n"
            )


if __name__ == "__main__":
    main()
