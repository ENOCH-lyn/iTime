import hashlib
from dataclasses import dataclass
from typing import Literal

from itime.calendar.subscription_fetcher import looks_like_complete_calendar
from itime.config import Settings
from itime.network.restricted_http import (
    FetchLimits,
    RestrictedHttpClient,
    RestrictedHttpError,
    safe_url_display,
)
from itime.public_links.parser import (
    PUBLIC_TEXT_MEDIA_TYPES,
    PublicLinkParseError,
    parse_public_document,
)

IMPORT_FETCH_LIMITS = FetchLimits(
    max_redirects=3,
    max_compressed_bytes=2 * 1024 * 1024,
    max_decompressed_bytes=4 * 1024 * 1024,
    max_compression_ratio=20,
    connect_timeout_seconds=3,
    read_timeout_seconds=5,
    total_timeout_seconds=15,
)
CALENDAR_MEDIA_TYPES = {"text/calendar", "application/ics"}

NETWORK_ERROR_MESSAGES = {
    "dns_failed": "无法解析课表网页地址，请稍后重试。",
    "peer_unavailable": "无法核验课表网页连接地址。",
    "peer_mismatch": "课表网页连接地址与已验证地址不一致。",
    "timeout": "课表网页响应超时，请稍后重试。",
    "network_failed": "无法连接课表网页，请稍后重试。",
    "redirect_limit": "课表网页重定向次数超过限制。",
    "redirect_invalid": "课表网页返回了无目标的重定向。",
    "https_downgrade": "课表网页不允许从 HTTPS 降级到 HTTP。",
    "url_invalid": "课表网页地址格式不正确。",
    "https_required": "课表网页地址必须使用 HTTPS。",
    "port_blocked": "课表网页使用了不允许的端口。",
    "ip_literal": "课表网页地址不能直接使用 IP 地址。",
    "http_blocked": "测试环境仅允许通过回环地址使用 HTTP。",
    "address_blocked": "课表网页解析到了不允许访问的网络。",
    "encoding_unsupported": "课表网页使用了不支持的内容编码。",
    "response_too_large": "课表网页响应超过安全体积限制。",
    "compression_ratio": "课表网页响应压缩比超过安全限制。",
    "gzip_invalid": "课表网页必须使用单层完整 gzip 编码。",
}


class CalendarImportFetchError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.http_status = http_status


@dataclass(frozen=True)
class CalendarImportFetchResult:
    kind: Literal["ics", "text"]
    final_url: str
    display_url: str
    display_host: str
    http_status: int
    media_type: str
    content_sha256: str
    redirect_count: int
    page_title: str | None = None
    text: str | None = None
    ics_content: bytes | None = None


class RestrictedCalendarImportFetcher:
    def __init__(
        self,
        settings: Settings,
        *,
        client: RestrictedHttpClient | None = None,
    ) -> None:
        self.settings = settings
        self.client = client or RestrictedHttpClient(settings)

    async def fetch(self, url: str) -> CalendarImportFetchResult:
        try:
            response = await self.client.fetch(
                url,
                headers={
                    "Accept": (
                        "text/calendar, application/ics;q=0.9, text/html;q=0.8, "
                        "application/xhtml+xml;q=0.8, text/plain;q=0.7"
                    ),
                    "Accept-Encoding": "gzip, identity",
                    "User-Agent": "iTime-Calendar-Import/1",
                    "Connection": "close",
                },
                limits=IMPORT_FETCH_LIMITS,
            )
        except RestrictedHttpError as exc:
            raise CalendarImportFetchError(
                f"calendar_import_{exc.reason}",
                NETWORK_ERROR_MESSAGES.get(
                    exc.reason,
                    "课表网页读取失败，请稍后重试。",
                ),
                retryable=exc.retryable,
                http_status=exc.http_status,
            ) from exc

        if response.status in {401, 403}:
            raise CalendarImportFetchError(
                "calendar_import_authentication_required",
                "网页需要登录或授权，iTime 未发送任何用户凭据。",
                retryable=False,
                http_status=response.status,
            )
        if response.status == 429:
            raise CalendarImportFetchError(
                "calendar_import_rate_limited",
                "课表网页暂时限制访问，系统会稍后重试。",
                retryable=True,
                http_status=response.status,
            )
        if response.status >= 500:
            raise CalendarImportFetchError(
                "calendar_import_upstream_unavailable",
                "课表网页暂时不可用，系统会稍后重试。",
                retryable=True,
                http_status=response.status,
            )
        if response.status != 200:
            raise CalendarImportFetchError(
                "calendar_import_http_failed",
                f"课表网页返回 HTTP {response.status}。",
                retryable=False,
                http_status=response.status,
            )

        content_type = response.headers.get("content-type", "")
        media_type = content_type.split(";", 1)[0].strip().casefold()
        if not media_type:
            raise self._content_type_error(response.status)
        disposition = response.headers.get("content-disposition", "")
        is_attachment = (
            disposition.split(";", 1)[0].strip().casefold() == "attachment"
        )
        display_url, display_host = safe_url_display(
            response.final_url,
            self.settings,
        )
        common = {
            "final_url": response.final_url,
            "display_url": display_url,
            "display_host": display_host,
            "http_status": response.status,
            "media_type": media_type,
            "content_sha256": hashlib.sha256(response.body).hexdigest(),
            "redirect_count": response.redirect_count,
        }

        if media_type in CALENDAR_MEDIA_TYPES:
            if not looks_like_complete_calendar(response.body):
                raise CalendarImportFetchError(
                    "calendar_import_calendar_invalid",
                    "网页返回的日历文件不完整。",
                    retryable=False,
                    http_status=response.status,
                )
            return CalendarImportFetchResult(
                kind="ics",
                ics_content=response.body,
                **common,
            )
        if (
            media_type == "text/plain"
            and looks_like_complete_calendar(response.body)
        ):
            return CalendarImportFetchResult(
                kind="ics",
                ics_content=response.body,
                **common,
            )
        if media_type not in PUBLIC_TEXT_MEDIA_TYPES or is_attachment:
            raise self._content_type_error(response.status)
        try:
            document = parse_public_document(response.body, content_type)
        except PublicLinkParseError as exc:
            raise CalendarImportFetchError(
                exc.code.replace("public_link_", "calendar_import_", 1),
                exc.message,
                retryable=False,
                http_status=response.status,
            ) from exc
        return CalendarImportFetchResult(
            kind="text",
            page_title=document.page_title,
            text=document.extracted_text,
            **common,
        )

    @staticmethod
    def _content_type_error(http_status: int) -> CalendarImportFetchError:
        return CalendarImportFetchError(
            "calendar_import_content_type_invalid",
            "网页返回的内容类型暂不支持。",
            retryable=False,
            http_status=http_status,
        )
