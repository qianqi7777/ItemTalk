"""环境配置入口；秘密字段不参与日志展示，数据库 URL 由驱动安全构造。"""

import json
from datetime import date
from decimal import Decimal
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy import URL


class TextProfile(BaseModel):
    model: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    accepted: bool = False


class PriceRate(BaseModel):
    provider: str
    model: str
    effective_from: date
    currency: Literal["CNY", "USD"] = "CNY"
    input_per_million: Decimal = Field(ge=0, le=1000000, allow_inf_nan=False)
    output_per_million: Decimal = Field(ge=0, le=1000000, allow_inf_nan=False)
    min_input_tokens: int = Field(default=0, ge=0)
    max_input_tokens: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def valid_range(self):
        if self.max_input_tokens is not None and self.max_input_tokens < self.min_input_tokens:
            raise ValueError("Invalid input-token pricing range")
        return self


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    environment: str = "development"
    database_url: SecretStr | None = None
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "itemtalk"
    db_user: str = "itemtalk"
    db_password: SecretStr = SecretStr("devpassword")
    jwt_secret: SecretStr = SecretStr("development-only-secret")
    auth_cookie_secure: bool = False
    auth_cookie_path: str = "/"
    auth_ttl_seconds: int = 86400
    public_path_prefix: str = ""
    app_public_url: str = "http://localhost:5173/"
    build_version: str = Field(default="0" * 40, pattern=r"^[0-9a-f]{40}$")
    model_provider: str = "dashscope"
    model_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model_api_key: SecretStr = SecretStr("")
    chat_model: str = "qwen3.7-flash"
    vision_provider: str = "dashscope"
    vision_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    vision_api_key: SecretStr = SecretStr("")
    vision_model: str = "qwen3.7-flash"
    embedding_provider: str = "dashscope"
    embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embedding_api_key: SecretStr = SecretStr("")
    embedding_model: str = "text-embedding-v4"
    embedding_dimensions: int = Field(default=1024, ge=1024, le=1024)
    embedding_revision: str = "initial"
    search_provider: str = ""
    search_base_url: str = "https://api.bochaai.com/v1/web-search"
    search_api_key: SecretStr = SecretStr("")
    stt_provider: str = ""
    stt_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    stt_api_key: SecretStr = SecretStr("")
    stt_model: str = "qwen3-asr-flash"
    stt_timeout_seconds: int = Field(default=60, ge=1, le=180)
    chat_timeout_seconds: int = Field(default=45, ge=1, le=180)
    vision_timeout_seconds: int = Field(default=60, ge=1, le=180)
    embedding_timeout_seconds: int = Field(default=30, ge=1, le=180)
    search_timeout_seconds: int = Field(default=20, ge=1, le=120)
    search_max_results: int = Field(default=10, ge=1, le=20)
    upstream_max_attempts: int = Field(default=2, ge=1, le=3)
    fetch_dns_provider: Literal["system", "cloudflare"] = "system"
    tts_provider: str = ""
    cos_secret_id: SecretStr = SecretStr("")
    cos_secret_key: SecretStr = SecretStr("")
    cos_bucket: str = ""
    cos_region: str = "ap-beijing"
    storage_backend: str = "local"
    storage_directory: str = ".local-data/assets"
    model_timeout_seconds: int = 45
    model_max_output_tokens: int = 2048
    daily_model_call_limit: int = 100
    daily_global_model_call_limit: int = 1000
    lease_seconds: int = 180
    run_timeout_seconds: int = 180
    worker_poll_seconds: float = 1
    rate_limit_per_minute: int = 60
    # 独立发布门禁：迁移/集成验收后启用，不改变既有V1环境。
    v2_enabled: bool = False
    model_profiles: Annotated[dict[str, TextProfile], NoDecode] = Field(default_factory=dict)
    model_prices: Annotated[list[PriceRate], NoDecode] = Field(default_factory=list)
    runtime_global_hard_limit: int = Field(default=1000, ge=1, le=1000000)
    runtime_subject_hard_limit: int = Field(default=100, ge=1, le=1000000)
    query_cache_ttl_seconds: int = Field(default=86400, ge=1, le=86400)
    acceptance_run_id: str = Field(default="", pattern=r"^[a-zA-Z0-9_-]{0,64}$")
    acceptance_call_budget: int = Field(default=300, ge=1, le=300)

    @field_validator("model_profiles", "model_prices", mode="before")
    @classmethod
    def optional_json(cls, value, info):
        if isinstance(value, str):
            return (
                json.loads(value)
                if value.strip()
                else ({} if info.field_name == "model_profiles" else [])
            )
        return value

    @model_validator(mode="after")
    def production_requirements(self):
        if self.environment in {"production", "staging"}:
            if len(self.jwt_secret.get_secret_value()) < 32 or not self.auth_cookie_secure:
                raise ValueError("Hosted environments require a strong secret and Secure cookies")
            if not self.app_public_url.startswith("https://"):
                raise ValueError("Hosted environments require HTTPS")
            if self.model_provider == "mock":
                raise ValueError("Mock providers are restricted to development/tests")
        if self.environment == "production":
            if self.storage_backend != "cos" or not self.cos_bucket:
                raise ValueError("Production requires COS storage")
        if self.storage_backend == "cos" and self.environment in {"production", "staging"}:
            if not all(
                (
                    self.cos_bucket,
                    self.cos_secret_id.get_secret_value(),
                    self.cos_secret_key.get_secret_value(),
                )
            ):
                raise ValueError("Hosted COS storage requires complete credentials")
        return self

    def db_url(self):
        if self.database_url:
            return self.database_url.get_secret_value()
        return URL.create(
            "postgresql+psycopg",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )


@lru_cache
def settings():
    return Settings()
