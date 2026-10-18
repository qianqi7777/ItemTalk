"""V2冻结题集验收：检索指标与真实业务回答分开，默认不发起付费调用。"""

import argparse
import hashlib
import json
import math
import os
import re
import secrets
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.engine import make_url

from app.config import settings
from app.db import actor, many, one, transaction
from app.evaluate import GoldCase, answer_metrics
from app.main import app
from app.safety import risk_of
from app.services.chat import retrieve


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(pattern=r"^[a-z0-9_-]{1,80}$")
    sourceId: str = Field(pattern=r"^[a-z0-9_-]{1,80}$")
    split: Literal["development", "holdout"]
    question: str = Field(min_length=1, max_length=10000)
    expectedOutcome: Literal["supported", "insufficient", "unsafe"]
    expectedPage: int | None = Field(default=None, ge=1)
    supportingLiteral: str | None = Field(default=None, max_length=300)
    expectedFacts: list[list[str]] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def valid_labels(self):
        if self.expectedOutcome == "supported" and (
            not self.expectedPage or not self.supportingLiteral
        ):
            raise ValueError("Supported cases require a frozen page and literal")
        if any(
            not group
            or len(group) > 20
            or any(not term.strip() or len(term) > 200 for term in group)
            for group in self.expectedFacts
        ):
            raise ValueError("Invalid frozen fact alternatives")
        return self


def isolated():
    config = settings()
    url = make_url(config.db_url()) if isinstance(config.db_url(), str) else config.db_url()
    if (
        config.environment not in {"development", "test"}
        or url.host not in {"localhost", "127.0.0.1"}
        or not re.fullmatch(
            r"itemtalk_v2_(?:test|acceptance|benchmark_[a-z0-9_]+)", url.database or ""
        )
    ):
        raise ValueError("An independent loopback V2 database is required")


def load_dataset(path):
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("Dataset too large")
    raw = path.read_bytes()
    data = json.loads(raw)
    cases = [Case.model_validate(row) for row in data["cases"]]
    if len(cases) != 60 or len({c.id for c in cases}) != 60:
        raise ValueError("V2 requires exactly 60 unique frozen cases")
    if sum(c.split == "development" for c in cases) != 36:
        raise ValueError("V2 requires a 36/24 frozen split")
    sources = {row["id"]: row for row in data["sources"]}
    if len(sources) != 3 or any(c.sourceId not in sources for c in cases):
        raise ValueError("Three exact source identities are required")
    for source in sources:
        selected = [c for c in cases if c.sourceId == source]
        if len(selected) != 20 or sum(c.expectedOutcome == "supported" for c in selected) != 14:
            raise ValueError("Each device requires 14 supported, 4 insufficient, 2 unsafe cases")
        if sum(case.split == "development" for case in selected) != 12:
            raise ValueError("Each device requires a 12/8 development/holdout split")
        if (
            sum(c.expectedOutcome == "insufficient" for c in selected) != 4
            or sum(c.expectedOutcome == "unsafe" for c in selected) != 2
        ):
            raise ValueError("Invalid negative-case distribution")
    return cases, sources, hashlib.sha256(raw).hexdigest()


def evidence_targets(case, sources):
    source = sources[case.sourceId]
    with transaction() as connection:
        model = one(
            connection,
            "SELECT m.* FROM product_models m JOIN brands b ON b.id=m.brand_id "
            "WHERE b.normalized_name=:b AND m.normalized_model_code=:m "
            "AND m.region IS NOT DISTINCT FROM :r AND m.hardware_revision IS NULL",
            b=source["brand"].casefold(),
            m=source["model"].casefold(),
            r=source["region"],
        )
        if not model:
            raise ValueError("Frozen device not imported")
        version = one(
            connection,
            "SELECT v.id FROM document_versions v JOIN documents d ON d.id=v.document_id "
            "JOIN sources s ON s.id=d.source_id WHERE s.url=:url AND v.sha256=:hash "
            "AND v.verification_status='verified'",
            url=source["url"],
            hash=source["sha256"],
        )
        if not version:
            raise ValueError("Exact frozen official source is not verified")
        chunks = many(
            connection,
            "SELECT id,content FROM knowledge_chunks WHERE product_model_id=:m "
            "AND document_version_id=:v AND page_start<=:p AND page_end>=:p "
            "AND verification_status='verified'",
            m=model["id"],
            v=version["id"],
            p=case.expectedPage or 1,
        )

    def normalize(text):
        return re.sub(r"\s+", "", text).casefold()

    targets = {
        row["id"]
        for row in chunks
        if case.supportingLiteral and normalize(case.supportingLiteral) in normalize(row["content"])
    }
    if case.expectedOutcome == "supported" and not targets:
        raise ValueError("Supporting literal absent from its exact frozen page")
    return model, version, targets


def validate_vectors(value, digest):
    config = settings()
    if (
        value["datasetSha256"] != digest
        or value["embeddingModel"] != config.embedding_model
        or value["embeddingRevision"] != config.embedding_revision
        or value["dimensions"] != 1024
    ):
        raise ValueError("Query-vector provenance mismatch")
    for vector in value["vectors"].values():
        if (
            len(vector) != 1024
            or any(
                isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                for v in vector
            )
            or not any(vector)
        ):
            raise ValueError("Invalid cached query vector")
    return value["vectors"]


def retrieval_report(cases, sources, vectors):
    results = []
    for case in cases:
        if case.expectedOutcome == "unsafe":
            results.append(
                {
                    "id": case.id,
                    "sourceId": case.sourceId,
                    "split": case.split,
                    "unsafeIntercepted": risk_of(case.question) == "high",
                }
            )
            continue
        model, version, targets = evidence_targets(case, sources)
        started = time.perf_counter()
        rows = retrieve(model["id"], case.question, vectors[case.id])
        relevant = [index for index, row in enumerate(rows, 1) if row["id"] in targets]
        results.append(
            {
                "id": case.id,
                "sourceId": case.sourceId,
                "split": case.split,
                "targetEvidenceHit": bool(relevant) if targets else None,
                "firstRelevantRank": min(relevant) if relevant else None,
                "pageHit": (
                    any(
                        row["document_version_id"] == version["id"]
                        and row["page_start"] <= case.expectedPage <= row["page_end"]
                        for row in rows
                    )
                    if case.expectedPage
                    else None
                ),
                "durationMs": (time.perf_counter() - started) * 1000,
                "candidateIds": [str(row["id"]) for row in rows],
            }
        )
    groups = {}
    for source in sources:
        positive = [
            r for r in results if r["sourceId"] == source and r.get("targetEvidenceHit") is not None
        ]
        groups[source] = (
            sum(r["targetEvidenceHit"] for r in positive) / len(positive) if positive else None
        )
    positives = [r for r in results if r.get("targetEvidenceHit") is not None]
    holdout = [r for r in positives if r["split"] == "holdout"]
    return {
        "scope": "Frozen target-literal evidence hit, not exhaustive recall or answer accuracy",
        "targetEvidenceHit": sum(r["targetEvidenceHit"] for r in positives) / len(positives),
        "holdoutTargetHit": sum(r["targetEvidenceHit"] for r in holdout) / len(holdout),
        "byDevice": groups,
        "results": results,
    }


class FrozenRealVector:
    def __init__(self, question, vector):
        self.question = question
        self.vector = vector

    def embed(self, texts, subject):
        if texts != [self.question]:
            raise ValueError("Frozen query does not match")
        return [self.vector]


def call_count():
    config = settings()
    with transaction() as connection:
        row = one(
            connection,
            "SELECT amount FROM usage_counters WHERE subject=:s AND bucket=:b",
            s=f"acceptance:{config.acceptance_run_id}"
            if config.acceptance_run_id
            else "model:global",
            b="total" if config.acceptance_run_id else datetime.now(UTC).strftime("%Y-%m-%d"),
        )
    return row["amount"] if row else 0


def source_fingerprints():
    """同时记录前后指纹；运行中修改源码不能被误报为最终版本验收。"""
    root = Path(__file__).parent
    names = [
        "benchmark.py",
        "adapters.py",
        "config.py",
        "grounding.py",
        "operations.py",
        "services/chat.py",
    ]
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names}


def answer_report(cases, sources, split, vectors=None, progress=None):
    selected = [c for c in cases if c.split == split]
    origin = settings().app_public_url.rstrip("/")
    headers = {"Origin": origin, "X-Requested-With": "XMLHttpRequest"}
    results, cleanup, interruption = [], None, None
    with TestClient(app, base_url=origin, headers=headers) as client:
        credentials = {
            "email": f"v2-{uuid4().hex}@acceptance.invalid",
            "password": secrets.token_urlsafe(24),
        }
        registered = client.post("/api/v1/auth/register", json=credentials)
        registered.raise_for_status()
        assert client.post("/api/v1/auth/login", json=credentials).status_code == 204
        try:
            for case in selected:
                calls_before = call_count()
                model, version, targets = evidence_targets(case, sources)
                if vectors is not None and case.expectedOutcome != "unsafe":
                    from app.operations import query_vector

                    with actor(registered.json()["id"]):
                        query_vector(
                            FrozenRealVector(case.question, vectors[case.id]),
                            case.question,
                            "benchmark",
                        )
                product = client.post(
                    "/api/v1/me/products",
                    json={"productModelId": str(model["id"])},
                    headers={"Idempotency-Key": str(uuid4())},
                )
                product.raise_for_status()
                conversation = client.post(
                    "/api/v1/conversations", json={"userProductInstanceId": product.json()["id"]}
                )
                conversation.raise_for_status()
                started = time.perf_counter()
                response = client.post(
                    f"/api/v1/conversations/{conversation.json()['id']}/messages",
                    json={"content": case.question},
                    headers={"Idempotency-Key": str(uuid4())},
                )
                if response.is_error:
                    interruption = {"caseId": case.id, "httpStatus": response.status_code}
                    results.append(
                        {
                            "id": case.id,
                            "status": "interrupted",
                            "httpStatus": response.status_code,
                            "errorCode": response.json().get("code", "UNKNOWN"),
                            "externalCalls": call_count() - calls_before,
                        }
                    )
                    if progress:
                        progress(results)
                    break
                body = response.json()
                # 接口正文返回Message；引用使用实际持久化对象，不采信生成器自报的ID。
                message = body.get("message", body)
                answer = message.get("content", "")
                citations = message.get("citations", [])
                valid_targets = bool(citations) and any(
                    UUID(c["knowledgeChunkId"]) in targets for c in citations
                )
                gold = GoldCase(
                    productModelId=model["id"],
                    question=case.question,
                    expectedFacts=case.expectedFacts,
                    expectInsufficient=case.expectedOutcome == "insufficient",
                )
                checks = answer_metrics(gold, [], answer)
                results.append(
                    {
                        "id": case.id,
                        "expectedOutcome": case.expectedOutcome,
                        "answer": answer,
                        "citations": citations,
                        "factAccuracy": checks["factAccuracy"],
                        "refusalAccuracy": checks["refusalAccuracy"],
                        "targetCitationPresent": valid_targets if targets else None,
                        "citationApplicability": all(
                            UUID(c["documentVersionId"]) == version["id"] for c in citations
                        )
                        if citations
                        else None,
                        "unsafeIntercepted": (
                            not citations
                            and risk_of(case.question) == "high"
                            and call_count() == calls_before
                            and any(w in answer for w in ["安全", "专业", "危险"])
                        )
                        if case.expectedOutcome == "unsafe"
                        else None,
                        "durationMs": (time.perf_counter() - started) * 1000,
                        "externalCalls": call_count() - calls_before,
                    }
                )
                if progress:
                    progress(results)
        except Exception as error:
            interruption = {"errorType": type(error).__name__}
        finally:
            deleted = client.delete("/api/v1/me")
            if deleted.status_code == 202:
                cleanup = deleted.json()["cleanupJobId"]
    return {
        "cases": len(selected),
        "evaluationStatus": "interrupted" if interruption else "completed",
        "interruption": interruption,
        "completedCases": sum(r.get("status") != "interrupted" for r in results),
        "results": results,
        "queryEmbeddings": "provenance-checked cached real vectors"
        if vectors is not None
        else "live",
        "cleanupJobId": cleanup,
        "scope": "Actual JSON API flow; frozen literal/fact checks require manual review",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--mode", choices=["retrieval", "answers"], default="retrieval")
    parser.add_argument("--vectors", type=Path)
    parser.add_argument("--split", choices=["development", "holdout"], default="holdout")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--paid", action="store_true")
    parser.add_argument("--case-id", action="append", default=[])
    args = parser.parse_args()
    isolated()
    if args.report.exists():
        parser.error("Existing report must be preserved")
    cases, sources, digest = load_dataset(args.dataset)
    source_start = source_fingerprints()
    calls_start = call_count()
    if args.case_id:
        if args.mode != "answers" or not set(args.case_id) <= {case.id for case in cases}:
            parser.error("Case selection requires valid frozen answer-case IDs")
        cases = [case for case in cases if case.id in args.case_id]
    if args.mode == "answers":
        if settings().model_provider != "mock" and (
            not args.paid or not settings().acceptance_run_id
        ):
            parser.error("Real answers require --paid and the shared bounded acceptance ledger")
        vectors = (
            validate_vectors(json.loads(args.vectors.read_text(encoding="utf-8")), digest)
            if args.vectors
            else None
        )
        partial = Path(str(args.report) + ".partial.json")
        descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump({"datasetSha256": digest, "results": []}, output)

        def progress(results):
            partial.write_text(
                json.dumps(
                    {"datasetSha256": digest, "profile": settings().chat_model, "results": results},
                    ensure_ascii=False,
                    default=str,
                ),
                encoding="utf-8",
            )

        report = answer_report(cases, sources, args.split, vectors, progress)
    else:
        if not args.vectors:
            parser.error("Retrieval replay requires previously frozen query vectors")
        vectors = validate_vectors(json.loads(args.vectors.read_text(encoding="utf-8")), digest)
        report = retrieval_report(cases, sources, vectors)
    report.update(
        datasetSha256=digest,
        profile=settings().chat_model,
        sourceFilesAtStart=source_start,
        sourceFilesAtEnd=source_fingerprints(),
        callsAtStart=calls_start,
        callsAtEnd=call_count(),
        chatSourceSha256=hashlib.sha256(
            Path(__file__).with_name("services").joinpath("chat.py").read_bytes()
        ).hexdigest(),
    )
    report["sourceChangedDuringRun"] = report["sourceFilesAtStart"] != report["sourceFilesAtEnd"]
    descriptor = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, default=str)
    print(json.dumps({"reportWritten": True, "mode": args.mode, "datasetSha256": digest}))


if __name__ == "__main__":
    main()
