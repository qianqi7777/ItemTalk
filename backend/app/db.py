"""SQLAlchemy 持久化基础设施；所有业务写入在显式事务内执行。"""

import json
from contextlib import contextmanager
from functools import lru_cache
from uuid import UUID

from sqlalchemy import create_engine, text

from app.config import settings


@lru_cache
def engine():
    # API (2 processes) + worker + migrator stay within the 20-connection budget.
    return create_engine(
        settings().db_url(), pool_size=3, max_overflow=1, pool_pre_ping=True, hide_parameters=True
    )


@contextmanager
def transaction():
    with engine().begin() as connection:
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
