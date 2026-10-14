"""评测工具使用受控 fixture 验证指标计算，不宣称真实检索质量。"""

from contextlib import nullcontext
from uuid import UUID

import pytest

from app.db import one, transaction
from app.evaluate import GoldCase, answer_metrics, contains_fact, evaluate
from app.worker import run_once
from tests.conftest import register_login
from tests.test_api import identified_product

pytestmark = pytest.mark.postgres


def test_gold_set_reports_expected_evidence_page_and_region(client):
    register_login(client)
    product = identified_product(client)
    assert run_once()
    with transaction() as connection:
        version = one(connection, "SELECT id FROM document_versions")["id"]
    case = GoldCase(
        productModelId=UUID(product["productModelId"]),
        question="如何开机",
        expectedDocumentVersionIds=[version],
        expectedPage=1,
        expectedRegion="CN",
    )
    report = evaluate([case])
    assert report == {
        "cases": 1,
        "completedCases": 1,
        "evaluationStatus": "completed",
        "recallAt10": 1,
        "chunkRecallAt10": None,
        "meanReciprocalRankAt10": None,
        "pageAccuracy": 1,
        "applicabilityAccuracy": 1,
        "emptyRetrievalAccuracy": None,
        "citationValidity": None,
        "citationTargetAccuracy": None,
        "factAccuracy": None,
        "forbiddenFactAvoidance": None,
        "refusalAccuracy": None,
    }


def test_fact_matching_does_not_confuse_button_durations():
    assert contains_fact("电源键按住约 5 秒", "5秒")
    assert not contains_fact("等待15秒", "5秒")
    assert not contains_fact("等待3.5秒", "5秒")
    assert not contains_fact("配对最多8个设备", "配对最多9个设备")


def test_refusal_requires_actual_answer_and_no_unsupported_citations():
    case = GoldCase(productModelId=UUID(int=1), question="未知功能？", expectInsufficient=True)
    evidence = [{"id": UUID(int=2), "content": "不相关的开机说明"}]
    assert answer_metrics(case, evidence, "资料不足，无法确认该功能。")["refusalAccuracy"]
    assert not answer_metrics(case, evidence, "资料不足，但可以开启该功能[1]。")["refusalAccuracy"]
    assert not answer_metrics(case, evidence, "功能最大深度5米。")["refusalAccuracy"]
    assert not answer_metrics(case, evidence, "资料不足[" + "9" * 5000 + "]")["refusalAccuracy"]


def test_citations_and_facts_use_annotated_chunks_not_merely_same_document():
    target, unrelated = UUID(int=2), UUID(int=3)
    case = GoldCase(
        productModelId=UUID(int=1),
        question="如何进入配对模式？",
        expectedKnowledgeChunkIds=[target],
        expectedFacts=[["5秒", "五秒"]],
        forbiddenFacts=["3秒"],
    )
    evidence = [{"id": unrelated}, {"id": target}]
    good = answer_metrics(case, evidence, "按住电源键5秒。[2]")
    assert (
        good["citationTargetAccuracy"] and good["factAccuracy"] and good["forbiddenFactAvoidance"]
    )
    bad = answer_metrics(case, evidence, "按住电源键3秒。[1]")
    assert bad["citationValidity"] and not bad["citationTargetAccuracy"] and not bad["factAccuracy"]
    assert not bad["forbiddenFactAvoidance"]
    assert not answer_metrics(case, evidence, "按住5秒。[0]")["citationValidity"]


def test_inconsistent_gold_labels_are_rejected():
    with pytest.raises(ValueError):
        GoldCase(
            productModelId=UUID(int=1),
            question="未知？",
            expectInsufficient=True,
            expectedKnowledgeChunkIds=[UUID(int=2)],
        )
    with pytest.raises(ValueError):
        GoldCase(productModelId=UUID(int=1), question="问题", expectedFacts=[[]])


def test_answers_are_optional_and_empty_retrieval_is_not_refusal(client, monkeypatch):
    from app.adapters import ModelGateway

    register_login(client)
    product = identified_product(client)
    assert run_once()
    with transaction() as connection:
        chunk = one(connection, "SELECT id,document_version_id FROM knowledge_chunks")
    positive = GoldCase(
        productModelId=UUID(product["productModelId"]),
        question="如何开机？",
        expectedDocumentVersionIds=[chunk["document_version_id"]],
        expectedKnowledgeChunkIds=[chunk["id"]],
        expectedRegion="CN",
        expectedFacts=[["3秒"]],
    )
    negative = GoldCase(
        productModelId=positive.productModelId,
        question="设备可以水下拍摄吗？",
        expectedRegion="CN",
        expectInsufficient=True,
    )
    calls = []

    def answer(gateway, question, *args, **kwargs):
        calls.append(question)
        return "证据不足，无法确认。" if "水下" in question else "按住电源键3秒。[1]"

    monkeypatch.setattr(ModelGateway, "answer", answer)
    assert evaluate([positive, negative])["refusalAccuracy"] is None
    assert calls == []
    details = []
    report = evaluate([positive, negative], generate_answers=True, details=details)
    assert report["chunkRecallAt10"] == report["meanReciprocalRankAt10"] == 1
    assert report["factAccuracy"] == report["citationTargetAccuracy"] == 1
    assert report["refusalAccuracy"] == 1 and report["emptyRetrievalAccuracy"] == 0
    assert len(details) == len(calls) == 2


def test_quota_interrupt_keeps_only_completed_cases_and_stops_spending(monkeypatch):
    from app.adapters import ModelGateway
    from app.errors import DomainError

    cases = [GoldCase(productModelId=UUID(int=1), question=f"开机问题{n}") for n in range(3)]
    calls = []

    def embed(gateway, texts, subject):
        calls.append(texts)
        if len(calls) == 2:
            raise DomainError(429, "RATE_LIMITED", "private quota explanation")
        return [[1.0] + [0.0] * 1023]

    monkeypatch.setattr(ModelGateway, "embed", embed)
    monkeypatch.setattr(ModelGateway, "budget", lambda *args: None)
    monkeypatch.setattr("app.evaluate.transaction", lambda: nullcontext(object()))
    monkeypatch.setattr(
        "app.evaluate.one", lambda *args, **kwargs: {"region": None, "hardware_revision": None}
    )
    monkeypatch.setattr(
        "app.evaluate.retrieve",
        lambda *args, **kwargs: [
            {
                "id": UUID(int=2),
                "document_version_id": UUID(int=3),
                "content": "按住电源键3秒",
                "url": "https://example.invalid/test",
                "page_start": 1,
                "page_end": 1,
            }
        ],
    )
    details = []
    result = evaluate(cases, generate_answers=True, details=details)
    assert result["cases"] == 3 and result["completedCases"] == 1
    assert result["evaluationStatus"] == "interrupted" and result["failedCaseIndex"] == 2
    assert result["errorCode"] == "RATE_LIMITED" and "private" not in str(result)
    assert len(calls) == 2 and len(details) == 1
    assert details[0]["case"]["question"] == cases[0].question
