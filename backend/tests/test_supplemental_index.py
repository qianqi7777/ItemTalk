"""A matched third-party manual never becomes manufacturer authority."""

from uuid import UUID

import pytest

from app.adapters import ModelGateway
from app.config import settings
from app.db import execute, one, transaction
from app.services import knowledge, supplemental_index, supplemental_research
from app.services.queue import claim
from tests.conftest import register_login
from tests.test_api import identified_product

pytestmark = pytest.mark.postgres
URL = "https://manuals.plus/roseselsa/ceramics-ultra-manual"
TEXT = "ROSESELSA CERAMICS Ultra\nWireless pairing: open the case and select ROSE Ceramics U."


def build_supplement(
    client, monkeypatch, text=TEXT, *, data=None, mime="text/html", fetch=None, embed=None
):
    register_login(client)
    product = identified_product(client)
    with transaction() as c:
        execute(
            c,
            "UPDATE brands SET "
            "name='ROSESELSA',normalized_name='roseselsa',official_domains='[]'::jsonb",
        )
        execute(
            c,
            "UPDATE product_models SET model_code='CERAMICS "
            "Ultra',normalized_model_code='ceramics "
            "ultra',region=NULL,hardware_revision=NULL",
        )
        model = one(
            c,
            "SELECT m.*,b.name AS brand_name,b.official_domains FROM product_models m "
            "JOIN brands b ON b.id=m.brand_id WHERE m.id=:m",
            m=UUID(product["productModelId"]),
        )
    monkeypatch.setenv("MODEL_PROVIDER", "dashscope")
    settings.cache_clear()
    monkeypatch.setattr(knowledge, "discover_candidates", lambda *_: [])
    monkeypatch.setattr(knowledge, "manufacturer_entries", lambda *_: [])
    monkeypatch.setattr(
        supplemental_research,
        "discover",
        lambda *_: [{"url": URL, "title": "ROSESELSA CERAMICS Ultra User Manual"}],
    )
    monkeypatch.setattr(
        supplemental_research,
        "fetch_public",
        fetch
        or (
            lambda url: (
                url,
                mime,
                data if data is not None else ("<article><h1>" + text + "</h1></article>").encode(),
            )
        ),
    )
    monkeypatch.setattr(
        ModelGateway,
        "embed",
        embed or (lambda _self, texts, _subject: [[1.0] + [0.0] * 1023 for _ in texts]),
    )
    job = claim(UUID(product["knowledgeJobId"]))
    knowledge.research(job)
    return product, model, job


def test_official_failure_builds_separate_searchable_supplement(client, monkeypatch):
    product, model, _ = build_supplement(client, monkeypatch)
    with transaction() as c:
        assert one(c, "SELECT count(*) AS n FROM knowledge_chunks")["n"] == 0
        assert (
            one(c, "SELECT identity_status FROM product_models WHERE id=:m", m=model["id"])[
                "identity_status"
            ]
            == "user_confirmed"
        )
        assert one(c, "SELECT count(*) AS n FROM supplemental_chunks")["n"] > 0
    rows = supplemental_index.retrieve(model["id"], "wireless pairing", [1.0] + [0.0] * 1023)
    assert rows and rows[0]["url"] == URL
    value = client.get("/api/v1/me/products").json()["items"][0]
    assert value["knowledgeStatus"] == "supplemental_ready"
    assert value["supplementalDocumentCount"] == 1
    docs = client.get(
        f"/api/v1/products/{model['id']}/supplemental-documents?instanceId={product['id']}"
    )
    assert docs.status_code == 200
    assert docs.json()["items"][0]["sourceKind"] == "third_party"
    # Formal manuals still require manufacturer evidence.
    assert (
        client.get(f"/api/v1/products/{model['id']}/manual?instanceId={product['id']}").status_code
        == 404
    )


@pytest.mark.parametrize(
    "text",
    [
        TEXT.replace("ROSESELSA", "OtherBrand"),
        TEXT.replace("Ultra", "Ultra Pro"),
        TEXT.replace("Ultra", "Ultra 2"),
    ],
)
def test_wrong_identity_cannot_publish_supplement(client, monkeypatch, text):
    _, model, _ = build_supplement(client, monkeypatch, text)
    assert not supplemental_index.retrieve(model["id"], "pairing", [1.0] + [0.0] * 1023)
    with transaction() as c:
        assert one(c, "SELECT count(*) AS n FROM supplemental_chunks")["n"] == 0


def test_source_withdrawal_blocks_index_and_generated_reference(client, monkeypatch):
    _, model, _ = build_supplement(client, monkeypatch)
    rows = supplemental_index.retrieve(model["id"], "pairing", [1.0] + [0.0] * 1023)
    with transaction() as c:
        execute(c, "UPDATE sources SET verification_status='revoked' WHERE url=:url", url=URL)
        assert not supplemental_index.live(c, model["id"], rows)
    assert not supplemental_index.retrieve(model["id"], "pairing", [1.0] + [0.0] * 1023)


def test_supplement_api_requires_own_matching_instance(client, monkeypatch):
    product, model, _ = build_supplement(client, monkeypatch)
    client.post("/api/v1/auth/logout")
    register_login(client, "other-supplement@example.com")
    response = client.get(
        f"/api/v1/products/{model['id']}/supplemental-documents?instanceId={product['id']}"
    )
    assert response.status_code == 404


def test_navigation_cannot_supply_the_model_identity(client, monkeypatch):
    body = (
        "<main><h1>Other Device Manual</h1>Unrelated instructions.</main>"
        "<aside>" + TEXT + "</aside>"
    ).encode()
    _, model, _ = build_supplement(client, monkeypatch, data=body)
    assert not supplemental_index.retrieve(model["id"], "pairing", [1.0] + [0.0] * 1023)


def test_scanned_supplement_uses_ocr_and_does_not_promote_identity(client, monkeypatch):
    from app.services import document_extraction
    from tests.test_scanned_manuals import scanned_pdf

    calls = []
    monkeypatch.setattr(
        document_extraction, "transcribe_page", lambda *_args, **_kwargs: calls.append(True) or TEXT
    )
    _, model, _ = build_supplement(client, monkeypatch, data=scanned_pdf(), mime="application/pdf")
    assert calls == [True]
    rows = supplemental_index.retrieve(model["id"], "pairing", [1.0] + [0.0] * 1023)
    assert rows and rows[0]["page_start"] == 1


def test_index_answer_persists_third_party_provenance_and_withdrawal(client, monkeypatch):
    from app.grounding import AnswerSupport
    from app.services import web_answer
    from tests.test_api import post

    product, _, _ = build_supplement(client, monkeypatch)
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    settings.cache_clear()
    monkeypatch.setattr(
        web_answer, "research", lambda *_a, **_kw: pytest.fail("Indexed answer must not re-search")
    )
    monkeypatch.setattr(
        ModelGateway, "answer", lambda *_a, **_kw: "Open the case and select ROSE Ceramics U.[1]"
    )
    monkeypatch.setattr(
        ModelGateway,
        "assess_answer",
        lambda *_a, **_kw: AnswerSupport(
            fullySupported=True,
            citations=[
                dict(rank=1, supported=True, quote="open the case and select ROSE Ceramics U.")
            ],
        ),
    )
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()
    path = f"/conversations/{conversation['id']}/messages"
    response = post(client, path, {"content": "How do I pair via wireless?"})
    assert response.status_code == 201
    value = response.json()
    assert value["answerBasis"] == "web"
    assert value["webLookup"]["lookupMode"] == "supplemental_index"
    assert value["webSources"][0]["sourceKind"] == "third_party"
    assert "_supplemental" not in value["webSources"][0]
    with transaction() as c:
        execute(c, "UPDATE sources SET verification_status='revoked' WHERE url=:url", url=URL)
    saved = client.get("/api/v1" + path).json()["items"][-1]
    assert saved["webSources"][0]["availabilityStatus"] == "withdrawn"


def test_withdrawal_during_answer_assessment_prevents_commit(client, monkeypatch):
    from app.grounding import AnswerSupport
    from app.services import web_answer
    from tests.test_api import post

    product, _, _ = build_supplement(client, monkeypatch)
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    settings.cache_clear()
    monkeypatch.setattr(web_answer, "research", lambda *_a, **_kw: pytest.fail("No new search"))
    monkeypatch.setattr(ModelGateway, "answer", lambda *_a, **_kw: "Select ROSE Ceramics U.[1]")

    def withdraw(*_args, **_kwargs):
        with transaction() as c:
            execute(c, "UPDATE sources SET verification_status='revoked' WHERE url=:url", url=URL)
        return AnswerSupport(
            fullySupported=True,
            citations=[dict(rank=1, supported=True, quote="select ROSE Ceramics U.")],
        )

    monkeypatch.setattr(ModelGateway, "assess_answer", withdraw)
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()
    path = f"/conversations/{conversation['id']}/messages"
    response = post(client, path, {"content": "How do I pair via wireless?"})
    assert response.status_code == 409
    assert not any(
        row["role"] == "assistant" for row in client.get("/api/v1" + path).json()["items"]
    )


def test_supplement_does_not_enable_sensitive_device_steps(client, monkeypatch):
    from app.services import web_answer
    from tests.test_api import post

    product, _, _ = build_supplement(client, monkeypatch)
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    settings.cache_clear()
    monkeypatch.setattr(
        supplemental_index, "lookup", lambda *_: pytest.fail("Official evidence required")
    )
    monkeypatch.setattr(
        web_answer,
        "research",
        lambda *_a, **_kw: dict(
            status="empty",
            reason="official_required",
            queryKey="test",
            sources=[],
            searchedAt="2026-11-05T00:00:00+00:00",
            cached=False,
        ),
    )
    conversation = post(client, "/conversations", {"userProductInstanceId": product["id"]}).json()
    saved = post(
        client, f"/conversations/{conversation['id']}/messages", {"content": "如何刷固件？"}
    ).json()
    assert saved["answerBasis"] == "insufficient" and not saved["webSources"]
