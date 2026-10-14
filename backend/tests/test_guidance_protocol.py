"""真实国内Agent协议回归：先强制取证，再用JSON模式规划；普通实验回答保持自由文本。"""

import json

import pytest
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from app.adapters import ModelGateway
from app.agent_probe import GatewayChatModel
from app.config import settings
from app.guidance_agent import GuidanceChatModel, parse_plan


def test_guidance_requires_evidence_before_structured_final_answer(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "dashscope")
    settings.cache_clear()
    monkeypatch.setattr(ModelGateway, "budget", lambda *args: None)
    requests = []
    plan = {
        "riskLevel": "low",
        "steps": [
            {
                "instruction": "按住电源键2秒。",
                "successCriteria": "蓝灯熄灭",
                "riskLevel": "low",
                "preconditions": [],
                "safetyNotes": [],
                "citationRanks": [1],
            }
        ],
    }

    def respond(self, role, endpoint, payload):
        requests.append(payload)
        if payload.get("tool_choice") == {
            "type": "function",
            "function": {"name": "read_evidence"},
        }:
            message = {
                "content": None,
                "tool_calls": [
                    {
                        "id": "read-one",
                        "type": "function",
                        "function": {
                            "name": "read_evidence",
                            "arguments": '{"question":"如何关机"}',
                        },
                    }
                ],
            }
        else:
            content = json.dumps(plan)
            # 实测tools声明仍会让百炼返回围栏，即使请求JSON模式。
            content = f"```json\n{content}\n```"
            message = {"content": content}
        return {"choices": [{"message": message}]}

    monkeypatch.setattr(ModelGateway, "request", respond)

    @tool
    def read_evidence(question: str) -> str:
        """Read verified evidence for the current product."""
        return "[]"

    try:
        model = GuidanceChatModel().bind_tools([read_evidence])
        messages = [
            SystemMessage(content="只输出JSON计划，先检索资料"),
            HumanMessage(content="关机"),
        ]
        first = model.invoke(messages)
        assert first.tool_calls[0]["name"] == "read_evidence"
        messages += [
            first,
            ToolMessage(
                content='[{"rank":1,"content":"按住电源键2秒关机"}]', tool_call_id="read-one"
            ),
        ]
        final = model.invoke(messages)
        assert parse_plan(final.content).model_dump() == plan
        assert requests[0].get("response_format") is None
        assert requests[1]["response_format"] == {"type": "json_object"}
        assert "tool_choice" not in requests[1]
        assert all(
            [entry["function"]["name"] for entry in r["tools"]] == ["read_evidence"]
            for r in requests
        )
    finally:
        settings.cache_clear()


@pytest.mark.parametrize(
    "body",
    [
        '说明\n```json\n{"riskLevel":"high","steps":[]}\n```',
        '```json\n{"riskLevel":"high","steps":[]}\n```\n解释',
        '```python\n{"riskLevel":"high","steps":[]}\n```',
        '```json\n{"riskLevel":"high","steps":[]}\n```\n```json\n{}\n```',
        '{"riskLevel":"high","steps":[],"execute":"arbitrary command"}',
        '```json\n{"riskLevel":"high","steps":[],"execute":"arbitrary command"}\n```',
        "x" * 20001,
    ],
)
def test_plan_envelope_never_accepts_extra_text_or_unknown_fields(body):
    with pytest.raises(ValueError):
        parse_plan(body)


def test_plan_envelope_accepts_bounded_json_without_loosening_schema():
    value = '{"riskLevel":"high","steps":[]}'
    assert parse_plan(value) == parse_plan("```json\n" + value + "\n```")


def test_generic_gateway_still_accepts_free_text(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "dashscope")
    settings.cache_clear()
    monkeypatch.setattr(ModelGateway, "budget", lambda *args: None)
    seen = []

    def respond(self, role, endpoint, payload):
        seen.append(payload)
        return {"choices": [{"message": {"content": "工具实验已完成"}}]}

    monkeypatch.setattr(ModelGateway, "request", respond)
    try:
        result = GatewayChatModel().invoke([HumanMessage(content="读取资料")])
        assert result.content == "工具实验已完成"
        assert "response_format" not in seen[0] and "tool_choice" not in seen[0]
    finally:
        settings.cache_clear()
