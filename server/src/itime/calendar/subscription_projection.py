from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from dateutil.rrule import rruleset, rrulestr
from sqlalchemy import and_, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from itime.calendar.models import (
    CalendarSource,
    CalendarSubscriptionEvent,
    CalendarSubscriptionException,
    CalendarSubscriptionOverride,
)
from itime.calendar.subscription_exception_service import (
    apply_subscription_exception,
)
from itime.errors import AppError

MAX_SUBSCRIPTION_EVENTS = 5000


@dataclass(frozen=True)
class SubscriptionCandidate:
    master: CalendarSubscriptionEvent
    master_override: CalendarSubscriptionOverride | None
    occurrence_start: datetime
    match_key: str
    source_override: CalendarSubscriptionEvent | None
    source_override_values: CalendarSubscriptionOverride | None


async def query_subscription_occurrences(
    session: AsyncSession,
    owner_id: UUID,
    *,
    query_start: datetime,
    query_end: datetime,
    remaining_limit: int,
    source_id: UUID | None = None,
) -> list[dict]:
    statement = select(CalendarSubscriptionEvent).where(
        CalendarSubscriptionEvent.owner_id == owner_id
    )
    if source_id is not None:
        statement = statement.where(
            CalendarSubscriptionEvent.source_id == source_id
        )
    events = list(
        await session.scalars(
            statement.order_by(
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
    source_versions = dict(
        (
            await session.execute(
                select(CalendarSource.id, CalendarSource.version).where(
                    CalendarSource.owner_id == owner_id,
                    CalendarSource.id.in_({event.source_id for event in events}),
                )
            )
        ).all()
    ) if events else {}
    by_source_uid: defaultdict[
        tuple[UUID, str],
        list[CalendarSubscriptionEvent],
    ] = defaultdict(list)
    for event in events:
        by_source_uid[(event.source_id, event.uid)].append(event)

    candidates: list[SubscriptionCandidate] = []
    masters: dict[UUID, CalendarSubscriptionEvent] = {}
    grouped_events: dict[UUID, list[CalendarSubscriptionEvent]] = {}
    for grouped in by_source_uid.values():
        master = next((event for event in grouped if not event.recurrence_id), None)
        if (
            master is None
            or master.baseline is None
            or master.baseline.get("start") is None
        ):
            continue
        masters[master.id] = master
        grouped_events[master.id] = grouped
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
            limit=remaining_limit - len(candidates),
        ):
            match_key = _match_key(master.baseline["start"], occurrence_start)
            source_override = detached.get(match_key)
            if source_override is not None:
                processed_detached.add(match_key)
            candidates.append(
                SubscriptionCandidate(
                    master=master,
                    master_override=master_override,
                    occurrence_start=occurrence_start,
                    match_key=match_key,
                    source_override=source_override,
                    source_override_values=(
                        overrides.get(source_override.id)
                        if source_override is not None
                        else None
                    ),
                )
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
            candidates.append(
                SubscriptionCandidate(
                    master=master,
                    master_override=master_override,
                    occurrence_start=moved_start,
                    match_key=source_override.recurrence_id,
                    source_override=source_override,
                    source_override_values=overrides.get(source_override.id),
                )
            )

    exception_map = await _load_relevant_exceptions(
        session,
        owner_id,
        candidates,
        masters,
        query_start=query_start,
        query_end=query_end,
    )
    results: list[dict] = []
    projected_keys: set[tuple[UUID, str]] = set()
    for candidate in candidates:
        key = (candidate.master.id, candidate.match_key)
        projected_keys.add(key)
        projected = _project_occurrence(
            candidate.master,
            candidate.master_override,
            candidate.occurrence_start,
            candidate.match_key,
            source_version=source_versions[candidate.master.source_id],
            source_override=candidate.source_override,
            source_override_values=candidate.source_override_values,
        )
        exception = exception_map.get(key)
        if exception is not None:
            projected = apply_subscription_exception(projected, exception)
        if _intersects(projected, query_start, query_end):
            results.append(projected)
            if len(results) > remaining_limit:
                raise AppError(
                    "calendar_result_too_large",
                    "该范围内超过 2000 条安排，请缩小查询范围。",
                    status=422,
                )

    for key, exception in exception_map.items():
        if exception.action != "moved" or key in projected_keys:
            continue
        master = masters.get(exception.master_event_id)
        if master is None or master.baseline is None:
            continue
        occurrence_start = _occurrence_start_for_key(
            master.baseline,
            exception.source_occurrence_key,
        )
        if occurrence_start is None:
            continue
        grouped = grouped_events[master.id]
        source_override = next(
            (
                event
                for event in grouped
                if event.recurrence_id == exception.source_occurrence_key
            ),
            None,
        )
        projected = _project_occurrence(
            master,
            overrides.get(master.id),
            occurrence_start,
            exception.source_occurrence_key,
            source_version=source_versions[master.source_id],
            source_override=source_override,
            source_override_values=(
                    overrides.get(source_override.id)
                    if source_override is not None
                    else None
                ),
        )
        projected = apply_subscription_exception(projected, exception)
        if _intersects(projected, query_start, query_end):
            results.append(projected)
            if len(results) > remaining_limit:
                raise AppError(
                    "calendar_result_too_large",
                    "该范围内超过 2000 条安排，请缩小查询范围。",
                    status=422,
                )
    return results


async def get_subscription_occurrence(
    session: AsyncSession,
    owner_id: UUID,
    *,
    source_id: UUID,
    master_event_id: UUID,
    source_occurrence_key: str,
    include_local_exception: bool = True,
) -> dict:
    source = await session.scalar(
        select(CalendarSource).where(
            CalendarSource.id == source_id,
            CalendarSource.owner_id == owner_id,
        )
    )
    master = await session.scalar(
        select(CalendarSubscriptionEvent).where(
            CalendarSubscriptionEvent.id == master_event_id,
            CalendarSubscriptionEvent.source_id == source_id,
            CalendarSubscriptionEvent.owner_id == owner_id,
            CalendarSubscriptionEvent.recurrence_id == "",
        )
    )
    if source is None or master is None or master.baseline is None:
        raise AppError(
            "calendar_subscription_occurrence_not_found",
            "未找到此订阅安排实例。",
            status=404,
        )
    occurrence_start = _occurrence_start_for_key(
        master.baseline,
        source_occurrence_key,
    )
    if occurrence_start is None:
        raise AppError(
            "calendar_subscription_occurrence_not_found",
            "当前来源规则中不存在此订阅安排实例。",
            status=404,
        )
    grouped = list(
        await session.scalars(
            select(CalendarSubscriptionEvent).where(
                CalendarSubscriptionEvent.source_id == source_id,
                CalendarSubscriptionEvent.owner_id == owner_id,
                CalendarSubscriptionEvent.uid == master.uid,
            )
        )
    )
    source_override = next(
        (
            event
            for event in grouped
            if event.recurrence_id == source_occurrence_key
        ),
        None,
    )
    event_ids = [event.id for event in grouped]
    overrides = {
        override.event_id: override
        for override in await session.scalars(
            select(CalendarSubscriptionOverride).where(
                CalendarSubscriptionOverride.owner_id == owner_id,
                CalendarSubscriptionOverride.event_id.in_(event_ids),
            )
        )
    }
    occurrence = _project_occurrence(
        master,
        overrides.get(master.id),
        occurrence_start,
        source_occurrence_key,
        source_version=source.version,
        source_override=source_override,
        source_override_values=(
            overrides.get(source_override.id)
            if source_override is not None
            else None
        ),
    )
    if not include_local_exception:
        return occurrence
    exception = await session.scalar(
        select(CalendarSubscriptionException).where(
            CalendarSubscriptionException.owner_id == owner_id,
            CalendarSubscriptionException.master_event_id == master.id,
            CalendarSubscriptionException.source_occurrence_key
            == source_occurrence_key,
        )
    )
    if exception is not None:
        occurrence = apply_subscription_exception(occurrence, exception)
    return occurrence


async def _load_relevant_exceptions(
    session: AsyncSession,
    owner_id: UUID,
    candidates: list[SubscriptionCandidate],
    masters: dict[UUID, CalendarSubscriptionEvent],
    *,
    query_start: datetime,
    query_end: datetime,
) -> dict[tuple[UUID, str], CalendarSubscriptionException]:
    if not masters:
        return {}
    candidate_keys = {
        (candidate.master.id, candidate.match_key)
        for candidate in candidates
    }
    conditions = [
        and_(
            CalendarSubscriptionException.action == "moved",
            CalendarSubscriptionException.moved_start_at < query_end,
            CalendarSubscriptionException.moved_end_at > query_start,
        )
    ]
    if candidate_keys:
        conditions.append(
            tuple_(
                CalendarSubscriptionException.master_event_id,
                CalendarSubscriptionException.source_occurrence_key,
            ).in_(candidate_keys)
        )
    exceptions = list(
        await session.scalars(
            select(CalendarSubscriptionException).where(
                CalendarSubscriptionException.owner_id == owner_id,
                CalendarSubscriptionException.master_event_id.in_(masters),
                or_(*conditions),
            )
        )
    )
    return {
        (exception.master_event_id, exception.source_occurrence_key): exception
        for exception in exceptions
    }


def _occurrence_start_for_key(
    baseline: dict,
    source_occurrence_key: str,
) -> datetime | None:
    start_spec = baseline["start"]
    zone = ZoneInfo(start_spec["time_zone"])
    try:
        if source_occurrence_key.startswith("DATE:"):
            candidate = datetime.combine(
                date.fromisoformat(source_occurrence_key.removeprefix("DATE:")),
                time.min,
                tzinfo=zone,
            )
        elif source_occurrence_key.startswith("FLOATING:"):
            candidate = datetime.fromisoformat(
                source_occurrence_key.removeprefix("FLOATING:")
            ).replace(tzinfo=zone)
        elif source_occurrence_key.startswith("INSTANT:"):
            candidate = datetime.fromisoformat(
                source_occurrence_key.removeprefix("INSTANT:").replace(
                    "Z",
                    "+00:00",
                )
            ).astimezone(zone)
        else:
            return None
    except ValueError:
        return None
    if _match_key(start_spec, candidate) != source_occurrence_key:
        return None
    occurrence = _recurrence_set(baseline).after(candidate, inc=True)
    if occurrence is None or occurrence != candidate:
        return None
    return candidate


def _recurrence_set(baseline: dict) -> rruleset:
    start = _start_datetime(baseline["start"])
    recurrence = rruleset(cache=False)
    recurrence.rdate(start)
    rule = baseline.get("rrule")
    if rule:
        recurrence.rrule(rrulestr(rule, dtstart=start))
    for rdate in baseline.get("rdates", []):
        recurrence.rdate(_start_datetime(rdate))
    for exdate in baseline.get("exdates", []):
        recurrence.exdate(_start_datetime(exdate))
    return recurrence


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
    maximum_duration = _duration(baseline)
    search_start = query_start - maximum_duration
    recurrence = _recurrence_set(baseline)
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
    source_version: int,
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
    source_status = "source_cancelled" if cancelled else "scheduled"
    return {
        "series_id": None,
        "series_version": None,
        "subscription_event_id": effective_event.id,
        "subscription_master_event_id": master.id,
        "source_event_version": effective_event.version,
        "source_version": source_version,
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
        "status": source_status,
        "source_status": source_status,
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
