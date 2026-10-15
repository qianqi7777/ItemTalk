"""真实网络样本发现的识别误猜与制造地混淆回归；不调用付费服务。"""

import json

import httpx
import pytest
from pydantic import ValidationError

from app.identification import identification_with_evidence


def result(**changes):
    return {
        "candidates": [
            {
                "brandName": "Raspberry Pi",
                "modelCode": "4 Model B",
                "confidence": 0.95,
                "evidence": "板上文字 Raspberry Pi 4 Model B",
                "modelText": "Raspberry Pi 4 Model B",
                **changes,
            }
        ],
        "clarification": None,
    }


def test_rear_view_shape_guess_is_not_returned_or_repeated_in_clarification():
    value = result(
        modelCode="D3100",
        brandName="Nikon",
        modelText=None,
        evidence="依据 GUIDE 转盘与按键布局推断 D3100",
    )
    value["clarification"] = "外观推断为 D3100，请补拍铭牌。"
    output = identification_with_evidence(value, None, images=True)
    assert output.candidates == []
    assert "D3100" not in output.clarification and "铭牌" in output.clarification


def test_claimed_literal_does_not_override_an_admitted_shape_guess():
    output = identification_with_evidence(
        result(modelText="4 Model B", evidence="根据外观推测型号"),
        None,
        images=True,
    )
    assert output.candidates == []


@pytest.mark.parametrize(
    "label", ["Made in the UK", "制造地区 UK", "生产地区 UK", "Region UK (manufactured in UK)"]
)
def test_manufacturing_country_is_not_a_sales_region(label):
    value = result(region="UK", regionText=label)
    value["clarification"] = "依据 Made in the UK 确定地区 UK"
    output = identification_with_evidence(value, None, images=True)
    assert len(output.candidates) == 1 and output.candidates[0].region is None
    assert "UK" not in output.clarification


def test_country_without_a_sales_label_does_not_qualify():
    output = identification_with_evidence(result(region="UK", regionText="UK"), None, images=True)
    assert output.candidates[0].region is None


def test_explicit_region_and_hardware_revision_labels_are_retained():
    output = identification_with_evidence(
        result(
            region="CN",
            regionText="Market: CN",
            hardwareRevision="1.4",
            hardwareRevisionText="H/W Ver: 1.4",
        ),
        None,
        images=True,
    )
    assert output.candidates[0].region == "CN"
    assert output.candidates[0].hardwareRevision == "1.4"
    assert output.candidates[0].confidence == 0.95
    assert "modelText" not in output.candidates[0].model_dump()


@pytest.mark.parametrize(
    "label", ["Copyright 2018", "固件版本 Rev 2018", "硬件版本 2018 Copyright 2018"]
)
def test_copyright_or_firmware_is_not_hardware_revision(label):
    output = identification_with_evidence(
        result(hardwareRevision="2018", hardwareRevisionText=label), None, images=True
    )
    assert output.candidates[0].hardwareRevision is None


def test_user_supplied_model_and_explicit_labels_work_with_an_ambiguous_photo():
    value = result(modelText=None, region="CN", hardwareRevision="rev2")
    output = identification_with_evidence(
        value, "我的型号 4 Model B；地区 CN；硬件版本 rev2", images=True
    )
    assert output.candidates[0].region == "CN" and output.candidates[0].hardwareRevision == "rev2"


def test_text_only_contract_remains_unchanged():
    value = result(region="CN")
    value["candidates"][0].pop("modelText")
    output = identification_with_evidence(value, "Raspberry Pi 4 Model B CN", images=False)
    assert output.candidates[0].region == "CN"


def test_unknown_provider_fields_still_fail_strict_validation():
    with pytest.raises(ValidationError):
        identification_with_evidence(result(unexpected="not allowed"), None, images=True)


def test_image_protocol_example_keeps_evidence_on_each_candidate(monkeypatch):
    from app.adapters import ModelGateway
    from app.config import Settings

    def respond(request):
        payload = json.loads(request.content)
        prompt = payload["messages"][0]["content"]
        example = json.loads(prompt.split("输出结构示例：", 1)[1].split("\n", 1)[0])
        assert set(example) == {"candidates", "clarification"}
        assert {"modelText", "regionText", "hardwareRevisionText"} <= set(example["candidates"][0])
        # 实际供应商曾把摘录放顶层：协议示例必须明确嵌套，不能放宽 extra 校验补救。
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(result())}}]}
        )

    gateway = ModelGateway(httpx.Client(transport=httpx.MockTransport(respond)))
    gateway.config = Settings(model_provider="dashscope", vision_api_key="test-key")
    monkeypatch.setattr(gateway, "budget", lambda subject: None)
    output = gateway.identify(None, [(b"photo", "image/png")], "test")
    assert output.candidates[0].modelCode == "4 Model B"


def test_root_level_evidence_fields_still_fail_strict_validation():
    value = result()
    value["modelText"] = "4 Model B"
    with pytest.raises(ValidationError):
        identification_with_evidence(value, None, images=True)
