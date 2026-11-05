"""Production-role and immutable-evidence regressions from independent review."""

from uuid import uuid4

import pytest

from app.adapters import ModelGateway
from app.config import settings
from app.db import execute, one, transaction
from app.errors import DomainError
from app.services import knowledge
from app.services import supplemental_index as index
from app.services import supplemental_research as research
from app.services.products import build_for
from app.services.queue import claim
from tests.test_supplemental_index import TEXT, URL, build_supplement

pytestmark = pytest.mark.postgres


def test_real_api_role_can_lock_but_cannot_modify_reference_content(client, monkeypatch):
    _, model, _ = build_supplement(client, monkeypatch)
    rows = index.retrieve(model["id"], "pairing", [1.0] + [0.0] * 1023)
    with transaction() as c:
        execute(c, "SET LOCAL ROLE itemtalk_app")
        assert index.live(c, model["id"], rows)
        assert not one(
            c,
            "SELECT "
            "has_column_privilege(current_user,'supplemental_chunks','content','UPDATE') "
            "AS ok",
        )["ok"]
    with pytest.raises(Exception, match="immutable"):
        with transaction() as c:
            execute(c, "SET LOCAL ROLE itemtalk_app")
            execute(
                c,
                "UPDATE supplemental_chunks SET supplemental_document_id=:new WHERE id=:id",
                new=uuid4(),
                id=rows[0]["id"],
            )


@pytest.mark.parametrize("suffix", ["v2", "generation 2", "A", "升级版"])
def test_revision_suffixes_cannot_borrow_base_model_manual(client, monkeypatch, suffix):
    _, model, _ = build_supplement(client, monkeypatch, TEXT.replace("Ultra", "Ultra " + suffix))
    assert not index.retrieve(model["id"], "pairing", [1.0] + [0.0] * 1023)


def test_partial_vector_publication_freezes_the_complete_text_plan(client, monkeypatch):
    original = TEXT + "\nWireless pairing instructions and notes. " * 650
    calls = []

    def fail_after_first_batch(_gateway, texts, _subject):
        calls.append(True)
        if len(calls) > 1:
            raise DomainError(502, "UPSTREAM_MODEL_ERROR", "temporary")
        return [[1.0] + [0.0] * 1023 for _ in texts]

    with pytest.raises(DomainError):
        build_supplement(client, monkeypatch, original, embed=fail_after_first_batch)
    with transaction() as c:
        model = one(
            c,
            "SELECT m.*,b.name AS brand_name,b.official_domains FROM product_models m "
            "JOIN brands b ON b.id=m.brand_id",
        )
        job = one(c, "SELECT * FROM jobs WHERE job_type='research' AND status='running'")
        version = one(
            c,
            "SELECT v.* FROM document_versions v JOIN documents d ON d.id=v.document_id "
            "JOIN sources s ON s.id=d.source_id WHERE s.url=:u",
            u=URL,
        )
    monkeypatch.setattr(
        research, "parse_document", lambda *_a, **_kw: [(1, TEXT + "\nchanged OCR text")]
    )
    pages = research.pages_for(job, model, version, b"same immutable source", "text/html")
    assert "\n".join(text for _, text in pages).strip() == original.strip()
    monkeypatch.setattr(
        ModelGateway, "embed", lambda _g, texts, _s: [[1.0] + [0.0] * 1023 for _ in texts]
    )
    with pytest.raises(DomainError, match="正文|片段"):
        research.publish(job, model, version, [(1, TEXT + "\nchanged text " * 1800)], URL)


def test_reembedding_is_not_visible_until_every_batch_completes(client, monkeypatch):
    original = TEXT + "\nWireless pairing instructions and notes. " * 650
    _, model, _ = build_supplement(client, monkeypatch, original)
    monkeypatch.setenv("EMBEDDING_REVISION", "supplement-r2")
    settings.cache_clear()
    with transaction() as c:
        job = build_for(c, model["id"], None, None, force=True)
    job = claim(job["id"])
    calls = []

    def embed(_g, texts, _s):
        calls.append(True)
        if len(calls) > 1:
            raise DomainError(502, "UPSTREAM_MODEL_ERROR", "temporary")
        return [[1.0] + [0.0] * 1023 for _ in texts]

    monkeypatch.setattr(ModelGateway, "embed", embed)
    with pytest.raises(DomainError):
        knowledge.research(job)
    assert not index.retrieve(model["id"], "pairing", [1.0] + [0.0] * 1023)


def test_linked_download_cannot_redirect_to_arbitrary_html(client, monkeypatch):
    child = "https://manuals.plus/m/reference.pdf"

    def fetch(url):
        if url == URL:
            return (
                url,
                "text/html",
                (
                    "<article><h1>" + TEXT + "</h1><a href='" + child + "'>PDF</a></article>"
                ).encode(),
            )
        return "https://outside.example/landing", "text/html", ("<h1>" + TEXT + "</h1>").encode()

    _, model, _ = build_supplement(client, monkeypatch, fetch=fetch)
    assert all(
        row["url"] == URL for row in index.retrieve(model["id"], "pairing", [1.0] + [0.0] * 1023)
    )


def test_revoking_parent_page_withdraws_its_derived_pdf(client, monkeypatch):
    from tests.test_parser import pdf_bytes

    child = "https://manuals.plus/m/reference.pdf"

    def fetch(url):
        if url == URL:
            return (
                url,
                "text/html",
                (
                    "<article><h1>" + TEXT + "</h1><a href='" + child + "'>PDF</a></article>"
                ).encode(),
            )
        return url, "application/pdf", pdf_bytes(None, "", text=TEXT.encode())

    _, model, _ = build_supplement(client, monkeypatch, fetch=fetch)
    assert any(
        row["url"] == child for row in index.retrieve(model["id"], "pairing", [1.0] + [0.0] * 1023)
    )
    with transaction() as c:
        execute(c, "UPDATE sources SET verification_status='revoked' WHERE url=:u", u=URL)
    assert not index.retrieve(model["id"], "pairing", [1.0] + [0.0] * 1023)
