"""公共来源章复用边界；独立PG验证引用完整性及公私隔离，不访问真实供应商。"""

from uuid import UUID

import pytest

from app.adapters import ModelGateway
from app.db import execute, one, transaction
from app.worker import run_once
from tests.conftest import register_login
from tests.test_api import post
from tests.test_personal_manuals import read_private, request_private, seed_memory
from tests.test_v1_content import ready_product

pytestmark = pytest.mark.postgres


def public_manual(client, product):
    return client.get(f"/api/v1/products/{product['productModelId']}/manual").json()


def source_section(manual):
    return next(
        section for section in manual["sections"] if section["sectionKey"].startswith("source-")
    )


def count_coverage(monkeypatch):
    calls = []
    original = ModelGateway.chapter_answer

    def counted(gateway, *args, **kwargs):
        if kwargs.get("coverage"):
            calls.append(True)
        return original(gateway, *args, **kwargs)

    monkeypatch.setattr(ModelGateway, "chapter_answer", counted)
    return calls


def test_private_source_reuses_ready_public_input_without_provider_call(client, monkeypatch):
    person = register_login(client)
    product = ready_product(client)
    initial = public_manual(client, product)
    assert run_once(UUID(initial["jobId"]))
    public = public_manual(client, product)
    cached = source_section(public)
    assert cached["generationStatus"] == "ready"
    seed_memory(person, "偏好简短中文")
    calls = count_coverage(monkeypatch)
    job = request_private(client, product)
    assert run_once(UUID(job["id"]))
    private = read_private(client, product)
    reused = source_section(private)
    assert private["status"] == "ready"
    assert calls == []
    assert reused["reused"]
    assert reused["body"] == cached["body"] and reused["citations"] == cached["citations"]
    assert reused["personalNotes"] == []
    assert not next(
        section for section in private["sections"] if section["sectionKey"] == "overview"
    )["reused"]
    assert private["personalContext"] and public_manual(client, product)["personalContext"] == []


def test_private_sources_are_not_used_for_another_instance_or_public_manual(client, monkeypatch):
    register_login(client)
    product = ready_product(client)
    pending_public = public_manual(client, product)
    first = request_private(client, product)
    assert run_once(UUID(first["id"]))
    assert source_section(read_private(client, product))["generationStatus"] == "ready"
    second = post(client, "/me/products", {"productModelId": product["productModelId"]}).json()
    calls = count_coverage(monkeypatch)
    second_job = request_private(client, second)
    assert run_once(UUID(second_job["id"]))
    assert calls == [True]
    assert not source_section(read_private(client, second))["reused"]
    assert run_once(UUID(pending_public["jobId"]))
    assert calls == [True, True]
    public = public_manual(client, product)
    assert public["status"] == "ready" and public["personalContext"] == []
    assert not source_section(public)["reused"]


@pytest.mark.parametrize("invalid", ["missing-citations", "unsafe-body"])
def test_invalid_public_source_cache_is_not_reused(client, monkeypatch, invalid):
    register_login(client)
    product = ready_product(client)
    pending = public_manual(client, product)
    cached = source_section(pending)
    body = "不完整缓存[1]" if invalid == "missing-citations" else "带电拆机[1]"
    with transaction() as connection:
        row = one(
            connection,
            "SELECT evidence_ids FROM manual_sections WHERE id=:id",
            id=UUID(cached["id"]),
        )
        if invalid == "unsafe-body":
            execute(
                connection,
                "INSERT INTO manual_section_citations(manual_section_id,knowledge_chunk_id,rank) "
                "VALUES(:id,:chunk,1)",
                id=UUID(cached["id"]),
                chunk=row["evidence_ids"][0],
            )
        # 模拟旧缓存/维护数据异常；不更改任何已就绪记录，数据仅在本用例专用库。
        execute(
            connection,
            "UPDATE manual_sections SET generation_status='ready',body=:body WHERE id=:id",
            id=UUID(cached["id"]),
            body=body,
        )
    calls = count_coverage(monkeypatch)
    job = request_private(client, product)
    assert run_once(UUID(job["id"]))
    section = source_section(read_private(client, product))
    assert calls == [True]
    assert section["generationStatus"] == "ready" and not section["reused"]
    assert section["body"] != body and section["citations"]
