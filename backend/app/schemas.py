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
    forceWebSearch: bool = False

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


class FeedbackRequest(StrictModel):
    feedbackType: Literal["helpful", "incorrect"]
    note: str | None = Field(default=None, max_length=2000)


class GapIntent(StrictModel):
    category: Literal[
        "setup", "power", "connectivity", "features", "maintenance", "troubleshooting", "other"
    ]


class ExtractedMemory(StrictModel):
    memoryType: Literal["preference", "product_fact"]
    content: str = Field(min_length=1, max_length=1200)


class MemoryExtraction(StrictModel):
    memories: list[ExtractedMemory] = Field(default_factory=list, max_length=5)


class GuidanceRequest(StrictModel):
    userProductInstanceId: UUID
    topic: str = Field(min_length=1, max_length=200)


class GuidanceFeedback(StrictModel):
    currentStepNo: int = Field(ge=1)
    expectedVersion: int = Field(ge=1)
    result: Literal["completed", "failed", "unclear", "paused"]
    note: str | None = Field(default=None, max_length=2000)
    assetIds: list[UUID] = Field(default_factory=list, max_length=3)
    preconditionsConfirmed: bool = False

    @model_validator(mode="after")
    def unique_assets(self):
        if len(set(self.assetIds)) != len(self.assetIds):
            raise ValueError("反馈图片不能重复")
        return self


class GuidanceResume(StrictModel):
    expectedVersion: int = Field(ge=1)


class PlannedStep(StrictModel):
    instruction: str = Field(min_length=1, max_length=1500)
    successCriteria: str = Field(min_length=1, max_length=500)
    riskLevel: Literal["low", "medium"]
    preconditions: list[str] = Field(default_factory=list, max_length=6)
    safetyNotes: list[str] = Field(default_factory=list, max_length=6)
    citationRanks: list[int] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def bounded_fields(self):
        if any(
            not value.strip() or len(value) > 500 for value in self.preconditions + self.safetyNotes
        ):
            raise ValueError("前提或安全提示长度无效")
        if len(set(self.citationRanks)) != len(self.citationRanks) or any(
            not 1 <= rank <= 12 for rank in self.citationRanks
        ):
            raise ValueError("引用编号无效")
        return self


class GuidancePlan(StrictModel):
    riskLevel: Literal["low", "medium", "high"]
    steps: list[PlannedStep] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def require_safe_steps(self):
        if self.riskLevel != "high" and not self.steps:
            raise ValueError("低/中风险引导必须有证据支持的步骤")
        return self


class GuidanceObservation(StrictModel):
    observation: str = Field(min_length=1, max_length=1500)
    uncertainty: str = Field(max_length=500)


class ManualJobRequest(StrictModel):
    userProductInstanceId: UUID | None = None


class SectionJobRequest(StrictModel):
    sectionIds: list[UUID] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def unique_sections(self):
        if len(set(self.sectionIds)) != len(self.sectionIds):
            raise ValueError("章节不能重复")
        return self


class PersonalChapterNote(StrictModel):
    text: str = Field(min_length=1, max_length=1000)
    memoryIds: list[UUID] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def unique_memory(self):
        if len(set(self.memoryIds)) != len(self.memoryIds):
            raise ValueError("个人补充的记忆引用不能重复")
        return self


class ChapterPersonalization(StrictModel):
    notes: list[PersonalChapterNote] = Field(default_factory=list, max_length=10)
