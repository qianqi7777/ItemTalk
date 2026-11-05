"""Boundary context comes from live immutable chunks, including a non-hit neighbor."""

import pytest

from app.adapters import ModelGateway
from app.db import execute, many, transaction
from app.errors import DomainError
from app.services import supplemental_index
from tests.test_supplemental_index import build_supplement

pytestmark = pytest.mark.postgres


def test_legacy_table_context_and_all_contributors_are_revalidated(client, monkeypatch):
    header = "ROSESELSA CERAMICS Ultra\nFunction | Left (L) | Right (R)\n"
    tail = "Play/Pause | ●"
    padding = "Notes\n" * ((2000 - len(header) - len(tail)) // 6)
    padding += " " * (2000 - len(header) - len(tail) - len(padding))
    text = header + padding + tail + "● | ●●\nAnswer Call | ● | ●\n"
    _, model, _ = build_supplement(client, monkeypatch, text)
    with transaction() as c:
        chunks = many(c, "SELECT * FROM supplemental_chunks ORDER BY chunk_no")
    assert chunks[0]["content"].endswith(tail)
    assert chunks[1]["content"].startswith("● | ●●")
    # The second half need not be a semantic/lexical hit to restore the row.
    retrieve = supplemental_index.retrieve
    monkeypatch.setattr(supplemental_index, "retrieve", lambda *args: retrieve(*args, limit=10)[:1])
    g = ModelGateway()
    try:
        result = supplemental_index.lookup(model, "Play/Pause", g, "table-test")
    finally:
        g.close()
    assert result and len(result["sources"]) == 1
    source = result["sources"][0]
    assert "Play/Pause | ●● | ●●" in source["excerpt"]
    assert len(source["excerpt"]) <= 2000
    with transaction() as c:
        supplemental_index.validate_lookup(c, model["id"], result)
        assert supplemental_index.availability(c, source)["availabilityStatus"] == "available"
        other = next(r for r in chunks if str(r["id"]) != source["id"])
        execute(c, "DELETE FROM supplemental_chunks WHERE id=:id", id=other["id"])
        assert supplemental_index.availability(c, source)["availabilityStatus"] == "withdrawn"
        with pytest.raises(DomainError, match="补充资料"):
            supplemental_index.validate_lookup(c, model["id"], result)


def test_repeated_context_proofs_validate_without_losing_old_snapshot_support(client, monkeypatch):
    text = "ROSESELSA CERAMICS Ultra\n" + "Full line\n" * 220
    _, model, _ = build_supplement(client, monkeypatch, text)
    g = ModelGateway()
    try:
        result = supplemental_index.lookup(model, "Full line", g, "table-test")
    finally:
        g.close()
    assert result and len(result["sources"]) > 1
    with transaction() as c:
        supplemental_index.validate_lookup(c, model["id"], result)
        legacy = dict(result["sources"][0])
        legacy["_supplemental"] = {
            k: v for k, v in legacy["_supplemental"].items() if k not in {"context", "page"}
        }
        supplemental_index.validate_lookup(c, model["id"], {**result, "sources": [legacy]})
        assert supplemental_index.availability(c, legacy)["availabilityStatus"] == "available"


def test_frozen_page_change_withdraws_context_snapshot(client, monkeypatch):
    _, model, _ = build_supplement(client, monkeypatch)
    g = ModelGateway()
    try:
        result = supplemental_index.lookup(model, "wireless pairing", g, "table-test")
    finally:
        g.close()
    with transaction() as c:
        execute(c, "UPDATE document_pages SET extracted_text='Changed frozen page'")
        assert (
            supplemental_index.availability(c, result["sources"][0])["availabilityStatus"]
            == "withdrawn"
        )
        with pytest.raises(DomainError):
            supplemental_index.validate_lookup(c, model["id"], result)


def test_lookup_does_not_bridge_a_whitespace_only_legacy_slice(client, monkeypatch):
    title = "ROSESELSA CERAMICS Ultra\n"
    tail = "Play/Pause | ●"
    first = title + "Notes\n" * ((2000 - len(title) - len(tail)) // 6)
    first += " " * (2000 - len(first) - len(tail)) + tail
    text = first + "\n " * 1000 + "● | ●●\n"
    _, model, _ = build_supplement(client, monkeypatch, text)
    with transaction() as c:
        assert len(many(c, "SELECT id FROM supplemental_chunks")) == 2
    g = ModelGateway()
    try:
        result = supplemental_index.lookup(model, "Play/Pause", g, "table-test")
    finally:
        g.close()
    assert result
    assert all("Play/Pause | ●● | ●●" not in s["excerpt"] for s in result["sources"])
