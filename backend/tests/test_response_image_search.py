"""Image search uses the model tool, and downloads through the public fetch boundary."""

import json

import httpx
import pytest

from app.errors import DomainError
from tests.test_model_responses import envelope, make_gateway
from tests.test_visual_recognition import picture


def test_search_results_become_bounded_unverified_references(monkeypatch):
    from app.image_search import search_images

    def reply(request):
        payload = json.loads(request.content)
        assert request.url.path.endswith("/responses")
        assert payload["tools"] == [{"type": "image_search"}]
        assert payload["tool_choice"] == "required"
        assert payload["input"][-1]["content"][-1]["type"] == "input_image"
        rows = [{"title": "Acme Phone 16", "url": "https://images.example/16.jpg"}]
        return httpx.Response(
            200,
            json=envelope(
                "possible product",
                output=[
                    {
                        "type": "image_search_call",
                        "status": "completed",
                        "output": json.dumps(rows),
                    },
                ],
            ),
        )

    def fetcher(url, **kwargs):
        assert url == "https://images.example/16.jpg"
        assert kwargs["maximum"] == 2 * 1024 * 1024
        return url, "image/jpeg", picture()

    gateway = make_gateway(monkeypatch, reply)
    refs = search_images(gateway, [(picture(), "image/jpeg")], "user", fetcher=fetcher)
    assert len(refs) == 1
    assert refs[0]["sourceKind"] == "third_party"
    assert refs[0]["imageData"].startswith("data:image/jpeg;base64,")
    assert "未核验" in refs[0]["text"]


@pytest.mark.parametrize(
    "output", [None, "not-json", "{}", '[{"url":"http://127.0.0.1/a","title":"secret"}]']
)
def test_search_missing_or_invalid_output_cannot_create_candidates(monkeypatch, output):
    from app.image_search import search_images

    gateway = make_gateway(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json=envelope(
                output=[
                    {"type": "image_search_call", "status": "completed", "output": output},
                ]
            ),
        ),
    )
    with pytest.raises(DomainError):
        search_images(
            gateway,
            [(picture(), "image/jpeg")],
            "user",
            fetcher=lambda *args, **kwargs: pytest.fail("must not fetch invalid result"),
        )


def test_disabled_tool_is_not_silently_treated_as_a_search(monkeypatch):
    from app.image_search import search_images

    gateway = make_gateway(
        monkeypatch, lambda request: httpx.Response(200, json=envelope("This is a phone"))
    )
    with pytest.raises(DomainError):
        search_images(gateway, [(picture(), "image/jpeg")], "user")


@pytest.mark.parametrize("text_mode", ["unavailable", "download_timeout"])
def test_recognition_uses_image_search_when_text_search_is_unavailable(monkeypatch, text_mode):
    from app.services.recognition import recognize
    from tests.test_visual_recognition import observation

    def reply(request):
        payload = json.loads(request.content)
        if payload.get("tools"):
            return httpx.Response(
                200,
                json=envelope(
                    output=[
                        {
                            "type": "image_search_call",
                            "status": "completed",
                            "output": json.dumps(
                                [{"title": "Acme Phone 16", "url": "https://images.example/16.jpg"}]
                            ),
                        }
                    ]
                ),
            )
        user_content = payload["input"][-1]["content"][0]["text"]
        context = json.loads(user_content)
        if "references" in context:
            ref = context["references"][0]
            return httpx.Response(
                200,
                json=envelope(
                    json.dumps(
                        {
                            "candidates": [
                                {
                                    "brandName": "Acme",
                                    "modelCode": "Phone 16",
                                    "sourceIds": [ref["id"]],
                                    "matchReasons": ["外观与搜图结果相似"],
                                    "referenceImageSourceId": ref["id"],
                                }
                            ]
                        }
                    )
                ),
            )
        return httpx.Response(200, json=envelope(json.dumps(observation())))

    class Search:
        def search(self, *args, **kwargs):
            if text_mode == "download_timeout":
                return [{"url": "https://pages.example/product", "title": "product"}]
            raise DomainError(503, "UPSTREAM_SEARCH_ERROR", "search unavailable")

    def fetcher(url, **kwargs):
        if url.startswith("https://pages.example/"):
            raise DomainError(504, "FETCH_ERROR", "page download timeout")
        return url, "image/jpeg", picture()

    gateway = make_gateway(monkeypatch, reply, vision_image_search_enabled=True)
    result = recognize(
        gateway,
        [(picture(), "image/jpeg")],
        "",
        {},
        "user",
        search=Search(),
        fetcher=fetcher,
    )
    assert result["candidates"][0]["sources"][0]["url"] == "https://images.example/16.jpg"
    assert result["candidates"][0]["referenceImage"]
    assert result["analysis"]["imageSearchStatus"] == "found"
