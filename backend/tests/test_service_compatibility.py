"""真实服务暴露的协议问题回归，所有外部调用使用受控 HTTP transport。"""

import json
from uuid import UUID

import httpx
import pytest

from app.adapters import ModelGateway, SearchGateway
from app.config import Settings, settings
from app.db import execute, transaction
from app.errors import DomainError
from app.services.knowledge import known_model_sources
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
