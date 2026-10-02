import asyncio
import hashlib
from datetime import datetime
from pathlib import PurePath
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response
from sqlalchemy import and_, or_, select

from itime.ai.job_service import delete_media
from itime.ai.models import AIJob, TemporaryMedia
from itime.auth.security import now
from itime.auth.session import authorized
from itime.calendar.import_parsers import (
    MAX_FILE_BYTES,
    MAX_ICS_BYTES,
    ParsedCandidate,
    build_xlsx_template,
    parse_ics,
    parse_xlsx,
)
from itime.calendar.import_schemas import (
    ImportCandidateUpdate,
    ImportConfirm,
    ImportFilename,
    ImportUploadOperation,
)
from itime.calendar.import_service import (
    batch_result,
    batch_version_conflict,
    candidate_result,
    confirmed_series_result,
    default_import_time_zone,
    import_detail,
    locked_import_batch,
    locked_import_candidate,
    new_batch_expiry,
    prepare_candidate_rows,
    refresh_batch_state,
    require_editable_batch,
    term_name_map,
    validate_candidate,
)
from itime.calendar.models import (
    CalendarImportBatch,
    CalendarImportCandidate,
    CalendarImportIdentity,
    CalendarSeries,
    CalendarSource,
)
from itime.calendar.series_service import (
    find_series_conflicts,
    series_result,
    series_values,
)
from itime.errors import AppError
from itime.tasks.service import (
    emit_change,
    replay_operation,
    save_operation,
    validate_cursor,
)

router = APIRouter(prefix="/api/v1/calendar/imports", tags=["日历导入"])


def _file_format(filename: str) -> str:
    suffix = PurePath(filename).suffix.casefold()
    if suffix == ".xlsx":
        return "xlsx"
    if suffix == ".ics":
        return "ics"
    raise AppError(
        "unsupported_import_format",
        "仅支持 .xlsx 和 .ics 文件。",
        status=415,
    )


def _validate_media_type(request: Request, format_name: str) -> None:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    allowed = {
        "xlsx": {
            "application/octet-stream",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        },
        "ics": {
            "application/octet-stream",
            "application/ics",
            "text/calendar",
            "text/plain",
        },
    }
    if content_type and content_type not in allowed[format_name]:
        raise AppError(
            "invalid_import_media_type",
            "文件类型与扩展名不匹配，请重新选择文件。",
            status=415,
        )


@router.get("/template.xlsx")
async def download_excel_template(request: Request) -> Response:
    async with authorized(request):
        contents = await asyncio.to_thread(build_xlsx_template)
    return Response(
        content=contents,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="iTime-calendar-import-template.xlsx"'
        },
    )


@router.post("/files", status_code=201)
async def upload_calendar_file(
    request: Request,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
    operation_id: Annotated[UUID, Header(alias="X-Operation-ID")],
) -> dict:
    filename = ImportFilename(filename=filename).filename
    format_name = _file_format(filename)
    _validate_media_type(request, format_name)
    contents = await request.body()
    size_limit = MAX_FILE_BYTES if format_name == "xlsx" else MAX_ICS_BYTES
    if not contents or len(contents) > size_limit:
        limit_name = "5 MiB" if format_name == "xlsx" else "2 MiB"
        raise AppError(
            "import_file_too_large",
            f"{format_name.upper()} 文件必须大于 0 且小于 {limit_name}。",
            status=413,
        )
    content_hash = hashlib.sha256(contents).hexdigest()
    operation = ImportUploadOperation(
        operation_id=operation_id,
        filename=filename,
        format=format_name,
        content_sha256=content_hash,
    )

    async with authorized(request, write=True) as identity:
        replay = await replay_operation(
            identity.session,
            identity.user.id,
            operation_id,
            "calendar_import.upload",
            operation,
        )
        if replay is not None:
            return replay
        time_zone = await default_import_time_zone(
            identity.session,
            identity.user.id,
        )
        if format_name == "xlsx":
            names = await term_name_map(identity.session, identity.user.id)
            parsed = await asyncio.to_thread(
                parse_xlsx,
                contents,
                term_ids_by_name=names,
            )
            source_kind = "excel_file"
        else:
            parsed = await asyncio.to_thread(
                parse_ics,
                contents,
                default_time_zone=time_zone,
            )
            source_kind = "ics_file"

        candidates = await prepare_candidate_rows(
            identity.session,
            identity.user.id,
            format_name,
            parsed,
        )
        source = CalendarSource(
            owner_id=identity.user.id,
            kind=source_kind,
            name=filename,
        )
        identity.session.add(source)
        await identity.session.flush()
        batch = CalendarImportBatch(
            owner_id=identity.user.id,
            source_id=source.id,
            format=format_name,
            filename=filename,
            content_sha256=content_hash,
            candidate_count=len(candidates),
            expires_at=new_batch_expiry(),
        )
        identity.session.add(batch)
        await identity.session.flush()
        for candidate in candidates:
            candidate.batch_id = batch.id
        identity.session.add_all(candidates)
        await identity.session.flush()
        await refresh_batch_state(identity.session, batch)
        await identity.session.flush()
        detail = await import_detail(identity.session, batch)
        result = {
            "operation_id": operation_id,
            "batch": detail["batch"],
        }
        save_operation(
            identity.session,
            identity.user.id,
            operation_id,
            "calendar_import.upload",
            operation,
            result,
        )
        return result


@router.get("")
async def list_calendar_imports(
    request: Request,
    before_time: datetime | None = None,
    before_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict:
    validate_cursor(before_time, before_id)
    async with authorized(request) as identity:
        statement = select(CalendarImportBatch).where(
            CalendarImportBatch.owner_id == identity.user.id
        )
        if before_time is not None:
            statement = statement.where(
                or_(
                    CalendarImportBatch.updated_at < before_time,
                    and_(
                        CalendarImportBatch.updated_at == before_time,
                        CalendarImportBatch.id < before_id,
                    ),
                )
            )
        rows = list(
            await identity.session.scalars(
                statement.order_by(
                    CalendarImportBatch.updated_at.desc(),
                    CalendarImportBatch.id.desc(),
                ).limit(limit + 1)
            )
        )
        page = rows[:limit]
        source_ids = [batch.source_id for batch in page]
        sources = (
            {
                source.id: source
                for source in await identity.session.scalars(
                    select(CalendarSource).where(
                        CalendarSource.owner_id == identity.user.id,
                        CalendarSource.id.in_(source_ids),
                    )
                )
            }
            if source_ids
            else {}
        )
        batch_ids = [batch.id for batch in page]
        candidates: dict[UUID, list[CalendarImportCandidate]] = {
            batch_id: [] for batch_id in batch_ids
        }
        if batch_ids:
            candidate_rows = await identity.session.scalars(
                select(CalendarImportCandidate).where(
                    CalendarImportCandidate.owner_id == identity.user.id,
                    CalendarImportCandidate.batch_id.in_(batch_ids),
                )
            )
            for candidate in candidate_rows:
                candidates[candidate.batch_id].append(candidate)
        return {
            "items": [
                batch_result(batch, sources[batch.source_id], candidates[batch.id])
                for batch in page
            ],
            "next_cursor": (
                {
                    "before_time": page[-1].updated_at,
                    "before_id": page[-1].id,
                }
                if len(rows) > limit
                else None
            ),
        }


@router.get("/{batch_id}")
async def get_calendar_import(batch_id: UUID, request: Request) -> dict:
    async with authorized(request) as identity:
        batch = await identity.session.scalar(
            select(CalendarImportBatch).where(
                CalendarImportBatch.id == batch_id,
                CalendarImportBatch.owner_id == identity.user.id,
            )
        )
        if batch is None:
            raise AppError("calendar_import_not_found", "未找到此导入批次。", status=404)
        return await import_detail(identity.session, batch)


@router.put("/{batch_id}/candidates/{candidate_id}")
async def update_import_candidate(
    batch_id: UUID,
    candidate_id: UUID,
    data: ImportCandidateUpdate,
    request: Request,
) -> dict:
    scope = f"calendar_import.update:{batch_id}:{candidate_id}"
    async with authorized(request, write=True) as identity:
        replay = await replay_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
        )
        if replay is not None:
            return replay
        batch = await locked_import_batch(
            identity.session,
            identity.user.id,
            batch_id,
        )
        require_editable_batch(batch)
        if batch.version != data.expected_batch_version:
            raise batch_version_conflict(batch)
        candidate = await locked_import_candidate(
            identity.session,
            identity.user.id,
            batch_id,
            candidate_id,
        )
        if candidate.version != data.expected_version:
            raise AppError(
                "calendar_import_candidate_version_conflict",
                "此候选已在其他页面更新，请刷新后比较再保存。",
                status=409,
                details={"current_version": candidate.version},
            )
        if candidate.state in {"confirmed", "skipped"}:
            raise AppError(
                "calendar_import_candidate_final",
                "已确认或已跳过的候选不能再修改。",
                status=409,
            )

        parsed = ParsedCandidate(
            raw_data=candidate.raw_data,
            draft=data.model_dump(
                mode="json",
                exclude={
                    "operation_id",
                    "expected_batch_version",
                    "expected_version",
                },
            ),
            external_uid=candidate.external_uid,
            external_recurrence_id=candidate.external_recurrence_id,
        )
        replacement = (
            await prepare_candidate_rows(
                identity.session,
                identity.user.id,
                batch.format,
                [parsed],
            )
        )[0]
        if (
            replacement.state == "valid"
            and replacement.identity_key is not None
        ):
            same_batch = await identity.session.scalar(
                select(CalendarImportCandidate).where(
                    CalendarImportCandidate.owner_id == identity.user.id,
                    CalendarImportCandidate.batch_id == batch.id,
                    CalendarImportCandidate.id != candidate.id,
                    CalendarImportCandidate.identity_key == replacement.identity_key,
                    CalendarImportCandidate.state.in_(
                        ["valid", "duplicate", "confirmed"]
                    ),
                )
            )
            if same_batch is not None:
                replacement.state = "duplicate"
                replacement.duplicate_series_id = same_batch.confirmed_series_id
                replacement.warnings = [
                    *replacement.warnings,
                    {
                        "code": "duplicate_in_file",
                        "message": "同一批次中已有相同候选。",
                        "position": same_batch.position,
                    },
                ]
        candidate.state = replacement.state
        candidate.draft = replacement.draft
        candidate.identity_key = replacement.identity_key
        candidate.content_fingerprint = replacement.content_fingerprint
        candidate.warnings = replacement.warnings
        candidate.error_code = replacement.error_code
        candidate.error_message = replacement.error_message
        candidate.error_details = replacement.error_details
        candidate.duplicate_series_id = replacement.duplicate_series_id
        candidate.version += 1
        candidate.updated_at = now()
        batch.version += 1
        batch.updated_at = now()
        await refresh_batch_state(identity.session, batch)
        await identity.session.flush()
        detail = await import_detail(identity.session, batch)
        result = {
            "operation_id": data.operation_id,
            "batch": detail["batch"],
            "candidate": candidate_result(candidate),
        }
        save_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
            result,
        )
        return result


@router.post("/{batch_id}/confirm")
async def confirm_calendar_import(
    batch_id: UUID,
    data: ImportConfirm,
    request: Request,
) -> dict:
    scope = f"calendar_import.confirm:{batch_id}"
    async with authorized(request, write=True) as identity:
        replay = await replay_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
        )
        if replay is not None:
            return replay
        batch = await locked_import_batch(
            identity.session,
            identity.user.id,
            batch_id,
        )
        require_editable_batch(batch)
        if batch.version != data.expected_batch_version:
            raise batch_version_conflict(batch)

        decision_by_id = {decision.candidate_id: decision for decision in data.decisions}
        candidates = list(
            await identity.session.scalars(
                select(CalendarImportCandidate)
                .where(
                    CalendarImportCandidate.owner_id == identity.user.id,
                    CalendarImportCandidate.batch_id == batch.id,
                    CalendarImportCandidate.id.in_(decision_by_id),
                )
                .order_by(CalendarImportCandidate.id)
                .with_for_update()
            )
        )
        if len(candidates) != len(decision_by_id):
            raise AppError(
                "calendar_import_candidate_not_found",
                "部分导入候选不存在或不属于此批次。",
                status=404,
            )
        for candidate in candidates:
            decision = decision_by_id[candidate.id]
            if candidate.version != decision.expected_version:
                raise AppError(
                    "calendar_import_candidate_version_conflict",
                    "部分候选已在其他页面更新，请刷新后比较再确认。",
                    status=409,
                    details={
                        "candidate_id": str(candidate.id),
                        "current_version": candidate.version,
                    },
                )
            if candidate.state in {"confirmed", "skipped"}:
                raise AppError(
                    "calendar_import_candidate_final",
                    "部分候选已经确认或跳过，请刷新批次。",
                    status=409,
                    details={"candidate_id": str(candidate.id)},
                )

        outcomes: dict[UUID, dict] = {}
        created: list[tuple[CalendarImportCandidate, CalendarSeries, list[dict]]] = []
        ordered = sorted(candidates, key=lambda item: item.position)
        for candidate in ordered:
            decision = decision_by_id[candidate.id]
            if decision.action == "skip":
                candidate.state = "skipped"
                candidate.version += 1
                candidate.updated_at = now()
                outcomes[candidate.id] = {
                    "candidate_id": candidate.id,
                    "outcome": "skipped",
                }
                continue
            if candidate.state == "invalid":
                raise AppError(
                    "invalid_import_candidate",
                    "选中的候选仍有错误，请先校对或改为跳过。",
                    status=409,
                    details={"candidate_id": str(candidate.id)},
                )
            if candidate.state == "duplicate" and candidate.duplicate_series_id is None:
                raise AppError(
                    "duplicate_import_candidate",
                    "此候选与同批较早条目重复，请确认较早条目或跳过本条。",
                    status=409,
                    details={"candidate_id": str(candidate.id)},
                )
            if candidate.draft is None:
                raise AppError("invalid_import_candidate", "候选缺少可确认的结构化内容。")
            parsed = ParsedCandidate(
                raw_data=candidate.raw_data,
                draft=candidate.draft,
                external_uid=candidate.external_uid,
                external_recurrence_id=candidate.external_recurrence_id,
                warnings=[
                    warning
                    for warning in candidate.warnings
                    if warning.get("code")
                    not in {
                        "exact_duplicate",
                        "source_content_changed",
                        "possible_duplicate",
                        "duplicate_in_file",
                        "invalid_local_time",
                    }
                ],
            )
            (
                fields,
                resolved,
                fingerprint,
                identity_key,
                warnings,
                error,
            ) = await validate_candidate(
                identity.session,
                identity.user.id,
                batch.format,
                parsed,
            )
            if error is not None or fields is None or resolved is None:
                raise AppError(
                    "invalid_import_candidate",
                    error[1] if error is not None else "候选无法确认。",
                    status=409,
                    details={
                        "candidate_id": str(candidate.id),
                        **(error[2] or {} if error is not None else {}),
                    },
                )
            existing_identity = await identity.session.scalar(
                select(CalendarImportIdentity).where(
                    CalendarImportIdentity.owner_id == identity.user.id,
                    CalendarImportIdentity.identity_key == identity_key,
                )
            )
            if existing_identity is not None:
                existing_series = await identity.session.scalar(
                    select(CalendarSeries).where(
                        CalendarSeries.id == existing_identity.series_id,
                        CalendarSeries.owner_id == identity.user.id,
                    )
                )
                if existing_series is None:
                    raise RuntimeError("calendar import identity target is missing")
                candidate.state = "duplicate"
                candidate.duplicate_series_id = existing_series.id
                candidate.identity_key = identity_key
                candidate.content_fingerprint = fingerprint
                candidate.version += 1
                candidate.updated_at = now()
                outcomes[candidate.id] = {
                    "candidate_id": candidate.id,
                    "outcome": "merged",
                    "series": series_result(existing_series),
                    "warnings": warnings,
                    "conflicts": {"items": [], "has_more": False},
                }
                continue

            series = CalendarSeries(
                owner_id=identity.user.id,
                predecessor_id=None,
                source_id=batch.source_id,
                source_type=(
                    "screenshot" if batch.format == "screenshot" else "import"
                ),
                **series_values(fields, resolved),
            )
            identity.session.add(series)
            await identity.session.flush()
            identity.session.add(
                CalendarImportIdentity(
                    owner_id=identity.user.id,
                    source_id=batch.source_id,
                    series_id=series.id,
                    format=batch.format,
                    identity_key=identity_key,
                    content_fingerprint=fingerprint,
                    external_uid=candidate.external_uid,
                    external_recurrence_id=candidate.external_recurrence_id,
                )
            )
            emit_change(
                identity.session,
                identity.user.id,
                "calendar_series",
                series.id,
                "created",
                series.version,
            )
            candidate.state = "confirmed"
            candidate.confirmed_series_id = series.id
            candidate.identity_key = identity_key
            candidate.content_fingerprint = fingerprint
            candidate.warnings = warnings
            candidate.error_code = None
            candidate.error_message = None
            candidate.error_details = None
            candidate.version += 1
            candidate.updated_at = now()
            created.append((candidate, series, warnings))
            await identity.session.flush()

        if batch.origin_job_id is not None:
            media = await identity.session.scalar(
                select(TemporaryMedia)
                .join(AIJob, AIJob.input_media_id == TemporaryMedia.id)
                .where(
                    AIJob.id == batch.origin_job_id,
                    AIJob.owner_id == identity.user.id,
                    TemporaryMedia.owner_id == identity.user.id,
                )
                .with_for_update(of=TemporaryMedia)
            )
            if media is None:
                raise RuntimeError("screenshot import media is missing")
            delete_media(media, reason="confirmed")

        for candidate, series, warnings in created:
            outcomes[candidate.id] = confirmed_series_result(
                candidate,
                series,
                warnings=warnings,
                conflicts=await find_series_conflicts(
                    identity.session,
                    identity.user.id,
                    series,
                ),
            )

        batch.version += 1
        await refresh_batch_state(identity.session, batch)
        await identity.session.flush()
        detail = await import_detail(identity.session, batch)
        result = {
            "operation_id": data.operation_id,
            "batch": detail["batch"],
            "outcomes": [
                outcomes[decision.candidate_id] for decision in data.decisions
            ],
        }
        save_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
            result,
        )
        return result
