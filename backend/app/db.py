"""SQLAlchemy 持久化基础设施；所有业务写入在显式事务内执行。"""

import json
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from uuid import UUID

from sqlalchemy import create_engine, text

from app.config import settings

# 身份来自已验证的 HTTP JWT 或已领取的 Worker 任务，不接收客户端指定的用户头。
actor_id = ContextVar("database_actor", default=None)


@contextmanager
def actor(user_id):
    token = actor_id.set(UUID(str(user_id)) if user_id else None)
    try:
        yield
    finally:
        actor_id.reset(token)


@lru_cache
def engine():
    # API (2 processes) + worker + migrator stay within the 20-connection budget.
    return create_engine(
        settings().db_url(), pool_size=3, max_overflow=1, pool_pre_ping=True, hide_parameters=True
    )


@contextmanager
def transaction():
    with engine().begin() as connection:
        # SET LOCAL 只在本事务有效；连接池复用、提交和回滚都不能遗留上一用户身份。
        execute(
            connection,
            "SELECT set_config('itemtalk.user_id',:actor,true)",
            actor=str(actor_id.get() or ""),
        )
        yield connection


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
