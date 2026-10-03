import codecs
import re
import unicodedata
from dataclasses import dataclass
from email.message import Message
from html.parser import HTMLParser

MAX_PAGE_TITLE_CHARS = 300
MAX_SUMMARY_CHARS = 2000
MAX_EXTRACTED_TEXT_CHARS = 24000
MAX_COLLECTED_CHARS = 100_000
PUBLIC_TEXT_MEDIA_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "text/plain",
}
IGNORED_TAGS = {
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "iframe",
}
BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "figcaption",
    "figure",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "td",
    "th",
    "tr",
    "ul",
}
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
HIDDEN_STYLE = re.compile(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)")


class PublicLinkParseError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PublicDocument:
    media_type: str
    page_title: str | None
    summary: str
    extracted_text: str


class PublicHTMLCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool, bool, bool]] = []
        self.hidden_depth = 0
        self.head_depth = 0
        self.title_depth = 0
        self.collected_chars = 0
        self.body_parts: list[str] = []
        self.title_parts: list[str] = []
        self.open_graph_title: str | None = None
        self.description: str | None = None
        self.open_graph_description: str | None = None

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        tag = tag.casefold()
        attributes = {
            name.casefold(): value or ""
            for name, value in attrs
        }
        self._read_metadata(tag, attributes)
        hidden = tag in IGNORED_TAGS or _hidden_attributes(attributes)
        head = tag == "head"
        title = tag == "title"
        if tag not in VOID_TAGS:
            self.stack.append((tag, hidden, head, title))
            self.hidden_depth += int(hidden)
            self.head_depth += int(head)
            self.title_depth += int(title)
        if tag in BLOCK_TAGS:
            self._append_body(" ")

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        tag = tag.casefold()
        attributes = {
            name.casefold(): value or ""
            for name, value in attrs
        }
        self._read_metadata(tag, attributes)
        if tag in BLOCK_TAGS:
            self._append_body(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        index = next(
            (
                position
                for position in range(len(self.stack) - 1, -1, -1)
                if self.stack[position][0] == tag
            ),
            None,
        )
        if index is not None:
            for _, hidden, head, title in self.stack[index:]:
                self.hidden_depth -= int(hidden)
                self.head_depth -= int(head)
                self.title_depth -= int(title)
            del self.stack[index:]
        if tag in BLOCK_TAGS:
            self._append_body(" ")

    def handle_data(self, data: str) -> None:
        if self.title_depth and not self.hidden_depth:
            self.title_parts.append(data)
        if not self.hidden_depth and not self.head_depth:
            self._append_body(data)

    def _read_metadata(self, tag: str, attributes: dict[str, str]) -> None:
        if tag != "meta":
            return
        key = (
            attributes.get("property")
            or attributes.get("name")
            or ""
        ).strip().casefold()
        content = attributes.get("content", "")
        if key == "og:title" and content and self.open_graph_title is None:
            self.open_graph_title = content
        elif key == "description" and content and self.description is None:
            self.description = content
        elif (
            key == "og:description"
            and content
            and self.open_graph_description is None
        ):
            self.open_graph_description = content

    def _append_body(self, value: str) -> None:
        if self.hidden_depth or self.head_depth or self.collected_chars >= MAX_COLLECTED_CHARS:
            return
        remaining = MAX_COLLECTED_CHARS - self.collected_chars
        selected = value[:remaining]
        self.body_parts.append(selected)
        self.collected_chars += len(selected)

    def document(self, media_type: str) -> PublicDocument:
        page_title = _bounded_text(
            self.open_graph_title or "".join(self.title_parts),
            MAX_PAGE_TITLE_CHARS,
        )
        description = _clean_text(
            self.description or self.open_graph_description or ""
        )
        body = _clean_text(" ".join(self.body_parts))
        extracted = _bounded_text(
            " ".join(part for part in (description, body) if part),
            MAX_EXTRACTED_TEXT_CHARS,
        )
        if not extracted:
            raise PublicLinkParseError(
                "public_link_content_empty",
                "网页没有可读取的正文。",
            )
        summary_parts = [description] if description else []
        if body and body != description:
            summary_parts.append(body)
        summary = _bounded_text(
            " ".join(summary_parts) or extracted,
            MAX_SUMMARY_CHARS,
        )
        return PublicDocument(
            media_type=media_type,
            page_title=page_title,
            summary=summary,
            extracted_text=extracted,
        )


def parse_public_document(body: bytes, content_type: str) -> PublicDocument:
    media_type, charset = _parse_content_type(content_type)
    if media_type not in PUBLIC_TEXT_MEDIA_TYPES:
        raise PublicLinkParseError(
            "public_link_content_type_invalid",
            "网页返回的内容类型暂不支持。",
        )
    text = _decode_text(body, charset)
    if media_type == "text/plain":
        extracted = _bounded_text(text, MAX_EXTRACTED_TEXT_CHARS)
        if not extracted:
            raise PublicLinkParseError(
                "public_link_content_empty",
                "网页没有可读取的正文。",
            )
        return PublicDocument(
            media_type=media_type,
            page_title=None,
            summary=_bounded_text(extracted, MAX_SUMMARY_CHARS) or "",
            extracted_text=extracted,
        )
    parser = PublicHTMLCollector()
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:
        raise PublicLinkParseError(
            "public_link_content_invalid",
            "网页正文格式无法读取。",
        ) from exc
    return parser.document(media_type)


def _parse_content_type(content_type: str) -> tuple[str, str | None]:
    message = Message()
    message["content-type"] = content_type
    return message.get_content_type().casefold(), message.get_content_charset()


def _decode_text(body: bytes, declared_charset: str | None) -> str:
    candidates = [declared_charset, "utf-8-sig", "gb18030"]
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        normalized = candidate.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        try:
            codecs.lookup(candidate)
            return body.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def _hidden_attributes(attributes: dict[str, str]) -> bool:
    if "hidden" in attributes:
        return True
    if attributes.get("aria-hidden", "").strip().casefold() == "true":
        return True
    return bool(HIDDEN_STYLE.search(attributes.get("style", "").casefold()))


def _clean_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    without_controls = "".join(
        (
            " "
            if character.isspace()
            else character
        )
        for character in normalized
        if not unicodedata.category(character).startswith("C")
        or character.isspace()
    )
    return " ".join(without_controls.split())


def _bounded_text(value: str, limit: int) -> str | None:
    cleaned = _clean_text(value)
    if not cleaned:
        return None
    return cleaned[:limit].rstrip()
