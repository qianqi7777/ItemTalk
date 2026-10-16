"""安全边界和协议校验，独立于模型账号与数据库的确定性测试。"""

import io
import socket
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image
from pydantic import ValidationError

from app import fetch
from app.adapters import ModelGateway
from app.config import Settings
from app.errors import DomainError
from app.fetch import encrypted_public_dns, public_addresses
from app.schemas import IdentifyRequest, MessageRequest
from app.security import digest, normalize
from app.services.chat import lexical_question
from app.services.knowledge import applicable, official_url
from app.storage import sanitize_image


@pytest.mark.parametrize(
    "schema,body",
    [
        (IdentifyRequest, {}),
        (IdentifyRequest, {"inputText": " \n"}),
        (MessageRequest, {}),
        (MessageRequest, {"content": " "}),
    ],
)
def test_empty_input_rejected(schema, body):
    with pytest.raises(ValidationError):
        schema.model_validate(body)


def test_normalization_and_hash():
    assert normalize(" ＡＢＣ ") == "abc"
    assert digest({"a": 1, "b": 2}) == digest({"b": 2, "a": 1})


def test_lexical_query_keeps_intent_and_nearby_models():
    model = {"model_code": "AB-100", "brand_name": "Acme"}
    assert lexical_question("ACME AB 100如何关闭？", model) == "如何关闭?"
    assert lexical_question("AB-1000如何关闭？", model) == "ab-1000如何关闭?"
    assert lexical_question("ＡＢ－１００", model) == "ab-100"
    assert lexical_question(" AB_100 配对", model) == "配对"


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.2", "169.254.169.254", "::1", "::ffff:8.8.8.8", "192.0.2.1"]
)
def test_ssrf_rejects_non_public_and_mapped(address):
    def resolver(*args, **kwargs):
        return [(socket.AF_INET, 1, 6, "", (address, 443))]

    with pytest.raises(DomainError):
        public_addresses("https://manual.example.com/a", resolver)


def test_ssrf_rejects_mixed_dns():
    def resolver(*args, **kwargs):
        return [(2, 1, 6, "", (ip, 443)) for ip in ("8.8.8.8", "127.0.0.1")]

    with pytest.raises(DomainError):
        public_addresses("https://manual.example.com/a", resolver)


def test_encrypted_dns_still_rejects_private_answers(monkeypatch):
    def response(*args, **kwargs):
        return httpx.Response(
            200,
            json={"Status": 0, "Answer": [{"type": 1, "data": "127.0.0.1"}]},
            request=httpx.Request("GET", "https://cloudflare-dns.com/dns-query"),
        )

    monkeypatch.setattr(httpx, "get", response)
    with pytest.raises(DomainError):
        public_addresses("https://manual.example.com/a", encrypted_public_dns)


@pytest.mark.parametrize("provider", ["system", "cloudflare"])
def test_public_address_check_uses_configured_resolver(monkeypatch, provider):
    def system(*args, **kwargs):
        assert provider == "system", "DoH模式不能回退到系统Fake-IP"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]

    def encrypted(*args, **kwargs):
        assert provider == "cloudflare"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("9.9.9.9", 443))]

    monkeypatch.setattr(fetch, "settings", lambda: SimpleNamespace(fetch_dns_provider=provider))
    monkeypatch.setattr(fetch.socket, "getaddrinfo", system)
    monkeypatch.setattr(fetch, "encrypted_public_dns", encrypted)
    _, addresses = public_addresses("https://manual.example.invalid/a", resolver=None)
    assert addresses == ["9.9.9.9" if provider == "cloudflare" else "8.8.8.8"]


def test_configured_encrypted_dns_rejects_mixed_private_answers(monkeypatch):
    monkeypatch.setattr(fetch, "settings", lambda: SimpleNamespace(fetch_dns_provider="cloudflare"))
    monkeypatch.setattr(
        fetch,
        "encrypted_public_dns",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))
            for address in ("8.8.8.8", "127.0.0.1")
        ],
    )
    with pytest.raises(DomainError):
        public_addresses("https://manual.example.invalid/a", resolver=None)


def test_model_region_hardware_exact_match():
    model = {"model_code": "AB-10", "region": "CN", "hardware_revision": "rev2"}
    assert applicable("AB-10 CN rev2 使用手册", model)
    assert not applicable("AB-100 CN rev2", model)
    assert not applicable("AB-10 US rev2", model)
    assert not applicable("AB-10 CN rev1", model)
    assert official_url("https://support.example.com/a", ["example.com"])
    assert not official_url("https://example.com.evil.org/a", ["example.com"])


def test_image_reencoding_and_magic():
    output = io.BytesIO()
    Image.new("RGB", (3, 3)).save(output, "PNG")
    data, mime = sanitize_image(output.getvalue(), "image/png")
    assert mime == "image/jpeg" and data.startswith(b"\xff\xd8")
    with pytest.raises(DomainError):
        sanitize_image(output.getvalue(), "image/jpeg")


def test_transparent_image_keeps_legible_text_background():
    output = io.BytesIO()
    image = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    image.putpixel((5, 5), (0, 0, 0, 255))
    image.save(output, "PNG")
    data, mime = sanitize_image(output.getvalue(), "image/png")
    with Image.open(io.BytesIO(data)) as clean:
        assert clean.getpixel((0, 0))[0] > 200
        assert clean.getpixel((5, 5))[0] < 100


def test_embedding_finite_dimensions(monkeypatch):
    gateway = ModelGateway(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, json={"data": [{"index": 0, "embedding": [1.0] * 12}]}
                )
            )
        )
    )
    gateway.config = Settings(model_provider="dashscope", embedding_api_key="test-key")
    monkeypatch.setattr(gateway, "budget", lambda subject: None)
    with pytest.raises(DomainError, match="向量结果无效"):
        gateway.embed(["test"], "test")


def test_model_retries_and_redacts_error(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429 if len(calls) < 3 else 401, text="sensitive-upstream-message")

    gateway = ModelGateway(httpx.Client(transport=httpx.MockTransport(handler)))
    gateway.config = Settings(
        model_provider="dashscope", model_api_key="test-key", upstream_max_attempts=3
    )
    monkeypatch.setattr(gateway, "budget", lambda subject: None)
    with pytest.raises(DomainError) as error:
        gateway.request("chat", "/chat/completions", {})
    assert len(calls) == 3 and "sensitive" not in str(error.value)


def test_text_key_is_never_reused_for_different_provider():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={})

    gateway = ModelGateway(httpx.Client(transport=httpx.MockTransport(handler)))
    gateway.config = Settings(
        model_provider="deepseek",
        model_api_key="test-key",
        vision_provider="dashscope",
        vision_api_key="",
    )
    with pytest.raises(DomainError, match="未配置"):
        gateway.request("vision", "/chat/completions", {})
    assert not calls


@pytest.mark.parametrize("environment", ["staging", "production"])
@pytest.mark.parametrize(
    "override",
    [
        {"jwt_secret": "short"},
        {"auth_cookie_secure": False},
        {"app_public_url": "http://example.com/itemtalk/"},
        {"model_provider": "mock"},
    ],
)
def test_hosted_environments_require_secure_real_services(environment, override):
    values = {
        "environment": environment,
        "jwt_secret": "a" * 32,
        "auth_cookie_secure": True,
        "app_public_url": "https://example.com/itemtalk/",
        "model_provider": "dashscope",
        "storage_backend": "cos",
        "cos_bucket": "test-bucket",
        "cos_secret_id": "test-id",
        "cos_secret_key": "test-key",
    }
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{**values, **override})


def test_only_staging_accepts_local_persistent_storage():
    values = {
        "jwt_secret": "a" * 32,
        "auth_cookie_secure": True,
        "app_public_url": "https://example.com/itemtalk/",
        "model_provider": "dashscope",
        "storage_backend": "local",
    }
    assert Settings(_env_file=None, environment="staging", **values).storage_backend == "local"
    with pytest.raises(ValidationError, match="Production requires COS"):
        Settings(_env_file=None, environment="production", **values)
