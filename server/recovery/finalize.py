from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from itime.account_deletion.models import AccountDeletion
from itime.admin.models import AdminSession
from itime.ai.conversation_models import AIJobEvent
from itime.ai.governance_models import AIBodyLog, AIQualitySample
from itime.ai.job_service import (
    cancel_active_ai_jobs_for_owner,
    delete_media,
)
from itime.ai.models import AIJob, TemporaryMedia, VoiceTranscription
from itime.ai.transcription_service import _expire_locked_transcription
from itime.auth.models import (
    AuthRateBucket,
    DeviceSession,
    EmailToken,
    MailOutbox,
    User,
)
from itime.calendar.lifecycle_service import _purge_series, _purge_source
from itime.calendar.models import (
    CalendarSeries,
    CalendarSource,
    CalendarSubscription,
)
from itime.calendar.subscription_service import stale_running_refresh
from itime.config import Settings
from itime.data_export.models import DataExportJob
from itime.database import Database
from itime.execution.difficulty_service import _purge_difficulty
from itime.execution.models import DifficultyRecord, FocusEvent, FocusSession
from itime.execution.service import _last_event_sequence, apply_transition
from itime.review.memory_service import _purge_memory
from itime.review.models import Memory
from itime.sync.models import SyncState
from itime.tasks.lifecycle_service import _purge_task
from itime.tasks.models import ChangeEvent, Task, TaskLink
from itime.tasks.service import emit_change

_ACTIVE_AI_STATES = ("queued", "running")
_OPEN_FOCUS_STATES = ("running", "paused")


class RecoveryFinalizationError(RuntimeError):
    """恢复库存在无法自动收敛的业务事实。"""

    def __init__(
        self,
        message: str,
        *,
        inconsistencies: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.inconsistencies = inconsistencies


@dataclass(frozen=True)
class FinalizationSummary:
    sessions_revoked: int
    tokens_expired: int
    jobs_recovered: dict[str, int]
    focus_abandoned: int
    retention_cleaned: dict[str, int]
    inconsistencies: tuple[str, ...]


async def _exists(session: AsyncSession, query: str) -> bool:
    return bool(await session.scalar(text(query)))


async def _collect_inconsistencies(
    session: AsyncSession,
    *,
    require_quiesced: bool,
) -> tuple[str, ...]:
    checks = (
        (
            "account_deletion_completed_user_present",
            """
            SELECT EXISTS (
                SELECT 1 FROM account_deletions
                WHERE status='completed' AND user_id IS NOT NULL
            )
            """,
        ),
        (
            "ai_job_lease_state",
            """
            SELECT EXISTS (
                SELECT 1 FROM ai_jobs
                WHERE
                    ((status='running') <>
                     (lease_id IS NOT NULL AND lease_until IS NOT NULL))
                    OR (
                        status IN ('succeeded', 'failed', 'timed_out', 'cancelled')
                        AND finished_at IS NULL
                    )
                    OR (
                        status IN ('queued', 'running')
                        AND finished_at IS NOT NULL
                    )
            )
            """,
        ),
        (
            "ai_attempt_lease_state",
            """
            SELECT EXISTS (
                SELECT 1
                FROM ai_job_attempts AS attempt
                LEFT JOIN ai_jobs AS job ON job.id=attempt.job_id
                WHERE attempt.status='running'
                  AND (
                    job.id IS NULL
                    OR job.status <> 'running'
                    OR job.lease_id IS DISTINCT FROM attempt.lease_id
                  )
            )
            """,
        ),
        (
            "ai_body_log_state",
            """
            SELECT EXISTS (
                SELECT 1
                FROM ai_body_logs AS body
                LEFT JOIN ai_job_attempts AS attempt
                  ON attempt.job_id=body.job_id
                 AND attempt.attempt_number=body.attempt_number
                WHERE body.status='running'
                  AND (
                    attempt.id IS NULL
                    OR attempt.status <> 'running'
                  )
            )
            """,
        ),
        (
            "quota_job_mode",
            """
            SELECT EXISTS (
                SELECT 1
                FROM ai_jobs AS job
                LEFT JOIN ai_quota_reservations AS reservation
                  ON reservation.job_id=job.id
                WHERE
                    (job.quota_mode='user' AND reservation.id IS NULL)
                    OR (job.quota_mode='platform' AND reservation.id IS NOT NULL)
                    OR (
                        job.quota_mode='user'
                        AND job.status IN ('queued', 'running')
                        AND reservation.status <> 'reserved'
                    )
                    OR (
                        job.quota_mode='user'
                        AND job.status='succeeded'
                        AND reservation.status <> 'settled'
                    )
                    OR (
                        job.quota_mode='user'
                        AND job.status IN ('failed', 'timed_out', 'cancelled')
                        AND reservation.status <> 'released'
                    )
            )
            """,
        ),
        (
            "quota_settlement_cost",
            """
            SELECT EXISTS (
                SELECT 1
                FROM ai_quota_reservations AS reservation
                JOIN ai_daily_allowances AS daily
                  ON daily.id=reservation.daily_allowance_id
                 AND daily.owner_id=reservation.owner_id
                LEFT JOIN ai_provider_costs AS cost
                  ON cost.id=reservation.provider_cost_id
                WHERE reservation.status='settled'
                  AND (
                    cost.id IS NULL
                    OR cost.job_id IS DISTINCT FROM reservation.job_id
                    OR cost.owner_id IS DISTINCT FROM reservation.owner_id
                    OR cost.attempt_status <> 'succeeded'
                    OR cost.total_cost_nanos IS NULL
                    OR cost.price_currency IS DISTINCT FROM daily.currency
                    OR reservation.actual_cost_nanos
                       IS DISTINCT FROM cost.total_cost_nanos
                    OR reservation.charged_nanos
                       IS DISTINCT FROM LEAST(
                           reservation.reserved_nanos,
                           cost.total_cost_nanos
                       )
                    OR reservation.platform_overage_nanos
                       IS DISTINCT FROM (
                           cost.total_cost_nanos - reservation.charged_nanos
                       )
                    OR reservation.usage_source
                       IS DISTINCT FROM cost.usage_source
                  )
            )
            """,
        ),
        (
            "quota_settlement_ledger",
            """
            SELECT EXISTS (
                SELECT 1
                FROM ai_quota_reservations AS reservation
                JOIN ai_daily_allowances AS daily
                  ON daily.id=reservation.daily_allowance_id
                 AND daily.owner_id=reservation.owner_id
                WHERE reservation.status='settled'
                  AND (
                    SELECT count(*)
                    FROM ai_quota_ledger_entries AS ledger
                    WHERE ledger.reservation_id=reservation.id
                      AND ledger.kind='usage_charge'
                      AND ledger.owner_id=reservation.owner_id
                      AND ledger.daily_allowance_id=reservation.daily_allowance_id
                      AND ledger.job_id=reservation.job_id
                      AND ledger.provider_cost_id
                          IS NOT DISTINCT FROM reservation.provider_cost_id
                      AND ledger.delta_nanos=-reservation.charged_nanos
                      AND ledger.currency=daily.currency
                      AND ledger.usage_source=reservation.usage_source
                  ) <> 1
            )
            """,
        ),
    )
    inconsistencies = [
        code for code, query in checks if await _exists(session, query)
    ]
    if require_quiesced:
        runtime_checks = (
            (
                "active_ai_jobs",
                """
                SELECT EXISTS (
                    SELECT 1 FROM ai_jobs
                    WHERE status IN ('queued', 'running')
                )
                """,
            ),
            (
                "active_quota_reservations",
                """
                SELECT EXISTS (
                    SELECT 1 FROM ai_quota_reservations
                    WHERE status='reserved'
                )
                """,
            ),
            (
                "running_data_exports",
                """
                SELECT EXISTS (
                    SELECT 1 FROM data_export_jobs WHERE status='running'
                )
                """,
            ),
            (
                "running_calendar_subscriptions",
                """
                SELECT EXISTS (
                    SELECT 1 FROM calendar_subscriptions
                    WHERE refresh_status='running'
                )
                """,
            ),
            (
                "running_public_links",
                """
                SELECT EXISTS (
                    SELECT 1 FROM task_links WHERE fetch_status='running'
                )
                """,
            ),
            (
                "processing_account_deletions",
                """
                SELECT EXISTS (
                    SELECT 1 FROM account_deletions
                    WHERE status='processing'
                )
                """,
            ),
            (
                "open_focus_sessions",
                """
                SELECT EXISTS (
                    SELECT 1 FROM focus_sessions
                    WHERE status IN ('running', 'paused')
                )
                """,
            ),
            (
                "unrevoked_sessions",
                """
                SELECT EXISTS (
                    SELECT 1 FROM device_sessions WHERE revoked_at IS NULL
                    UNION ALL
                    SELECT 1 FROM admin_sessions WHERE revoked_at IS NULL
                )
                """,
            ),
        )
        for code, query in runtime_checks:
            if await _exists(session, query):
                inconsistencies.append(code)
    return tuple(sorted(inconsistencies))


async def _revoke_sessions(
    session: AsyncSession,
    *,
    at: datetime,
) -> int:
    affected_users = set(
        await session.scalars(
            select(DeviceSession.user_id).where(
                DeviceSession.revoked_at.is_(None)
            )
        )
    )
    affected_users.update(
        await session.scalars(
            select(AdminSession.user_id).where(
                AdminSession.revoked_at.is_(None)
            )
        )
    )
    device_result = await session.execute(
        update(DeviceSession)
        .where(DeviceSession.revoked_at.is_(None))
        .values(revoked_at=at)
    )
    admin_result = await session.execute(
        update(AdminSession)
        .where(AdminSession.revoked_at.is_(None))
        .values(revoked_at=at)
    )
    if affected_users:
        await session.execute(
            update(User)
            .where(User.id.in_(affected_users))
            .values(auth_epoch=User.auth_epoch + 1)
        )
    return (device_result.rowcount or 0) + (admin_result.rowcount or 0)


async def _expire_tokens_and_mail(
    session: AsyncSession,
    *,
    at: datetime,
) -> tuple[int, int]:
    token_result = await session.execute(
        update(EmailToken)
        .where(EmailToken.used_at.is_(None))
        .values(used_at=at)
    )
    mail_result = await session.execute(
        update(MailOutbox)
        .where(MailOutbox.finished_at.is_(None))
        .values(
            encrypted_message=None,
            lease_id=None,
            lease_until=None,
            finished_at=at,
            error_code="recovery_finalization",
        )
    )
    return token_result.rowcount or 0, mail_result.rowcount or 0


async def _cancel_ai_jobs(session: AsyncSession) -> int:
    owner_ids = list(
        await session.scalars(
            select(AIJob.owner_id)
            .where(AIJob.status.in_(_ACTIVE_AI_STATES))
            .distinct()
            .order_by(AIJob.owner_id)
        )
    )
    cancelled = 0
    for owner_id in owner_ids:
        cancelled += await cancel_active_ai_jobs_for_owner(session, owner_id)
    return cancelled


async def _recover_exports(
    session: AsyncSession,
    *,
    at: datetime,
) -> int:
    jobs = list(
        await session.scalars(
            select(DataExportJob)
            .where(DataExportJob.status == "running")
            .order_by(DataExportJob.created_at, DataExportJob.id)
            .with_for_update()
        )
    )
    for job in jobs:
        job.status = "queued"
        job.available_at = at
        job.lease_id = None
        job.lease_until = None
        job.updated_at = at
    return len(jobs)


async def _recover_subscriptions(
    session: AsyncSession,
    *,
    at: datetime,
) -> int:
    subscriptions = list(
        await session.scalars(
            select(CalendarSubscription)
            .where(CalendarSubscription.refresh_status == "running")
            .order_by(CalendarSubscription.source_id)
            .with_for_update()
        )
    )
    for subscription in subscriptions:
        await stale_running_refresh(
            session,
            subscription,
            at,
            error_code="calendar_subscription_recovery_interrupted",
            error_message="恢复演练已废止旧订阅租约，原结果不会应用。",
        )
        subscription.refresh_status = (
            "queued"
            if subscription.lifecycle_status == "active"
            else "idle"
        )
        subscription.next_refresh_at = min(subscription.next_refresh_at, at)
        subscription.lease_id = None
        subscription.lease_until = None
        subscription.updated_at = at
        subscription.version += 1
    return len(subscriptions)


async def _recover_public_links(
    session: AsyncSession,
    *,
    at: datetime,
) -> int:
    links = list(
        await session.scalars(
            select(TaskLink)
            .where(TaskLink.fetch_status == "running")
            .order_by(TaskLink.owner_id, TaskLink.id)
            .with_for_update()
        )
    )
    for link in links:
        link.fetch_status = "queued"
        link.fetch_revision += 1
        link.available_at = at
        link.lease_id = None
        link.lease_until = None
        link.last_error_code = None
        link.last_error_message = None
        link.last_http_status = None
        link.updated_at = at
        emit_change(
            session,
            link.owner_id,
            "task_link",
            link.id,
            "updated",
            link.fetch_revision,
        )
    return len(links)


async def _recover_account_deletions(
    session: AsyncSession,
    *,
    at: datetime,
) -> int:
    deletions = list(
        await session.scalars(
            select(AccountDeletion)
            .where(AccountDeletion.status == "processing")
            .order_by(AccountDeletion.purge_after, AccountDeletion.id)
            .with_for_update()
        )
    )
    for deletion in deletions:
        deletion.status = "pending"
        deletion.available_at = at
        deletion.lease_id = None
        deletion.lease_until = None
        deletion.last_error_code = "account_deletion_recovery_interrupted"
        deletion.last_error_message = (
            "数据库恢复后已释放旧清理租约，等待安全重试。"
        )
        deletion.updated_at = at
    return len(deletions)


async def _abandon_focus(
    session: AsyncSession,
    *,
    at: datetime,
) -> int:
    focuses = list(
        await session.scalars(
            select(FocusSession)
            .where(FocusSession.status.in_(_OPEN_FOCUS_STATES))
            .order_by(FocusSession.owner_id, FocusSession.started_at, FocusSession.id)
            .with_for_update()
        )
    )
    for focus in focuses:
        sequence = await _last_event_sequence(session, focus.id)
        apply_transition(focus, "abandon", at)
        focus.version += 1
        focus.updated_at = at
        session.add(
            FocusEvent(
                owner_id=focus.owner_id,
                focus_session_id=focus.id,
                sequence=sequence + 1,
                kind="abandon",
                occurred_at=at,
                client_recorded_at=None,
                source="online",
                details={"reason": "recovery_finalization"},
            )
        )
        emit_change(
            session,
            focus.owner_id,
            "focus_session",
            focus.id,
            "abandoned",
            focus.version,
        )
    return len(focuses)


async def _purge_due_tasks(
    session: AsyncSession,
    *,
    at: datetime,
) -> int:
    tasks = list(
        await session.scalars(
            select(Task)
            .where(
                Task.deleted_at.is_not(None),
                Task.purge_after <= at,
                Task.purged_at.is_(None),
            )
            .order_by(Task.purge_after, Task.id)
            .with_for_update()
        )
    )
    for task in tasks:
        await _purge_task(session, task)
    return len(tasks)


async def _purge_due_calendar(
    session: AsyncSession,
    *,
    at: datetime,
) -> tuple[int, int]:
    series_rows = list(
        await session.scalars(
            select(CalendarSeries)
            .join(User, User.id == CalendarSeries.owner_id)
            .where(
                User.status == "active",
                CalendarSeries.deleted_at.is_not(None),
                CalendarSeries.purge_after <= at,
                CalendarSeries.purged_at.is_(None),
            )
            .order_by(
                CalendarSeries.owner_id,
                CalendarSeries.purge_after,
                CalendarSeries.id,
            )
            .with_for_update(of=(User, CalendarSeries))
        )
    )
    for series in series_rows:
        await _purge_series(session, series, current_time=at)

    source_rows = list(
        await session.scalars(
            select(CalendarSource)
            .join(User, User.id == CalendarSource.owner_id)
            .where(
                User.status == "active",
                CalendarSource.kind == "ics_subscription",
                CalendarSource.deleted_at.is_not(None),
                CalendarSource.purge_after <= at,
                CalendarSource.purged_at.is_(None),
            )
            .order_by(
                CalendarSource.owner_id,
                CalendarSource.purge_after,
                CalendarSource.id,
            )
            .with_for_update(of=(User, CalendarSource))
        )
    )
    for source in source_rows:
        await _purge_source(session, source, current_time=at)
    return len(series_rows), len(source_rows)


async def _purge_due_difficulties(
    session: AsyncSession,
    *,
    at: datetime,
) -> int:
    rows = list(
        await session.scalars(
            select(DifficultyRecord)
            .where(
                DifficultyRecord.deleted_at.is_not(None),
                DifficultyRecord.purge_after <= at,
                DifficultyRecord.purged_at.is_(None),
            )
            .order_by(DifficultyRecord.purge_after, DifficultyRecord.id)
            .with_for_update()
        )
    )
    for record in rows:
        await _purge_difficulty(session, record, current_time=at)
    return len(rows)


async def _purge_due_memories(
    session: AsyncSession,
    *,
    at: datetime,
) -> int:
    rows = list(
        await session.scalars(
            select(Memory)
            .where(
                Memory.deleted_at.is_not(None),
                Memory.purge_after <= at,
                Memory.purged_at.is_(None),
            )
            .order_by(Memory.purge_after, Memory.id)
            .with_for_update()
        )
    )
    for memory in rows:
        await _purge_memory(session, memory, current_time=at)
    return len(rows)


async def _expire_transcriptions(
    session: AsyncSession,
    *,
    at: datetime,
) -> int:
    rows = list(
        (
            await session.execute(
                select(AIJob, VoiceTranscription, TemporaryMedia)
                .join(
                    VoiceTranscription,
                    VoiceTranscription.job_id == AIJob.id,
                )
                .join(
                    TemporaryMedia,
                    TemporaryMedia.id == VoiceTranscription.media_id,
                )
                .where(
                    VoiceTranscription.expires_at <= at,
                    (
                        (VoiceTranscription.resolution == "pending")
                        | VoiceTranscription.transcript.is_not(None)
                    ),
                )
                .order_by(
                    VoiceTranscription.expires_at,
                    VoiceTranscription.id,
                )
                .with_for_update(of=(AIJob, VoiceTranscription, TemporaryMedia))
            )
        ).all()
    )
    for job, item, media in rows:
        await _expire_locked_transcription(
            session,
            job,
            item,
            media,
            current=at,
        )
    return len(rows)


async def _cleanup_sync_events(
    session: AsyncSession,
    *,
    at: datetime,
    retention_days: int,
) -> int:
    cutoff = at - timedelta(days=retention_days)
    rows = list(
        (
            await session.execute(
                select(ChangeEvent.sequence, ChangeEvent.owner_id)
                .where(ChangeEvent.changed_at <= cutoff)
                .order_by(ChangeEvent.changed_at, ChangeEvent.sequence)
                .with_for_update()
            )
        ).all()
    )
    if not rows:
        return 0
    owner_ids = sorted({row.owner_id for row in rows}, key=str)
    states = list(
        await session.scalars(
            select(SyncState)
            .where(SyncState.owner_id.in_(owner_ids))
            .order_by(SyncState.owner_id)
            .with_for_update()
        )
    )
    if len(states) != len(owner_ids):
        raise RecoveryFinalizationError(
            "同步事件缺少账号水位，恢复库不可开放。",
            inconsistencies=("sync_state_unavailable",),
        )
    discarded_by_owner: dict[UUID, int] = {}
    sequences: list[int] = []
    for row in rows:
        sequences.append(row.sequence)
        discarded_by_owner[row.owner_id] = max(
            discarded_by_owner.get(row.owner_id, 0),
            row.sequence,
        )
    await session.execute(
        delete(ChangeEvent).where(ChangeEvent.sequence.in_(sequences))
    )
    for state in states:
        state.discarded_through_sequence = max(
            state.discarded_through_sequence,
            discarded_by_owner[state.owner_id],
        )
        state.last_cleanup_at = at
        state.updated_at = at
    return len(rows)


async def _run_retention(
    session: AsyncSession,
    settings: Settings,
    *,
    at: datetime,
) -> dict[str, int]:
    tasks = await _purge_due_tasks(session, at=at)
    calendar_series, calendar_sources = await _purge_due_calendar(
        session,
        at=at,
    )
    difficulties = await _purge_due_difficulties(session, at=at)
    memories = await _purge_due_memories(session, at=at)
    voice_transcriptions = await _expire_transcriptions(session, at=at)

    media_rows = list(
        await session.scalars(
            select(TemporaryMedia)
            .where(
                TemporaryMedia.encrypted_content.is_not(None),
                TemporaryMedia.purpose != "voice_transcription",
                TemporaryMedia.expires_at <= at,
            )
            .order_by(TemporaryMedia.expires_at, TemporaryMedia.id)
            .with_for_update()
        )
    )
    for media in media_rows:
        delete_media(media, reason="expired")

    export_rows = list(
        await session.scalars(
            select(DataExportJob)
            .where(
                DataExportJob.status == "succeeded",
                DataExportJob.expires_at <= at,
                DataExportJob.artifact_ciphertext.is_not(None),
            )
            .order_by(DataExportJob.expires_at, DataExportJob.id)
            .with_for_update()
        )
    )
    for job in export_rows:
        job.status = "expired"
        job.artifact_ciphertext = None
        job.updated_at = at

    metadata_cutoff = at - timedelta(days=settings.export_job_retention_days)
    metadata_ids = list(
        await session.scalars(
            select(DataExportJob.id)
            .where(
                DataExportJob.status.in_(("failed", "expired")),
                DataExportJob.finished_at <= metadata_cutoff,
                DataExportJob.artifact_ciphertext.is_(None),
            )
            .order_by(DataExportJob.finished_at, DataExportJob.id)
            .with_for_update()
        )
    )
    if metadata_ids:
        await session.execute(
            delete(DataExportJob).where(DataExportJob.id.in_(metadata_ids))
        )

    body_ids = list(
        await session.scalars(
            select(AIBodyLog.id)
            .where(AIBodyLog.expires_at <= at)
            .order_by(AIBodyLog.expires_at, AIBodyLog.id)
            .with_for_update()
        )
    )
    if body_ids:
        await session.execute(delete(AIBodyLog).where(AIBodyLog.id.in_(body_ids)))
    sample_ids = list(
        await session.scalars(
            select(AIQualitySample.id)
            .where(AIQualitySample.expires_at <= at)
            .order_by(AIQualitySample.expires_at, AIQualitySample.id)
            .with_for_update()
        )
    )
    if sample_ids:
        await session.execute(
            delete(AIQualitySample).where(AIQualitySample.id.in_(sample_ids))
        )
    event_ids = list(
        await session.scalars(
            select(AIJobEvent.id)
            .where(AIJobEvent.expires_at <= at)
            .order_by(AIJobEvent.expires_at, AIJobEvent.id)
            .with_for_update()
        )
    )
    if event_ids:
        await session.execute(
            delete(AIJobEvent).where(AIJobEvent.id.in_(event_ids))
        )
    rate_result = await session.execute(delete(AuthRateBucket))
    sync_events = await _cleanup_sync_events(
        session,
        at=at,
        retention_days=settings.sync_event_retention_days,
    )
    return {
        "ai_body_logs": len(body_ids),
        "ai_job_events": len(event_ids),
        "ai_quality_samples": len(sample_ids),
        "auth_rate_buckets": rate_result.rowcount or 0,
        "calendar_series": calendar_series,
        "calendar_sources": calendar_sources,
        "data_export_artifacts": len(export_rows),
        "data_export_metadata": len(metadata_ids),
        "difficulties": difficulties,
        "memories": memories,
        "sync_events": sync_events,
        "tasks": tasks,
        "temporary_media": len(media_rows),
        "voice_transcriptions": voice_transcriptions,
    }


async def finalize_recovered_database(
    settings: Settings,
    *,
    at: datetime,
) -> FinalizationSummary:
    if at.tzinfo is None:
        raise RecoveryFinalizationError("恢复收尾时间必须包含时区")
    recovery_time = at.astimezone(UTC)
    database = Database(settings)
    try:
        async with database.transaction() as session:
            inconsistencies = await _collect_inconsistencies(
                session,
                require_quiesced=False,
            )
            if inconsistencies:
                raise RecoveryFinalizationError(
                    "恢复库存在不可自动修复的业务状态。",
                    inconsistencies=inconsistencies,
                )

            sessions_revoked = await _revoke_sessions(
                session,
                at=recovery_time,
            )
            tokens_expired, mail_outbox = await _expire_tokens_and_mail(
                session,
                at=recovery_time,
            )
            jobs_recovered = {
                "account_deletions": await _recover_account_deletions(
                    session,
                    at=recovery_time,
                ),
                "ai_jobs": await _cancel_ai_jobs(session),
                "calendar_subscriptions": await _recover_subscriptions(
                    session,
                    at=recovery_time,
                ),
                "data_exports": await _recover_exports(
                    session,
                    at=recovery_time,
                ),
                "mail_outbox": mail_outbox,
                "public_links": await _recover_public_links(
                    session,
                    at=recovery_time,
                ),
            }
            focus_abandoned = await _abandon_focus(
                session,
                at=recovery_time,
            )
            retention_cleaned = await _run_retention(
                session,
                settings,
                at=recovery_time,
            )
            await session.flush()
            inconsistencies = await _collect_inconsistencies(
                session,
                require_quiesced=True,
            )
            if inconsistencies:
                raise RecoveryFinalizationError(
                    "恢复收尾后业务状态仍不一致。",
                    inconsistencies=inconsistencies,
                )
            return FinalizationSummary(
                sessions_revoked=sessions_revoked,
                tokens_expired=tokens_expired,
                jobs_recovered=jobs_recovered,
                focus_abandoned=focus_abandoned,
                retention_cleaned=retention_cleaned,
                inconsistencies=(),
            )
    finally:
        await database.close()
