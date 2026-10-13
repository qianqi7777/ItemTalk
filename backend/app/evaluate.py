"""检索 Gold Set 评测入口；数据集由运营核验，输出仅包含聚合指标。"""

import argparse
import json
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.adapters import ModelGateway
from app.config import settings
from app.db import one, transaction
from app.services.chat import retrieve


class GoldCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    productModelId: UUID
    question: str = Field(min_length=1, max_length=10000)
    expectedDocumentVersionIds: list[UUID] = Field(default_factory=list)
    expectedPage: int | None = Field(default=None, ge=1)
    expectedRegion: str | None = None
    expectedHardwareRevision: str | None = None
    expectInsufficient: bool = False


def evaluate(cases):
    """报告召回、页码及适用性；空预期样例用于评估无依据拒答。"""
    recalls, pages, applicability, insufficient = [], [], [], []
    for case in cases:
        vector = ModelGateway().embed([case.question], "gold-set")[0]
        evidence = retrieve(case.productModelId, case.question, vector, limit=10)
        actual = {row["document_version_id"] for row in evidence}
        expected = set(case.expectedDocumentVersionIds)
        if expected:
            recalls.append(len(actual & expected) / len(expected))
        if case.expectedPage:
            pages.append(
                any(
                    row["document_version_id"] in expected
                    and row["page_start"] <= case.expectedPage <= row["page_end"]
                    for row in evidence
                )
            )
        with transaction() as connection:
            model = one(
                connection,
                ("SELECT region,hardware_revision FROM product_models WHERE id=:id"),
                id=case.productModelId,
            )
        applicability.append(
            bool(model)
            and model["region"] == case.expectedRegion
            and model["hardware_revision"] == case.expectedHardwareRevision
        )
        if case.expectInsufficient:
            insufficient.append(not evidence)

    def average(values):
        return sum(values) / len(values) if values else None

    return {
        "cases": len(cases),
        "recallAt10": average(recalls),
        "pageAccuracy": average(pages),
        "applicabilityAccuracy": average(applicability),
        "noEvidenceAccuracy": average(insufficient),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--paid", action="store_true")
    args = parser.parse_args()
    if settings().model_provider != "mock" and not args.paid:
        parser.error("Real embedding calls require --paid")
    if args.dataset.stat().st_size > 1024 * 1024:
        parser.error("Dataset exceeds size limit")
    cases = [
        GoldCase.model_validate(row) for row in json.loads(args.dataset.read_text(encoding="utf-8"))
    ]
    print(json.dumps(evaluate(cases), ensure_ascii=False))


if __name__ == "__main__":
    main()
