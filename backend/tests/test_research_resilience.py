"""A broken result must not hide another usable official manual."""

from uuid import UUID

import pytest

from app.adapters import ModelGateway, SearchGateway
from app.config import settings
from app.db import execute, one, transaction
from app.errors import DomainError
from app.services import knowledge
from app.worker import run_once
from tests.conftest import register_login
from tests.test_api import identified_product

pytestmark = pytest.mark.postgres


def test_ten_search_seeds_still_allow_a_support_page_to_reach_its_pdf(client, monkeypatch):
    from tests.test_parser import pdf_bytes

    register_login(client)
    product = identified_product(client)
    with transaction() as c:
        execute(c, "UPDATE brands SET official_domains='[\"brand.example\"]'::jsonb")
    monkeypatch.setenv("MODEL_PROVIDER", "dashscope")
    settings.cache_clear()
    monkeypatch.setattr(
        SearchGateway,
        "search",
        lambda *_: [
            {"url": f"https://brand.example/product/{i}", "title": "Product"} for i in range(10)
        ],
    )

    def fetch(url):
        if "/product/" in url:
            n = url.rsplit("/", 1)[-1]
            return url, "text/html", f'<a href="/support/{n}">Support</a>'.encode()
        if "/support/" in url:
            return url, "text/html", b'<a href="/manual.pdf">Manual</a>'
        return url, "application/pdf", pdf_bytes(None, "", text=b"DEMO-100 CN Instructions")

    monkeypatch.setattr(knowledge, "fetch_public", fetch)
    monkeypatch.setattr(
        ModelGateway, "embed", lambda _self, texts, _subject: [[1.0] + [0.0] * 1023 for _ in texts]
    )
    assert run_once(UUID(product["knowledgeJobId"]))
    assert client.get(f"/api/v1/me/products/{product['id']}").json()["knowledgeStatus"] == "ready"


@pytest.mark.parametrize("failure", ["download", "parse"])
def test_broken_first_official_candidate_does_not_prevent_later_publication(
    client, monkeypatch, failure
):
    register_login(client)
    product = identified_product(client)
    with transaction() as connection:
        execute(connection, "UPDATE brands SET official_domains='[\"brand.example\"]'::jsonb")
    monkeypatch.setenv("MODEL_PROVIDER", "dashscope")
    settings.cache_clear()
    monkeypatch.setattr(
        SearchGateway,
        "search",
        lambda *_: [
            {"url": "https://brand.example/broken.pdf", "title": "broken"},
            {"url": "https://brand.example/DEMO-100.pdf", "title": "official"},
        ],
    )

    def fetch(url):
        if "broken" in url:
            if failure == "download":
                raise DomainError(502, "UPSTREAM_SEARCH_ERROR", "Download error")
            return url, "application/pdf", b"invalid scan"
        return url, "text/plain", "DEMO-100 CN\n长按电源键 3 秒开机。".encode()

    monkeypatch.setattr(knowledge, "fetch_public", fetch)
    monkeypatch.setattr(
        ModelGateway, "embed", lambda _self, texts, _subject: [[1.0] + [0.0] * 1023 for _ in texts]
    )
    assert run_once(UUID(product["knowledgeJobId"]))
    assert client.get(f"/api/v1/me/products/{product['id']}").json()["knowledgeStatus"] == "ready"
    with transaction() as connection:
        assert one(connection, "SELECT count(*) AS n FROM knowledge_chunks")["n"] > 0
        assert (
            one(
                connection,
                "SELECT count(*) AS n FROM jobs WHERE job_type='parse' AND status='queued'",
            )["n"]
            == 0
        )


def test_download_page_expansion_does_not_recursively_crawl_returned_html(client, monkeypatch):
    register_login(client)
    product = identified_product(client)
    with transaction() as connection:
        execute(connection, "UPDATE brands SET official_domains='[\"brand.example\"]'::jsonb")
    monkeypatch.setenv("MODEL_PROVIDER", "dashscope")
    settings.cache_clear()
    monkeypatch.setattr(
        SearchGateway,
        "search",
        lambda *_: [{"url": "https://brand.example/downloads", "title": "Downloads"}],
    )

    def fetch(url):
        if url.endswith("/downloads"):
            return url, "text/html", b'<a href="/manual.pdf">Manual</a>'
        if url.endswith("/manual.pdf"):
            return url, "text/html", b'DEMO-100 CN <a href="/deep.pdf">Another link</a>'
        pytest.fail("A downloaded candidate returning HTML must not trigger another crawl level")

    monkeypatch.setattr(knowledge, "fetch_public", fetch)
    monkeypatch.setattr(
        ModelGateway, "embed", lambda _self, texts, _subject: [[1.0] + [0.0] * 1023 for _ in texts]
    )
    assert run_once(UUID(product["knowledgeJobId"]))
    assert client.get(f"/api/v1/me/products/{product['id']}").json()["knowledgeStatus"] == "ready"


def test_product_page_can_reach_official_support_page_and_manual(client, monkeypatch):
    from tests.test_parser import pdf_bytes

    register_login(client)
    product = identified_product(client)
    with transaction() as c:
        execute(c, "UPDATE brands SET official_domains='[\"brand.example\"]'::jsonb")
    monkeypatch.setenv("MODEL_PROVIDER", "dashscope")
    settings.cache_clear()
    monkeypatch.setattr(
        SearchGateway,
        "search",
        lambda *_: [{"url": "https://brand.example/product/G3", "title": "Product"}],
    )
    calls = []

    def fetch(url):
        calls.append(url)
        if url == "https://brand.example/product/G3":
            return (
                url,
                "text/html",
                b'<h1>Product</h1><a href="/support/G3">Support and downloads</a>',
            )
        if "/support/" in url:
            return url, "text/html", b'<a href="/files/manual.pdf">Manual</a>'
        assert url.endswith("manual.pdf")
        return url, "application/pdf", pdf_bytes(None, "", text=b"DEMO-100 CN Official manual")

    monkeypatch.setattr(knowledge, "fetch_public", fetch)
    monkeypatch.setattr(
        ModelGateway, "embed", lambda _s, texts, _u: [[1.0] + [0.0] * 1023 for _ in texts]
    )
    assert run_once(UUID(product["knowledgeJobId"]))
    assert calls[-1] == "https://brand.example/files/manual.pdf"
    assert client.get(f"/api/v1/me/products/{product['id']}").json()["knowledgeStatus"] == "ready"
