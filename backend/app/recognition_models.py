"""Strict private hypotheses; appearance inference is never an official identity claim."""

from typing import Literal

from pydantic import Field, field_validator

from app.schemas import ProvidedModel, StrictModel


class PhotoRequest(StrictModel):
    target: Literal["front", "back", "side", "ports", "buttons", "screen", "label"]
    reason: str = Field(min_length=1, max_length=260)
    instruction: str = Field(min_length=1, max_length=260)

    @field_validator("instruction")
    @classmethod
    def photo_only(cls, value):
        import re

        if re.search(r"拆开|拆卸|拆机|通电|接电|高压|短接|插入金属|https?://", value):
            return "保持设备原状，补拍能看清外部特征的照片；不需要拆机。"
        return value


class Hypothesis(StrictModel):
    brandName: str = Field(min_length=1, max_length=120)
    modelCode: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=400)


class Observation(StrictModel):
    category: str = Field(min_length=1, max_length=100)
    brandHint: str | None = Field(default=None, max_length=120)
    features: list[str] = Field(default_factory=list, max_length=8)
    hypotheses: list[Hypothesis] = Field(default_factory=list, max_length=5)
    photoRequests: list[PhotoRequest] = Field(default_factory=list, max_length=2)
    clarification: str | None = Field(default=None, max_length=1000)

    @field_validator("features")
    @classmethod
    def short_features(cls, values):
        if any(not value.strip() or len(value) > 260 for value in values):
            raise ValueError("Invalid visual feature")
        return values


class VisualMatch(ProvidedModel):
    sourceIds: list[str] = Field(min_length=1, max_length=3)
    matchReasons: list[str] = Field(min_length=1, max_length=4)
    differences: list[str] = Field(default_factory=list, max_length=4)
    missingEvidence: list[str] = Field(default_factory=list, max_length=4)
    strength: Literal["likely", "possible"] = "possible"
    referenceImageSourceId: str | None = None

    @field_validator("matchReasons", "differences", "missingEvidence", "sourceIds")
    @classmethod
    def bounded_items(cls, values):
        if any(not value.strip() or len(value) > 400 for value in values):
            raise ValueError("Invalid comparison item")
        return values


class Comparison(StrictModel):
    candidates: list[VisualMatch] = Field(default_factory=list, max_length=3)
    photoRequests: list[PhotoRequest] = Field(default_factory=list, max_length=2)
    clarification: str | None = Field(default=None, max_length=1000)
