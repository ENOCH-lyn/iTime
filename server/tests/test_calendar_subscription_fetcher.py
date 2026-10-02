import asyncio
import gzip
import ipaddress
from collections.abc import Mapping
from dataclasses import dataclass, field

import pytest
from cryptography.fernet import Fernet

import itime.calendar.subscription_fetcher as fetcher_module
from itime.calendar.subscription_fetcher import (
    MAX_COMPRESSED_BYTES,
    CalendarFetchResult,
    FetchValidators,
    ResolvedAddress,
    RestrictedCalendarFetcher,
    SubscriptionFetchError,
    TransportResponse,
)
from itime.config import Settings

ICS = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//iTime Test//EN\r\nEND:VCALENDAR\r\n"
PUBLIC_V4 = ResolvedAddress(ipaddress.ip_address("93.184.216.34"))
PUBLIC_V6 = ResolvedAddress(ipaddress.ip_address("2606:2800:220:1:248:1893:25c8:1946"))
LOOPBACK = ResolvedAddress(ipaddress.ip_address("127.0.0.1"))


def make_settings() -> Settings:
    return Settings(
        environment="test",
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
    responses: list[TransportResponse | SubscriptionFetchError]
    requests: list[tuple[str, ResolvedAddress, dict[str, str]]] = field(default_factory=list)

    async def get(
        self,
        url: str,
        address: ResolvedAddress,
        headers: Mapping[str, str],
    ) -> TransportResponse:
        self.requests.append((url, address, dict(headers)))
        response_value = self.responses.pop(0)
        if isinstance(response_value, SubscriptionFetchError):
            raise response_value
        return response_value


def response(
    *,
    status: int = 200,
    headers: Mapping[str, str] | None = None,
    body: bytes = ICS,
    peer: ResolvedAddress = PUBLIC_V4,
) -> TransportResponse:
    return TransportResponse(
        status=status,
        headers={"content-type": "text/calendar", **(headers or {})},
        body=body,
        peer_ip=peer.ip,
    )


async def fetch_with(
    resolver: FakeResolver,
    transport: FakeTransport,
    *,
    url: str = "https://calendar.example/feed.ics",
    validators: FetchValidators | None = None,
) -> CalendarFetchResult:
    return await RestrictedCalendarFetcher(
        make_settings(),
        resolver=resolver,
        transport=transport,
    ).fetch(url, validators=validators)


async def test_fetches_calendar_with_pinned_public_address():
    resolver = FakeResolver({"calendar.example": [PUBLIC_V4, PUBLIC_V6]})
    transport = FakeTransport(
        [
            response(
                headers={
                    "etag": '"version-1"',
                    "last-modified": "Wed, 21 Oct 2015 07:28:00 GMT",
                }
            )
        ]
    )

    result = await fetch_with(resolver, transport)

    assert result.status == 200
    assert result.content == ICS
    assert result.final_url == "https://calendar.example/feed.ics"
    assert result.etag == '"version-1"'
    assert result.content_sha256 is not None
    assert transport.requests[0][1] == PUBLIC_V4
    assert transport.requests[0][2]["Accept-Encoding"] == "gzip, identity"


async def test_retries_the_next_prevalidated_address_on_connection_failure():
    resolver = FakeResolver({"calendar.example": [PUBLIC_V6, PUBLIC_V4]})
    transport = FakeTransport(
        [
            SubscriptionFetchError(
                "calendar_subscription_network_failed",
                "无法连接订阅服务器，请稍后重试。",
                retryable=True,
            ),
            response(),
        ]
    )

    result = await fetch_with(resolver, transport)

    assert result.status == 200
    assert [request[1] for request in transport.requests] == [PUBLIC_V6, PUBLIC_V4]


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("http://calendar.example/feed.ics", "calendar_subscription_http_blocked"),
        ("https://user:secret@calendar.example/feed.ics", "calendar_subscription_url_invalid"),
        ("https://calendar.example/feed.ics#token", "calendar_subscription_url_invalid"),
        ("https://127.0.0.1/feed.ics", "calendar_subscription_ip_literal"),
        (" https://calendar.example/feed.ics", "calendar_subscription_url_invalid"),
    ],
)
async def test_rejects_unsafe_urls(url: str, code: str):
    resolver = FakeResolver({"calendar.example": [PUBLIC_V4]})
    transport = FakeTransport([])

    with pytest.raises(SubscriptionFetchError) as caught:
        await fetch_with(resolver, transport, url=url)

    assert caught.value.code == code
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
async def test_rejects_any_non_public_address(addresses: list[ResolvedAddress]):
    resolver = FakeResolver({"calendar.example": addresses})
    transport = FakeTransport([])

    with pytest.raises(SubscriptionFetchError) as caught:
        await fetch_with(resolver, transport)

    assert caught.value.code == "calendar_subscription_address_blocked"
    assert transport.requests == []


async def test_revalidates_each_redirect_and_blocks_https_downgrade():
    resolver = FakeResolver(
        {
            "calendar.example": [PUBLIC_V4],
            "other.example": [PUBLIC_V6],
        }
    )
    transport = FakeTransport(
        [
            response(status=302, headers={"location": "https://other.example/feed"}),
            response(
                status=302,
                headers={"location": "http://other.example/plain"},
                peer=PUBLIC_V6,
            ),
        ]
    )

    with pytest.raises(SubscriptionFetchError) as caught:
        await fetch_with(resolver, transport)

    assert caught.value.code == "calendar_subscription_https_downgrade"
    assert resolver.calls == [
        ("calendar.example", 443),
        ("other.example", 443),
    ]


async def test_sends_validators_only_to_the_exact_saved_final_url():
    resolver = FakeResolver(
        {
            "calendar.example": [PUBLIC_V4],
            "cdn.example": [PUBLIC_V6],
        }
    )
    transport = FakeTransport(
        [
            response(status=302, headers={"location": "https://cdn.example/final.ics"}),
            response(status=304, body=b"", peer=PUBLIC_V6),
        ]
    )

    result = await fetch_with(
        resolver,
        transport,
        validators=FetchValidators(
            final_url="https://cdn.example/final.ics",
            etag='"secret-validator"',
            last_modified="Wed, 21 Oct 2015 07:28:00 GMT",
        ),
    )

    assert result.status == 304
    assert "If-None-Match" not in transport.requests[0][2]
    assert transport.requests[1][2]["If-None-Match"] == '"secret-validator"'


async def test_rejects_unconditional_not_modified():
    resolver = FakeResolver({"calendar.example": [PUBLIC_V4]})
    transport = FakeTransport([response(status=304, body=b"")])

    with pytest.raises(SubscriptionFetchError) as caught:
        await fetch_with(
            resolver,
            transport,
            validators=FetchValidators(final_url="https://calendar.example/feed.ics"),
        )

    assert caught.value.code == "calendar_subscription_unexpected_not_modified"


async def test_rejects_peer_address_mismatch():
    resolver = FakeResolver({"calendar.example": [PUBLIC_V4]})
    transport = FakeTransport([response(peer=PUBLIC_V6)])

    with pytest.raises(SubscriptionFetchError) as caught:
        await fetch_with(resolver, transport)

    assert caught.value.code == "calendar_subscription_peer_mismatch"


async def test_accepts_single_gzip_layer_and_rejects_bombs_and_multiple_encodings():
    resolver = FakeResolver({"calendar.example": [PUBLIC_V4]})
    valid = FakeTransport(
        [response(headers={"content-encoding": "gzip"}, body=gzip.compress(ICS))]
    )
    assert (await fetch_with(resolver, valid)).content == ICS

    bomb = FakeTransport(
        [
            response(
                headers={"content-encoding": "gzip"},
                body=gzip.compress(b"A" * 100_000),
            )
        ]
    )
    with pytest.raises(SubscriptionFetchError) as caught:
        await fetch_with(resolver, bomb)
    assert caught.value.code == "calendar_subscription_compression_ratio"

    oversized = FakeTransport(
        [
            response(
                headers={"content-encoding": "gzip"},
                body=gzip.compress(b"A" * (4 * 1024 * 1024 + 1)),
            )
        ]
    )
    with pytest.raises(SubscriptionFetchError) as caught:
        await fetch_with(resolver, oversized)
    assert caught.value.code == "calendar_subscription_response_too_large"

    layered = FakeTransport(
        [response(headers={"content-encoding": "gzip, br"}, body=gzip.compress(ICS))]
    )
    with pytest.raises(SubscriptionFetchError) as caught:
        await fetch_with(resolver, layered)
    assert caught.value.code == "calendar_subscription_encoding_unsupported"


async def test_plain_text_requires_a_complete_vcalendar():
    resolver = FakeResolver({"calendar.example": [PUBLIC_V4]})
    valid = FakeTransport([response(headers={"content-type": "text/plain"})])
    assert (await fetch_with(resolver, valid)).content == ICS

    html = FakeTransport(
        [
            response(
                headers={"content-type": "text/plain"},
                body=b"<html>login</html>",
            )
        ]
    )
    with pytest.raises(SubscriptionFetchError) as caught:
        await fetch_with(resolver, html)
    assert caught.value.code == "calendar_subscription_content_type_invalid"


async def test_transport_enforces_streamed_compressed_limit():
    body = b"x" * (MAX_COMPRESSED_BYTES + 1)

    async def handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(
            (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/calendar\r\n"
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
        fetcher = RestrictedCalendarFetcher(
            make_settings(),
            resolver=FakeResolver({"calendar.test": [LOOPBACK]}),
        )
        with pytest.raises(SubscriptionFetchError) as caught:
            await fetcher.fetch(f"http://calendar.test:{port}/calendar.ics")
    finally:
        server.close()
        await server.wait_closed()

    assert caught.value.code == "calendar_subscription_response_too_large"


async def test_total_timeout_includes_dns(monkeypatch):
    class SlowResolver:
        async def resolve(self, hostname: str, port: int) -> list[ResolvedAddress]:
            await asyncio.sleep(0.05)
            return [PUBLIC_V4]

    monkeypatch.setattr(fetcher_module, "TOTAL_TIMEOUT_SECONDS", 0.01)
    fetcher = RestrictedCalendarFetcher(
        make_settings(),
        resolver=SlowResolver(),
        transport=FakeTransport([]),
    )

    with pytest.raises(SubscriptionFetchError) as caught:
        await fetcher.fetch("https://calendar.example/feed.ics")

    assert caught.value.code == "calendar_subscription_timeout"


async def test_real_transport_uses_the_validated_loopback_address(monkeypatch):
    seen_request = asyncio.Event()

    async def handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        request = await reader.readuntil(b"\r\n\r\n")
        assert b"Host: calendar.test:" in request
        seen_request.set()
        writer.write(
            (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/calendar\r\n"
                f"Content-Length: {len(ICS)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode()
            + ICS
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    try:
        result = await RestrictedCalendarFetcher(
            make_settings(),
            resolver=FakeResolver({"calendar.test": [LOOPBACK]}),
        ).fetch(f"http://calendar.test:{port}/calendar.ics")
    finally:
        server.close()
        await server.wait_closed()

    assert result.content == ICS
    assert seen_request.is_set()
