"""V2三类官方资料需要支持260页手册，但仍拒绝超出有界解析容量的文件。"""

import io

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.errors import DomainError
from app.parser import parse_document


def blank_pdf(pages):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    writer.pages[0][NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 10 Tf 5 20 Td (V2 parser fixture) Tj ET")
    writer.pages[0][NameObject("/Contents")] = writer._add_object(content)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def test_official_camera_manual_page_count_is_supported():
    result = parse_document(blank_pdf(260), "application/pdf")
    assert len(result) == 260 and result[-1][0] == 260


def test_documents_exceeding_page_budget_are_rejected():
    with pytest.raises(DomainError):
        parse_document(blank_pdf(401), "application/pdf")
