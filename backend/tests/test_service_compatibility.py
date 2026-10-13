"""真实服务暴露的协议问题回归，所有外部调用使用受控 HTTP transport。"""

import json
from uuid import UUID

import httpx
import pytest

from app.adapters import ModelGateway, SearchGateway
from app.config import Settings, settings
from app.db import execute, transaction
from app.errors import DomainError
from app.services.knowledge import known_model_sources, register_official_source
from app.services.queue import claim
from app.upstream import response_error
from tests.conftest import register_login
from tests.test_api import post


def test_vision_disables_thinking_and_caps_uncertain_candidates(monkeypatch):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "candidates": [
                                        {
                                            "brandName": "Xiaomi",
                                            "modelCode": "AX3000T",
                                            "confidence": 0.99,
                                            "evidence": "产品页面文字",
                                        }
                                    ],
                                    "clarification": "没有硬件版本铭牌",
                                }
                            )
                        }
                    }
                ]
            },
        )

    gateway = ModelGateway(httpx.Client(transport=httpx.MockTransport(respond)))
    gateway.config = Settings(model_provider="dashscope", vision_api_key="test-key")
    monkeypatch.setattr(gateway, "budget", lambda subject: None)
    result = gateway.identify("识别产品", [(b"image", "image/png")], "user")
    assert requests[0]["model"] == "qwen3.7-flash" and requests[0]["enable_thinking"] is False
    assert result.candidates[0].confidence == 0.6


def test_search_rejects_business_errors_even_with_http_200(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "bocha")
    monkeypatch.setenv("SEARCH_API_KEY", "test-key")
    settings.cache_clear()
    monkeypatch.setattr(ModelGateway, "budget", lambda *args: None)
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"code": 403, "message": "private-message"})
        )
    )
    with pytest.raises(DomainError) as error:
        SearchGateway(client).search("query")
    assert error.value.code == "UPSTREAM_SEARCH_ERROR" and "private" not in str(error.value)
    settings.cache_clear()


def test_upstream_error_does_not_expose_credential_like_code():
    error = response_error(
        httpx.Response(401, json={"error": {"code": "sk-test-sensitive", "message": "private"}})
    )
    assert error.upstream_code is None and "sensitive" not in str(error)


@pytest.mark.postgres
def test_registered_source_requires_domain_and_model_path(client):
    register_login(client)
    with transaction() as connection:
        for url in (
            "https://www.mi.com/global/product/ax3000t/",
            "https://www.mi.com/other/?model=AX3000T",
            "https://evil.example/ax3000t/",
        ):
            execute(
                connection,
                "INSERT INTO sources(url,normalized_url,verification_status) "
                "VALUES (:url,:url,'pending')",
                url=url,
            )
        found = known_model_sources(
            connection,
            {"official_domains": ["mi.com"], "model_code": "AX3000T", "brand_name": "Xiaomi"},
        )
    assert [row["url"] for row in found] == ["https://www.mi.com/global/product/ax3000t/"]


@pytest.mark.postgres
def test_targeted_worker_claim_does_not_claim_existing_job(client):
    register_login(client)
    first = post(client, "/products/identify", {"inputText": "first"}).json()
    second = post(client, "/products/identify", {"inputText": "second"}).json()
    assert claim(UUID(second["jobId"]))["id"] == UUID(second["jobId"])
    assert claim(UUID(first["jobId"]))["id"] == UUID(first["jobId"])


@pytest.mark.postgres
def test_registered_model_url_accepts_omitted_separators_but_not_other_models(client):
    register_login(client)
    with transaction() as connection:
        for path in (
            "/mdr/wh1000xm5/guide.html",
            "/mdr/wh1000xm50/guide.html",
            "/?model=WH-1000XM5",
        ):
            url = "https://helpguide.sony.net" + path
            execute(
                connection,
                "INSERT INTO sources(url,normalized_url,verification_status) "
                "VALUES (:url,:url,'pending')",
                url=url,
            )
        found = known_model_sources(
            connection,
            {"official_domains": ["sony.net"], "model_code": "WH-1000XM5", "brand_name": "Sony"},
        )
    assert [row["url"] for row in found] == ["https://helpguide.sony.net/mdr/wh1000xm5/guide.html"]


@pytest.mark.postgres
def test_operator_source_registration_cannot_publish_or_restore_revoked_sources(
    client, monkeypatch
):
    register_login(client)
    monkeypatch.setattr("app.services.knowledge.public_addresses", lambda url: None)
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO brands(name,normalized_name,official_domains) "
            "VALUES ('Sony','sony','[\"sony.net\"]'::jsonb)",
        )
        with pytest.raises(DomainError):
            register_official_source(connection, "Sony", "https://evil.example/wh1000xm5/")
        url = "https://helpguide.sony.net/mdr/wh1000xm5/guide.html"
        source = register_official_source(connection, "Sony", url)
        assert source["verification_status"] == "pending"
        execute(
            connection,
            "UPDATE sources SET verification_status='revoked' WHERE id=:id",
            id=source["id"],
        )
        with pytest.raises(DomainError):
            register_official_source(connection, "Sony", url)
