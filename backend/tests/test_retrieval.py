"""零关键词信号不能按随机UUID加分；重复型号不能代替问题的操作意图。"""

from uuid import UUID

import pytest

from app.db import execute, many, one, transaction
from app.services.chat import retrieve
from app.services.knowledge import persist_document, publish_chunks
from tests.conftest import register_login
from tests.test_v1_content import ready_product

pytestmark = pytest.mark.postgres


def test_zero_lexical_signal_uses_cosine_order_with_stable_ties(client):
    register_login(client)
    product = ready_product(client)
    with transaction() as c:
        model = one(
            c, "SELECT * FROM product_models WHERE id=:id", id=UUID(product["productModelId"])
        )
        model["model_code"] = "DEMO-100"
        pages = [(i, "DEMO-100 public common header") for i in range(1, 6)]
        version = persist_document(
            c,
            model,
            "https://example.invalid/zero-signal",
            "signal regression",
            "text/plain",
            b"bounded-fixture",
            pages,
            True,
        )
        # 向量越接近越相关。所有词法相似度都为0，让较远片段拥有较小UUID。
        vectors = [[1.0, float(i) / 100] + [0.0] * 1022 for i in range(1, 6)]
        publish_chunks(c, model, version, pages, vectors)
        rows = many(
            c,
            "SELECT id,chunk_no FROM knowledge_chunks "
            "WHERE document_version_id=:v ORDER BY chunk_no",
            v=version["id"],
        )
        for r in rows:
            execute(
                c,
                "UPDATE knowledge_chunks SET id=:new WHERE id=:old",
                new=UUID(int=100 - r["chunk_no"]),
                old=r["id"],
            )
        execute(
            c,
            "UPDATE knowledge_chunks SET verification_status='revoked' "
            "WHERE document_version_id<>:v",
            v=version["id"],
        )
    result = retrieve(model["id"], "unmatchedzzzz", [1.0] + [0.0] * 1023, limit=5)
    assert [r["chunk_no"] for r in result] == [1, 2, 3, 4, 5]
    assert all(r["text_score"] == 0 for r in result)
    assert result == retrieve(model["id"], "unmatchedzzzz", [1.0] + [0.0] * 1023, limit=5)


def test_repeated_lexical_notices_do_not_evict_best_semantic_candidates(client):
    register_login(client)
    product = ready_product(client)
    with transaction() as connection:
        model = one(
            connection,
            "SELECT * FROM product_models WHERE id=:id",
            id=UUID(product["productModelId"]),
        )
        pages = [(1, "manual alpha"), (2, "manual beta")] + [
            (number, "pairing pairing pairing repeated notice") for number in range(3, 15)
        ]
        version = persist_document(
            connection,
            model,
            "https://example.invalid/repeated-notices",
            "notice regression",
            "text/plain",
            b"semantic-reserve-fixture",
            pages,
            True,
        )
        vectors = [[1.0, float(number) / 10] + [0.0] * 1022 for number, _ in pages]
        publish_chunks(connection, model, version, pages, vectors)
        execute(
            connection,
            "UPDATE knowledge_chunks SET verification_status='revoked' "
            "WHERE document_version_id<>:v",
            v=version["id"],
        )
    vector = [1.0] + [0.0] * 1023
    result = retrieve(model["id"], "pairing", vector, limit=5)
    assert len(result) == 5
    assert [row["chunk_no"] for row in result[:2]] == [1, 2]
    assert all(row["text_score"] > 0 for row in result[2:])
    assert retrieve(model["id"], "pairing", vector, limit=1)[0]["chunk_no"] == 1


@pytest.mark.parametrize("stream", [False, True])
def test_chat_can_use_verified_evidence_after_fifth_candidate(client, monkeypatch, stream):
    """长说明书的直接答案排第六时，JSON和SSE不能在生成前丢掉它。"""
    from app.adapters import ModelGateway
    from tests.test_api import post

    register_login(client)
    product = ready_product(client)
    fact = "Charging power state: unplug USB before starting."
    pages = [
        (number, fact if number == 6 else f"General manual section {number}")
        for number in range(1, 13)
    ]
    with transaction() as connection:
        model = one(
            connection,
            "SELECT * FROM product_models WHERE id=:id",
            id=UUID(product["productModelId"]),
        )
        version = persist_document(
            connection,
            model,
            "https://example.invalid/late-evidence",
            "late evidence regression",
            "text/plain",
            b"late-evidence-fixture",
            pages,
            True,
        )
        publish_chunks(
            connection,
            model,
            version,
            pages,
            [[1.0, number / 100] + [0.0] * 1022 for number, _ in pages],
        )
        execute(
            connection,
            "UPDATE knowledge_chunks SET verification_status='revoked' "
            "WHERE document_version_id<>:v",
            v=version["id"],
        )

    seen = []

    def answer(_self, question, evidence, subject, memories=None):
        # 向量/词法检索真实执行；仅供应商生成使用固定输出，不声称质量验收。
        seen.append(evidence)
        relevant = next((row for row in evidence if row["content"] == fact), None)
        assert relevant is not None, "Direct supporting evidence was truncated before generation"
        return f"请拔下USB充电线后再开机。[{relevant['rank']}]"

    monkeypatch.setattr(ModelGateway, "answer", answer)
    monkeypatch.setattr(
        ModelGateway,
        "answer_stream",
        lambda self, *args, **kwargs: iter([answer(self, *args, **kwargs)]),
    )
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()
    endpoint = "stream" if stream else "messages"
    response = post(
        client, f"/conversations/{conversation['id']}/{endpoint}", {"content": "充电时能开机吗？"}
    )
    assert seen and any(row["content"] == fact for row in seen[-1])
    assert response.status_code == (200 if stream else 201)
    messages = client.get(f"/api/v1/conversations/{conversation['id']}/messages").json()["items"]
    result = messages[-1]
    assert "拔下USB充电线" in result["content"]
    assert len(result["citations"]) == 1
    assert result["citations"][0]["pageStart"] == 6
