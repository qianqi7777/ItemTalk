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
import tarfile
import tempfile
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from app.backup_assets import archive_assets, file_manifest, restore_assets, verify_references
from app.backup_cos import object_keys, snapshot_objects
from app.config import settings
from app.errors import DomainError
from app.storage import Storage


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
    # 数据摘要相同仍可能丢失隔离：恢复必须同时保存 FORCE RLS、策略、函数 owner
    # 和 EXECUTE ACL，不能把 SECURITY DEFINER 函数恢复成超级用户/PUBLIC 可调用。
    security_tables = connection.execute(
        "SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,r.rolname,"
        "ARRAY(SELECT a::text FROM unnest(c.relacl) a ORDER BY a::text) "
        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_roles r ON r.oid=c.relowner WHERE n.nspname='public' AND c.relkind='r' "
        "ORDER BY c.relname"
    ).fetchall()
    # pg_dump 会重排 ACL 中的角色；按完整授权项排序，不忽略权限内容和授予者。
    column_grants = connection.execute(
        "SELECT c.relname,a.attname,"
        "ARRAY(SELECT acl::text FROM unnest(a.attacl) acl ORDER BY acl::text) "
        "FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND a.attnum>0 AND NOT a.attisdropped "
        "AND a.attacl IS NOT NULL ORDER BY c.relname,a.attname"
    ).fetchall()
    policies = connection.execute(
        "SELECT tablename,policyname,permissive,roles::text,cmd,qual,with_check "
        "FROM pg_policies WHERE schemaname='public' ORDER BY tablename,policyname"
    ).fetchall()
    functions = connection.execute(
        "SELECT p.proname,pg_get_function_identity_arguments(p.oid),p.prosecdef,"
        "p.proconfig::text,r.rolname,"
        "ARRAY(SELECT a::text FROM unnest(p.proacl) a ORDER BY a::text),p.prosrc "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
        "JOIN pg_roles r ON r.oid=p.proowner WHERE n.nspname='public' AND "
        "NOT EXISTS(SELECT 1 FROM pg_depend d WHERE d.objid=p.oid "
        "AND d.classid='pg_proc'::regclass AND d.deptype='e') "
        "ORDER BY p.proname,pg_get_function_identity_arguments(p.oid)"
    ).fetchall()
    triggers = connection.execute(
        "SELECT c.relname,t.tgname,t.tgenabled,pg_get_triggerdef(t.oid) "
        "FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' "
        "AND NOT t.tgisinternal ORDER BY c.relname,t.tgname"
    ).fetchall()
    return {
        "tables": results,
        "extensions": extensions,
        "constraints": constraints,
        "securityTables": security_tables,
        "columnGrants": column_grants,
        "policies": policies,
        "functions": functions,
        "triggers": triggers,
    }


def postgres_clients(pg_bin=None):
    binaries = {}
    for name in ("pg_dump", "pg_restore"):
        if pg_bin:
            executable = Path(pg_bin) / (name + (".exe" if os.name == "nt" else ""))
        else:
            executable = Path(shutil.which(name) or "")
        if not executable.is_file():
            raise ValueError("PostgreSQL client binaries are required")
        # Debian的pg_wrapper按argv[0]分派；resolve会把pg_dump/pg_restore都
        # 变成pg_wrapper，从而丢失工具名。绝对路径仍须保留最后一级符号链接。
        binaries[name] = str(executable.absolute())
    return binaries


def restore_drill(
    pg_bin=None,
    output_directory=Path(".local-data/backups"),
    assets_directory=None,
    writers_paused=False,
    cos_assets=False,
    online_immutable_assets=False,
    verify_restore=True,
):
    """使用进程环境传递凭据；工具路径由运维显式指定，不接受模型生成命令。"""
    config = settings()
    if online_immutable_assets and (not cos_assets or writers_paused):
        raise ValueError("Online immutable mode requires COS and cannot claim paused writers")
    if cos_assets:
        if assets_directory is not None:
            raise ValueError("Local and COS backup modes are mutually exclusive")
        if not writers_paused and not online_immutable_assets:
            raise ValueError("Paired backups require paused writers and cleanup workers")
        if config.storage_backend != "cos" or not all(
            (
                config.cos_bucket,
                config.cos_secret_id.get_secret_value(),
                config.cos_secret_key.get_secret_value(),
            )
        ):
            raise ValueError("Complete COS configuration is required")
    if assets_directory is not None:
        if not writers_paused:
            raise ValueError("Paired backups require paused writers and cleanup workers")
        if config.storage_backend != "local":
            raise ValueError("Local asset archive cannot verify COS object recovery")
        assets_directory = Path(assets_directory)
    url = config.db_url()
    url = make_url(url) if isinstance(url, str) else url
    connect_args = url.translate_connect_args(username="user", database="dbname")
    connect_args.update(url.query)
    connect_args["connect_timeout"] = 10
    environment = os.environ.copy()
    for name, value in {
        "PGHOST": url.host,
        "PGPORT": url.port or 5432,
        "PGUSER": url.username,
        "PGDATABASE": url.database,
        "PGPASSWORD": url.password or "",
        "PGCONNECT_TIMEOUT": 10,
    }.items():
        if value is not None:
            environment[name] = str(value)
    binaries = postgres_clients(pg_bin)
    output_directory = Path(output_directory).resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    identifier = uuid4().hex
    backup = output_directory / f"restore-drill-{identifier}.dump"
    assets_archive = backup.with_suffix(".dump.assets.tar.gz")
    staging = None
    inventory = None
    object_storage = None
    try:
        with psycopg.connect(**connect_args) as source:
            source.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            # 非管理角色受 RLS 过滤时直接失败，避免生成缺少私有数据的“成功”备份。
            source.execute("SET LOCAL row_security=off")
            snapshot = source.execute("SELECT pg_export_snapshot()").fetchone()[0]
            expected = manifest(source)
            if cos_assets:
                # 临时目录由本次演练创建，只删除自己的副本。源COS没有PUT/DELETE。
                staging = tempfile.TemporaryDirectory(
                    prefix=f"itemtalk_cos_{identifier}_", dir=output_directory
                )
                assets_directory = Path(staging.name)
                object_storage = Storage()
                keys = object_keys(source)
                inventory = snapshot_objects(object_storage, keys, assets_directory)
            files = file_manifest(assets_directory) if assets_directory is not None else None
            referenced_assets = verify_references(source, files) if files is not None else None
            descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as file:
                # 固定 pg_dump 参数；密码只在 environment，不在 argv 或日志中。
                subprocess.run(  # noqa: S603
                    [
                        binaries["pg_dump"],
                        "--format=custom",
                        "--snapshot",
                        snapshot,
                    ],
                    env=environment,
                    stdout=file,
                    stderr=subprocess.PIPE,
                    check=True,
                    timeout=300,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            if files is not None:
                archive_assets(assets_directory, assets_archive, files)
        # 停写模式重验全DB；在线模式恢复的是导出快照，快照后的普通写入不纳入。
        # 两种模式都重验原对象字节：删除/改写导致失败，不能输出缺文件的成功备份。
        if files is not None:
            with psycopg.connect(**connect_args) as stable:
                stable.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                stable.execute("SET LOCAL row_security=off")
                if (not online_immutable_assets and manifest(stable) != expected) or file_manifest(
                    assets_directory
                ) != files:
                    raise ValueError("Source changed during the paired backup")
                if cos_assets and (
                    (not online_immutable_assets and object_keys(stable) != keys)
                    or snapshot_objects(object_storage, keys) != inventory
                ):
                    raise ValueError("COS objects changed during the paired backup")
        if verify_restore:
            restore_snapshot(
                backup, expected, files, assets_archive, connect_args, environment, binaries
            )
        else:
            # 每日只导出并验证归档目录；不能把没有实际恢复的结果标为演练通过。
            subprocess.run(  # noqa: S603
                [binaries["pg_restore"], "--list", str(backup)],
                env=environment,
                capture_output=True,
                check=True,
                timeout=300,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        with backup.open("rb") as file:
            checksum = hashlib.file_digest(file, "sha256").hexdigest()
        checksum_file = backup.with_suffix(".dump.sha256")
        checksums = f"{checksum}  {backup.name}\n"
        result = {
            "status": "passed" if verify_restore else "exported",
            "restoreVerified": verify_restore,
            "tables": len(expected["tables"]),
            "rows": sum(table["rows"] for table in expected["tables"].values()),
            "backup": str(backup),
        }
        if files is not None:
            with assets_archive.open("rb") as file:
                assets_checksum = hashlib.file_digest(file, "sha256").hexdigest()
            checksums += f"{assets_checksum}  {assets_archive.name}\n"
            result.update(
                assetFiles=len(files),
                assetBytes=sum(file["sizeBytes"] for file in files.values()),
                referencedAssets=referenced_assets,
                assetsArchive=str(assets_archive),
            )
            bundle = backup.with_suffix(".dump.manifest.json")
            descriptor = os.open(bundle, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                json.dump(
                    {
                        "formatVersion": 2 if cos_assets else 1,
                        "database": expected,
                        "assets": files,
                        "consistencyMode": "online-immutable-cos"
                        if online_immutable_assets
                        else "paused-paired",
                        **({"cosCurrentObjects": inventory} if cos_assets else {}),
                    },
                    file,
                    ensure_ascii=False,
                    sort_keys=True,
                )
            with bundle.open("rb") as file:
                bundle_checksum = hashlib.file_digest(file, "sha256").hexdigest()
            checksums += f"{bundle_checksum}  {bundle.name}\n"
            result["manifest"] = str(bundle)
            if cos_assets:
                # 此演练证明可从源COS备份恢复到隔离本地文件；目标COS回写须另验。
                result.update(
                    assetSource="cos",
                    assetRestoreTarget="local-isolated",
                    inventoriedObjects=len(inventory),
                    missingOptionalObjects=sum(value is None for value in inventory.values()),
                    sourceWritesPaused=writers_paused,
                    consistencyMode="online-immutable-cos"
                    if online_immutable_assets
                    else "paused-paired",
                )
        descriptor = os.open(checksum_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(checksums)
        return result
    finally:
        if staging is not None:
            staging.cleanup()


def restore_snapshot(backup, expected, files, assets_archive, connect_args, environment, binaries):
    """仅恢复本次创建的临时库；生产库名称永远不会传给pg_restore。"""
    scratch = f"itemtalk_restore_{uuid4().hex[:16]}"
    created = False
    try:
        with psycopg.connect(**connect_args, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(scratch)))
            created = True
        subprocess.run(  # noqa: S603
            [binaries["pg_restore"], "--exit-on-error", "--dbname", scratch, str(backup)],
            env={**environment, "PGDATABASE": scratch},
            capture_output=True,
            check=True,
            timeout=300,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        with psycopg.connect(**{**connect_args, "dbname": scratch}) as restored:
            # 每周从JSON清单加载时tuple变成list；比较规范JSON，仍核验全部字段。
            if json.loads(json.dumps(manifest(restored))) != json.loads(json.dumps(expected)):
                raise ValueError("Restored database differs from the consistent snapshot")
            if files is not None:
                with tempfile.TemporaryDirectory(
                    prefix="itemtalk_assets_", dir=Path(backup).parent
                ) as directory:
                    restore_assets(assets_archive, directory, files)
                    verify_references(restored, file_manifest(directory))
        return {"status": "passed", "restoreVerified": True, "target": "isolated-database"}
    finally:
        if created:
            # CREATE失败时不删已有库，甚至不会尝试清理同名对象。
            with psycopg.connect(**connect_args, autocommit=True) as admin:
                admin.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(scratch))
                )


def restore_saved_bundle(backup, pg_bin=None):
    """调用方先验证完整四文件校验清单，再用既有角色恢复至隔离库。"""
    backup = Path(backup)
    bundle = json.loads(backup.with_suffix(".dump.manifest.json").read_text(encoding="utf-8"))
    if bundle.get("formatVersion") not in {1, 2} or not isinstance(bundle.get("assets"), dict):
        raise ValueError("Paired manifest is required")
    url = settings().db_url()
    url = make_url(url) if isinstance(url, str) else url
    args = url.translate_connect_args(username="user", database="dbname")
    args.update(url.query)
    args["connect_timeout"] = 10
    environment = {
        **os.environ,
        "PGHOST": str(url.host or "localhost"),
        "PGPORT": str(url.port or 5432),
        "PGUSER": str(url.username or ""),
        "PGPASSWORD": str(url.password or ""),
        "PGCONNECT_TIMEOUT": "10",
    }
    return restore_snapshot(
        backup,
        bundle["database"],
        bundle["assets"],
        backup.with_suffix(".dump.assets.tar.gz"),
        args,
        environment,
        postgres_clients(pg_bin),
    )


def main():
    parser = argparse.ArgumentParser(description="Verify backup in an isolated restore database")
    parser.add_argument("--pg-bin", type=Path)
    parser.add_argument("--output-directory", type=Path, default=Path(".local-data/backups"))
    parser.add_argument("--assets-directory", type=Path, help="Paired local storage root")
    parser.add_argument(
        "--export-only", action="store_true", help="Export without claiming restore"
    )
    parser.add_argument(
        "--cos-assets", action="store_true", help="Read database-inventoried COS objects"
    )
    parser.add_argument(
        "--writers-paused", action="store_true", help="Confirm API/Worker/other writers are paused"
    )
    parser.add_argument(
        "--online-immutable-assets",
        action="store_true",
        help="Use DB snapshot and hash-verified immutable COS objects without pausing writes",
    )
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                restore_drill(
                    args.pg_bin,
                    args.output_directory,
                    args.assets_directory,
                    args.writers_paused,
                    args.cos_assets,
                    args.online_immutable_assets,
                    not args.export_only,
                ),
                ensure_ascii=False,
            )
        )
    except (
        ValueError,
        OSError,
        tarfile.TarError,
        psycopg.Error,
        subprocess.SubprocessError,
        DomainError,
    ):
        # 不输出 pg_dump/restore stderr；其中可能包含数据、路径或连接信息。
        parser.exit(
            1, "Backup/restore verification failed; inspect restricted operational state.\n"
        )


if __name__ == "__main__":
    main()
