"""检索 Gold Set 评测入口；数据集由运营核验，输出仅包含聚合指标。"""

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.adapters import ModelGateway
from app.config import settings
from app.db import one, transaction
from app.errors import DomainError
from app.services.chat import retrieve
from app.upstream import UpstreamError


class GoldCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    productModelId: UUID
    question: str = Field(min_length=1, max_length=10000)
    expectedDocumentVersionIds: list[UUID] = Field(default_factory=list, max_length=20)
    expectedKnowledgeChunkIds: list[UUID] = Field(default_factory=list, max_length=20)
    allowedCitationChunkIds: list[UUID] = Field(default_factory=list, max_length=50)
    expectedPage: int | None = Field(default=None, ge=1)
    expectedRegion: str | None = None
    expectedHardwareRevision: str | None = None
    expectInsufficient: bool = False
    expectedFacts: list[list[str]] = Field(default_factory=list, max_length=20)
    forbiddenFacts: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def valid_expectations(self):
        if self.expectInsufficient and (
            self.expectedDocumentVersionIds
            or self.expectedKnowledgeChunkIds
            or self.allowedCitationChunkIds
        ):
            raise ValueError("Insufficient cases cannot expect supporting evidence")
        if any(not group or len(group) > 20 for group in self.expectedFacts):
            raise ValueError("Expected facts require at least one alternative")
        terms = [term for group in self.expectedFacts for term in group] + self.forbiddenFacts
        if any(not term.strip() or len(term) > 200 for term in terms):
            raise ValueError("Expected fact terms must be nonempty and bounded")
        if self.allowedCitationChunkIds and not set(self.expectedKnowledgeChunkIds) <= set(
            self.allowedCitationChunkIds
        ):
            raise ValueError("Expected chunks must also be allowed citation targets")
        return self


def contains_fact(answer, term):
    """核验人员定义字面事实备选值；数字边界避免把15秒误判成5秒，不执行外部正则。"""
    answer, term = re.sub(r"\s+", "", answer).lower(), re.sub(r"\s+", "", term).lower()
    pattern = (r"(?<![\d.])" if term[0].isdigit() else "") + re.escape(term)
    if term[-1].isdigit():
        pattern += r"(?![\d.])"
    return bool(re.search(pattern, answer))


def answer_metrics(case, evidence, answer):
    """自动检查只证明标注事实和引用目标；不声称逐句语义蕴含或覆盖所有危险操作。"""
    raw_citations = re.findall(r"\[(\d+)\]", answer)
    cited = {int(rank) for rank in raw_citations if len(rank) <= 4}
    valid = (
        bool(raw_citations)
        and all(len(rank) <= 4 for rank in raw_citations)
        and all(1 <= rank <= len(evidence) for rank in cited)
    )
    targets = {evidence[rank - 1]["id"] for rank in cited} if valid else set()
    refusal = any(
        phrase in re.sub(r"\s+", "", answer)
        for phrase in (
            "证据不足",
            "资料不足",
            "无法确认",
            "没有足够",
            "没有相关",
            "未提供",
            "不能确认",
            "缺乏",
            "无法回答",
            "未说明",
            "无法从",
            "不包含",
            "没有提及",
        )
    )
    return {
        "citationValidity": valid if not case.expectInsufficient else None,
        "citationTargetAccuracy": (
            valid and targets <= set(case.allowedCitationChunkIds or case.expectedKnowledgeChunkIds)
            if case.expectedKnowledgeChunkIds or case.allowedCitationChunkIds
            else None
        ),
        "factAccuracy": (
            all(any(contains_fact(answer, term) for term in group) for group in case.expectedFacts)
            if case.expectedFacts
            else None
        ),
        "forbiddenFactAvoidance": (
            not any(contains_fact(answer, term) for term in case.forbiddenFacts)
            if case.forbiddenFacts
            else None
        ),
        # 证据不足的样例可仍检索到无关材料；必须实际看模型输出，不把非空检索当回答。
        "refusalAccuracy": refusal and not raw_citations if case.expectInsufficient else None,
    }


def evaluate(cases, generate_answers=False, details=None):
    """默认只评检索；显式生成回答才报告事实/引用/拒答，并可受限保存人工复核样例。"""
    recalls, chunks, reciprocal_ranks, pages, applicability, empty = [], [], [], [], [], []
    answers = []
    gateway = ModelGateway()
    completed = 0
    interruption = None
    try:
        for number, case in enumerate(cases, 1):
            try:
                vector = gateway.embed([case.question], "gold-set")[0]
                evidence = retrieve(case.productModelId, case.question, vector, limit=10)
                with transaction() as connection:
                    model = one(
                        connection,
                        "SELECT region,hardware_revision FROM product_models WHERE id=:id",
                        id=case.productModelId,
                    )
                answer = None
                metrics = None
                if generate_answers:
                    answer = gateway.answer(
                        case.question,
                        [
                            {"rank": index, "content": row["content"]}
                            for index, row in enumerate(evidence, 1)
                        ],
                        "gold-set",
                    )
                    metrics = answer_metrics(case, evidence, answer)
            except (DomainError, UpstreamError) as error:
                # 额度/供应商中断必须保留已完成样例，停止后续付费调用；失败样例不
                # 混入完整样例的分母，也不把未评完的报告伪装成整套验收通过。
                interruption = {
                    "failedCaseIndex": number,
                    "errorCode": error.code if isinstance(error, DomainError) else "UPSTREAM_ERROR",
                }
                break
            actual = {row["document_version_id"] for row in evidence}
            expected = set(case.expectedDocumentVersionIds)
            if expected:
                recalls.append(len(actual & expected) / len(expected))
            if case.expectedKnowledgeChunkIds:
                expected_chunks = set(case.expectedKnowledgeChunkIds)
                chunks.append(
                    len({r["id"] for r in evidence} & expected_chunks) / len(expected_chunks)
                )
                first = next(
                    (i for i, row in enumerate(evidence, 1) if row["id"] in expected_chunks), 0
                )
                reciprocal_ranks.append(1 / first if first else 0)
            if case.expectedPage:
                pages.append(
                    any(
                        row["document_version_id"] in expected
                        and row["page_start"] <= case.expectedPage <= row["page_end"]
                        for row in evidence
                    )
                )
            applicability.append(
                bool(model)
                and model["region"] == case.expectedRegion
                and model["hardware_revision"] == case.expectedHardwareRevision
            )
            if case.expectInsufficient:
                empty.append(not evidence)
            if metrics is not None:
                answers.append(metrics)
            completed += 1
            if details is not None:
                details.append(
                    {
                        "case": case.model_dump(mode="json"),
                        "answer": answer,
                        "evidence": [
                            {"id": str(row["id"]), "url": row["url"], "content": row["content"]}
                            for row in evidence
                        ],
                        "metrics": metrics,
                    }
                )
    finally:
        gateway.close()

    def average(values):
        return sum(values) / len(values) if values else None

    result = {
        "cases": len(cases),
        "completedCases": completed,
        "evaluationStatus": "interrupted" if interruption else "completed",
        "recallAt10": average(recalls),
        "chunkRecallAt10": average(chunks),
        "meanReciprocalRankAt10": average(reciprocal_ranks),
        "pageAccuracy": average(pages),
        "applicabilityAccuracy": average(applicability),
        "emptyRetrievalAccuracy": average(empty),
    }
    if interruption:
        result.update(interruption)
    for metric in (
        "citationValidity",
        "citationTargetAccuracy",
        "factAccuracy",
        "forbiddenFactAvoidance",
        "refusalAccuracy",
    ):
        result[metric] = average([row[metric] for row in answers if row[metric] is not None])
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--paid", action="store_true")
    parser.add_argument("--answers", action="store_true", help="Also make real/mock answer calls")
    parser.add_argument("--report", type=Path, help="Exclusive restricted file for manual review")
    args = parser.parse_args()
    if settings().model_provider != "mock" and not args.paid:
        parser.error("Real embedding/answer calls require --paid")
    if args.dataset.stat().st_size > 1024 * 1024:
        parser.error("Dataset exceeds size limit")
    data = args.dataset.read_bytes()
    try:
        rows = json.loads(data)
    except ValueError:
        parser.error("Dataset JSON is invalid")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 200:
        parser.error("Dataset requires 1 to 200 verified cases")
    try:
        cases = [GoldCase.model_validate(row) for row in rows]
    except ValueError:
        # 不把错误样例的原问题或输入值带到终端日志。
        parser.error("Dataset labels are invalid")
    if args.report and args.report.exists():
        parser.error("Report target already exists")
    details = [] if args.report else None
    result = evaluate(cases, args.answers, details)
    if args.report:
        descriptor = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as report:
            json.dump(
                {
                    "datasetSha256": hashlib.sha256(data).hexdigest(),
                    "provider": settings().model_provider,
                    "model": settings().chat_model,
                    "embeddingModel": settings().embedding_model,
                    "embeddingRevision": settings().embedding_revision,
                    "answersEvaluated": args.answers,
                    "aggregate": result,
                    "details": details,
                },
                report,
                ensure_ascii=False,
            )
    print(json.dumps(result, ensure_ascii=False))
    if result["evaluationStatus"] != "completed":
        parser.exit(
            2,
            "Evaluation interrupted; completed cases saved to the requested report.\n"
            if args.report
            else "Evaluation interrupted; no detailed report requested.\n",
        )


if __name__ == "__main__":
    main()
