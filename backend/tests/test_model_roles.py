"""Role isolation and fixed embedding identity; synthetic HTTP only."""

import json

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app import model_configs
from app.adapters import ModelGateway
from app.config import Settings, settings
from app.errors import DomainError
from app.speech import ASRProvider


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("MODEL_CONFIG_ENCRYPTION_KEY", Fernet.generate_key().decode())
    settings.cache_clear()
    yield
    settings.cache_clear()


def role_draft(role, **changes):
    values = dict(
        name="备用服务",
        role=role,
        baseUrl="https://models.example.com/v1",
        protocol="openai_chat",
        model="vision-test",
        apiKey="role-only-key",
    )
    if role == "stt":
        values.update(protocol="openai_transcription", model="asr-test")
    if role == "embedding":
        values.update(
            protocol="dashscope_embeddings",
            model="text-embedding-v4",
            revision="initial",
            dimensions=1024,
        )
    return model_configs.ModelConfigInput(**{**values, **changes})


@pytest.mark.parametrize("role", ["vision", "stt", "embedding"])
def test_role_switch_keeps_other_roles_original_credentials(configured, role):
    base = Settings(
        model_provider="dashscope", model_api_key="original-key", stt_provider="dashscope"
    )
    result = model_configs.apply_configuration(base, role_draft(role))
    assert result.model_api_key.get_secret_value() == "original-key"
    for candidate in ("vision", "stt", "embedding"):
        assert getattr(result, f"{candidate}_api_key").get_secret_value() == (
            "role-only-key" if candidate == role else "original-key"
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"model": "different-model"},
        {"revision": "different"},
        {"dimensions": 1536},
        {"protocol": "openai_embeddings"},
    ],
)
def test_vector_identity_cannot_be_changed(configured, changes):
    with pytest.raises(ValidationError):
        role_draft("embedding", **changes)


def test_wrong_role_protocol_rejected(configured):
    with pytest.raises(ValidationError):
        role_draft("vision", protocol="openai_transcription")


def test_multiple_role_overlays_never_copy_managed_chat_key_into_unconfigured_stt(configured):
    base = Settings(
        model_provider="dashscope",
        model_api_key="original-server-key",
        stt_provider="openai_compatible",
        stt_api_key="",
    )
    chat = role_draft("chat", protocol="openai_chat")
    after_chat = model_configs.apply_configuration(base, chat)
    after_vision = model_configs.apply_configuration(after_chat, role_draft("vision"))
    assert after_chat.stt_api_key.get_secret_value() == ""
    assert after_vision.stt_api_key.get_secret_value() == ""
    assert after_vision.vision_api_key.get_secret_value() == "role-only-key"


def test_changed_embedding_server_identity_returns_controlled_unavailable(configured, monkeypatch):
    row = dict(
        name="saved",
        role="embedding",
        base_url="https://models.example.com/v1",
        protocol="dashscope_embeddings",
        model="text-embedding-v4",
        embedding_revision="old-revision",
        dimensions=1024,
        encrypted_key=model_configs.encrypt_key("synthetic-key"),
    )
    monkeypatch.setattr(model_configs, "get_config", lambda *args: row)
    with pytest.raises(DomainError) as failure:
        model_configs.resolve_configuration(None, settings(), "managed_" + "0" * 32, "embedding")
    assert failure.value.code == "MODEL_CONFIG_UNAVAILABLE"
    assert failure.value.status == 503
    assert "服务器预设" in failure.value.message


def test_openai_audio_transcription_sends_multipart_and_uses_own_key(configured, monkeypatch):
    from tests.test_speech import audio_wav

    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={"text": "测试录音"})

    configuration = model_configs.apply_configuration(Settings(), role_draft("stt"))
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        gateway = ModelGateway(client, configuration=configuration, managed_roles={"stt"})
        monkeypatch.setattr(gateway, "budget", lambda subject: None)
        assert ASRProvider(gateway).transcribe(audio_wav(), "audio/wav", "user") == "测试录音"
    assert str(seen[0].url) == "https://models.example.com/v1/audio/transcriptions"
    assert seen[0].headers["Authorization"] == "Bearer role-only-key"
    assert seen[0].headers["Content-Type"].startswith("multipart/form-data;")
    assert b"asr-test" in seen[0].content and b"RIFF" in seen[0].content


def test_vision_probe_requires_picture_and_structured_result(configured):
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert any(item["type"] == "image_url" for item in body["messages"][-1]["content"])
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"color":"red"}'}}]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = model_configs.probe(
            role_draft("vision"), "admin", client=client, charge=lambda: None
        )
    assert result["checks"] == ["image", "json"]
    assert len(requests) == 1


@pytest.mark.parametrize("bad", ["dimension", "zero", "nan"])
def test_vector_probe_rejects_incompatible_endpoint(configured, bad):
    def respond(request):
        vector = [1.0] + [0.0] * 1023
        if bad == "dimension":
            vector = [1.0]
        elif bad == "zero":
            vector = [0.0] * 1024
        else:
            return httpx.Response(200, text='{"data":[{"index":0,"embedding":[NaN]}]}')
        return httpx.Response(
            200, json={"data": [{"index": i, "embedding": vector} for i in range(2)]}
        )

    settings().embedding_base_url = "https://baseline.example.com/v1"
    settings().embedding_api_key = __import__("pydantic").SecretStr("baseline-test-key")
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(DomainError):
            model_configs.probe(
                role_draft("embedding"), "admin", client=client, charge=lambda: None
            )


def test_vector_probe_only_calls_new_service_so_exhausted_original_key_does_not_block(configured):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 0, "embedding": [1.0] + [0.0] * 1023},
                    {"index": 1, "embedding": [0.0, 1.0] + [0.0] * 1022},
                ]
            },
        )

    settings().embedding_base_url = "https://baseline.example.com/v1"
    settings().embedding_api_key = __import__("pydantic").SecretStr("baseline-test-key")
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = model_configs.probe(
            role_draft("embedding"), "admin", client=client, charge=lambda: None
        )
    assert result["checks"] == ["dimensions", "batch"]
    assert len(requests) == 1
    assert requests[0].headers["Authorization"] == "Bearer role-only-key"
    assert len(json.loads(requests[0].content)["input"]) == 2


@pytest.mark.parametrize("protocol", ["dashscope_audio", "openai_transcription"])
def test_speech_probe_checks_shipped_audio_against_transcription(configured, protocol):
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={"text": "这是一段语音测试。"}
            if protocol == "openai_transcription"
            else {"choices": [{"message": {"content": "这是一段语音测试。"}}]},
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = model_configs.probe(
            role_draft("stt", protocol=protocol), "admin", client=client, charge=lambda: None
        )
    assert result["checks"] == ["audio", "transcription"]
    assert len(seen) == 1
