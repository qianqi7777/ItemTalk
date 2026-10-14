"""生成固定SHA/镜像摘要的维护发布记录；不加载生产配置，也不执行服务器操作。"""

import argparse
import json
import os
import re
from pathlib import Path

IMAGES = {
    "api": "ghcr.io/qianqi7777/itemtalk-api",
    "frontend": "ghcr.io/qianqi7777/itemtalk-frontend",
    "db": "ghcr.io/qianqi7777/itemtalk-db",
    "backup": "ghcr.io/qianqi7777/itemtalk-backup",
}


def release_record(sha, ci_run, revision, digests):
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise ValueError("Full commit SHA required")
    if not re.fullmatch(r"[1-9][0-9]{0,19}", ci_run):
        raise ValueError("Successful CI run ID required")
    if not re.fullmatch(r"[a-zA-Z0-9_]{1,64}", revision):
        raise ValueError("Single migration head required")
    if set(digests) != set(IMAGES) or any(
        not re.fullmatch(r"sha256:[a-f0-9]{64}", value) for value in digests.values()
    ):
        raise ValueError("All required image digests required")
    # 部署按摘要选择字节；sha标签只用于查找，重新构建同一提交也不能改变这份记录。
    return {
        "formatVersion": 1,
        "commitSha": sha,
        "ciRunId": int(ci_run),
        "schemaRevision": revision,
        "compatibility": "requires-maintenance",
        "images": {
            name: {"tag": f"{image}:sha-{sha}", "reference": f"{image}@{digests[name]}"}
            for name, image in IMAGES.items()
        },
    }


def image_overrides(record):
    return (
        "\n".join(
            [
                f"IMAGE_TAG=sha-{record['commitSha']}",
                f"RELEASE_SHA={record['commitSha']}",
            ]
            + [f"{name.upper()}_IMAGE={record['images'][name]['reference']}" for name in IMAGES]
        )
        + "\n"
    )


def main():
    parser = argparse.ArgumentParser(description="Record verified maintenance image digests")
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    try:
        record = release_record(
            os.environ.get("RELEASE_SHA", ""),
            os.environ.get("CI_RUN_ID", ""),
            os.environ.get("SCHEMA_REVISION", ""),
            {name: os.environ.get(f"{name.upper()}_DIGEST", "") for name in IMAGES},
        )
        # 不复用旧目录；部分失败保留可检查结果，不能覆盖另一份发布记录。
        args.output_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        for name, content in {
            "release.json": json.dumps(record, ensure_ascii=False, indent=2) + "\n",
            "image-overrides.env": image_overrides(record),
        }.items():
            descriptor = os.open(
                args.output_directory / name,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
                output.write(content)
    except (ValueError, OSError):
        parser.exit(
            1,
            "Release record invalid or output unavailable; no existing files overwritten.\n",
        )


if __name__ == "__main__":
    main()
