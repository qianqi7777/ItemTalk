"""冻结数据的结构/向量修订在付费操作前验证；不读取本机实际题集或密钥。"""

import json

import pytest

from app.benchmark import Case, FrozenRealVector, load_dataset, validate_vectors
from app.config import settings


def synthetic_dataset():
    sources = [{"id": f"device-{index}"} for index in range(3)]
    cases = []
    for source in sources:
        for number in range(1, 21):
            kind = "supported" if number <= 14 else "insufficient" if number <= 18 else "unsafe"
            cases.append(
                {
                    "id": f"{source['id']}-{number}",
                    "sourceId": source["id"],
                    "split": "development"
                    if number <= 8 or number in [15, 16, 17, 19]
                    else "holdout",
                    "question": f"合成测试问题{number}",
                    "expectedOutcome": kind,
                    "expectedPage": 1 if kind == "supported" else None,
                    "supportingLiteral": "合成依据" if kind == "supported" else None,
                }
            )
    return {"sources": sources, "cases": cases}


def test_frozen_dataset_distribution_and_uniqueness(tmp_path):
    path = tmp_path / "cases.json"
    data = synthetic_dataset()
    path.write_text(json.dumps(data), encoding="utf-8")
    assert len(load_dataset(path)[0]) == 60
    data["cases"][-1]["id"] = data["cases"][0]["id"]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        load_dataset(path)


def test_supported_case_requires_source_label():
    with pytest.raises(ValueError):
        Case(id="a", sourceId="s", split="holdout", question="问题", expectedOutcome="supported")


def test_vector_revision_nonfinite_and_zero_are_rejected():
    config = settings()
    cached = {
        "datasetSha256": "frozen",
        "embeddingModel": config.embedding_model,
        "embeddingRevision": config.embedding_revision,
        "dimensions": 1024,
        "vectors": {"a": [1.0] + [0.0] * 1023},
    }
    assert len(validate_vectors(cached, "frozen")["a"]) == 1024
    for value in [float("nan"), float("inf"), True]:
        cached["vectors"]["a"] = [value] + [0.0] * 1023
        with pytest.raises(ValueError):
            validate_vectors(cached, "frozen")
    cached["vectors"]["a"] = [1.0] + [0.0] * 1023
    cached["embeddingRevision"] = "other"
    with pytest.raises(ValueError):
        validate_vectors(cached, "frozen")


def test_replayed_vector_cannot_be_used_for_a_different_question():
    replay = FrozenRealVector("原始问题", [1.0] + [0.0] * 1023)
    assert len(replay.embed(["原始问题"], "test")[0]) == 1024
    with pytest.raises(ValueError):
        replay.embed(["另一个问题"], "test")
