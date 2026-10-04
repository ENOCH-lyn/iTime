import argparse
import asyncio
import hashlib
import os
from urllib.parse import urlsplit

from itime.calendar import subscription_worker
from itime.calendar.subscription_fetcher import CalendarFetchResult, FetchValidators
from itime.config import Settings, get_settings
from itime.database import Database
from itime.public_links import worker as public_link_worker
from itime.public_links.fetcher import PublicLinkFetchResult
from itime.public_links.parser import PublicDocument

SYNTHETIC_CALENDAR = (
    b"BEGIN:VCALENDAR\r\n"
    b"VERSION:2.0\r\n"
    b"PRODID:-//iTime Performance//EN\r\n"
    b"BEGIN:VEVENT\r\n"
    b"UID:synthetic-worker-event\r\n"
    b"DTSTART:20261005T090000Z\r\n"
    b"DTEND:20261005T100000Z\r\n"
    b"SUMMARY:Synthetic performance event\r\n"
    b"END:VEVENT\r\n"
    b"END:VCALENDAR\r\n"
)


def require_test_environment(settings: Settings) -> None:
    if settings.environment != "test":
        raise RuntimeError("性能 worker 只允许在 test 环境运行")


class SyntheticCalendarFetcher:
    def __init__(self, *, delay_seconds: float) -> None:
        if delay_seconds < 0:
            raise ValueError("延迟不能为负数")
        self.delay_seconds = delay_seconds

    async def fetch(
        self,
        url: str,
        *,
        validators: FetchValidators | None = None,
    ) -> CalendarFetchResult:
        await asyncio.sleep(self.delay_seconds)
        final_url = validators.final_url if validators is not None else url
        return CalendarFetchResult(
            status=200,
            final_url=final_url,
            etag='"synthetic-v2"',
            last_modified="Mon, 05 Oct 2026 12:00:00 GMT",
            content_sha256=hashlib.sha256(SYNTHETIC_CALENDAR).hexdigest(),
            content=SYNTHETIC_CALENDAR,
            redirect_count=0,
        )


class SyntheticPublicLinkFetcher:
    def __init__(self, *, delay_seconds: float) -> None:
        if delay_seconds < 0:
            raise ValueError("延迟不能为负数")
        self.delay_seconds = delay_seconds

    async def fetch(self, url: str) -> PublicLinkFetchResult:
        await asyncio.sleep(self.delay_seconds)
        host = urlsplit(url).hostname or "performance.invalid"
        body = b"Synthetic performance document."
        return PublicLinkFetchResult(
            final_host=host,
            http_status=200,
            content_sha256=hashlib.sha256(body).hexdigest(),
            document=PublicDocument(
                media_type="text/plain",
                page_title="Synthetic",
                summary="Synthetic performance document.",
                extracted_text="Synthetic performance document.",
            ),
            redirect_count=0,
        )


async def run_subscription(
    database: Database,
    settings: Settings,
    *,
    delay_seconds: float,
) -> bool:
    require_test_environment(settings)
    return await subscription_worker.process_one(
        database,
        settings,
        fetcher=SyntheticCalendarFetcher(delay_seconds=delay_seconds),
    )


async def run_public_link(
    database: Database,
    settings: Settings,
    *,
    delay_seconds: float,
) -> bool:
    require_test_environment(settings)
    return await public_link_worker.process_one(
        database,
        settings,
        fetcher=SyntheticPublicLinkFetcher(delay_seconds=delay_seconds),
    )


async def run_forever(kind: str, *, delay_seconds: float) -> None:
    settings = get_settings()
    require_test_environment(settings)
    database = Database(settings)
    try:
        while True:
            if kind == "subscription":
                found = await run_subscription(
                    database,
                    settings,
                    delay_seconds=delay_seconds,
                )
            else:
                found = await run_public_link(
                    database,
                    settings,
                    delay_seconds=delay_seconds,
                )
            if not found:
                await asyncio.sleep(1)
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="运行 iTime 性能测试受限 worker")
    parser.add_argument("kind", choices=("subscription", "public-link"))
    parser.add_argument(
        "--delay",
        type=float,
        default=float(os.environ.get("ITIME_PERF_FETCH_DELAY_SECONDS", "0.05")),
    )
    args = parser.parse_args()
    asyncio.run(run_forever(args.kind, delay_seconds=args.delay))


if __name__ == "__main__":
    main()
