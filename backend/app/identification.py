"""图像识别的字段证据检查；外观猜测、制造地和年份不能变成型号适用条件。"""

import re
import unicodedata

from pydantic import Field

from app.schemas import Candidate, IdentificationOutput, StrictModel


class EvidenceCandidate(Candidate):
    # 供应商内部的可见文字摘录，不扩展公开 API 或写入公共型号资料。
    # 摘录仍是不可信模型输出；通过一致性检查的候选也必须由用户确认，不能声称官方核验。
    modelText: str | None = Field(default=None, max_length=1000)
    regionText: str | None = Field(default=None, max_length=1000)
    hardwareRevisionText: str | None = Field(default=None, max_length=1000)


class EvidenceOutput(StrictModel):
    candidates: list[EvidenceCandidate] = Field(max_length=5)
    clarification: str | None = Field(default=None, max_length=1000)


INFERRED = re.compile(r"推断|推测|猜测|猜想|inferred|guess(?:ed|ing)?", re.IGNORECASE)
REGION_LABEL = re.compile(
    r"销售地区|销售区域|销售版本|地区版本|地区|区域|\bregion\b|\bmarket\b", re.IGNORECASE
)
HARDWARE_LABEL = re.compile(
    r"硬件(?:修订|版本)|\bhardware\s*(?:revision|version|rev)|\bh\s*/?\s*w\b|\brev(?:ision)?\b",
    re.IGNORECASE,
)
NOT_MARKET = re.compile(
    r"制造|生产|产地|出厂|原产|made\s+in|manufactur|country\s+of\s+origin|production",
    re.IGNORECASE,
)
NOT_REVISION = re.compile(
    r"生产日期|制造日期|出厂日期|版权|copyright|©|firmware|固件", re.IGNORECASE
)


def normalized(value):
    return "".join(
        char for char in unicodedata.normalize("NFKC", value).casefold() if char.isalnum()
    )


def labeled_value(value, sources, *, hardware=False):
    if not value:
        return False
    label = HARDWARE_LABEL if hardware else REGION_LABEL
    excluded = NOT_REVISION if hardware else NOT_MARKET
    if hardware:
        value = re.sub(r"^(?:rev(?:ision)?|v)\s*", "", value, flags=re.IGNORECASE)
    target = normalized(value)
    for source in sources:
        for clause in re.split(r"[;；\n。]", source or ""):
            if (
                target
                and label.search(clause)
                and not excluded.search(clause)
                and target in normalized(clause)
            ):
                return True
    return False


def identification_with_evidence(value, text, *, images):
    if not images:
        return IdentificationOutput.model_validate(value)
    observed = EvidenceOutput.model_validate(value)
    candidates = []
    changed = False
    for candidate in observed.candidates:
        model = normalized(candidate.modelCode)
        # 混合输入允许用户明确提供型号；纯图片必须摘录能包含该型号的实际文字。
        supplied = bool(model and model in normalized(text or ""))
        visible = bool(model and model in normalized(candidate.modelText or ""))
        if not (supplied or visible) or (not supplied and INFERRED.search(candidate.evidence)):
            changed = True
            continue
        fields = candidate.model_dump(exclude={"modelText", "regionText", "hardwareRevisionText"})
        if candidate.region and not labeled_value(candidate.region, (candidate.regionText, text)):
            fields["region"] = None
            changed = True
        if candidate.hardwareRevision and not labeled_value(
            candidate.hardwareRevision, (candidate.hardwareRevisionText, text), hardware=True
        ):
            fields["hardwareRevision"] = None
            changed = True
        candidates.append(fields)
    clarification = observed.clarification
    if changed:
        # 不把被拒绝的型号猜测/制造地结论继续留在解释文字中误导用户。
        clarification = (
            "已读取到候选型号，但地区或硬件版本缺少明确标签，请补拍铭牌后确认。"
            if candidates
            else "图片中未读取到完整型号，请补拍机身型号文字或铭牌，不能仅凭外观确认。"
        )
    output = IdentificationOutput.model_validate(
        {"candidates": candidates, "clarification": clarification}
    )
    if output.clarification:
        for candidate in output.candidates:
            candidate.confidence = min(candidate.confidence, 0.6)
    return output
