"""Private manuals freeze consent and evidence; all chunks and incremental chapters are verified."""

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError

from app.adapters import ModelGateway
from app.db import actor, execute, many, one, transaction
from app.main import app
from app.security import digest
from app.services import manuals
from app.services.knowledge import persist_document, publish_chunks
from app.worker import run_once
from tests.conftest import HEADERS, register_login
from tests.test_api import post
from tests.test_rls import own_product
from tests.test_rls import runtime as runtime
from tests.test_v1_content import ready_product

pytestmark = pytest.mark.postgres


def seed_memory(user, content, instance=None, confirmed=True):
    with transaction() as connection:
        return one(
            connection,
            (
                "INSERT INTO user_memories(user_id,memory_type,scope,user_product_instance_id,"
                "content,content_hash,consent_status,confirmed_at) VALUES(:u,:type,:scope,:i,"
                ":text,:h,:status,CASE WHEN :yes THEN now() ELSE NULL END) RETURNING id"
            ),
            u=UUID(user["id"]),
            type="product_fact" if instance else "preference",
            scope="instance" if instance else "global",
            i=UUID(instance["id"]) if instance else None,
            text=content,
            h=digest(content),
            status="confirmed" if confirmed else "pending",
            yes=confirmed,
        )["id"]


def request_private(client, product):
    response = post(
        client,
        f"/products/{product['productModelId']}/manual-jobs",
        {"userProductInstanceId": product["id"]},
    )
    assert response.status_code == 202, response.text
    return response.json()


def read_private(client, product):
    response = client.get(
        f"/api/v1/products/{product['productModelId']}/manual?instanceId={product['id']}"
    )
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Vary"] == "Cookie"
    return response.json()


def add_evidence(product, pages):
    text = "\n".join(body for _, body in pages)
    vectors = ModelGateway().embed([body for _, body in pages], "manual-catalog-test")
    with transaction() as connection:
        model = one(
            connection,
            "SELECT * FROM product_models WHERE id=:m",
            m=UUID(product["productModelId"]),
        )
        version = persist_document(
            connection,
            model,
            "https://example.invalid/itemtalk-demo-expanded",
            "扩展核验资料",
            "text/plain",
            text.encode(),
            pages,
            True,
        )
        publish_chunks(connection, model, version, pages, vectors)
    return version


def test_private_context_only_confirmed_same_instance_and_public_unchanged(client):
    user = register_login(client)
    product = ready_product(client)
    second = post(client, "/me/products", {"productModelId": product["productModelId"]}).json()
    seed_memory(user, "偏好简短中文")
    setting = seed_memory(user, "当前开启节能模式", product)
    seed_memory(user, "另一台设备使用访客网络", second)
    seed_memory(user, "尚未确认的偏好", confirmed=False)
    job = request_private(client, product)
    assert run_once(UUID(job["id"]))
    private = read_private(client, product)
    assert private["visibility"] == "private" and private["status"] == "ready"
    assert {row["content"] for row in private["personalContext"]} == {
        "偏好简短中文",
        "当前开启节能模式",
    }
    assert private["coverage"]["citedChunks"] == private["coverage"]["totalChunks"]
    assert any(section["personalNotes"] for section in private["sections"])
    assert all("当前开启节能模式" not in (section["body"] or "") for section in private["sections"])
    public = client.get(f"/api/v1/products/{product['productModelId']}/manual").json()
    assert public["visibility"] == "public" and public["personalContext"] == []
    assert all(section["personalNotes"] == [] for section in public["sections"])
    assert public["id"] != private["id"]
    with transaction() as connection:
        assert all(
            row["context_snapshot"] == [] and row["context_hash"] is None
            for row in many(connection, "SELECT * FROM manuals WHERE visibility='public'")
        )
    repeated = request_private(client, product)
    assert repeated["id"] == job["id"]
    assert not read_private(client, second)["visibility"] == "private"
    assert client.delete(f"/api/v1/me/memories/{setting}").status_code == 204
    assert all(
        section["personalNotes"] == [] for section in read_private(client, product)["sections"]
    )
    with TestClient(app, base_url="http://localhost:5173", headers=HEADERS) as other:
        register_login(other, "outsider@example.com")
        assert (
            post(
                other,
                f"/products/{product['productModelId']}/manual-jobs",
                {"userProductInstanceId": product["id"]},
            ).status_code
            == 404
        )
        assert (
            other.get(
                f"/api/v1/manuals/{private['id']}/sections/{private['sections'][0]['id']}"
            ).status_code
            == 404
        )


def test_catalog_after_chunk_twelve_is_cited_and_unchanged_chapter_is_reused(client):
    register_login(client)
    product = ready_product(client)
    path = f"/api/v1/products/{product['productModelId']}/manual"
    first = client.get(path).json()
    assert run_once(UUID(first["jobId"]))
    original = client.get(path).json()
    old_source = next(s for s in original["sections"] if s["sectionKey"].startswith("source-"))
    pages = [(i, f"DEMO-100 CN 第{i}页连接功能及使用条件。") for i in range(1, 26)]
    add_evidence(product, pages)
    with transaction() as connection:
        new = manuals.schedule(connection, UUID(product["productModelId"]))
    assert run_once(new["job_id"])
    current = client.get(path).json()
    assert current["version"] == 2 and current["status"] == "ready"
    assert current["coverage"] == {"totalChunks": 26, "citedChunks": 26}
    reused = next(s for s in current["sections"] if s["sectionKey"] == old_source["sectionKey"])
    assert reused["reused"] and reused["body"] == old_source["body"]
    assert reused["citations"] == old_source["citations"]
    with transaction() as connection:
        all_ids = {
            row["id"] for row in manuals.evidence(connection, UUID(product["productModelId"]))
        }
        assigned = {
            id
            for row in many(
                connection,
                "SELECT evidence_ids FROM manual_sections WHERE manual_id=:m",
                m=UUID(current["id"]),
            )
            for id in row["evidence_ids"]
        }
        assert assigned == all_ids
        assert all(
            len(row["evidence_ids"]) <= 10
            for row in many(
                connection,
                "SELECT evidence_ids FROM manual_sections WHERE manual_id=:m",
                m=UUID(current["id"]),
            )
        )


def test_selective_retry_calls_only_unfinished_chapter(client, monkeypatch):
    user = register_login(client)
    product = ready_product(client)
    job = request_private(client, product)
    original = ModelGateway.chapter_answer

    def partial(gateway, question, *args, **kwargs):
        if question == manuals.CHAPTERS[1][2]:
            return "没有引用的段落"
        return original(gateway, question, *args, **kwargs)

    monkeypatch.setattr(ModelGateway, "chapter_answer", partial)
    assert run_once(UUID(job["id"]))
    draft = read_private(client, product)
    assert draft["status"] == "partial"
    incomplete = next(s for s in draft["sections"] if s["generationStatus"] != "ready")
    ready = {s["id"]: s["body"] for s in draft["sections"] if s["generationStatus"] == "ready"}
    calls = []

    def capture(gateway, *args, **kwargs):
        calls.append(args[0])
        return original(gateway, *args, **kwargs)

    monkeypatch.setattr(ModelGateway, "chapter_answer", capture)
    path = f"/manuals/{draft['id']}/section-jobs"
    assert post(client, path, {"sectionIds": [next(iter(ready))]}).status_code == 409
    assert post(client, path, {"sectionIds": [str(uuid4())]}).status_code == 404
    key = str(uuid4())
    response = post(client, path, {"sectionIds": [incomplete["id"]]}, key=key)
    assert response.status_code == 202, response.text
    assert (
        post(client, path, {"sectionIds": [incomplete["id"]]}, key=key).json()["id"]
        == response.json()["id"]
    )
    assert run_once(UUID(response.json()["id"]))
    current = read_private(client, product)
    assert current["status"] == "ready" and len(calls) == 1
    assert all(s["body"] == ready[s["id"]] for s in current["sections"] if s["id"] in ready)
    with actor(user["id"]), pytest.raises(DBAPIError), transaction() as connection:
        execute(
            connection,
            "UPDATE manual_sections SET evidence_ids='{}' WHERE id=:s",
            s=UUID(current["sections"][0]["id"]),
        )


def test_consent_change_requires_new_version_and_filters_old_context(client, monkeypatch):
    user = register_login(client)
    product = ready_product(client)
    memory = seed_memory(user, "偏好简短中文")
    job = request_private(client, product)
    original = ModelGateway.chapter_answer
    once = []

    def revoke(gateway, *args, **kwargs):
        body = original(gateway, *args, **kwargs)
        if not once:
            once.append(True)
            with transaction() as connection:
                execute(
                    connection,
                    "UPDATE user_memories SET consent_status='revoked',deleted_at=now() "
                    "WHERE id=:m",
                    m=memory,
                )
        return body

    monkeypatch.setattr(ModelGateway, "chapter_answer", revoke)
    assert run_once(UUID(job["id"]))
    stale = read_private(client, product)
    assert stale["inputsStale"] and stale["contextStale"] and stale["personalContext"] == []
    assert not any(s["generationStatus"] == "ready" for s in stale["sections"])
    monkeypatch.setattr(ModelGateway, "chapter_answer", original)
    new = request_private(client, product)
    assert new["id"] != job["id"]
    assert run_once(UUID(new["id"]))
    assert read_private(client, product)["version"] == 2


def test_nonowner_private_generation_and_snapshot_immutability(runtime):
    with TestClient(app, base_url="http://localhost:5173", headers=HEADERS) as client:
        with runtime("app"):
            user = register_login(client)
        product = own_product(client, runtime)
        seed_memory(user, "简短中文")
        with runtime("app"):
            job = request_private(client, product)
        with runtime("worker"):
            assert run_once(UUID(job["id"]))
        with runtime("app"):
            private = read_private(client, product)
            assert private["status"] == "ready"
            with actor(user["id"]), pytest.raises(DBAPIError), transaction() as connection:
                execute(
                    connection,
                    "UPDATE manuals SET context_snapshot='[]' WHERE id=:m",
                    m=UUID(private["id"]),
                )
        with transaction() as connection:
            with pytest.raises(DBAPIError):
                execute(
                    connection,
                    "INSERT INTO manuals(product_model_id,visibility,version,status,evidence_hash,"
                    "context_snapshot) VALUES(:m,'public',50,'pending',:h,"
                    '\'[{"content":"private"}]\')',
                    m=UUID(product["productModelId"]),
                    h="z" * 64,
                )


def test_reverting_personal_context_reactivates_exact_historical_inputs(client):
    user = register_login(client)
    product = ready_product(client)
    original_job = request_private(client, product)
    assert run_once(UUID(original_job["id"]))
    original = read_private(client, product)
    memory = seed_memory(user, "偏好简短中文")
    personalized_job = request_private(client, product)
    assert run_once(UUID(personalized_job["id"]))
    assert read_private(client, product)["version"] == 2
    assert client.delete(f"/api/v1/me/memories/{memory}").status_code == 204
    reverted = request_private(client, product)
    assert reverted["id"] == original_job["id"]
    current = read_private(client, product)
    assert current["id"] == original["id"] and not current["inputsStale"]
    assert current["personalContext"] == [] and current["latestBuild"] is None


def test_personal_note_cannot_invent_memory_reference_or_dangerous_action(client, monkeypatch):
    from app.schemas import ChapterPersonalization

    user = register_login(client)
    product = ready_product(client)
    setting = seed_memory(user, "当前开启节能模式", product)
    job = request_private(client, product)
    monkeypatch.setattr(
        ModelGateway,
        "personalize_chapter",
        lambda *a, **k: ChapterPersonalization.model_validate(
            {"notes": [{"text": "另外一个实例的设置", "memoryIds": [str(uuid4())]}]}
        ),
    )
    assert run_once(UUID(job["id"]))
    assert read_private(client, product)["status"] == "failed"
    monkeypatch.setattr(
        ModelGateway,
        "personalize_chapter",
        lambda *a, **k: ChapterPersonalization.model_validate(
            {"notes": [{"text": "带电拆开电源并绕过保护", "memoryIds": [str(setting)]}]}
        ),
    )
    response = post(
        client,
        f"/products/{product['productModelId']}/manual-jobs",
        {"userProductInstanceId": product["id"]},
        retry=job["id"],
    )
    assert response.status_code == 202
    assert run_once(UUID(response.json()["id"]))
    assert all(s["personalNotes"] == [] for s in read_private(client, product)["sections"])


def test_public_section_rejects_personal_notes_at_database_boundary(client):
    register_login(client)
    product = ready_product(client)
    public = client.get(f"/api/v1/products/{product['productModelId']}/manual").json()
    with pytest.raises(DBAPIError), transaction() as connection:
        execute(
            connection,
            (
                'UPDATE manual_sections SET personal_notes=\'[{"text":"private","memoryIds":[]}]\' '
                "WHERE id=:s"
            ),
            s=UUID(public["sections"][0]["id"]),
        )


def test_revoking_setting_after_official_answer_prevents_personal_model_call(client, monkeypatch):
    user = register_login(client)
    product = ready_product(client)
    setting = seed_memory(user, "当前开启节能模式", product)
    job = request_private(client, product)
    original = ModelGateway.chapter_answer
    notes_called = []

    def revoked(gateway, *args, **kwargs):
        body = original(gateway, *args, **kwargs)
        with transaction() as connection:
            execute(
                connection,
                "UPDATE user_memories SET consent_status='revoked',deleted_at=now() WHERE id=:id",
                id=setting,
            )
        return body

    monkeypatch.setattr(ModelGateway, "chapter_answer", revoked)
    monkeypatch.setattr(
        ModelGateway, "personalize_chapter", lambda *a, **k: notes_called.append(True)
    )
    assert run_once(UUID(job["id"]))
    assert not notes_called
    assert read_private(client, product)["contextStale"]


def test_incomplete_catalog_citations_cannot_publish_or_override_safety(client, monkeypatch):
    register_login(client)
    product = ready_product(client)
    add_evidence(product, [(i, f"DEMO-100 CN 安全使用条件第{i}条。") for i in range(1, 16)])
    with transaction() as connection:
        manual = manuals.schedule(connection, UUID(product["productModelId"]))
    monkeypatch.setattr(
        ModelGateway, "chapter_answer", lambda *a, **k: "请带电拆开电源，绕过保护。[1]"
    )
    assert run_once(manual["job_id"])
    current = client.get(f"/api/v1/products/{product['productModelId']}/manual").json()
    assert current["status"] == "failed"
    assert not any("带电拆开" in (s["body"] or "") for s in current["sections"])
    monkeypatch.setattr(ModelGateway, "chapter_answer", lambda *a, **k: "仅归纳第一段。[1]")
    response = post(
        client,
        f"/products/{product['productModelId']}/manual-jobs",
        {},
        retry=str(manual["job_id"]),
    )
    assert response.status_code == 202
    assert run_once(UUID(response.json()["id"]))
    current = client.get(f"/api/v1/products/{product['productModelId']}/manual").json()
    assert current["status"] == "partial"
    assert any(
        s["generationStatus"] != "ready"
        for s in current["sections"]
        if s["sectionKey"].startswith("source-") and "第1–10页" in s["title"]
    )
