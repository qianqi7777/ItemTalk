"""备份恢复演练：一致性快照导出后恢复到新数据库，核对结构和内容摘要。

仅用于运维命令，不提供 HTTP/Agent 工具入口。原数据库始终只读；清理只作用于
本次成功创建、名称以 itemtalk_restore_ 开头的临时数据库，失败不删除已有库。
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from app.config import settings


def canonical_constraint(definition):
    """归一化 pg_dump 重新解析时的 varchar 常量数组强制转换。

    PostgreSQL 可将 varchar[]→text[] 改写为每个常量 varchar→text；两者的
    CHECK 字典相同。只转换完全由 varchar 字面常量组成的数组，其他定义保留。
    """
    literal = r"'(?:''|[^'])*'::character varying"
    pattern = r"\(ARRAY\[((?:" + literal + r")(?:, " + literal + r")*)\]\)::text\[\]"

    def distribute(match):
        entries = re.findall(literal, match.group(1))
        return "ARRAY[" + ", ".join(f"({entry})::text" for entry in entries) + "]"

    return re.sub(pattern, distribute, definition)


def manifest(connection):
    """对所有公共 schema 表计算确定性摘要，响应仅提供数量，不泄漏原始数据。"""
    tables = connection.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
    ).fetchall()
    results = {}
    for (table,) in tables:
        digest = hashlib.sha256()
        count = 0
        # MD5 只作为固定宽度排序键；完整行内容的完整性摘要使用 SHA-256。
        query = sql.SQL(
            "SELECT to_jsonb(r)::text FROM {} r ORDER BY md5(to_jsonb(r)::text),to_jsonb(r)::text"
        ).format(sql.Identifier("public", table))
        with connection.cursor(name=f"verify_{uuid4().hex}") as cursor:
            cursor.execute(query)
            for (row,) in cursor:
                digest.update(row.encode())
                digest.update(b"\n")
                count += 1
        results[table] = {"rows": count, "sha256": digest.hexdigest()}
    extensions = connection.execute(
        "SELECT extname,extversion FROM pg_extension ORDER BY extname"
    ).fetchall()
    constraints = connection.execute(
        "SELECT c.conname,pg_get_constraintdef(c.oid) FROM pg_constraint c "
        "JOIN pg_namespace n ON n.oid=c.connamespace "
        "WHERE n.nspname='public' ORDER BY c.conname,pg_get_constraintdef(c.oid)"
    ).fetchall()
    constraints = [(name, canonical_constraint(definition)) for name, definition in constraints]
    return {"tables": results, "extensions": extensions, "constraints": constraints}


def restore_drill(pg_bin=None, output_directory=Path(".local-data/backups")):
    """使用进程环境传递凭据；工具路径由运维显式指定，不接受模型生成命令。"""
    url = settings().db_url()
    url = make_url(url) if isinstance(url, str) else url
    connect_args = url.translate_connect_args(username="user", database="dbname")
    environment = os.environ.copy()
    for name, value in {
        "PGHOST": url.host,
        "PGPORT": url.port or 5432,
        "PGUSER": url.username,
        "PGDATABASE": url.database,
        "PGPASSWORD": url.password or "",
    }.items():
        if value is not None:
            environment[name] = str(value)
    binaries = {}
    for name in ("pg_dump", "pg_restore"):
        if pg_bin:
            executable = Path(pg_bin) / (name + (".exe" if os.name == "nt" else ""))
        else:
            executable = Path(shutil.which(name) or "")
        if not executable.is_file():
            raise ValueError("PostgreSQL client binaries are required")
        binaries[name] = str(executable.resolve())
    output_directory = Path(output_directory).resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    identifier = uuid4().hex
    backup = output_directory / f"restore-drill-{identifier}.dump"
    scratch = f"itemtalk_restore_{identifier[:16]}"
    created = False
    try:
        with psycopg.connect(**connect_args) as source:
            source.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            snapshot = source.execute("SELECT pg_export_snapshot()").fetchone()[0]
            expected = manifest(source)
            descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as file:
                # 固定 pg_dump 参数；密码只在 environment，不在 argv 或日志中。
                subprocess.run(  # noqa: S603
                    [
                        binaries["pg_dump"],
                        "--format=custom",
                        "--no-owner",
                        "--no-privileges",
                        "--snapshot",
                        snapshot,
                    ],
                    env=environment,
                    stdout=file,
                    stderr=subprocess.PIPE,
                    check=True,
                    timeout=300,
                )
        with psycopg.connect(**connect_args, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(scratch)))
            created = True
        restore_env = {**environment, "PGDATABASE": scratch}
        subprocess.run(  # noqa: S603
            [
                binaries["pg_restore"],
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
                "--dbname",
                scratch,
                str(backup),
            ],
            env=restore_env,
            capture_output=True,
            check=True,
            timeout=300,
        )
        with psycopg.connect(**{**connect_args, "dbname": scratch}) as restored:
            actual = manifest(restored)
            if actual != expected:
                raise ValueError("Restored database differs from the consistent snapshot")
        with backup.open("rb") as file:
            checksum = hashlib.file_digest(file, "sha256").hexdigest()
        checksum_file = backup.with_suffix(".dump.sha256")
        checksum_file.write_text(f"{checksum}  {backup.name}\n", encoding="utf-8")
        return {
            "status": "passed",
            "tables": len(expected["tables"]),
            "rows": sum(table["rows"] for table in expected["tables"].values()),
            "backup": str(backup),
        }
    finally:
        if created:
            # scratch 仅由本函数生成，且只有 CREATE 成功后才执行清理。
            with psycopg.connect(**connect_args, autocommit=True) as admin:
                admin.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(scratch))
                )


def main():
    parser = argparse.ArgumentParser(description="Verify backup in an isolated restore database")
    parser.add_argument("--pg-bin", type=Path)
    parser.add_argument("--output-directory", type=Path, default=Path(".local-data/backups"))
    args = parser.parse_args()
    try:
        print(json.dumps(restore_drill(args.pg_bin, args.output_directory), ensure_ascii=False))
    except (ValueError, OSError, psycopg.Error, subprocess.SubprocessError):
        # 不输出 pg_dump/restore stderr；其中可能包含数据、路径或连接信息。
        parser.exit(
            1, "Backup/restore verification failed; inspect restricted operational state.\n"
        )


if __name__ == "__main__":
    main()
