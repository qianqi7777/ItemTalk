"""Immutable, tested text-model configurations; secret material never leaves the server."""

import hashlib
import hmac
import ipaddress
import json
import time
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from cryptography.fernet import Fernet, InvalidToken
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

from app.config import settings
from app.db import one
from app.deadlines import request_scope
from app.errors import DomainError
from app.model_transport import PublicModelTransport
from app.security import decode_token, token_for
from app.upstream import response_error


class ModelConfigInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    baseUrl: str = Field(min_length=8, max_length=500)
    role: Literal["chat", "vision", "stt", "embedding"] = "chat"
    protocol: Literal[
        "openai_chat",
        "dashscope_chat",
        "openai_responses",
        "dashscope_responses",
        "dashscope_image_search",
        "dashscope_audio",
        "openai_transcription",
        "openai_embeddings",
        "dashscope_embeddings",
    ]
    model: str = Field(pattern=r"^[a-zA-Z0-9_./:-]{1,100}$")
    apiKey: SecretStr = Field(min_length=1, max_length=4096)
    revision: str | None = Field(default=None, max_length=40)
    dimensions: int | None = None

    @model_validator(mode="after")
    def role_protocol(self):
        allowed = {
            "chat": {"openai_chat", "dashscope_chat", "openai_responses", "dashscope_responses"},
            "vision": {
                "openai_chat",
                "dashscope_chat",
                "openai_responses",
                "dashscope_responses",
                "dashscope_image_search",
            },
            "stt": {"dashscope_audio", "openai_transcription"},
            "embedding": {embedding_protocol(settings())},
        }
        if self.protocol not in allowed[self.role]:
            raise ValueError("此用途不支持所选协议；向量协议保持服务器预设")
        if self.role == "embedding":
            base = settings()
            if (self.model, self.revision, self.dimensions) != (
                base.embedding_model,
                base.embedding_revision,
                base.embedding_dimensions,
            ):
                raise ValueError("向量模型、修订和维度不可更改，只允许更换地址和密钥")
        elif self.revision is not None or self.dimensions is not None:
            raise ValueError("只有向量配置包含修订和维度")
        return self

    @field_validator("baseUrl")
    @classmethod
    def safe_base_url(cls, value):
        try:
            parts = urlsplit(value)
            if (
                parts.scheme != "https"
                or not parts.hostname
                or parts.username is not None
                or parts.password is not None
                or parts.port not in (None, 443)
                or parts.query
                or parts.fragment
                or "?" in value
                or "#" in value
                or any(c.isspace() or ord(c) < 32 for c in value)
                or "\\" in value
                or "%" in parts.netloc
                or parts.path.rstrip("/").endswith(
                    ("/chat/completions", "/responses", "/embeddings", "/audio/transcriptions")
                )
            ):
                raise ValueError("invalid base URL")
            try:
                address = ipaddress.ip_address(parts.hostname)
            except ValueError:
                address = None
            if address is not None and (
                not address.is_global or getattr(address, "ipv4_mapped", None)
            ):
                raise ValueError("nonpublic address")
            return value.rstrip("/")
        except ValueError:
            raise ValueError("请填写公网HTTPS API基础地址，不含密钥、查询参数或接口路径") from None

    @field_validator("apiKey")
    @classmethod
    def valid_key(cls, value):
        if any(c.isspace() or ord(c) < 32 for c in value.get_secret_value()):
            raise ValueError("API Key不能包含空白字符")
        return value


class ModelConfigSave(ModelConfigInput):
    testToken: str = Field(min_length=1, max_length=2048)


def embedding_protocol(base):
    return "dashscope_embeddings" if base.embedding_provider == "dashscope" else "openai_embeddings"


def presets():
    base = settings()
    result = {}
    for role in ("chat", "vision", "stt", "embedding"):
        provider = base.model_provider if role == "chat" else getattr(base, f"{role}_provider")
        protocol = "dashscope_chat" if provider == "dashscope" else "openai_chat"
        if role in {"chat", "vision"} and getattr(base, f"{role}_api_protocol") == "responses":
            protocol = "dashscope_responses" if provider == "dashscope" else "openai_responses"
            if role == "vision" and base.vision_image_search_enabled and provider == "dashscope":
                protocol = "dashscope_image_search"
        if role == "stt":
            protocol = "dashscope_audio" if provider == "dashscope" else "openai_transcription"
        if role == "embedding":
            protocol = embedding_protocol(base)
        result[role] = {
            "model": getattr(base, f"{role}_model"),
            "baseUrl": base.model_base_url if role == "chat" else getattr(base, f"{role}_base_url"),
            "protocol": protocol,
            "revision": base.embedding_revision if role == "embedding" else None,
            "dimensions": base.embedding_dimensions if role == "embedding" else None,
        }
    return result


def cipher():
    try:
        return Fernet(settings().model_config_encryption_key.get_secret_value().encode())
    except (ValueError, TypeError):
        raise DomainError(
            503, "MODEL_CONFIG_UNAVAILABLE", "服务器尚未配置模型密钥加密，请联系运维"
        ) from None


def encrypt_key(value):
    return cipher().encrypt(value.encode()).decode()


def decrypt_key(value):
    try:
        return cipher().decrypt(value.encode()).decode()
    except (InvalidToken, UnicodeError, ValueError):
        raise DomainError(
            503, "MODEL_CONFIG_UNAVAILABLE", "模型配置密钥无法解密，请恢复正确的加密配置"
        ) from None


def fingerprint(value):
    cipher()  # Fail closed before issuing test proofs if encryption is unavailable.
    data = {field: getattr(value, field) for field in ModelConfigInput.model_fields}
    data["apiKey"] = value.apiKey.get_secret_value()
    return hmac.new(
        settings().model_config_encryption_key.get_secret_value().encode(),
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode(),
        hashlib.sha256,
    ).hexdigest()


def test_proof(value, actor):
    return token_for(actor, "model_config_test", fingerprint=fingerprint(value))


def verify_proof(value, actor, proof):
    try:
        claims = decode_token(proof, "model_config_test")
        if claims["sub"] != str(actor) or not hmac.compare_digest(
            claims.get("fingerprint", ""), fingerprint(value)
        ):
            raise ValueError("different draft")
    except (DomainError, ValueError, TypeError):
        raise DomainError(422, "VALIDATION_ERROR", "配置已变更或测试已过期，请重新测试") from None


def apply_configuration(base, value):
    overrides = {}
    # Freeze original effective keys before changing any role, so an unrelated
    # role never inherits a newly supplied credential.
    if not base._effective_model_keys_frozen:
        for role in ("vision", "embedding", "stt"):
            if (
                not getattr(base, f"{role}_api_key").get_secret_value()
                and getattr(base, f"{role}_provider") == base.model_provider
            ):
                overrides[f"{role}_api_key"] = base.model_api_key
    prefix = "model" if value.role == "chat" else value.role
    overrides.update(
        {
            f"{prefix}_provider": "dashscope"
            if value.protocol.startswith("dashscope_")
            else "openai_compatible",
            f"{prefix}_base_url": value.baseUrl,
            f"{prefix}_api_key": value.apiKey,
            f"{value.role}_model": value.model,
        }
    )
    if value.role in {"chat", "vision"}:
        overrides[f"{value.role}_api_protocol"] = (
            "responses"
            if value.protocol
            in {"openai_responses", "dashscope_responses", "dashscope_image_search"}
            else "chat_completions"
        )
    if value.role == "vision":
        overrides["vision_image_search_enabled"] = value.protocol == "dashscope_image_search"
    result = base.model_copy(update=overrides)
    result._effective_model_keys_frozen = True
    return result


def get_config(connection, profile):
    row = one(connection, "SELECT itemtalk_model_config(:p) AS value", p=profile)
    if not row or row["value"] is None:
        raise DomainError(503, "MODEL_CONFIG_UNAVAILABLE", "当前模型配置不存在，请切回服务器预设")
    return row["value"]


def resolve_configuration(connection, base, profile, role="chat"):
    row = get_config(connection, profile)
    if row.get("role", "chat") != role:
        raise DomainError(422, "VALIDATION_ERROR", "模型配置用途不匹配")
    try:
        value = ModelConfigInput(
            name=row["name"],
            baseUrl=row["base_url"],
            protocol=row["protocol"],
            model=row["model"],
            apiKey=decrypt_key(row["encrypted_key"]),
            role=role,
            revision=row.get("embedding_revision"),
            dimensions=row.get("dimensions"),
        )
    except ValidationError:
        raise DomainError(
            503,
            "MODEL_CONFIG_UNAVAILABLE",
            "已保存的模型配置与服务器预设不兼容，请切回服务器预设后重新配置",
        ) from None
    return apply_configuration(base, value)


def list_configs(connection):
    return one(connection, "SELECT itemtalk_role_model_config_list() AS value")["value"]


def encryption_ready():
    try:
        cipher()
        return True
    except DomainError:
        return False


def save_config(connection, value):
    return one(
        connection,
        "SELECT itemtalk_add_role_model_config(:p,:n,:b,:w,:m,:k,:r,:e,:d) AS value",
        p="managed_" + uuid4().hex,
        n=value.name,
        b=value.baseUrl,
        w=value.protocol,
        m=value.model,
        k=encrypt_key(value.apiKey.get_secret_value()),
        r=value.role,
        e=value.revision,
        d=value.dimensions,
    )["value"]


def probe(value, subject, *, client=None, charge=None):
    """Bounded synthetic text/JSON/SSE checks, plus a Responses function roundtrip."""
    from app.adapters import ModelGateway

    cipher()
    if value.role != "chat":
        from app.model_probes import probe_role

        return probe_role(value, subject, client=client, charge=charge)
    started = time.monotonic()
    gateway = ModelGateway(configuration=apply_configuration(settings(), value), managed_chat=True)
    charge = charge or (lambda: gateway.budget(subject))
    owned = client is None
    client = client or httpx.Client(transport=PublicModelTransport(), timeout=15, trust_env=False)
    headers = {"Authorization": "Bearer " + value.apiKey.get_secret_value()}
    base_payload = {
        "model": value.model,
        "max_tokens": 64,
        "messages": [{"role": "user", "content": 'Return a JSON object: {"ok":true}.'}],
    }
    if value.protocol.startswith("dashscope_"):
        base_payload["enable_thinking"] = False
    responses = value.protocol in {"openai_responses", "dashscope_responses"}
    checks = []
    try:
        with request_scope(started + (75 if responses else 45)):
            for kind in ("text", "json", "stream"):
                charge()
                payload = dict(base_payload)
                if kind == "json":
                    payload["response_format"] = {"type": "json_object"}
                if kind == "stream":
                    payload["stream"] = True
                endpoint = "/chat/completions"
                if responses:
                    from app.model_responses import prepare_request

                    endpoint = "/responses"
                    payload = prepare_request(payload, gateway.config.model_provider)
                with client.stream(
                    "POST",
                    value.baseUrl + endpoint,
                    headers=headers,
                    json=payload,
                    timeout=15,
                ) as response:
                    if response.status_code != 200:
                        # Do not read supplier error bodies into exception/log output.
                        raise response_error(httpx.Response(response.status_code))
                    if kind == "stream":
                        from app.model_responses import TextPrefix

                        prefix = TextPrefix()
                        content, complete, size = False, False, 0
                        for line in response.iter_lines():
                            size += len(line)
                            if size > 65536:
                                raise ValueError("oversized test stream")
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]" and not responses:
                                complete = True
                                break
                            event = json.loads(data)
                            if responses:
                                from app.model_responses import stream_event

                                delta, complete, _usage = stream_event(event)
                                delta = prefix.push(delta)
                                content = content or bool(delta.strip())
                                if complete:
                                    break
                                continue
                            content = content or any(
                                isinstance(row.get("delta", {}).get("content"), str)
                                and bool(row["delta"]["content"].strip())
                                for row in event.get("choices", [])
                            )
                        if not content or not complete:
                            raise ValueError("incomplete stream")
                    else:
                        raw = b""
                        for chunk in response.iter_bytes():
                            raw += chunk
                            if len(raw) > 65536:
                                raise ValueError("oversized test response")
                        result = json.loads(raw)
                        if responses:
                            from app.model_responses import normalize_response

                            result = normalize_response(
                                result, gateway.config.model_provider, json_output=kind == "json"
                            )
                            if result["choices"][0]["finish_reason"] != "stop":
                                raise ValueError("incomplete response")
                        content = result["choices"][0]["message"]["content"]
                        if not isinstance(content, str) or not content.strip():
                            raise ValueError("empty content")
                        if kind == "json" and json.loads(content) != {"ok": True}:
                            raise ValueError("invalid structured content")
                checks.append(kind)
            if responses:
                _probe_responses_tools(
                    client, value, gateway.config.model_provider, headers, charge
                )
                checks.append("tools")
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise DomainError(
            502, "UPSTREAM_MODEL_ERROR", "模型测试失败：请检查协议、JSON、流式与工具调用能力"
        ) from None
    finally:
        if owned:
            client.close()
        gateway.close()
    return {"checks": checks, "durationMs": round((time.monotonic() - started) * 1000)}


def _probe_responses_tools(client, value, provider, headers, charge):
    from app.deadlines import remaining
    from app.model_responses import normalize_response, prepare_request

    def call(payload):
        charge()
        with client.stream(
            "POST",
            value.baseUrl + "/responses",
            headers=headers,
            json=prepare_request(payload, provider),
            timeout=remaining(15),
        ) as response:
            if response.status_code != 200:
                raise response_error(httpx.Response(response.status_code))
            raw = b""
            for chunk in response.iter_bytes():
                raw += chunk
                if len(raw) > 65536:
                    raise ValueError("oversized tool probe")
        result = normalize_response(json.loads(raw), provider)
        if result["choices"][0]["finish_reason"] == "length":
            raise ValueError("incomplete tool probe")
        return result["choices"][0]["message"]

    messages = [
        {"role": "user", "content": "Call connectivity_check, then reply OK using its result."}
    ]
    payload = {
        "model": value.model,
        "max_tokens": 128,
        "messages": messages,
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "connectivity_check",
                    "description": "Return a fixed synthetic connectivity result.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                },
            }
        ],
        "tool_choice": {"type": "function", "function": {"name": "connectivity_check"}},
        **({"enable_thinking": False} if provider == "dashscope" else {}),
    }
    answer = call(payload)
    calls = answer.get("tool_calls", [])
    if (
        len(calls) != 1
        or calls[0]["function"]["name"] != "connectivity_check"
        or json.loads(calls[0]["function"]["arguments"]) != {}
    ):
        raise ValueError("required test tool was not called correctly")
    messages.extend(
        [
            {"role": "assistant", **answer},
            {"role": "tool", "tool_call_id": calls[0]["id"], "content": "OK"},
        ]
    )
    final = call({**payload, "tool_choice": "none"})
    if final.get("tool_calls") or not final.get("content", "").strip():
        raise ValueError("tool continuation did not complete")
