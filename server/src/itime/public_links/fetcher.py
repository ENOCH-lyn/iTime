import hashlib
from dataclasses import dataclass
from urllib.parse import urlsplit

from itime.config import Settings
from itime.network.restricted_http import (
    FetchLimits,
    RestrictedHttpClient,
    RestrictedHttpError,
)
from itime.public_links.parser import (
    PUBLIC_TEXT_MEDIA_TYPES,
    PublicDocument,
    PublicLinkParseError,
    parse_public_document,
)

PUBLIC_LINK_LIMITS = FetchLimits(
    max_redirects=3,
    max_compressed_bytes=1024 * 1024,
    max_decompressed_bytes=2 * 1024 * 1024,
    max_compression_ratio=20,
    connect_timeout_seconds=3,
    read_timeout_seconds=5,
    total_timeout_seconds=15,
)

NETWORK_ERROR_MESSAGES = {
    "dns_failed": "无法解析网页服务器地址，请稍后重试。",
    "peer_unavailable": "无法核验网页服务器连接地址。",
    "peer_mismatch": "网页服务器连接地址与已验证地址不一致。",
    "timeout": "网页响应超时，请稍后重试。",
    "network_failed": "无法连接网页服务器，请稍后重试。",
    "redirect_limit": "网页重定向次数超过限制。",
    "redirect_invalid": "网页服务器返回了无目标的重定向。",
    "https_downgrade": "网页地址不允许从 HTTPS 降级到 HTTP。",
    "url_invalid": "网页地址格式不正确。",
    "https_required": "网页地址必须使用 HTTPS。",
    "port_blocked": "网页地址使用了不允许的端口。",
    "ip_literal": "网页地址不能直接使用 IP 地址。",
    "http_blocked": "测试环境仅允许通过回环地址使用 HTTP。",
    "address_blocked": "网页地址解析到了不允许访问的网络。",
    "encoding_unsupported": "网页响应使用了不支持的内容编码。",
    "response_too_large": "网页响应超过安全体积限制。",
    "compression_ratio": "网页响应压缩比超过安全限制。",
    "gzip_invalid": "网页响应必须使用单层完整 gzip 编码。",
}


class PublicLinkFetchError(Exception):
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
class PublicLinkFetchResult:
    final_host: str
    http_status: int
    content_sha256: str
    document: PublicDocument
    redirect_count: int


class RestrictedPublicLinkFetcher:
    def __init__(
        self,
        settings: Settings,
        *,
        client: RestrictedHttpClient | None = None,
    ) -> None:
        self.client = client or RestrictedHttpClient(settings)

    async def fetch(self, url: str) -> PublicLinkFetchResult:
        try:
            response = await self.client.fetch(
                url,
                headers={
                    "Accept": (
                        "text/html, application/xhtml+xml, text/plain;q=0.8"
                    ),
                    "Accept-Encoding": "gzip, identity",
                    "User-Agent": "iTime-Public-Link/1",
                    "Connection": "close",
                },
                limits=PUBLIC_LINK_LIMITS,
            )
        except RestrictedHttpError as exc:
            raise PublicLinkFetchError(
                f"public_link_{exc.reason}",
                NETWORK_ERROR_MESSAGES[exc.reason],
                retryable=exc.retryable,
                http_status=exc.http_status,
            ) from exc

        if response.status in {401, 403}:
            raise PublicLinkFetchError(
                "public_link_authentication_required",
                "网页需要登录或授权，iTime 未发送任何用户凭据。",
                retryable=False,
                http_status=response.status,
            )
        if response.status == 429:
            raise PublicLinkFetchError(
                "public_link_rate_limited",
                "网页服务器暂时限制访问，系统会稍后重试。",
                retryable=True,
                http_status=response.status,
            )
        if response.status >= 500:
            raise PublicLinkFetchError(
                "public_link_upstream_unavailable",
                "网页服务器暂时不可用，系统会稍后重试。",
                retryable=True,
                http_status=response.status,
            )
        if response.status != 200:
            raise PublicLinkFetchError(
                "public_link_http_failed",
                f"网页服务器返回 HTTP {response.status}。",
                retryable=False,
                http_status=response.status,
            )

        disposition = response.headers.get("content-disposition", "")
        if disposition.split(";", 1)[0].strip().casefold() == "attachment":
            raise PublicLinkFetchError(
                "public_link_attachment_blocked",
                "此地址返回的是附件，不是可直接阅读的网页。",
                retryable=False,
                http_status=response.status,
            )
        content_type = response.headers.get("content-type", "")
        media_type = content_type.split(";", 1)[0].strip().casefold()
        if not content_type or media_type not in PUBLIC_TEXT_MEDIA_TYPES:
            raise PublicLinkFetchError(
                "public_link_content_type_invalid",
                "网页返回的内容类型暂不支持。",
                retryable=False,
                http_status=response.status,
            )
        try:
            document = parse_public_document(response.body, content_type)
        except PublicLinkParseError as exc:
            raise PublicLinkFetchError(
                exc.code,
                exc.message,
                retryable=False,
                http_status=response.status,
            ) from exc
        final_host = urlsplit(response.final_url).hostname
        if final_host is None:
            raise PublicLinkFetchError(
                "public_link_url_invalid",
                "网页最终地址格式不正确。",
                retryable=False,
                http_status=response.status,
            )
        return PublicLinkFetchResult(
            final_host=final_host,
            http_status=response.status,
            content_sha256=hashlib.sha256(response.body).hexdigest(),
            document=document,
            redirect_count=response.redirect_count,
        )
