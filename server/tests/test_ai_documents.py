from io import BytesIO

import pytest
from docx import Document

from itime.ai.document_service import MAX_DOCUMENT_CHARS, extract_document
from itime.errors import AppError


def _pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
        + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(output)
    output += f"xref\n0 {len(objects) + 1}\n".encode()
    output += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        output += f"{offset:010d} 00000 n \n".encode()
    output += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(output)


def _docx(text: str) -> bytes:
    output = BytesIO()
    document = Document()
    document.add_paragraph(text)
    document.save(output)
    return output.getvalue()


def test_extracts_pdf_docx_and_text() -> None:
    pdf_name, pdf_text, pdf_truncated = extract_document(
        "notes.pdf", _pdf("Hello from PDF")
    )
    assert pdf_name == "notes.pdf"
    assert "Hello from PDF" in pdf_text
    assert pdf_truncated is False

    _, docx_text, _ = extract_document("notes.docx", _docx("来自 Word 的内容"))
    assert "来自 Word 的内容" in docx_text

    _, text, _ = extract_document("notes.txt", "纯文本内容".encode())
    assert text == "纯文本内容"


def test_truncates_long_text() -> None:
    _, text, truncated = extract_document("long.txt", ("x" * (MAX_DOCUMENT_CHARS + 10)).encode())
    assert len(text) == MAX_DOCUMENT_CHARS
    assert truncated is True


def test_rejects_old_word_and_unsupported_formats() -> None:
    with pytest.raises(AppError) as old_word:
        extract_document("old.doc", b"not a real doc")
    assert "另存为 .docx" in old_word.value.message

    with pytest.raises(AppError) as unsupported:
        extract_document("image.exe", b"MZ")
    assert "暂不支持" in unsupported.value.message


def test_scanned_pdf_without_text_is_reported() -> None:
    # A valid PDF with a blank page and no text operators.
    with pytest.raises(AppError) as error:
        extract_document("scan.pdf", _pdf(""))
    assert error.value.code == "document_unreadable"
