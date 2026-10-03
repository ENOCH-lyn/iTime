import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlunsplit

from itime.config import Settings
from itime.network.restricted_http import (
    AddressResolver,
    AiohttpPinnedTransport,
    FetchLimits,
    ResolvedAddress,
    RestrictedHttpClient,
    RestrictedHttpError,
    RestrictedTransport,
    SystemAddressResolver,
    TransportResponse,
    validate_url,
)

MAX_REDIRECTS = 3
MAX_COMPRESSED_BYTES = 2 * 1024 * 1024
MAX_DECOMPRESSED_BYTES = 4 * 1024 * 1024
MAX_COMPRESSION_RATIO = 20
CONNECT_TIMEOUT_SECONDS = 3
READ_TIMEOUT_SECONDS = 5
TOTAL_TIMEOUT_SECONDS = 15
CALENDAR_MEDIA_TYPES = {"text/calendar", "application/ics"}

CalendarTransport = RestrictedTransport

ERROR_MESSAGES = {
    "dns_failed": "无法解析订阅服务器地址，请稍后重试。",
    "peer_unavailable": "无法核验订阅服务器连接地址。",
    "peer_mismatch": "订阅服务器连接地址与已验证地址不一致。",
    "timeout": "订阅服务器响应超时，请稍后重试。",
    "network_failed": "无法连接订阅服务器，请稍后重试。",
    "redirect_limit": "订阅地址重定向次数超过限制。",
    "redirect_invalid": "订阅服务器返回了无目标的重定向。",
    "https_downgrade": "订阅地址不允许从 HTTPS 降级到 HTTP。",
    "url_invalid": "订阅地址格式不正确。",
    "https_required": "订阅地址必须使用 HTTPS。",
    "port_blocked": "订阅地址使用了不允许的端口。",
    "ip_literal": "订阅地址不能直接使用 IP 地址。",
    "http_blocked": "测试环境仅允许通过回环地址使用 HTTP。",
    "address_blocked": "订阅地址解析到了非公网网络地址。",
    "encoding_unsupported": "订阅响应使用了不支持的内容编码。",
    "response_too_large": "订阅响应超过安全体积限制。",
    "compression_ratio": "订阅响应压缩比超过安全限制。",
    "gzip_invalid": "订阅响应必须使用单层完整 gzip 编码。",
}


class SubscriptionFetchError(RestrictedHttpError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        http_status: int | None = None,
    ) -> None:
        reason = code.removeprefix("calendar_subscription_")
        super().__init__(
            reason,
            retryable=retryable,
            http_status=http_status,
        )
        self.code = code
        self.message = message
        self.args = (message,)


@dataclass(frozen=True)
class FetchValidators:
    final_url: str
    etag: str | None = None
    last_modified: str | None = None


@dataclass(frozen=True)
class CalendarFetchResult:
    status: int
    final_url: str
    etag: str | None
    last_modified: str | None
    content_sha256: str | None
    content: bytes | None
    redirect_count: int


class _CalendarResolver(Protocol):
    async def resolve(self, hostname: str, port: int) -> list[ResolvedAddress]: ...


class RestrictedCalendarFetcher:
    def __init__(
        self,
        settings: Settings,
        *,
        resolver: _CalendarResolver | None = None,
        transport: CalendarTransport | None = None,
    ) -> None:
        self.settings = settings
        self.resolver = resolver
        self.transport = transport

    async def fetch(
        self,
        url: str,
        *,
        validators: FetchValidators | None = None,
    ) -> CalendarFetchResult:
        limits = FetchLimits(
            max_redirects=MAX_REDIRECTS,
            max_compressed_bytes=MAX_COMPRESSED_BYTES,
            max_decompressed_bytes=MAX_DECOMPRESSED_BYTES,
            max_compression_ratio=MAX_COMPRESSION_RATIO,
            connect_timeout_seconds=CONNECT_TIMEOUT_SECONDS,
            read_timeout_seconds=READ_TIMEOUT_SECONDS,
            total_timeout_seconds=TOTAL_TIMEOUT_SECONDS,
        )

        def request_headers(current_url: str) -> Mapping[str, str]:
            headers = {
                "Accept": "text/calendar, application/ics;q=0.9, text/plain;q=0.5",
                "Accept-Encoding": "gzip, identity",
                "User-Agent": "iTime-Calendar-Subscription/1",
                "Connection": "close",
            }
            if validators is not None and current_url == validators.final_url:
                if validators.etag:
                    headers["If-None-Match"] = validators.etag
                if validators.last_modified:
                    headers["If-Modified-Since"] = validators.last_modified
            return headers

        try:
            response = await RestrictedHttpClient(
                self.settings,
                resolver=self.resolver,
                transport=self.transport,
            ).fetch(
                url,
                headers=request_headers,
                limits=limits,
            )
        except SubscriptionFetchError:
            raise
        except RestrictedHttpError as exc:
            raise _calendar_error(exc) from exc

        validators_sent = (
            validators is not None
            and response.final_url == validators.final_url
            and bool(validators.etag or validators.last_modified)
        )
        if response.status == 304:
            if not validators_sent:
                raise SubscriptionFetchError(
                    "calendar_subscription_unexpected_not_modified",
                    "订阅服务器返回了无效的未修改响应。",
                    retryable=True,
                    http_status=304,
                )
            return CalendarFetchResult(
                status=304,
                final_url=response.final_url,
                etag=validators.etag if validators is not None else None,
                last_modified=(
                    validators.last_modified if validators is not None else None
                ),
                content_sha256=None,
                content=None,
                redirect_count=response.redirect_count,
            )
        if response.status != 200:
            raise SubscriptionFetchError(
                "calendar_subscription_http_failed",
                f"订阅服务器返回 HTTP {response.status}。",
                retryable=response.status == 429 or response.status >= 500,
                http_status=response.status,
            )
        _validate_media_type(response.headers, response.body)
        return CalendarFetchResult(
            status=200,
            final_url=response.final_url,
            etag=_optional_header(response.headers, "etag"),
            last_modified=_optional_header(response.headers, "last-modified"),
            content_sha256=hashlib.sha256(response.body).hexdigest(),
            content=response.body,
            redirect_count=response.redirect_count,
        )


def subscription_url_metadata(url: str, settings: Settings) -> tuple[str, str]:
    try:
        parsed = validate_url(url, settings)
    except RestrictedHttpError as exc:
        raise _calendar_error(exc) from exc
    hostname = parsed.hostname or ""
    default_port = 443 if parsed.scheme == "https" else 80
    display_host = (
        hostname
        if parsed.port in {None, default_port}
        else f"{hostname}:{parsed.port}"
    )
    display_path = "/" if parsed.path in {"", "/"} else "/..."
    display_url = urlunsplit(
        (
            parsed.scheme,
            display_host,
            display_path,
            "",
            "",
        )
    )
    return display_url, display_host


def _calendar_error(error: RestrictedHttpError) -> SubscriptionFetchError:
    return SubscriptionFetchError(
        f"calendar_subscription_{error.reason}",
        ERROR_MESSAGES[error.reason],
        retryable=error.retryable,
        http_status=error.http_status,
    )


def _optional_header(headers: Mapping[str, str], name: str) -> str | None:
    value = headers.get(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped[:1000] if stripped else None


def _validate_media_type(headers: Mapping[str, str], content: bytes) -> None:
    content_type = headers.get("content-type", "").split(";", 1)[0].strip().casefold()
    if content_type in CALENDAR_MEDIA_TYPES:
        return
    if content_type == "text/plain" and _looks_like_complete_calendar(content):
        return
    raise SubscriptionFetchError(
        "calendar_subscription_content_type_invalid",
        "订阅服务器未返回可识别的日历内容。",
        retryable=False,
    )


def _looks_like_complete_calendar(content: bytes) -> bool:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return bool(lines) and lines[0] == "BEGIN:VCALENDAR" and lines[-1] == "END:VCALENDAR"


__all__ = [
    "AiohttpPinnedTransport",
    "AddressResolver",
    "CalendarFetchResult",
    "CalendarTransport",
    "FetchValidators",
    "ResolvedAddress",
    "RestrictedCalendarFetcher",
    "SubscriptionFetchError",
    "SystemAddressResolver",
    "TransportResponse",
    "subscription_url_metadata",
]
