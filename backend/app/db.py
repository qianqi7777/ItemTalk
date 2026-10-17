"""SQLAlchemy 持久化基础设施；所有业务写入在显式事务内执行。"""

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.config import settings
from app.errors import DomainError

# 身份来自已验证的 HTTP JWT 或已领取的 Worker 任务，不接收客户端指定的用户头。
actor_id = ContextVar("database_actor", default=None)
identity_lock_held = ContextVar("database_identity_lock_held", default=None)
actor_may_cleanup = ContextVar("database_actor_may_cleanup", default=False)


@contextmanager
def actor(user_id, *, allow_deleted=False):
    token = actor_id.set(UUID(str(user_id)) if user_id else None)
    cleanup_token = actor_may_cleanup.set(allow_deleted)
    try:
        yield
    finally:
        actor_may_cleanup.reset(cleanup_token)
        actor_id.reset(token)


@lru_cache
def engine():
    # 两个API进程各至多6、Worker至多3、迁移1，共16，留4个诊断连接。
    # 上游调用仍在事务外；不同角色不能沿用API池而突破20连接预算。
    url = settings().db_url()
    role = make_url(url).username
    size, overflow = (2, 1) if role == "itemtalk_worker" else (5, 1)
    if role == "itemtalk_migrator":
        size, overflow = 1, 0
    return create_engine(
        url, pool_size=size, max_overflow=overflow, pool_pre_ping=True, hide_parameters=True
    )


@contextmanager
def transaction(*, exclusive=False, lock_identity=True):
    with engine().begin() as connection:
        # SET LOCAL 只在本事务有效；连接池复用、提交和回滚都不能遗留上一用户身份。
        execute(
            connection,
            "SELECT set_config('itemtalk.user_id',:actor,true)",
            actor=str(actor_id.get() or ""),
        )
        held = identity_lock_held.get()
        current = actor_id.get()
        if exclusive and held == current and current:
            raise RuntimeError("Deletion must start outside an existing private transaction")
        token = None
        if current and lock_identity and held != current:
            # 私有请求共享用户锁，删除独占用户锁。锁先于业务行锁取得，阻止已认证的
            # 并发请求在删除快照之后新建记录；数据库事务结束自动释放。
            key = int.from_bytes(
                hashlib.sha256(actor_id.get().bytes).digest()[:8], "big", signed=True
            )
            function = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
            execute(connection, f"SELECT {function}(:key)", key=key)  # noqa: S608
            token = identity_lock_held.set(current)
        try:
            if current and not actor_may_cleanup.get():
                # 认证依赖与业务事务之间可能发生账号删除；取得共享锁后再次验证。
                active = one(
                    connection,
                    ("SELECT id FROM users WHERE id=:u AND status='active' AND deleted_at IS NULL"),
                    u=current,
                )
                if not active:
                    raise DomainError(401, "AUTH_REQUIRED", "账号不可用，请重新登录")
            # 图工具/预算会打开嵌套连接；复用外层身份锁，避免与排队删除相互等待。
            yield connection
        finally:
            if token is not None:
                identity_lock_held.reset(token)


def execute(connection, statement, **params):
    return connection.execute(text(statement), params)


def one(connection, statement, **params):
    result = execute(connection, statement, **params).mappings().first()
    return dict(result) if result else None


def many(connection, statement, **params):
    return [dict(row) for row in execute(connection, statement, **params).mappings()]


def json_value(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        default=lambda v: str(v) if isinstance(v, UUID) else v.isoformat(),
    )


def validate_runtime(kind):
    """托管环境启动前校验真实角色，避免 URL 误配超级用户让 FORCE RLS 失效。"""
    with transaction() as connection:
        role = one(
            connection,
            (
                "SELECT r.rolsuper,r.rolbypassrls,"
                "pg_has_role(current_user,'itemtalk_guard','MEMBER') AS guard_member,"
                "EXISTS(SELECT 1 FROM pg_class c "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relowner=r.oid AND c.relkind='r') AS owns_table "
                "FROM pg_roles r WHERE r.rolname=current_user"
            ),
        )
        queue_allowed = one(
            connection,
            (
                "SELECT has_function_privilege(current_user,'itemtalk_queue_claim(uuid,integer)',"
                "'EXECUTE') AS allowed"
            ),
        )["allowed"]
        protected = one(
            connection,
            (
                "SELECT relrowsecurity AND relforcerowsecurity AS protected FROM pg_class "
                "WHERE oid='public.guidance_sessions'::regclass"
            ),
        )["protected"]
        if any(role.values()) or queue_allowed != (kind == "worker") or not protected:
            raise RuntimeError("Database runtime role or RLS boundary is not configured safely")
