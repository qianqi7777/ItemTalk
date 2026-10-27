"""Real appearance pipeline with supplier/search/download boundaries replaced only."""

import importlib
import json

import httpx

from app.adapters import ModelGateway
from app.config import Settings
from app.errors import DomainError
from tests.test_visual_recognition import observation, picture


def matching(**changes):
    return {
        "brandName": "Acme",
        "modelCode": "Phone 16",
        "sourceIds": ["ref"],
        "matchReasons": ["镜头布局相似"],
        "differences": [],
        "missingEvidence": ["未拍正面"],
        "strength": "possible",
        "referenceImageSourceId": None,
        **changes,
    }


def test_only_real_sources_can_back_candidates_and_regions_need_labels():
    service = importlib.import_module("app.services.recognition")
    models = importlib.import_module("app.recognition_models")
    result = models.Comparison(candidates=[matching(region="CN", hardwareRevision="2025")])
    refs = [
        {
            "id": "ref",
            "url": "https://vendor.example/phone",
            "title": "Acme Phone 16",
            "text": "Acme Phone 16 product",
            "sourceKind": "third_party",
        }
    ]
    candidates = service.candidates_from_sources(result, refs, {}, "")
    assert len(candidates) == 1
    assert candidates[0]["candidateId"] and "productModelId" not in candidates[0]
    assert candidates[0]["region"] is None and candidates[0]["hardwareRevision"] is None
    assert (
        service.candidates_from_sources(
            models.Comparison(candidates=[matching(sourceIds=["invented"])]), refs, {}, ""
        )
        == []
    )
    assert (
        service.candidates_from_sources(
            models.Comparison(candidates=[matching(modelCode="Phone 17")]), refs, {}, ""
        )
        == []
    )
    assert (
        service.candidates_from_sources(result, refs, {"rejectedModels": ["Acme Phone 16"]}, "")
        == []
    )


def test_rejected_apple_model_cannot_reenter_under_chinese_brand_alias():
    service = importlib.import_module("app.services.recognition")
    models = importlib.import_module("app.recognition_models")
    refs = [
        {
            "id": "ref",
            "url": "https://vendor.example/phone",
            "title": "Apple iPhone 16",
            "text": "Apple iPhone 16 details",
            "sourceKind": "third_party",
        }
    ]
    result = models.Comparison(candidates=[matching(brandName="苹果", modelCode="iPhone 16")])
    assert (
        service.candidates_from_sources(result, refs, {"rejectedModels": ["Apple iPhone 16"]}, "")
        == []
    )


def test_registered_official_brand_can_use_model_only_official_page():
    service = importlib.import_module("app.services.recognition")
    models = importlib.import_module("app.recognition_models")
    refs = [
        {
            "id": "ref",
            "url": "https://acme.example/phone",
            "title": "Phone 16",
            "text": "Phone 16 technical details",
            "sourceKind": "official",
        }
    ]
    result = models.Comparison(candidates=[matching()])
    assert service.candidates_from_sources(
        result, refs, {}, "", registered_domains={"acme": ["acme.example"]}
    )
    assert (
        service.candidates_from_sources(
            result, refs, {}, "", registered_domains={"other": ["acme.example"]}
        )
        == []
    )


def test_comparison_mention_cannot_borrow_another_models_reference_image():
    service = importlib.import_module("app.services.recognition")
    models = importlib.import_module("app.recognition_models")
    sources = importlib.import_module("app.services.recognition_sources")
    for title in ["Acme Phone 16", "Acme Phones"]:
        refs = [
            {
                "id": "ref",
                "url": "https://acme.example/phones",
                "title": title,
                "text": "Acme Phone 16. Compare with Acme Phone 17.",
                "sourceKind": "third_party",
                "imageData": "data:image/jpeg;base64,16",
            }
        ]
        result = models.Comparison(
            candidates=[matching(modelCode="Phone 17", referenceImageSourceId="ref")]
        )
        candidate = service.candidates_from_sources(result, refs, {}, "")[0]
        assert candidate["referenceImage"] is None
    # The exact single-product caption still retains its own thumbnail.
    refs[0]["title"] = "Acme Phone 17"
    assert service.candidates_from_sources(result, refs, {}, "")[0]["referenceImage"]
    assert not sources.exact_image_title("Acme Phone 16 and Phone 17", "Acme", "Phone 17")


def test_search_outage_keeps_observation_and_targeted_photo_request(monkeypatch):
    service = importlib.import_module("app.services.recognition")

    def respond(request):
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(observation())}}]}
        )

    class Search:
        def search(self, *a, **kw):
            raise DomainError(503, "UPSTREAM_SEARCH_ERROR", "搜索未配置")

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        gateway = ModelGateway(
            client, configuration=Settings(model_provider="dashscope", vision_api_key="test")
        )
        monkeypatch.setattr(gateway, "budget", lambda subject: None)
        value = service.recognize(
            gateway, [(picture(), "image/jpeg")], "", {}, "user", search=Search()
        )
    assert value["candidates"] == []
    assert value["analysis"]["observation"]["category"] == "智能手机"
    assert value["analysis"]["photoRequests"][0]["target"] == "front"
    assert value["analysis"]["researchStatus"] == "unavailable"


def test_exhausted_search_budget_preserves_observation_and_explains_scope(monkeypatch):
    service = importlib.import_module("app.services.recognition")

    def respond(request):
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(observation())}}]}
        )

    class Search:
        def search(self, *a, **kw):
            raise DomainError(429, "RATE_LIMITED", "站内全站日调用额度已用完")

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        gateway = ModelGateway(
            client, configuration=Settings(model_provider="dashscope", vision_api_key="test")
        )
        monkeypatch.setattr(gateway, "budget", lambda subject: None)
        value = service.recognize(
            gateway, [(picture(), "image/jpeg")], "", {}, "user", search=Search()
        )
    assert value["analysis"]["researchStatus"] == "limited"
    assert "额度" in value["clarification"]
    assert value["analysis"]["observation"]["features"]


def test_two_visual_passes_use_downloaded_sources_and_all_user_photos(monkeypatch):
    service = importlib.import_module("app.services.recognition")
    requests, searches, downloads = [], [], []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            output = observation()
        else:
            context = json.loads(payload["messages"][-1]["content"][0]["text"])
            ref = next(r for r in context["references"] if r["title"] == "iPhone 16")
            output = {
                "candidates": [
                    matching(
                        brandName="Apple",
                        modelCode="iPhone 16",
                        sourceIds=[ref["id"]],
                        referenceImageSourceId=ref["id"],
                    )
                ],
                "photoRequests": [],
                "clarification": "请选择或补拍",
            }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(output)}}]})

    class Search:
        def search(self, query, **kwargs):
            searches.append(query)
            return [{"url": "https://apple.com/phones", "title": "Apple phones"}]

    def fetch(url, **kwargs):
        downloads.append(url)
        if url.endswith(".jpg"):
            return url, "image/jpeg", picture()
        return (
            url,
            "text/html",
            (b'<h2>iPhone 16</h2><img src="/16.jpg"><p>Apple iPhone 16 two cameras</p>'),
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        gateway = ModelGateway(
            client, configuration=Settings(model_provider="dashscope", vision_api_key="test")
        )
        monkeypatch.setattr(gateway, "budget", lambda subject: None)
        value = service.recognize(
            gateway, [(picture(), "image/jpeg")] * 2, "", {}, "user", search=Search(), fetcher=fetch
        )
    assert len(requests) == 2 and len(searches) == 2
    assert len(downloads) == 2
    assert value["candidates"][0]["modelCode"] == "iPhone 16"
    assert value["candidates"][0]["referenceImage"].startswith("data:image/jpeg")
    assert (
        len(
            [item for item in requests[1]["messages"][-1]["content"] if item["type"] == "image_url"]
        )
        == 3
    )
