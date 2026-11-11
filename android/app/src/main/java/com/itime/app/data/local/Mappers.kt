package com.itime.app.data.local

import com.itime.app.data.remote.AdditionDto
import com.itime.app.data.remote.ExceptionDto
import com.itime.app.data.remote.OccurrenceStateDto
import com.itime.app.data.remote.ProjectDto
import com.itime.app.data.remote.SeriesDto
import com.itime.app.data.remote.StepDto
import com.itime.app.data.remote.TaskDto
import com.itime.app.data.remote.TermDto
import com.itime.app.data.remote.TimeSettingsDto

fun TaskDto.toEntity(): TaskEntity {
    val schedule = this.schedule
    return TaskEntity(
        id = id,
        title = title,
        description = description,
        projectId = project?.id,
        projectName = project?.name,
        projectKind = project?.kind,
        estimatedMinutes = estimated_minutes,
        estimateSource = estimate_source,
        priority = priority,
        status = status,
        completedAt = completed_at,
        lifecycle = lifecycle,
        archivedAt = archived_at,
        deletedAt = deleted_at,
        purgeAfter = purge_after,
        version = version,
        labelsCsv = labels.joinToString(",") { it.name },
        createdAt = created_at,
        updatedAt = updated_at,
        dueKind = schedule.due_kind,
        dueDate = schedule.due_date,
        dueTime = schedule.due_time,
        dueAt = schedule.due_at,
        scheduleTz = schedule.time_zone,
        scheduleConfigured = schedule.configured,
        lastOccurrenceDate = schedule.last_occurrence_date,
        recFrequency = schedule.recurrence.frequency,
        recInterval = schedule.recurrence.interval,
        recWeekdaysCsv = schedule.recurrence.weekdays.joinToString(","),
        recMonth = schedule.recurrence.month,
        recDayOfMonth = schedule.recurrence.day_of_month,
        recUntil = schedule.recurrence.until,
        recCount = schedule.recurrence.count,
        splittable = schedule.splittable,
        minimumBlockMinutes = schedule.minimum_block_minutes,
        pendingSync = false,
    )
}

fun StepDto.toEntity(taskId: String): StepEntity = StepEntity(
    id = id,
    taskId = taskId,
    title = title,
    position = position,
    completed = completed,
    completedAt = completed_at,
)

fun ProjectDto.toEntity(): ProjectEntity = ProjectEntity(
    id = id,
    kind = kind,
    name = name,
    description = description,
    version = version,
    updatedAt = updated_at,
)

fun TermDto.toEntity(): TermEntity = TermEntity(
    id = id,
    name = name,
    startDate = start_date,
    endDate = end_date,
    firstWeekMonday = first_week_monday,
    timeZone = time_zone,
    version = version,
    updatedAt = updated_at,
)

fun TermDto.toPeriods(): List<TermPeriodEntity> = periods.map {
    TermPeriodEntity(
        termId = id,
        periodNumber = it.period_number,
        name = it.name,
        startsAt = it.starts_at,
        endsAt = it.ends_at,
    )
}

fun SeriesDto.toEntity(): SeriesEntity = SeriesEntity(
    id = id,
    kind = kind,
    termId = term_id,
    name = name,
    teacher = teacher,
    description = description,
    location = location,
    busy = busy,
    sourceType = source_type,
    timeZone = time_zone,
    startDate = start_date ?: "1970-01-01",
    startsAt = starts_at ?: "00:00:00",
    endsAt = ends_at ?: "00:00:00",
    endDayOffset = end_day_offset,
    startPeriodNumber = start_period_number,
    endPeriodNumber = end_period_number,
    recFrequency = recurrence.frequency,
    recInterval = recurrence.interval,
    recWeekdaysCsv = recurrence.weekdays.joinToString(","),
    recMonth = recurrence.month,
    recDayOfMonth = recurrence.day_of_month,
    recUntil = recurrence.until,
    recCount = recurrence.count,
    recWeekNumbersCsv = recurrence.week_numbers.joinToString(","),
    lifecycle = lifecycle,
    version = version,
    updatedAt = updated_at,
    pendingSync = false,
)

fun ExceptionDto.toEntity(): ExceptionEntity = ExceptionEntity(
    id = id,
    seriesId = series_id,
    occurrenceKey = occurrence_key,
    originalStartDate = original_start_date,
    action = action,
    movedStartDate = moved_start_date,
    movedStartsAt = moved_starts_at,
    movedEndsAt = moved_ends_at,
    movedEndDayOffset = moved_end_day_offset,
    movedTimeZone = moved_time_zone,
    overrideName = override_name,
    overrideLocation = override_location,
    overrideDescription = override_description,
    overrideBusy = override_busy,
    version = version,
)

fun AdditionDto.toEntity(): AdditionEntity = AdditionEntity(
    id = id,
    seriesId = series_id,
    occurrenceKey = occurrence_key,
    localStartDate = local_start_date,
    startsAt = starts_at,
    endsAt = ends_at,
    endDayOffset = end_day_offset,
    timeZone = time_zone,
    status = status,
    overrideName = override_name,
    overrideLocation = override_location,
    overrideBusy = override_busy,
    version = version,
)

fun OccurrenceStateDto.toEntity(taskId: String): OccurrenceEntity = OccurrenceEntity(
    id = id,
    taskId = taskId,
    occurrenceKey = occurrence_key,
    originalDate = original_date,
    kind = kind,
    effectiveDate = effective_date,
    effectiveTime = effective_time,
    effectiveAt = effective_at,
    effectiveTimeZone = effective_time_zone,
    rescheduled = rescheduled,
    status = status,
    completedAt = completed_at,
    completedStepIdsCsv = completed_step_ids.joinToString(","),
    makeupForKey = makeup_for_key,
    makeupForDate = makeup_for_date,
    version = version,
)

fun TimeSettingsDto.toEntity(): TimeSettingsEntity = TimeSettingsEntity(
    initialized = initialized,
    timeZone = time_zone,
    weatherLocation = weather_location,
    weekStartsOn = week_starts_on,
    activityDayStartMinute = activity_day_start_minute,
    version = version,
)
