"""MVP 写入契约；拒绝额外字段、空白输入及重复附件。"""

import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Credentials(StrictModel):
    # 密码必须逐字保留；全局表单 trim 规则不能改变用户的密码。
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=128)

    @model_validator(mode="after")
    def email_valid(self):
        self.email = self.email.strip()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", self.email):
            raise ValueError("邮箱格式无效")
        return self


class Input(StrictModel):
    assetIds: list[UUID] = Field(default_factory=list, max_length=10)

    def validate_input(self, value):
        if value is not None and not value.strip():
            raise ValueError("文字不能只包含空白")
        if not value and not self.assetIds:
            raise ValueError("请填写文字或上传附件")
        if len(set(self.assetIds)) != len(self.assetIds):
            raise ValueError("附件不能重复")
        return self


class IdentifyRequest(Input):
    inputText: str | None = Field(default=None, min_length=1, max_length=10000)
    supersedesAttemptId: UUID | None = None

    @model_validator(mode="after")
    def complete(self):
        return self.validate_input(self.inputText)


class MessageRequest(Input):
    content: str | None = Field(default=None, min_length=1, max_length=10000)

    @model_validator(mode="after")
    def complete(self):
        return self.validate_input(self.content)


class PresignRequest(StrictModel):
    filename: str = Field(min_length=1, max_length=255)
    mimeType: Literal[
        "image/jpeg",
        "image/png",
        "image/webp",
        "audio/mpeg",
        "audio/wav",
        "audio/webm",
        "audio/mp4",
    ]
    sizeBytes: int = Field(ge=1, le=26214400)
    kind: Literal["product_photo", "nameplate", "voice", "feedback"]


class ProvidedModel(StrictModel):
    brandName: str = Field(min_length=1, max_length=120)
    modelCode: str = Field(min_length=1, max_length=160)
    region: str | None = Field(default=None, max_length=40)
    hardwareRevision: str | None = Field(default=None, max_length=80)


class ConfirmRequest(StrictModel):
    productModelId: UUID | None = None
    userProvidedModel: ProvidedModel | None = None
    correctionNote: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def exclusive(self):
        if bool(self.productModelId) == bool(self.userProvidedModel):
            raise ValueError("请选择候选或输入型号")
        return self


class InstanceRequest(StrictModel):
    productModelId: UUID
    nickname: str | None = Field(default=None, max_length=120)


class KnowledgeRequest(StrictModel):
    forceRefresh: bool = False


class ConversationRequest(StrictModel):
    userProductInstanceId: UUID
    title: str | None = Field(default=None, max_length=200)


class Candidate(ProvidedModel):
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(max_length=1000)


class IdentificationOutput(StrictModel):
    candidates: list[Candidate] = Field(max_length=5)
    clarification: str | None = Field(default=None, max_length=1000)
