from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.calendar.models import UserTimeSettings
from itime.errors import AppError
from itime.execution.models import DifficultyRecord, FocusSession
from itime.planning.models import Plan, PlanVersion, ScheduledBlock
from itime.review.models import ReviewInputSnapshot, ReviewMessage, ReviewSession
from itime.tasks.models import Task, TaskOccurrenceState

MAX_FACTS_PER_GROUP = 200


def activity_window(
    activity_date: date,
    time_zone: str,
) -> tuple[datetime, datetime]:
    zone = ZoneInfo(time_zone)
    start = datetime.combine(activity_date, time(4), tzinfo=zone).astimezone(UTC)
    end = datetime.combine(
        activity_date + timedelta(days=1),
        time(4),
        tzinfo=zone,
    ).astimezone(UTC)
    return start, end


def _stable_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _bounded(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    return items[:MAX_FACTS_PER_GROUP], len(items) > MAX_FACTS_PER_GROUP


async def collect_review_facts(
    session: AsyncSession,
    owner_id: UUID,
    activity_date: date,
    *,
    current_time: datetime,
) -> tuple[dict[str, Any], str, datetime, datetime]:
    settings = await session.get(UserTimeSettings, owner_id)
    if settings is None:
        raise AppError(
            "time_settings_required",
            "请先保存当前使用的时区，再开始复盘。",
            status=409,
        )
    start, end = activity_window(activity_date, settings.time_zone)

    planned_rows = list(
        (
            await session.execute(
                select(ScheduledBlock, PlanVersion)
                .join(
                    PlanVersion,
                    PlanVersion.id == ScheduledBlock.plan_version_id,
                )
                .join(
                    Plan,
                    Plan.id == PlanVersion.plan_id,
                )
                .where(
                    ScheduledBlock.owner_id == owner_id,
                    ScheduledBlock.local_date == activity_date,
                    Plan.owner_id == owner_id,
                    Plan.current_version_id == PlanVersion.id,
                )
                .order_by(
                    ScheduledBlock.local_starts_at,
                    ScheduledBlock.position,
                    ScheduledBlock.id,
                )
                .limit(MAX_FACTS_PER_GROUP + 1)
            )
        ).all()
    )
    planned, planned_truncated = _bounded(
        [
            {
                "source_key": f"plan_block:{block.id}",
                "plan_version": version.version,
                "task_id": str(block.task_id),
                "occurrence_key": block.occurrence_key,
                "title": block.task_title_snapshot,
                "local_date": block.local_date.isoformat(),
                "starts_at": block.local_starts_at.isoformat(),
                "ends_at": block.local_ends_at.isoformat(),
                "time_zone": block.time_zone,
                "step_titles": list(block.step_titles_snapshot),
                "estimated_minutes": block.estimated_minutes,
            }
            for block, version in planned_rows
        ]
    )

    tasks = list(
        await session.scalars(
            select(Task)
            .where(
                Task.owner_id == owner_id,
                Task.completed_at >= start,
                Task.completed_at < end,
                Task.purged_at.is_(None),
            )
            .order_by(Task.completed_at, Task.id)
            .limit(MAX_FACTS_PER_GROUP + 1)
        )
    )
    occurrence_rows = list(
        (
            await session.execute(
                select(TaskOccurrenceState, Task)
                .join(
                    Task,
                    Task.id == TaskOccurrenceState.task_id,
                )
                .where(
                    TaskOccurrenceState.owner_id == owner_id,
                    TaskOccurrenceState.completed_at >= start,
                    TaskOccurrenceState.completed_at < end,
                    Task.purged_at.is_(None),
                )
                .order_by(
                    TaskOccurrenceState.completed_at,
                    TaskOccurrenceState.id,
                )
                .limit(MAX_FACTS_PER_GROUP + 1)
            )
        ).all()
    )
    completion_items = [
        {
            "source_key": f"task:{task.id}",
            "task_id": str(task.id),
            "occurrence_key": None,
            "title": task.title,
            "completed_at": task.completed_at.isoformat(),
        }
        for task in tasks
    ]
    completion_items.extend(
        {
            "source_key": f"task_occurrence:{occurrence.id}",
            "task_id": str(task.id),
            "occurrence_key": occurrence.occurrence_key,
            "title": task.title,
            "completed_at": occurrence.completed_at.isoformat(),
        }
        for occurrence, task in occurrence_rows
    )
    completion_items.sort(
        key=lambda item: (item["completed_at"], item["source_key"])
    )
    completions, completions_truncated = _bounded(completion_items)

    focus_rows = list(
        await session.scalars(
            select(FocusSession)
            .where(
                FocusSession.owner_id == owner_id,
                FocusSession.started_at < end,
                or_(
                    FocusSession.effective_ended_at.is_(None),
                    FocusSession.effective_ended_at > start,
                ),
            )
            .order_by(FocusSession.started_at, FocusSession.id)
            .limit(MAX_FACTS_PER_GROUP + 1)
        )
    )
    focus_items: list[dict[str, Any]] = []
    total_seconds = 0
    for focus in focus_rows:
        effective_start = focus.effective_started_at or focus.started_at
        effective_end = focus.effective_ended_at or min(current_time, end)
        overlap_start = max(effective_start, start)
        overlap_end = min(effective_end, end)
        seconds = max(0, int((overlap_end - overlap_start).total_seconds()))
        if seconds == 0:
            continue
        total_seconds += seconds
        focus_items.append(
            {
                "source_key": f"focus:{focus.id}",
                "title": focus.title_snapshot,
                "task_id": str(focus.task_id) if focus.task_id else None,
                "task_title": focus.task_title_snapshot,
                "status": focus.status,
                "started_at": effective_start.isoformat(),
                "ended_at": (
                    effective_end.isoformat()
                    if focus.effective_ended_at is not None
                    else None
                ),
                "activity_seconds": seconds,
                "total_seconds": focus.effective_duration_seconds,
                "revised": focus.version > 1,
            }
        )
    focus_items, focus_truncated = _bounded(focus_items)

    difficulties = list(
        await session.scalars(
            select(DifficultyRecord)
            .where(
                DifficultyRecord.owner_id == owner_id,
                DifficultyRecord.deleted_at.is_(None),
                DifficultyRecord.purged_at.is_(None),
                DifficultyRecord.occurred_at >= start,
                DifficultyRecord.occurred_at < end,
            )
            .order_by(DifficultyRecord.occurred_at, DifficultyRecord.id)
            .limit(MAX_FACTS_PER_GROUP + 1)
        )
    )
    difficulty_items, difficulties_truncated = _bounded(
        [
            {
                "source_key": f"difficulty:{record.id}",
                "task_id": str(record.task_id) if record.task_id else None,
                "task_title": record.task_title_snapshot,
                "content": record.content,
                "category": record.category,
                "emotion": record.emotion,
                "occurred_at": record.occurred_at.isoformat(),
            }
            for record in difficulties
        ]
    )

    facts = {
        "activity_date": activity_date.isoformat(),
        "time_zone": settings.time_zone,
        "activity_window": {
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
        "planned_blocks": {
            "items": planned,
            "truncated": planned_truncated,
        },
        "task_completions": {
            "items": completions,
            "truncated": completions_truncated,
        },
        "focus": {
            "items": focus_items,
            "total_seconds": total_seconds,
            "truncated": focus_truncated,
        },
        "difficulties": {
            "items": difficulty_items,
            "truncated": difficulties_truncated,
        },
    }
    return facts, sha256(_stable_json(facts)).hexdigest(), start, end


async def build_review_snapshot(
    session: AsyncSession,
    review: ReviewSession,
    *,
    purpose: str,
    generation_instruction: str,
    current_time: datetime,
) -> ReviewInputSnapshot:
    facts, facts_sha256, start, end = await collect_review_facts(
        session,
        review.owner_id,
        review.activity_date,
        current_time=current_time,
    )
    messages = list(
        await session.scalars(
            select(ReviewMessage)
            .where(
                ReviewMessage.owner_id == review.owner_id,
                ReviewMessage.review_session_id == review.id,
            )
            .order_by(ReviewMessage.sequence)
        )
    )
    snapshot = ReviewInputSnapshot(
        owner_id=review.owner_id,
        review_session_id=review.id,
        purpose=purpose,
        activity_date=review.activity_date,
        time_zone=review.time_zone,
        activity_started_at=start,
        activity_ended_at=end,
        session_input_revision=review.input_revision,
        facts_sha256=facts_sha256,
        facts=facts,
        user_context={
            "supplemental_facts": review.supplemental_facts,
            "messages": [
                {
                    "source_key": f"review_message:{message.id}",
                    "role": message.role,
                    "content": message.content,
                }
                for message in messages
            ],
        },
        memory_context=[],
        generation_instruction=generation_instruction,
        created_at=current_time,
    )
    session.add(snapshot)
    await session.flush()
    return snapshot


def review_snapshot_payload(snapshot: ReviewInputSnapshot) -> dict[str, Any]:
    return {
        "purpose": snapshot.purpose,
        "activity_date": snapshot.activity_date.isoformat(),
        "time_zone": snapshot.time_zone,
        "activity_window": {
            "start": snapshot.activity_started_at.isoformat(),
            "end": snapshot.activity_ended_at.isoformat(),
        },
        "session_input_revision": snapshot.session_input_revision,
        "facts_sha256": snapshot.facts_sha256,
        "facts": snapshot.facts,
        "user_context": snapshot.user_context,
        "memory_context": snapshot.memory_context,
        "generation_instruction": snapshot.generation_instruction,
    }
