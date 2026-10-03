import asyncio
import gzip
import ipaddress
from collections.abc import Mapping
from dataclasses import dataclass, field

import pytest
from cryptography.fernet import Fernet

from itime.config import Settings
from itime.network.restricted_http import (
    FetchLimits,
    ResolvedAddress,
    RestrictedHttpClient,
    RestrictedHttpError,
    TransportResponse,
)

PUBLIC_V4 = ResolvedAddress(ipaddress.ip_address("93.184.216.34"))
PUBLIC_V6 = ResolvedAddress(ipaddress.ip_address("2606:2800:220:1:248:1893:25c8:1946"))
LOOPBACK = ResolvedAddress(ipaddress.ip_address("127.0.0.1"))
LIMITS = FetchLimits(
    max_redirects=3,
    max_compressed_bytes=1024,
    max_decompressed_bytes=2048,
    max_compression_ratio=20,
    connect_timeout_seconds=3,
    read_timeout_seconds=5,
    total_timeout_seconds=15,
)


def make_settings(environment: str = "test") -> Settings:
    return Settings(
        environment=environment,
        database_url="postgresql+psycopg://test:test@127.0.0.1/test",
        encryption_key=Fernet.generate_key().decode(),
    )


@dataclass
class FakeResolver:
    addresses: dict[str, list[ResolvedAddress]]
    calls: list[tuple[str, int]] = field(default_factory=list)

    async def resolve(self, hostname: str, port: int) -> list[ResolvedAddress]:
        self.calls.append((hostname, port))
        return self.addresses.get(hostname, [])


@dataclass
class FakeTransport:
    responses: list[TransportResponse | RestrictedHttpError]
    requests: list[tuple[str, ResolvedAddress, dict[str, str]]] = field(
        default_factory=list
    )

    async def get(
        self,
        url: str,
        address: ResolvedAddress,
        headers: Mapping[str, str],
    ) -> TransportResponse:
        self.requests.append((url, address, dict(headers)))
        response = self.responses.pop(0)
        if isinstance(response, RestrictedHttpError):
            raise response
        return response


def response(
    *,
    status: int = 200,
    headers: Mapping[str, str] | None = None,
    body: bytes = b"public page",
    peer: ResolvedAddress = PUBLIC_V4,
) -> TransportResponse:
    return TransportResponse(
        status=status,
        headers={"Content-Type": "text/plain", **(headers or {})},
        body=body,
        peer_ip=peer.ip,
    )


async def fetch_with(
    resolver: FakeResolver,
    transport: FakeTransport,
    *,
    url: str = "https://public.example/page",
):
    return await RestrictedHttpClient(
        make_settings(),
        resolver=resolver,
        transport=transport,
    ).fetch(
        url,
        headers={"Accept": "text/html", "Connection": "close"},
        limits=LIMITS,
    )


async def test_fetches_with_prevalidated_address_and_normalized_headers():
    resolver = FakeResolver({"public.example": [PUBLIC_V4]})
    transport = FakeTransport(
        [response(headers={"X-Request-ID": "source-request"})]
    )

    result = await fetch_with(resolver, transport)

    assert result.status == 200
    assert result.body == b"public page"
    assert result.final_url == "https://public.example/page"
    assert result.headers["content-type"] == "text/plain"
    assert result.headers["x-request-id"] == "source-request"
    assert transport.requests == [
        (
            "https://public.example/page",
            PUBLIC_V4,
            {"Accept": "text/html", "Connection": "close"},
        )
    ]


async def test_retries_only_prevalidated_addresses_on_retryable_connection_error():
    resolver = FakeResolver({"public.example": [PUBLIC_V6, PUBLIC_V4]})
    transport = FakeTransport(
        [
            RestrictedHttpError("network_failed", retryable=True),
            response(),
        ]
    )

    result = await fetch_with(resolver, transport)

    assert result.status == 200
    assert [request[1] for request in transport.requests] == [PUBLIC_V6, PUBLIC_V4]


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://public.example/page", "http_blocked"),
        ("https://user:secret@public.example/page", "url_invalid"),
        ("https://public.example/page#fragment", "url_invalid"),
        ("https://127.0.0.1/page", "ip_literal"),
        (" https://public.example/page", "url_invalid"),
    ],
)
async def test_rejects_unsafe_urls(url: str, reason: str):
    resolver = FakeResolver({"public.example": [PUBLIC_V4]})
    transport = FakeTransport([])

    with pytest.raises(RestrictedHttpError) as caught:
        await fetch_with(resolver, transport, url=url)

    assert caught.value.reason == reason
    assert transport.requests == []


async def test_rejects_nonstandard_production_port():
    transport = FakeTransport([])
    client = RestrictedHttpClient(
        make_settings("development"),
        resolver=FakeResolver({"public.example": [PUBLIC_V4]}),
        transport=transport,
    )

    with pytest.raises(RestrictedHttpError) as caught:
        await client.fetch(
            "https://public.example:8443/page",
            headers={},
            limits=LIMITS,
        )

    assert caught.value.reason == "port_blocked"
    assert transport.requests == []


@pytest.mark.parametrize(
    "addresses",
    [
        [PUBLIC_V4, ResolvedAddress(ipaddress.ip_address("10.0.0.1"))],
        [ResolvedAddress(ipaddress.ip_address("::ffff:127.0.0.1"))],
        [ResolvedAddress(ipaddress.ip_address("169.254.169.254"))],
        [ResolvedAddress(ipaddress.ip_address("100.64.0.1"))],
        [ResolvedAddress(ipaddress.ip_address("192.0.2.1"))],
    ],
)
async def test_rejects_the_entire_dns_result_when_any_address_is_not_public(
    addresses: list[ResolvedAddress],
):
    resolver = FakeResolver({"public.example": addresses})
    transport = FakeTransport([])

    with pytest.raises(RestrictedHttpError) as caught:
        await fetch_with(resolver, transport)

    assert caught.value.reason == "address_blocked"
    assert transport.requests == []


async def test_revalidates_redirects_and_rejects_https_downgrade():
    resolver = FakeResolver(
        {
            "public.example": [PUBLIC_V4],
            "cdn.example": [PUBLIC_V6],
        }
    )
    transport = FakeTransport(
        [
            response(status=302, headers={"Location": "https://cdn.example/final"}),
            response(
                status=302,
                headers={"Location": "http://cdn.example/plain"},
                peer=PUBLIC_V6,
            ),
        ]
    )

    with pytest.raises(RestrictedHttpError) as caught:
        await fetch_with(resolver, transport)

    assert caught.value.reason == "https_downgrade"
    assert resolver.calls == [("public.example", 443), ("cdn.example", 443)]


async def test_rejects_peer_address_mismatch():
    resolver = FakeResolver({"public.example": [PUBLIC_V4]})
    transport = FakeTransport([response(peer=PUBLIC_V6)])

    with pytest.raises(RestrictedHttpError) as caught:
        await fetch_with(resolver, transport)

    assert caught.value.reason == "peer_mismatch"


async def test_decodes_one_gzip_layer_and_enforces_configured_limits():
    resolver = FakeResolver({"public.example": [PUBLIC_V4]})
    valid_body = b"A" * 200
    valid = FakeTransport(
        [
            response(
                headers={"Content-Encoding": "gzip"},
                body=gzip.compress(valid_body),
            )
        ]
    )
    assert (await fetch_with(resolver, valid)).body == valid_body

    bomb = FakeTransport(
        [
            response(
                headers={"Content-Encoding": "gzip"},
                body=gzip.compress(b"A" * 1000),
            )
        ]
    )
    with pytest.raises(RestrictedHttpError) as caught:
        await fetch_with(resolver, bomb)
    assert caught.value.reason == "compression_ratio"

    oversized = FakeTransport([response(body=b"x" * 2049)])
    with pytest.raises(RestrictedHttpError) as caught:
        await fetch_with(resolver, oversized)
    assert caught.value.reason == "response_too_large"

    layered = FakeTransport(
        [
            response(
                headers={"Content-Encoding": "gzip, br"},
                body=gzip.compress(b"page"),
            )
        ]
    )
    with pytest.raises(RestrictedHttpError) as caught:
        await fetch_with(resolver, layered)
    assert caught.value.reason == "encoding_unsupported"


async def test_total_timeout_includes_dns():
    class SlowResolver:
        async def resolve(self, hostname: str, port: int) -> list[ResolvedAddress]:
            await asyncio.sleep(0.05)
            return [PUBLIC_V4]

    limits = FetchLimits(
        **{
            **LIMITS.__dict__,
            "total_timeout_seconds": 0.01,
        }
    )
    client = RestrictedHttpClient(
        make_settings(),
        resolver=SlowResolver(),
        transport=FakeTransport([]),
    )

    with pytest.raises(RestrictedHttpError) as caught:
        await client.fetch(
            "https://public.example/page",
            headers={},
            limits=limits,
        )

    assert caught.value.reason == "timeout"


async def test_real_transport_uses_pinned_loopback_and_sends_only_caller_headers(
    monkeypatch,
):
    seen_headers: list[bytes] = []

    async def handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        request = await reader.readuntil(b"\r\n\r\n")
        seen_headers.append(request)
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/plain\r\n"
            b"Content-Length: 11\r\n"
            b"Set-Cookie: ignored=yes\r\n"
            b"Connection: close\r\n\r\n"
            b"public page"
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    try:
        result = await RestrictedHttpClient(
            make_settings(),
            resolver=FakeResolver({"public.test": [LOOPBACK]}),
        ).fetch(
            f"http://public.test:{port}/page",
            headers={
                "Accept": "text/plain",
                "User-Agent": "iTime-Test/1",
                "Connection": "close",
            },
            limits=LIMITS,
        )
    finally:
        server.close()
        await server.wait_closed()

    assert result.body == b"public page"
    request = seen_headers[0].lower()
    assert b"host: public.test:" in request
    assert b"user-agent: itime-test/1" in request
    assert b"cookie:" not in request
    assert b"authorization:" not in request
    assert b"proxy-authorization:" not in request


async def test_real_transport_enforces_streamed_compressed_limit():
    body = b"x" * 1025

    async def handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(
            (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/plain\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode()
        )
        writer.write(body)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        client = RestrictedHttpClient(
            make_settings(),
            resolver=FakeResolver({"public.test": [LOOPBACK]}),
        )
        with pytest.raises(RestrictedHttpError) as caught:
            await client.fetch(
                f"http://public.test:{port}/page",
                headers={},
                limits=LIMITS,
            )
    finally:
        server.close()
        await server.wait_closed()

    assert caught.value.reason == "response_too_large"
