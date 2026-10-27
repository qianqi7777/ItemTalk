"""Appearance recognition contracts and real-source boundaries, no external network."""

import importlib
import io
import json

import httpx
import pytest
from PIL import Image

from app.adapters import ModelGateway
from app.config import Settings
from app.errors import DomainError


def picture():
    out = io.BytesIO()
    Image.new("RGB", (40, 60), "blue").save(out, "JPEG")
    return out.getvalue()


def observation():
    return {
        "category": "智能手机",
        "brandHint": "Apple",
        "features": ["两个竖向摄像头"],
        "hypotheses": [{"brandName": "Apple", "modelCode": "iPhone 16", "reason": "镜头布局"}],
        "photoRequests": [
            {"target": "front", "reason": "比较屏幕和边框", "instruction": "补拍正面屏幕"}
        ],
        "clarification": "需要结合正面和侧面区分",
    }


def test_photo_without_nameplate_returns_category_features_and_hypothesis(monkeypatch):
    vision = importlib.import_module("app.recognition_vision")
    seen = []

    def respond(request):
        seen.append(json.loads(request.content))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(observation())}}]}
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        gateway = ModelGateway(
            client, configuration=Settings(model_provider="dashscope", vision_api_key="test-only")
        )
        monkeypatch.setattr(gateway, "budget", lambda subject: None)
        result = vision.observe(gateway, [(picture(), "image/jpeg")], "", {}, "user")
    assert result.category == "智能手机" and result.hypotheses[0].modelCode == "iPhone 16"
    assert result.photoRequests[0].target == "front"
    assert seen[0]["model"] == "qwen3.7-flash"
    assert any(row["type"] == "image_url" for row in seen[0]["messages"][-1]["content"])


def test_comparison_sees_all_photos_reference_image_and_rejected_models(monkeypatch):
    vision = importlib.import_module("app.recognition_vision")
    models = importlib.import_module("app.recognition_models")
    seen = []

    def respond(request):
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "candidates": [],
                                    "photoRequests": [],
                                    "clarification": "目前候选不符，请补拍侧面",
                                }
                            )
                        }
                    }
                ]
            },
        )

    ref = {
        "id": "s1",
        "url": "https://vendor.example/phone",
        "title": "Phone 16",
        "text": "Apple iPhone 16 technical details",
        "sourceKind": "third_party",
        "imageData": "data:image/jpeg;base64," + __import__("base64").b64encode(picture()).decode(),
    }
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        gateway = ModelGateway(
            client, configuration=Settings(model_provider="dashscope", vision_api_key="test-only")
        )
        monkeypatch.setattr(gateway, "budget", lambda subject: None)
        vision.compare(
            gateway,
            [(picture(), "image/jpeg")] * 2,
            models.Observation(**observation()),
            [ref],
            {"rejectedModels": ["Apple iPhone 17"]},
            "user",
        )
    images = [x for x in seen[0]["messages"][-1]["content"] if x["type"] == "image_url"]
    assert len(images) == 3
    assert "Apple iPhone 17" in json.dumps(seen[0], ensure_ascii=False)


def test_reference_extraction_keeps_product_sections_and_correct_image():
    sources = importlib.import_module("app.services.recognition_sources")
    raw = (
        b'<h1>Identify phone</h1><h2>Phone 16</h2><img src="/16.jpg">'
        b'<p>Acme Phone 16 details</p><h2>Phone 17</h2><img src="/17.jpg">'
        b"<p>Acme Phone 17 details</p>"
    )
    refs = sources.page_references("https://vendor.example/models", "Phones", raw)
    sixteen = next(r for r in refs if r["title"] == "Phone 16")
    assert sixteen["imageUrl"] == "https://vendor.example/16.jpg"
    assert "Phone 17 details" not in sixteen["text"]
    assert all(not r.get("imageUrl") for r in refs if r["title"] == "Identify phone")


@pytest.mark.parametrize(
    "brand,model,text,expected",
    [
        ("Apple", "iPhone 16", "Apple iPhone 16 技术规格", True),
        ("Apple", "iPhone 16", "Apple iPhone 16 Pro Max 技术规格", False),
        ("Apple", "iPhone 16", "Apple iPhone 160", False),
        ("Apple", "iPhone 17", "OtherBrand iPhone 17", False),
    ],
)
def test_source_must_contain_correct_brand_and_model_variant(brand, model, text, expected):
    sources = importlib.import_module("app.services.recognition_sources")
    assert sources.supports_model(brand, model, text) is expected


def test_thumbnail_is_bounded_real_jpeg_and_rejects_fake_content():
    sources = importlib.import_module("app.services.recognition_sources")
    result = sources.thumbnail(picture(), "image/jpeg")
    assert result.startswith("data:image/jpeg;base64,") and len(result) < 110000
    with pytest.raises(DomainError):
        sources.thumbnail(b'<svg onload="alert(1)"></svg>', "image/jpeg")


def test_search_queries_never_send_raw_private_note_or_serial_number():
    sources = importlib.import_module("app.services.recognition_sources")
    models = importlib.import_module("app.recognition_models")
    item = observation()
    item["features"] = ["联系13812345678", "序列号 SERIAL-123456789012345"]
    queries = sources.search_queries(models.Observation(**item))
    assert queries and all("13812345678" not in q and "123456789012345" not in q for q in queries)


def test_search_queries_do_not_echo_arbitrary_private_category_or_feature_text():
    sources = importlib.import_module("app.services.recognition_sources")
    models = importlib.import_module("app.recognition_models")
    item = observation()
    item.update(category="客户私人地址", brandHint="张先生私有品牌", features=["客户保密短语"])
    item["hypotheses"][0]["modelCode"] = "客户保密短语"
    value = " ".join(sources.search_queries(models.Observation(**item)))
    assert "客户" not in value and "张先生" not in value and "保密" not in value


@pytest.mark.parametrize("private", ["Alice lives at 3 Oak Road", "AB12CD34"])
def test_search_does_not_trust_model_guess_to_be_public(private):
    sources = importlib.import_module("app.services.recognition_sources")
    models = importlib.import_module("app.recognition_models")
    item = observation()
    item["hypotheses"][0]["modelCode"] = private
    assert private not in " ".join(sources.search_queries(models.Observation(**item)))
