"""P0 受限 Agent 实验：只开放证据读取工具，验证 checkpoint 与人工中断恢复。

此入口不暴露 HTTP 路由。数据库归属在工具执行时重新检查，默认文件、shell、
子 Agent 工具从模型可见列表剔除，并在工具执行边界再次拒绝。
"""

import json
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from deepagents.middleware.filesystem import FilesystemPermission
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.conninfo import make_conninfo
from sqlalchemy.engine import make_url

from app.adapters import ModelGateway
from app.config import settings
from app.db import actor_id, transaction
from app.errors import DomainError
from app.services.chat import retrieve
from app.services.products import owned

ALLOWED_TOOLS = frozenset({"read_evidence"})


def checkpoint_conninfo():
    """驱动 DSN 仅在内存构造，禁止把含密码的 DSN 放到命令参数或日志。"""
    url = (
        make_url(settings().db_url())
        if isinstance(settings().db_url(), str)
        else settings().db_url()
    )
    return make_conninfo(
        host=url.host,
        port=url.port,
        user=url.username,
        password=url.password or "",
        dbname=url.database,
    )


class RestrictedTools(AgentMiddleware):
    """同时限制模型声明和执行；模型伪造任意工具名也不能绕过领域服务。"""

    def wrap_model_call(self, request, handler):
        tools = [entry for entry in request.tools if getattr(entry, "name", None) in ALLOWED_TOOLS]
        return handler(request.override(tools=tools))

    def wrap_tool_call(self, request, handler):
        if request.tool_call["name"] not in ALLOWED_TOOLS:
            return ToolMessage(content="工具不在允许列表中", tool_call_id=request.tool_call["id"])
        return handler(request)


class GatewayChatModel(BaseChatModel):
    """将国内兼容协议隔离到网关，不引入供应商默认的执行工具。"""

    model_name: str = "itemtalk:restricted"
    budget_subject: str = "agent-probe"
    bound_tools: list[Any] = []
    require_evidence: bool = False
    json_output: bool = False

    @property
    def _llm_type(self):
        return "itemtalk"

    def bind_tools(self, tools, **kwargs):
        return self.model_copy(update={"bound_tools": list(tools)})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        gateway = kwargs.pop("_gateway", None) or ModelGateway()
        if gateway.config.model_provider == "mock":
            if any(isinstance(message, ToolMessage) for message in messages):
                answer = AIMessage(content="受限证据工具已执行，checkpoint 恢复完成")
            else:
                answer = AIMessage(
                    content="",
                    tool_calls=[{"name": "read_evidence", "args": {}, "id": "probe-read"}],
                )
            return ChatResult(generations=[ChatGeneration(message=answer)])
        from langchain_core.utils.function_calling import convert_to_openai_tool

        gateway.budget(self.budget_subject)
        wire = []
        for message in messages:
            role = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}[
                message.type
            ]
            row = {"role": role, "content": message.content}
            if isinstance(message, ToolMessage):
                row["tool_call_id"] = message.tool_call_id
            if isinstance(message, AIMessage) and message.tool_calls:
                row["tool_calls"] = [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {"name": call["name"], "arguments": json.dumps(call["args"])},
                    }
                    for call in message.tool_calls
                ]
            wire.append(row)
        payload = {
            "model": gateway.config.chat_model,
            "messages": wire,
            "max_tokens": 1000,
            "tools": [convert_to_openai_tool(entry) for entry in self.bound_tools],
        }
        if self.require_evidence and not any(
            isinstance(message, ToolMessage) for message in messages
        ):
            if not any(tool["function"]["name"] == "read_evidence" for tool in payload["tools"]):
                raise DomainError(502, "UPSTREAM_MODEL_ERROR", "受限证据工具不可用")
            # 提示词不能保证模型真的检索；首轮必须走唯一允许的证据工具，防止凭空计划。
            payload["tool_choice"] = {"type": "function", "function": {"name": "read_evidence"}}
        elif self.json_output:
            payload["response_format"] = {"type": "json_object"}
        if gateway.config.model_provider == "dashscope":
            payload["enable_thinking"] = False
        result = gateway.request("chat", "/chat/completions", payload)["choices"][0]["message"]
        calls = [
            {
                "id": call["id"],
                "name": call["function"]["name"],
                "args": json.loads(call["function"]["arguments"]),
            }
            for call in result.get("tool_calls", [])
        ]
        answer = AIMessage(content=result.get("content") or "", tool_calls=calls)
        return ChatResult(generations=[ChatGeneration(message=answer)])


def restricted_agent(checkpointer, user_id: UUID, instance_id: UUID):
    """稳定 checkpoint ID 必须包含本人实例，不能接收任意跨用户 thread_id。"""
    with transaction() as connection:
        owned(connection, "instances", instance_id, user_id)

    @tool
    def read_evidence(question: str = "当前产品的使用说明和硬件规格") -> str:
        """根据具体问题检索当前本人实例已核验的证据，保留来源和页码。"""
        if not question.strip() or len(question) > 1000:
            raise DomainError(422, "VALIDATION_ERROR", "证据检索问题长度无效")
        with transaction() as connection:
            instance = owned(connection, "instances", instance_id, user_id)
        vector = ModelGateway().embed([question], user_id)[0]
        chunks = retrieve(instance["product_model_id"], question, vector)
        rows = [
            {
                "rank": index,
                "content": chunk["content"],
                "url": chunk["url"],
                "pageStart": chunk["page_start"],
                "pageEnd": chunk["page_end"],
            }
            for index, chunk in enumerate(chunks, 1)
        ]
        return json.dumps(rows, ensure_ascii=False)

    return create_deep_agent(
        model=GatewayChatModel(),
        tools=[read_evidence],
        backend=StateBackend(),
        permissions=[
            FilesystemPermission(operations=["read", "write"], paths=["/**"], mode="deny")
        ],
        middleware=[RestrictedTools()],
        checkpointer=checkpointer,
        interrupt_on={"read_evidence": True},
        system_prompt=(
            "你只能调用 read_evidence，检索 question 参数应为用户当前具体问题。"
            "仅根据工具返回的证据回答；不要把局部频段天线数量当成总数。"
            "证据不足就明确说明。禁止执行命令或文件操作。工具执行前等待确认。"
        ),
    )


@contextmanager
def saver():
    # CLI 探针连接为独立 autocommit 连接，显式绑定同一身份；关闭连接后不复用。
    with PostgresSaver.from_conn_string(checkpoint_conninfo()) as checkpointer:
        checkpointer.conn.execute(
            "SELECT set_config('itemtalk.user_id',%s,false)", (str(actor_id.get() or ""),)
        )
        yield checkpointer
