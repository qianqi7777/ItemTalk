"""公开PDF的所有者加密可读；真正需密码的文档与超限仍被隔离解析拒绝。"""

import io

import pytest
from pypdf import PdfWriter

from app.errors import DomainError
from app.parser import parse_document


def pdf_bytes(user_password, owner_password, pages=1):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    # 测试PDF的内容流直接使用固定文字，避免外部字体或完整厂商文档依赖。
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    writer.pages[0][NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 10 50 Td (Official manual) Tj ET")
    writer.pages[0][NameObject("/Contents")] = writer._add_object(stream)
    if user_password is not None:
        writer.encrypt(user_password=user_password, owner_password=owner_password)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_owner_encrypted_public_pdf_reads_without_a_password():
    result = parse_document(pdf_bytes("", "publisher-only"), "application/pdf")
    assert result == [[1, "Official manual"]]


def test_password_protected_pdf_is_still_rejected():
    with pytest.raises(DomainError) as error:
        parse_document(pdf_bytes("required-user-password", "publisher-only"), "application/pdf")
    assert error.value.code == "SOURCE_UNVERIFIED"


def test_readable_encryption_does_not_bypass_page_limit():
    with pytest.raises(DomainError) as error:
        parse_document(pdf_bytes("", "publisher-only", pages=201), "application/pdf")
    assert error.value.code == "SOURCE_UNVERIFIED"
