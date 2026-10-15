"""长官方手册走正常研究/解析/向量任务；只对外部模型使用确定性替身。"""

from uuid import UUID

import pytest

from app.adapters import ModelGateway
from app.config import settings
from app.db import execute, many, one, transaction
from app.errors import DomainError
from app.services import knowledge
from app.services.products import build_for
from app.worker import run_once
from tests.conftest import register_login
from tests.test_api import identified_product

pytestmark = pytest.mark.postgres


def setup_document(client, monkeypatch, pages=142):
    register_login(client)
    product = identified_product(client)
    monkeypatch.setattr(
        knowledge,
        "parse_document",
        lambda *_: [
            (number, f"DEMO-100 CN 第 {number} 页的官方使用说明") for number in range(1, pages + 1)
        ],
    )
    return product, UUID(product["knowledgeJobId"])


def chunks():
    with transaction() as connection:
        return many(
            connection,
            "SELECT id,chunk_no,page_start,page_end,content,"
            "embedding_revision FROM knowledge_chunks ORDER BY chunk_no",
        )


def fake_vectors(gateway, texts, subject):
    return [[1.0] + [0.0] * 1023 for _ in texts]


def retry_now(job):
    with transaction() as connection:
        execute(connection, "UPDATE jobs SET available_at=now() WHERE id=:id", id=job)


def test_142_page_document_is_not_rejected_or_truncated(client, monkeypatch):
    product, job = setup_document(client, monkeypatch)
    calls = []

    def embed(gateway, texts, subject):
        calls.append(list(texts))
        return fake_vectors(gateway, texts, subject)

    monkeypatch.setattr(ModelGateway, "embed", embed)
    assert run_once(job)
    assert client.get(f"/api/v1/me/products/{product['id']}").json()["knowledgeStatus"] == "ready"
    rows = chunks()
    assert [row["page_start"] for row in rows] == list(range(1, 143))
    assert [row["chunk_no"] for row in rows] == list(range(1, 143))
    assert all(row["page_start"] == row["page_end"] for row in rows)
    assert [len(batch) for batch in calls] == [10] * 14 + [2]


def test_retry_preserves_first_batch_and_only_embeds_missing_chunks(client, monkeypatch):
    _, job = setup_document(client, monkeypatch, pages=25)
    calls = []

    def embed(gateway, texts, subject):
        calls.append(list(texts))
        if len(calls) == 2:
            raise DomainError(429, "RATE_LIMITED", "测试正常额度中断")
        return fake_vectors(gateway, texts, subject)

    monkeypatch.setattr(ModelGateway, "embed", embed)
    assert run_once(job)
    saved = chunks()
    assert len(saved) == 10
    with transaction() as connection:
        assert (
            one(connection, "SELECT status FROM jobs WHERE id=:id", id=job)["status"] != "succeeded"
        )
    retry_now(job)
    assert run_once(job)
    rows = chunks()
    assert len(rows) == 25 and rows[:10] == saved
    assert [len(batch) for batch in calls] == [10, 10, 10, 5]
    assert calls[0] not in calls[1:]
    assert calls[1] == calls[2]  # 只有未成功返回的批次需要再次调用。


def test_refresh_reuses_exact_same_verified_revision_without_paid_embedding(client, monkeypatch):
    product, job = setup_document(client, monkeypatch, pages=25)
    monkeypatch.setattr(ModelGateway, "embed", fake_vectors)
    assert run_once(job)
    saved = chunks()
    with transaction() as connection:
        refreshed = build_for(connection, UUID(product["productModelId"]), None, None, force=True)

    def forbidden(*_):
        pytest.fail("Exact verified embeddings must be reused")

    monkeypatch.setattr(ModelGateway, "embed", forbidden)
    assert run_once(refreshed["id"])
    assert chunks() == saved
    assert client.get(f"/api/v1/me/products/{product['id']}").json()["knowledgeStatus"] == "ready"


def test_embedding_revision_change_rebuilds_vectors_without_changing_citation_ids(
    client, monkeypatch
):
    product, job = setup_document(client, monkeypatch, pages=25)
    monkeypatch.setattr(ModelGateway, "embed", fake_vectors)
    assert run_once(job)
    saved = chunks()
    monkeypatch.setenv("EMBEDDING_REVISION", "replacement")
    settings.cache_clear()
    with transaction() as connection:
        refreshed = build_for(connection, UUID(product["productModelId"]), None, None, force=True)
    calls = []

    def embed(gateway, texts, subject):
        calls.append(len(texts))
        return fake_vectors(gateway, texts, subject)

    monkeypatch.setattr(ModelGateway, "embed", embed)
    assert run_once(refreshed["id"])
    rows = chunks()
    assert calls == [10, 10, 5]
    assert [row["id"] for row in rows] == [row["id"] for row in saved]
    assert all(row["embedding_revision"] == "replacement" for row in rows)


@pytest.mark.parametrize("invalidate", ["source", "lease"])
def test_between_batch_revocation_or_stale_lease_stops_before_next_paid_call(
    client, monkeypatch, invalidate
):
    _, job = setup_document(client, monkeypatch, pages=25)
    calls = []

    def embed(gateway, texts, subject):
        calls.append(len(texts))
        return fake_vectors(gateway, texts, subject)

    monkeypatch.setattr(ModelGateway, "embed", embed)
    progress = knowledge.progress

    def invalidate_after_batch(child, stage, percent):
        progress(child, stage, percent)
        if stage == "embedding" and child["job_type"] == "embed":
            with transaction() as connection:
                if invalidate == "source":
                    execute(connection, "UPDATE sources SET verification_status='revoked'")
                else:
                    execute(
                        connection,
                        "UPDATE jobs SET lease_until=now()-interval '1 second' WHERE id=:id",
                        id=job,
                    )

    monkeypatch.setattr(knowledge, "progress", invalidate_after_batch)
    assert run_once(job)
    assert len(chunks()) == 10 and calls == [10]
    with transaction() as connection:
        assert (
            one(connection, "SELECT status FROM jobs WHERE id=:id", id=job)["status"] != "succeeded"
        )


def test_chunk_numbers_and_pages_remain_stable_across_empty_and_split_pages():
    assert knowledge.document_chunks([(1, " "), (2, "a" * 2001), (3, "b")]) == [
        (1, 2, "a" * 2000),
        (2, 2, "a"),
        (3, 3, "b"),
    ]
