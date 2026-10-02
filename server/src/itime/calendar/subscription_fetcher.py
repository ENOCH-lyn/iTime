import asyncio
import hashlib
import ipaddress
import socket
import ssl
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit, urlunsplit

import aiohttp
from aiohttp.abc import AbstractResolver

from itime.config import Settings

MAX_REDIRECTS = 3
MAX_COMPRESSED_BYTES = 2 * 1024 * 1024
MAX_DECOMPRESSED_BYTES = 4 * 1024 * 1024
MAX_COMPRESSION_RATIO = 20
CONNECT_TIMEOUT_SECONDS = 3
READ_TIMEOUT_SECONDS = 5
TOTAL_TIMEOUT_SECONDS = 15
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
CALENDAR_MEDIA_TYPES = {"text/calendar", "application/ics"}


class SubscriptionFetchError(Exception):
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
class ResolvedAddress:
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address

    @property
    def family(self) -> socket.AddressFamily:
        return socket.AF_INET6 if self.ip.version == 6 else socket.AF_INET


@dataclass(frozen=True)
class FetchValidators:
    final_url: str
    etag: str | None = None
    last_modified: str | None = None


@dataclass(frozen=True)
class TransportResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
    peer_ip: ipaddress.IPv4Address | ipaddress.IPv6Address
    body_decoded: bool = False


@dataclass(frozen=True)
class CalendarFetchResult:
    status: int
    final_url: str
    etag: str | None
    last_modified: str | None
    content_sha256: str | None
    content: bytes | None
    redirect_count: int


class AddressResolver(Protocol):
    async def resolve(self, hostname: str, port: int) -> list[ResolvedAddress]: ...


class CalendarTransport(Protocol):
    async def get(
        self,
        url: str,
        address: ResolvedAddress,
        headers: Mapping[str, str],
    ) -> TransportResponse: ...


class SystemAddressResolver:
    async def resolve(self, hostname: str, port: int) -> list[ResolvedAddress]:
        try:
            rows = await asyncio.to_thread(
                socket.getaddrinfo,
                hostname,
                port,
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise SubscriptionFetchError(
                "calendar_subscription_dns_failed",
                "无法解析订阅服务器地址，请稍后重试。",
                retryable=True,
            ) from exc
        addresses: list[ResolvedAddress] = []
        seen: set[str] = set()
        for row in rows:
            try:
                parsed = _normalize_ip(ipaddress.ip_address(row[4][0]))
            except ValueError:
                continue
            if str(parsed) not in seen:
                addresses.append(ResolvedAddress(parsed))
                seen.add(str(parsed))
        return addresses


class _PinnedResolver(AbstractResolver):
    def __init__(self, hostname: str, address: ResolvedAddress) -> None:
        self.hostname = hostname
        self.address = address

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_UNSPEC,
    ) -> list[dict[str, object]]:
        if host.rstrip(".").casefold() != self.hostname.rstrip(".").casefold():
            raise OSError("unexpected resolver hostname")
        return [
            {
                "hostname": host,
                "host": str(self.address.ip),
                "port": port,
                "family": self.address.family,
                "proto": socket.IPPROTO_TCP,
                "flags": socket.AI_NUMERICHOST,
            }
        ]

    async def close(self) -> None:
        return None


class _PeerVerifyingConnector(aiohttp.TCPConnector):
    def __init__(self, expected_address: ResolvedAddress, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.expected_address = expected_address
        self.verified_peer: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None

    async def _wrap_create_connection(self, *args: Any, **kwargs: Any):
        transport, protocol = await super()._wrap_create_connection(*args, **kwargs)
        peername = transport.get_extra_info("peername")
        try:
            peer_ip = (
                _normalize_ip(ipaddress.ip_address(peername[0]))
                if isinstance(peername, tuple) and peername
                else None
            )
        except ValueError:
            peer_ip = None
        if peer_ip is None:
            transport.close()
            raise SubscriptionFetchError(
                "calendar_subscription_peer_unavailable",
                "无法核验订阅服务器连接地址。",
                retryable=True,
            )
        if peer_ip != self.expected_address.ip:
            transport.close()
            raise SubscriptionFetchError(
                "calendar_subscription_peer_mismatch",
                "订阅服务器连接地址与已验证地址不一致。",
                retryable=False,
            )
        self.verified_peer = peer_ip
        return transport, protocol


class AiohttpPinnedTransport:
    async def get(
        self,
        url: str,
        address: ResolvedAddress,
        headers: Mapping[str, str],
    ) -> TransportResponse:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        if hostname is None:
            raise SubscriptionFetchError(
                "calendar_subscription_url_invalid",
                "订阅地址格式不正确。",
                retryable=False,
            )
        timeout = aiohttp.ClientTimeout(
            total=TOTAL_TIMEOUT_SECONDS,
            connect=CONNECT_TIMEOUT_SECONDS,
            sock_connect=CONNECT_TIMEOUT_SECONDS,
            sock_read=READ_TIMEOUT_SECONDS,
        )
        resolver = _PinnedResolver(hostname, address)
        connector = _PeerVerifyingConnector(
            address,
            resolver=resolver,
            use_dns_cache=False,
            force_close=True,
            family=address.family,
            ssl=ssl.create_default_context(),
            limit=1,
        )
        try:
            async with aiohttp.ClientSession(
                connector=connector,
                connector_owner=True,
                timeout=timeout,
                auto_decompress=False,
                trust_env=False,
                cookie_jar=aiohttp.DummyCookieJar(),
            ) as session:
                async with session.get(
                    url,
                    headers=headers,
                    allow_redirects=False,
                ) as response:
                    peer_ip = connector.verified_peer
                    if peer_ip is None:
                        raise SubscriptionFetchError(
                            "calendar_subscription_peer_unavailable",
                            "无法核验订阅服务器连接地址。",
                            retryable=True,
                        )
                    body_decoder = _LimitedBodyDecoder(
                        response.headers.get("Content-Encoding", "identity"),
                        http_status=response.status,
                    )
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        body_decoder.feed(chunk)
                    response_headers: dict[str, str] = {}
                    for key in response.headers:
                        values = response.headers.getall(key, [])
                        response_headers[key.casefold()] = ",".join(values)
                    return TransportResponse(
                        status=response.status,
                        headers=response_headers,
                        body=body_decoder.finish(),
                        peer_ip=peer_ip,
                        body_decoded=True,
                    )
        except SubscriptionFetchError:
            raise
        except TimeoutError as exc:
            raise SubscriptionFetchError(
                "calendar_subscription_timeout",
                "订阅服务器响应超时，请稍后重试。",
                retryable=True,
            ) from exc
        except (aiohttp.ClientError, OSError) as exc:
            raise SubscriptionFetchError(
                "calendar_subscription_network_failed",
                "无法连接订阅服务器，请稍后重试。",
                retryable=True,
            ) from exc


class RestrictedCalendarFetcher:
    def __init__(
        self,
        settings: Settings,
        *,
        resolver: AddressResolver | None = None,
        transport: CalendarTransport | None = None,
    ) -> None:
        self.settings = settings
        self.resolver = resolver or SystemAddressResolver()
        self.transport = transport or AiohttpPinnedTransport()

    async def fetch(
        self,
        url: str,
        *,
        validators: FetchValidators | None = None,
    ) -> CalendarFetchResult:
        try:
            async with asyncio.timeout(TOTAL_TIMEOUT_SECONDS):
                return await self._fetch_redirects(url, validators=validators)
        except TimeoutError as exc:
            raise SubscriptionFetchError(
                "calendar_subscription_timeout",
                "订阅服务器响应超时，请稍后重试。",
                retryable=True,
            ) from exc

    async def _fetch_redirects(
        self,
        url: str,
        *,
        validators: FetchValidators | None,
    ) -> CalendarFetchResult:
        current_url = url
        for redirect_count in range(MAX_REDIRECTS + 1):
            parsed = _validate_url(current_url, self.settings)
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            addresses = await self.resolver.resolve(parsed.hostname or "", port)
            _validate_addresses(addresses, parsed.scheme, self.settings)
            headers = {
                "Accept": "text/calendar, application/ics;q=0.9, text/plain;q=0.5",
                "Accept-Encoding": "gzip, identity",
                "User-Agent": "iTime-Calendar-Subscription/1",
                "Connection": "close",
            }
            validators_match = validators is not None and current_url == validators.final_url
            validators_sent = False
            if validators_match and validators is not None:
                if validators.etag:
                    headers["If-None-Match"] = validators.etag
                    validators_sent = True
                if validators.last_modified:
                    headers["If-Modified-Since"] = validators.last_modified
                    validators_sent = True
            response: TransportResponse | None = None
            last_connection_error: SubscriptionFetchError | None = None
            for address in addresses:
                try:
                    response = await self.transport.get(current_url, address, headers)
                except SubscriptionFetchError as exc:
                    if not exc.retryable:
                        raise
                    last_connection_error = exc
                    continue
                if _normalize_ip(response.peer_ip) != address.ip:
                    raise SubscriptionFetchError(
                        "calendar_subscription_peer_mismatch",
                        "订阅服务器连接地址与已验证地址不一致。",
                        retryable=False,
                    )
                break
            if response is None:
                if last_connection_error is not None:
                    raise last_connection_error
                raise SubscriptionFetchError(
                    "calendar_subscription_network_failed",
                    "无法连接订阅服务器，请稍后重试。",
                    retryable=True,
                )
            if response.status in REDIRECT_STATUSES:
                if redirect_count == MAX_REDIRECTS:
                    raise SubscriptionFetchError(
                        "calendar_subscription_redirect_limit",
                        "订阅地址重定向次数超过限制。",
                        retryable=False,
                        http_status=response.status,
                    )
                location = response.headers.get("location")
                if not location:
                    raise SubscriptionFetchError(
                        "calendar_subscription_redirect_invalid",
                        "订阅服务器返回了无目标的重定向。",
                        retryable=False,
                        http_status=response.status,
                    )
                next_url = urljoin(current_url, location)
                next_parsed = _validate_url(next_url, self.settings)
                if parsed.scheme == "https" and next_parsed.scheme != "https":
                    raise SubscriptionFetchError(
                        "calendar_subscription_https_downgrade",
                        "订阅地址不允许从 HTTPS 降级到 HTTP。",
                        retryable=False,
                        http_status=response.status,
                    )
                current_url = next_url
                continue
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
                    final_url=current_url,
                    etag=validators.etag if validators is not None else None,
                    last_modified=(
                        validators.last_modified if validators is not None else None
                    ),
                    content_sha256=None,
                    content=None,
                    redirect_count=redirect_count,
                )
            if response.status != 200:
                raise SubscriptionFetchError(
                    "calendar_subscription_http_failed",
                    f"订阅服务器返回 HTTP {response.status}。",
                    retryable=response.status == 429 or response.status >= 500,
                    http_status=response.status,
                )
            content = _decode_body(response)
            _validate_media_type(response.headers, content)
            return CalendarFetchResult(
                status=200,
                final_url=current_url,
                etag=_optional_header(response.headers, "etag"),
                last_modified=_optional_header(response.headers, "last-modified"),
                content_sha256=hashlib.sha256(content).hexdigest(),
                content=content,
                redirect_count=redirect_count,
            )
        raise AssertionError("redirect loop must terminate")


def subscription_url_metadata(url: str, settings: Settings) -> tuple[str, str]:
    parsed = _validate_url(url, settings)
    hostname = parsed.hostname or ""
    default_port = 443 if parsed.scheme == "https" else 80
    display_host = hostname if parsed.port in {None, default_port} else f"{hostname}:{parsed.port}"
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


def _validate_url(url: str, settings: Settings):
    if url != url.strip() or any(ord(character) < 32 or ord(character) == 127 for character in url):
        raise SubscriptionFetchError(
            "calendar_subscription_url_invalid",
            "订阅地址包含无效字符。",
            retryable=False,
        )
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise SubscriptionFetchError(
            "calendar_subscription_url_invalid",
            "订阅地址格式不正确。",
            retryable=False,
        ) from exc
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise SubscriptionFetchError(
            "calendar_subscription_url_invalid",
            "订阅地址必须是无凭据、无片段的完整 HTTPS URL。",
            retryable=False,
        )
    if settings.environment != "test" and parsed.scheme != "https":
        raise SubscriptionFetchError(
            "calendar_subscription_https_required",
            "订阅地址必须使用 HTTPS。",
            retryable=False,
        )
    if settings.environment != "test" and port not in {None, 443}:
        raise SubscriptionFetchError(
            "calendar_subscription_port_blocked",
            "订阅地址使用了不允许的端口。",
            retryable=False,
        )
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        pass
    else:
        raise SubscriptionFetchError(
            "calendar_subscription_ip_literal",
            "订阅地址不能直接使用 IP 地址。",
            retryable=False,
        )
    return parsed


def _normalize_ip(
    value: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    if isinstance(value, ipaddress.IPv6Address) and value.ipv4_mapped is not None:
        return value.ipv4_mapped
    return value


def _validate_addresses(
    addresses: list[ResolvedAddress],
    scheme: str,
    settings: Settings,
) -> None:
    if not addresses:
        raise SubscriptionFetchError(
            "calendar_subscription_dns_failed",
            "订阅服务器没有可连接的地址。",
            retryable=True,
        )
    normalized = [_normalize_ip(address.ip) for address in addresses]
    loopback_test = settings.environment == "test" and scheme == "http" and all(
        address.is_loopback for address in normalized
    )
    if scheme == "http" and not loopback_test:
        raise SubscriptionFetchError(
            "calendar_subscription_http_blocked",
            "测试环境仅允许通过回环地址使用 HTTP。",
            retryable=False,
        )
    if not loopback_test and any(not _is_public_address(address) for address in normalized):
        raise SubscriptionFetchError(
            "calendar_subscription_address_blocked",
            "订阅地址解析到了非公网网络地址。",
            retryable=False,
        )


def _is_public_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    if not address.is_global:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        nat64_prefix = ipaddress.ip_network("64:ff9b::/96")
        if address in nat64_prefix:
            embedded = ipaddress.IPv4Address(address.packed[-4:])
            return embedded.is_global
        if address.sixtofour is not None:
            return address.sixtofour.is_global
        if address.teredo is not None:
            server, client = address.teredo
            return server.is_global and client.is_global
    return True


def _optional_header(headers: Mapping[str, str], name: str) -> str | None:
    value = headers.get(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped[:1000] if stripped else None


def _decode_body(response: TransportResponse) -> bytes:
    if response.body_decoded:
        return response.body
    decoder = _LimitedBodyDecoder(
        response.headers.get("content-encoding", "identity"),
        http_status=response.status,
    )
    decoder.feed(response.body)
    return decoder.finish()


class _LimitedBodyDecoder:
    def __init__(self, content_encoding: str, *, http_status: int) -> None:
        self.http_status = http_status
        self.encoding = content_encoding.strip().casefold()
        if self.encoding not in {"", "identity", "gzip"}:
            raise SubscriptionFetchError(
                "calendar_subscription_encoding_unsupported",
                "订阅响应使用了不支持的内容编码。",
                retryable=False,
                http_status=http_status,
            )
        self.compressed_bytes = 0
        self.output = bytearray()
        self.decompressor = (
            zlib.decompressobj(16 + zlib.MAX_WBITS)
            if self.encoding == "gzip"
            else None
        )

    def feed(self, chunk: bytes) -> None:
        self.compressed_bytes += len(chunk)
        if self.compressed_bytes > MAX_COMPRESSED_BYTES:
            raise SubscriptionFetchError(
                "calendar_subscription_response_too_large",
                "订阅响应超过 2 MiB 压缩体限制。",
                retryable=False,
                http_status=self.http_status,
            )
        if self.decompressor is None:
            self.output.extend(chunk)
        else:
            try:
                self.output.extend(
                    self.decompressor.decompress(
                        chunk,
                        MAX_DECOMPRESSED_BYTES + 1 - len(self.output),
                    )
                )
            except zlib.error as exc:
                raise self._invalid_gzip() from exc
            if self.decompressor.unconsumed_tail:
                raise self._too_large()
        self._check_limits()

    def finish(self) -> bytes:
        if self.decompressor is not None:
            try:
                if len(self.output) <= MAX_DECOMPRESSED_BYTES:
                    self.output.extend(
                        self.decompressor.flush(
                            MAX_DECOMPRESSED_BYTES + 1 - len(self.output)
                        )
                    )
            except zlib.error as exc:
                raise self._invalid_gzip() from exc
            if (
                not self.decompressor.eof
                or self.decompressor.unused_data
                or self.decompressor.unconsumed_tail
            ):
                raise self._invalid_gzip()
        self._check_limits(final=True)
        return bytes(self.output)

    def _check_limits(self, *, final: bool = False) -> None:
        if len(self.output) > MAX_DECOMPRESSED_BYTES:
            raise self._too_large()
        if (
            final
            and self.decompressor is not None
            and self.compressed_bytes
            and len(self.output) > self.compressed_bytes * MAX_COMPRESSION_RATIO
        ):
            raise SubscriptionFetchError(
                "calendar_subscription_compression_ratio",
                "订阅响应压缩比超过安全限制。",
                retryable=False,
                http_status=self.http_status,
            )

    def _too_large(self) -> SubscriptionFetchError:
        return SubscriptionFetchError(
            "calendar_subscription_response_too_large",
            "订阅响应超过 4 MiB 解压体限制。",
            retryable=False,
            http_status=self.http_status,
        )

    def _invalid_gzip(self) -> SubscriptionFetchError:
        return SubscriptionFetchError(
            "calendar_subscription_gzip_invalid",
            "订阅响应必须使用单层完整 gzip 编码。",
            retryable=False,
            http_status=self.http_status,
        )


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
