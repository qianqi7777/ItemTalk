"""Direct manufacturer links can authenticate one PDF without trusting its whole CDN."""

from uuid import UUID

import pytest

from app.adapters import ModelGateway, SearchGateway
from app.config import settings
from app.db import execute, one, transaction
from app.services import knowledge
from app.services.manual_provenance import linked_manual_is_official
from app.storage import Storage
from app.worker import run_once
from tests.conftest import register_login
from tests.test_api import identified_product
from tests.test_parser import pdf_bytes

pytestmark = pytest.mark.postgres


def build(client, monkeypatch, *, redirect=False, direct_search=False):
    register_login(client)
    product = identified_product(client)
    with transaction() as connection:
        execute(connection, "UPDATE brands SET official_domains='[\"brand.example\"]'::jsonb")
    monkeypatch.setenv("MODEL_PROVIDER", "dashscope")
    settings.cache_clear()
    parent = "https://brand.example/support"
    pdf = "https://cdn.example/manual.pdf"
    monkeypatch.setattr(
        SearchGateway,
        "search",
        lambda *_: [{"url": pdf if direct_search else parent, "title": "Manual"}],
    )

    def fetch(url):
        if url == parent:
            return (
                url,
                "text/html",
                b'<h1>Downloads</h1><a href="https://cdn.example/manual.pdf">Manual</a>',
            )
        assert url == pdf
        return (
            ("https://other.example/manual.pdf" if redirect else url),
            "application/pdf",
            pdf_bytes(None, "", text=b"DEMO-100 CN Official manual. Press power."),
        )

    monkeypatch.setattr(knowledge, "fetch_public", fetch)
    monkeypatch.setattr(
        ModelGateway, "embed", lambda _self, texts, _subject: [[1.0] + [0.0] * 1023 for _ in texts]
    )
    assert run_once(UUID(product["knowledgeJobId"]))
    return product


def test_readable_manual_linked_from_official_html_publishes_with_provenance(client, monkeypatch):
    product = build(client, monkeypatch)
    assert client.get(f"/api/v1/me/products/{product['id']}").json()["knowledgeStatus"] == "ready"
    with transaction() as c:
        child = one(
            c,
            "SELECT v.* FROM document_versions v JOIN documents d ON d.id=v.document_id "
            "JOIN sources s ON s.id=d.source_id WHERE s.domain='cdn.example'",
        )
        assert (
            child["official_parent_version_id"]
            and child["official_link_url"] == "https://cdn.example/manual.pdf"
        )
        assert child["verification_status"] == "verified"


@pytest.mark.parametrize("redirect,direct", [(True, False), (False, True)])
def test_direct_search_or_redirect_cannot_grant_cdn_trust(client, monkeypatch, redirect, direct):
    product = build(client, monkeypatch, redirect=redirect, direct_search=direct)
    assert client.get(f"/api/v1/me/products/{product['id']}").json()["knowledgeStatus"] != "ready"


@pytest.mark.parametrize("target", ["source", "version"])
def test_withdrawing_official_parent_revokes_dependent_manual(client, monkeypatch, target):
    build(client, monkeypatch)
    with transaction() as c:
        parent = one(
            c,
            "SELECT v.id,d.source_id FROM document_versions v "
            "JOIN documents d ON d.id=v.document_id "
            "JOIN sources s ON s.id=d.source_id WHERE s.url='https://brand.example/support'",
        )
        if target == "source":
            execute(
                c,
                "UPDATE sources SET verification_status='revoked' WHERE id=:id",
                id=parent["source_id"],
            )
        else:
            execute(
                c,
                "UPDATE document_versions SET verification_status='revoked' WHERE id=:id",
                id=parent["id"],
            )
        result = one(
            c,
            "SELECT s.verification_status AS s,v.verification_status AS v FROM document_versions v "
            "JOIN documents d ON d.id=v.document_id JOIN sources s ON s.id=d.source_id "
            "WHERE s.domain='cdn.example'",
        )
        assert result and result["s"] == result["v"] == "revoked"
        assert (
            one(
                c, "SELECT count(*) AS n FROM knowledge_chunks WHERE verification_status='verified'"
            )["n"]
            == 0
        )


def child_document(c):
    return one(
        c,
        "SELECT v.*,s.url,a.object_key FROM document_versions v "
        "JOIN documents d ON d.id=v.document_id JOIN sources s ON s.id=d.source_id "
        "JOIN assets a ON a.id=v.asset_id WHERE s.domain='cdn.example'",
    )


def test_link_proof_rejects_missing_link_and_changed_parent_bytes(client, monkeypatch):
    build(client, monkeypatch)
    with transaction() as c:
        child = child_document(c)
        child["official_domains"] = ["brand.example"]
        assert linked_manual_is_official(c, child)
        assert not linked_manual_is_official(
            c,
            {
                **child,
                "url": "https://cdn.example/not-linked.pdf",
                "official_link_url": "https://cdn.example/not-linked.pdf",
            },
        )
        parent = one(
            c,
            "SELECT a.object_key FROM document_versions v JOIN assets a ON a.id=v.asset_id "
            "WHERE v.id=:id",
            id=child["official_parent_version_id"],
        )
        Storage().path(parent["object_key"]).write_bytes(b"changed document")
        assert not linked_manual_is_official(c, child)


def test_existing_manual_keeps_its_valid_original_proof_when_parent_page_changes(
    client, monkeypatch
):
    product = build(client, monkeypatch)
    with transaction() as c:
        child = child_document(c)
        model = {"id": UUID(product["productModelId"]), "official_domains": ["brand.example"]}
        parent = knowledge.persist_document(
            c,
            model,
            "https://brand.example/support",
            "Support",
            "text/html",
            b'<h1>New layout</h1><a href="https://cdn.example/manual.pdf">Manual</a>',
            [],
            False,
        )
        reused = knowledge.persist_document(
            c,
            model,
            child["url"],
            "Manual",
            "application/pdf",
            Storage().read(child["object_key"]),
            [],
            False,
            official_parent_version_id=parent["id"],
            official_link_url=child["url"],
        )
        assert reused["id"] == child["id"]
        assert reused["official_parent_version_id"] == child["official_parent_version_id"]


def test_proof_cannot_be_rewritten_in_database(client, monkeypatch):
    from sqlalchemy.exc import DBAPIError

    build(client, monkeypatch)
    with pytest.raises(DBAPIError, match="immutable"):
        with transaction() as c:
            execute(
                c,
                "UPDATE document_versions SET official_link_url='https://cdn.example/other.pdf' "
                "WHERE official_parent_version_id IS NOT NULL",
            )


def test_brand_authority_removed_during_parse_cannot_publish_cdn_manual(client, monkeypatch):
    original = knowledge.linked_manual_is_official
    removed = False

    def revoke_between_checks(connection, document, *, lock=False):
        nonlocal removed
        trusted = original(connection, document, lock=lock)
        if trusted and not lock and not removed:
            removed = True
            with transaction() as other:
                execute(other, "UPDATE brands SET official_domains='[]'::jsonb")
        return trusted

    monkeypatch.setattr(knowledge, "linked_manual_is_official", revoke_between_checks)
    product = build(client, monkeypatch)
    assert removed
    assert client.get(f"/api/v1/me/products/{product['id']}").json()["knowledgeStatus"] != "ready"
    with transaction() as c:
        assert (
            one(
                c, "SELECT count(*) AS n FROM knowledge_chunks WHERE verification_status='verified'"
            )["n"]
            == 0
        )


def test_child_version_revoked_during_publication_stays_revoked(client, monkeypatch):
    original = knowledge.linked_manual_is_official
    revoked = False

    def revoke_after_parent_lock(connection, document, *, lock=False):
        nonlocal revoked
        trusted = original(connection, document, lock=lock)
        if trusted and lock and not revoked:
            revoked = True
            with transaction() as other:
                execute(
                    other,
                    "UPDATE document_versions SET verification_status='revoked' WHERE id=:id",
                    id=document["id"],
                )
        return trusted

    monkeypatch.setattr(knowledge, "linked_manual_is_official", revoke_after_parent_lock)
    product = build(client, monkeypatch)
    assert revoked
    assert client.get(f"/api/v1/me/products/{product['id']}").json()["knowledgeStatus"] != "ready"
    with transaction() as c:
        child = child_document(c)
        assert child["verification_status"] == "revoked"
        assert (
            one(
                c, "SELECT count(*) AS n FROM knowledge_chunks WHERE verification_status='verified'"
            )["n"]
            == 0
        )


def test_redirect_from_linked_cdn_to_official_html_does_not_abort_other_manuals(
    client, monkeypatch
):
    register_login(client)
    product = identified_product(client)
    with transaction() as c:
        execute(c, "UPDATE brands SET official_domains='[\"brand.example\"]'::jsonb")
    monkeypatch.setenv("MODEL_PROVIDER", "dashscope")
    settings.cache_clear()
    monkeypatch.setattr(
        SearchGateway,
        "search",
        lambda *_: [{"url": "https://brand.example/support", "title": "Support"}],
    )

    def fetch(url):
        if url.endswith("/support"):
            return (
                url,
                "text/html",
                (
                    b'<a href="https://cdn.example/bad.pdf">Broken</a>'
                    b'<a href="https://brand.example/good.pdf">Good</a>'
                ),
            )
        if "cdn.example" in url:
            return "https://brand.example/not-found", "text/html", b"not found"
        return url, "application/pdf", pdf_bytes(None, "", text=b"DEMO-100 CN Official manual.")

    monkeypatch.setattr(knowledge, "fetch_public", fetch)
    monkeypatch.setattr(
        ModelGateway, "embed", lambda _s, texts, _u: [[1.0] + [0.0] * 1023 for _ in texts]
    )
    assert run_once(UUID(product["knowledgeJobId"]))
    assert client.get(f"/api/v1/me/products/{product['id']}").json()["knowledgeStatus"] == "ready"


def test_direct_child_withdrawal_revokes_source_and_chunks(client, monkeypatch):
    build(client, monkeypatch)
    with transaction() as c:
        child = child_document(c)
        execute(
            c,
            "UPDATE document_versions SET verification_status='revoked' WHERE id=:id",
            id=child["id"],
        )
        state = one(
            c,
            "SELECT s.verification_status FROM documents d "
            "JOIN sources s ON s.id=d.source_id WHERE d.id=:id",
            id=child["document_id"],
        )
        assert state["verification_status"] == "revoked"
        assert (
            one(
                c,
                "SELECT count(*) AS n FROM knowledge_chunks WHERE document_version_id=:id "
                "AND verification_status='verified'",
                id=child["id"],
            )["n"]
            == 0
        )
