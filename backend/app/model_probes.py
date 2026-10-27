"""Bounded role-specific checks using only shipped, non-user samples."""

import base64
import json
import struct
import time
import zlib
from pathlib import Path

import httpx

from app.adapters import ModelGateway
from app.config import settings
from app.deadlines import request_scope
from app.errors import DomainError
from app.model_configs import apply_configuration
from app.speech import ASRProvider


def red_picture():
    def chunk(kind, data):
        return (
            struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))
        )

    raw = (b"\x00" + b"\xff\x00\x00" * 64) * 64
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack("!IIBBBBB", 64, 64, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def probe_role(value, subject, *, client=None, charge=None):
    started = time.monotonic()
    base = apply_configuration(settings(), value)
    # Each probe makes one bounded call, including a retry allowance of zero.
    base = base.model_copy(update={"upstream_max_attempts": 1})
    gateway = ModelGateway(client, configuration=base, managed_roles={value.role})
    if charge is not None:
        gateway.budget = lambda _subject: charge()
    try:
        with request_scope(started + 45):
            if value.role == "vision":
                gateway.budget(subject)
                payload = {
                    "model": value.model,
                    "max_tokens": 64,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "Identify the main color of this image. "
                                        'Return JSON with key "color" and an English color name.'
                                    ),
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": "data:image/png;base64,"
                                        + base64.b64encode(red_picture()).decode()
                                    },
                                },
                            ],
                        }
                    ],
                }
                if value.protocol == "dashscope_chat":
                    payload["enable_thinking"] = False
                result = gateway.request("vision", "/chat/completions", payload)
                content = json.loads(result["choices"][0]["message"]["content"])
                if content.get("color", "").lower().strip() != "red":
                    raise ValueError("invalid image response")
                checks = ["image", "json"]
            elif value.role == "stt":
                audio = (
                    Path(__file__)
                    .with_name("probe_assets")
                    .joinpath("transcription.wav")
                    .read_bytes()
                )
                text = ASRProvider(gateway).transcribe(audio, "audio/wav", subject)
                if "测试" not in text or not any(term in text for term in ("语音", "录音")):
                    raise ValueError("sample transcription mismatch")
                checks = ["audio", "transcription"]
            else:
                vectors = gateway.embed(["产品开关在哪里", "如何检查电池电量"], subject)
                if any(not any(vector) for vector in vectors):
                    raise ValueError("zero vector")
                checks = ["dimensions", "batch"]
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise DomainError(
            502, "UPSTREAM_MODEL_ERROR", "模型能力测试失败，请检查协议、模型及返回内容"
        ) from None
    finally:
        gateway.close()
    return {"checks": checks, "durationMs": round((time.monotonic() - started) * 1000)}
