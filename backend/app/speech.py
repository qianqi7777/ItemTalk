"""录音转写适配器：只接收已确认的音频字节，不自动把转写当成用户消息。"""

import base64
import io
import wave

from app.adapters import ModelGateway
from app.config import settings
from app.errors import DomainError


class ASRProvider:
    """Qwen3-ASR-Flash 的 OpenAI 兼容协议，与文本/视觉保持独立配置。"""

    def __init__(self, gateway=None):
        self.gateway = gateway or ModelGateway()
        self.config = self.gateway.config

    def transcribe(self, data, mime, subject):
        if self.config.stt_provider != "dashscope":
            raise DomainError(503, "UPSTREAM_MODEL_ERROR", "语音转写未配置")
        if mime not in {"audio/wav", "audio/mpeg", "audio/mp4", "audio/webm"}:
            raise DomainError(422, "FILE_TYPE_NOT_ALLOWED", "此音频格式不支持语音转写")
        # Data URL 受供应商 10MB 上限约束，预留编码膨胀和请求头空间。
        if not data or len(data) > 7 * 1024 * 1024:
            raise DomainError(413, "FILE_TOO_LARGE", "转写音频需小于 7 MB")
        if mime == "audio/wav":
            try:
                with wave.open(io.BytesIO(data)) as audio:
                    duration = audio.getnframes() / audio.getframerate()
                    if duration > 180:
                        raise DomainError(422, "VALIDATION_ERROR", "录音最长 3 分钟")
            except (wave.Error, EOFError, ZeroDivisionError):
                raise DomainError(422, "FILE_TYPE_NOT_ALLOWED", "WAV 音频格式无效") from None
        self.gateway.budget(subject)
        result = self.gateway.request(
            "stt",
            "/chat/completions",
            {
                "model": self.config.stt_model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_audio",
                                "input_audio": {
                                    "data": f"data:{mime};base64,{base64.b64encode(data).decode()}"
                                },
                            }
                        ],
                    }
                ],
                "stream": False,
                "asr_options": {"language": "zh", "enable_itn": False},
            },
        )
        try:
            text = result["choices"][0]["message"]["content"]
            if not isinstance(text, str) or not text.strip() or len(text) > 10000:
                raise ValueError("Invalid transcription")
            return text.strip()
        except (KeyError, IndexError, TypeError, ValueError):
            raise DomainError(502, "UPSTREAM_MODEL_ERROR", "转写结果无效，请重新录音") from None


def asr_enabled():
    config = settings()
    return config.stt_provider == "dashscope" and bool(
        config.stt_api_key.get_secret_value()
        or (config.model_provider == "dashscope" and config.model_api_key.get_secret_value())
    )
