import hashlib
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from itime.ai.conversation_models import Conversation, ConversationMessage
from itime.ai.governance_models import UserPrivacySetting
from itime.ai.models import AIConfigCapability, AIConfigVersion
from itime.auth.models import SecurityEvent, User
from itime.auth.security import Security
from itime.calendar.models import (
    AcademicTerm,
    CalendarSeries,
    TermPeriod,
    UserTimeSettings,
)
from itime.config import Settings
from itime.database import Database
from itime.execution.models import DifficultyRecord, FocusEvent, FocusSession
from itime.planning.models import Plan, PlanVersion, ScheduledBlock
from itime.quota.models import AIDailyAllowance, AIQuotaPolicyState, AIQuotaPolicyVersion
from itime.review.models import Memory, MemorySettings, ReviewMessage, ReviewSession
from itime.tasks.models import (
    ChangeEvent,
    Label,
    Project,
    Task,
    TaskLabel,
    TaskLink,
    TaskSchedule,
    TaskStep,
)
from performance.contracts import AccountCredential, SeedManifest, SeedProfile

SYNTHETIC_LOGIN_SECRET = "itime-performance-only"  # noqa: S105
TIME_ZONE = "UTC"
AI_CAPABILITIES = (
    "calendar_screenshot",
    "conversation",
    "planning",
    "review",
    "transcription",
)


@dataclass(frozen=True)
class GlobalSeedState:
    password_hash: str
    quota_policy_id: UUID


@dataclass(frozen=True)
class StandardAccountResult:
    counts: dict[str, int]
    credential: AccountCredential | None


def stable_uuid(profile: str, seed: int, kind: str, *parts: object) -> UUID:
    suffix = ":".join(str(part) for part in parts)
    return uuid5(NAMESPACE_URL, f"itime-performance:{profile}:{seed}:{kind}:{suffix}")


async def _insert_batched(
    session: AsyncSession,
    model: type[object],
    rows: list[dict[str, object]],
    *,
    batch_size: int = 1000,
) -> None:
    if not rows:
        return
    for offset in range(0, len(rows), batch_size):
        await session.execute(insert(model), rows[offset : offset + batch_size])


def _add_count(counts: Counter[str], name: str, rows: list[object]) -> None:
    counts[name] += len(rows)


def _activity_window(at: datetime) -> tuple[date, datetime, datetime]:
    start = datetime.combine(at.date(), time(4), tzinfo=UTC)
    activity_date = at.date()
    if at < start:
        activity_date -= timedelta(days=1)
        start -= timedelta(days=1)
    return activity_date, start, start + timedelta(days=1)


async def _prepare_globals(
    database: Database,
    settings: Settings,
    *,
    profile: SeedProfile,
    seed: int,
    at: datetime,
) -> GlobalSeedState:
    security = Security(settings)
    password_hash = await security.hash_password(SYNTHETIC_LOGIN_SECRET)

    async with database.transaction() as session:
        for capability in AI_CAPABILITIES:
            state = await session.get(
                AIConfigCapability,
                capability,
                with_for_update=True,
            )
            if state is None:
                raise RuntimeError(f"缺少 AI 能力初始状态：{capability}")
            config_id = stable_uuid(profile.name, seed, "ai-config", capability)
            session.add(
                AIConfigVersion(
                    id=config_id,
                    capability=capability,
                    version=state.next_version,
                    endpoint_url="http://host.docker.internal:18081/v1",
                    model=f"performance-{capability}",
                    api_key_ciphertext=security.ai_cipher.encrypt(
                        b"synthetic-performance-key"
                    ),
                    timeout_seconds=30,
                    max_output_tokens=None if capability == "transcription" else 4096,
                    pricing_basis=(
                        "audio_minute" if capability == "transcription" else "token"
                    ),
                    price_currency="CNY",
                    input_price_nanos_per_million_tokens=(
                        None if capability == "transcription" else 1_000_000_000
                    ),
                    cached_input_price_nanos_per_million_tokens=(
                        None if capability == "transcription" else 500_000_000
                    ),
                    output_price_nanos_per_million_tokens=(
                        None if capability == "transcription" else 2_000_000_000
                    ),
                    audio_price_nanos_per_minute=(
                        6_000_000 if capability == "transcription" else None
                    ),
                    status="published",
                    last_test_id=stable_uuid(
                        profile.name,
                        seed,
                        "ai-config-test",
                        capability,
                    ),
                    last_test_started_at=at,
                    last_test_completed_at=at,
                    published_at=at,
                    created_at=at,
                    updated_at=at,
                )
            )
            await session.flush()
            state.next_version += 1
            state.current_version_id = config_id
            state.updated_at = at

        quota_state = await session.get(
            AIQuotaPolicyState,
            1,
            with_for_update=True,
        )
        if quota_state is None:
            raise RuntimeError("缺少 AI 额度策略初始状态")
        policy_id = stable_uuid(profile.name, seed, "quota-policy", 1)
        session.add(
            AIQuotaPolicyVersion(
                id=policy_id,
                version=quota_state.next_version,
                currency="CNY",
                daily_allowance_nanos=1_000_000_000,
                reservation_estimator_version="reservation-utf8-vision-v1",
                status="published",
                published_at=at,
                created_at=at,
                updated_at=at,
            )
        )
        await session.flush()
        quota_state.next_version += 1
        quota_state.current_version_id = policy_id
        quota_state.updated_at = at

    return GlobalSeedState(
        password_hash=password_hash,
        quota_policy_id=policy_id,
    )


def _task_lifecycle(
    index: int,
    total: int,
    at: datetime,
) -> tuple[str, datetime | None, datetime | None, datetime | None]:
    active_end = total * 80 // 100
    archived_end = active_end + total * 10 // 100
    trashed_end = archived_end + total * 8 // 100
    if index < active_end:
        return "active", None, None, None
    if index < archived_end:
        return "archived", at - timedelta(days=10), None, None
    if index < trashed_end:
        deleted_at = at - timedelta(days=2)
        return "trashed", None, deleted_at, deleted_at + timedelta(days=30)
    deleted_at = at - timedelta(days=40)
    return "purged", None, deleted_at, at - timedelta(days=10)


async def _seed_account_base(
    session: AsyncSession,
    *,
    profile: SeedProfile,
    account_index: int,
    seed: int,
    at: datetime,
    password_hash: str,
    quota_policy_id: UUID,
) -> tuple[UUID, Counter[str]]:
    counts: Counter[str] = Counter()
    owner_id = stable_uuid(profile.name, seed, "account", account_index)
    created_at = at - timedelta(days=240 - account_index)
    activity_date, window_start, window_end = _activity_window(at)

    rows_by_model = (
        (
            User,
            [
                {
                    "id": owner_id,
                    "email": (
                        f"perf-{profile.name}-{seed}-{account_index:03d}"
                        "@performance.invalid"
                    ),
                    "password_hash": password_hash,
                    "email_verified_at": created_at,
                    "status": "active",
                    "ai_access": "allowed",
                    "is_admin": False,
                    "auth_epoch": 0,
                    "management_version": 1,
                    "created_at": created_at,
                }
            ],
            "users",
        ),
        (
            UserTimeSettings,
            [
                {
                    "owner_id": owner_id,
                    "time_zone": TIME_ZONE,
                    "week_starts_on": 1,
                    "activity_day_start_minute": 240,
                    "version": 1,
                    "created_at": created_at,
                    "updated_at": at,
                }
            ],
            "user_time_settings",
        ),
        (
            UserPrivacySetting,
            [
                {
                    "owner_id": owner_id,
                    "quality_improvement_enabled": account_index % 5 != 0,
                    "version": 1,
                    "created_at": created_at,
                    "updated_at": at,
                }
            ],
            "user_privacy_settings",
        ),
        (
            MemorySettings,
            [
                {
                    "owner_id": owner_id,
                    "enabled": True,
                    "version": 1,
                    "created_at": created_at,
                    "updated_at": at,
                }
            ],
            "memory_settings",
        ),
        (
            AIDailyAllowance,
            [
                {
                    "id": stable_uuid(
                        profile.name,
                        seed,
                        "daily-allowance",
                        account_index,
                    ),
                    "owner_id": owner_id,
                    "activity_date": activity_date,
                    "window_start_at": window_start,
                    "window_end_at": window_end,
                    "time_zone": TIME_ZONE,
                    "activity_day_start_minute": 240,
                    "policy_version_id": quota_policy_id,
                    "currency": "CNY",
                    "base_amount_nanos": 1_000_000_000,
                    "version": 1,
                    "created_at": at,
                    "updated_at": at,
                }
            ],
            "ai_daily_allowances",
        ),
    )
    for model, rows, count_name in rows_by_model:
        await _insert_batched(session, model, rows)
        _add_count(counts, count_name, rows)
    counts["sync_states"] += 1
    counts["planning_input_revisions"] += 1
    return owner_id, counts


async def _seed_tasks(
    session: AsyncSession,
    *,
    profile: SeedProfile,
    owner_id: UUID,
    account_index: int,
    seed: int,
    at: datetime,
) -> tuple[Counter[str], list[UUID], list[str]]:
    counts: Counter[str] = Counter()
    project_ids = [
        stable_uuid(profile.name, seed, "project", account_index, index)
        for index in range(24)
    ]
    projects = [
        {
            "id": project_id,
            "owner_id": owner_id,
            "kind": "goal" if index % 6 == 0 else "project",
            "name": f"性能项目 {account_index + 1}-{index + 1}",
            "description": "确定性性能测试项目",
            "version": 1,
            "created_at": at - timedelta(days=180 - index),
            "updated_at": at - timedelta(minutes=index),
        }
        for index, project_id in enumerate(project_ids)
    ]
    await _insert_batched(session, Project, projects)
    _add_count(counts, "projects", projects)

    label_ids = [
        stable_uuid(profile.name, seed, "label", account_index, index)
        for index in range(40)
    ]
    labels = [
        {
            "id": label_id,
            "owner_id": owner_id,
            "name": f"标签 {index + 1}",
            "normalized_name": f"标签 {index + 1}",
            "version": 1,
            "created_at": at - timedelta(days=120 - index),
            "updated_at": at,
        }
        for index, label_id in enumerate(label_ids)
    ]
    await _insert_batched(session, Label, labels)
    _add_count(counts, "labels", labels)

    task_ids: list[UUID] = []
    task_titles: list[str] = []
    task_rows: list[dict[str, object]] = []
    schedule_rows: list[dict[str, object]] = []
    step_rows: list[dict[str, object]] = []
    task_label_rows: list[dict[str, object]] = []
    link_rows: list[dict[str, object]] = []
    lifecycle_counts: Counter[str] = Counter()
    schedule_start = at.date() - timedelta(days=45)

    for index in range(profile.tasks_per_active):
        task_id = stable_uuid(profile.name, seed, "task", account_index, index)
        lifecycle, archived_at, deleted_at, lifecycle_end = _task_lifecycle(
            index,
            profile.tasks_per_active,
            at,
        )
        purge_after = lifecycle_end if lifecycle == "trashed" else (
            at - timedelta(days=10) if lifecycle == "purged" else None
        )
        purged_at = lifecycle_end if lifecycle == "purged" else None
        completed = lifecycle != "purged" and index % 4 == 0
        title = (
            "已彻底删除的任务"
            if lifecycle == "purged"
            else f"性能任务 {account_index + 1}-{index + 1}"
        )
        task_ids.append(task_id)
        task_titles.append(title)
        lifecycle_counts[f"task_lifecycle_{lifecycle}"] += 1
        task_rows.append(
            {
                "id": task_id,
                "owner_id": owner_id,
                "project_id": (
                    None if lifecycle == "purged" else project_ids[index % len(project_ids)]
                ),
                "title": title,
                "description": (
                    "" if lifecycle == "purged" else "用于性能基线的合成任务"
                ),
                "estimated_minutes": None if lifecycle == "purged" else 25 + index % 8 * 5,
                "estimate_source": None if lifecycle == "purged" else "user",
                "priority": (
                    0 if lifecycle == "purged" else (seed + account_index + index) % 4
                ),
                "status": "completed" if completed else "open",
                "completed_at": at - timedelta(days=index % 30) if completed else None,
                "archived_at": archived_at,
                "deleted_at": deleted_at,
                "purge_after": purge_after,
                "purged_at": purged_at,
                "version": 2 if lifecycle != "active" else 1,
                "created_at": at - timedelta(days=120 - index % 120),
                "updated_at": at - timedelta(seconds=index),
            }
        )
        if lifecycle == "purged":
            continue

        due_date = schedule_start + timedelta(days=index % 180)
        recurring = index % 5 == 0
        schedule_rows.append(
            {
                "task_id": task_id,
                "owner_id": owner_id,
                "predecessor_task_id": None,
                "due_kind": "date",
                "start_date": due_date,
                "last_occurrence_date": (
                    due_date + timedelta(days=9) if recurring else due_date
                ),
                "due_local_time": None,
                "due_at": None,
                "time_zone": TIME_ZONE,
                "ambiguous_offset_minutes": None,
                "recurrence_frequency": "daily" if recurring else "none",
                "recurrence_interval": 1,
                "recurrence_weekdays": [],
                "recurrence_month": None,
                "recurrence_day_of_month": None,
                "recurrence_until": None,
                "recurrence_count": 10 if recurring else None,
                "splittable": True,
                "minimum_block_minutes": 15,
                "created_at": at - timedelta(days=90),
                "updated_at": at,
            }
        )
        for position in range(2):
            step_rows.append(
                {
                    "id": stable_uuid(
                        profile.name,
                        seed,
                        "task-step",
                        account_index,
                        index,
                        position,
                    ),
                    "task_id": task_id,
                    "owner_id": owner_id,
                    "title": f"步骤 {position + 1}",
                    "position": position,
                    "completed": completed,
                    "completed_at": (
                        at - timedelta(days=index % 30) if completed else None
                    ),
                    "created_at": at - timedelta(days=90),
                    "updated_at": at,
                }
            )
        for offset in range(2):
            task_label_rows.append(
                {
                    "task_id": task_id,
                    "label_id": label_ids[(index + offset) % len(label_ids)],
                    "owner_id": owner_id,
                }
            )
        if index % 20 == 0:
            extracted = f"合成公开资料 {account_index + 1}-{index + 1}"
            link_rows.append(
                {
                    "id": stable_uuid(
                        profile.name,
                        seed,
                        "task-link",
                        account_index,
                        index,
                    ),
                    "task_id": task_id,
                    "owner_id": owner_id,
                    "title": "合成资料",
                    "url": f"https://resource-{account_index}-{index}.invalid/item",
                    "position": 0,
                    "fetch_status": "ready",
                    "fetch_revision": 1,
                    "attempt_count": 1,
                    "available_at": at,
                    "lease_id": None,
                    "lease_until": None,
                    "last_attempt_at": at,
                    "last_success_at": at,
                    "final_host": f"resource-{account_index}-{index}.invalid",
                    "media_type": "text/plain",
                    "page_title": "合成资料",
                    "summary": extracted,
                    "extracted_text": extracted,
                    "content_sha256": hashlib.sha256(extracted.encode()).hexdigest(),
                    "last_error_code": None,
                    "last_error_message": None,
                    "last_http_status": 200,
                    "created_at": at,
                    "updated_at": at,
                }
            )

    for model, rows, count_name in (
        (Task, task_rows, "tasks"),
        (TaskSchedule, schedule_rows, "task_schedules"),
        (TaskStep, step_rows, "task_steps"),
        (TaskLabel, task_label_rows, "task_labels"),
        (TaskLink, link_rows, "task_links"),
    ):
        await _insert_batched(session, model, rows)
        _add_count(counts, count_name, rows)
    counts.update(lifecycle_counts)
    return counts, task_ids, task_titles


async def _seed_calendar_and_plan(
    session: AsyncSession,
    *,
    profile: SeedProfile,
    owner_id: UUID,
    task_ids: list[UUID],
    task_titles: list[str],
    account_index: int,
    seed: int,
    at: datetime,
) -> Counter[str]:
    counts: Counter[str] = Counter()
    week_start = at.date() - timedelta(days=at.weekday())
    term_start = week_start - timedelta(weeks=8)
    term_id = stable_uuid(profile.name, seed, "term", account_index)
    term_rows = [
        {
            "id": term_id,
            "owner_id": owner_id,
            "name": "性能基线学期",
            "start_date": term_start,
            "end_date": term_start + timedelta(weeks=25, days=6),
            "first_week_monday": term_start,
            "time_zone": TIME_ZONE,
            "version": 1,
            "created_at": at - timedelta(days=90),
            "updated_at": at,
        }
    ]
    period_rows = [
        {
            "term_id": term_id,
            "period_number": number,
            "owner_id": owner_id,
            "name": f"第 {number} 节",
            "normalized_name": f"第 {number} 节",
            "starts_at": time(8 + number, 0),
            "ends_at": time(8 + number, 45),
        }
        for number in range(1, 9)
    ]
    series_rows: list[dict[str, object]] = []
    for index in range(12):
        course = index < 8
        starts_at = time(8 + index % 8, 0)
        series_rows.append(
            {
                "id": stable_uuid(
                    profile.name,
                    seed,
                    "calendar-series",
                    account_index,
                    index,
                ),
                "owner_id": owner_id,
                "term_id": term_id if course else None,
                "predecessor_id": None,
                "source_id": None,
                "kind": "course" if course else "fixed",
                "name": (
                    f"课程 {index + 1}" if course else f"固定安排 {index - 7}"
                ),
                "description": "确定性性能测试日历",
                "location": f"教室 {index + 1}",
                "busy": True,
                "source_type": "manual",
                "time_zone": TIME_ZONE,
                "start_date": term_start + timedelta(days=index % 5),
                "starts_at": starts_at,
                "ends_at": time(starts_at.hour, 50),
                "end_day_offset": 0,
                "start_period_number": index + 1 if course else None,
                "end_period_number": index + 1 if course else None,
                "recurrence_frequency": "weekly",
                "recurrence_interval": 1,
                "recurrence_weekdays": [index % 5 + 1],
                "recurrence_month": None,
                "recurrence_day_of_month": None,
                "recurrence_until": None,
                "recurrence_count": 18 if course else 26,
                "course_first_week_monday": term_start if course else None,
                "course_week_numbers": list(range(1, 19)) if course else [],
                "ambiguous_offset_minutes": None,
                "deleted_at": None,
                "purge_after": None,
                "purged_at": None,
                "version": 1,
                "created_at": at - timedelta(days=90),
                "updated_at": at,
            }
        )
    for model, rows, count_name in (
        (AcademicTerm, term_rows, "academic_terms"),
        (TermPeriod, period_rows, "term_periods"),
        (CalendarSeries, series_rows, "calendar_series"),
    ):
        await _insert_batched(session, model, rows)
        _add_count(counts, count_name, rows)

    if not task_ids:
        return counts
    plan_id = stable_uuid(profile.name, seed, "plan", account_index)
    version_id = stable_uuid(profile.name, seed, "plan-version", account_index)
    plan_rows = [
        {
            "id": plan_id,
            "owner_id": owner_id,
            "week_start_date": week_start,
            "time_zone": TIME_ZONE,
            "current_version_id": version_id,
            "version": 1,
            "created_at": at,
            "updated_at": at,
        }
    ]
    version_rows = [
        {
            "id": version_id,
            "plan_id": plan_id,
            "owner_id": owner_id,
            "version": 1,
            "source": "manual",
            "proposal_id": None,
            "scope": "week",
            "target_date": at.date(),
            "density": "balanced",
            "buffer_minutes": 10,
            "availability_start": time(8),
            "availability_end": time(22),
            "summary": "性能基线计划",
            "assumptions": [],
            "input_revision": profile.sync_events_per_active,
            "created_at": at,
        }
    ]
    await _insert_batched(session, Plan, plan_rows)
    await _insert_batched(session, PlanVersion, version_rows)
    _add_count(counts, "plans", plan_rows)
    _add_count(counts, "plan_versions", version_rows)

    block_rows: list[dict[str, object]] = []
    for index, task_id in enumerate(task_ids[:14]):
        local_date = week_start + timedelta(days=index % 7)
        local_start = time(8 + index % 8)
        start_at = datetime.combine(local_date, local_start, tzinfo=UTC)
        block_rows.append(
            {
                "id": stable_uuid(
                    profile.name,
                    seed,
                    "scheduled-block",
                    account_index,
                    index,
                ),
                "plan_version_id": version_id,
                "owner_id": owner_id,
                "position": index,
                "task_id": task_id,
                "occurrence_key": None,
                "task_title_snapshot": task_titles[index],
                "step_ids": [],
                "step_titles_snapshot": [],
                "local_date": local_date,
                "local_starts_at": local_start,
                "local_ends_at": time(local_start.hour + 1),
                "time_zone": TIME_ZONE,
                "starts_at": start_at,
                "ends_at": start_at + timedelta(hours=1),
                "estimate_source": "user",
                "estimated_minutes": 60,
                "created_at": at,
            }
        )
    await _insert_batched(session, ScheduledBlock, block_rows)
    _add_count(counts, "scheduled_blocks", block_rows)
    return counts


async def _seed_activity_history(
    session: AsyncSession,
    *,
    profile: SeedProfile,
    owner_id: UUID,
    task_ids: list[UUID],
    task_titles: list[str],
    account_index: int,
    seed: int,
    at: datetime,
) -> Counter[str]:
    counts: Counter[str] = Counter()
    usable_tasks = task_ids[: max(1, profile.tasks_per_active * 98 // 100)]

    focus_rows: list[dict[str, object]] = []
    focus_event_rows: list[dict[str, object]] = []
    for index in range(180):
        task_offset = index % len(usable_tasks) if usable_tasks else None
        task_id = usable_tasks[task_offset] if task_offset is not None else None
        task_title = task_titles[task_offset] if task_offset is not None else None
        focus_id = stable_uuid(
            profile.name,
            seed,
            "focus",
            account_index,
            index,
        )
        started = at - timedelta(days=index % 180, hours=index % 8)
        ended = started + timedelta(minutes=25 + index % 35)
        focus_rows.append(
            {
                "id": focus_id,
                "owner_id": owner_id,
                "source": "manual",
                "task_id": task_id,
                "occurrence_key": None,
                "scheduled_block_id": None,
                "title_snapshot": task_title or "无关联专注",
                "task_title_snapshot": task_title,
                "step_snapshots": [],
                "mode": "stopwatch",
                "target_seconds": None,
                "status": "finished",
                "started_at": started,
                "ended_at": ended,
                "active_started_at": None,
                "accumulated_seconds": int((ended - started).total_seconds()),
                "effective_started_at": started,
                "effective_ended_at": ended,
                "effective_duration_seconds": int((ended - started).total_seconds()),
                "time_zone": TIME_ZONE,
                "version": 1,
                "created_at": ended,
                "updated_at": ended,
            }
        )
        for sequence, kind, occurred_at in (
            (1, "start", started),
            (2, "finish", ended),
        ):
            focus_event_rows.append(
                {
                    "id": stable_uuid(
                        profile.name,
                        seed,
                        "focus-event",
                        account_index,
                        index,
                        sequence,
                    ),
                    "owner_id": owner_id,
                    "focus_session_id": focus_id,
                    "sequence": sequence,
                    "kind": kind,
                    "occurred_at": occurred_at,
                    "client_recorded_at": occurred_at,
                    "source": "online",
                    "details": {},
                    "created_at": occurred_at,
                }
            )
    await _insert_batched(session, FocusSession, focus_rows)
    await _insert_batched(session, FocusEvent, focus_event_rows)
    _add_count(counts, "focus_sessions", focus_rows)
    _add_count(counts, "focus_events", focus_event_rows)

    difficulty_rows: list[dict[str, object]] = []
    for index in range(90):
        purged = index % 50 == 49
        deleted = not purged and index % 12 == 11
        deleted_at = at - timedelta(days=2) if deleted else (
            at - timedelta(days=40) if purged else None
        )
        difficulty_rows.append(
            {
                "id": stable_uuid(
                    profile.name,
                    seed,
                    "difficulty",
                    account_index,
                    index,
                ),
                "owner_id": owner_id,
                "task_id": (
                    None
                    if purged or not usable_tasks
                    else usable_tasks[index % len(usable_tasks)]
                ),
                "focus_session_id": None,
                "occurrence_key": None,
                "task_title_snapshot": (
                    None
                    if purged or not usable_tasks
                    else task_titles[index % len(usable_tasks)]
                ),
                "content": None if purged else f"合成困难记录 {index + 1}",
                "category": None if purged else ("阻塞" if index % 2 else "分心"),
                "emotion": None if purged else ("焦虑" if index % 3 else "平静"),
                "occurred_at": at - timedelta(days=index),
                "time_zone": TIME_ZONE,
                "deleted_at": deleted_at,
                "purge_after": (
                    deleted_at + timedelta(days=30) if deleted_at is not None else None
                ),
                "purged_at": at - timedelta(days=5) if purged else None,
                "version": 2 if deleted_at is not None else 1,
                "created_at": at - timedelta(days=index),
                "updated_at": at,
            }
        )
    await _insert_batched(session, DifficultyRecord, difficulty_rows)
    _add_count(counts, "difficulty_records", difficulty_rows)

    review_rows: list[dict[str, object]] = []
    review_message_rows: list[dict[str, object]] = []
    for index in range(90):
        review_id = stable_uuid(
            profile.name,
            seed,
            "review",
            account_index,
            index,
        )
        activity_date = at.date() - timedelta(days=index)
        review_rows.append(
            {
                "id": review_id,
                "owner_id": owner_id,
                "activity_date": activity_date,
                "time_zone": TIME_ZONE,
                "supplemental_facts": "",
                "input_revision": index,
                "question_count": 0,
                "version": 1,
                "created_at": at - timedelta(days=index),
                "updated_at": at - timedelta(days=index),
            }
        )
        review_message_rows.append(
            {
                "id": stable_uuid(
                    profile.name,
                    seed,
                    "review-message",
                    account_index,
                    index,
                ),
                "owner_id": owner_id,
                "review_session_id": review_id,
                "sequence": 1,
                "role": "user",
                "kind": "answer",
                "content": f"第 {index + 1} 天的合成复盘记录",
                "source_job_id": None,
                "created_at": at - timedelta(days=index),
            }
        )
    await _insert_batched(session, ReviewSession, review_rows)
    await _insert_batched(session, ReviewMessage, review_message_rows)
    _add_count(counts, "review_sessions", review_rows)
    _add_count(counts, "review_messages", review_message_rows)

    memory_rows: list[dict[str, object]] = []
    for index in range(24):
        purged = index == 23
        deleted = not purged and index in {20, 21, 22}
        deleted_at = at - timedelta(days=2) if deleted else (
            at - timedelta(days=40) if purged else None
        )
        memory_rows.append(
            {
                "id": stable_uuid(
                    profile.name,
                    seed,
                    "memory",
                    account_index,
                    index,
                ),
                "owner_id": owner_id,
                "kind": ("preference", "constraint", "context", "goal")[index % 4],
                "title": None if purged else f"合成长期记忆 {index + 1}",
                "content": None if purged else "仅用于隔离性能测试的合成内容",
                "source_type": "manual",
                "source_proposal_id": None,
                "deleted_at": deleted_at,
                "purge_after": (
                    deleted_at + timedelta(days=30) if deleted_at is not None else None
                ),
                "purged_at": at - timedelta(days=5) if purged else None,
                "version": 2 if deleted_at is not None else 1,
                "created_at": at - timedelta(days=index),
                "updated_at": at,
            }
        )
    await _insert_batched(session, Memory, memory_rows)
    _add_count(counts, "memories", memory_rows)
    return counts


async def _seed_conversations_and_events(
    session: AsyncSession,
    *,
    profile: SeedProfile,
    owner_id: UUID,
    task_ids: list[UUID],
    account_index: int,
    seed: int,
    at: datetime,
) -> Counter[str]:
    counts: Counter[str] = Counter()
    conversation_rows: list[dict[str, object]] = []
    message_rows: list[dict[str, object]] = []
    for conversation_index in range(20):
        conversation_id = stable_uuid(
            profile.name,
            seed,
            "conversation",
            account_index,
            conversation_index,
        )
        conversation_rows.append(
            {
                "id": conversation_id,
                "owner_id": owner_id,
                "title": f"性能会话 {conversation_index + 1}",
                "title_source": "auto",
                "version": 1,
                "archived_at": at if conversation_index % 10 == 9 else None,
                "created_at": at - timedelta(days=conversation_index),
                "updated_at": at - timedelta(minutes=conversation_index),
            }
        )
        for sequence in range(1, 41):
            completed_at = at - timedelta(
                days=conversation_index,
                seconds=40 - sequence,
            )
            message_rows.append(
                {
                    "id": stable_uuid(
                        profile.name,
                        seed,
                        "conversation-message",
                        account_index,
                        conversation_index,
                        sequence,
                    ),
                    "owner_id": owner_id,
                    "conversation_id": conversation_id,
                    "sequence": sequence,
                    "role": "user" if sequence % 2 else "assistant",
                    "status": "complete",
                    "content": f"合成会话消息 {conversation_index + 1}-{sequence}",
                    "error_code": None,
                    "error_message": None,
                    "created_at": completed_at,
                    "updated_at": completed_at,
                    "completed_at": completed_at,
                }
            )
    await _insert_batched(session, Conversation, conversation_rows)
    await _insert_batched(session, ConversationMessage, message_rows)
    _add_count(counts, "conversations", conversation_rows)
    _add_count(counts, "conversation_messages", message_rows)

    change_rows: list[dict[str, object]] = []
    for index in range(profile.sync_events_per_active):
        has_task = bool(task_ids)
        change_rows.append(
            {
                "owner_id": owner_id,
                "entity_type": "task" if has_task else "privacy_settings",
                "entity_id": task_ids[index % len(task_ids)] if has_task else owner_id,
                "action": "updated",
                "entity_version": index // max(1, len(task_ids)) + 1,
                "changed_at": at - timedelta(
                    seconds=profile.sync_events_per_active - index
                ),
            }
        )
    await _insert_batched(session, ChangeEvent, change_rows)
    _add_count(counts, "change_events", change_rows)

    audit_rows = [
        {
            "id": stable_uuid(
                profile.name,
                seed,
                "security-event",
                account_index,
                index,
            ),
            "user_id": owner_id,
            "action": ("login_succeeded", "password_changed", "session_revoked", "login_succeeded")[
                index
            ],
            "request_id": str(
                stable_uuid(
                    profile.name,
                    seed,
                    "security-request",
                    account_index,
                    index,
                )
            ),
            "target_id": str(owner_id),
            "details": {"synthetic": True},
            "created_at": at - timedelta(days=index),
        }
        for index in range(4)
    ]
    await _insert_batched(session, SecurityEvent, audit_rows)
    _add_count(counts, "security_events", audit_rows)
    return counts


async def _seed_account_data(
    session: AsyncSession,
    *,
    profile: SeedProfile,
    owner_id: UUID,
    account_index: int,
    seed: int,
    at: datetime,
) -> Counter[str]:
    counts, task_ids, task_titles = await _seed_tasks(
        session,
        profile=profile,
        owner_id=owner_id,
        account_index=account_index,
        seed=seed,
        at=at,
    )
    counts.update(
        await _seed_calendar_and_plan(
            session,
            profile=profile,
            owner_id=owner_id,
            task_ids=task_ids,
            task_titles=task_titles,
            account_index=account_index,
            seed=seed,
            at=at,
        )
    )
    counts.update(
        await _seed_activity_history(
            session,
            profile=profile,
            owner_id=owner_id,
            task_ids=task_ids,
            task_titles=task_titles,
            account_index=account_index,
            seed=seed,
            at=at,
        )
    )
    counts.update(
        await _seed_conversations_and_events(
            session,
            profile=profile,
            owner_id=owner_id,
            task_ids=task_ids,
            account_index=account_index,
            seed=seed,
            at=at,
        )
    )
    return counts


async def seed_standard(
    database: Database,
    settings: Settings,
    profile: SeedProfile,
    *,
    seed: int,
    at: datetime,
) -> SeedManifest:
    if at.tzinfo is None:
        raise ValueError("性能数据基准时间必须包含 UTC offset")
    globals_ = await _prepare_globals(
        database,
        settings,
        profile=profile,
        seed=seed,
        at=at,
    )
    counts: Counter[str] = Counter()
    credentials: list[AccountCredential] = []

    for account_index in range(profile.accounts):
        async with database.transaction() as session:
            owner_id, account_counts = await _seed_account_base(
                session,
                profile=profile,
                account_index=account_index,
                seed=seed,
                at=at,
                password_hash=globals_.password_hash,
                quota_policy_id=globals_.quota_policy_id,
            )
            credential = None
            if account_index < profile.active_accounts:
                account_counts.update(
                    await _seed_account_data(
                        session,
                        profile=profile,
                        owner_id=owner_id,
                        account_index=account_index,
                        seed=seed,
                        at=at,
                    )
                )
                credential = AccountCredential(
                    email=(
                        f"perf-{profile.name}-{seed}-{account_index:03d}"
                        "@performance.invalid"
                    ),
                    password=SYNTHETIC_LOGIN_SECRET,
                    dataset=profile.name,
                )
        counts.update(account_counts)
        if credential is not None:
            credentials.append(credential)

    return SeedManifest(
        profile=profile.name,
        seed=seed,
        generated_at=at.isoformat(),
        accounts=profile.accounts,
        active_accounts=profile.active_accounts,
        counts=dict(sorted(counts.items())),
        credentials=tuple(credentials),
    )
