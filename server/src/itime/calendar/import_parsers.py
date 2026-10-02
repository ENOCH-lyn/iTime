import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any
from zipfile import BadZipFile, ZipFile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from icalendar import Calendar
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from itime.errors import AppError

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ICS_BYTES = 2 * 1024 * 1024
MAX_CANDIDATES = 500
MAX_XLSX_ENTRIES = 1000
MAX_XLSX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_XLSX_ENTRY_BYTES = 20 * 1024 * 1024
MAX_XLSX_COMPRESSION_RATIO = 100
MAX_XLSX_COLUMNS = 32
MAX_ICS_COMPONENTS = 2500
MAX_ICS_EVENT_PROPERTIES = 200

WEEKDAY_CODES = {
    "MO": 1,
    "TU": 2,
    "WE": 3,
    "TH": 4,
    "FR": 5,
    "SA": 6,
    "SU": 7,
}
WEEKDAY_NAMES = {
    "周一": 1,
    "星期一": 1,
    "周二": 2,
    "星期二": 2,
    "周三": 3,
    "星期三": 3,
    "周四": 4,
    "星期四": 4,
    "周五": 5,
    "星期五": 5,
    "周六": 6,
    "星期六": 6,
    "周日": 7,
    "周天": 7,
    "星期日": 7,
    "星期天": 7,
}
HEADER_ALIASES = {
    "名称": "name",
    "name": "name",
    "类型": "kind",
    "kind": "kind",
    "学期": "term_name",
    "term": "term_name",
    "开始日期": "start_date",
    "start_date": "start_date",
    "开始时间": "starts_at",
    "start_time": "starts_at",
    "结束时间": "ends_at",
    "end_time": "ends_at",
    "跨日": "end_day_offset",
    "end_day_offset": "end_day_offset",
    "开始节次": "start_period_number",
    "start_period": "start_period_number",
    "结束节次": "end_period_number",
    "end_period": "end_period_number",
    "重复": "frequency",
    "frequency": "frequency",
    "间隔": "interval",
    "interval": "interval",
    "星期": "weekdays",
    "weekdays": "weekdays",
    "重复结束日期": "until",
    "until": "until",
    "重复次数": "count",
    "count": "count",
    "周次": "week_numbers",
    "week_numbers": "week_numbers",
    "时区": "time_zone",
    "time_zone": "time_zone",
    "地点": "location",
    "location": "location",
    "说明": "description",
    "description": "description",
    "占用时间": "busy",
    "busy": "busy",
}
FREQUENCY_NAMES = {
    "不重复": "none",
    "none": "none",
    "每日": "daily",
    "daily": "daily",
    "工作日": "weekdays",
    "weekdays": "weekdays",
    "每周": "weekly",
    "weekly": "weekly",
    "每月": "monthly",
    "monthly": "monthly",
    "每年": "yearly",
    "yearly": "yearly",
}


@dataclass
class ParsedCandidate:
    raw_data: dict[str, Any]
    draft: dict[str, Any] | None = None
    warnings: list[dict[str, Any]] = field(default_factory=list)
    external_uid: str | None = None
    external_recurrence_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    error_details: dict[str, Any] | None = None


class CandidateError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def _warning(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **details}


def _safe_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    return str(value)


def _text(value: Any, *, maximum: int, required: bool = False) -> str:
    text_value = "" if value is None else str(value).strip()
    if required and not text_value:
        raise CandidateError("missing_required_value", "缺少必填内容。")
    if len(text_value) > maximum:
        raise CandidateError(
            "value_too_long",
            f"内容超过 {maximum} 个字符。",
            details={"maximum": maximum},
        )
    return text_value


def _date_value(value: Any, *, required: bool = False) -> date | None:
    if value in (None, ""):
        if required:
            raise CandidateError("invalid_date", "缺少日期。")
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise CandidateError("invalid_date", "日期必须使用 YYYY-MM-DD 格式。") from exc


def _time_value(value: Any, *, required: bool = False) -> time | None:
    if value in (None, ""):
        if required:
            raise CandidateError("invalid_time", "缺少时间。")
        return None
    if isinstance(value, datetime):
        result = value.time()
    elif isinstance(value, time):
        result = value
    else:
        try:
            result = time.fromisoformat(str(value).strip())
        except ValueError as exc:
            raise CandidateError("invalid_time", "时间必须使用 HH:MM 格式。") from exc
    if result.tzinfo is not None:
        raise CandidateError("invalid_time", "Excel 时间不能携带 UTC 偏移。")
    return result.replace(microsecond=0)


def _integer(
    value: Any,
    *,
    default: int | None = None,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        raise CandidateError("invalid_number", "数字格式不正确。")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise CandidateError("invalid_number", "数字格式不正确。") from exc
    if isinstance(value, float) and value != parsed:
        raise CandidateError("invalid_number", "数字必须是整数。")
    if minimum is not None and parsed < minimum or maximum is not None and parsed > maximum:
        raise CandidateError(
            "number_out_of_range",
            "数字超出允许范围。",
            details={"minimum": minimum, "maximum": maximum},
        )
    return parsed


def _boolean(value: Any, *, default: bool = True) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"是", "true", "1", "yes", "y"}:
        return True
    if normalized in {"否", "false", "0", "no", "n"}:
        return False
    raise CandidateError("invalid_boolean", "布尔值请填写“是”或“否”。")


def _number_list(value: Any, *, minimum: int, maximum: int) -> list[int]:
    if value in (None, ""):
        return []
    result: set[int] = set()
    chunks = re.split(r"[,，;；\\s]+", str(value).strip())
    for chunk in chunks:
        if not chunk:
            continue
        if chunk in WEEKDAY_NAMES:
            numbers = [WEEKDAY_NAMES[chunk]]
        elif "-" in chunk or "—" in chunk:
            parts = re.split(r"[-—]", chunk)
            if len(parts) != 2:
                raise CandidateError("invalid_number_list", "编号列表格式不正确。")
            start = _integer(parts[0], minimum=minimum, maximum=maximum)
            end = _integer(parts[1], minimum=minimum, maximum=maximum)
            if start is None or end is None or end < start:
                raise CandidateError("invalid_number_list", "编号范围格式不正确。")
            numbers = list(range(start, end + 1))
        else:
            number = _integer(chunk, minimum=minimum, maximum=maximum)
            if number is None:
                raise CandidateError("invalid_number_list", "编号列表格式不正确。")
            numbers = [number]
        result.update(numbers)
    return sorted(result)


def _excel_kind(value: Any) -> str:
    normalized = _text(value, maximum=20).lower()
    if normalized in {"", "固定安排", "fixed"}:
        return "fixed"
    if normalized in {"课程", "course"}:
        return "course"
    raise CandidateError("invalid_kind", "类型必须是“固定安排”或“课程”。")


def _excel_frequency(value: Any, *, kind: str) -> str:
    if kind == "course":
        return "weekly"
    normalized = _text(value, maximum=20).lower()
    try:
        return FREQUENCY_NAMES[normalized or "不重复"]
    except KeyError as exc:
        raise CandidateError("invalid_recurrence", "重复类型不受支持。") from exc


def _excel_candidate(
    values: dict[str, Any],
    *,
    term_ids_by_name: dict[str, str | None],
) -> ParsedCandidate:
    raw = {key: _safe_json_value(value) for key, value in values.items()}
    try:
        kind = _excel_kind(values.get("kind"))
        term_name = _text(values.get("term_name"), maximum=120)
        term_id = term_ids_by_name.get(term_name.casefold()) if term_name else None
        if kind == "course" and term_name and term_id is None:
            raise CandidateError(
                "term_not_found",
                "未找到唯一匹配的学期，请在候选中重新选择。",
                details={"term_name": term_name},
            )
        frequency = _excel_frequency(values.get("frequency"), kind=kind)
        start_period = _integer(
            values.get("start_period_number"),
            minimum=1,
            maximum=99,
        )
        end_period = _integer(
            values.get("end_period_number"),
            minimum=1,
            maximum=99,
        )
        uses_period = start_period is not None or end_period is not None
        if uses_period and (start_period is None or end_period is None):
            raise CandidateError("invalid_period_reference", "开始和结束节次必须同时填写。")
        start_date = _date_value(values.get("start_date"), required=kind == "fixed")
        starts_at = _time_value(values.get("starts_at"), required=not uses_period)
        ends_at = _time_value(values.get("ends_at"), required=not uses_period)
        weekdays = _number_list(values.get("weekdays"), minimum=1, maximum=7)
        week_numbers = _number_list(values.get("week_numbers"), minimum=1, maximum=60)
        draft = {
            "kind": kind,
            "term_id": term_id,
            "name": _text(values.get("name"), maximum=200, required=True),
            "description": _text(values.get("description"), maximum=20_000),
            "location": _text(values.get("location"), maximum=300),
            "busy": _boolean(values.get("busy")),
            "time_zone": _text(values.get("time_zone"), maximum=64) or None,
            "start_date": start_date.isoformat() if start_date is not None else None,
            "starts_at": starts_at.isoformat() if starts_at is not None else None,
            "ends_at": ends_at.isoformat() if ends_at is not None else None,
            "end_day_offset": _integer(
                values.get("end_day_offset"),
                default=0,
                minimum=0,
                maximum=1,
            ),
            "start_period_number": start_period,
            "end_period_number": end_period,
            "ambiguous_utc_offset_minutes": None,
            "recurrence": {
                "frequency": frequency,
                "interval": _integer(
                    values.get("interval"),
                    default=1,
                    minimum=1,
                    maximum=366,
                ),
                "weekdays": weekdays,
                "month": start_date.month if frequency == "yearly" and start_date else None,
                "day_of_month": (
                    start_date.day if frequency in {"monthly", "yearly"} and start_date else None
                ),
                "until": (
                    parsed_until.isoformat()
                    if (parsed_until := _date_value(values.get("until"))) is not None
                    else None
                ),
                "count": _integer(values.get("count"), minimum=1, maximum=366),
                "week_numbers": week_numbers,
            },
        }
        if kind == "course":
            draft["recurrence"].update(
                {
                    "frequency": "weekly",
                    "interval": 1,
                    "until": None,
                    "count": None,
                }
            )
        return ParsedCandidate(raw_data=raw, draft=draft)
    except CandidateError as exc:
        return ParsedCandidate(
            raw_data=raw,
            error_code=exc.code,
            error_message=exc.message,
            error_details=exc.details,
        )


def _validate_xlsx_archive(contents: bytes) -> None:
    try:
        with ZipFile(BytesIO(contents)) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > MAX_XLSX_ENTRIES:
                raise AppError("unsafe_xlsx", "Excel 文件结构过大或为空。")
            names: set[str] = set()
            total_size = 0
            for item in entries:
                path = PurePosixPath(item.filename)
                normalized = item.filename.lower()
                if (
                    item.filename in names
                    or path.is_absolute()
                    or ".." in path.parts
                    or any(
                        blocked in normalized
                        for blocked in (
                            "vbaproject",
                            "activex",
                            "oleobject",
                            "embeddings/",
                            "externallinks/",
                        )
                    )
                ):
                    raise AppError("unsafe_xlsx", "Excel 文件包含不支持的嵌入或外部内容。")
                names.add(item.filename)
                total_size += item.file_size
                if (
                    item.file_size > MAX_XLSX_ENTRY_BYTES
                    or total_size > MAX_XLSX_UNCOMPRESSED_BYTES
                    or (
                        item.file_size > 1024
                        and item.file_size > max(item.compress_size, 1) * MAX_XLSX_COMPRESSION_RATIO
                    )
                ):
                    raise AppError("unsafe_xlsx", "Excel 文件解压后的内容超过安全限制。")
    except BadZipFile as exc:
        raise AppError("invalid_xlsx", "Excel 文件已损坏或不是有效的 XLSX 文件。") from exc


def parse_xlsx(
    contents: bytes,
    *,
    term_ids_by_name: dict[str, str | None],
) -> list[ParsedCandidate]:
    if not contents or len(contents) > MAX_FILE_BYTES:
        raise AppError("import_file_too_large", "Excel 文件必须小于 5 MiB。", status=413)
    _validate_xlsx_archive(contents)
    try:
        workbook = load_workbook(
            BytesIO(contents),
            read_only=True,
            data_only=False,
            keep_links=False,
        )
    except Exception as exc:
        raise AppError("invalid_xlsx", "无法读取此 Excel 文件，请使用下载的模板。") from exc
    try:
        if "日历安排" not in workbook.sheetnames:
            raise AppError("invalid_xlsx_template", "Excel 中缺少“日历安排”工作表。")
        if len(workbook.sheetnames) > 4:
            raise AppError("invalid_xlsx_template", "Excel 工作表数量超过限制。")
        sheet = workbook["日历安排"]
        if sheet.max_row > MAX_CANDIDATES + 1 or sheet.max_column > MAX_XLSX_COLUMNS:
            raise AppError(
                "too_many_import_candidates",
                "日历工作表最多包含 500 条安排和 32 列。",
            )
        rows = sheet.iter_rows(
            min_row=1,
            max_row=MAX_CANDIDATES + 1,
            max_col=MAX_XLSX_COLUMNS,
        )
        try:
            header_row = next(rows)
        except StopIteration as exc:
            raise AppError("invalid_xlsx_template", "Excel 模板没有表头。") from exc
        if len(header_row) > MAX_XLSX_COLUMNS:
            raise AppError("invalid_xlsx_template", "Excel 表头列数超过限制。")
        headers: list[str | None] = []
        seen: set[str] = set()
        for cell in header_row:
            raw_header = _text(cell.value, maximum=80)
            if not raw_header:
                headers.append(None)
                continue
            canonical = HEADER_ALIASES.get(raw_header.strip().lower())
            if canonical is None:
                canonical = HEADER_ALIASES.get(raw_header.strip())
            if canonical is None:
                raise AppError(
                    "invalid_xlsx_template",
                    f"Excel 包含未知列“{raw_header}”，请使用最新模板。",
                )
            if canonical in seen:
                raise AppError("invalid_xlsx_template", "Excel 中存在重复列。")
            headers.append(canonical)
            seen.add(canonical)
        if "name" not in seen:
            raise AppError("invalid_xlsx_template", "Excel 缺少“名称”列。")

        candidates: list[ParsedCandidate] = []
        for row_number, row in enumerate(rows, start=2):
            if row_number > MAX_CANDIDATES + 1:
                if any(cell.value not in (None, "") for cell in row):
                    raise AppError(
                        "too_many_import_candidates",
                        "单次最多导入 500 条安排，请拆分文件。",
                    )
                continue
            values: dict[str, Any] = {}
            formula = False
            for index, cell in enumerate(row[: len(headers)]):
                header = headers[index]
                if header is None:
                    continue
                if cell.data_type == "f":
                    formula = True
                values[header] = cell.value
            if not any(value not in (None, "") for value in values.values()):
                continue
            if formula:
                candidates.append(
                    ParsedCandidate(
                        raw_data={key: _safe_json_value(value) for key, value in values.items()},
                        error_code="xlsx_formula_not_allowed",
                        error_message="导入单元格不能使用公式，请填写最终值。",
                        error_details={"row": row_number},
                    )
                )
            else:
                candidate = _excel_candidate(
                    values,
                    term_ids_by_name=term_ids_by_name,
                )
                candidate.raw_data["row"] = row_number
                candidates.append(candidate)
        if not candidates:
            raise AppError("empty_import_file", "文件中没有可导入的安排。")
        return candidates
    finally:
        workbook.close()


def _ics_property_text(event: Any, name: str, maximum: int) -> str:
    value = event.get(name)
    return _text(value, maximum=maximum) if value is not None else ""


def _ics_zone_and_start(
    property_value: Any,
    *,
    default_time_zone: str,
) -> tuple[date | datetime, str, list[dict[str, Any]], datetime | None]:
    value = property_value.dt
    warnings: list[dict[str, Any]] = []
    if isinstance(value, date) and not isinstance(value, datetime):
        return value, default_time_zone, warnings, None
    if not isinstance(value, datetime):
        raise CandidateError("invalid_ics_time", "ICS 开始时间格式不正确。")
    tzid = str(property_value.params.get("TZID", "")).strip()
    if tzid:
        try:
            ZoneInfo(tzid)
        except ZoneInfoNotFoundError as exc:
            raise CandidateError(
                "invalid_time_zone",
                "ICS 使用了系统不支持的时区。",
                details={"time_zone": tzid},
            ) from exc
        zone_name = tzid
    elif value.tzinfo is None:
        zone_name = default_time_zone
        warnings.append(
            _warning(
                "floating_time_assumed",
                f"未声明时区，已按当前时区 {default_time_zone} 解释。",
            )
        )
    elif value.utcoffset() == timedelta(0):
        zone_name = "UTC"
    else:
        zone_name = default_time_zone
        warnings.append(
            _warning(
                "fixed_offset_converted",
                f"固定偏移时间已换算到当前时区 {default_time_zone}。",
            )
        )
    if value.tzinfo is None:
        local = value
        aware_value = value.replace(tzinfo=ZoneInfo(zone_name))
    else:
        local = value.astimezone(ZoneInfo(zone_name)).replace(tzinfo=None)
        aware_value = value
    return local, zone_name, warnings, aware_value


def _ics_end(
    event: Any,
    start: date | datetime,
    time_zone: str,
    aware_start: datetime | None,
) -> tuple[date | datetime, timedelta]:
    end_property = event.get("DTEND")
    duration_property = event.get("DURATION")
    if end_property is not None and duration_property is not None:
        raise CandidateError("invalid_ics_time", "ICS 不能同时提供结束时间和持续时长。")
    if end_property is not None:
        value = end_property.dt
        if isinstance(start, datetime) and isinstance(value, datetime):
            if value.tzinfo is not None:
                local_end = value.astimezone(ZoneInfo(time_zone)).replace(tzinfo=None)
                duration = (
                    value.astimezone(UTC) - aware_start.astimezone(UTC)
                    if aware_start is not None
                    else local_end - start
                )
                return local_end, duration
            return value, value - start
        if (
            isinstance(start, date)
            and not isinstance(start, datetime)
            and isinstance(value, date)
            and not isinstance(value, datetime)
        ):
            return value, value - start
        raise CandidateError("invalid_ics_time", "ICS 开始与结束时间类型不一致。")
    if duration_property is not None:
        duration = duration_property.dt
        if not isinstance(duration, timedelta):
            raise CandidateError("invalid_ics_time", "ICS 持续时长格式不正确。")
        if isinstance(start, datetime) and aware_start is not None:
            end = (aware_start.astimezone(UTC) + duration).astimezone(ZoneInfo(time_zone))
            return end.replace(tzinfo=None), duration
        return start + duration, duration
    if isinstance(start, date) and not isinstance(start, datetime):
        return start + timedelta(days=1), timedelta(days=1)
    raise CandidateError("missing_ics_end", "ICS 事件缺少 DTEND 或 DURATION。")


def _single_rule_value(rule: dict[str, list[Any]], key: str) -> Any | None:
    values = rule.get(key, [])
    if not values:
        return None
    if len(values) != 1:
        raise CandidateError("unsupported_ics_recurrence", f"ICS {key} 只支持单个值。")
    return values[0]


def _rrule_draft(
    event: Any,
    *,
    start: date | datetime,
    time_zone: str,
) -> dict[str, Any]:
    rules = [value for key, value in event.property_items() if str(key).upper() == "RRULE"]
    if not rules:
        return {
            "frequency": "none",
            "interval": 1,
            "weekdays": [],
            "month": None,
            "day_of_month": None,
            "until": None,
            "count": None,
            "week_numbers": [],
        }
    if len(rules) != 1:
        raise CandidateError("unsupported_ics_recurrence", "一个事件只能包含一条 RRULE。")
    if any(event.get(name) is not None for name in ("EXDATE", "RDATE", "EXRULE")):
        raise CandidateError(
            "unsupported_ics_recurrence",
            "当前不能无损导入 EXDATE、RDATE 或 EXRULE，请校对后改为支持的规则。",
        )
    rule = {str(key).upper(): list(values) for key, values in dict(rules[0]).items()}
    supported = {"FREQ", "INTERVAL", "BYDAY", "BYMONTHDAY", "BYMONTH", "COUNT", "UNTIL", "WKST"}
    unsupported = sorted(set(rule) - supported)
    if unsupported:
        raise CandidateError(
            "unsupported_ics_recurrence",
            "ICS 重复规则包含当前不支持的字段。",
            details={"fields": unsupported},
        )
    frequency = str(_single_rule_value(rule, "FREQ") or "").upper()
    interval = int(_single_rule_value(rule, "INTERVAL") or 1)
    count_value = _single_rule_value(rule, "COUNT")
    until_value = _single_rule_value(rule, "UNTIL")
    if (count_value is None) == (until_value is None):
        raise CandidateError(
            "unsupported_ics_recurrence",
            "重复事件必须且只能包含 COUNT 或 UNTIL。",
        )
    if interval < 1 or interval > 366:
        raise CandidateError("unsupported_ics_recurrence", "重复间隔超出支持范围。")
    count = int(count_value) if count_value is not None else None
    if count is not None and not 1 <= count <= 366:
        raise CandidateError("unsupported_ics_recurrence", "重复次数最多为 366。")
    start_date = start.date() if isinstance(start, datetime) else start
    until: date | None = None
    if until_value is not None:
        if isinstance(until_value, datetime):
            if until_value.tzinfo is not None:
                local_until = until_value.astimezone(ZoneInfo(time_zone)).replace(tzinfo=None)
            else:
                local_until = until_value
            until = local_until.date()
            start_time = start.time() if isinstance(start, datetime) else time.min
            if local_until.time() < start_time:
                until -= timedelta(days=1)
        elif isinstance(until_value, date):
            until = until_value
        else:
            raise CandidateError("unsupported_ics_recurrence", "RRULE UNTIL 格式不正确。")
        if until < start_date:
            raise CandidateError("unsupported_ics_recurrence", "重复结束时间早于首次事件。")

    raw_days = [str(value).upper() for value in rule.get("BYDAY", [])]
    if any(value not in WEEKDAY_CODES for value in raw_days):
        raise CandidateError(
            "unsupported_ics_recurrence",
            "暂不支持带序号的 BYDAY 规则。",
        )
    weekdays = sorted({WEEKDAY_CODES[value] for value in raw_days})
    by_month_day = _single_rule_value(rule, "BYMONTHDAY")
    by_month = _single_rule_value(rule, "BYMONTH")
    wkst = str(_single_rule_value(rule, "WKST") or "MO").upper()
    if wkst != "MO" and interval > 1:
        raise CandidateError(
            "unsupported_ics_recurrence",
            "间隔超过一周时只支持周一作为周起点。",
        )

    if frequency == "DAILY" and weekdays == [1, 2, 3, 4, 5] and interval == 1:
        mapped_frequency = "weekdays"
        weekdays = []
    elif frequency == "DAILY" and not weekdays and by_month_day is None and by_month is None:
        mapped_frequency = "daily"
    elif frequency == "WEEKLY" and by_month_day is None and by_month is None:
        mapped_frequency = "weekly"
        weekdays = weekdays or [start_date.isoweekday()]
    elif frequency == "MONTHLY" and not weekdays and by_month is None:
        mapped_frequency = "monthly"
        by_month_day = by_month_day or start_date.day
    elif frequency == "YEARLY" and not weekdays:
        mapped_frequency = "yearly"
        by_month = by_month or start_date.month
        by_month_day = by_month_day or start_date.day
    else:
        raise CandidateError(
            "unsupported_ics_recurrence",
            "此 ICS 重复规则无法无损转换为 iTime 规则。",
        )
    if by_month_day is not None and not 1 <= int(by_month_day) <= 31:
        raise CandidateError("unsupported_ics_recurrence", "只支持正数 BYMONTHDAY。")
    if by_month is not None and not 1 <= int(by_month) <= 12:
        raise CandidateError("unsupported_ics_recurrence", "BYMONTH 超出范围。")
    return {
        "frequency": mapped_frequency,
        "interval": interval,
        "weekdays": weekdays,
        "month": int(by_month) if mapped_frequency == "yearly" else None,
        "day_of_month": (int(by_month_day) if mapped_frequency in {"monthly", "yearly"} else None),
        "until": until.isoformat() if until is not None else None,
        "count": count,
        "week_numbers": [],
    }


def _ics_candidate(event: Any, *, default_time_zone: str, position: int) -> ParsedCandidate:
    uid: str | None = None
    recurrence_id: Any = None
    raw: dict[str, Any] = {"event": position}
    try:
        parser_errors = getattr(event, "errors", [])
        if parser_errors:
            raise CandidateError(
                "invalid_ics_event",
                "ICS 事件包含无法解析的属性。",
                details={
                    "fields": sorted({str(field).upper() for field, _message in parser_errors})
                },
            )
        property_names = [
            str(name).upper()
            for name, _value in event.property_items()
            if str(name).upper() not in {"BEGIN", "END"}
        ]
        duplicates = sorted(
            {
                name
                for name in {
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
                if property_names.count(name) > 1
            }
        )
        if duplicates:
            raise CandidateError(
                "invalid_ics_event",
                "ICS 事件包含重复的单值属性。",
                details={"fields": duplicates},
            )
        raw_uid = event.get("UID")
        raw_summary = event.get("SUMMARY")
        raw.update(
            {
                "uid": str(raw_uid)[:512] if raw_uid is not None else None,
                "summary": str(raw_summary)[:200] if raw_summary is not None else "",
            }
        )
        uid = _ics_property_text(event, "UID", 512) or None
        recurrence_id_property = event.get("RECURRENCE-ID")
        recurrence_id = (
            _safe_json_value(recurrence_id_property.dt)
            if recurrence_id_property is not None
            else None
        )
        raw["recurrence_id"] = recurrence_id
        if len(event.property_items()) > MAX_ICS_EVENT_PROPERTIES:
            raise CandidateError("ics_event_too_large", "单个 ICS 事件属性过多。")
        if recurrence_id is not None:
            raise CandidateError(
                "unsupported_ics_override",
                "带 RECURRENCE-ID 的单次覆盖不能无损导入，请校对为独立安排。",
            )
        if str(event.get("STATUS", "")).upper() == "CANCELLED":
            raise CandidateError("ics_event_cancelled", "来源事件已取消，不会作为新安排导入。")
        start_property = event.get("DTSTART")
        if start_property is None:
            raise CandidateError("missing_ics_start", "ICS 事件缺少 DTSTART。")
        start, time_zone, warnings, aware_start = _ics_zone_and_start(
            start_property,
            default_time_zone=default_time_zone,
        )
        if (
            isinstance(start, datetime)
            and event.get("DURATION") is not None
            and event.get("RRULE") is not None
            and time_zone != "UTC"
        ):
            raise CandidateError(
                "unsupported_ics_duration_recurrence",
                "非 UTC 重复事件的 DURATION 可能跨夏令时变化，当前不能无损导入。",
            )
        end, actual_duration = _ics_end(event, start, time_zone, aware_start)
        if isinstance(start, datetime) and isinstance(end, datetime):
            wall_duration = end - start
            local_start_date = start.date()
            starts_at = start.time().replace(microsecond=0)
            ends_at = end.time().replace(microsecond=0)
            end_day_offset = (end.date() - start.date()).days
        elif (
            isinstance(start, date)
            and not isinstance(start, datetime)
            and isinstance(end, date)
            and not isinstance(end, datetime)
        ):
            wall_duration = end - start
            local_start_date = start
            starts_at = time.min
            ends_at = time.min
            end_day_offset = wall_duration.days
            warnings.append(
                _warning(
                    "all_day_event",
                    "全天事件将作为当天 00:00 至次日 00:00 的占用时间导入。",
                )
            )
        else:
            raise CandidateError("invalid_ics_time", "ICS 开始与结束时间类型不一致。")
        if (
            actual_duration <= timedelta(0)
            or actual_duration > timedelta(days=1)
            or wall_duration <= timedelta(0)
            or wall_duration > timedelta(days=1)
            or end_day_offset not in {0, 1}
        ):
            raise CandidateError(
                "unsupported_ics_duration",
                "只支持大于 0 且不超过 24 小时的事件。",
            )
        recurrence = _rrule_draft(event, start=start, time_zone=time_zone)
        draft = {
            "kind": "fixed",
            "term_id": None,
            "name": _ics_property_text(event, "SUMMARY", 200) or "未命名安排",
            "description": _ics_property_text(event, "DESCRIPTION", 20_000),
            "location": _ics_property_text(event, "LOCATION", 300),
            "busy": str(event.get("TRANSP", "OPAQUE")).upper() != "TRANSPARENT",
            "time_zone": time_zone,
            "start_date": local_start_date.isoformat(),
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
            "end_day_offset": end_day_offset,
            "start_period_number": None,
            "end_period_number": None,
            "ambiguous_utc_offset_minutes": None,
            "recurrence": recurrence,
        }
        ignored = [
            name
            for name in ("VALARM", "ATTENDEE", "ORGANIZER", "CATEGORIES", "URL", "ATTACH")
            if event.get(name) is not None
            or any(component.name == name for component in event.subcomponents)
        ]
        if ignored:
            warnings.append(
                _warning(
                    "ignored_ics_properties",
                    "部分不属于日程时间的 ICS 字段不会导入。",
                    fields=ignored,
                )
            )
        return ParsedCandidate(
            raw_data=raw,
            draft=draft,
            warnings=warnings,
            external_uid=uid,
            external_recurrence_id=str(recurrence_id) if recurrence_id is not None else None,
        )
    except CandidateError as exc:
        return ParsedCandidate(
            raw_data=raw,
            external_uid=uid,
            external_recurrence_id=str(recurrence_id) if recurrence_id is not None else None,
            error_code=exc.code,
            error_message=exc.message,
            error_details=exc.details,
        )
    except (AttributeError, OverflowError, TypeError, ValueError):
        return ParsedCandidate(
            raw_data=raw,
            external_uid=uid,
            external_recurrence_id=(str(recurrence_id) if recurrence_id is not None else None),
            error_code="invalid_ics_event",
            error_message="ICS 事件字段格式不正确，请校对后重试。",
        )


def parse_ics(contents: bytes, *, default_time_zone: str) -> list[ParsedCandidate]:
    if not contents or len(contents) > MAX_ICS_BYTES:
        raise AppError("import_file_too_large", "ICS 文件必须小于 2 MiB。", status=413)
    try:
        text_value = contents.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AppError("invalid_ics_encoding", "ICS 文件必须使用 UTF-8 编码。") from exc
    if "\x00" in text_value:
        raise AppError("invalid_ics", "ICS 文件包含无效字符。")
    try:
        calendar = Calendar.from_ical(text_value)
    except Exception as exc:
        raise AppError("invalid_ics", "ICS 文件已损坏或格式不正确。") from exc
    components = list(calendar.walk())
    if len(components) > MAX_ICS_COMPONENTS:
        raise AppError("ics_file_too_complex", "ICS 文件组件数量超过限制。")
    events = [component for component in components if component.name == "VEVENT"]
    if not events:
        raise AppError("empty_import_file", "ICS 文件中没有日历事件。")
    if len(events) > MAX_CANDIDATES:
        raise AppError(
            "too_many_import_candidates",
            "单次最多导入 500 条安排，请拆分文件。",
        )
    return [
        _ics_candidate(event, default_time_zone=default_time_zone, position=index)
        for index, event in enumerate(events, start=1)
    ]


def build_xlsx_template() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "日历安排"
    headers = [
        "名称",
        "类型",
        "学期",
        "开始日期",
        "开始时间",
        "结束时间",
        "跨日",
        "开始节次",
        "结束节次",
        "重复",
        "间隔",
        "星期",
        "重复结束日期",
        "重复次数",
        "周次",
        "时区",
        "地点",
        "说明",
        "占用时间",
    ]
    sheet.append(headers)
    sheet.append(
        [
            "高等数学",
            "固定安排",
            "",
            date(2026, 9, 7),
            time(8, 0),
            time(9, 30),
            0,
            "",
            "",
            "每周",
            1,
            "1,3",
            date(2027, 1, 15),
            "",
            "",
            "Asia/Shanghai",
            "教学楼 A101",
            "示例行可删除",
            "是",
        ]
    )
    header_fill = PatternFill("solid", fgColor="2D6A5F")
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center")
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = "A1:S2"
    widths = [24, 14, 20, 14, 12, 12, 9, 12, 12, 12, 9, 18, 16, 12, 18, 20, 24, 36, 12]
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[chr(64 + index)].width = width
    for row in range(2, MAX_CANDIDATES + 2):
        sheet.cell(row, 4).number_format = "yyyy-mm-dd"
        sheet.cell(row, 5).number_format = "hh:mm"
        sheet.cell(row, 6).number_format = "hh:mm"
        sheet.cell(row, 13).number_format = "yyyy-mm-dd"
    kind_validation = DataValidation(
        type="list",
        formula1='"固定安排,课程"',
        allow_blank=True,
    )
    repeat_validation = DataValidation(
        type="list",
        formula1='"不重复,每日,工作日,每周,每月,每年"',
        allow_blank=True,
    )
    busy_validation = DataValidation(type="list", formula1='"是,否"', allow_blank=True)
    sheet.add_data_validation(kind_validation)
    sheet.add_data_validation(repeat_validation)
    sheet.add_data_validation(busy_validation)
    kind_validation.add(f"B2:B{MAX_CANDIDATES + 1}")
    repeat_validation.add(f"J2:J{MAX_CANDIDATES + 1}")
    busy_validation.add(f"S2:S{MAX_CANDIDATES + 1}")

    instructions = workbook.create_sheet("填写说明")
    instructions.append(["字段", "填写规则"])
    instructions.append(["名称", "必填，最多 200 字。"])
    instructions.append(["类型", "固定安排或课程；课程必须填写已有学期及星期、周次。"])
    instructions.append(["日期时间", "日期 YYYY-MM-DD，时间 HH:MM；跨日只能填 0 或 1。"])
    instructions.append(["节次", "课程可填写连续开始/结束节次，此时开始/结束时间留空。"])
    instructions.append(["重复", "重复安排必须只填写结束日期或重复次数之一，最多 366 次。"])
    instructions.append(["星期/周次", "用逗号或范围分隔，例如 1,3,5 或 1-16。"])
    instructions.append(["时区", "可留空使用当前时区，也可填写 IANA 时区，如 Asia/Shanghai。"])
    instructions.append(["安全", "不要使用公式、宏、外部链接或嵌入对象。"])
    instructions.column_dimensions["A"].width = 18
    instructions.column_dimensions["B"].width = 88
    instructions.freeze_panes = "A2"
    for cell in instructions[1]:
        cell.fill = header_fill
        cell.font = Font(color="FFFFFF", bold=True)

    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()
