package com.itime.app.data.remote

import kotlinx.serialization.Serializable

@Serializable
data class StepDraftBody(val title: String)

@Serializable
data class RecurrenceBody(
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
data class TaskCreateBody(
    val operation_id: String,
    val title: String,
    val description: String = "",
    val project_id: String? = null,
    val estimated_minutes: Int? = null,
    val priority: Int = 0,
    val labels: List<String> = emptyList(),
    val steps: List<StepDraftBody> = emptyList(),
)

@Serializable
data class TaskUpdateBody(
    val operation_id: String,
    val expected_version: Int,
    val title: String,
    val description: String = "",
    val project_id: String? = null,
    val estimated_minutes: Int? = null,
    val priority: Int = 0,
    val labels: List<String> = emptyList(),
)

@Serializable
data class TaskCompletionBody(
    val operation_id: String,
    val expected_version: Int,
    val completed: Boolean,
)

@Serializable
data class TaskLifecycleBody(
    val operation_id: String,
    val expected_version: Int,
)

@Serializable
data class TaskArchiveBody(
    val operation_id: String,
    val expected_version: Int,
    val archived: Boolean,
)

@Serializable
data class ScheduleUpdateBody(
    val operation_id: String,
    val expected_task_version: Int,
    val due_kind: String,
    val due_date: String? = null,
    val due_time: String? = null,
    val time_zone: String? = null,
    val splittable: Boolean = true,
    val minimum_block_minutes: Int = 15,
    val recurrence: RecurrenceBody = RecurrenceBody(),
)

@Serializable
data class SeriesCreateBody(
    val operation_id: String,
    val kind: String,
    val term_id: String? = null,
    val name: String,
    val teacher: String = "",
    val description: String = "",
    val location: String = "",
    val busy: Boolean = true,
    val time_zone: String? = null,
    val start_date: String? = null,
    val starts_at: String? = null,
    val ends_at: String? = null,
    val end_day_offset: Int = 0,
    val start_period_number: Int? = null,
    val end_period_number: Int? = null,
    val recurrence: RecurrenceBody = RecurrenceBody(),
)

@Serializable
data class SeriesUpdateBody(
    val operation_id: String,
    val expected_version: Int,
    val kind: String,
    val term_id: String? = null,
    val name: String,
    val teacher: String = "",
    val description: String = "",
    val location: String = "",
    val busy: Boolean = true,
    val time_zone: String? = null,
    val start_date: String? = null,
    val starts_at: String? = null,
    val ends_at: String? = null,
    val end_day_offset: Int = 0,
    val start_period_number: Int? = null,
    val end_period_number: Int? = null,
    val recurrence: RecurrenceBody = RecurrenceBody(),
)

@Serializable
data class SeriesDeletePayloadBody(
    val operation_id: String,
    val expected_version: Int,
)

@Serializable
data class TermCreateBody(    val operation_id: String,
    val name: String,
    val start_date: String,
    val end_date: String,
    val first_week_monday: String,
    val time_zone: String? = null,
)

@Serializable
data class TimeSettingsUpdateBody(
    val operation_id: String,
    val expected_version: Int,
    val time_zone: String,
    val weather_location: String? = null,
    val week_starts_on: Int = 1,
    val activity_day_start_minute: Int = 240,
)

@Serializable
data class ProjectCreateBody(
    val operation_id: String,
    val kind: String = "project",
    val name: String,
    val description: String = "",
)

@Serializable
data class StepCreateBody(
    val operation_id: String,
    val expected_task_version: Int,
    val title: String,
)

@Serializable
data class StepUpdateBody(
    val operation_id: String,
    val expected_task_version: Int,
    val title: String,
    val completed: Boolean,
)

@Serializable
data class StepDeleteBody(
    val operation_id: String,
    val expected_task_version: Int,
)

@Serializable
data class OccurrenceStatusBody(
    val operation_id: String,
    val expected_task_version: Int,
    val expected_occurrence_version: Int,
    val occurrence_key: String,
    val status: String,
    val completed_step_ids: List<String> = emptyList(),
)
