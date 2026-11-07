"""Frozen public manuals remain usable under reviewed brand aliases during a website outage."""

import pytest

from app.adapters import ModelGateway
from app.db import execute, one, transaction
from app.services import knowledge, supplemental_index, supplemental_research
from app.services.products import build_for, product_model
from app.services.queue import claim
from app.storage import Storage
from tests.test_supplemental_index import TEXT, URL, build_supplement

pytestmark = pytest.mark.postgres


def alias_model(*, code="Ceramics Ultra", region=None, hardware=None):
    with transaction() as c:
        model = product_model(c, "rose technics", code, region, hardware)
        execute(
            c,
            "UPDATE product_models SET identity_status='user_confirmed' WHERE id=:id",
            id=model["id"],
        )
        return one(
            c,
            "SELECT m.*,b.name AS brand_name,b.official_domains FROM product_models m "
            "JOIN brands b ON b.id=m.brand_id WHERE m.id=:id",
            id=model["id"],
        )


def research_for(model, *, force=False):
    with transaction() as c:
        job = build_for(c, model["id"], None, None, force=force)
    job = claim(job["id"])
    knowledge.research(job)
    return job


def test_saved_public_manual_builds_alias_index_without_any_network_or_ocr(client, monkeypatch):
    _, old, _ = build_supplement(client, monkeypatch)
    model = alias_model()

    def offline(*_a, **_kw):
        pytest.fail("A saved matching public manual must not access search, websites or OCR")

    monkeypatch.setattr(knowledge, "discover_candidates", offline)
    monkeypatch.setattr(knowledge, "fetch_public", offline)
    monkeypatch.setattr(supplemental_research, "discover", offline)
    monkeypatch.setattr(supplemental_research, "fetch_public", offline)
    monkeypatch.setattr(supplemental_research, "extract_scanned_pages", offline)
    research_for(model)
    rows = supplemental_index.retrieve(model["id"], "wireless pairing", [1.0] + [0.0] * 1023)
    original = supplemental_index.retrieve(old["id"], "wireless pairing", [1.0] + [0.0] * 1023)
    assert rows and rows[0]["url"] == URL
    assert rows[0]["document_version_id"] == original[0]["document_version_id"]
    assert rows[0]["content"] == TEXT and rows[0]["page_start"] == 1
    with transaction() as c:
        assert one(c, "SELECT count(*) AS n FROM knowledge_chunks")["n"] == 0
        assert one(
            c, "SELECT is_official,verification_status FROM sources WHERE url=:u", u=URL
        ) == {"is_official": False, "verification_status": "pending"}
    monkeypatch.setattr(ModelGateway, "embed", offline)
    research_for(model)
    assert supplemental_index.retrieve(model["id"], "wireless pairing", [1.0] + [0.0] * 1023)


@pytest.mark.parametrize(
    "difference", ["model", "region", "hardware", "source", "document", "asset", "hash"]
)
def test_cache_cannot_cross_identity_or_revive_withdrawn_or_corrupt_evidence(
    client, monkeypatch, difference
):
    build_supplement(client, monkeypatch)
    model = alias_model(
        code="Ceramics Ultra Pro" if difference == "model" else "Ceramics Ultra",
        region="CN" if difference == "region" else None,
        hardware="v2" if difference == "hardware" else None,
    )
    with transaction() as c:
        if difference == "source":
            execute(c, "UPDATE sources SET verification_status='revoked' WHERE url=:u", u=URL)
        elif difference == "document":
            execute(c, "UPDATE supplemental_documents SET status='revoked'")
        elif difference == "asset":
            execute(c, "UPDATE assets SET status='deleted' WHERE scope='public'")
        elif difference == "hash":
            asset = one(
                c,
                "SELECT a.object_key FROM assets a JOIN document_versions v ON v.asset_id=a.id "
                "JOIN supplemental_documents d ON d.document_version_id=v.id LIMIT 1",
            )
            Storage().path(asset["object_key"]).write_bytes(b"corrupt frozen source")
    monkeypatch.setattr(knowledge, "discover_candidates", lambda *_: [])
    monkeypatch.setattr(knowledge, "manufacturer_entries", lambda *_: [])
    monkeypatch.setattr(supplemental_research, "discover", lambda *_: [])
    research_for(model)
    assert not supplemental_index.retrieve(model["id"], "pairing", [1.0] + [0.0] * 1023)


def test_alias_reuse_rechecks_the_frozen_parent_download_link(client, monkeypatch):
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

    build_supplement(client, monkeypatch, fetch=fetch)
    with transaction() as c:
        # Withdraw the originating page. Its derived PDF remains on disk.
        execute(c, "UPDATE sources SET verification_status='revoked' WHERE url=:u", u=URL)
    model = alias_model()
    monkeypatch.setattr(knowledge, "discover_candidates", lambda *_: [])
    monkeypatch.setattr(knowledge, "manufacturer_entries", lambda *_: [])
    monkeypatch.setattr(supplemental_research, "discover", lambda *_: [])
    research_for(model)
    assert not supplemental_index.retrieve(model["id"], "pairing", [1.0] + [0.0] * 1023)


def test_reuse_runs_under_the_production_worker_role(client, monkeypatch):
    from contextlib import contextmanager

    from app.services import queue, supplemental_reuse

    build_supplement(client, monkeypatch)
    model = alias_model()

    @contextmanager
    def worker_transaction():
        with transaction() as c:
            execute(c, "SET LOCAL ROLE itemtalk_worker")
            yield c

    for module in (knowledge, supplemental_research, supplemental_reuse, queue):
        monkeypatch.setattr(module, "transaction", worker_transaction)
    monkeypatch.setattr(
        knowledge, "discover_candidates", lambda *_: pytest.fail("Use saved manual")
    )
    research_for(model)
    assert supplemental_index.retrieve(model["id"], "pairing", [1.0] + [0.0] * 1023)


def test_explicit_official_refresh_is_not_suppressed_by_a_saved_third_party_manual(
    client, monkeypatch
):
    _, model, _ = build_supplement(client, monkeypatch)
    official = "https://roseselsa.com/manuals/ceramics-ultra.txt"
    with transaction() as c:
        execute(c, "UPDATE brands SET official_domains='[\"roseselsa.com\"]'::jsonb")
    monkeypatch.setattr(
        knowledge,
        "discover_candidates",
        lambda *_: [{"url": official, "title": "CERAMICS Ultra manual"}],
    )
    monkeypatch.setattr(knowledge, "fetch_public", lambda u: (u, "text/plain", TEXT.encode()))
    job = research_for(model, force=True)
    with transaction() as c:
        assert one(c, "SELECT status FROM jobs WHERE id=:j", j=job["id"])["status"] == "succeeded"
        assert one(c, "SELECT count(*) AS n FROM knowledge_chunks")["n"] > 0


@pytest.mark.parametrize("corruption", ["frozen_page", "asset"])
def test_live_discovery_cannot_publish_a_saved_version_rejected_by_integrity_checks(
    client, monkeypatch, corruption
):
    from app.errors import DomainError

    build_supplement(client, monkeypatch)
    with transaction() as c:
        if corruption == "frozen_page":
            execute(
                c,
                "UPDATE document_pages SET extracted_text=:text",
                text=TEXT + "\nChanged instructions.",
            )
        else:
            row = one(
                c,
                "SELECT a.object_key FROM assets a JOIN document_versions v ON v.asset_id=a.id "
                "JOIN supplemental_documents d ON d.document_version_id=v.id LIMIT 1",
            )
            Storage().path(row["object_key"]).write_bytes(b"Corrupt original asset")
    model = alias_model()
    # The ordinary discovery/download path still returns the original URL and bytes.
    try:
        research_for(model)
    except DomainError as error:
        assert error.code == "SOURCE_UNVERIFIED"
    assert not supplemental_index.retrieve(model["id"], "pairing", [1.0] + [0.0] * 1023)


def test_official_throttling_still_tries_a_different_reviewed_host(client, monkeypatch):
    from app.errors import DomainError

    _, model, _ = build_supplement(client, monkeypatch)
    with transaction() as c:
        execute(c, "UPDATE supplemental_documents SET status='revoked'")
        execute(
            c, 'UPDATE brands SET official_domains=\'["roseselsa.com","rosetechnics.com"]\'::jsonb'
        )
    first = "https://roseselsa.com/manuals/ceramics-ultra.txt"
    second = "https://rosetechnics.com/manuals/ceramics-ultra.txt"
    calls = []
    monkeypatch.setattr(
        knowledge,
        "discover_candidates",
        lambda *_: [{"url": u, "title": "CERAMICS Ultra manual"} for u in (first, second)],
    )

    def fetch(url):
        calls.append(url)
        if url == first:
            raise DomainError(503, "SOURCE_RATE_LIMITED", "Website throttled")
        return url, "text/plain", TEXT.encode()

    monkeypatch.setattr(knowledge, "fetch_public", fetch)
    monkeypatch.setattr(supplemental_research, "discover", lambda *_: [])
    research_for(model, force=True)
    assert calls == [first, second]
    with transaction() as c:
        assert one(c, "SELECT count(*) AS n FROM knowledge_chunks")["n"] > 0
