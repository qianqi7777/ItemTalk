"""运维CLI显式受限环境文件读取；不默认读取应用配置，不展开环境引用。"""

import os
import stat
from pathlib import Path

from dotenv import dotenv_values

from app.backup_assets import regular_file


def configuration(env_file=None):
    values = {}
    if env_file is not None:
        path = regular_file(Path(env_file))
        if os.name != "nt" and (
            stat.S_IMODE(path.stat().st_mode) != 0o600 or path.stat().st_uid != os.getuid()
        ):
            raise ValueError("Operational environment must be owner-only mode 0600")
        values = dict(dotenv_values(path, interpolate=False))
    # 文件优先，不能让终端遗留true发送开关覆盖受限文件的false。
    return {**os.environ, **values}
