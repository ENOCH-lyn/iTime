from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from dateutil.rrule import rruleset, rrulestr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.calendar.models import (
    CalendarSubscriptionEvent,
    CalendarSubscriptionOverride,
)
from itime.errors import AppError

MAX_SUBSCRIPTION_EVENTS = 5000


async def query_subscription_occurrences(
    session: AsyncSession,
    owner_id: UUID,
    *,
    query_start: datetime,
    query_end: datetime,
    remaining_limit: int,
) -> list[dict]:
    events = list(
        await session.scalars(
            select(CalendarSubscriptionEvent)
            .where(CalendarSubscriptionEvent.owner_id == owner_id)
            .order_by(
                CalendarSubscriptionEvent.source_id,
                CalendarSubscriptionEvent.uid,
                CalendarSubscriptionEvent.recurrence_id,
            )
            .limit(MAX_SUBSCRIPTION_EVENTS + 1)
        )
    )
    if len(events) > MAX_SUBSCRIPTION_EVENTS:
        raise AppError(
            "calendar_result_too_large",
            "订阅来源事件过多，请暂停不需要的来源或缩小查询范围。",
            status=422,
        )
    overrides = {
        override.event_id: override
        for override in await session.scalars(
            select(CalendarSubscriptionOverride).where(
                CalendarSubscriptionOverride.owner_id == owner_id,
                CalendarSubscriptionOverride.event_id.in_(
                    [event.id for event in events]
                ),
            )
        )
    } if events else {}
    by_source_uid: defaultdict[
        tuple[UUID, str],
        list[CalendarSubscriptionEvent],
    ] = defaultdict(list)
    for event in events:
        by_source_uid[(event.source_id, event.uid)].append(event)

    results: list[dict] = []
    for grouped in by_source_uid.values():
        master = next((event for event in grouped if not event.recurrence_id), None)
        if (
            master is None
            or master.baseline is None
            or master.baseline.get("start") is None
        ):
            continue
        detached = {
            event.recurrence_id: event
            for event in grouped
            if event.recurrence_id
        }
        master_override = overrides.get(master.id)
        processed_detached: set[str] = set()
        for occurrence_start in _expanded_starts(
            master.baseline,
            query_start=query_start,
            query_end=query_end,
            limit=remaining_limit - len(results),
        ):
            match_key = _match_key(master.baseline["start"], occurrence_start)
            source_override = detached.get(match_key)
            if source_override is not None:
                processed_detached.add(match_key)
            projected = _project_occurrence(
                master,
                master_override,
                occurrence_start,
                match_key,
                source_override=source_override,
                source_override_values=(
                    overrides.get(source_override.id)
                    if source_override is not None
                    else None
                ),
            )
            if _intersects(projected, query_start, query_end):
                results.append(projected)
                if len(results) > remaining_limit:
                    raise AppError(
                        "calendar_result_too_large",
                        "该范围内超过 2000 条安排，请缩小查询范围。",
                        status=422,
                    )
        for source_override in detached.values():
            if (
                source_override.recurrence_id in processed_detached
                or source_override.baseline is None
                or source_override.baseline.get("status") == "cancelled"
            ):
                continue
            moved_start = _start_datetime(source_override.baseline["start"])
            if (
                source_override.missing_at is not None
                and moved_start.astimezone(UTC) >= source_override.missing_at
            ):
                continue
            original_key = source_override.recurrence_id
            projected = _project_occurrence(
                master,
                master_override,
                moved_start,
                original_key,
                source_override=source_override,
                source_override_values=overrides.get(source_override.id),
            )
            if _intersects(projected, query_start, query_end):
                results.append(projected)
                if len(results) > remaining_limit:
                    raise AppError(
                        "calendar_result_too_large",
                        "该范围内超过 2000 条安排，请缩小查询范围。",
                        status=422,
                    )
    return results


def _expanded_starts(
    baseline: dict,
    *,
    query_start: datetime,
    query_end: datetime,
    limit: int,
) -> list[datetime]:
    if limit < 0:
        raise AppError(
            "calendar_result_too_large",
            "该范围内超过 2000 条安排，请缩小查询范围。",
            status=422,
        )
    start = _start_datetime(baseline["start"])
    maximum_duration = _duration(baseline)
    search_start = query_start - maximum_duration
    recurrence = rruleset(cache=False)
    recurrence.rdate(start)
    rule = baseline.get("rrule")
    if rule:
        recurrence.rrule(rrulestr(rule, dtstart=start))
    for rdate in baseline.get("rdates", []):
        recurrence.rdate(_start_datetime(rdate))
    for exdate in baseline.get("exdates", []):
        recurrence.exdate(_start_datetime(exdate))
    results: list[datetime] = []
    for value in recurrence.xafter(search_start, count=limit + 2, inc=True):
        if value >= query_end:
            break
        results.append(value)
        if len(results) > limit:
            raise AppError(
                "calendar_result_too_large",
                "该范围内超过 2000 条安排，请缩小查询范围。",
                status=422,
            )
    return results


def _project_occurrence(
    master: CalendarSubscriptionEvent,
    master_override: CalendarSubscriptionOverride | None,
    occurrence_start: datetime,
    match_key: str,
    *,
    source_override: CalendarSubscriptionEvent | None,
    source_override_values: CalendarSubscriptionOverride | None,
) -> dict:
    master_baseline = master.baseline or {}
    baseline = master_baseline
    effective_event = master
    local_override = master_override
    use_detached = (
        source_override is not None
        and source_override.baseline is not None
        and (
            source_override.missing_at is None
            or occurrence_start.astimezone(UTC) < source_override.missing_at
        )
    )
    if use_detached and source_override is not None:
        effective_event = source_override
        local_override = source_override_values
        baseline = {**master_baseline, **source_override.baseline}
        if source_override.baseline.get("start"):
            occurrence_start = _start_datetime(baseline["start"])

    fields = {
        field: baseline.get(field, master_baseline.get(field))
        for field in ("name", "description", "location", "busy")
    }
    if local_override is not None:
        fields.update(local_override.values)
    duration = _duration(
        baseline if baseline.get("duration") is not None else master_baseline
    )
    start_utc, warnings = _validated_start(occurrence_start, baseline["start"])
    zone = ZoneInfo(baseline["start"]["time_zone"])
    local_start = occurrence_start.astimezone(zone)
    if baseline["start"]["kind"] == "date":
        local_end = datetime.combine(
            local_start.date() + duration,
            time.min,
            tzinfo=zone,
        )
        end_utc = local_end.astimezone(UTC) if start_utc is not None else None
    else:
        end_utc = start_utc + duration if start_utc is not None else None
        local_end = (occurrence_start.astimezone(UTC) + duration).astimezone(zone)
    cancelled = _cancelled_for_occurrence(master, occurrence_start)
    if use_detached and source_override is not None:
        cancelled = cancelled or _cancelled_for_occurrence(
            source_override,
            occurrence_start,
        )
    return {
        "series_id": None,
        "series_version": None,
        "subscription_event_id": effective_event.id,
        "source_event_version": effective_event.version,
        "occurrence_key": f"subscription:{master.id}:{match_key}",
        "occurrence_type": "subscription",
        "kind": "fixed",
        "term_id": None,
        "source_id": master.source_id,
        "source_type": "subscription",
        "name": fields["name"] or "未命名安排",
        "description": fields["description"] or "",
        "location": fields["location"] or "",
        "busy": bool(fields["busy"]) and not cancelled,
        "status": "source_cancelled" if cancelled else "scheduled",
        "time_zone": baseline["start"]["time_zone"],
        "all_day": baseline["start"]["kind"] == "date",
        "original_local_start_date": local_start.date(),
        "original_local_start_time": (
            None if baseline["start"]["kind"] == "date" else local_start.time().replace(tzinfo=None)
        ),
        "local_start_date": local_start.date(),
        "local_start_time": (
            None if baseline["start"]["kind"] == "date" else local_start.time().replace(tzinfo=None)
        ),
        "local_end_date": local_end.date(),
        "local_end_time": (
            None if baseline["start"]["kind"] == "date" else local_end.time().replace(tzinfo=None)
        ),
        "start_at": start_utc,
        "end_at": end_utc,
        "warning_codes": warnings,
        "exception_id": None,
        "exception_version": None,
        "exception_action": None,
        "addition_id": None,
        "addition_version": None,
        "source_occurrence_key": match_key,
        "conflict_with": [],
    }


def _cancelled_for_occurrence(
    event: CalendarSubscriptionEvent,
    occurrence_start: datetime,
) -> bool:
    if event.baseline is not None and event.baseline.get("status") == "cancelled":
        if event.cancelled_at is None:
            return True
        return occurrence_start.astimezone(UTC) >= event.cancelled_at
    if event.recurrence_id and event.missing_at is not None:
        return False
    if event.source_status != "cancelled":
        return False
    if event.cancelled_at is None:
        return True
    return occurrence_start.astimezone(UTC) >= event.cancelled_at


def _validated_start(
    value: datetime,
    start_spec: dict,
) -> tuple[datetime | None, list[str]]:
    zone = ZoneInfo(start_spec["time_zone"])
    local_naive = value.astimezone(zone).replace(tzinfo=None)
    aware = local_naive.replace(tzinfo=zone, fold=value.fold)
    roundtrip = aware.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
    if roundtrip != local_naive:
        return None, ["invalid_local_time"]
    return aware.astimezone(UTC), []


def _intersects(
    occurrence: dict,
    query_start: datetime,
    query_end: datetime,
) -> bool:
    if occurrence["start_at"] is None or occurrence["end_at"] is None:
        local_date = occurrence["local_start_date"]
        return query_start.date() <= local_date <= query_end.date()
    if occurrence["start_at"] == occurrence["end_at"]:
        return query_start <= occurrence["start_at"] < query_end
    return (
        occurrence["start_at"] < query_end
        and occurrence["end_at"] > query_start
    )


def _duration(baseline: dict) -> timedelta:
    duration = baseline["duration"]
    if duration["kind"] == "days":
        return timedelta(days=int(duration["value"]))
    return timedelta(seconds=int(duration["value"]))


def _start_datetime(start: dict) -> datetime:
    zone = ZoneInfo(start["time_zone"])
    if start["kind"] == "date":
        return datetime.combine(date.fromisoformat(start["date"]), time.min, tzinfo=zone)
    return datetime.fromisoformat(start["local"]).replace(tzinfo=zone)


def _match_key(start_spec: dict, value: datetime) -> str:
    if start_spec["kind"] == "date":
        return f"DATE:{value.date().isoformat()}"
    if start_spec.get("semantic") == "floating":
        return f"FLOATING:{value.replace(tzinfo=None).isoformat(timespec='seconds')}"
    return "INSTANT:" + value.astimezone(UTC).isoformat().replace("+00:00", "Z")
