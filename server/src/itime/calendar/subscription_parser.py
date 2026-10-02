import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrulestr
from icalendar import Calendar

from itime.errors import AppError

MAX_COMPONENTS = 10_000
MAX_EVENTS = 5_000
MAX_EVENT_PROPERTIES = 200
MAX_EXCEPTION_DATES = 500
MAX_EVENT_DURATION = timedelta(days=7)
SUPPORTED_FREQUENCIES = {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}


@dataclass(frozen=True)
class ParsedSubscriptionItem:
    uid: str
    recurrence_id: str
    baseline: dict[str, Any] | None
    content_sha256: str
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class SubscriptionSnapshot:
    items: list[ParsedSubscriptionItem]
    item_errors: list[dict[str, Any]]
    absence_complete: bool


class SubscriptionItemError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def parse_subscription_ics(
    content: bytes,
    *,
    default_time_zone: str,
) -> SubscriptionSnapshot:
    try:
        text_value = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AppError(
            "calendar_subscription_encoding_invalid",
            "订阅日历必须使用 UTF-8 编码。",
        ) from exc
    if "\x00" in text_value:
        raise AppError("calendar_subscription_ics_invalid", "订阅日历包含无效字符。")
    try:
        calendar = Calendar.from_ical(text_value)
    except Exception as exc:
        raise AppError(
            "calendar_subscription_ics_invalid",
            "订阅日历已损坏或格式不正确。",
        ) from exc
    components = list(calendar.walk())
    if len(components) > MAX_COMPONENTS:
        raise AppError(
            "calendar_subscription_ics_too_complex",
            "订阅日历组件数量超过限制。",
        )
    events = [component for component in components if component.name == "VEVENT"]
    if len(events) > MAX_EVENTS:
        raise AppError(
            "calendar_subscription_too_many_events",
            "订阅日历事件数量超过 5000 条限制。",
        )

    parsed: list[ParsedSubscriptionItem] = []
    item_errors: list[dict[str, Any]] = []
    absence_complete = True
    for position, event in enumerate(events, start=1):
        uid = _uid_text(event)
        recurrence_id = _recurrence_identity(event.get("RECURRENCE-ID"))
        if not uid:
            absence_complete = False
            item_errors.append(
                {
                    "position": position,
                    "code": "calendar_subscription_uid_missing",
                    "message": "来源事件缺少 UID，本次不会据缺失取消旧事件。",
                }
            )
            continue
        try:
            baseline = _event_baseline(
                event,
                uid=uid,
                recurrence_id=recurrence_id,
                default_time_zone=default_time_zone,
            )
            digest = _content_digest(baseline)
            parsed.append(
                ParsedSubscriptionItem(
                    uid=uid,
                    recurrence_id=recurrence_id,
                    baseline=baseline,
                    content_sha256=digest,
                )
            )
        except SubscriptionItemError as exc:
            digest = hashlib.sha256(
                f"{uid}\0{recurrence_id}\0{exc.code}".encode()
            ).hexdigest()
            parsed.append(
                ParsedSubscriptionItem(
                    uid=uid,
                    recurrence_id=recurrence_id,
                    baseline=None,
                    content_sha256=digest,
                    error_code=exc.code,
                    error_message=exc.message,
                )
            )
            item_errors.append(
                {
                    "position": position,
                    "uid": uid,
                    "recurrence_id": recurrence_id or None,
                    "code": exc.code,
                    "message": exc.message,
                }
            )
        except (AttributeError, OverflowError, TypeError, ValueError):
            digest = hashlib.sha256(
                f"{uid}\0{recurrence_id}\0invalid".encode()
            ).hexdigest()
            parsed.append(
                ParsedSubscriptionItem(
                    uid=uid,
                    recurrence_id=recurrence_id,
                    baseline=None,
                    content_sha256=digest,
                    error_code="calendar_subscription_event_invalid",
                    error_message="来源事件字段格式不正确，已保留旧版本。",
                )
            )
            item_errors.append(
                {
                    "position": position,
                    "uid": uid,
                    "recurrence_id": recurrence_id or None,
                    "code": "calendar_subscription_event_invalid",
                    "message": "来源事件字段格式不正确，已保留旧版本。",
                }
            )

    by_identity: dict[tuple[str, str], ParsedSubscriptionItem] = {}
    conflicts: set[tuple[str, str]] = set()
    for item in parsed:
        key = (item.uid, item.recurrence_id)
        previous = by_identity.get(key)
        if previous is None:
            by_identity[key] = item
        elif previous.content_sha256 != item.content_sha256:
            conflicts.add(key)
    for uid, recurrence_id in conflicts:
        error = ParsedSubscriptionItem(
            uid=uid,
            recurrence_id=recurrence_id,
            baseline=None,
            content_sha256=hashlib.sha256(
                f"{uid}\0{recurrence_id}\0duplicate".encode()
            ).hexdigest(),
            error_code="calendar_subscription_identity_conflict",
            error_message="同一 UID 与 RECURRENCE-ID 出现冲突内容，已保留旧版本。",
        )
        by_identity[(uid, recurrence_id)] = error
        item_errors.append(
            {
                "uid": uid,
                "recurrence_id": recurrence_id or None,
                "code": error.error_code,
                "message": error.error_message,
            }
        )
    return SubscriptionSnapshot(
        items=list(by_identity.values()),
        item_errors=item_errors[:100],
        absence_complete=absence_complete,
    )


def _event_baseline(
    event: Any,
    *,
    uid: str,
    recurrence_id: str,
    default_time_zone: str,
) -> dict[str, Any]:
    parser_errors = getattr(event, "errors", [])
    if parser_errors:
        raise SubscriptionItemError(
            "calendar_subscription_event_invalid",
            "来源事件包含无法解析的属性，已保留旧版本。",
        )
    property_names = [
        str(name).upper()
        for name, _value in event.property_items()
        if str(name).upper() not in {"BEGIN", "END"}
    ]
    duplicate_fields = {
        "UID",
        "DTSTART",
        "DTEND",
        "DURATION",
        "RRULE",
        "RECURRENCE-ID",
        "SUMMARY",
        "DESCRIPTION",
        "LOCATION",
        "TRANSP",
        "STATUS",
    }
    duplicates = sorted(
        name for name in duplicate_fields if property_names.count(name) > 1
    )
    if duplicates:
        raise SubscriptionItemError(
            "calendar_subscription_event_invalid",
            "来源事件包含重复的单值属性，已保留旧版本。",
        )
    if len(event.property_items()) > MAX_EVENT_PROPERTIES:
        raise SubscriptionItemError(
            "calendar_subscription_event_too_large",
            "来源事件属性数量超过限制，已保留旧版本。",
        )
    start_property = event.get("DTSTART")
    status = str(event.get("STATUS", "")).strip().upper()
    if status not in {"", "CONFIRMED", "TENTATIVE", "CANCELLED"}:
        status = ""
    if start_property is None:
        if status == "CANCELLED":
            return {
                "uid": uid,
                "recurrence_id": recurrence_id,
                "status": "cancelled",
            }
        raise SubscriptionItemError(
            "calendar_subscription_start_missing",
            "来源事件缺少 DTSTART，已保留旧版本。",
        )
    start = _temporal_value(start_property, default_time_zone=default_time_zone)
    duration = _event_duration(event, start, default_time_zone=default_time_zone)
    recurrence = _recurrence_rule(event, start)
    rdates = _date_list(event.get("RDATE"), default_time_zone=default_time_zone)
    exdates = _date_list(event.get("EXDATE"), default_time_zone=default_time_zone)
    return {
        "uid": uid,
        "recurrence_id": recurrence_id,
        "name": _property_text(event, "SUMMARY", 200) or "未命名安排",
        "description": _property_text(event, "DESCRIPTION", 20_000),
        "location": _property_text(event, "LOCATION", 300),
        "busy": str(event.get("TRANSP", "OPAQUE")).upper() != "TRANSPARENT",
        "status": "cancelled" if status == "CANCELLED" else "active",
        "start": start,
        "duration": duration,
        "rrule": recurrence,
        "rdates": rdates,
        "exdates": exdates,
    }


def _property_text(event: Any, name: str, limit: int) -> str:
    value = event.get(name)
    if value is None or isinstance(value, list):
        return ""
    text_value = str(value).strip()
    if any(ord(character) < 32 and character not in "\t\n\r" for character in text_value):
        raise SubscriptionItemError(
            "calendar_subscription_event_invalid",
            f"来源事件的 {name} 包含无效字符，已保留旧版本。",
        )
    return text_value[:limit]


def _uid_text(event: Any) -> str:
    value = event.get("UID")
    if value is None or isinstance(value, list):
        return ""
    text_value = str(value)
    if not text_value.strip() or len(text_value) > 512:
        return ""
    if any(ord(character) < 32 or ord(character) == 127 for character in text_value):
        return ""
    return text_value


def _temporal_value(value: Any, *, default_time_zone: str) -> dict[str, Any]:
    decoded = value.dt
    if isinstance(decoded, datetime):
        raw_tzid = value.params.get("TZID") if hasattr(value, "params") else None
        time_zone = _property_time_zone(value, decoded, default_time_zone)
        zone = ZoneInfo(time_zone)
        if decoded.tzinfo is None:
            local = decoded.replace(tzinfo=zone)
        else:
            local = decoded.astimezone(zone)
        return {
            "kind": "datetime",
            "semantic": (
                "iana"
                if raw_tzid
                else ("floating" if decoded.tzinfo is None else "utc")
            ),
            "local": local.replace(tzinfo=None).isoformat(timespec="seconds"),
            "time_zone": time_zone,
            "utc": local.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        }
    if isinstance(decoded, date):
        return {
            "kind": "date",
            "semantic": "date",
            "date": decoded.isoformat(),
            "time_zone": default_time_zone,
        }
    raise SubscriptionItemError(
        "calendar_subscription_time_invalid",
        "来源事件时间格式不正确，已保留旧版本。",
    )


def _property_time_zone(value: Any, decoded: datetime, default_time_zone: str) -> str:
    raw_tzid = value.params.get("TZID") if hasattr(value, "params") else None
    if raw_tzid:
        time_zone = str(raw_tzid)
        try:
            ZoneInfo(time_zone)
        except ZoneInfoNotFoundError as exc:
            raise SubscriptionItemError(
                "calendar_subscription_timezone_unsupported",
                "来源事件使用了未知时区，已保留旧版本。",
            ) from exc
        return time_zone
    if decoded.tzinfo is None:
        return default_time_zone
    key = getattr(decoded.tzinfo, "key", None)
    if isinstance(key, str):
        try:
            ZoneInfo(key)
        except ZoneInfoNotFoundError:
            pass
        else:
            return key
    return "UTC"


def _event_duration(
    event: Any,
    start: dict[str, Any],
    *,
    default_time_zone: str,
) -> dict[str, Any]:
    end_property = event.get("DTEND")
    duration_property = event.get("DURATION")
    if end_property is not None and duration_property is not None:
        raise SubscriptionItemError(
            "calendar_subscription_duration_invalid",
            "来源事件不能同时包含 DTEND 和 DURATION，已保留旧版本。",
        )
    if start["kind"] == "date":
        start_date = date.fromisoformat(start["date"])
        if end_property is not None:
            end = _temporal_value(end_property, default_time_zone=default_time_zone)
            if end["kind"] != "date":
                raise SubscriptionItemError(
                    "calendar_subscription_duration_invalid",
                    "全天事件的结束时间类型不一致，已保留旧版本。",
                )
            duration = date.fromisoformat(end["date"]) - start_date
        elif duration_property is not None:
            duration = duration_property.dt
        else:
            duration = timedelta(days=1)
        if (
            not isinstance(duration, timedelta)
            or duration <= timedelta(0)
            or duration > MAX_EVENT_DURATION
            or duration.seconds
            or duration.microseconds
        ):
            raise SubscriptionItemError(
                "calendar_subscription_duration_invalid",
                "全天事件时长必须为 1 至 7 个完整日历日。",
            )
        return {"kind": "days", "value": duration.days}

    start_at = datetime.fromisoformat(start["utc"].replace("Z", "+00:00"))
    if end_property is not None:
        end = _temporal_value(end_property, default_time_zone=default_time_zone)
        if end["kind"] != "datetime":
            raise SubscriptionItemError(
                "calendar_subscription_duration_invalid",
                "来源事件的开始与结束时间类型不一致，已保留旧版本。",
            )
        duration = datetime.fromisoformat(end["utc"].replace("Z", "+00:00")) - start_at
    elif duration_property is not None:
        duration = duration_property.dt
    else:
        duration = timedelta(0)
    if (
        not isinstance(duration, timedelta)
        or duration < timedelta(0)
        or duration > MAX_EVENT_DURATION
    ):
        raise SubscriptionItemError(
            "calendar_subscription_duration_invalid",
            "来源事件时长必须大于 0 且不超过 7 天。",
        )
    return {"kind": "seconds", "value": int(duration.total_seconds())}


def _recurrence_rule(event: Any, start: dict[str, Any]) -> str | None:
    if event.get("EXRULE") is not None:
        raise SubscriptionItemError(
            "calendar_subscription_recurrence_unsupported",
            "来源事件使用了不支持的 EXRULE，已保留旧版本。",
        )
    property_value = event.get("RRULE")
    if property_value is None:
        return None
    frequency_values = property_value.get("FREQ", [])
    frequency = str(frequency_values[0]).upper() if len(frequency_values) == 1 else ""
    if frequency not in SUPPORTED_FREQUENCIES:
        raise SubscriptionItemError(
            "calendar_subscription_recurrence_unsupported",
            "来源事件的重复频率过密或不受支持，已保留旧版本。",
        )
    if any(len(values) > 128 for values in property_value.values()):
        raise SubscriptionItemError(
            "calendar_subscription_recurrence_too_complex",
            "来源事件重复规则过于复杂，已保留旧版本。",
        )
    rule = property_value.to_ical().decode("ascii")
    try:
        rrulestr(rule, dtstart=_start_datetime(start))
    except (TypeError, ValueError, OverflowError) as exc:
        raise SubscriptionItemError(
            "calendar_subscription_recurrence_invalid",
            "来源事件重复规则无法解析，已保留旧版本。",
        ) from exc
    return rule


def _date_list(value: Any, *, default_time_zone: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    properties = value if isinstance(value, list) else [value]
    values: list[dict[str, Any]] = []
    for property_value in properties:
        date_values = getattr(property_value, "dts", None)
        if date_values is None:
            raise SubscriptionItemError(
                "calendar_subscription_recurrence_invalid",
                "来源事件的例外日期无法解析，已保留旧版本。",
            )
        for item in date_values:
            values.append(_temporal_value(item, default_time_zone=default_time_zone))
            if len(values) > MAX_EXCEPTION_DATES:
                raise SubscriptionItemError(
                    "calendar_subscription_recurrence_too_complex",
                    "来源事件的例外日期数量超过限制，已保留旧版本。",
                )
    return values


def _recurrence_identity(value: Any) -> str:
    if value is None:
        return ""
    try:
        decoded = value.dt
        raw_tzid = value.params.get("TZID") if hasattr(value, "params") else None
    except (AttributeError, TypeError, ValueError):
        return "INVALID:" + hashlib.sha256(bytes(value.to_ical())).hexdigest()
    if isinstance(decoded, datetime):
        if raw_tzid:
            time_zone = str(raw_tzid)
            try:
                zone = ZoneInfo(time_zone)
            except ZoneInfoNotFoundError:
                return f"TZID:{time_zone}:{decoded.replace(tzinfo=None).isoformat()}"
            local = (
                decoded.replace(tzinfo=zone)
                if decoded.tzinfo is None
                else decoded.astimezone(zone)
            )
            utc_value = local.astimezone(UTC).isoformat().replace("+00:00", "Z")
            return f"INSTANT:{utc_value}"
        if decoded.tzinfo is None:
            return f"FLOATING:{decoded.isoformat(timespec='seconds')}"
        return "INSTANT:" + decoded.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(decoded, date):
        return f"DATE:{decoded.isoformat()}"
    return "INVALID:" + hashlib.sha256(bytes(value.to_ical())).hexdigest()


def _start_datetime(start: dict[str, Any]) -> datetime:
    zone = ZoneInfo(start["time_zone"])
    if start["kind"] == "date":
        return datetime.combine(date.fromisoformat(start["date"]), time.min, tzinfo=zone)
    return datetime.fromisoformat(start["local"]).replace(tzinfo=zone)


def _content_digest(baseline: dict[str, Any]) -> str:
    payload = json.dumps(
        baseline,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()
