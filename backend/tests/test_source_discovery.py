"""Bounded discovery follows official links without admitting arbitrary downloads."""

import pytest

from app.errors import DomainError
from app.services.source_discovery import discover_candidates, manual_links


def test_official_download_page_resolves_relative_pdf_and_rejects_external_or_executable():
    links = manual_links(
        "https://brand.example/support/G3/",
        b'<a href="../files/G3.pdf">Manual</a><a href="https://evil.example/G3.pdf">Manual</a>'
        b'<a href="/tool.exe">Download</a><a href="../files/G3.pdf#p=2">same</a>',
        ["brand.example"],
    )
    assert links == [{"url": "https://brand.example/support/files/G3.pdf", "title": "Manual"}]


def test_registered_sources_survive_search_outage():
    def unavailable(_query):
        raise DomainError(502, "UPSTREAM_SEARCH_ERROR", "temporary error")

    results = discover_candidates(
        {
            "brand_name": "Brand",
            "model_code": "G3",
            "region": None,
            "official_domains": ["brand.example"],
        },
        [{"url": "https://brand.example/G3.pdf", "title": "Known official manual"}],
        unavailable,
    )
    assert results == [{"url": "https://brand.example/G3.pdf", "title": "Known official manual"}]


def test_search_falls_back_to_english_but_stays_bounded_and_deduplicated():
    queries = []

    def search(query):
        queries.append(query)
        return (
            []
            if len(queries) == 1
            else [{"url": "https://brand.example/G3.pdf", "title": "Manual"}] * 2
        )

    results = discover_candidates(
        {
            "brand_name": "Brand",
            "model_code": "G3",
            "region": None,
            "official_domains": ["brand.example"],
        },
        [],
        search,
    )
    assert len(results) == 1 and len(queries) == 2 and "manual" in queries[1]


def test_quota_exhaustion_is_not_retried_as_a_search_failure():
    calls = []

    def denied(query):
        calls.append(query)
        raise DomainError(429, "RATE_LIMITED", "Budget exhausted")

    with pytest.raises(DomainError):
        discover_candidates(
            {
                "brand_name": "B",
                "model_code": "G3",
                "region": None,
                "official_domains": ["brand.example"],
            },
            [],
            denied,
        )
    assert len(calls) == 1


def test_registered_but_stale_source_does_not_suppress_a_working_replacement():
    results = discover_candidates(
        {
            "brand_name": "Brand",
            "model_code": "G3",
            "region": None,
            "official_domains": ["brand.example"],
        },
        [{"url": "https://brand.example/old.pdf", "title": "Old"}],
        lambda _query: [{"url": "https://brand.example/new.pdf", "title": "Current"}],
    )
    assert [row["url"] for row in results] == [
        "https://brand.example/old.pdf",
        "https://brand.example/new.pdf",
    ]


def test_malformed_html_link_cannot_abort_the_rest_of_the_download_page():
    links = manual_links(
        "https://brand.example/",
        b'<a href="https://[">bad</a><a href="/manual.pdf">Manual</a>',
        ["brand.example"],
    )
    assert [row["url"] for row in links] == ["https://brand.example/manual.pdf"]


def test_full_registered_candidate_list_still_retains_a_discovered_replacement():
    results = discover_candidates(
        {
            "brand_name": "Brand",
            "model_code": "G3",
            "region": None,
            "official_domains": ["brand.example"],
        },
        [{"url": f"https://brand.example/old-{index}.pdf", "title": "Old"} for index in range(10)],
        lambda _query: [{"url": "https://brand.example/new.pdf", "title": "Current"}],
    )
    assert len(results) <= 10
    assert any(row["url"] == "https://brand.example/new.pdf" for row in results)
