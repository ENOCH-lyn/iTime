"""AI tool-driven write-preview proposals."""
from datetime import date, datetime, time, timedelta
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from itime.calendar.exception_service import (
    exception_result,
    exception_values,
    find_occurrence_date,
    validate_exception_fields,
)
from itime.calendar.lifecycle_service import trash_calendar_series
from itime.calendar.models import AcademicTerm, CalendarException, CalendarSeries
from itime.calendar.schemas import (
    ExceptionCreate,
    SeriesFields,
    SeriesLifecycleInput,
)
from itime.calendar.schemas import (
    RecurrenceInput as SeriesRecurrenceInput,
)
from itime.calendar.series_service import (
    resolve_series_draft,
    series_result,
    series_values,
)
from itime.calendar.service import default_term_periods, default_term_time_zone, validate_term_dates
from itime.database import Base
from itime.errors import AppError
from itime.planning.plan_service import create_manual_plan_version, read_plan
from itime.planning.schemas import (
    ManualPlanBlockInput,
    ManualPlanVersionInput,
    ManualUnscheduledItemInput,
)
from itime.public_links.service import stop_public_link_fetches_for_task
from itime.tasks.lifecycle_service import expire_task_operation_responses
from itime.tasks.models import Task, TaskOccurrenceState, TaskSchedule
from itime.tasks.progress_service import complete_task
from itime.tasks.schedule_service import (
    resolve_task_schedule,
    task_schedule_values,
)
from itime.tasks.schemas import RecurrenceInput, TaskScheduleFields
from itime.tasks.service import (
    emit_change,
    ensure_task_editable,
    ensure_task_readable,
    task_detail,
)

TASK_TRASH_RETENTION = timedelta(days=30)

PROPOSAL_EXPIRY = timedelta(days=7)

TOOL_NAMES: tuple[str, ...] = (
    "create_task",
    "update_task",
    "complete_task",
    "delete_task",
    "delete_calendar_series",
    "update_calendar_series",
    "create_calendar_exception",
    "create_calendar_series",
    "create_term",
    "update_term",
    "start_review",
    "write_review_report",
    "save_memory",
    "update_memory",
    "trash_memory",
    "create_difficulty",
    "propose_plan",
    "confirm_plan",
    "update_plan_block",
    "create_plan_block",
    "delete_plan_block",
    "batch_update_plan_blocks",
    "create_timetable_courses",
)

STATUSES: tuple[str, ...] = ("pending", "applied", "rejected", "expired")


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{item}'" for item in values)


class ActionProposal(Base):
    __tablename__ = "ai_action_proposals"
    __table_args__ = (
        CheckConstraint(
            f"tool_name IN ({_quoted(TOOL_NAMES)})",
            name="ck_ai_action_proposals_tool_name",
        ),
        CheckConstraint(
            f"status IN ({_quoted(STATUSES)})",
            name="ck_ai_action_proposals_status",
        ),
        CheckConstraint(
            "char_length(btrim(operation_id)) BETWEEN 1 AND 64",
            name="ck_ai_action_proposals_operation_id",
        ),
        CheckConstraint("version >= 1", name="ck_ai_action_proposals_version"),
        ForeignKeyConstraint(
            ["conversation_id"],
            ["ai_conversations.id"],
            ondelete="CASCADE",
            name="fk_ai_action_proposals_conversation_id",
        ),
        UniqueConstraint(
            "owner_id",
            "conversation_id",
            "source_message_id",
            "tool_name",
            "operation_id",
            name="uq_ai_action_proposals_idempotency",
            postgresql_nulls_not_distinct=True,
        ),
        Index(
            "ix_ai_action_proposals_owner_status",
            "owner_id",
            "status",
            text("created_at DESC"),
            "id",
        ),
        Index(
            "ix_ai_action_proposals_expiry",
            "expires_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
    )
    conversation_id: Mapped[UUID]
    source_message_id: Mapped[UUID | None]
    tool_name: Mapped[str] = mapped_column(String(40))
    operation_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        String(16),
        default="pending",
        server_default=text("'pending'"),
    )
    preview: Mapped[dict] = mapped_column(JSONB)
    payload: Mapped[dict] = mapped_column(JSONB)
    expected_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now() + interval '7 days'"),
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_resource_id: Mapped[UUID | None]
    applied_resource_version: Mapped[int | None] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


def proposal_result(proposal: ActionProposal) -> dict:
    return {
        "id": proposal.id,
        "conversation_id": proposal.conversation_id,
        "source_message_id": proposal.source_message_id,
        "tool_name": proposal.tool_name,
        "operation_id": proposal.operation_id,
        "status": proposal.status,
        "preview": proposal.preview,
        "payload": proposal.payload,
        "expected_version": proposal.expected_version,
        "expires_at": proposal.expires_at,
        "applied_at": proposal.applied_at,
        "rejected_at": proposal.rejected_at,
        "applied_resource_id": proposal.applied_resource_id,
        "applied_resource_version": proposal.applied_resource_version,
        "version": proposal.version,
        "created_at": proposal.created_at,
        "updated_at": proposal.updated_at,
    }


async def create_proposal(
    session: AsyncSession,
    owner_id: UUID,
    *,
    conversation_id: UUID,
    source_message_id: UUID | None,
    tool_name: str,
    operation_id: str,
    preview: dict,
    payload: dict,
    expected_version: int | None,
    current_time: datetime,
) -> ActionProposal:
    if tool_name not in TOOL_NAMES:
        raise AppError(
            "ai_tool_name_invalid",
            "AI 工具名不在白名单。",
            status=400,
            details={"tool_name": tool_name},
        )
    expires_at = current_time + PROPOSAL_EXPIRY
    values = {
        "owner_id": owner_id,
        "conversation_id": conversation_id,
        "source_message_id": source_message_id,
        "tool_name": tool_name,
        "operation_id": operation_id,
        "status": "pending",
        "preview": preview,
        "payload": payload,
        "expected_version": expected_version,
        "expires_at": expires_at,
        "version": 1,
        "created_at": current_time,
        "updated_at": current_time,
    }
    stmt = (
        postgresql_insert(ActionProposal)
        .values(**values)
        .on_conflict_do_nothing(
            constraint="uq_ai_action_proposals_idempotency",
        )
    )
    await session.execute(stmt)
    reselect = select(ActionProposal).where(
        ActionProposal.owner_id == owner_id,
        ActionProposal.conversation_id == conversation_id,
        ActionProposal.tool_name == tool_name,
        ActionProposal.operation_id == operation_id,
    )
    if source_message_id is None:
        reselect = reselect.where(ActionProposal.source_message_id.is_(None))
    else:
        reselect = reselect.where(ActionProposal.source_message_id == source_message_id)
    proposal = await session.scalar(reselect)
    if proposal is None:
        raise AppError(
            "ai_tool_proposal_unavailable",
            "AI 预览暂存失败，请稍后重试。",
            status=503,
            retryable=True,
        )
    return proposal


async def get_proposal(
    session: AsyncSession,
    owner_id: UUID,
    proposal_id: UUID,
    *,
    lock: bool = False,
) -> ActionProposal:
    statement = select(ActionProposal).where(
        ActionProposal.id == proposal_id,
        ActionProposal.owner_id == owner_id,
    )
    if lock:
        statement = statement.with_for_update()
    proposal = await session.scalar(statement)
    if proposal is None:
        raise AppError(
            "ai_action_proposal_not_found",
            "未找到此 AI 预览。",
            status=404,
        )
    return proposal


def _mark_applied(
    proposal: ActionProposal,
    current_time: datetime,
    resource_id: UUID,
    resource_version: int,
) -> None:
    proposal.status = "applied"
    proposal.applied_at = current_time
    proposal.updated_at = current_time
    proposal.applied_resource_id = resource_id
    proposal.applied_resource_version = resource_version
    proposal.version += 1


async def apply_proposal(
    session: AsyncSession,
    owner_id: UUID,
    proposal_id: UUID,
    *,
    expected_version: int,
    current_time: datetime,
) -> dict:
    proposal = await get_proposal(session, owner_id, proposal_id, lock=True)
    if proposal.status == "applied":
        return {"proposal": proposal_result(proposal)}
    if proposal.status != "pending":
        raise AppError(
            "ai_action_proposal_not_pending",
            "此 AI 预览已处理，不能重复确认。",
            status=409,
            details={"status": proposal.status},
        )
    if proposal.expires_at <= current_time:
        proposal.status = "expired"
        proposal.updated_at = current_time
        proposal.version += 1
        raise AppError(
            "ai_action_proposal_expired",
            "此 AI 预览已过期，请重新生成。",
            status=409,
        )
    if proposal.version != expected_version:
        raise AppError(
            "ai_action_proposal_version_conflict",
            "此 AI 预览已在其他页面更新，请刷新后重试。",
            status=409,
            details={"current_version": proposal.version},
        )
    payload = proposal.payload
    if proposal.tool_name in {"create_term", "update_term"}:
        if proposal.tool_name == "create_term":
            start = date.fromisoformat(str(payload["start_date"]))
            weeks = int(payload.get("week_count") or 20)
            end = start + timedelta(days=weeks * 7 - 1)
            first_monday = start - timedelta(days=start.weekday())
            validate_term_dates(start, end, first_monday)
            term = AcademicTerm(
                owner_id=owner_id,
                name=str(payload["name"]).strip(),
                start_date=start,
                end_date=end,
                first_week_monday=first_monday,
                time_zone=await default_term_time_zone(session, owner_id),
            )
            session.add(term)
            await session.flush()
            session.add_all(default_term_periods(term.id, owner_id))
            await session.flush()
            emit_change(
                session,
                owner_id,
                "term",
                term.id,
                "created",
                term.version,
            )
        else:
            term = await session.scalar(
                select(AcademicTerm)
                .where(
                    AcademicTerm.id == UUID(str(payload["term_id"])),
                    AcademicTerm.owner_id == owner_id,
                )
                .with_for_update()
            )
            if term is None:
                raise AppError("term_not_found", "未找到此学期。", status=404)
            if "name" in payload and payload["name"]:
                term.name = str(payload["name"]).strip()
            if "start_date" in payload and payload["start_date"]:
                start = date.fromisoformat(str(payload["start_date"]))
                weeks = (
                    int(payload["week_count"])
                    if payload.get("week_count")
                    else ((term.end_date - term.start_date).days // 7) + 1
                )
                end = start + timedelta(days=weeks * 7 - 1)
                first_monday = start - timedelta(days=start.weekday())
                validate_term_dates(start, end, first_monday)
                term.start_date = start
                term.end_date = end
                term.first_week_monday = first_monday
            term.version += 1
            term.updated_at = current_time
            emit_change(
                session,
                owner_id,
                "term",
                term.id,
                "updated",
                term.version,
            )
        proposal.status = "applied"
        proposal.applied_at = current_time
        proposal.updated_at = current_time
        proposal.applied_resource_id = term.id
        proposal.applied_resource_version = term.version
        proposal.version += 1
        return {"proposal": proposal_result(proposal), "term_id": str(term.id)}
    if proposal.tool_name == "create_calendar_series" and "items" in payload:
        time_zone = await default_term_time_zone(session, owner_id)
        created = []
        for item in payload["items"]:
            repeat = str(item.get("repeat") or "none")
            recurrence = SeriesRecurrenceInput(
                frequency=repeat,
                interval=1,
                weekdays=[int(value) for value in item.get("repeat_weekdays") or []],
                until=(
                    date.fromisoformat(str(item["repeat_until"]))
                    if item.get("repeat_until")
                    else None
                ),
                count=item.get("repeat_count"),
            )
            fields = SeriesFields(
                kind="fixed",
                term_id=None,
                name=str(item["name"]),
                teacher=str(item.get("teacher") or ""),
                description=str(item.get("description") or ""),
                location=str(item.get("location") or ""),
                busy=True,
                time_zone=time_zone,
                start_date=date.fromisoformat(str(item["start_date"])),
                starts_at=time.fromisoformat(str(item["starts_at"])),
                ends_at=time.fromisoformat(str(item["ends_at"])),
                end_day_offset=0,
                recurrence=recurrence,
            )
            resolved = await resolve_series_draft(session, owner_id, fields)
            series = CalendarSeries(
                owner_id=owner_id,
                source_type="manual",
                source_id=None,
                course_group_id=None,
                **series_values(fields, resolved),
            )
            session.add(series)
            await session.flush()
            emit_change(
                session,
                owner_id,
                "calendar_series",
                series.id,
                "created",
                series.version,
            )
            created.append(series)
            if item.get("create_task"):
                start_at = time.fromisoformat(str(item["starts_at"]))
                end_at = time.fromisoformat(str(item["ends_at"]))
                duration = (
                    end_at.hour * 60 + end_at.minute
                    - start_at.hour * 60 - start_at.minute
                )
                task = Task(
                    id=uuid4(),
                    owner_id=owner_id,
                    project_id=None,
                    title=str(item["name"]),
                    description=str(item.get("description") or ""),
                    estimated_minutes=max(15, duration),
                    estimate_source="ai",
                    priority=0,
                    status="open",
                    version=1,
                    created_at=current_time,
                    updated_at=current_time,
                )
                session.add(task)
                await session.flush()
                emit_change(
                    session,
                    owner_id,
                    "task",
                    task.id,
                    "created",
                    task.version,
                )
                schedule_fields = TaskScheduleFields(
                    due_kind="datetime",
                    due_date=date.fromisoformat(str(item["start_date"])),
                    due_time=start_at,
                    time_zone=time_zone,
                    recurrence=RecurrenceInput(
                        frequency=repeat,
                        interval=1,
                        weekdays=[
                            int(value)
                            for value in item.get("repeat_weekdays") or []
                        ],
                        until=(
                            date.fromisoformat(str(item["repeat_until"]))
                            if item.get("repeat_until")
                            else None
                        ),
                        count=item.get("repeat_count"),
                    ),
                )
                resolved_schedule = await resolve_task_schedule(
                    session,
                    owner_id,
                    task,
                    schedule_fields,
                )
                session.add(
                    TaskSchedule(
                        task_id=task.id,
                        owner_id=owner_id,
                        predecessor_task_id=None,
                        **task_schedule_values(
                            schedule_fields,
                            resolved_schedule,
                        ),
                    )
                )
        if not created:
            raise AppError(
                "calendar_batch_empty",
                "没有可创建的活动。",
                status=400,
            )
        _mark_applied(
            proposal,
            current_time,
            created[0].id,
            created[0].version,
        )
        await session.flush()
        return {
            "proposal": proposal_result(proposal),
            "series": [series_result(series) for series in created],
        }
    if proposal.tool_name == "create_calendar_series":
        term = await session.scalar(
            select(AcademicTerm).where(
                AcademicTerm.id == UUID(str(payload["term_id"])),
                AcademicTerm.owner_id == owner_id,
            )
        )
        if term is None:
            raise AppError("term_not_found", "未找到此学期。", status=404)
        fields = SeriesFields(
            kind="course",
            term_id=term.id,
            name=str(payload["name"]),
            teacher=str(payload.get("teacher") or ""),
            description=str(payload.get("description") or ""),
            location=str(payload.get("location") or ""),
            busy=True,
            time_zone=term.time_zone,
            start_date=None,
            starts_at=time.fromisoformat(str(payload["starts_at"])),
            ends_at=time.fromisoformat(str(payload["ends_at"])),
            end_day_offset=0,
            recurrence=SeriesRecurrenceInput(
                frequency="weekly",
                interval=1,
                weekdays=[int(payload["weekday"])],
                week_numbers=[int(value) for value in payload["week_numbers"]],
            ),
        )
        resolved = await resolve_series_draft(session, owner_id, fields)
        series = CalendarSeries(
            owner_id=owner_id,
            source_type="manual",
            source_id=None,
            course_group_id=uuid4(),
            **series_values(fields, resolved),
        )
        session.add(series)
        await session.flush()
        emit_change(
            session,
            owner_id,
            "calendar_series",
            series.id,
            "created",
            series.version,
        )
        _mark_applied(proposal, current_time, series.id, series.version)
        await session.flush()
        return {
            "proposal": proposal_result(proposal),
            "series": series_result(series),
        }
    if proposal.tool_name == "create_timetable_courses":
        term = await session.scalar(
            select(AcademicTerm).where(
                AcademicTerm.id == UUID(str(payload["term_id"])),
                AcademicTerm.owner_id == owner_id,
            )
        )
        if term is None:
            raise AppError("term_not_found", "未找到此学期。", status=404)
        created = []
        for course in payload.get("courses") or []:
            week_numbers = sorted({int(value) for value in course["week_numbers"]})
            fields = SeriesFields(
                kind="course",
                term_id=term.id,
                name=str(course["name"]),
                teacher=str(course.get("teacher") or ""),
                description=str(course.get("description") or ""),
                location=str(course.get("location") or ""),
                busy=True,
                time_zone=term.time_zone,
                start_date=None,
                starts_at=time.fromisoformat(str(course["starts_at"])),
                ends_at=time.fromisoformat(str(course["ends_at"])),
                end_day_offset=0,
                recurrence=SeriesRecurrenceInput(
                    frequency="weekly",
                    interval=1,
                    weekdays=[int(course["weekday"])],
                    week_numbers=week_numbers,
                ),
            )
            resolved = await resolve_series_draft(session, owner_id, fields)
            series = CalendarSeries(
                owner_id=owner_id,
                source_type="manual",
                source_id=None,
                course_group_id=uuid4(),
                **series_values(fields, resolved),
            )
            session.add(series)
            await session.flush()
            emit_change(
                session,
                owner_id,
                "calendar_series",
                series.id,
                "created",
                series.version,
            )
            created.append(series)
        if not created:
            raise AppError(
                "timetable_courses_empty",
                "没有可写入的课程。",
                status=400,
            )
        _mark_applied(
            proposal,
            current_time,
            created[0].id,
            created[0].version,
        )
        await session.flush()
        return {
            "proposal": proposal_result(proposal),
            "series": [series_result(series) for series in created],
        }
    if proposal.tool_name == "batch_update_plan_blocks":
        plan = await read_plan(
            session,
            owner_id,
            UUID(str(payload["plan_id"])),
        )
        current = plan["current_version"]
        changes = {
            UUID(str(change["block_id"])): change
            for change in payload["changes"]
        }
        current_ids = {UUID(block["id"]) for block in current["blocks"]}
        if set(changes) - current_ids:
            raise AppError(
                "plan_block_not_found",
                "批次中有计划块已不存在，请重新生成。",
                status=409,
            )
        blocks: list[ManualPlanBlockInput] = []
        for block in current["blocks"]:
            change = changes.get(UUID(block["id"]))
            local_date = date.fromisoformat(block["local_date"])
            starts_at = time.fromisoformat(block["local_starts_at"])
            ends_at = time.fromisoformat(block["local_ends_at"])
            if change is not None:
                if change.get("local_date") is not None:
                    local_date = date.fromisoformat(str(change["local_date"]))
                if change.get("starts_at") is not None:
                    starts_at = time.fromisoformat(str(change["starts_at"]))
                if change.get("ends_at") is not None:
                    ends_at = time.fromisoformat(str(change["ends_at"]))
            if starts_at >= ends_at:
                raise AppError(
                    "invalid_plan_time",
                    "调整后的计划块结束必须晚于开始。",
                )
            if (
                starts_at.minute % 15
                or ends_at.minute % 15
                or starts_at.second
                or ends_at.second
            ):
                raise AppError(
                    "invalid_plan_time",
                    "计划块必须按 15 分钟对齐。",
                )
            blocks.append(
                ManualPlanBlockInput(
                    task_id=UUID(block["task_id"]),
                    occurrence_key=block["occurrence_key"],
                    local_date=local_date,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    step_ids=[UUID(step_id) for step_id in block["step_ids"]],
                )
            )
        unscheduled = [
            ManualUnscheduledItemInput(
                task_id=UUID(item["task_id"]),
                occurrence_key=item["occurrence_key"],
                remaining_minutes=int(item["remaining_minutes"]),
                reason=item["reason"],
                explanation=item["explanation"],
            )
            for item in current["unscheduled_items"]
        ]
        saved = await create_manual_plan_version(
            session,
            owner_id,
            ManualPlanVersionInput(
                operation_id=UUID(str(proposal.operation_id)),
                week_start_date=date.fromisoformat(plan["week_start_date"]),
                expected_plan_version=int(plan["version"]),
                density=current["density"],
                buffer_minutes=int(current["buffer_minutes"]),
                availability_start=time.fromisoformat(
                    current["availability_start"]
                ),
                availability_end=time.fromisoformat(
                    current["availability_end"]
                ),
                blocks=blocks,
                unscheduled_items=unscheduled,
            ),
        )
        saved_plan = saved["plan"]
        _mark_applied(
            proposal,
            current_time,
            UUID(saved_plan["id"]),
            int(saved_plan["version"]),
        )
        await session.flush()
        return {
            "proposal": proposal_result(proposal),
            "plan": saved_plan,
        }
    if proposal.tool_name == "delete_plan_block":
        plan = await read_plan(
            session,
            owner_id,
            UUID(str(payload["plan_id"])),
        )
        current = plan["current_version"]
        target = next(
            (
                item
                for item in current["blocks"]
                if item["id"] == str(payload["block_id"])
            ),
            None,
        )
        if target is None:
            raise AppError("plan_block_not_found", "未找到此计划块。", status=404)
        blocks = [
            ManualPlanBlockInput(
                task_id=UUID(block["task_id"]),
                occurrence_key=block["occurrence_key"],
                local_date=date.fromisoformat(block["local_date"]),
                starts_at=time.fromisoformat(block["local_starts_at"]),
                ends_at=time.fromisoformat(block["local_ends_at"]),
                step_ids=[UUID(step_id) for step_id in block["step_ids"]],
            )
            for block in current["blocks"]
            if block["id"] != target["id"]
        ]
        unscheduled = [
            ManualUnscheduledItemInput(
                task_id=UUID(item["task_id"]),
                occurrence_key=item["occurrence_key"],
                remaining_minutes=int(item["remaining_minutes"]),
                reason=item["reason"],
                explanation=item["explanation"],
            )
            for item in current["unscheduled_items"]
            if str(item["task_id"]) != str(target["task_id"])
        ]
        unscheduled.append(
            ManualUnscheduledItemInput(
                task_id=UUID(target["task_id"]),
                occurrence_key=target["occurrence_key"],
                remaining_minutes=max(1, int(target["estimated_minutes"])),
                reason="outside_scope",
                explanation="AI 已从本周计划中移除该计划块。",
            )
        )
        saved = await create_manual_plan_version(
            session,
            owner_id,
            ManualPlanVersionInput(
                operation_id=UUID(str(proposal.operation_id)),
                week_start_date=date.fromisoformat(plan["week_start_date"]),
                expected_plan_version=int(plan["version"]),
                density=current["density"],
                buffer_minutes=int(current["buffer_minutes"]),
                availability_start=time.fromisoformat(
                    current["availability_start"]
                ),
                availability_end=time.fromisoformat(
                    current["availability_end"]
                ),
                blocks=blocks,
                unscheduled_items=unscheduled,
            ),
        )
        saved_plan = saved["plan"]
        _mark_applied(
            proposal,
            current_time,
            UUID(saved_plan["id"]),
            int(saved_plan["version"]),
        )
        await session.flush()
        return {
            "proposal": proposal_result(proposal),
            "plan": saved_plan,
        }
    if proposal.tool_name == "create_plan_block":
        plan = await read_plan(
            session,
            owner_id,
            UUID(str(payload["plan_id"])),
        )
        current = plan["current_version"]
        task_id = UUID(str(payload["task_id"]))
        blocks = [
            ManualPlanBlockInput(
                task_id=UUID(block["task_id"]),
                occurrence_key=block["occurrence_key"],
                local_date=date.fromisoformat(block["local_date"]),
                starts_at=time.fromisoformat(block["local_starts_at"]),
                ends_at=time.fromisoformat(block["local_ends_at"]),
                step_ids=[UUID(step_id) for step_id in block["step_ids"]],
            )
            for block in current["blocks"]
        ]
        blocks.append(
            ManualPlanBlockInput(
                task_id=task_id,
                occurrence_key=None,
                local_date=date.fromisoformat(str(payload["local_date"])),
                starts_at=time.fromisoformat(str(payload["starts_at"])),
                ends_at=time.fromisoformat(str(payload["ends_at"])),
                step_ids=[],
            )
        )
        unscheduled = [
            ManualUnscheduledItemInput(
                task_id=UUID(item["task_id"]),
                occurrence_key=item["occurrence_key"],
                remaining_minutes=int(item["remaining_minutes"]),
                reason=item["reason"],
                explanation=item["explanation"],
            )
            for item in current["unscheduled_items"]
            if str(item["task_id"]) != str(task_id)
        ]
        saved = await create_manual_plan_version(
            session,
            owner_id,
            ManualPlanVersionInput(
                operation_id=UUID(str(proposal.operation_id)),
                week_start_date=date.fromisoformat(plan["week_start_date"]),
                expected_plan_version=int(plan["version"]),
                density=current["density"],
                buffer_minutes=int(current["buffer_minutes"]),
                availability_start=time.fromisoformat(
                    current["availability_start"]
                ),
                availability_end=time.fromisoformat(
                    current["availability_end"]
                ),
                blocks=blocks,
                unscheduled_items=unscheduled,
            ),
        )
        saved_plan = saved["plan"]
        _mark_applied(
            proposal,
            current_time,
            UUID(saved_plan["id"]),
            int(saved_plan["version"]),
        )
        await session.flush()
        return {
            "proposal": proposal_result(proposal),
            "plan": saved_plan,
        }
    if proposal.tool_name == "update_plan_block":
        plan = await read_plan(
            session,
            owner_id,
            UUID(str(payload["plan_id"])),
        )
        current = plan["current_version"]
        target = next(
            (
                item
                for item in current["blocks"]
                if item["id"] == str(payload["block_id"])
            ),
            None,
        )
        if target is None:
            raise AppError("plan_block_not_found", "未找到此计划块。", status=404)
        blocks: list[ManualPlanBlockInput] = []
        for block in current["blocks"]:
            local_date = block["local_date"]
            starts_at = block["local_starts_at"]
            ends_at = block["local_ends_at"]
            if block["id"] == target["id"]:
                local_date = str(payload.get("local_date") or local_date)
                starts_at = str(payload.get("starts_at") or starts_at)
                ends_at = str(payload.get("ends_at") or ends_at)
            blocks.append(
                ManualPlanBlockInput(
                    task_id=UUID(block["task_id"]),
                    occurrence_key=block["occurrence_key"],
                    local_date=date.fromisoformat(local_date),
                    starts_at=time.fromisoformat(starts_at),
                    ends_at=time.fromisoformat(ends_at),
                    step_ids=[UUID(step_id) for step_id in block["step_ids"]],
                )
            )
        unscheduled = [
            ManualUnscheduledItemInput(
                task_id=UUID(item["task_id"]),
                occurrence_key=item["occurrence_key"],
                remaining_minutes=int(item["remaining_minutes"]),
                reason=item["reason"],
                explanation=item["explanation"],
            )
            for item in current["unscheduled_items"]
        ]
        saved = await create_manual_plan_version(
            session,
            owner_id,
            ManualPlanVersionInput(
                operation_id=UUID(str(proposal.operation_id)),
                week_start_date=date.fromisoformat(plan["week_start_date"]),
                expected_plan_version=int(plan["version"]),
                density=current["density"],
                buffer_minutes=int(current["buffer_minutes"]),
                availability_start=time.fromisoformat(
                    current["availability_start"]
                ),
                availability_end=time.fromisoformat(
                    current["availability_end"]
                ),
                blocks=blocks,
                unscheduled_items=unscheduled,
            ),
        )
        saved_plan = saved["plan"]
        _mark_applied(
            proposal,
            current_time,
            UUID(saved_plan["id"]),
            int(saved_plan["version"]),
        )
        await session.flush()
        return {
            "proposal": proposal_result(proposal),
            "plan": saved_plan,
        }
    if proposal.tool_name == "create_calendar_exception":
        series_id = UUID(str(payload.get("series_id")))
        series = await session.scalar(
            select(CalendarSeries)
            .where(
                CalendarSeries.id == series_id,
                CalendarSeries.owner_id == owner_id,
            )
            .with_for_update()
        )
        if series is None:
            raise AppError("calendar_series_not_found", "未找到此课程。", status=404)
        occurrence_date = date.fromisoformat(str(payload["occurrence_date"]))
        key = f"{occurrence_date.isoformat()}T{series.starts_at.isoformat()}"
        original_date = await find_occurrence_date(
            session,
            owner_id,
            series,
            key,
        )
        existing = await session.scalar(
            select(CalendarException).where(
                CalendarException.owner_id == owner_id,
                CalendarException.series_id == series.id,
                CalendarException.original_start_date == original_date,
            )
        )
        if existing is not None:
            raise AppError(
                "calendar_exception_exists",
                "该次安排已经有单次修改。",
                status=409,
            )
        action = str(payload.get("action"))
        if action not in {"cancelled", "moved"}:
            raise AppError("invalid_exception_action", "单次安排动作不受支持。")
        if action == "moved":
            moved_date = date.fromisoformat(
                str(payload.get("moved_start_date") or occurrence_date)
            )
            moved_start = time.fromisoformat(
                str(payload.get("moved_starts_at") or series.starts_at)
            )
            moved_end = time.fromisoformat(
                str(payload.get("moved_ends_at") or series.ends_at)
            )
            moved_end_offset = series.end_day_offset
        else:
            moved_date = None
            moved_start = None
            moved_end = None
            moved_end_offset = None
        data = ExceptionCreate(
            operation_id=UUID(str(proposal.operation_id)),
            expected_series_version=series.version,
            occurrence_key=key,
            action=action,
            moved_start_date=moved_date,
            moved_starts_at=moved_start,
            moved_ends_at=moved_end,
            moved_end_day_offset=moved_end_offset,
            moved_time_zone=series.time_zone if action == "moved" else None,
        )
        moved_time_zone = validate_exception_fields(series, data)
        exception = CalendarException(
            series_id=series.id,
            owner_id=owner_id,
            occurrence_key=key,
            original_start_date=original_date,
            **exception_values(data, moved_time_zone),
        )
        session.add(exception)
        series.version += 1
        series.updated_at = current_time
        await session.flush()
        emit_change(
            session,
            owner_id,
            "calendar_exception",
            exception.id,
            "created",
            exception.version,
        )
        emit_change(
            session,
            owner_id,
            "calendar_series",
            series.id,
            "updated",
            series.version,
        )
        _mark_applied(proposal, current_time, series.id, series.version)
        await session.flush()
        return {
            "proposal": proposal_result(proposal),
            "series": series_result(series),
            "exception": exception_result(exception),
        }
    if proposal.tool_name == "update_calendar_series":
        series_id = UUID(str(payload.get("series_id")))
        series = await session.scalar(
            select(CalendarSeries)
            .where(
                CalendarSeries.id == series_id,
                CalendarSeries.owner_id == owner_id,
            )
            .with_for_update()
        )
        if series is None:
            raise AppError("calendar_series_not_found", "未找到此课程。", status=404)
        starts_at = series.starts_at
        ends_at = series.ends_at
        if payload.get("starts_at") is not None:
            starts_at = time.fromisoformat(str(payload["starts_at"]))
        if payload.get("ends_at") is not None:
            ends_at = time.fromisoformat(str(payload["ends_at"]))
        times_changed = (
            payload.get("starts_at") is not None
            or payload.get("ends_at") is not None
        )
        week_numbers = payload.get("week_numbers")
        weekday = payload.get("weekday")
        week_list = (
            list(week_numbers)
            if week_numbers is not None
            else list(series.course_week_numbers)
        )
        weekdays = list(series.recurrence_weekdays)
        if weekday is not None:
            if (
                series.kind != "course"
                or series.start_date is None
                or not week_list
            ):
                raise AppError(
                    "invalid_course_weekday",
                    "只有带周次的课程可以修改星期。",
                )
            first_week = min(week_list)
            base_monday = series.start_date - timedelta(
                days=(first_week - 1) * 7
                + (series.start_date.isoweekday() - 1)
            )
            start_date = base_monday + timedelta(
                days=(first_week - 1) * 7 + int(weekday) - 1
            )
            weekdays = [int(weekday)]
        else:
            start_date = None if week_numbers is not None else series.start_date
        fields = SeriesFields(
            kind=series.kind,
            term_id=series.term_id,
            name=str(payload.get("name") or series.name),
            teacher=(
                str(payload["teacher"])
                if payload.get("teacher") is not None
                else series.teacher
            ),
            description=(
                str(payload["description"])
                if payload.get("description") is not None
                else series.description
            ),
            location=(
                str(payload["location"])
                if payload.get("location") is not None
                else series.location
            ),
            busy=series.busy,
            time_zone=series.time_zone,
            start_date=start_date,
            starts_at=starts_at,
            ends_at=ends_at,
            end_day_offset=series.end_day_offset,
            start_period_number=(
                None if times_changed else series.start_period_number
            ),
            end_period_number=(
                None if times_changed else series.end_period_number
            ),
            ambiguous_utc_offset_minutes=series.ambiguous_offset_minutes,
            recurrence=SeriesRecurrenceInput(
                frequency=series.recurrence_frequency,
                interval=series.recurrence_interval,
                weekdays=weekdays,
                month=series.recurrence_month,
                day_of_month=series.recurrence_day_of_month,
                until=(
                    None
                    if series.kind == "course"
                    else series.recurrence_until
                ),
                count=(
                    None
                    if series.kind == "course"
                    else series.recurrence_count
                ),
                week_numbers=week_list,
            ),
        )
        resolved = await resolve_series_draft(session, owner_id, fields)
        values = series_values(fields, resolved)
        changed = any(
            getattr(series, field) != value for field, value in values.items()
        )
        if changed:
            for field, value in values.items():
                setattr(series, field, value)
            series.version += 1
            series.updated_at = current_time
            emit_change(
                session,
                owner_id,
                "calendar_series",
                series.id,
                "updated",
                series.version,
            )
        await session.flush()
        _mark_applied(proposal, current_time, series.id, series.version)
        await session.flush()
        return {
            "proposal": proposal_result(proposal),
            "series": series_result(series),
        }
    if proposal.tool_name == "delete_calendar_series":
        series_id = UUID(str(payload.get("series_id")))
        series = await session.scalar(
            select(CalendarSeries).where(
                CalendarSeries.id == series_id,
                CalendarSeries.owner_id == owner_id,
            )
        )
        if series is None:
            raise AppError("calendar_series_not_found", "未找到此课程。", status=404)
        deleted = await trash_calendar_series(
            session,
            owner_id,
            series.id,
            SeriesLifecycleInput(
                operation_id=UUID(str(proposal.operation_id)),
                expected_version=series.version,
            ),
            current_time=current_time,
        )
        series_payload = deleted["series"]
        _mark_applied(
            proposal,
            current_time,
            series.id,
            int(series_payload["version"]),
        )
        await session.flush()
        return {
            "proposal": proposal_result(proposal),
            "series": series_payload,
        }
    task_id = UUID(str(payload.get("task_id")))
    task = await session.scalar(
        select(Task).where(Task.id == task_id, Task.owner_id == owner_id)
    )
    if task is None:
        raise AppError("task_not_found", "未找到此任务。", status=404)
    if proposal.tool_name == "update_task":
        ensure_task_editable(task)
        title = payload.get("title")
        description = payload.get("description")
        estimated_minutes = payload.get("estimated_minutes")
        priority = payload.get("priority")
        if title is not None:
            task.title = str(title)
        if description is not None:
            task.description = str(description)
        if estimated_minutes is not None:
            task.estimated_minutes = int(estimated_minutes)
            task.estimate_source = "ai"
        if priority is not None:
            task.priority = int(priority)
        due_date_value = payload.get("due_date")
        if due_date_value is not None:
            schedule = await session.scalar(
                select(TaskSchedule)
                .where(
                    TaskSchedule.task_id == task.id,
                    TaskSchedule.owner_id == owner_id,
                )
                .with_for_update()
            )
            if schedule is None or schedule.time_zone is None:
                raise AppError(
                    "task_schedule_unavailable",
                    "此任务缺少时间设置，暂时不能由 AI 修改日期。",
                    status=409,
                )
            if schedule.recurrence_frequency != "none":
                raise AppError(
                    "ai_recurring_task_date_unsupported",
                    "暂不支持由 AI 修改重复任务的日期，请先在任务页调整重复规则。",
                    status=409,
                )
            fields = TaskScheduleFields(
                due_kind="date",
                due_date=date.fromisoformat(str(due_date_value)),
                due_time=None,
                time_zone=schedule.time_zone,
                splittable=schedule.splittable,
                minimum_block_minutes=schedule.minimum_block_minutes,
                recurrence=RecurrenceInput(),
            )
            resolved = await resolve_task_schedule(
                session,
                owner_id,
                task,
                fields,
            )
            for field, value in task_schedule_values(fields, resolved).items():
                setattr(schedule, field, value)
            schedule.updated_at = current_time
        repeat_value = payload.get("repeat")
        repeat_weekdays = payload.get("repeat_weekdays")
        repeat_until = payload.get("repeat_until")
        repeat_count = payload.get("repeat_count")
        if any(
            value is not None
            for value in (repeat_value, repeat_weekdays, repeat_until, repeat_count)
        ):
            schedule = await session.scalar(
                select(TaskSchedule)
                .where(
                    TaskSchedule.task_id == task.id,
                    TaskSchedule.owner_id == owner_id,
                )
                .with_for_update()
            )
            if schedule is None or schedule.time_zone is None:
                raise AppError(
                    "task_schedule_unavailable",
                    "此任务缺少时间设置，暂时不能由 AI 修改重复规则。",
                    status=409,
                )
            occurrence_state = await session.scalar(
                select(TaskOccurrenceState.id)
                .where(TaskOccurrenceState.task_id == task.id)
                .limit(1)
            )
            if occurrence_state is not None:
                raise AppError(
                    "task_occurrence_requires_manual_repeat",
                    "此任务已有实例记录，请先在任务页处理后再改重复规则。",
                    status=409,
                )
            frequency = str(repeat_value or schedule.recurrence_frequency)
            if frequency == "none" and (
                repeat_weekdays is not None
                or repeat_until is not None
                or repeat_count is not None
            ):
                raise AppError(
                    "invalid_task_schedule",
                    "取消重复时不能同时设置重复星期或结束条件。",
                )
            due_date_value = schedule.start_date
            if due_date_value is None:
                raise AppError(
                    "task_due_date_required",
                    "重复任务必须有截止日期。",
                )
            due_kind = (
                schedule.due_kind if schedule.due_kind != "none" else "date"
            )
            if frequency == "weekly":
                weekdays = list(repeat_weekdays or schedule.recurrence_weekdays)
                if not weekdays:
                    weekdays = [due_date_value.isoweekday()]
            else:
                weekdays = []
            same_frequency = frequency == schedule.recurrence_frequency
            until = repeat_until
            count = repeat_count
            if frequency == "none":
                until = None
                count = None
            else:
                if until is None and count is None and same_frequency:
                    until = schedule.recurrence_until
                    count = schedule.recurrence_count
                if until is None and count is None:
                    until = current_time.date() + timedelta(days=120)
            fields = TaskScheduleFields(
                due_kind=due_kind,
                due_date=due_date_value,
                due_time=schedule.due_local_time,
                time_zone=schedule.time_zone,
                splittable=schedule.splittable,
                minimum_block_minutes=schedule.minimum_block_minutes,
                recurrence=RecurrenceInput(
                    frequency=frequency,
                    interval=(
                        schedule.recurrence_interval if same_frequency else 1
                    ),
                    weekdays=weekdays,
                    until=until,
                    count=count,
                ),
            )
            resolved = await resolve_task_schedule(
                session,
                owner_id,
                task,
                fields,
            )
            for field, value in task_schedule_values(fields, resolved).items():
                setattr(schedule, field, value)
            schedule.updated_at = current_time
        task.version += 1
        task.updated_at = current_time
        emit_change(session, owner_id, "task", task.id, "updated", task.version)
        await session.flush()
        task_result = await task_detail(session, task)
    elif proposal.tool_name == "complete_task":
        task_result, _, _ = await complete_task(
            session,
            owner_id,
            task,
            None,
            None,
            changed_at=current_time,
        )
    elif proposal.tool_name == "delete_task":
        ensure_task_readable(task)
        if task.deleted_at is None:
            await expire_task_operation_responses(session, owner_id, task.id)
            await stop_public_link_fetches_for_task(
                session,
                task,
                at=current_time,
            )
            task.deleted_at = current_time
            task.purge_after = current_time + TASK_TRASH_RETENTION
            task.version += 1
            task.updated_at = current_time
            emit_change(session, owner_id, "task", task.id, "deleted", task.version)
        await session.flush()
        task_result = await task_detail(session, task)
    else:
        raise AppError(
            "ai_action_proposal_unsupported",
            "此 AI 预览类型暂不支持应用。",
            status=422,
            details={"tool_name": proposal.tool_name},
        )
    _mark_applied(proposal, current_time, task.id, task.version)
    await session.flush()
    return {
        "proposal": proposal_result(proposal),
        "task": task_result,
    }


async def reject_proposal(
    session: AsyncSession,
    owner_id: UUID,
    proposal_id: UUID,
    *,
    expected_version: int,
    current_time: datetime,
) -> dict:
    proposal = await get_proposal(session, owner_id, proposal_id, lock=True)
    if proposal.status == "applied":
        raise AppError(
            "ai_action_proposal_already_applied",
            "此 AI 预览已经确认，不能再拒绝。",
            status=409,
        )
    if proposal.status == "pending":
        if proposal.version != expected_version:
            raise AppError(
                "ai_action_proposal_version_conflict",
                "此 AI 预览已在其他页面更新，请刷新后重试。",
                status=409,
                details={"current_version": proposal.version},
            )
        proposal.status = "rejected"
        proposal.rejected_at = current_time
        proposal.updated_at = current_time
        proposal.version += 1
        await session.flush()
    return {"proposal": proposal_result(proposal)}


async def expire_proposals(
    session: AsyncSession,
    *,
    current_time: datetime,
) -> int:
    stmt = (
        update(ActionProposal)
        .where(
            ActionProposal.status == "pending",
            ActionProposal.expires_at <= current_time,
        )
        .values(status="expired", updated_at=current_time)
    )
    result = await session.execute(stmt)
    return result.rowcount or 0
