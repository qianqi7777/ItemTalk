"""Exercise persisted provenance and usable chat against an empty product knowledge base."""

import json

import pytest

from app.adapters import ModelGateway
from tests.conftest import register_login
from tests.test_api import identified_product, post
from tests.test_v1_content import ready_product

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("stream", [False, True])
def test_empty_knowledge_gives_generic_help_and_keeps_label_after_reload(
    client, monkeypatch, stream
):
    register_login(client)
    product = identified_product(client)  # identified, but research has never run
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()

    def no_answer(*_args, **_kwargs):
        pytest.fail("No evidence must not trigger product-specific answer generation")

    for method in ("embed", "answer", "answer_stream", "assess_answer", "gap_intent"):
        monkeypatch.setattr(ModelGateway, method, no_answer)
    endpoint = "stream" if stream else "messages"
    response = post(
        client,
        f"/conversations/{conversation['id']}/{endpoint}",
        {"content": "蓝牙鼠标连接不上电脑怎么办？"},
    )
    assert response.status_code == (200 if stream else 201)
    saved = client.get(f"/api/v1/conversations/{conversation['id']}/messages").json()["items"][-1]
    assert saved["answerBasis"] == "general" and saved["citations"] == []
    assert "通用建议" in saved["content"] and "蓝牙设置" in saved["content"]
    if stream:
        tokens = []
        done = None
        for block in response.text.split("\n\n"):
            if "\ndata: " not in block:
                continue
            event, raw = block.split("\ndata: ", 1)
            if event == "event: token":
                tokens.append(json.loads(raw)["text"])
            elif event == "event: done":
                done = json.loads(raw)
        assert "".join(tokens) == saved["content"]
        recovered = client.get(
            f"/api/v1/conversations/{conversation['id']}/runs/{done['runId']}"
        ).json()
        assert recovered["message"] == saved and done["messageId"] == saved["id"]
    else:
        assert response.json() == saved


@pytest.mark.parametrize("question", ["怎么刷固件？", "如何恢复出厂设置？", "充电电压是多少？"])
def test_empty_knowledge_never_invents_sensitive_model_steps(client, question):
    register_login(client)
    product = identified_product(client)
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()
    answer = post(
        client, f"/conversations/{conversation['id']}/messages", {"content": question}
    ).json()
    assert answer["answerBasis"] == "insufficient"
    assert "需要" in answer["content"] and answer["citations"] == []


@pytest.mark.parametrize("rejection", ["insufficient", "unsupported"])
def test_irrelevant_existing_evidence_still_allows_labeled_low_risk_help(
    client, monkeypatch, rejection
):
    register_login(client)
    product = ready_product(client)
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()
    monkeypatch.setattr(
        ModelGateway,
        "answer",
        lambda *_args, **_kwargs: (
            "资料不足，无法回答。" if rejection == "insufficient" else "长按电源键开机。[1]"
        ),
    )
    monkeypatch.setattr(
        ModelGateway,
        "assess_answer",
        lambda *_args, **_kwargs: {
            "fullySupported": False,
            "citations": [{"rank": 1, "supported": False, "quote": ""}],
        },
    )
    answer = post(
        client,
        f"/conversations/{conversation['id']}/messages",
        {"content": "鼠标蓝牙连接不上电脑怎么办？"},
    ).json()
    assert answer["answerBasis"] == "general" and "蓝牙设置" in answer["content"]
    assert answer["citations"] == [] and "长按" not in answer["content"]
