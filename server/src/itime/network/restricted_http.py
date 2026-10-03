import asyncio
import ipaddress
import socket
import ssl
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import SplitResult, urljoin, urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver

from itime.config import Settings

REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class RestrictedHttpError(Exception):
    def __init__(
        self,
        reason: str,
        *,
        retryable: bool,
        http_status: int | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable
        self.http_status = http_status


@dataclass(frozen=True)
class FetchLimits:
    max_redirects: int
    max_compressed_bytes: int
    max_decompressed_bytes: int
    max_compression_ratio: int
    connect_timeout_seconds: float
    read_timeout_seconds: float
    total_timeout_seconds: float


@dataclass(frozen=True)
class ResolvedAddress:
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address

    @property
    def family(self) -> socket.AddressFamily:
        return socket.AF_INET6 if self.ip.version == 6 else socket.AF_INET


@dataclass(frozen=True)
class TransportResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
    peer_ip: ipaddress.IPv4Address | ipaddress.IPv6Address
    body_decoded: bool = False


@dataclass(frozen=True)
class RestrictedHttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
    final_url: str
    peer_ip: ipaddress.IPv4Address | ipaddress.IPv6Address
    redirect_count: int


class AddressResolver(Protocol):
    async def resolve(self, hostname: str, port: int) -> list[ResolvedAddress]: ...


class RestrictedTransport(Protocol):
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
            raise RestrictedHttpError("dns_failed", retryable=True) from exc
        addresses: list[ResolvedAddress] = []
        seen: set[str] = set()
        for row in rows:
            try:
                parsed = normalize_ip(ipaddress.ip_address(row[4][0]))
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
                normalize_ip(ipaddress.ip_address(peername[0]))
                if isinstance(peername, tuple) and peername
                else None
            )
        except ValueError:
            peer_ip = None
        if peer_ip is None:
            transport.close()
            raise RestrictedHttpError("peer_unavailable", retryable=True)
        if peer_ip != self.expected_address.ip:
            transport.close()
            raise RestrictedHttpError("peer_mismatch", retryable=False)
        self.verified_peer = peer_ip
        return transport, protocol


class AiohttpPinnedTransport:
    def __init__(self, limits: FetchLimits) -> None:
        self.limits = limits

    async def get(
        self,
        url: str,
        address: ResolvedAddress,
        headers: Mapping[str, str],
    ) -> TransportResponse:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        if hostname is None:
            raise RestrictedHttpError("url_invalid", retryable=False)
        timeout = aiohttp.ClientTimeout(
            total=self.limits.total_timeout_seconds,
            connect=self.limits.connect_timeout_seconds,
            sock_connect=self.limits.connect_timeout_seconds,
            sock_read=self.limits.read_timeout_seconds,
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
                        raise RestrictedHttpError(
                            "peer_unavailable",
                            retryable=True,
                        )
                    decoder = LimitedBodyDecoder(
                        response.headers.get("Content-Encoding", "identity"),
                        limits=self.limits,
                        http_status=response.status,
                    )
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        decoder.feed(chunk)
                    response_headers: dict[str, str] = {}
                    for key in response.headers:
                        values = response.headers.getall(key, [])
                        response_headers[key.casefold()] = ",".join(values)
                    return TransportResponse(
                        status=response.status,
                        headers=response_headers,
                        body=decoder.finish(),
                        peer_ip=peer_ip,
                        body_decoded=True,
                    )
        except RestrictedHttpError:
            raise
        except TimeoutError as exc:
            raise RestrictedHttpError("timeout", retryable=True) from exc
        except (aiohttp.ClientError, OSError) as exc:
            raise RestrictedHttpError("network_failed", retryable=True) from exc


HeaderFactory = Mapping[str, str] | Callable[[str], Mapping[str, str]]


class RestrictedHttpClient:
    def __init__(
        self,
        settings: Settings,
        *,
        resolver: AddressResolver | None = None,
        transport: RestrictedTransport | None = None,
    ) -> None:
        self.settings = settings
        self.resolver = resolver or SystemAddressResolver()
        self.transport = transport

    async def fetch(
        self,
        url: str,
        *,
        headers: HeaderFactory,
        limits: FetchLimits,
    ) -> RestrictedHttpResponse:
        try:
            async with asyncio.timeout(limits.total_timeout_seconds):
                return await self._fetch_redirects(
                    url,
                    headers=headers,
                    limits=limits,
                )
        except TimeoutError as exc:
            raise RestrictedHttpError("timeout", retryable=True) from exc

    async def _fetch_redirects(
        self,
        url: str,
        *,
        headers: HeaderFactory,
        limits: FetchLimits,
    ) -> RestrictedHttpResponse:
        current_url = url
        transport = self.transport or AiohttpPinnedTransport(limits)
        for redirect_count in range(limits.max_redirects + 1):
            parsed = validate_url(current_url, self.settings)
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            addresses = await self.resolver.resolve(parsed.hostname or "", port)
            validate_addresses(addresses, parsed.scheme, self.settings)
            request_headers = (
                dict(headers(current_url))
                if callable(headers)
                else dict(headers)
            )
            response: TransportResponse | None = None
            last_connection_error: RestrictedHttpError | None = None
            for address in addresses:
                try:
                    response = await transport.get(
                        current_url,
                        address,
                        request_headers,
                    )
                except RestrictedHttpError as exc:
                    if not exc.retryable:
                        raise
                    last_connection_error = exc
                    continue
                if normalize_ip(response.peer_ip) != address.ip:
                    raise RestrictedHttpError("peer_mismatch", retryable=False)
                break
            if response is None:
                if last_connection_error is not None:
                    raise last_connection_error
                raise RestrictedHttpError("network_failed", retryable=True)
            response_headers = {
                key.casefold(): value for key, value in response.headers.items()
            }
            body = (
                response.body
                if response.body_decoded
                else decode_body(
                    response.body,
                    response_headers.get("content-encoding", "identity"),
                    limits=limits,
                    http_status=response.status,
                )
            )
            if response.status in REDIRECT_STATUSES:
                if redirect_count == limits.max_redirects:
                    raise RestrictedHttpError(
                        "redirect_limit",
                        retryable=False,
                        http_status=response.status,
                    )
                location = response_headers.get("location")
                if not location:
                    raise RestrictedHttpError(
                        "redirect_invalid",
                        retryable=False,
                        http_status=response.status,
                    )
                next_url = urljoin(current_url, location)
                next_parsed = validate_url(next_url, self.settings)
                if parsed.scheme == "https" and next_parsed.scheme != "https":
                    raise RestrictedHttpError(
                        "https_downgrade",
                        retryable=False,
                        http_status=response.status,
                    )
                current_url = next_url
                continue
            return RestrictedHttpResponse(
                status=response.status,
                headers=response_headers,
                body=body,
                final_url=current_url,
                peer_ip=normalize_ip(response.peer_ip),
                redirect_count=redirect_count,
            )
        raise AssertionError("redirect loop must terminate")


def validate_url(url: str, settings: Settings) -> SplitResult:
    if url != url.strip() or any(
        ord(character) < 32 or ord(character) == 127 for character in url
    ):
        raise RestrictedHttpError("url_invalid", retryable=False)
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise RestrictedHttpError("url_invalid", retryable=False) from exc
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise RestrictedHttpError("url_invalid", retryable=False)
    if settings.environment != "test" and parsed.scheme != "https":
        raise RestrictedHttpError("https_required", retryable=False)
    if settings.environment != "test" and port not in {None, 443}:
        raise RestrictedHttpError("port_blocked", retryable=False)
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        pass
    else:
        raise RestrictedHttpError("ip_literal", retryable=False)
    return parsed


def normalize_ip(
    value: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    if isinstance(value, ipaddress.IPv6Address) and value.ipv4_mapped is not None:
        return value.ipv4_mapped
    return value


def validate_addresses(
    addresses: list[ResolvedAddress],
    scheme: str,
    settings: Settings,
) -> None:
    if not addresses:
        raise RestrictedHttpError("dns_failed", retryable=True)
    normalized = [normalize_ip(address.ip) for address in addresses]
    loopback_test = settings.environment == "test" and scheme == "http" and all(
        address.is_loopback for address in normalized
    )
    if scheme == "http" and not loopback_test:
        raise RestrictedHttpError("http_blocked", retryable=False)
    if not loopback_test and any(not is_public_address(address) for address in normalized):
        raise RestrictedHttpError("address_blocked", retryable=False)


def is_public_address(
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


def decode_body(
    body: bytes,
    content_encoding: str,
    *,
    limits: FetchLimits,
    http_status: int,
) -> bytes:
    decoder = LimitedBodyDecoder(
        content_encoding,
        limits=limits,
        http_status=http_status,
    )
    decoder.feed(body)
    return decoder.finish()


class LimitedBodyDecoder:
    def __init__(
        self,
        content_encoding: str,
        *,
        limits: FetchLimits,
        http_status: int,
    ) -> None:
        self.limits = limits
        self.http_status = http_status
        self.encoding = content_encoding.strip().casefold()
        if self.encoding not in {"", "identity", "gzip"}:
            raise RestrictedHttpError(
                "encoding_unsupported",
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
        if self.compressed_bytes > self.limits.max_compressed_bytes:
            raise RestrictedHttpError(
                "response_too_large",
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
                        self.limits.max_decompressed_bytes + 1 - len(self.output),
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
                if len(self.output) <= self.limits.max_decompressed_bytes:
                    self.output.extend(
                        self.decompressor.flush(
                            self.limits.max_decompressed_bytes + 1 - len(self.output)
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
        if len(self.output) > self.limits.max_decompressed_bytes:
            raise self._too_large()
        if (
            final
            and self.decompressor is not None
            and self.compressed_bytes
            and len(self.output)
            > self.compressed_bytes * self.limits.max_compression_ratio
        ):
            raise RestrictedHttpError(
                "compression_ratio",
                retryable=False,
                http_status=self.http_status,
            )

    def _too_large(self) -> RestrictedHttpError:
        return RestrictedHttpError(
            "response_too_large",
            retryable=False,
            http_status=self.http_status,
        )

    def _invalid_gzip(self) -> RestrictedHttpError:
        return RestrictedHttpError(
            "gzip_invalid",
            retryable=False,
            http_status=self.http_status,
        )
