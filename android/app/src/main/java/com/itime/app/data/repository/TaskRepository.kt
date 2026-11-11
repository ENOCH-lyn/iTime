package com.itime.app.data.repository

import androidx.room.withTransaction
import com.itime.app.data.local.ItimeDatabase
import com.itime.app.data.local.OutboxEntity
import com.itime.app.data.local.StepEntity
import com.itime.app.data.local.TaskEntity
import com.itime.app.data.local.TaskWithSteps
import com.itime.app.data.remote.RecurrenceBody
import com.itime.app.data.sync.Intent
import com.itime.app.data.sync.ScheduleSpec
import com.itime.app.data.sync.SyncEngine
import com.itime.app.data.sync.TaskCompletePayload
import com.itime.app.data.sync.TaskCreatePayload
import com.itime.app.data.sync.TaskLifecyclePayload
import com.itime.app.data.sync.TaskOccurrencePayload
import com.itime.app.data.sync.TaskSchedulePayload
import com.itime.app.data.sync.TaskUpdatePayload
import com.itime.app.domain.RecurrenceSpec
import com.itime.app.domain.TaskDraft
import kotlinx.coroutines.flow.Flow
import kotlinx.serialization.json.Json
import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.util.UUID

/**
 * Local-first task repository: every mutation is written to Room immediately
 * and queued in the outbox; the sync engine later replays it against the
 * server.  Reads always come from Room, so the UI works offline unchanged.
 */
class TaskRepository(
    private val db: ItimeDatabase,
    private val json: Json,
    private val sync: SyncEngine,
) {
    fun observeActive(): Flow<List<TaskEntity>> = db.taskDao().observeActive()
    fun observeArchived(): Flow<List<TaskEntity>> = db.taskDao().observeArchived()
    fun observeTrashed(): Flow<List<TaskEntity>> = db.taskDao().observeTrashed()
    fun observe(id: String): Flow<TaskEntity?> = db.taskDao().observe(id)
    fun observeWithSteps(id: String): Flow<TaskWithSteps?> = db.taskDao().observeWithSteps(id)
    fun observeOccurrences(taskId: String) = db.occurrenceDao().observeForTask(taskId)

    suspend fun get(id: String): TaskEntity? = db.taskDao().find(id)

    suspend fun create(draft: TaskDraft): String {
        val id = UUID.randomUUID().toString()
        val operationId = UUID.randomUUID().toString()
        val payload = TaskCreatePayload(
            title = draft.title.trim(),
            description = draft.description.trim(),
            projectId = draft.projectId,
            estimatedMinutes = draft.estimatedMinutes,
            priority = draft.priority,
            labels = draft.labels,
            steps = draft.steps.map { it.trim() }.filter { it.isNotEmpty() },
            schedule = draft.toScheduleSpec(),
        )
        db.withTransaction {
            db.taskDao().upsert(draft.toEntity(id))
            db.stepDao().upsertAll(
                payload.steps.mapIndexed { index, title ->
                    StepEntity(
                        id = UUID.randomUUID().toString(),
                        taskId = id,
                        title = title,
                        position = index,
                    )
                },
            )
            db.outboxDao().upsert(
                OutboxEntity(
                    operationId = operationId,
                    entityType = Intent.ENTITY_TASK,
                    entityId = id,
                    intent = Intent.TASK_CREATE,
                    payloadJson = json.encodeToString(TaskCreatePayload.serializer(), payload),
                    baseVersion = 0,
                    createdAt = 0L,
                ),
            )
        }
        sync.requestSync("local-create-task")
        return id
    }

    suspend fun update(id: String, draft: TaskDraft) {
        val existing = db.taskDao().find(id) ?: return
        val payload = TaskUpdatePayload(
            title = draft.title.trim(),
            description = draft.description.trim(),
            projectId = draft.projectId,
            estimatedMinutes = draft.estimatedMinutes,
            priority = draft.priority,
            labels = draft.labels,
        )
        db.withTransaction {
            db.taskDao().upsert(
                existing.copy(
                    title = payload.title,
                    description = payload.description,
                    projectId = payload.projectId,
                    estimatedMinutes = payload.estimatedMinutes,
                    priority = payload.priority,
                    labelsCsv = payload.labels.joinToString(","),
                    pendingSync = true,
                ),
            )
            enqueue(
                entityId = id,
                intent = Intent.TASK_UPDATE,
                payload = json.encodeToString(TaskUpdatePayload.serializer(), payload),
                baseVersion = existing.version,
            )
        }
        sync.requestSync("local-update-task")
    }

    suspend fun setCompleted(id: String, completed: Boolean) {
        val existing = db.taskDao().find(id) ?: return
        db.withTransaction {
            db.taskDao().upsert(
                existing.copy(
                    status = if (completed) "completed" else "open",
                    completedAt = if (completed) Instant.now().toString() else null,
                    pendingSync = true,
                ),
            )
            enqueue(
                entityId = id,
                intent = Intent.TASK_COMPLETE,
                payload = json.encodeToString(
                    TaskCompletePayload.serializer(),
                    TaskCompletePayload(completed),
                ),
                baseVersion = existing.version,
            )
        }
        sync.requestSync("local-complete-task")
    }

    suspend fun setOccurrenceStatus(
        taskId: String,
        occurrenceKey: String,
        status: String,
    ) {
        val task = db.taskDao().find(taskId) ?: return
        val occurrence = db.occurrenceDao().find(taskId, occurrenceKey)
        db.withTransaction {
            db.occurrenceDao().upsert(
                (occurrence ?: com.itime.app.data.local.OccurrenceEntity(
                    id = UUID.randomUUID().toString(),
                    taskId = taskId,
                    occurrenceKey = occurrenceKey,
                    originalDate = occurrenceKey.substringBefore('T'),
                )).copy(
                    status = status,
                    completedAt = if (status == "completed") Instant.now().toString() else null,
                ),
            )
            enqueue(
                entityId = taskId,
                intent = Intent.TASK_OCCURRENCE,
                payload = json.encodeToString(
                    TaskOccurrencePayload.serializer(),
                    TaskOccurrencePayload(occurrenceKey, status),
                ),
                baseVersion = task.version,
            )
        }
        sync.requestSync("local-occurrence")
    }

    suspend fun updateSchedule(id: String, draft: TaskDraft) {
        val existing = db.taskDao().find(id) ?: return
        val spec = draft.toScheduleSpec()
        if (spec == null) return
        db.withTransaction {
            db.taskDao().upsert(
                existing.copy(
                    dueKind = spec.dueKind,
                    dueDate = spec.dueDate,
                    dueTime = spec.dueTime,
                    scheduleTz = spec.timeZone,
                    scheduleConfigured = spec.dueKind != "none",
                    recFrequency = spec.recurrence.frequency,
                    recInterval = spec.recurrence.interval,
                    recWeekdaysCsv = spec.recurrence.weekdays.joinToString(","),
                    recUntil = spec.recurrence.until,
                    recCount = spec.recurrence.count,
                    pendingSync = true,
                ),
            )
            enqueue(
                entityId = id,
                intent = Intent.TASK_SCHEDULE,
                payload = json.encodeToString(TaskSchedulePayload.serializer(), TaskSchedulePayload(spec)),
                baseVersion = existing.version,
            )
        }
        sync.requestSync("local-schedule")
    }

    suspend fun trash(id: String) = lifecycle(id, "trash")
    suspend fun restore(id: String) = lifecycle(id, "restore")
    suspend fun archive(id: String, archived: Boolean) = lifecycle(id, if (archived) "archive" else "unarchive")

    private suspend fun lifecycle(id: String, mode: String) {
        val existing = db.taskDao().find(id) ?: return
        val now = Instant.now().toString()
        val patched = when (mode) {
            "trash" -> existing.copy(lifecycle = "trashed", deletedAt = now, pendingSync = true)
            "restore" -> existing.copy(lifecycle = "active", deletedAt = null, pendingSync = true)
            "archive" -> existing.copy(lifecycle = "archived", archivedAt = now, pendingSync = true)
            else -> existing.copy(lifecycle = "active", archivedAt = null, pendingSync = true)
        }
        db.withTransaction {
            db.taskDao().upsert(patched)
            enqueue(
                entityId = id,
                intent = Intent.TASK_LIFECYCLE,
                payload = json.encodeToString(
                    TaskLifecyclePayload.serializer(),
                    TaskLifecyclePayload(mode),
                ),
                baseVersion = existing.version,
            )
        }
        sync.requestSync("local-lifecycle")
    }

    private suspend fun enqueue(
        entityId: String,
        intent: String,
        payload: String,
        baseVersion: Int,
    ) {
        db.outboxDao().upsert(
            OutboxEntity(
                operationId = UUID.randomUUID().toString(),
                entityType = Intent.ENTITY_TASK,
                entityId = entityId,
                intent = intent,
                payloadJson = payload,
                baseVersion = baseVersion,
                createdAt = System.currentTimeMillis(),
            ),
        )
    }
}

fun RecurrenceSpec.toBody(): RecurrenceBody = RecurrenceBody(
    frequency = frequency,
    interval = interval.coerceAtLeast(1),
    weekdays = weekdays,
    until = until?.toString(),
    count = count,
)

fun TaskDraft.toScheduleSpec(): ScheduleSpec? {
    if (dueDate == null) return null
    return ScheduleSpec(
        dueKind = if (dueTime != null) "datetime" else "date",
        dueDate = dueDate.toString(),
        dueTime = dueTime?.toString(),
        recurrence = recurrence.toBody(),
    )
}

fun TaskDraft.toEntity(id: String): TaskEntity {
    val spec = toScheduleSpec()
    return TaskEntity(
        id = id,
        title = title.trim(),
        description = description.trim(),
        projectId = projectId,
        estimatedMinutes = estimatedMinutes,
        priority = priority,
        labelsCsv = labels.joinToString(","),
        status = "open",
        lifecycle = "active",
        version = 0,
        dueKind = spec?.dueKind ?: "none",
        dueDate = spec?.dueDate,
        dueTime = spec?.dueTime,
        scheduleTz = spec?.timeZone,
        scheduleConfigured = spec != null,
        recFrequency = spec?.recurrence?.frequency ?: "none",
        recInterval = spec?.recurrence?.interval ?: 1,
        recWeekdaysCsv = spec?.recurrence?.weekdays?.joinToString(",") ?: "",
        recUntil = spec?.recurrence?.until,
        recCount = spec?.recurrence?.count,
        pendingSync = true,
    )
}

fun TaskEntity.toDraft(): TaskDraft = TaskDraft(
    title = title,
    description = description,
    projectId = projectId,
    estimatedMinutes = estimatedMinutes,
    priority = priority,
    labels = labels,
    steps = emptyList(),
    dueDate = com.itime.app.core.Times.parseDate(dueDate),
    dueTime = com.itime.app.core.Times.parseTime(dueTime),
    recurrence = RecurrenceSpec(
        frequency = recFrequency,
        interval = recInterval,
        weekdays = recWeekdaysCsv.split(',').mapNotNull { it.trim().toIntOrNull() },
        until = com.itime.app.core.Times.parseDate(recUntil),
        count = recCount,
    ),
)

fun TaskDraft.dueLabel(): String? {
    val date: LocalDate = dueDate ?: return null
    val time: LocalTime? = dueTime
    return if (time == null) date.toString() else "$date $time"
}
