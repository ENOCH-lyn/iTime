from datetime import UTC, date, datetime, time
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Select, select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from itime.account_deletion.models import AccountDeletion
from itime.ai.conversation_models import (
    ChatGeneration,
    Conversation,
    ConversationMessage,
)
from itime.ai.governance_models import UserPrivacySetting
from itime.ai.models import AIJob, TemporaryMedia
from itime.auth.models import DeviceSession, SecurityEvent, User
from itime.calendar.models import (
    AcademicTerm,
    CalendarAddition,
    CalendarException,
    CalendarImportBatch,
    CalendarImportCandidate,
    CalendarImportIdentity,
    CalendarSeries,
    CalendarSource,
    CalendarSubscription,
    CalendarSubscriptionEvent,
    CalendarSubscriptionOverride,
    CalendarSubscriptionRefresh,
    TermPeriod,
    UserTimeSettings,
)
from itime.calendar.subscription_service import decrypt_subscription_url
from itime.config import Settings
from itime.database import Database
from itime.errors import AppError
from itime.execution.models import (
    DifficultyRecord,
    ExecutionRevision,
    FocusEvent,
    FocusSession,
)
from itime.planning.models import (
    Plan,
    PlanningInputSnapshot,
    PlanSourceConflict,
    PlanUnscheduledItem,
    PlanVersion,
    Proposal,
    ProposalChange,
    ScheduledBlock,
)
from itime.quota.models import (
    AIDailyAllowance,
    AIQuotaAdminAdjustment,
    AIQuotaLedgerEntry,
    AIQuotaReservation,
)
from itime.review.models import (
    Memory,
    MemoryProposal,
    MemorySettings,
    ReviewAnnotation,
    ReviewInputSnapshot,
    ReviewMessage,
    ReviewReportVersion,
    ReviewSession,
)
from itime.tasks.models import (
    Label,
    Project,
    Task,
    TaskLabel,
    TaskLink,
    TaskOccurrenceState,
    TaskSchedule,
    TaskStep,
)

type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | list[JSONValue] | dict[str, JSONValue]
EXPORT_SCHEMA_VERSION = 1


def export_value(value: object) -> JSONValue:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [export_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): export_value(item) for key, item in value.items()}
    raise TypeError(f"unsupported export value: {type(value).__name__}")


def _columns(model: type, fields: tuple[str, ...]):
    return tuple(getattr(model, field).label(field) for field in fields)


async def _rows(
    connection: AsyncConnection,
    model: type,
    fields: tuple[str, ...],
    owner_id: UUID,
    *,
    owner_field: str = "owner_id",
    order: tuple[str, ...] = (),
) -> list[dict[str, JSONValue]]:
    statement: Select = select(*_columns(model, fields)).where(
        getattr(model, owner_field) == owner_id
    )
    ordering = order or fields[:1]
    statement = statement.order_by(*(getattr(model, field) for field in ordering))
    result = await connection.execute(statement)
    return [
        {key: export_value(value) for key, value in row.items()}
        for row in result.mappings()
    ]


async def _one(
    connection: AsyncConnection,
    model: type,
    fields: tuple[str, ...],
    owner_id: UUID,
    *,
    owner_field: str = "owner_id",
) -> dict[str, JSONValue] | None:
    rows = await _rows(
        connection,
        model,
        fields,
        owner_id,
        owner_field=owner_field,
    )
    return rows[0] if rows else None


async def _subscription_rows(
    connection: AsyncConnection,
    owner_id: UUID,
    settings: Settings,
) -> list[dict[str, JSONValue]]:
    fields = (
        "source_id",
        "display_url",
        "display_host",
        "default_time_zone",
        "lifecycle_status",
        "refresh_status",
        "refresh_interval_minutes",
        "next_refresh_at",
        "fetch_revision",
        "generation",
        "attempt_count",
        "consecutive_failures",
        "last_attempt_at",
        "last_success_at",
        "last_change_at",
        "final_host",
        "content_sha256",
        "last_error_code",
        "last_error_message",
        "version",
        "created_at",
        "updated_at",
    )
    statement = (
        select(
            *_columns(CalendarSubscription, fields),
            CalendarSubscription.encrypted_url.label("_encrypted_url"),
        )
        .where(CalendarSubscription.owner_id == owner_id)
        .order_by(CalendarSubscription.source_id)
    )
    result = await connection.execute(statement)
    rows: list[dict[str, JSONValue]] = []
    for raw in result.mappings():
        encrypted_url = raw["_encrypted_url"]
        item = {
            key: export_value(value)
            for key, value in raw.items()
            if key != "_encrypted_url"
        }
        item["url"] = decrypt_subscription_url(settings, encrypted_url)
        rows.append(item)
    return rows


async def _job_rows(
    connection: AsyncConnection,
    owner_id: UUID,
    capability: str,
) -> list[dict[str, JSONValue]]:
    fields = (
        "id",
        "capability",
        "status",
        "error_code",
        "error_message",
        "attempt_count",
        "version",
        "created_at",
        "updated_at",
        "finished_at",
    )
    statement = (
        select(*_columns(AIJob, fields))
        .where(AIJob.owner_id == owner_id, AIJob.capability == capability)
        .order_by(AIJob.created_at, AIJob.id)
    )
    result = await connection.execute(statement)
    return [
        {key: export_value(value) for key, value in row.items()}
        for row in result.mappings()
    ]


async def _account_deletion_rows(
    connection: AsyncConnection,
    owner_id: UUID,
) -> list[dict[str, JSONValue]]:
    fields = (
        "id",
        "status",
        "requested_at",
        "purge_after",
        "cancelled_at",
        "created_at",
        "updated_at",
    )
    result = await connection.execute(
        select(*_columns(AccountDeletion, fields))
        .where(
            AccountDeletion.user_id == owner_id,
            AccountDeletion.status == "cancelled",
        )
        .order_by(
            AccountDeletion.requested_at,
            AccountDeletion.id,
        )
    )
    return [
        {key: export_value(value) for key, value in row.items()}
        for row in result.mappings()
    ]


async def collect_export_snapshot(
    connection: AsyncConnection,
    owner_id: UUID,
    settings: Settings,
    *,
    snapshot_at: datetime,
) -> dict[str, JSONValue]:
    profile = await connection.execute(
        select(
            User.id.label("id"),
            User.email.label("email"),
            User.email_verified_at.label("email_verified_at"),
            User.status.label("status"),
            User.ai_access.label("ai_access"),
            User.is_admin.label("is_admin"),
            User.created_at.label("created_at"),
        ).where(User.id == owner_id)
    )
    profile_row = profile.mappings().first()
    if profile_row is None:
        raise AppError(
            "data_export_owner_missing",
            "账号已不存在，无法继续生成数据导出。",
            status=409,
        )

    account = {
        "profile": {
            key: export_value(value)
            for key, value in profile_row.items()
        },
        "devices": await _rows(
            connection,
            DeviceSession,
            (
                "id",
                "device_name",
                "created_at",
                "last_seen_at",
                "expires_at",
                "revoked_at",
            ),
            owner_id,
            owner_field="user_id",
            order=("created_at", "id"),
        ),
        "security_events": await _rows(
            connection,
            SecurityEvent,
            (
                "id",
                "action",
                "request_id",
                "target_id",
                "details",
                "created_at",
            ),
            owner_id,
            owner_field="user_id",
            order=("created_at", "id"),
        ),
        "account_deletions": await _account_deletion_rows(
            connection,
            owner_id,
        ),
    }
    exported_settings = {
        "time": await _one(
            connection,
            UserTimeSettings,
            (
                "time_zone",
                "week_starts_on",
                "activity_day_start_minute",
                "version",
                "created_at",
                "updated_at",
            ),
            owner_id,
        ),
        "privacy": await _one(
            connection,
            UserPrivacySetting,
            (
                "quality_improvement_enabled",
                "version",
                "created_at",
                "updated_at",
            ),
            owner_id,
        ),
        "memory": await _one(
            connection,
            MemorySettings,
            ("enabled", "version", "created_at", "updated_at"),
            owner_id,
        ),
    }
    tasks = {
        "projects": await _rows(
            connection,
            Project,
            ("id", "kind", "name", "description", "version", "created_at", "updated_at"),
            owner_id,
            order=("created_at", "id"),
        ),
        "tasks": await _rows(
            connection,
            Task,
            (
                "id",
                "project_id",
                "title",
                "description",
                "estimated_minutes",
                "estimate_source",
                "priority",
                "status",
                "completed_at",
                "archived_at",
                "deleted_at",
                "purge_after",
                "purged_at",
                "version",
                "created_at",
                "updated_at",
            ),
            owner_id,
            order=("created_at", "id"),
        ),
        "schedules": await _rows(
            connection,
            TaskSchedule,
            (
                "task_id",
                "predecessor_task_id",
                "due_kind",
                "start_date",
                "last_occurrence_date",
                "due_local_time",
                "due_at",
                "time_zone",
                "ambiguous_offset_minutes",
                "recurrence_frequency",
                "recurrence_interval",
                "recurrence_weekdays",
                "recurrence_month",
                "recurrence_day_of_month",
                "recurrence_until",
                "recurrence_count",
                "splittable",
                "minimum_block_minutes",
                "created_at",
                "updated_at",
            ),
            owner_id,
            order=("task_id",),
        ),
        "occurrences": await _rows(
            connection,
            TaskOccurrenceState,
            (
                "id",
                "task_id",
                "occurrence_key",
                "original_date",
                "kind",
                "effective_date",
                "effective_local_time",
                "effective_at",
                "effective_time_zone",
                "ambiguous_offset_minutes",
                "rescheduled",
                "status",
                "completed_at",
                "completed_step_ids",
                "step_count_snapshot",
                "makeup_for_key",
                "makeup_for_date",
                "version",
                "created_at",
                "updated_at",
            ),
            owner_id,
            order=("effective_date", "task_id", "occurrence_key"),
        ),
        "labels": await _rows(
            connection,
            Label,
            ("id", "name", "version", "created_at", "updated_at"),
            owner_id,
            order=("name", "id"),
        ),
        "task_labels": await _rows(
            connection,
            TaskLabel,
            ("task_id", "label_id"),
            owner_id,
            order=("task_id", "label_id"),
        ),
        "steps": await _rows(
            connection,
            TaskStep,
            (
                "id",
                "task_id",
                "title",
                "position",
                "completed",
                "completed_at",
                "created_at",
                "updated_at",
            ),
            owner_id,
            order=("task_id", "position"),
        ),
        "links": await _rows(
            connection,
            TaskLink,
            (
                "id",
                "task_id",
                "title",
                "url",
                "position",
                "fetch_status",
                "last_attempt_at",
                "last_success_at",
                "final_host",
                "media_type",
                "page_title",
                "summary",
                "extracted_text",
                "content_sha256",
                "last_error_code",
                "last_error_message",
                "last_http_status",
                "updated_at",
                "created_at",
            ),
            owner_id,
            order=("task_id", "position"),
        ),
    }
    calendar = {
        "time_settings": exported_settings["time"],
        "terms": await _rows(
            connection,
            AcademicTerm,
            (
                "id",
                "name",
                "start_date",
                "end_date",
                "first_week_monday",
                "time_zone",
                "version",
                "created_at",
                "updated_at",
            ),
            owner_id,
            order=("start_date", "id"),
        ),
        "term_periods": await _rows(
            connection,
            TermPeriod,
            ("term_id", "period_number", "name", "starts_at", "ends_at"),
            owner_id,
            order=("term_id", "period_number"),
        ),
        "sources": await _rows(
            connection,
            CalendarSource,
            ("id", "kind", "name", "version", "created_at", "updated_at"),
            owner_id,
            order=("created_at", "id"),
        ),
        "subscriptions": await _subscription_rows(
            connection,
            owner_id,
            settings,
        ),
        "subscription_refreshes": await _rows(
            connection,
            CalendarSubscriptionRefresh,
            (
                "id",
                "source_id",
                "fetch_revision",
                "attempt_number",
                "trigger",
                "status",
                "http_status",
                "final_host",
                "event_count",
                "changed_count",
                "cancelled_count",
                "error_count",
                "item_errors",
                "error_code",
                "error_message",
                "started_at",
                "finished_at",
            ),
            owner_id,
            order=("source_id", "attempt_number"),
        ),
        "subscription_events": await _rows(
            connection,
            CalendarSubscriptionEvent,
            (
                "id",
                "source_id",
                "uid",
                "recurrence_id",
                "baseline",
                "parse_status",
                "source_status",
                "error_code",
                "error_message",
                "first_seen_generation",
                "last_seen_generation",
                "cancelled_at",
                "missing_at",
                "version",
                "created_at",
                "updated_at",
            ),
            owner_id,
            order=("source_id", "uid", "recurrence_id"),
        ),
        "subscription_overrides": await _rows(
            connection,
            CalendarSubscriptionOverride,
            (
                "event_id",
                "values",
                "source_values",
                "version",
                "created_at",
                "updated_at",
            ),
            owner_id,
            order=("event_id",),
        ),
        "series": await _rows(
            connection,
            CalendarSeries,
            (
                "id",
                "term_id",
                "predecessor_id",
                "source_id",
                "kind",
                "name",
                "description",
                "location",
                "busy",
                "source_type",
                "time_zone",
                "start_date",
                "starts_at",
                "ends_at",
                "end_day_offset",
                "start_period_number",
                "end_period_number",
                "recurrence_frequency",
                "recurrence_interval",
                "recurrence_weekdays",
                "recurrence_month",
                "recurrence_day_of_month",
                "recurrence_until",
                "recurrence_count",
                "course_first_week_monday",
                "course_week_numbers",
                "ambiguous_offset_minutes",
                "version",
                "created_at",
                "updated_at",
            ),
            owner_id,
            order=("start_date", "starts_at", "id"),
        ),
        "import_batches": await _rows(
            connection,
            CalendarImportBatch,
            (
                "id",
                "source_id",
                "origin_job_id",
                "format",
                "filename",
                "content_sha256",
                "state",
                "candidate_count",
                "version",
                "expires_at",
                "created_at",
                "updated_at",
            ),
            owner_id,
            order=("created_at", "id"),
        ),
        "import_candidates": await _rows(
            connection,
            CalendarImportCandidate,
            (
                "id",
                "batch_id",
                "position",
                "state",
                "raw_data",
                "draft",
                "external_uid",
                "external_recurrence_id",
                "warnings",
                "error_code",
                "error_message",
                "error_details",
                "duplicate_series_id",
                "confirmed_series_id",
                "version",
                "created_at",
                "updated_at",
            ),
            owner_id,
            order=("batch_id", "position"),
        ),
        "import_identities": await _rows(
            connection,
            CalendarImportIdentity,
            (
                "id",
                "source_id",
                "series_id",
                "format",
                "external_uid",
                "external_recurrence_id",
                "created_at",
            ),
            owner_id,
            order=("created_at", "id"),
        ),
        "exceptions": await _rows(
            connection,
            CalendarException,
            (
                "id",
                "series_id",
                "occurrence_key",
                "original_start_date",
                "action",
                "moved_start_date",
                "moved_starts_at",
                "moved_ends_at",
                "moved_end_day_offset",
                "moved_time_zone",
                "moved_ambiguous_offset_minutes",
                "override_name",
                "override_description",
                "override_location",
                "override_busy",
                "version",
                "created_at",
                "updated_at",
            ),
            owner_id,
            order=("series_id", "original_start_date", "id"),
        ),
        "additions": await _rows(
            connection,
            CalendarAddition,
            (
                "id",
                "series_id",
                "occurrence_key",
                "source_occurrence_key",
                "source_original_start_date",
                "status",
                "local_start_date",
                "starts_at",
                "ends_at",
                "end_day_offset",
                "time_zone",
                "ambiguous_offset_minutes",
                "override_name",
                "override_description",
                "override_location",
                "override_busy",
                "version",
                "created_at",
                "updated_at",
            ),
            owner_id,
            order=("series_id", "local_start_date", "id"),
        ),
        "jobs": await _job_rows(connection, owner_id, "calendar_screenshot"),
    }
    conversations = {
        "conversations": await _rows(
            connection,
            Conversation,
            (
                "id",
                "title",
                "title_source",
                "version",
                "archived_at",
                "created_at",
                "updated_at",
            ),
            owner_id,
            order=("created_at", "id"),
        ),
        "messages": await _rows(
            connection,
            ConversationMessage,
            (
                "id",
                "conversation_id",
                "sequence",
                "role",
                "status",
                "content",
                "error_code",
                "error_message",
                "created_at",
                "updated_at",
                "completed_at",
            ),
            owner_id,
            order=("conversation_id", "sequence"),
        ),
        "generations": await _rows(
            connection,
            ChatGeneration,
            (
                "job_id",
                "conversation_id",
                "request_message_id",
                "response_message_id",
                "history_through_sequence",
                "context_message_ids",
                "public_source_context",
                "created_at",
            ),
            owner_id,
            order=("conversation_id", "history_through_sequence"),
        ),
        "jobs": await _job_rows(connection, owner_id, "conversation"),
    }
    planning = {
        "input_snapshots": await _rows(
            connection,
            PlanningInputSnapshot,
            (
                "id",
                "revision",
                "scope",
                "target_date",
                "week_start_date",
                "week_end_date",
                "time_zone",
                "density",
                "buffer_minutes",
                "availability_start",
                "availability_end",
                "user_instructions",
                "selected_task_ids",
                "task_candidates",
                "fixed_occurrences",
                "current_plan_id",
                "current_plan_version",
                "preserved_dates",
                "created_at",
            ),
            owner_id,
            order=("created_at", "id"),
        ),
        "proposals": await _rows(
            connection,
            Proposal,
            (
                "id",
                "source_job_id",
                "conversation_id",
                "source_message_id",
                "input_snapshot_id",
                "schema_version",
                "status",
                "title",
                "summary",
                "assumptions",
                "warnings",
                "payload",
                "expires_at",
                "applied_at",
                "rejected_at",
                "applied_plan_id",
                "applied_plan_version_id",
                "version",
                "created_at",
                "updated_at",
            ),
            owner_id,
            order=("created_at", "id"),
        ),
        "proposal_changes": await _rows(
            connection,
            ProposalChange,
            (
                "id",
                "proposal_id",
                "position",
                "action",
                "entity_type",
                "target_id",
                "before_data",
                "after_data",
                "created_at",
            ),
            owner_id,
            order=("proposal_id", "position"),
        ),
        "plans": await _rows(
            connection,
            Plan,
            (
                "id",
                "week_start_date",
                "time_zone",
                "current_version_id",
                "version",
                "created_at",
                "updated_at",
            ),
            owner_id,
            order=("week_start_date", "id"),
        ),
        "versions": await _rows(
            connection,
            PlanVersion,
            (
                "id",
                "plan_id",
                "version",
                "source",
                "proposal_id",
                "scope",
                "target_date",
                "density",
                "buffer_minutes",
                "availability_start",
                "availability_end",
                "summary",
                "assumptions",
                "input_revision",
                "created_at",
            ),
            owner_id,
            order=("plan_id", "version"),
        ),
        "blocks": await _rows(
            connection,
            ScheduledBlock,
            (
                "id",
                "plan_version_id",
                "position",
                "task_id",
                "occurrence_key",
                "task_title_snapshot",
                "step_ids",
                "step_titles_snapshot",
                "local_date",
                "local_starts_at",
                "local_ends_at",
                "time_zone",
                "starts_at",
                "ends_at",
                "estimate_source",
                "estimated_minutes",
                "created_at",
            ),
            owner_id,
            order=("plan_version_id", "position"),
        ),
        "source_conflicts": await _rows(
            connection,
            PlanSourceConflict,
            (
                "id",
                "plan_id",
                "plan_version_id",
                "source_id",
                "source_generation",
                "status",
                "resolution",
                "changes",
                "affected_block_ids",
                "details_limited",
                "version",
                "detected_at",
                "resolved_at",
            ),
            owner_id,
            order=("detected_at", "id"),
        ),
        "unscheduled_items": await _rows(
            connection,
            PlanUnscheduledItem,
            (
                "id",
                "plan_version_id",
                "position",
                "task_id",
                "occurrence_key",
                "task_title_snapshot",
                "remaining_minutes",
                "reason",
                "explanation",
                "created_at",
            ),
            owner_id,
            order=("plan_version_id", "position"),
        ),
        "jobs": await _job_rows(connection, owner_id, "planning"),
    }
    execution = {
        "focus_sessions": await _rows(
            connection,
            FocusSession,
            (
                "id",
                "source",
                "task_id",
                "occurrence_key",
                "scheduled_block_id",
                "title_snapshot",
                "task_title_snapshot",
                "step_snapshots",
                "mode",
                "target_seconds",
                "status",
                "started_at",
                "ended_at",
                "active_started_at",
                "accumulated_seconds",
                "effective_started_at",
                "effective_ended_at",
                "effective_duration_seconds",
                "time_zone",
                "version",
                "created_at",
                "updated_at",
            ),
            owner_id,
            order=("started_at", "id"),
        ),
        "focus_events": await _rows(
            connection,
            FocusEvent,
            (
                "id",
                "focus_session_id",
                "sequence",
                "kind",
                "occurred_at",
                "client_recorded_at",
                "source",
                "details",
                "created_at",
            ),
            owner_id,
            order=("focus_session_id", "sequence"),
        ),
        "revisions": await _rows(
            connection,
            ExecutionRevision,
            (
                "id",
                "focus_session_id",
                "session_version",
                "before_data",
                "after_data",
                "reason",
                "created_at",
            ),
            owner_id,
            order=("focus_session_id", "session_version"),
        ),
        "difficulties": await _rows(
            connection,
            DifficultyRecord,
            (
                "id",
                "task_id",
                "focus_session_id",
                "occurrence_key",
                "task_title_snapshot",
                "content",
                "category",
                "emotion",
                "occurred_at",
                "time_zone",
                "deleted_at",
                "purge_after",
                "purged_at",
                "version",
                "created_at",
                "updated_at",
            ),
            owner_id,
            order=("occurred_at", "id"),
        ),
    }
    reviews = {
        "sessions": await _rows(
            connection,
            ReviewSession,
            (
                "id",
                "activity_date",
                "time_zone",
                "supplemental_facts",
                "input_revision",
                "question_count",
                "version",
                "created_at",
                "updated_at",
            ),
            owner_id,
            order=("activity_date", "id"),
        ),
        "input_snapshots": await _rows(
            connection,
            ReviewInputSnapshot,
            (
                "id",
                "review_session_id",
                "purpose",
                "activity_date",
                "time_zone",
                "activity_started_at",
                "activity_ended_at",
                "session_input_revision",
                "facts",
                "user_context",
                "created_at",
            ),
            owner_id,
            order=("review_session_id", "created_at", "id"),
        ),
        "messages": await _rows(
            connection,
            ReviewMessage,
            (
                "id",
                "review_session_id",
                "sequence",
                "role",
                "kind",
                "content",
                "source_job_id",
                "created_at",
            ),
            owner_id,
            order=("review_session_id", "sequence"),
        ),
        "reports": await _rows(
            connection,
            ReviewReportVersion,
            (
                "id",
                "review_session_id",
                "version",
                "source_job_id",
                "input_snapshot_id",
                "summary",
                "facts",
                "user_statements",
                "insights",
                "next_actions",
                "uncertainties",
                "created_at",
            ),
            owner_id,
            order=("review_session_id", "version"),
        ),
        "annotations": await _rows(
            connection,
            ReviewAnnotation,
            (
                "id",
                "report_version_id",
                "content",
                "deleted_at",
                "version",
                "created_at",
                "updated_at",
            ),
            owner_id,
            order=("report_version_id", "created_at", "id"),
        ),
        "memory_proposals": await _rows(
            connection,
            MemoryProposal,
            (
                "id",
                "report_version_id",
                "position",
                "kind",
                "title",
                "content",
                "rationale",
                "status",
                "confirmed_at",
                "rejected_at",
                "version",
                "created_at",
                "updated_at",
            ),
            owner_id,
            order=("report_version_id", "position"),
        ),
        "memories": await _rows(
            connection,
            Memory,
            (
                "id",
                "kind",
                "title",
                "content",
                "source_type",
                "source_proposal_id",
                "deleted_at",
                "purge_after",
                "purged_at",
                "version",
                "created_at",
                "updated_at",
            ),
            owner_id,
            order=("created_at", "id"),
        ),
        "jobs": await _job_rows(connection, owner_id, "review"),
    }
    quota = {
        "daily_allowances": await _rows(
            connection,
            AIDailyAllowance,
            (
                "id",
                "activity_date",
                "window_start_at",
                "window_end_at",
                "time_zone",
                "activity_day_start_minute",
                "currency",
                "base_amount_nanos",
                "version",
                "created_at",
                "updated_at",
            ),
            owner_id,
            order=("window_start_at", "id"),
        ),
        "reservations": await _rows(
            connection,
            AIQuotaReservation,
            (
                "id",
                "job_id",
                "daily_allowance_id",
                "status",
                "reserved_nanos",
                "charged_nanos",
                "usage_source",
                "release_reason",
                "accepted_at",
                "terminal_at",
                "created_at",
                "updated_at",
            ),
            owner_id,
            order=("accepted_at", "id"),
        ),
        "adjustments": await _rows(
            connection,
            AIQuotaAdminAdjustment,
            (
                "id",
                "daily_allowance_id",
                "delta_nanos",
                "reason",
                "created_at",
            ),
            owner_id,
            owner_field="target_owner_id",
            order=("created_at", "id"),
        ),
        "ledger": await _rows(
            connection,
            AIQuotaLedgerEntry,
            (
                "id",
                "daily_allowance_id",
                "reservation_id",
                "adjustment_id",
                "job_id",
                "kind",
                "delta_nanos",
                "currency",
                "usage_source",
                "created_at",
            ),
            owner_id,
            order=("created_at", "id"),
        ),
    }
    media = {
        "temporary_media": await _rows(
            connection,
            TemporaryMedia,
            (
                "id",
                "purpose",
                "original_filename",
                "content_type",
                "content_sha256",
                "byte_size",
                "width",
                "height",
                "expires_at",
                "deleted_at",
                "deletion_reason",
                "created_at",
            ),
            owner_id,
            order=("created_at", "id"),
        )
    }
    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "snapshot_at": export_value(snapshot_at),
        "account": account,
        "settings": exported_settings,
        "tasks": tasks,
        "calendar": calendar,
        "conversations": conversations,
        "planning": planning,
        "execution": execution,
        "reviews": reviews,
        "quota": quota,
        "media": media,
    }


async def read_consistent_snapshot(
    database: Database,
    owner_id: UUID,
    settings: Settings,
) -> dict[str, JSONValue]:
    async with database.engine.connect() as raw_connection:
        connection = await raw_connection.execution_options(
            isolation_level="REPEATABLE READ"
        )
        async with connection.begin():
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            snapshot_at = await connection.scalar(
                text("SELECT transaction_timestamp()")
            )
            if not isinstance(snapshot_at, datetime):
                raise RuntimeError("database did not return a snapshot timestamp")
            return await collect_export_snapshot(
                connection,
                owner_id,
                settings,
                snapshot_at=snapshot_at,
            )
