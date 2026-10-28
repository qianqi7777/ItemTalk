"""Scanned manual publication preserves authority, page citations and retry caches."""

import io
import json
from uuid import UUID

import pytest
from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter

from app.adapters import ModelGateway, SearchGateway
from app.config import settings
from app.db import execute, json_value, many, one, transaction
from app.errors import DomainError
from app.services import knowledge
from app.worker import run_once
from tests.conftest import register_login
from tests.test_api import identified_product
from tests.test_parser import pdf_bytes

pytestmark = pytest.mark.postgres


def scanned_pdf(count=1, *, native=False):
    image = Image.new("RGB", (600, 800), "white")
    ImageDraw.Draw(image).text(
        (30, 40), "DEMO-100 CN\nPress power for three seconds.", fill="black"
    )
    output = io.BytesIO()
    image.save(output, "PDF")
    writer = PdfWriter()
    if native:
        writer.append(
            io.BytesIO(pdf_bytes(None, "", text=b"DEMO-100 CN. Native safety instructions."))
        )
    for _ in range(count):
        writer.add_page(PdfReader(io.BytesIO(output.getvalue())).pages[0])
    result = io.BytesIO()
    writer.write(result)
    return result.getvalue()


def setup_manual(client, monkeypatch, data, responder):
    register_login(client)
    product = identified_product(client)
    with transaction() as c:
        execute(c, "UPDATE brands SET official_domains='[\"brand.example\"]'::jsonb")
    monkeypatch.setenv("MODEL_PROVIDER", "dashscope")
    settings.cache_clear()
    monkeypatch.setattr(
        SearchGateway,
        "search",
        lambda *_: [{"url": "https://brand.example/manual.pdf", "title": "Manual"}],
    )
    monkeypatch.setattr(knowledge, "fetch_public", lambda url: (url, "application/pdf", data))
    monkeypatch.setattr(ModelGateway, "request", responder)
    monkeypatch.setattr(
        ModelGateway, "embed", lambda _self, texts, _subject: [[1.0] + [0.0] * 1023 for _ in texts]
    )
    return product


def response(text="DEMO-100 CN\nPress power for three seconds."):
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": json.dumps({"text": text, "unreadable": False})},
            }
        ]
    }


def test_mixed_scanned_manual_publishes_text_with_exact_original_page_numbers(client, monkeypatch):
    calls = []

    def respond(_self, role, _endpoint, payload):
        calls.append(payload)
        assert role == "vision"
        return response()

    product = setup_manual(client, monkeypatch, scanned_pdf(native=True), respond)
    assert run_once(UUID(product["knowledgeJobId"]))
    assert client.get(f"/api/v1/me/products/{product['id']}").json()["knowledgeStatus"] == "ready"
    assert len(calls) == 1
    assert "2" in calls[0]["messages"][-1]["content"][0]["text"]
    with transaction() as c:
        pages = many(
            c, "SELECT page_no,extracted_text,locator FROM document_pages ORDER BY page_no"
        )
        assert [p["page_no"] for p in pages] == [1, 2]
        assert "Native safety" in pages[0]["extracted_text"]
        assert pages[1]["locator"]["extractionMethod"] == "vision_ocr"
        assert (
            one(
                c, "SELECT count(*) AS n FROM knowledge_chunks WHERE verification_status='verified'"
            )["n"]
            > 0
        )


def test_ocr_retry_reuses_completed_pages_after_model_quota_failure(client, monkeypatch):
    pages_seen = []
    failing = True

    def respond(_self, _role, _endpoint, payload):
        page = payload["messages"][-1]["content"][0]["text"]
        pages_seen.append(page)
        if "2" in page and failing:
            raise DomainError(429, "MODEL_BUDGET_EXCEEDED", "模型额度已用完")
        return response()

    product = setup_manual(client, monkeypatch, scanned_pdf(2), respond)
    assert run_once(UUID(product["knowledgeJobId"]))
    with transaction() as c:
        assert one(c, "SELECT count(*) AS n FROM document_pages")["n"] == 1
        assert one(c, "SELECT count(*) AS n FROM knowledge_chunks")["n"] == 0
        execute(c, "UPDATE jobs SET available_at=now() WHERE id=:id", id=product["knowledgeJobId"])
    failing = False
    assert run_once(UUID(product["knowledgeJobId"]))
    assert len([p for p in pages_seen if "1" in p]) == 1
    assert len([p for p in pages_seen if "2" in p]) == 2
    assert client.get(f"/api/v1/me/products/{product['id']}").json()["knowledgeStatus"] == "ready"


def test_over_twenty_scanned_pages_rejects_before_any_model_call(client, monkeypatch):
    def unexpected(*_args):
        pytest.fail("An oversized scan must be rejected before any paid model call")

    product = setup_manual(client, monkeypatch, scanned_pdf(21), unexpected)
    assert run_once(UUID(product["knowledgeJobId"]))
    with transaction() as c:
        job = one(c, "SELECT * FROM jobs WHERE id=:id", id=product["knowledgeJobId"])
        assert "20" in job["last_error"] and "扫描" in job["last_error"]
        assert one(c, "SELECT count(*) AS n FROM knowledge_chunks")["n"] == 0


def test_source_revoked_during_ocr_cannot_cache_or_publish_the_result(client, monkeypatch):
    calls = []

    def revoke(_self, _role, _endpoint, _payload):
        calls.append(True)
        with transaction() as c:
            execute(c, "UPDATE sources SET verification_status='revoked'")
        return response()

    product = setup_manual(client, monkeypatch, scanned_pdf(), revoke)
    assert run_once(UUID(product["knowledgeJobId"]))
    assert calls == [True]
    with transaction() as c:
        assert one(c, "SELECT count(*) AS n FROM knowledge_chunks")["n"] == 0
        assert one(c, "SELECT count(*) AS n FROM document_pages")["n"] == 0
        assert (
            one(
                c,
                "SELECT count(*) AS n FROM document_versions WHERE verification_status='verified'",
            )["n"]
            == 0
        )


def test_normal_text_pdf_does_not_consume_vision_calls(client, monkeypatch):
    def unexpected(*_args):
        pytest.fail("Native text extraction must not consume a vision call")

    product = setup_manual(
        client, monkeypatch, pdf_bytes(None, "", text=b"DEMO-100 CN Instructions"), unexpected
    )
    assert run_once(UUID(product["knowledgeJobId"]))
    assert client.get(f"/api/v1/me/products/{product['id']}").json()["knowledgeStatus"] == "ready"


def test_truncated_ocr_is_not_cached_or_published(client, monkeypatch):
    def truncated(*_args):
        value = response()
        value["choices"][0]["finish_reason"] = "length"
        return value

    product = setup_manual(client, monkeypatch, scanned_pdf(), truncated)
    assert run_once(UUID(product["knowledgeJobId"]))
    with transaction() as c:
        assert one(c, "SELECT count(*) AS n FROM document_pages")["n"] == 0
        assert one(c, "SELECT count(*) AS n FROM knowledge_chunks")["n"] == 0
        job = one(
            c, "SELECT status,last_error FROM jobs WHERE id=:id", id=product["knowledgeJobId"]
        )
        assert job["status"] == "partial_success" and "扫描页" in job["last_error"]


def test_unreadable_scan_does_not_hide_a_later_readable_official_manual(client, monkeypatch):
    def unreadable(*_args):
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": json.dumps({"text": "", "unreadable": True})},
                }
            ]
        }

    product = setup_manual(client, monkeypatch, scanned_pdf(), unreadable)
    monkeypatch.setattr(
        SearchGateway,
        "search",
        lambda *_: [
            {"url": "https://brand.example/a.pdf", "title": "Scan"},
            {"url": "https://brand.example/b.pdf", "title": "Text"},
        ],
    )
    monkeypatch.setattr(
        knowledge,
        "fetch_public",
        lambda url: (
            url,
            "application/pdf",
            scanned_pdf()
            if url.endswith("a.pdf")
            else pdf_bytes(None, "", text=b"DEMO-100 CN official instructions"),
        ),
    )
    assert run_once(UUID(product["knowledgeJobId"]))
    assert client.get(f"/api/v1/me/products/{product['id']}").json()["knowledgeStatus"] == "ready"


def test_retry_reuses_a_completed_blank_ocr_page(client, monkeypatch):
    calls = []
    failing = True

    def respond(_self, _role, _endpoint, payload):
        page = payload["messages"][-1]["content"][0]["text"]
        calls.append(page)
        if "1" in page:
            return response("")
        if failing:
            raise DomainError(429, "MODEL_BUDGET_EXCEEDED", "模型额度已用完")
        return response()

    product = setup_manual(client, monkeypatch, scanned_pdf(2), respond)
    assert run_once(UUID(product["knowledgeJobId"]))
    with transaction() as c:
        first = one(c, "SELECT extracted_text,locator FROM document_pages")
        assert first["extracted_text"] == "" and first["locator"]["complete"] is True
        execute(c, "UPDATE jobs SET available_at=now() WHERE id=:id", id=product["knowledgeJobId"])
    failing = False
    assert run_once(UUID(product["knowledgeJobId"]))
    assert len([page for page in calls if "1" in page]) == 1
    assert client.get(f"/api/v1/me/products/{product['id']}").json()["knowledgeStatus"] == "ready"


def test_changing_vision_model_recomputes_unpublished_cached_pages(client, monkeypatch):
    calls = []
    failing = True

    def respond(_self, _role, _endpoint, payload):
        page = payload["messages"][-1]["content"][0]["text"]
        calls.append((payload["model"], page))
        if "2" in page and failing:
            raise DomainError(429, "MODEL_BUDGET_EXCEEDED", "模型额度已用完")
        return response()

    product = setup_manual(client, monkeypatch, scanned_pdf(2), respond)
    assert run_once(UUID(product["knowledgeJobId"]))
    with transaction() as c:
        execute(c, "UPDATE jobs SET available_at=now() WHERE id=:id", id=product["knowledgeJobId"])
    failing = False
    monkeypatch.setenv("VISION_MODEL", "other-vision-model")
    settings.cache_clear()
    assert run_once(UUID(product["knowledgeJobId"]))
    assert len([item for item in calls if "1" in item[1]]) == 2
    assert calls[-2][0] == "other-vision-model"
    assert client.get(f"/api/v1/me/products/{product['id']}").json()["knowledgeStatus"] == "ready"


def test_ocr_deadline_reports_saved_progress_and_never_calls_model(client, monkeypatch):
    import time

    from app.deadlines import request_scope
    from app.services import document_extraction

    monkeypatch.setattr(
        document_extraction, "request_scope", lambda _deadline: request_scope(time.monotonic() - 1)
    )

    def unexpected(*_args):
        pytest.fail("Expired extraction must not call the model")

    product = setup_manual(client, monkeypatch, scanned_pdf(), unexpected)
    assert run_once(UUID(product["knowledgeJobId"]))
    with transaction() as c:
        job = one(
            c, "SELECT status,last_error FROM jobs WHERE id=:id", id=product["knowledgeJobId"]
        )
        assert job["status"] == "queued" and "10 分钟" in job["last_error"]


def test_concurrent_cached_transcription_is_the_content_checked_for_publication(
    client, monkeypatch
):
    def respond(_self, _role, _endpoint, _payload):
        import hashlib

        config = _self.config
        fingerprint = hashlib.sha256(
            json.dumps(
                [config.vision_provider, config.vision_base_url, config.vision_model]
            ).encode()
        ).hexdigest()
        with transaction() as c:
            version = one(c, "SELECT id FROM document_versions LIMIT 1")
            execute(
                c,
                "INSERT INTO document_pages(document_version_id,page_no,extracted_text,locator) "
                "VALUES(:v,1,'OTHER-200 CN unrelated manual',CAST(:locator AS jsonb))",
                v=version["id"],
                locator=json_value(
                    {
                        "extractionMethod": "vision_ocr",
                        "complete": True,
                        "configFingerprint": fingerprint,
                    }
                ),
            )
        return response()

    product = setup_manual(client, monkeypatch, scanned_pdf(), respond)
    assert run_once(UUID(product["knowledgeJobId"]))
    with transaction() as c:
        assert (
            one(c, "SELECT status FROM jobs WHERE id=:id", id=product["knowledgeJobId"])["status"]
            == "partial_success"
        )
        assert one(c, "SELECT count(*) AS n FROM knowledge_chunks")["n"] == 0
        assert (
            one(c, "SELECT verification_status FROM document_versions")["verification_status"]
            != "verified"
        )
