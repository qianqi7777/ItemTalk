"""问答引用支持核验；判断来自模型，编号、摘录及删除范围仍由服务端约束。"""

import re

from pydantic import Field, StrictBool, StrictInt, model_validator

from app.errors import DomainError
from app.schemas import StrictModel
from app.security import normalize


class CitationSupport(StrictModel):
    rank: StrictInt = Field(ge=1, le=10)
    supported: StrictBool
    quote: str = Field(max_length=400)


class AnswerSupport(StrictModel):
    fullySupported: StrictBool
    citations: list[CitationSupport] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def unique_ranks(self):
        ranks = [row.rank for row in self.citations]
        if len(ranks) != len(set(ranks)):
            raise ValueError("Duplicate citation assessment")
        return self


def compact(value):
    return re.sub(r"\s+", "", normalize(value))


def grounded_answer(answer, evidence, assessment):
    """只删除原有引用，不新增编号或改写事实；摘录不能替代模型判断的语义充分性。"""
    assessment = AnswerSupport.model_validate(assessment)
    by_rank = {row["rank"]: row["content"] for row in evidence}
    markers = re.findall(r"\[(\d+)\]", answer)
    if (
        not markers
        or any(len(value) > 4 for value in markers)
        or {int(value) for value in markers} != set(by_rank)
        or len(by_rank) != len(evidence)
        or {row.rank for row in assessment.citations} != set(by_rank)
    ):
        raise DomainError(502, "UPSTREAM_MODEL_ERROR", "引用核验结果无效")
    retained = set()
    for row in assessment.citations:
        if row.supported:
            quote = compact(row.quote)
            # 模型不能用虚构的原文摘录为引用背书，空摘录也不能表示支持。
            if not quote or quote not in compact(by_rank[row.rank]):
                raise DomainError(502, "UPSTREAM_MODEL_ERROR", "引用核验摘录无效")
            retained.add(row.rank)
    if not assessment.fullySupported or not retained:
        return None, set()
    cleaned = re.sub(
        r"\[(\d+)\]",
        lambda match: match[0] if int(match[1]) in retained else "",
        answer,
    )
    return cleaned, retained
