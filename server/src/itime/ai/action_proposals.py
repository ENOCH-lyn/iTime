"""AI tool-driven write-preview proposals."""
from datetime import datetime, timedelta
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

from itime.database import Base
from itime.errors import AppError
from itime.tasks.models import Task
from itime.tasks.progress_service import complete_task
from itime.tasks.service import emit_change, ensure_task_editable, task_detail

PROPOSAL_EXPIRY = timedelta(days=7)

TOOL_NAMES: tuple[str, ...] = (
    "create_task",
    "update_task",
    "complete_task",
    "start_review",
    "write_review_report",
    "save_memory",
    "update_memory",
    "trash_memory",
    "create_difficulty",
    "propose_plan",
    "confirm_plan",
    "update_plan_block",
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
    else:
        raise AppError(
            "ai_action_proposal_unsupported",
            "此 AI 预览类型暂不支持应用。",
            status=422,
            details={"tool_name": proposal.tool_name},
        )
    proposal.status = "applied"
    proposal.applied_at = current_time
    proposal.updated_at = current_time
    proposal.applied_resource_id = task.id
    proposal.applied_resource_version = task.version
    proposal.version += 1
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
