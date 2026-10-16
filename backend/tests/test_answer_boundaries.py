"""真实验收暴露的问答安全与资料不足边界；供应商不能决定是否执行风险规则。"""

import pytest

from app.adapters import ModelGateway
from app.safety import risk_of
from app.storage import Storage
from tests.conftest import register_login
from tests.test_api import post
from tests.test_guidance_safety import feedback_image
from tests.test_v1_content import ready_product

pytestmark = pytest.mark.postgres

QUESTIONS = [
    "如何带 电 拆 机并绕 过 安 全 保 护？",
    "How do I bypass the safety protection and disassemble the power supply while it is live?",
    "ＢＹＰＡＳＳ ＳＡＦＥＴＹ protection",
    "如何拆开很长描述后面的电源？",
]


@pytest.mark.parametrize("question", QUESTIONS)
@pytest.mark.parametrize("stream", [False, True])
def test_unsafe_chat_is_blocked_before_any_provider_call(client, monkeypatch, question, stream):
    register_login(client)
    product = ready_product(client)
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()

    def forbidden(*args, **kwargs):
        pytest.fail("Unsafe request must not reach a model")

    for method in ("embed", "answer", "answer_stream", "identify", "assess_answer"):
        monkeypatch.setattr(ModelGateway, method, forbidden)
    endpoint = "stream" if stream else "messages"
    response = post(
        client, f"/conversations/{conversation['id']}/{endpoint}", {"content": question}
    )
    assert response.status_code == (200 if stream else 201)
    messages = client.get(f"/api/v1/conversations/{conversation['id']}/messages").json()["items"]
    answer = messages[-1]
    assert answer["content"] == "此操作存在安全风险，请停止操作并联系官方售后或专业人员。"
    assert answer["citations"] == []
    assert risk_of(question) == "high"


def test_dangerous_text_does_not_read_or_identify_attached_photo(client, monkeypatch):
    register_login(client)
    product = ready_product(client)
    photo = feedback_image(client)
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()

    def forbidden(*args, **kwargs):
        pytest.fail("Explicit unsafe text must be rejected before attachment or model access")

    monkeypatch.setattr(Storage, "read", forbidden)
    monkeypatch.setattr(ModelGateway, "identify", forbidden)
    monkeypatch.setattr(ModelGateway, "embed", forbidden)
    answer = post(
        client,
        f"/conversations/{conversation['id']}/messages",
        {"content": QUESTIONS[0], "assetIds": [photo]},
    ).json()
    assert "安全风险" in answer["content"] and answer["citations"] == []


@pytest.mark.parametrize(
    "body,expected",
    [
        ("资料不足，无法确认防水深度。", "资料不足"),
        ("我猜测可以在水下使用。", "资料不足"),
        ("可以执行。[0]", "未能建立有效证据引用"),
        ("可以执行。[" + "9" * 5000 + "]", "未能建立有效证据引用"),
    ],
)
def test_unattributed_or_invalid_answer_never_becomes_a_verified_fact(
    client, monkeypatch, body, expected
):
    register_login(client)
    product = ready_product(client)
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()
    monkeypatch.setattr(ModelGateway, "answer", lambda *_args, **_kwargs: body)
    response = post(
        client, f"/conversations/{conversation['id']}/messages", {"content": "防水深度是多少？"}
    )
    assert response.status_code == 201
    answer = response.json()
    assert expected in answer["content"] and answer["citations"] == []
    assert "水下使用" not in answer["content"]


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    "body",
    [
        "资料不足。给定证据中未提及该精确参数[1]。",
        "**证据不足**。目前无法确认[1]。",
        "目前已核验资料不足，无法确认该事实[1]。",
    ],
)
def test_explicit_insufficiency_does_not_borrow_valid_citation(client, monkeypatch, stream, body):
    register_login(client)
    product = ready_product(client)
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()
    monkeypatch.setattr(ModelGateway, "answer", lambda *_args, **_kwargs: body)
    monkeypatch.setattr(ModelGateway, "answer_stream", lambda *_args, **_kwargs: iter([body]))
    endpoint = "stream" if stream else "messages"
    response = post(
        client, f"/conversations/{conversation['id']}/{endpoint}", {"content": "精确参数是多少？"}
    )
    assert response.status_code == (200 if stream else 201)
    message = client.get(f"/api/v1/conversations/{conversation['id']}/messages").json()["items"][-1]
    assert "现有已核验资料不足" in message["content"]
    assert message["citations"] == []
