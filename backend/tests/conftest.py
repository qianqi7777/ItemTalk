"""测试默认隔离数据库；TEST_DATABASE_URL 必须显式指向可重建的测试库。"""

import os

import pytest
from fastapi.testclient import TestClient

# 运行测试绝不继承真实供应商/COS 开关，避免开发终端带生产环境变量时付费调用。
os.environ.update(
    {
        "ENVIRONMENT": "test",
        "MODEL_PROVIDER": "mock",
        "AUTH_COOKIE_SECURE": "false",
        "AUTH_COOKIE_PATH": "/",
        "APP_PUBLIC_URL": "http://localhost:5173/",
        "RATE_LIMIT_PER_MINUTE": "10000",
        "DAILY_MODEL_CALL_LIMIT": "10000",
        "STORAGE_BACKEND": "local",
        "STT_PROVIDER": "",
        "STT_API_KEY": "",
    }
)

from app.config import settings  # noqa: E402
from app.db import engine, execute, transaction  # noqa: E402
from app.main import app  # noqa: E402

HEADERS = {"Origin": "http://localhost:5173", "X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
def database(tmp_path, monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL integration")
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("STORAGE_DIRECTORY", str(tmp_path / "assets"))
    settings.cache_clear()
    engine.cache_clear()
    with transaction() as connection:
        # 测试库迁移由 CI/本地脚本执行；清空数据保留已验证的真实约束。
        execute(connection, "TRUNCATE users,brands,sources,usage_counters CASCADE")
    yield
    engine().dispose()
    engine.cache_clear()
    settings.cache_clear()


@pytest.fixture
def client(database):
    with TestClient(app, base_url="http://localhost:5173", headers=HEADERS) as client:
        yield client


def register_login(client, email="person@example.com"):
    credentials = {"email": email, "password": "test-password-123"}
    assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
    assert client.post("/api/v1/auth/login", json=credentials).status_code == 204
    return client.get("/api/v1/me").json()
