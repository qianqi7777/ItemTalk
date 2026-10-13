"""语音适配协议、受控失败和转写确认边界回归；不调用真实付费服务。"""

import io
import json
import wave
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.adapters import ModelGateway
from app.config import Settings
from app.db import one, transaction
from app.errors import DomainError
from app.main import app
from app.speech import ASRProvider
from tests.conftest import HEADERS, register_login
from tests.test_api import post


def audio_wav():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 1600)
    return output.getvalue()


def test_asr_uses_independent_settings_and_returns_text(monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "中文问题"}}]})

    gateway = ModelGateway(httpx.Client(transport=httpx.MockTransport(respond)))
    gateway.config = Settings(
        stt_provider="dashscope",
        stt_api_key="test-key",
        stt_base_url="https://speech.example.com/v1",
    )
    monkeypatch.setattr(gateway, "budget", lambda subject: None)
    assert ASRProvider(gateway).transcribe(audio_wav(), "audio/wav", "user") == "中文问题"
    payload = json.loads(requests[0].content)
    assert str(requests[0].url) == "https://speech.example.com/v1/chat/completions"
    assert payload["model"] == "qwen3-asr-flash"
    assert payload["messages"][0]["content"][0]["input_audio"]["data"].startswith(
        "data:audio/wav;base64,"
    )
    assert "enable_thinking" not in payload


@pytest.mark.parametrize(
    "data,mime",
    [(b"data", "image/png"), (b"bad", "audio/wav"), (b"0" * (7 * 1024 * 1024 + 1), "audio/mpeg")],
    ids=["bad-mime", "invalid-wav", "oversize"],
)
def test_asr_invalid_audio_does_not_call_provider(monkeypatch, data, mime):
    gateway = ModelGateway()
    gateway.config = Settings(stt_provider="dashscope")
    monkeypatch.setattr(gateway, "request", lambda *args: pytest.fail("Unexpected paid request"))
    with pytest.raises(DomainError):
        ASRProvider(gateway).transcribe(data, mime, "user")


@pytest.mark.postgres
def test_transcription_is_owned_idempotent_and_does_not_send_message(client, monkeypatch):
    register_login(client)
    raw = audio_wav()
    signed = post(
        client,
        "/assets/presign",
        {"filename": "voice.wav", "mimeType": "audio/wav", "sizeBytes": len(raw), "kind": "voice"},
    ).json()
    assert client.put(signed["uploadUrl"], content=raw).status_code == 204
    assert post(client, f"/assets/{signed['assetId']}/confirm", {}).status_code == 200
    calls = []

    def recognize(self, data, mime, subject):
        calls.append(subject)
        return "需要用户确认的问题"

    monkeypatch.setattr(ASRProvider, "transcribe", recognize)
    key = str(uuid4())
    route = f"/assets/{signed['assetId']}/transcribe"
    first = post(client, route, {}, key)
    second = post(client, route, {}, key)
    assert first.status_code == 200 and first.json() == second.json()
    assert first.json()["requiresConfirmation"] is True and len(calls) == 1
    with transaction() as connection:
        assert one(connection, "SELECT count(*) AS n FROM messages")["n"] == 0
    with TestClient(app, base_url="http://localhost:5173", headers=HEADERS) as other:
        register_login(other, "voice-other@example.com")
        assert post(other, route, {}).status_code == 404
