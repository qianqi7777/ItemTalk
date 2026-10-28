"""Scanned PDF rendering and literal transcription stay bounded and page-specific."""

import importlib
import io
import json

import httpx
import pytest
from PIL import Image
from pypdf import PdfWriter

from app.adapters import ModelGateway
from app.config import Settings
from app.errors import DomainError
from app.parser import parse_document
from tests.test_parser import pdf_bytes


def test_empty_pdf_pages_can_be_requested_for_the_scanned_manual_pipeline():
    assert parse_document(
        pdf_bytes(None, "", pages=2, text=b""), "application/pdf", allow_empty_pdf=True
    ) == [[1, ""], [2, ""]]


def test_empty_pages_without_content_do_not_consume_the_scan_page_limit():
    writer = PdfWriter()
    writer.append(io.BytesIO(pdf_bytes(None, "", text=b"Official instructions")))
    for _ in range(21):
        writer.add_blank_page(width=100, height=100)
    output = io.BytesIO()
    writer.write(output)
    scanned = []
    pages = parse_document(output.getvalue(), "application/pdf", scan_pages=scanned)
    assert len(pages) == 22
    assert scanned == []


def test_short_native_text_with_a_scanned_image_is_selected_for_ocr():
    from pypdf import PdfReader

    from tests.test_scanned_manuals import scanned_pdf

    writer = PdfWriter()
    page = PdfReader(io.BytesIO(scanned_pdf())).pages[0]
    page.merge_page(PdfReader(io.BytesIO(pdf_bytes(None, "", text=b"Page 1"))).pages[0])
    writer.add_page(page)
    output = io.BytesIO()
    writer.write(output)
    scanned = []
    pages = parse_document(output.getvalue(), "application/pdf", scan_pages=scanned)
    assert pages[0][1].strip() == "Page 1"
    assert scanned == [1]


def test_real_pdf_renderer_preserves_page_and_bounds_dimensions():
    render = importlib.import_module("app.pdf_render")
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=200)
    writer.add_blank_page(width=800, height=600)
    output = io.BytesIO()
    writer.write(output)
    image = render.render_page(output.getvalue(), 2)
    with Image.open(io.BytesIO(image)) as page:
        assert page.format == "JPEG"
        assert page.width > page.height
        assert page.width <= 2400 and page.height <= 2400
    with pytest.raises(DomainError):
        render.render_page(output.getvalue(), 3)


def test_pdf_renderer_rejects_extreme_page_dimensions():
    render = importlib.import_module("app.pdf_render")
    writer = PdfWriter()
    writer.add_blank_page(width=1000000, height=1)
    output = io.BytesIO()
    writer.write(output)
    with pytest.raises(DomainError):
        render.render_page(output.getvalue(), 1)


def transcribe(monkeypatch, *, text="DEMO-100\n电源开关", finish="stop", content=None):
    ocr = importlib.import_module("app.document_ocr")
    requests, budgets = [], []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": finish,
                        "message": {
                            "content": content
                            if content is not None
                            else json.dumps({"text": text, "unreadable": False})
                        },
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        gateway = ModelGateway(
            client, configuration=Settings(model_provider="dashscope", vision_api_key="test")
        )
        monkeypatch.setattr(gateway, "budget", budgets.append)
        result = ocr.transcribe_page(gateway, b"validated-render", 7, "document-owner")
    return result, requests, budgets


def test_ocr_uses_configured_vision_model_budget_and_literal_page_instruction(monkeypatch):
    result, requests, budgets = transcribe(monkeypatch)
    assert result == "DEMO-100\n电源开关"
    assert budgets == ["document-owner"]
    assert requests[0]["model"] == "qwen3.7-flash"
    content = requests[0]["messages"][-1]["content"]
    assert "7" in content[0]["text"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "不得补全" in requests[0]["messages"][0]["content"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"finish": "length"},
        {"finish": "content_filter"},
        {"content": "not JSON"},
        {"content": '{"text": 123, "unreadable":false}'},
        {"content": '{"text":"guess", "unreadable":true}'},
        {"text": "x" * 40001},
    ],
)
def test_ocr_rejects_truncation_unreadable_or_invalid_output(monkeypatch, kwargs):
    with pytest.raises(DomainError):
        transcribe(monkeypatch, **kwargs)


def test_blank_page_transcription_is_a_valid_completed_result(monkeypatch):
    result, _, _ = transcribe(monkeypatch, text="")
    assert result == ""
