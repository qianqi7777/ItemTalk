"""资料不足时不能附带无关FAQ引用；正常有据的否定事实仍可被引用。"""

from uuid import uuid4

import pytest

from app.adapters import ModelGateway
from app.grounding import evidence_insufficient
from app.worker import run_once
from tests.conftest import register_login
from tests.test_api import identified_product, post


@pytest.mark.parametrize(
    "answer",
    [
        "根据提供的证据，无法回答该问题。以下是相关FAQ[1]。",
        "证据中未包含这台设备的实际地址。有关开关机见[1]。",
        "**现有已核验资料不足，不能确认。相关说明[1]。",
        "根据提供的证据，无法直接得知您这台设备此刻滤芯的实际剩余百分比。相关FAQ[3]。",
    ],
)
def test_meta_refusal_is_not_a_citable_answer(answer):
    assert evidence_insufficient(answer)


@pytest.mark.parametrize(
    "answer",
    [
        "根据提供的证据，该设备不支持5GHz Wi-Fi[1]。",
        "设备充满约需要3.5小时[1]。资料未说明其他条件。",
        "根据资料，设备关闭后无法使用触摸面板[1]。",
        "根据资料，在无法确认电池状态时，先查看指示灯[1]。",
    ],
)
def test_supported_negative_facts_are_not_discarded(answer):
    assert not evidence_insufficient(answer)


@pytest.mark.postgres
def test_json_and_sse_refuse_unrelated_citations_without_extra_judge(client, monkeypatch):
    register_login(client)
    product = identified_product(client)
    assert run_once()
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()
    answer = "根据提供的证据，无法回答该问题。这里有其他操作说明[1]。"
    monkeypatch.setattr(ModelGateway, "answer", lambda *args, **kwargs: answer)
    monkeypatch.setattr(ModelGateway, "answer_stream", lambda *args, **kwargs: iter([answer]))

    def no_judge(*args, **kwargs):
        raise AssertionError("Refusal must not validate unrelated citations")

    monkeypatch.setattr(ModelGateway, "assess_answer", no_judge)
    result = post(
        client,
        f"/conversations/{conversation['id']}/messages",
        {"content": "设备的实际地址是什么？"},
    )
    assert result.status_code == 201 and result.json()["citations"] == []
    assert "资料不足" in result.json()["content"]
    streamed = client.post(
        f"/api/v1/conversations/{conversation['id']}/stream",
        json={"content": "设备的实际地址是什么？"},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert streamed.status_code == 200 and "event: citation" not in streamed.text
    assert "资料不足" in streamed.text
    assert "这里有其他操作说明" not in streamed.text


def test_http_metrics_distinguish_stream_interruption_from_200(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from app import operations

    captured = []
    monkeypatch.setattr(operations, "settings", lambda: SimpleNamespace(v2_enabled=True))
    monkeypatch.setattr(operations, "metric", lambda *args, **kwargs: captured.append(args))

    async def interrupted(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise asyncio.CancelledError()

    async def send(message):
        pass

    scope = {"type": "http", "method": "POST", "route": SimpleNamespace(path="/stream")}
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(operations.MetricsMiddleware(interrupted)(scope, None, send))
    assert len(captured) == 1 and captured[0][2] == "interrupted"
