package com.itime.app.data.sync

import com.itime.app.data.remote.RecurrenceBody
import kotlinx.serialization.Serializable

/**
 * Payloads stored in the outbox.  They intentionally do NOT carry
 * ``expected_version``: the sync engine injects the version it last observed
 * from the server right before sending.  That keeps queued edits rebasable
 * after an earlier queued write for the same entity succeeds.
 */
@Serializable
data class ScheduleSpec(
    val dueKind: String = "none",
    val dueDate: String? = null,
    val dueTime: String? = null,
    val timeZone: String? = null,
    val recurrence: RecurrenceBody = RecurrenceBody(),
)

@Serializable
data class TaskCreatePayload(
    val title: String,
    val description: String = "",
    val projectId: String? = null,
    val estimatedMinutes: Int? = null,
    val priority: Int = 0,
    val labels: List<String> = emptyList(),
    val steps: List<String> = emptyList(),
    val schedule: ScheduleSpec? = null,
)

@Serializable
data class TaskUpdatePayload(
    val title: String,
    val description: String = "",
    val projectId: String? = null,
    val estimatedMinutes: Int? = null,
    val priority: Int = 0,
    val labels: List<String> = emptyList(),
)

@Serializable
data class TaskCompletePayload(val completed: Boolean)

@Serializable
data class TaskOccurrencePayload(
    val occurrenceKey: String,
    val status: String,
    val completedStepIds: List<String> = emptyList(),
)

@Serializable
data class TaskLifecyclePayload(val mode: String)

@Serializable
data class TaskSchedulePayload(val schedule: ScheduleSpec)

@Serializable
data class StepOpPayload(
    val taskId: String,
    val stepId: String? = null,
    val title: String = "",
    val completed: Boolean = false,
)

@Serializable
data class SeriesCreatePayload(
    val kind: String,
    val termId: String? = null,
    val name: String,
    val teacher: String = "",
    val description: String = "",
    val location: String = "",
    val busy: Boolean = true,
    val timeZone: String? = null,
    val startDate: String? = null,
    val startsAt: String? = null,
    val endsAt: String? = null,
    val endDayOffset: Int = 0,
    val startPeriodNumber: Int? = null,
    val endPeriodNumber: Int? = null,
    val recurrence: RecurrenceBody = RecurrenceBody(),
)

@Serializable
data class SeriesUpdatePayload(
    val kind: String,
    val termId: String? = null,
    val name: String,
    val teacher: String = "",
    val description: String = "",
    val location: String = "",
    val busy: Boolean = true,
    val timeZone: String? = null,
    val startDate: String? = null,
    val startsAt: String? = null,
    val endsAt: String? = null,
    val endDayOffset: Int = 0,
    val startPeriodNumber: Int? = null,
    val endPeriodNumber: Int? = null,
    val recurrence: RecurrenceBody = RecurrenceBody(),
)

@Serializable
data class SeriesDeletePayload(val mode: String = "trash")

@Serializable
data class TermCreatePayload(
    val name: String,
    val startDate: String,
    val endDate: String,
    val firstWeekMonday: String,
    val timeZone: String? = null,
)

@Serializable
data class SettingsUpdatePayload(val timeZone: String)

@Serializable
data class ProjectCreatePayload(
    val name: String,
    val kind: String = "project",
    val description: String = "",
)

object Intent {
    const val TASK_CREATE = "task.create"
    const val TASK_UPDATE = "task.update"
    const val TASK_COMPLETE = "task.complete"
    const val TASK_OCCURRENCE = "task.occurrence"
    const val TASK_LIFECYCLE = "task.lifecycle"
    const val TASK_SCHEDULE = "task.schedule"
    const val TASK_STEP_CREATE = "task.step.create"
    const val TASK_STEP_UPDATE = "task.step.update"
    const val TASK_STEP_DELETE = "task.step.delete"
    const val SERIES_CREATE = "series.create"
    const val SERIES_UPDATE = "series.update"
    const val SERIES_DELETE = "series.delete"
    const val TERM_CREATE = "term.create"
    const val SETTINGS_UPDATE = "settings.update"
    const val PROJECT_CREATE = "project.create"

    /** Entity type stored in the outbox, aligned with the server's change feed. */
    const val ENTITY_TASK = "task"
    const val ENTITY_SERIES = "calendar_series"
    const val ENTITY_TERM = "term"
    const val ENTITY_SETTINGS = "time_settings"
    const val ENTITY_PROJECT = "project"
}

object SyncMeta {
    const val CURSOR = "cursor"
    const val LAST_SUCCESS_AT = "last_success_at"
    const val LAST_ATTEMPT_AT = "last_attempt_at"
    const val LAST_ERROR = "last_error"
    const val RESET_REASON = "reset_reason"
    const val DEVICE_ID = "device_id"
}
