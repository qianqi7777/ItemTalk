"""本地存储配对恢复：归档只包含普通文件，摘要与数据库资产元数据同时核验。"""

import hashlib
import os
import re
import stat
import tarfile
from pathlib import Path


def safe_name(name):
    """使用跨 Windows/Linux 都无歧义的相对对象键，禁止链接及特殊路径。"""
    if (
        not name
        or "\\" in name
        or any(part in {"", ".", ".."} for part in name.split("/"))
        or not re.fullmatch(r"[a-zA-Z0-9_./-]+", name)
    ):
        raise ValueError("Invalid archive object name")
    return name


def regular_file(path):
    if path.is_symlink() or path.is_junction() or not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("Backup only accepts regular files")
    return path


def file_manifest(directory):
    directory = Path(directory)
    if directory.is_symlink() or directory.is_junction() or not directory.is_dir():
        raise ValueError("Asset directory must be a real directory")
    directory = directory.resolve()
    result = {}
    for current, folders, files in os.walk(directory, followlinks=False):
        for folder in folders:
            path = Path(current) / folder
            if path.is_symlink() or path.is_junction():
                raise ValueError("Asset directory contains a link")
        for name in files:
            path = regular_file(Path(current) / name)
            key = safe_name(path.relative_to(directory).as_posix())
            with path.open("rb") as file:
                result[key] = {
                    "sizeBytes": path.stat().st_size,
                    "sha256": hashlib.file_digest(file, "sha256").hexdigest(),
                }
    return dict(sorted(result.items()))


def verify_references(connection, files):
    """已确认且未删除的资产必须能按数据库哈希和大小读回；pending可尚未上传。"""
    references = connection.execute(
        "SELECT object_key,size_bytes,sha256 FROM assets "
        "WHERE status='available' AND deleted_at IS NULL ORDER BY object_key"
    ).fetchall()
    for key, size, checksum in references:
        safe_name(key)
        if not checksum or files.get(key) != {"sizeBytes": size, "sha256": checksum.strip()}:
            raise ValueError("Referenced asset is missing or differs from database metadata")
    return len(references)


def archive_assets(directory, archive, expected):
    directory, archive = Path(directory).resolve(), Path(archive).resolve()
    if archive.is_relative_to(directory):
        raise ValueError("Backup output must be outside the asset directory")
    # 排除任何既有输出覆盖；所有者/绝对路径不写入归档，恢复目标始终是新建目录。
    descriptor = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output, tarfile.open(fileobj=output, mode="w:gz") as tar:
        for key, metadata in expected.items():
            path = regular_file(directory / key)
            info = tarfile.TarInfo(key)
            info.size = metadata["sizeBytes"]
            info.mode = 0o600
            with path.open("rb") as file:
                tar.addfile(info, file)
    if file_manifest(directory) != expected:
        raise ValueError("Assets changed while creating the paired backup")


def restore_assets(archive, directory, expected):
    """不使用extractall；只逐一写入清单内的文件，拒绝链接/重复/超额/未知成员。"""
    directory = Path(directory)
    if directory.is_symlink() or directory.is_junction() or not directory.is_dir():
        raise ValueError("Restore directory must be a real directory")
    directory = directory.resolve()
    if any(directory.iterdir()):
        raise ValueError("Restore directory must be empty")
    seen = set()
    with tarfile.open(archive, mode="r:gz") as tar:
        for member in tar:
            key = safe_name(member.name)
            metadata = expected.get(key)
            if (
                not member.isfile()
                or member.issparse()
                or key in seen
                or metadata is None
                or member.size != metadata["sizeBytes"]
            ):
                raise ValueError("Archive member differs from the expected manifest")
            path = directory / key
            if not path.resolve().is_relative_to(directory):
                raise ValueError("Archive object escapes the restore directory")
            path.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            if source is None:
                raise ValueError("Archive object is unreadable")
            digest = hashlib.sha256()
            remaining = member.size
            with source, path.open("xb") as target:
                while remaining:
                    chunk = source.read(min(remaining, 1024 * 1024))
                    if not chunk:
                        raise ValueError("Archive object is truncated")
                    target.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
            if digest.hexdigest() != metadata["sha256"]:
                raise ValueError("Restored asset checksum mismatch")
            seen.add(key)
    if seen != set(expected) or file_manifest(directory) != expected:
        raise ValueError("Restored asset inventory differs from the paired backup")
