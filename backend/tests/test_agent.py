"""P0 实验仅验证工具约束与持久化恢复；真实模型能力另行 smoke。"""

from uuid import UUID

import pytest
from langgraph.types import Command

from app.agent_probe import restricted_agent, saver
from app.db import one, transaction
from app.errors import DomainError
from tests.conftest import register_login
from tests.test_api import identified_product

pytestmark = pytest.mark.postgres


def test_checkpoint_interrupt_resume_and_ownership(client):
    user_id = UUID(register_login(client)["id"])
    instance_id = UUID(identified_product(client)["id"])
    config = {"configurable": {"thread_id": f"probe:{user_id}:{instance_id}"}, "recursion_limit": 8}
    with saver() as checkpointer:
        agent = restricted_agent(checkpointer, user_id, instance_id)
        first = agent.invoke(
            {"messages": [{"role": "user", "content": "读取当前产品证据"}]}, config
        )
        assert first["__interrupt__"]
    # 重建进程内图对象后仍能加载数据库 checkpoint，而非只测试内存恢复。
    with saver() as checkpointer:
        agent = restricted_agent(checkpointer, user_id, instance_id)
        resumed = agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), config)
        assert "恢复完成" in resumed["messages"][-1].content


def test_agent_rejects_foreign_instance_before_loading_checkpoint(client):
    register_login(client)
    instance_id = UUID(identified_product(client)["id"])
    with transaction() as connection:
        other = one(
            connection,
            (
                "INSERT INTO users(login,login_normalized,hashed_password) VALUES "
                "('foreign','foreign','test-hash') RETURNING id"
            ),
        )["id"]
    with saver() as checkpointer, pytest.raises(DomainError):
        restricted_agent(checkpointer, other, instance_id)
