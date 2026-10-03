import codecs
import csv
import io
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from icalendar import Calendar, Event, vRecur

EXPECTED_EXPORT_PATHS = (
    "README.txt",
    "itime-data.json",
    "csv/projects.csv",
    "csv/tasks.csv",
    "csv/task-steps.csv",
    "csv/task-occurrences.csv",
    "csv/calendar-events.csv",
    "csv/plan-blocks.csv",
    "csv/focus-sessions.csv",
    "csv/difficulties.csv",
    "csv/quota-ledger.csv",
    "calendar/itime.ics",
    "markdown/conversations.md",
    "markdown/reviews.md",
    "markdown/memories.md",
    "manifest.json",
)


class ExportBuildError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ExportLimits:
    max_uncompressed_bytes: int
    max_archive_bytes: int


@dataclass(frozen=True)
class ExportArchive:
    content: bytes
    sha256: str
    byte_size: int
    filename: str
    manifest: dict[str, Any]


def stable_json(value: object, *, pretty: bool = False) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    )


def csv_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        rendered = "true" if value else "false"
    elif isinstance(value, (dict, list)):
        rendered = stable_json(value)
    else:
        rendered = str(value)
    if rendered.lstrip().startswith(("=", "+", "-", "@")):
        return f"'{rendered}"
    return rendered


def _csv_file(rows: list[dict], fields: tuple[str, ...]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream,
        fieldnames=fields,
        extrasaction="ignore",
        lineterminator="\r\n",
    )
    writer.writeheader()
    writer.writerows(
        {field: csv_cell(row.get(field)) for field in fields}
        for row in rows
    )
    return codecs.BOM_UTF8 + stream.getvalue().encode("utf-8")


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("datetime must include an offset")
    return parsed


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _parse_time(value: str) -> time:
    return time.fromisoformat(value)


def _event(
    *,
    uid: str,
    summary: str,
    snapshot_at: datetime,
    kind: str,
    entity_id: str,
    starts_at: datetime | date,
    ends_at: datetime | date | None = None,
    description: str = "",
    location: str = "",
) -> Event:
    item = Event()
    item.add("uid", uid)
    item.add("dtstamp", snapshot_at)
    item.add("summary", summary)
    item.add("dtstart", starts_at)
    if ends_at is not None:
        item.add("dtend", ends_at)
    if description:
        item.add("description", description)
    if location:
        item.add("location", location)
    item.add("x-itime-kind", kind)
    item.add("x-itime-entity-id", entity_id)
    return item


WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")


def _recurrence(schedule: dict) -> dict[str, object] | None:
    frequency = schedule.get("recurrence_frequency", "none")
    if frequency == "none":
        return None
    if frequency == "weekdays":
        result: dict[str, object] = {"FREQ": ["WEEKLY"], "BYDAY": list(WEEKDAYS[:5])}
    else:
        result = {"FREQ": [str(frequency).upper()]}
        if frequency == "weekly":
            result["BYDAY"] = [
                WEEKDAYS[int(value) - 1]
                for value in schedule.get("recurrence_weekdays", [])
                if 1 <= int(value) <= 7
            ]
        elif frequency == "monthly":
            result["BYMONTHDAY"] = [int(schedule["recurrence_day_of_month"])]
        elif frequency == "yearly":
            result["BYMONTH"] = [int(schedule["recurrence_month"])]
            result["BYMONTHDAY"] = [int(schedule["recurrence_day_of_month"])]
    interval = int(schedule.get("recurrence_interval", 1))
    if interval != 1:
        result["INTERVAL"] = [interval]
    if schedule.get("recurrence_count") is not None:
        result["COUNT"] = [int(schedule["recurrence_count"])]
    elif schedule.get("recurrence_until"):
        result["UNTIL"] = [_parse_date(str(schedule["recurrence_until"]))]
    return result


def _local_datetime(day: str, local_time: str, time_zone: str) -> datetime:
    return datetime.combine(
        _parse_date(day),
        _parse_time(local_time),
        tzinfo=ZoneInfo(time_zone),
    )


def _task_events(snapshot: dict, snapshot_at: datetime) -> list[Event]:
    tasks = {
        item["id"]: item
        for item in snapshot["tasks"]["tasks"]
        if item.get("deleted_at") is None and item.get("purged_at") is None
    }
    events: list[Event] = []
    for schedule in snapshot["tasks"]["schedules"]:
        task = tasks.get(schedule["task_id"])
        if task is None or schedule.get("due_kind") == "none":
            continue
        if schedule["due_kind"] == "date":
            starts_at: datetime | date = _parse_date(schedule["start_date"])
        else:
            starts_at = _parse_datetime(schedule["due_at"])
        item = _event(
            uid=f"task-{task['id']}@itime",
            summary=task["title"],
            snapshot_at=snapshot_at,
            kind="task_due",
            entity_id=task["id"],
            starts_at=starts_at,
            description=task.get("description") or "",
        )
        recurrence = _recurrence(schedule)
        if recurrence is not None:
            item.add("rrule", recurrence)
        events.append(item)
    return events


def _series_events(snapshot: dict, snapshot_at: datetime) -> list[Event]:
    events: list[Event] = []
    event_by_series: dict[str, Event] = {}
    series_by_id = {
        item["id"]: item for item in snapshot["calendar"]["series"]
    }
    for series in series_by_id.values():
        start = _local_datetime(
            series["start_date"],
            series["starts_at"],
            series["time_zone"],
        )
        end_date = _parse_date(series["start_date"]) + timedelta(
            days=int(series.get("end_day_offset") or 0)
        )
        end = datetime.combine(
            end_date,
            _parse_time(series["ends_at"]),
            tzinfo=ZoneInfo(series["time_zone"]),
        )
        item = _event(
            uid=f"calendar-series-{series['id']}@itime",
            summary=series["name"],
            snapshot_at=snapshot_at,
            kind=f"calendar_{series['kind']}",
            entity_id=series["id"],
            starts_at=start,
            ends_at=end,
            description=series.get("description") or "",
            location=series.get("location") or "",
        )
        item.add("transp", "OPAQUE" if series.get("busy", True) else "TRANSPARENT")
        if series.get("kind") == "course" and series.get("course_week_numbers"):
            first_week = _parse_date(series["course_first_week_monday"])
            weekday_offset = _parse_date(series["start_date"]).isoweekday() - 1
            dates = [
                first_week + timedelta(days=(int(week) - 1) * 7 + weekday_offset)
                for week in series["course_week_numbers"]
            ]
            extra_starts = [
                datetime.combine(
                    day,
                    _parse_time(series["starts_at"]),
                    tzinfo=ZoneInfo(series["time_zone"]),
                )
                for day in dates
                if day != start.date()
            ]
            if extra_starts:
                item.add("rdate", extra_starts)
        else:
            recurrence = _recurrence(series)
            if recurrence is not None:
                item.add("rrule", recurrence)
        item.add("x-itime-source-id", series.get("source_id") or "manual")
        events.append(item)
        event_by_series[series["id"]] = item

    for exception in snapshot["calendar"]["exceptions"]:
        series = series_by_id.get(exception["series_id"])
        base = event_by_series.get(exception["series_id"])
        if series is None or base is None:
            continue
        original = _local_datetime(
            exception["original_start_date"],
            series["starts_at"],
            series["time_zone"],
        )
        base.add("exdate", original)
        if exception["action"] != "moved":
            continue
        moved_start = _local_datetime(
            exception["moved_start_date"],
            exception["moved_starts_at"],
            exception["moved_time_zone"],
        )
        moved_end_date = _parse_date(exception["moved_start_date"]) + timedelta(
            days=int(exception.get("moved_end_day_offset") or 0)
        )
        moved_end = datetime.combine(
            moved_end_date,
            _parse_time(exception["moved_ends_at"]),
            tzinfo=ZoneInfo(exception["moved_time_zone"]),
        )
        moved = _event(
            uid=f"calendar-series-{series['id']}@itime",
            summary=exception.get("override_name") or series["name"],
            snapshot_at=snapshot_at,
            kind="calendar_moved",
            entity_id=exception["id"],
            starts_at=moved_start,
            ends_at=moved_end,
            description=(
                exception.get("override_description")
                if exception.get("override_description") is not None
                else series.get("description") or ""
            ),
            location=(
                exception.get("override_location")
                if exception.get("override_location") is not None
                else series.get("location") or ""
            ),
        )
        moved.add("recurrence-id", original)
        moved.add("x-itime-source-id", series.get("source_id") or "manual")
        events.append(moved)

    for addition in snapshot["calendar"]["additions"]:
        if addition.get("status") != "scheduled":
            continue
        series = series_by_id.get(addition["series_id"])
        if series is None:
            continue
        start = _local_datetime(
            addition["local_start_date"],
            addition["starts_at"],
            addition["time_zone"],
        )
        end_date = _parse_date(addition["local_start_date"]) + timedelta(
            days=int(addition.get("end_day_offset") or 0)
        )
        end = datetime.combine(
            end_date,
            _parse_time(addition["ends_at"]),
            tzinfo=ZoneInfo(addition["time_zone"]),
        )
        added = _event(
            uid=f"calendar-addition-{addition['id']}@itime",
            summary=addition.get("override_name") or series["name"],
            snapshot_at=snapshot_at,
            kind="calendar_addition",
            entity_id=addition["id"],
            starts_at=start,
            ends_at=end,
            description=addition.get("override_description") or series.get("description") or "",
            location=addition.get("override_location") or series.get("location") or "",
        )
        added.add("x-itime-source-id", series.get("source_id") or "manual")
        events.append(added)
    return events


def _plan_events(snapshot: dict, snapshot_at: datetime) -> list[Event]:
    current_versions = {
        plan["current_version_id"]
        for plan in snapshot["planning"]["plans"]
    }
    return [
        _event(
            uid=f"plan-block-{block['id']}@itime",
            summary=block["task_title_snapshot"],
            snapshot_at=snapshot_at,
            kind="plan_block",
            entity_id=block["id"],
            starts_at=_parse_datetime(block["starts_at"]),
            ends_at=_parse_datetime(block["ends_at"]),
        )
        for block in snapshot["planning"]["blocks"]
        if block["plan_version_id"] in current_versions
    ]


def _baseline_start(spec: dict) -> datetime | date:
    if spec["kind"] == "date":
        return _parse_date(spec["date"])
    return _local_datetime(
        spec["local"][:10],
        spec["local"][11:],
        spec["time_zone"],
    )


def _subscription_events(
    snapshot: dict,
    snapshot_at: datetime,
) -> tuple[list[Event], list[dict[str, str]]]:
    overrides = {
        item["event_id"]: item["values"]
        for item in snapshot["calendar"]["subscription_overrides"]
    }
    events: list[Event] = []
    warnings: list[dict[str, str]] = []
    for source in snapshot["calendar"]["subscription_events"]:
        baseline = source.get("baseline")
        if (
            not isinstance(baseline, dict)
            or not isinstance(baseline.get("start"), dict)
        ):
            warnings.append(
                {
                    "code": "subscription_event_not_exported_to_ics",
                    "entity_id": source["id"],
                }
            )
            continue
        if source.get("source_status") == "cancelled" or baseline.get("status") == "cancelled":
            continue
        try:
            fields = {
                **{
                    key: baseline.get(key)
                    for key in ("name", "description", "location", "busy")
                },
                **overrides.get(source["id"], {}),
            }
            start = _baseline_start(baseline["start"])
            duration = baseline.get("duration") or {"kind": "seconds", "value": 0}
            if isinstance(start, date) and not isinstance(start, datetime):
                end: datetime | date = start + timedelta(
                    days=int(duration["value"])
                )
            else:
                end = start + timedelta(
                    **{
                        "days" if duration["kind"] == "days" else "seconds":
                        int(duration["value"])
                    }
                )
            item = _event(
                uid=f"subscription-{source['source_id']}-{source['uid']}@itime",
                summary=fields.get("name") or "未命名安排",
                snapshot_at=snapshot_at,
                kind="calendar_subscription",
                entity_id=source["id"],
                starts_at=start,
                ends_at=end,
                description=fields.get("description") or "",
                location=fields.get("location") or "",
            )
            item.add("x-itime-source-id", source["source_id"])
            if source.get("recurrence_id"):
                item.add("recurrence-id", _baseline_start(baseline["start"]))
            elif baseline.get("rrule"):
                item.add("rrule", vRecur.from_ical(baseline["rrule"]))
                for name, values in (
                    ("rdate", baseline.get("rdates", [])),
                    ("exdate", baseline.get("exdates", [])),
                ):
                    for value in values:
                        item.add(name, _baseline_start(value))
            events.append(item)
        except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError):
            warnings.append(
                {
                    "code": "subscription_event_not_exported_to_ics",
                    "entity_id": source["id"],
                }
            )
    return events, warnings


def _calendar_file(
    snapshot: dict,
) -> tuple[bytes, list[dict[str, str]], int]:
    snapshot_at = _parse_datetime(snapshot["snapshot_at"])
    calendar = Calendar()
    calendar.add("prodid", "-//iTime//Personal Data Export//CN")
    calendar.add("version", "2.0")
    calendar.add("calscale", "GREGORIAN")
    subscription_events, warnings = _subscription_events(snapshot, snapshot_at)
    events = [
        *_task_events(snapshot, snapshot_at),
        *_series_events(snapshot, snapshot_at),
        *_plan_events(snapshot, snapshot_at),
        *subscription_events,
    ]
    for item in events:
        calendar.add_component(item)
    return calendar.to_ical(), warnings, len(events)


MARKDOWN_ESCAPES = "\\`*_{}[]<>()#+-.!|>"


def escape_markdown(value: object) -> str:
    text_value = "" if value is None else str(value)
    return "".join(
        f"\\{character}" if character in MARKDOWN_ESCAPES else character
        for character in text_value
    )


def markdown_heading(level: int, value: object) -> str:
    return f"{'#' * level} {escape_markdown(value)}"


def _conversation_markdown(snapshot: dict) -> tuple[bytes, int]:
    messages: dict[str, list[dict]] = {}
    for message in snapshot["conversations"]["messages"]:
        messages.setdefault(message["conversation_id"], []).append(message)
    lines = ["# iTime conversations", ""]
    conversations = snapshot["conversations"]["conversations"]
    for conversation in conversations:
        lines.extend(
            [
                markdown_heading(2, conversation["title"]),
                "",
                f"ID: `{conversation['id']}`",
                "",
            ]
        )
        for message in messages.get(conversation["id"], []):
            role = "User" if message["role"] == "user" else "Assistant"
            lines.extend(
                [
                    markdown_heading(3, f"{role} · {message['created_at']}"),
                    "",
                    escape_markdown(message.get("content") or ""),
                    "",
                ]
            )
    return "\n".join(lines).encode(), len(conversations)


def _review_markdown(snapshot: dict) -> tuple[bytes, int]:
    sessions = {
        item["id"]: item for item in snapshot["reviews"]["sessions"]
    }
    reports: dict[str, list[dict]] = {}
    for report in snapshot["reviews"]["reports"]:
        reports.setdefault(report["review_session_id"], []).append(report)
    annotations: dict[str, list[dict]] = {}
    for annotation in snapshot["reviews"]["annotations"]:
        annotations.setdefault(annotation["report_version_id"], []).append(annotation)
    lines = ["# iTime reviews", ""]
    for session_id, session in sessions.items():
        lines.extend(
            [
                markdown_heading(2, session["activity_date"]),
                "",
                escape_markdown(session.get("supplemental_facts") or ""),
                "",
            ]
        )
        for report in reports.get(session_id, []):
            lines.extend(
                [
                    markdown_heading(3, f"Report v{report['version']}"),
                    "",
                    escape_markdown(report["summary"]),
                    "",
                    markdown_heading(4, "Facts"),
                    "",
                ]
            )
            lines.extend(
                f"- {escape_markdown(item)}"
                for item in report.get("facts", [])
            )
            lines.extend(["", markdown_heading(4, "Next actions"), ""])
            lines.extend(
                f"- {escape_markdown(item)}"
                for item in report.get("next_actions", [])
            )
            active_annotations = [
                item
                for item in annotations.get(report["id"], [])
                if item.get("deleted_at") is None
            ]
            if active_annotations:
                lines.extend(["", markdown_heading(4, "Annotations"), ""])
                lines.extend(
                    f"- {escape_markdown(item['content'])}"
                    for item in active_annotations
                )
            lines.append("")
    return "\n".join(lines).encode(), len(sessions)


def _memory_markdown(snapshot: dict) -> tuple[bytes, int]:
    lines = ["# iTime memories", ""]
    memories = snapshot["reviews"]["memories"]
    for memory in memories:
        lifecycle = (
            "purged"
            if memory.get("purged_at")
            else "trashed"
            if memory.get("deleted_at")
            else "active"
        )
        lines.extend(
            [
                markdown_heading(2, memory.get("title") or "Purged memory"),
                "",
                f"- Kind: `{memory['kind']}`",
                f"- State: `{lifecycle}`",
                f"- Source: `{memory['source_type']}`",
                "",
                escape_markdown(memory.get("content") or ""),
                "",
            ]
        )
    return "\n".join(lines).encode(), len(memories)


def _calendar_csv(snapshot: dict) -> list[dict]:
    rows = [
        {
            "kind": f"calendar_{item['kind']}",
            "id": item["id"],
            "parent_id": item.get("term_id") or "",
            "title": item["name"],
            "description": item.get("description") or "",
            "location": item.get("location") or "",
            "start_date": item["start_date"],
            "start_time": item["starts_at"],
            "end_time": item["ends_at"],
            "time_zone": item["time_zone"],
            "status": "active",
            "source_type": item["source_type"],
        }
        for item in snapshot["calendar"]["series"]
    ]
    rows.extend(
        {
            "kind": "subscription",
            "id": item["id"],
            "parent_id": item["source_id"],
            "title": (item.get("baseline") or {}).get("name") or "",
            "description": (item.get("baseline") or {}).get("description") or "",
            "location": (item.get("baseline") or {}).get("location") or "",
            "start_date": stable_json((item.get("baseline") or {}).get("start")),
            "start_time": "",
            "end_time": stable_json((item.get("baseline") or {}).get("duration")),
            "time_zone": (
                ((item.get("baseline") or {}).get("start") or {}).get("time_zone")
                or ""
            ),
            "status": item["source_status"],
            "source_type": "subscription",
        }
        for item in snapshot["calendar"]["subscription_events"]
    )
    return rows


def _build_entries(snapshot: dict) -> tuple[list[tuple[str, bytes, int]], list[dict[str, str]]]:
    calendar_content, warnings, calendar_count = _calendar_file(snapshot)
    conversation_md, conversation_count = _conversation_markdown(snapshot)
    review_md, review_count = _review_markdown(snapshot)
    memory_md, memory_count = _memory_markdown(snapshot)
    csv_specs = (
        (
            "csv/projects.csv",
            snapshot["tasks"]["projects"],
            ("id", "kind", "name", "description", "version", "created_at", "updated_at"),
        ),
        (
            "csv/tasks.csv",
            snapshot["tasks"]["tasks"],
            (
                "id",
                "project_id",
                "title",
                "description",
                "estimated_minutes",
                "priority",
                "status",
                "completed_at",
                "archived_at",
                "deleted_at",
                "purged_at",
                "version",
                "created_at",
                "updated_at",
            ),
        ),
        (
            "csv/task-steps.csv",
            snapshot["tasks"]["steps"],
            (
                "id",
                "task_id",
                "title",
                "position",
                "completed",
                "completed_at",
                "created_at",
                "updated_at",
            ),
        ),
        (
            "csv/task-occurrences.csv",
            snapshot["tasks"]["occurrences"],
            (
                "id",
                "task_id",
                "occurrence_key",
                "original_date",
                "kind",
                "effective_date",
                "effective_local_time",
                "effective_at",
                "effective_time_zone",
                "rescheduled",
                "status",
                "completed_at",
                "completed_step_ids",
                "makeup_for_key",
                "makeup_for_date",
                "version",
            ),
        ),
        (
            "csv/calendar-events.csv",
            _calendar_csv(snapshot),
            (
                "kind",
                "id",
                "parent_id",
                "title",
                "description",
                "location",
                "start_date",
                "start_time",
                "end_time",
                "time_zone",
                "status",
                "source_type",
            ),
        ),
        (
            "csv/plan-blocks.csv",
            snapshot["planning"]["blocks"],
            (
                "id",
                "plan_version_id",
                "position",
                "task_id",
                "occurrence_key",
                "task_title_snapshot",
                "step_titles_snapshot",
                "local_date",
                "local_starts_at",
                "local_ends_at",
                "time_zone",
                "starts_at",
                "ends_at",
                "estimated_minutes",
                "created_at",
            ),
        ),
        (
            "csv/focus-sessions.csv",
            snapshot["execution"]["focus_sessions"],
            (
                "id",
                "source",
                "task_id",
                "occurrence_key",
                "scheduled_block_id",
                "title_snapshot",
                "mode",
                "target_seconds",
                "status",
                "started_at",
                "ended_at",
                "effective_started_at",
                "effective_ended_at",
                "effective_duration_seconds",
                "time_zone",
                "version",
            ),
        ),
        (
            "csv/difficulties.csv",
            snapshot["execution"]["difficulties"],
            (
                "id",
                "task_id",
                "focus_session_id",
                "occurrence_key",
                "task_title_snapshot",
                "content",
                "category",
                "emotion",
                "occurred_at",
                "time_zone",
                "deleted_at",
                "purged_at",
                "version",
            ),
        ),
        (
            "csv/quota-ledger.csv",
            snapshot["quota"]["ledger"],
            (
                "id",
                "daily_allowance_id",
                "reservation_id",
                "adjustment_id",
                "job_id",
                "kind",
                "delta_nanos",
                "currency",
                "usage_source",
                "created_at",
            ),
        ),
    )
    entries: list[tuple[str, bytes, int]] = [
        (
            "README.txt",
            (
                "iTime 个人数据导出\n\n"
                "此压缩包可能包含敏感个人信息，请妥善保存。\n"
                "itime-data.json 是完整权威导出；"
                "CSV、ICS 和 Markdown 文件用于阅读或迁移。\n"
            ).encode(),
            0,
        ),
        (
            "itime-data.json",
            stable_json(snapshot, pretty=True).encode(),
            sum(
                len(value)
                for category in snapshot.values()
                if isinstance(category, dict)
                for value in category.values()
                if isinstance(value, list)
            ),
        ),
    ]
    entries.extend(
        (path, _csv_file(rows, fields), len(rows))
        for path, rows, fields in csv_specs
    )
    entries.extend(
        (
            ("calendar/itime.ics", calendar_content, calendar_count),
            ("markdown/conversations.md", conversation_md, conversation_count),
            ("markdown/reviews.md", review_md, review_count),
            ("markdown/memories.md", memory_md, memory_count),
        )
    )
    return entries, warnings


def _zip_info(path: str, snapshot_at: datetime) -> ZipInfo:
    normalized = snapshot_at.astimezone(UTC)
    year = max(1980, normalized.year)
    info = ZipInfo(
        path,
        (
            year,
            normalized.month,
            normalized.day,
            normalized.hour,
            normalized.minute,
            normalized.second,
        ),
    )
    info.compress_type = ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    return info


def build_export_archive(
    snapshot: dict,
    export_id: UUID,
    limits: ExportLimits,
) -> ExportArchive:
    snapshot_at = _parse_datetime(snapshot["snapshot_at"])
    entries, warnings = _build_entries(snapshot)
    uncompressed_size = sum(len(content) for _, content, _ in entries)
    if uncompressed_size > limits.max_uncompressed_bytes:
        raise ExportBuildError(
            "data_export_too_large",
            "数据内容超过当前导出容量，请保留请求编号并联系管理员。",
        )
    manifest_files = {
        path: {
            "sha256": sha256(content).hexdigest(),
            "byte_size": len(content),
            "record_count": record_count,
        }
        for path, content, record_count in entries
    }
    manifest = {
        "export_id": str(export_id),
        "schema_version": snapshot["schema_version"],
        "snapshot_at": snapshot["snapshot_at"],
        "account_id": snapshot["account"]["profile"]["id"],
        "generator": "itime-server",
        "files": manifest_files,
        "warnings": warnings,
    }
    entries.append(
        (
            "manifest.json",
            stable_json(manifest, pretty=True).encode(),
            len(manifest_files),
        )
    )
    output = io.BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as package:
        for path, content, _ in entries:
            package.writestr(_zip_info(path, snapshot_at), content)
    content = output.getvalue()
    if len(content) > limits.max_archive_bytes:
        raise ExportBuildError(
            "data_export_too_large",
            "数据包超过当前下载容量，请保留请求编号并联系管理员。",
        )
    return ExportArchive(
        content=content,
        sha256=sha256(content).hexdigest(),
        byte_size=len(content),
        filename=f"itime-export-{snapshot_at.strftime('%Y%m%d-%H%M%SZ')}.zip",
        manifest=manifest,
    )
