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


class CitationChoice(StrictModel):
    rank: StrictInt = Field(ge=1, le=10)
    supported: StrictBool
    quoteIndex: StrictInt | None = Field(default=None, ge=1, le=10)


class SupportChoices(StrictModel):
    fullySupported: StrictBool
    citations: list[CitationChoice] = Field(min_length=1, max_length=10)


def quote_options(evidence):
    """原文窗口覆盖整个片段并重叠100字；模型只选编号，不能自由改写摘录。"""
    result = []
    for row in evidence:
        content = row["content"]
        if not content or len(content) > 2000:
            raise ValueError("Invalid verification evidence size")
        result.append(
            {
                "rank": row["rank"],
                "quotes": [
                    {"quoteIndex": number, "text": content[offset : offset + 400]}
                    for number, offset in enumerate(range(0, len(content), 300), 1)
                ],
            }
        )
    return result


def resolve_choices(choices, options):
    """只能取当前引用自己的原文窗口；越界、跨片段、真假字符串继续失败关闭。"""
    choices = SupportChoices.model_validate(choices)
    by_rank = {
        row["rank"]: {quote["quoteIndex"]: quote["text"] for quote in row["quotes"]}
        for row in options
    }
    resolved = []
    for row in choices.citations:
        if row.rank not in by_rank:
            raise ValueError("Unknown citation rank")
        if row.supported:
            if row.quoteIndex not in by_rank[row.rank]:
                raise ValueError("Unknown source quote index")
            quote = by_rank[row.rank][row.quoteIndex]
        else:
            if row.quoteIndex is not None:
                raise ValueError("Unsupported citation cannot select a quote")
            quote = ""
        resolved.append(CitationSupport(rank=row.rank, supported=row.supported, quote=quote))
    return AnswerSupport(fullySupported=choices.fullySupported, citations=resolved)


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
