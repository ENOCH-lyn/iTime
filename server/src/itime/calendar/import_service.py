import hashlib
import json
from collections import Counter, defaultdict
from datetime import timedelta
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.auth.security import now
from itime.calendar.import_parsers import ParsedCandidate
from itime.calendar.models import (
    AcademicTerm,
    CalendarImportBatch,
    CalendarImportCandidate,
    CalendarImportIdentity,
    CalendarSeries,
    CalendarSource,
    UserTimeSettings,
)
from itime.calendar.schemas import SeriesFields
from itime.calendar.series_service import (
    ResolvedSeriesDraft,
    resolve_series_draft,
    series_result,
    series_values,
)
from itime.errors import AppError

IMPORT_REVIEW_DAYS = 7


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def semantic_series_payload(
    fields: SeriesFields,
    resolved: ResolvedSeriesDraft,
) -> dict:
    values = series_values(fields, resolved)
    return {
        "kind": values["kind"],
        "term_id": str(values["term_id"]) if values["term_id"] is not None else None,
        "name": " ".join(values["name"].split()).casefold(),
        "description": values["description"],
        "location": " ".join(values["location"].split()).casefold(),
        "busy": values["busy"],
        "time_zone": values["time_zone"],
        "start_date": values["start_date"].isoformat(),
        "starts_at": values["starts_at"].isoformat(),
        "ends_at": values["ends_at"].isoformat(),
        "end_day_offset": values["end_day_offset"],
        "start_period_number": values["start_period_number"],
        "end_period_number": values["end_period_number"],
        "recurrence_frequency": values["recurrence_frequency"],
        "recurrence_interval": values["recurrence_interval"],
        "recurrence_weekdays": values["recurrence_weekdays"],
        "recurrence_month": values["recurrence_month"],
        "recurrence_day_of_month": values["recurrence_day_of_month"],
        "recurrence_until": (
            values["recurrence_until"].isoformat()
            if values["recurrence_until"] is not None
            else None
        ),
        "recurrence_count": values["recurrence_count"],
        "course_first_week_monday": (
            values["course_first_week_monday"].isoformat()
            if values["course_first_week_monday"] is not None
            else None
        ),
        "course_week_numbers": values["course_week_numbers"],
        "ambiguous_offset_minutes": values["ambiguous_offset_minutes"],
    }


def content_fingerprint(
    fields: SeriesFields,
    resolved: ResolvedSeriesDraft,
) -> str:
    return sha256_text(canonical_json(semantic_series_payload(fields, resolved)))


def import_identity_key(
    format_name: str,
    fingerprint: str,
    *,
    external_uid: str | None,
    external_recurrence_id: str | None,
) -> str:
    if external_uid is None:
        return sha256_text(f"{format_name}\0fingerprint\0{fingerprint}")
    return sha256_text(f"{format_name}\0uid\0{external_uid}\0{external_recurrence_id or ''}")


def series_semantic_payload(series: CalendarSeries) -> dict:
    return {
        "kind": series.kind,
        "term_id": str(series.term_id) if series.term_id is not None else None,
        "name": " ".join(series.name.split()).casefold(),
        "description": series.description,
        "location": " ".join(series.location.split()).casefold(),
        "busy": series.busy,
        "time_zone": series.time_zone,
        "start_date": series.start_date.isoformat(),
        "starts_at": series.starts_at.isoformat(),
        "ends_at": series.ends_at.isoformat(),
        "end_day_offset": series.end_day_offset,
        "start_period_number": series.start_period_number,
        "end_period_number": series.end_period_number,
        "recurrence_frequency": series.recurrence_frequency,
        "recurrence_interval": series.recurrence_interval,
        "recurrence_weekdays": series.recurrence_weekdays,
        "recurrence_month": series.recurrence_month,
        "recurrence_day_of_month": series.recurrence_day_of_month,
        "recurrence_until": (
            series.recurrence_until.isoformat() if series.recurrence_until is not None else None
        ),
        "recurrence_count": series.recurrence_count,
        "course_first_week_monday": (
            series.course_first_week_monday.isoformat()
            if series.course_first_week_monday is not None
            else None
        ),
        "course_week_numbers": series.course_week_numbers,
        "ambiguous_offset_minutes": series.ambiguous_offset_minutes,
    }


def series_content_fingerprint(series: CalendarSeries) -> str:
    return sha256_text(canonical_json(series_semantic_payload(series)))


def validation_error(exc: ValidationError) -> tuple[str, str, dict]:
    return (
        "invalid_candidate",
        "候选字段格式不正确，请校对后重试。",
        {
            "fields": [
                {"path": ".".join(map(str, error["loc"])), "type": error["type"]}
                for error in exc.errors()
            ]
        },
    )


async def default_import_time_zone(session: AsyncSession, owner_id: UUID) -> str:
    value = await session.scalar(
        select(UserTimeSettings.time_zone).where(UserTimeSettings.owner_id == owner_id)
    )
    if value is None:
        raise AppError(
            "time_settings_required",
            "请先保存当前使用的时区，再导入日历文件。",
            status=409,
        )
    return value


async def term_name_map(
    session: AsyncSession,
    owner_id: UUID,
) -> dict[str, str | None]:
    terms = list(
        await session.scalars(select(AcademicTerm).where(AcademicTerm.owner_id == owner_id))
    )
    grouped: defaultdict[str, list[AcademicTerm]] = defaultdict(list)
    for term in terms:
        grouped[" ".join(term.name.split()).casefold()].append(term)
    return {name: str(rows[0].id) if len(rows) == 1 else None for name, rows in grouped.items()}


async def validate_candidate(
    session: AsyncSession,
    owner_id: UUID,
    format_name: str,
    parsed: ParsedCandidate,
) -> tuple[
    SeriesFields | None,
    ResolvedSeriesDraft | None,
    str | None,
    str | None,
    list[dict],
    tuple[str, str, dict | None] | None,
]:
    if parsed.draft is None:
        return (
            None,
            None,
            None,
            None,
            parsed.warnings,
            (
                parsed.error_code or "invalid_candidate",
                parsed.error_message or "此候选无法解析，请校对后重试。",
                parsed.error_details,
            ),
        )
    try:
        fields = SeriesFields.model_validate(parsed.draft)
        resolved = await resolve_series_draft(session, owner_id, fields)
    except ValidationError as exc:
        return None, None, None, None, parsed.warnings, validation_error(exc)
    except AppError as exc:
        return (
            None,
            None,
            None,
            None,
            parsed.warnings,
            (exc.code, exc.message, exc.details or None),
        )
    fingerprint = content_fingerprint(fields, resolved)
    identity_key = import_identity_key(
        format_name,
        fingerprint,
        external_uid=parsed.external_uid,
        external_recurrence_id=parsed.external_recurrence_id,
    )
    warnings = [
        *parsed.warnings,
        *[
            {
                "code": "invalid_local_time",
                "message": "该次安排位于夏令时跳过的本地时间，未自动平移。",
                "occurrence_key": key,
            }
            for key in resolved.warning_occurrence_keys
        ],
    ]
    return fields, resolved, fingerprint, identity_key, warnings, None


async def prepare_candidate_rows(
    session: AsyncSession,
    owner_id: UUID,
    format_name: str,
    parsed_candidates: list[ParsedCandidate],
) -> list[CalendarImportCandidate]:
    prepared: list[
        tuple[
            ParsedCandidate,
            SeriesFields | None,
            ResolvedSeriesDraft | None,
            str | None,
            str | None,
            list[dict],
            tuple[str, str, dict | None] | None,
        ]
    ] = []
    for parsed in parsed_candidates:
        validation = await validate_candidate(
            session,
            owner_id,
            format_name,
            parsed,
        )
        prepared.append((parsed, *validation))

    identity_keys = [item[4] for item in prepared if item[4] is not None]
    identities = (
        {
            identity.identity_key: identity
            for identity in await session.scalars(
                select(CalendarImportIdentity).where(
                    CalendarImportIdentity.owner_id == owner_id,
                    CalendarImportIdentity.identity_key.in_(identity_keys),
                )
            )
        }
        if identity_keys
        else {}
    )
    names = {" ".join(item[1].name.split()).casefold() for item in prepared if item[1] is not None}
    possible_series: defaultdict[str, list[CalendarSeries]] = defaultdict(list)
    if names:
        rows = await session.scalars(
            select(CalendarSeries)
            .where(
                CalendarSeries.owner_id == owner_id,
                CalendarSeries.deleted_at.is_(None),
                CalendarSeries.purged_at.is_(None),
                func.lower(func.btrim(CalendarSeries.name)).in_(names),
            )
            .order_by(CalendarSeries.updated_at.desc(), CalendarSeries.id.desc())
            .limit(1001)
        )
        for series in rows:
            possible_series[" ".join(series.name.split()).casefold()].append(series)

    seen_keys: dict[str, int] = {}
    candidates: list[CalendarImportCandidate] = []
    for position, item in enumerate(prepared, start=1):
        parsed, fields, resolved, fingerprint, identity_key, warnings, error = item
        state = "invalid" if error is not None else "valid"
        duplicate_series_id = None
        if error is None and identity_key is not None:
            existing_identity = identities.get(identity_key)
            if existing_identity is not None:
                state = "duplicate"
                duplicate_series_id = existing_identity.series_id
                warnings.append(
                    {
                        "code": (
                            "source_content_changed"
                            if existing_identity.content_fingerprint != fingerprint
                            else "exact_duplicate"
                        ),
                        "message": (
                            "相同来源标识已经导入，但内容有变化；文件导入不会覆盖已有安排。"
                            if existing_identity.content_fingerprint != fingerprint
                            else "此安排已经导入，不会重复创建。"
                        ),
                        "series_id": str(existing_identity.series_id),
                    }
                )
            elif identity_key in seen_keys:
                state = "duplicate"
                warnings.append(
                    {
                        "code": "duplicate_in_file",
                        "message": "同一文件中已有相同候选，仅保留首次出现项用于确认。",
                        "position": seen_keys[identity_key],
                    }
                )
            else:
                seen_keys[identity_key] = position
                matching = [
                    series
                    for series in possible_series.get(
                        " ".join(fields.name.split()).casefold(),
                        [],
                    )
                    if series_content_fingerprint(series) == fingerprint
                ][:5]
                if matching:
                    warnings.append(
                        {
                            "code": "possible_duplicate",
                            "message": "已有内容相同的手工安排，请确认是否仍要保留两条。",
                            "series_ids": [str(series.id) for series in matching],
                        }
                    )

        candidate = CalendarImportCandidate(
            owner_id=owner_id,
            position=position,
            state=state,
            raw_data=parsed.raw_data,
            draft=(
                fields.model_dump(mode="json")
                if fields is not None and resolved is not None
                else parsed.draft
            ),
            identity_key=identity_key,
            content_fingerprint=fingerprint,
            external_uid=parsed.external_uid,
            external_recurrence_id=parsed.external_recurrence_id,
            warnings=warnings,
            error_code=error[0] if error is not None else None,
            error_message=error[1] if error is not None else None,
            error_details=error[2] if error is not None else None,
            duplicate_series_id=duplicate_series_id,
        )
        candidates.append(candidate)
    return candidates


def source_result(source: CalendarSource) -> dict:
    return {
        "id": source.id,
        "kind": "image" if source.kind == "screenshot" else source.kind,
        "name": source.name,
        "version": source.version,
        "created_at": source.created_at,
        "updated_at": source.updated_at,
    }


def candidate_result(candidate: CalendarImportCandidate) -> dict:
    return {
        "id": candidate.id,
        "position": candidate.position,
        "state": candidate.state,
        "raw_data": candidate.raw_data,
        "draft": candidate.draft,
        "external_uid": candidate.external_uid,
        "external_recurrence_id": candidate.external_recurrence_id,
        "warnings": candidate.warnings,
        "error": (
            {
                "code": candidate.error_code,
                "message": candidate.error_message,
                "details": candidate.error_details or {},
            }
            if candidate.error_code is not None
            else None
        ),
        "duplicate_series_id": candidate.duplicate_series_id,
        "confirmed_series_id": candidate.confirmed_series_id,
        "version": candidate.version,
        "created_at": candidate.created_at,
        "updated_at": candidate.updated_at,
    }


def effective_batch_state(batch: CalendarImportBatch) -> str:
    if (
        batch.state not in {"confirmed", "cancelled", "failed", "expired"}
        and batch.expires_at <= now()
    ):
        return "expired"
    return batch.state


def batch_result(
    batch: CalendarImportBatch,
    source: CalendarSource,
    candidates: list[CalendarImportCandidate],
) -> dict:
    counts = Counter(candidate.state for candidate in candidates)
    return {
        "id": batch.id,
        "source": source_result(source),
        "format": batch.source_type,
        "source_type": batch.source_type,
        "source_display": batch.source_display,
        "filename": batch.filename,
        "state": effective_batch_state(batch),
        "candidate_count": batch.candidate_count,
        "counts": {
            key: counts.get(key, 0)
            for key in ("valid", "invalid", "duplicate", "confirmed", "skipped")
        },
        "input_summary": batch.input_summary,
        "conversation_id": batch.conversation_id,
        "message_id": batch.message_id,
        "job_id": batch.origin_job_id,
        "error": (
            {
                "code": batch.error_code,
                "message": batch.error_message,
            }
            if batch.error_code is not None
            else None
        ),
        "version": batch.version,
        "expires_at": batch.expires_at,
        "created_at": batch.created_at,
        "updated_at": batch.updated_at,
    }


async def import_detail(
    session: AsyncSession,
    batch: CalendarImportBatch,
) -> dict:
    source = await session.get(CalendarSource, batch.source_id)
    if source is None:
        raise RuntimeError("calendar import source is missing")
    candidates = list(
        await session.scalars(
            select(CalendarImportCandidate)
            .where(
                CalendarImportCandidate.owner_id == batch.owner_id,
                CalendarImportCandidate.batch_id == batch.id,
            )
            .order_by(CalendarImportCandidate.position)
        )
    )
    return {
        "batch": batch_result(batch, source, candidates),
        "candidates": [candidate_result(candidate) for candidate in candidates],
    }


async def locked_import_batch(
    session: AsyncSession,
    owner_id: UUID,
    batch_id: UUID,
) -> CalendarImportBatch:
    batch = await session.scalar(
        select(CalendarImportBatch)
        .where(
            CalendarImportBatch.id == batch_id,
            CalendarImportBatch.owner_id == owner_id,
        )
        .with_for_update()
    )
    if batch is None:
        raise AppError("calendar_import_not_found", "未找到此导入批次。", status=404)
    return batch


def require_editable_batch(batch: CalendarImportBatch) -> None:
    state = effective_batch_state(batch)
    if state == "expired":
        raise AppError(
            "calendar_import_expired",
            "导入候选已过期，请重新提交。",
            status=409,
        )
    if state == "confirmed":
        raise AppError(
            "calendar_import_completed",
            "此导入批次已经处理完成。",
            status=409,
        )
    if state in {"queued", "processing"}:
        raise AppError(
            "calendar_import_processing",
            "导入内容仍在处理中，请稍后刷新。",
            status=409,
            retryable=True,
        )
    if state in {"cancelled", "failed"}:
        raise AppError(
            f"calendar_import_{state}",
            "此导入批次已取消。" if state == "cancelled" else "此导入批次处理失败。",
            status=409,
        )


def batch_version_conflict(batch: CalendarImportBatch) -> AppError:
    return AppError(
        "calendar_import_version_conflict",
        "导入候选已在其他页面更新，请刷新后比较再继续。",
        status=409,
        details={"current_version": batch.version},
    )


async def locked_import_candidate(
    session: AsyncSession,
    owner_id: UUID,
    batch_id: UUID,
    candidate_id: UUID,
) -> CalendarImportCandidate:
    candidate = await session.scalar(
        select(CalendarImportCandidate)
        .where(
            CalendarImportCandidate.id == candidate_id,
            CalendarImportCandidate.owner_id == owner_id,
            CalendarImportCandidate.batch_id == batch_id,
        )
        .with_for_update()
    )
    if candidate is None:
        raise AppError("calendar_import_candidate_not_found", "未找到此导入候选。", status=404)
    return candidate


def candidate_version_conflict(candidate: CalendarImportCandidate) -> AppError:
    return AppError(
        "calendar_import_candidate_version_conflict",
        "此候选已在其他页面更新，请刷新后比较再保存。",
        status=409,
        details={"current_version": candidate.version},
    )


async def refresh_batch_state(
    session: AsyncSession,
    batch: CalendarImportBatch,
) -> None:
    states = list(
        await session.scalars(
            select(CalendarImportCandidate.state).where(
                CalendarImportCandidate.owner_id == batch.owner_id,
                CalendarImportCandidate.batch_id == batch.id,
            )
        )
    )
    unresolved = any(state in {"valid", "invalid"} for state in states)
    batch.state = "needs_review" if unresolved else "confirmed"
    batch.updated_at = now()


def new_batch_expiry():
    return now() + timedelta(days=IMPORT_REVIEW_DAYS)


def confirmed_series_result(
    candidate: CalendarImportCandidate,
    series: CalendarSeries,
    *,
    warnings: list[dict],
    conflicts: dict,
) -> dict:
    return {
        "candidate_id": candidate.id,
        "outcome": "created",
        "series": series_result(series),
        "warnings": warnings,
        "conflicts": conflicts,
    }
