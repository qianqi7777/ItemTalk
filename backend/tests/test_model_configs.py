"""Model configuration boundaries; no real supplier calls or application database."""

import importlib
import json
import time

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.config import settings
from app.errors import DomainError


@pytest.fixture
def configs(monkeypatch):
    monkeypatch.setenv("MODEL_CONFIG_ENCRYPTION_KEY", Fernet.generate_key().decode())
    settings.cache_clear()
    yield importlib.import_module("app.model_configs")
    settings.cache_clear()


def draft(configs, **changes):
    return configs.ModelConfigInput(
        name="备用服务",
        baseUrl="https://models.example.com/v1",
        protocol="openai_chat",
        model="vendor/model-v2",
        apiKey="test-only-supplier-key",
        **changes,
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://models.example.com/v1",
        "https://user:key@models.example.com/v1",
        "https://models.example.com:8443/v1",
        "https://models.example.com/v1?key=secret",
        "https://models.example.com/v1#part",
        "https://models.example.com/v1/chat/completions",
        "https://127.0.0.1/v1",
        "https://[::ffff:127.0.0.1]/v1",
    ],
)
def test_rejects_unsafe_or_ambiguous_base_urls(configs, url):
    with pytest.raises((ValidationError, DomainError)):
        configs.ModelConfigInput(
            name="备用", baseUrl=url, protocol="openai_chat", model="model", apiKey="test-only-key"
        )


def test_secret_round_trip_does_not_store_plaintext_and_requires_master_key(configs, monkeypatch):
    value = draft(configs)
    encrypted = configs.encrypt_key(value.apiKey.get_secret_value())
    assert "test-only-supplier-key" not in encrypted
    assert configs.decrypt_key(encrypted) == "test-only-supplier-key"
    monkeypatch.setenv("MODEL_CONFIG_ENCRYPTION_KEY", "")
    settings.cache_clear()
    with pytest.raises(DomainError) as error:
        configs.decrypt_key(encrypted)
    assert error.value.status == 503


def test_test_proof_binds_every_field_secret_and_actor(configs):
    value = draft(configs)
    actor = "09f50cc7-680a-4616-bd20-2b150586184d"
    proof = configs.test_proof(value, actor)
    configs.verify_proof(value, actor, proof)
    for field, change in [
        ("model", "other-model"),
        ("baseUrl", "https://other.example.com/v1"),
        ("apiKey", "different-test-key"),
        ("name", "不同名称"),
        ("protocol", "dashscope_chat"),
    ]:
        edited = configs.ModelConfigInput(**{**value.model_dump(), field: change})
        with pytest.raises(DomainError):
            configs.verify_proof(edited, actor, proof)
    with pytest.raises(DomainError):
        configs.verify_proof(value, "00000000-0000-0000-0000-000000000001", proof)
    assert "test-only-supplier-key" not in proof


def test_expired_test_proof_requires_a_new_test(configs, monkeypatch):
    actor = "09f50cc7-680a-4616-bd20-2b150586184d"
    proof = configs.test_proof(draft(configs), actor)
    monkeypatch.setattr(
        "jwt.api_jwt.datetime",
        type(
            "Clock",
            (),
            {
                "now": staticmethod(
                    lambda tz: __import__("datetime").datetime.fromtimestamp(time.time() + 901, tz)
                )
            },
        ),
    )
    with pytest.raises(DomainError):
        configs.verify_proof(draft(configs), actor, proof)


def test_text_switch_preserves_original_vision_and_embedding_shared_keys(configs, monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "dashscope")
    monkeypatch.setenv("MODEL_API_KEY", "original-test-key")
    monkeypatch.setenv("VISION_API_KEY", "")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    monkeypatch.setenv("STT_PROVIDER", "dashscope")
    monkeypatch.setenv("STT_API_KEY", "")
    settings.cache_clear()
    result = configs.apply_configuration(settings(), draft(configs))
    assert result.model_provider == "openai_compatible"
    assert result.model_base_url == "https://models.example.com/v1"
    assert result.chat_model == "vendor/model-v2"
    assert result.model_api_key.get_secret_value() == "test-only-supplier-key"
    assert result.vision_api_key.get_secret_value() == "original-test-key"
    assert result.embedding_api_key.get_secret_value() == "original-test-key"
    assert result.stt_api_key.get_secret_value() == "original-test-key"


@pytest.mark.parametrize("role", ["vision", "embedding", "stt"])
def test_switch_never_introduces_new_key_sharing_for_unconfigured_other_roles(
    configs, monkeypatch, role
):
    from app.adapters import ModelGateway

    monkeypatch.setenv("MODEL_PROVIDER", "deepseek")
    monkeypatch.setenv("MODEL_API_KEY", "original-deepseek-test-key")
    monkeypatch.setenv("STT_PROVIDER", "dashscope")
    monkeypatch.setenv("STT_API_KEY", "")
    monkeypatch.setenv("VISION_PROVIDER", "dashscope")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "dashscope")
    monkeypatch.setenv("VISION_API_KEY", "")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    settings.cache_clear()
    value = configs.ModelConfigInput(
        **{**draft(configs).model_dump(), "protocol": "dashscope_chat"}
    )
    requests = []
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: requests.append(request) or httpx.Response(200, json={})
        )
    )
    gateway = ModelGateway(
        client, configuration=configs.apply_configuration(settings(), value), managed_chat=True
    )
    with pytest.raises(DomainError) as error:
        gateway.request(role, "/embeddings" if role == "embedding" else "/chat/completions", {})
    assert error.value.status == 503
    assert requests == []


def test_probe_checks_plain_json_and_stream_without_private_input(configs, monkeypatch):
    payloads = []

    def reply(request):
        body = json.loads(request.content)
        payloads.append(body)
        assert request.headers["Authorization"] == "Bearer test-only-supplier-key"
        assert str(request.url) == "https://models.example.com/v1/chat/completions"
        if body.get("stream"):
            return httpx.Response(
                200, text='data: {"choices":[{"delta":{"content":"OK"}}]}\n\ndata: [DONE]\n\n'
            )
        content = '{"ok":true}' if body.get("response_format") else "OK"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    result = configs.probe(
        draft(configs),
        "admin-test",
        client=httpx.Client(transport=httpx.MockTransport(reply)),
        charge=lambda: None,
    )
    assert result["checks"] == ["text", "json", "stream"]
    assert len(payloads) == 3
    assert all(body["model"] == "vendor/model-v2" for body in payloads)


@pytest.mark.parametrize("kind", ["redirect", "empty", "invalid_json", "broken_stream", "quota"])
def test_probe_rejects_unusable_model_without_exposing_supplier_body(configs, kind):
    def reply(request):
        body = json.loads(request.content)
        if kind == "quota":
            return httpx.Response(429, json={"error": {"message": "SECRET-SUPPLIER-BODY"}})
        if kind == "redirect":
            return httpx.Response(302, headers={"Location": "https://127.0.0.1/"})
        if body.get("stream"):
            return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"OK"}}]}\n\n')
        content = (
            ""
            if kind == "empty"
            else "not-json"
            if kind == "invalid_json"
            else ('{"ok":true}' if body.get("response_format") else "OK")
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    with pytest.raises(DomainError) as error:
        configs.probe(
            draft(configs),
            "admin-test",
            client=httpx.Client(transport=httpx.MockTransport(reply)),
            charge=lambda: None,
        )
    assert "SECRET-SUPPLIER-BODY" not in str(error.value)


def test_managed_transport_rechecks_dns_and_blocks_private_before_sending_credentials(monkeypatch):
    transport = importlib.import_module("app.model_transport")
    from app import fetch

    monkeypatch.setattr(
        fetch.socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("127.0.0.1", 443))]
    )
    with httpx.Client(transport=transport.PublicModelTransport()) as client:
        with pytest.raises(DomainError):
            client.post(
                "https://models.example.com/v1/chat/completions",
                headers={"Authorization": "Bearer test-only-key"},
                json={},
            )


def test_managed_transport_pins_public_ip_and_never_follows_redirects(monkeypatch):
    transport = importlib.import_module("app.model_transport")
    observed = []
    monkeypatch.setattr(
        transport,
        "public_addresses",
        lambda url: (__import__("urllib.parse", fromlist=["urlsplit"]).urlsplit(url), ["8.8.8.8"]),
    )

    class Connection:
        def __init__(self, hostname, address):
            observed.append((hostname, address))

        def request(self, method, path, body=None, headers=None):
            observed.append((method, path, httpx.Headers(headers)["Authorization"]))

        def getresponse(self):
            return type(
                "Response",
                (),
                {"status": 302, "getheaders": lambda self: [("Location", "https://127.0.0.1/")]},
            )()

        def close(self):
            observed.append("closed")

    monkeypatch.setattr(transport, "PinnedHTTPS", Connection)
    with httpx.Client(transport=transport.PublicModelTransport()) as client:
        with pytest.raises(DomainError):
            client.post(
                "https://models.example.com/v1/chat/completions",
                headers={"Authorization": "Bearer test-only-key"},
                json={},
            )
    assert observed == [
        ("models.example.com", "8.8.8.8"),
        ("POST", "/v1/chat/completions", "Bearer test-only-key"),
        "closed",
    ]


@pytest.mark.parametrize(
    "original,protocol,thinking",
    [
        ("dashscope", "openai_chat", False),
        ("deepseek", "dashscope_chat", True),
        ("mock", "openai_chat", False),
    ],
)
@pytest.mark.parametrize("guidance", [False, True])
def test_agents_use_resolved_protocol_including_mock_decision(
    configs, monkeypatch, original, protocol, thinking, guidance
):
    from langchain_core.messages import HumanMessage

    from app import agent_probe, guidance_agent, operations
    from app.adapters import ModelGateway

    monkeypatch.setenv("MODEL_PROVIDER", original)
    settings.cache_clear()
    value = configs.ModelConfigInput(**{**draft(configs).model_dump(), "protocol": protocol})
    observed = []

    def respond(request):
        observed.append(json.loads(request.content))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "live configured response"}}]}
        )

    gateway = ModelGateway(
        httpx.Client(transport=httpx.MockTransport(respond)),
        configuration=configs.apply_configuration(settings(), value),
        managed_chat=True,
    )
    monkeypatch.setattr(gateway, "budget", lambda *_: None)
    monkeypatch.setattr(operations, "metric", lambda *a, **kw: None)
    monkeypatch.setattr(agent_probe, "ModelGateway", lambda: gateway)
    monkeypatch.setattr(guidance_agent, "ModelGateway", lambda: gateway)
    model = (
        guidance_agent.GuidanceChatModel(require_evidence=False, json_output=False)
        if guidance
        else agent_probe.GatewayChatModel()
    )
    result = model.invoke([HumanMessage(content='{"topic":"开机","mode":"create"}')])
    assert result.content == "live configured response"
    assert observed[0]["model"] == "vendor/model-v2"
    if thinking:
        assert observed[0]["enable_thinking"] is False
    else:
        assert "enable_thinking" not in observed[0]
