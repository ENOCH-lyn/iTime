"""Write-preview AI tool handlers.

Each handler builds an `ActionProposal` (or planning Proposal) representing a
preview of the write; the model gets back `{proposal_id, preview,
requires_confirmation: True}` and nothing is applied until the user confirms
via the apply endpoint (D3).
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from itime.ai.action_proposals import create_proposal

from .schemas import (
    CompleteTaskArgs,
    ConfirmPlanArgs,
    CreateDifficultyArgs,
    CreateTaskArgs,
    ProposePlanArgs,
    SaveMemoryArgs,
    StartReviewArgs,
    TrashMemoryArgs,
    UpdateMemoryArgs,
    UpdatePlanBlockArgs,
    UpdateTaskArgs,
    WriteReviewReportArgs,
)


def _preview(summary: str, details: dict | None = None) -> dict:
    body: dict = {"summary": summary}
    if details is not None:
        body["details"] = details
    return body


def _payload(model: BaseModel) -> dict:
    return model.model_dump(mode="json", exclude={"operation_id"})


def _response(proposal) -> dict:
    return {
        "proposal_id": str(proposal.id),
        "preview": proposal.preview,
        "requires_confirmation": True,
        "status": proposal.status,
        "expires_at": proposal.expires_at.isoformat(),
    }


async def _record(
    session: AsyncSession,
    owner_id: UUID,
    conversation_id: UUID,
    source_message_id: UUID | None,
    tool_name: str,
    args: BaseModel,
    *,
    preview: dict,
    expected_version: int | None,
    current_time: datetime,
) -> dict:
    proposal = await create_proposal(
        session,
        owner_id,
        conversation_id=conversation_id,
        source_message_id=source_message_id,
        tool_name=tool_name,
        operation_id=args.operation_id,
        preview=preview,
        payload=_payload(args),
        expected_version=expected_version,
        current_time=current_time,
    )
    return _response(proposal)


async def handle_create_task(
    session: AsyncSession,
    owner_id: UUID,
    args: dict,
    *,
    conversation_id: UUID,
    source_message_id: UUID | None,
    current_time: datetime,
) -> dict:
    payload = CreateTaskArgs.model_validate(args)
    preview = _preview(
        f"创建任务「{payload.title}」",
        {
            "title": payload.title,
            "due_at": payload.due_at.isoformat() if payload.due_at else None,
            "estimated_minutes": payload.estimated_minutes,
            "tags": payload.tags or [],
        },
    )
    return await _record(
        session,
        owner_id,
        conversation_id,
        source_message_id,
        "create_task",
        payload,
        preview=preview,
        expected_version=None,
        current_time=current_time,
    )


async def handle_update_task(
    session: AsyncSession,
    owner_id: UUID,
    args: dict,
    *,
    conversation_id: UUID,
    source_message_id: UUID | None,
    current_time: datetime,
) -> dict:
    payload = UpdateTaskArgs.model_validate(args)
    preview = _preview(
        "更新任务字段",
        {"task_id": str(payload.task_id), "patch": payload.patch},
    )
    return await _record(
        session,
        owner_id,
        conversation_id,
        source_message_id,
        "update_task",
        payload,
        preview=preview,
        expected_version=payload.expected_version,
        current_time=current_time,
    )


async def handle_complete_task(
    session: AsyncSession,
    owner_id: UUID,
    args: dict,
    *,
    conversation_id: UUID,
    source_message_id: UUID | None,
    current_time: datetime,
) -> dict:
    payload = CompleteTaskArgs.model_validate(args)
    preview = _preview(
        "标记任务完成",
        {
            "task_id": str(payload.task_id),
            "actual_minutes": payload.actual_minutes,
            "note": payload.note,
        },
    )
    return await _record(
        session,
        owner_id,
        conversation_id,
        source_message_id,
        "complete_task",
        payload,
        preview=preview,
        expected_version=payload.expected_version,
        current_time=current_time,
    )


async def handle_start_review(
    session: AsyncSession,
    owner_id: UUID,
    args: dict,
    *,
    conversation_id: UUID,
    source_message_id: UUID | None,
    current_time: datetime,
) -> dict:
    payload = StartReviewArgs.model_validate(args)
    preview = _preview(
        f"开启 {payload.date.isoformat()} 的复盘会话",
        {"date": payload.date.isoformat()},
    )
    return await _record(
        session,
        owner_id,
        conversation_id,
        source_message_id,
        "start_review",
        payload,
        preview=preview,
        expected_version=None,
        current_time=current_time,
    )


async def handle_write_review_report(
    session: AsyncSession,
    owner_id: UUID,
    args: dict,
    *,
    conversation_id: UUID,
    source_message_id: UUID | None,
    current_time: datetime,
) -> dict:
    payload = WriteReviewReportArgs.model_validate(args)
    preview = _preview(
        f"生成 {payload.date.isoformat()} 的复盘报告",
        {
            "date": payload.date.isoformat(),
            "summary": payload.summary,
            "highlights": payload.highlights,
            "blockers": payload.blockers,
            "next_focus": payload.next_focus,
        },
    )
    return await _record(
        session,
        owner_id,
        conversation_id,
        source_message_id,
        "write_review_report",
        payload,
        preview=preview,
        expected_version=None,
        current_time=current_time,
    )


async def handle_save_memory(
    session: AsyncSession,
    owner_id: UUID,
    args: dict,
    *,
    conversation_id: UUID,
    source_message_id: UUID | None,
    current_time: datetime,
) -> dict:
    payload = SaveMemoryArgs.model_validate(args)
    preview = _preview(
        f"保存记忆「{payload.title}」",
        {
            "kind": payload.kind,
            "title": payload.title,
            "content": payload.content,
            "tags": payload.tags or [],
        },
    )
    return await _record(
        session,
        owner_id,
        conversation_id,
        source_message_id,
        "save_memory",
        payload,
        preview=preview,
        expected_version=None,
        current_time=current_time,
    )


async def handle_update_memory(
    session: AsyncSession,
    owner_id: UUID,
    args: dict,
    *,
    conversation_id: UUID,
    source_message_id: UUID | None,
    current_time: datetime,
) -> dict:
    payload = UpdateMemoryArgs.model_validate(args)
    preview = _preview(
        "更新记忆",
        {
            "memory_id": str(payload.memory_id),
            "title": payload.title,
            "content": payload.content,
            "tags": payload.tags,
        },
    )
    return await _record(
        session,
        owner_id,
        conversation_id,
        source_message_id,
        "update_memory",
        payload,
        preview=preview,
        expected_version=payload.expected_version,
        current_time=current_time,
    )


async def handle_trash_memory(
    session: AsyncSession,
    owner_id: UUID,
    args: dict,
    *,
    conversation_id: UUID,
    source_message_id: UUID | None,
    current_time: datetime,
) -> dict:
    payload = TrashMemoryArgs.model_validate(args)
    preview = _preview(
        "移除记忆到回收站",
        {"memory_id": str(payload.memory_id)},
    )
    return await _record(
        session,
        owner_id,
        conversation_id,
        source_message_id,
        "trash_memory",
        payload,
        preview=preview,
        expected_version=payload.expected_version,
        current_time=current_time,
    )


async def handle_create_difficulty(
    session: AsyncSession,
    owner_id: UUID,
    args: dict,
    *,
    conversation_id: UUID,
    source_message_id: UUID | None,
    current_time: datetime,
) -> dict:
    payload = CreateDifficultyArgs.model_validate(args)
    preview = _preview(
        f"记录困难「{payload.title}」",
        {
            "date": payload.date.isoformat(),
            "severity": payload.severity,
            "title": payload.title,
            "note": payload.note,
            "related_task_id": (
                str(payload.related_task_id)
                if payload.related_task_id
                else None
            ),
        },
    )
    return await _record(
        session,
        owner_id,
        conversation_id,
        source_message_id,
        "create_difficulty",
        payload,
        preview=preview,
        expected_version=None,
        current_time=current_time,
    )


async def handle_propose_plan(
    session: AsyncSession,
    owner_id: UUID,
    args: dict,
    *,
    conversation_id: UUID,
    source_message_id: UUID | None,
    current_time: datetime,
) -> dict:
    payload = ProposePlanArgs.model_validate(args)
    preview = _preview(
        f"规划 {payload.period_from.isoformat()} ~ {payload.period_to.isoformat()}",
        {
            "kind": payload.kind,
            "blocks": [block.model_dump(mode="json") for block in payload.blocks],
            "rationale": payload.rationale,
        },
    )
    return await _record(
        session,
        owner_id,
        conversation_id,
        source_message_id,
        "propose_plan",
        payload,
        preview=preview,
        expected_version=None,
        current_time=current_time,
    )


async def handle_confirm_plan(
    session: AsyncSession,
    owner_id: UUID,
    args: dict,
    *,
    conversation_id: UUID,
    source_message_id: UUID | None,
    current_time: datetime,
) -> dict:
    payload = ConfirmPlanArgs.model_validate(args)
    preview = _preview(
        "确认规划提案",
        {"proposal_id": str(payload.proposal_id)},
    )
    return await _record(
        session,
        owner_id,
        conversation_id,
        source_message_id,
        "confirm_plan",
        payload,
        preview=preview,
        expected_version=payload.expected_version,
        current_time=current_time,
    )


async def handle_update_plan_block(
    session: AsyncSession,
    owner_id: UUID,
    args: dict,
    *,
    conversation_id: UUID,
    source_message_id: UUID | None,
    current_time: datetime,
) -> dict:
    payload = UpdatePlanBlockArgs.model_validate(args)
    preview = _preview(
        "更新规划块",
        {
            "plan_id": str(payload.plan_id),
            "block_id": str(payload.block_id),
            "patch": payload.patch,
        },
    )
    return await _record(
        session,
        owner_id,
        conversation_id,
        source_message_id,
        "update_plan_block",
        payload,
        preview=preview,
        expected_version=payload.expected_version,
        current_time=current_time,
    )
