"""长手册拒绝内容的输出约束不改变证据、个人上下文或引用。"""

import json

import pytest

from app.adapters import ModelGateway


@pytest.mark.parametrize("coverage", [False, True])
@pytest.mark.parametrize("memories", [None, [{"style": "简短表达"}]])
def test_real_chapter_payload_requests_fixed_refusal_and_preserves_inputs(
    monkeypatch, coverage, memories
):
    gateway = ModelGateway()
    gateway.config = gateway.config.model_copy(update={"model_provider": "dashscope"})
    monkeypatch.setattr(gateway, "budget", lambda _subject: None)
    captured = []
    body = "请停止操作并联系官方专业支持。[1]"

    def request(role, endpoint, payload):
        captured.append((role, endpoint, payload))
        return {"choices": [{"message": {"content": body}}]}

    monkeypatch.setattr(gateway, "request", request)
    evidence = [
        {"rank": 1, "content": "已核验来源原文"},
        {"rank": 2, "content": "另一页的独立核验事实"},
    ]
    assert (
        gateway.chapter_answer(
            "逐段归纳全部资料", evidence, "manual:example", memories=memories, coverage=coverage
        )
        == body
    )
    assert len(captured) == 1
    role, endpoint, payload = captured[0]
    assert (role, endpoint) == ("chat", "/chat/completions")
    user_input = json.loads(payload["messages"][1]["content"])
    assert user_input["question"].startswith("逐段归纳全部资料\n")
    assert "请停止操作并联系官方专业支持。" in user_input["question"]
    assert "不要复述或列举危险动作名称" in user_input["question"]
    assert user_input["evidence"] == evidence
    assert user_input["userMemory"] == (memories or [])
    if coverage:
        assert "每项最多80字" in user_input["question"]
        assert "完整编号清单：[1]、[2]。" in user_input["question"]
    else:
        assert "完整编号清单" not in user_input["question"]
