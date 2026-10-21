"""Extract text from small documents for AI conversation context."""

import re
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile

from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from itime.errors import AppError

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_DOCUMENT_CHARS = 12_000
MAX_PDF_PAGES = 100
DOCUMENT_SUFFIXES = {".pdf", ".docx", ".txt", ".md", ".markdown", ".csv"}


def _read_error(message: str, status: int = 400) -> AppError:
    return AppError("document_unreadable", message, status=status)


def _decode_text(contents: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return contents.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise _read_error("文件编码无法识别，请使用 UTF-8 或 GB18030 文本。")


def _read_pdf(contents: bytes) -> str:
    if not contents.startswith(b"%PDF-"):
        raise _read_error("这不是有效的 PDF 文件。")
    try:
        reader = PdfReader(BytesIO(contents))
        if reader.is_encrypted:
            raise _read_error("这个 PDF 有密码，无法读取。")
        pages = reader.pages[:MAX_PDF_PAGES]
        text = "\n".join(page.extract_text() or "" for page in pages)
    except AppError:
        raise
    except (PdfReadError, ValueError, OSError):
        raise _read_error("PDF 无法读取，请确认文件没有损坏。") from None
    if not text.strip():
        raise _read_error("这个 PDF 没有可提取的文字；扫描件请先转换为可复制文字的 PDF。")
    return text


def _read_docx(contents: bytes) -> str:
    if not contents.startswith(b"PK\x03\x04"):
        raise _read_error("这不是有效的 DOCX 文件。")
    try:
        document = Document(BytesIO(contents))
        parts = [paragraph.text for paragraph in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                parts.append("\t".join(cell.text.strip() for cell in row.cells))
    except (PackageNotFoundError, BadZipFile, KeyError, ValueError):
        raise _read_error("Word 文件无法读取，请确认文件没有损坏。") from None
    return "\n".join(parts)


def extract_document(filename: str, contents: bytes) -> tuple[str, str, bool]:
    """Return a safe filename, extracted text and whether text was truncated."""
    safe_name = re.sub(r"[\x00-\x1f\x7f]", "", Path(filename or "document").name).strip()
    if not safe_name:
        safe_name = "document"
    safe_name = safe_name[:255]
    suffix = Path(safe_name).suffix.lower()

    if not contents:
        raise _read_error("文件是空的。")
    if len(contents) > MAX_DOCUMENT_BYTES:
        raise _read_error("文件不能超过 10 MB。", status=413)
    if suffix == ".doc":
        raise _read_error("旧版 .doc 请另存为 .docx 后再上传。")
    if suffix not in DOCUMENT_SUFFIXES:
        raise _read_error("暂不支持这个格式，请上传 PDF、DOCX、TXT、MD 或 CSV。")

    if suffix == ".pdf":
        text = _read_pdf(contents)
    elif suffix == ".docx":
        text = _read_docx(contents)
    else:
        text = _decode_text(contents)

    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise _read_error("文件里没有可读取的文字。")
    truncated = len(text) > MAX_DOCUMENT_CHARS
    return safe_name, text[:MAX_DOCUMENT_CHARS], truncated
