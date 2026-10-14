"""按数据库对象清单只读备份COS；不遍历共享桶，也不修改对象或桶配置。"""

import os
import re
from pathlib import Path

from app.storage import sha256

MAX_OBJECT_BYTES = 50 * 1024 * 1024


def object_keys(connection):
    """包括待确认、软删除及独立清理清单；缺失是否允许由资产状态另行核验。"""
    rows = connection.execute(
        "SELECT object_key FROM assets UNION SELECT object_key FROM private_objects "
        "ORDER BY object_key"
    ).fetchall()
    keys = [row[0] for row in rows]
    for key in keys:
        if not isinstance(key, str) or not re.fullmatch(
            r"(?:private|public|temporary)/[a-f0-9]{32}", key
        ):
            raise ValueError("Unsupported object key in the database inventory")
    return keys


def snapshot_objects(storage, keys, directory=None):
    """有界读取当前对象字节，显式记录不存在；重验时也核对缺失状态不变。"""
    result = {}
    for key in keys:
        # keys通常来自object_keys；本边界独立拒绝越界，不能接受模型生成路径。
        if not isinstance(key, str) or not re.fullmatch(
            r"(?:private|public|temporary)/[a-f0-9]{32}", key
        ):
            raise ValueError("Unsupported object key in the backup inventory")
        try:
            data = storage.read(key, maximum=MAX_OBJECT_BYTES)
        except FileNotFoundError:
            result[key] = None
            continue
        if len(data) > MAX_OBJECT_BYTES:
            raise ValueError("Backup object exceeds the application file limit")
        result[key] = {"sizeBytes": len(data), "sha256": sha256(data)}
        if directory is not None:
            path = Path(directory) / key
            path.parent.mkdir(exist_ok=True)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(data)
    return result
