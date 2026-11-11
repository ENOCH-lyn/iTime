package com.itime.app.data.remote

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

@Serializable
data class ErrorDto(
    val code: String = "unknown",
    val message: String = "请求失败，请稍后重试。",
    val request_id: String? = null,
    val saved: String? = null,
    val retryable: Boolean = false,
    val details: JsonElement? = null,
)

@Serializable
data class ErrorEnvelope(val error: ErrorDto = ErrorDto())

@Serializable
data class UserDto(
    val id: String,
    val email: String,
    val email_verified: Boolean = false,
    val ai_access: String = "allowed",
    val is_admin: Boolean = false,
    val created_at: String? = null,
)

@Serializable
data class SessionDto(
    val user: UserDto,
    val session_id: String,
    val expires_at: String? = null,
    val csrf_token: String,
)

@Serializable
data class MeDto(
    val user: UserDto,
    val session_id: String,
    val expires_at: String? = null,
    val csrf_token: String,
)

@Serializable
data class PolicyDto(
    val password_min_length: Int = 8,
    val password_max_length: Int = 128,
    val session_hours: Int = 24,
    val email_available: Boolean = false,
    val registration_mode: String = "open",
    val require_email_verification: Boolean = false,
)

@Serializable
data class LoginRequest(
    val email: String,
    val password: String,
    val device_name: String = "iTime Android",
)

@Serializable
data class RegisterRequest(
    val email: String,
    val password: String,
    val device_name: String = "iTime Android",
    val time_zone: String? = null,
)

@Serializable
data class ProjectRefDto(
    val id: String,
    val kind: String = "project",
    val name: String = "",
)

@Serializable
data class LabelDto(
    val name: String,
    val color: String? = null,
)

@Serializable
data class StepProgressDto(
    val total: Int = 0,
    val completed: Int = 0,
)

@Serializable
data class RecurrenceDto(
    val frequency: String = "none",
    val interval: Int = 1,
    val weekdays: List<Int> = emptyList(),
    val month: Int? = null,
    val day_of_month: Int? = null,
    val until: String? = null,
    val count: Int? = null,
    val week_numbers: List<Int> = emptyList(),
)

@Serializable
data class ScheduleDto(
    val configured: Boolean = false,
    val preferences_saved: Boolean = false,
    val predecessor_task_id: String? = null,
    val due_kind: String = "none",
    val due_date: String? = null,
    val last_occurrence_date: String? = null,
    val due_time: String? = null,
    val due_at: String? = null,
    val time_zone: String? = null,
    val ambiguous_utc_offset_minutes: Int? = null,
    val splittable: Boolean = true,
    val minimum_block_minutes: Int = 15,
    val recurrence: RecurrenceDto = RecurrenceDto(),
)

@Serializable
data class StepDto(
    val id: String,
    val title: String,
    val position: Int = 0,
    val completed: Boolean = false,
    val completed_at: String? = null,
    val created_at: String? = null,
    val updated_at: String? = null,
)

@Serializable
data class TaskDto(
    val id: String,
    val title: String,
    val description: String = "",
    val project: ProjectRefDto? = null,
    val estimated_minutes: Int? = null,
    val estimate_source: String? = null,
    val priority: Int = 0,
    val status: String = "open",
    val completed_at: String? = null,
    val lifecycle: String = "active",
    val archived_at: String? = null,
    val deleted_at: String? = null,
    val purge_after: String? = null,
    val purged_at: String? = null,
    val trash_remaining_days: Int? = null,
    val version: Int = 1,
    val labels: List<LabelDto> = emptyList(),
    val step_progress: StepProgressDto = StepProgressDto(),
    val link_count: Int = 0,
    val schedule: ScheduleDto = ScheduleDto(),
    val created_at: String? = null,
    val updated_at: String? = null,
    val steps: List<StepDto> = emptyList(),
)

@Serializable
data class TaskListDto(
    val items: List<TaskDto> = emptyList(),
    val next_before_time: String? = null,
    val next_before_id: String? = null,
)

@Serializable
data class TaskEnvelope(val task: TaskDto)

@Serializable
data class ProjectEnvelope(val project: ProjectDto)

@Serializable
data class TimeSettingsEnvelope(val settings: TimeSettingsDto)

@Serializable
data class ProjectDto(
    val id: String,
    val kind: String = "project",
    val name: String,
    val description: String = "",
    val version: Int = 1,
    val task_count: Int? = null,
    val open_task_count: Int? = null,
    val created_at: String? = null,
    val updated_at: String? = null,
)

@Serializable
data class ProjectListDto(val items: List<ProjectDto> = emptyList())

@Serializable
data class TermPeriodDto(
    val period_number: Int,
    val name: String,
    val starts_at: String,
    val ends_at: String,
)

@Serializable
data class TermDto(
    val id: String,
    val name: String,
    val start_date: String,
    val end_date: String,
    val first_week_monday: String,
    val time_zone: String = "Asia/Shanghai",
    val version: Int = 1,
    val created_at: String? = null,
    val updated_at: String? = null,
    val periods: List<TermPeriodDto> = emptyList(),
    val period_count: Int = 0,
)

@Serializable
data class TermListDto(val items: List<TermDto> = emptyList())

@Serializable
data class TermEnvelope(val term: TermDto)

@Serializable
data class SeriesDto(
    val id: String,
    val kind: String = "fixed",
    val term_id: String? = null,
    val predecessor_id: String? = null,
    val source_id: String? = null,
    val name: String,
    val teacher: String = "",
    val description: String = "",
    val location: String = "",
    val course_group_id: String? = null,
    val busy: Boolean = true,
    val source_type: String = "manual",
    val time_zone: String = "Asia/Shanghai",
    val start_date: String? = null,
    val starts_at: String? = null,
    val ends_at: String? = null,
    val end_day_offset: Int = 0,
    val start_period_number: Int? = null,
    val end_period_number: Int? = null,
    val ambiguous_utc_offset_minutes: Int? = null,
    val recurrence: RecurrenceDto = RecurrenceDto(),
    val lifecycle: String = "active",
    val deleted_at: String? = null,
    val purge_after: String? = null,
    val purged_at: String? = null,
    val trash_remaining_days: Int? = null,
    val version: Int = 1,
    val created_at: String? = null,
    val updated_at: String? = null,
)

@Serializable
data class SeriesListDto(val items: List<SeriesDto> = emptyList())

@Serializable
data class SeriesEnvelope(val series: SeriesDto)

@Serializable
data class ExceptionDto(
    val id: String,
    val series_id: String,
    val occurrence_key: String,
    val original_start_date: String,
    val action: String = "cancelled",
    val moved_start_date: String? = null,
    val moved_starts_at: String? = null,
    val moved_ends_at: String? = null,
    val moved_end_day_offset: Int? = null,
    val moved_time_zone: String? = null,
    val moved_ambiguous_utc_offset_minutes: Int? = null,
    val override_name: String? = null,
    val override_description: String? = null,
    val override_location: String? = null,
    val override_busy: Boolean? = null,
    val version: Int = 1,
    val created_at: String? = null,
    val updated_at: String? = null,
)

@Serializable
data class AdditionDto(
    val id: String,
    val series_id: String,
    val occurrence_key: String,
    val source_occurrence_key: String? = null,
    val source_original_start_date: String? = null,
    val status: String = "confirmed",
    val local_start_date: String,
    val starts_at: String? = null,
    val ends_at: String? = null,
    val end_day_offset: Int = 0,
    val time_zone: String? = null,
    val ambiguous_utc_offset_minutes: Int? = null,
    val override_name: String? = null,
    val override_description: String? = null,
    val override_location: String? = null,
    val override_busy: Boolean? = null,
    val version: Int = 1,
    val created_at: String? = null,
    val updated_at: String? = null,
)

@Serializable
data class TaskOccurrenceListDto(val items: List<OccurrenceStateDto> = emptyList())

@Serializable
data class OccurrenceStateDto(
    val id: String,
    val occurrence_key: String,
    val original_date: String,
    val kind: String = "base",
    val effective_date: String? = null,
    val effective_time: String? = null,
    val effective_at: String? = null,
    val effective_time_zone: String? = null,
    val ambiguous_utc_offset_minutes: Int? = null,
    val rescheduled: Boolean = false,
    val status: String = "open",
    val completed_at: String? = null,
    val completed_step_ids: List<String> = emptyList(),
    val makeup_for_key: String? = null,
    val makeup_for_date: String? = null,
    val version: Int = 1,
    val created_at: String? = null,
    val updated_at: String? = null,
    val task_id: String? = null,
)

@Serializable
data class TimeSettingsDto(
    val initialized: Boolean = false,
    val time_zone: String? = null,
    val weather_location: String? = null,
    val week_starts_on: Int = 1,
    val activity_day_start_minute: Int = 240,
    val version: Int = 0,
    val created_at: String? = null,
    val updated_at: String? = null,
)

@Serializable
data class SyncEventDto(
    val sequence: Long,
    val entity_type: String,
    val entity_id: String,
    val action: String,
    val entity_version: Int,
    val changed_at: String? = null,
)

@Serializable
data class SyncPageDto(
    val events: List<SyncEventDto> = emptyList(),
    val next_cursor: String,
    val has_more: Boolean = false,
    val reset_required: Boolean = false,
    val reset_reason: String? = null,
    val server_time: String? = null,
)

@Serializable
data class SyncSnapshotDto(
    val cursor: String,
    val server_time: String? = null,
    val time_settings: TimeSettingsDto = TimeSettingsDto(),
    val projects: List<ProjectDto> = emptyList(),
    val tasks: List<TaskDto> = emptyList(),
    val terms: List<TermDto> = emptyList(),
    val series: List<SeriesDto> = emptyList(),
    val calendar_exceptions: List<ExceptionDto> = emptyList(),
    val calendar_additions: List<AdditionDto> = emptyList(),
    val task_occurrences: List<OccurrenceStateDto> = emptyList(),
)

@Serializable
data class SyncEntityDto(
    val entity_type: String,
    val entity_id: String,
    val deleted: Boolean = false,
    val series_id: String? = null,
    val task_id: String? = null,
    val data: JsonElement? = null,
)

@Serializable
data class OperationAckDto(
    @SerialName("operation_id") val operationId: String? = null,
    @SerialName("merge") val merge: String? = null,
)
