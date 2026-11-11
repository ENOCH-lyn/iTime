package com.itime.app.data.sync

import androidx.room.withTransaction
import com.itime.app.data.SyncPhase
import com.itime.app.data.SyncStatusHolder
import com.itime.app.data.local.AdditionEntity
import com.itime.app.data.local.ExceptionEntity
import com.itime.app.data.local.ItimeDatabase
import com.itime.app.data.local.OccurrenceEntity
import com.itime.app.data.local.OutboxEntity
import com.itime.app.data.local.ProjectEntity
import com.itime.app.data.local.SeriesEntity
import com.itime.app.data.local.StepEntity
import com.itime.app.data.local.SyncMetaEntity
import com.itime.app.data.local.TaskEntity
import com.itime.app.data.local.TermEntity
import com.itime.app.data.local.TermPeriodEntity
import com.itime.app.data.local.TimeSettingsEntity
import com.itime.app.data.local.toEntity
import com.itime.app.data.local.toPeriods
import com.itime.app.data.remote.AdditionDto
import com.itime.app.data.remote.ApiException
import com.itime.app.data.remote.ExceptionDto
import com.itime.app.data.remote.ItimeApi
import com.itime.app.data.remote.OccurrenceStateDto
import com.itime.app.data.remote.ProjectDto
import com.itime.app.data.remote.RecurrenceBody
import com.itime.app.data.remote.ScheduleUpdateBody
import com.itime.app.data.remote.SeriesCreateBody
import com.itime.app.data.remote.SeriesDeletePayloadBody
import com.itime.app.data.remote.SeriesDto
import com.itime.app.data.remote.SeriesUpdateBody
import com.itime.app.data.remote.StepCreateBody
import com.itime.app.data.remote.StepDeleteBody
import com.itime.app.data.remote.StepReorderBody
import com.itime.app.data.remote.StepUpdateBody
import com.itime.app.data.remote.SyncEntityDto
import com.itime.app.data.remote.SyncEventDto
import com.itime.app.data.remote.TaskArchiveBody
import com.itime.app.data.remote.TaskCompletionBody
import com.itime.app.data.remote.TaskCreateBody
import com.itime.app.data.remote.TaskDto
import com.itime.app.data.remote.TaskEnvelope
import com.itime.app.data.remote.TaskLifecycleBody
import com.itime.app.data.remote.TaskUpdateBody
import com.itime.app.data.remote.TermCreateBody
import com.itime.app.data.remote.TermDto
import com.itime.app.data.remote.TimeSettingsDto
import com.itime.app.data.remote.TimeSettingsUpdateBody
import com.itime.app.data.session.SessionStore
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.time.Instant
import java.util.UUID

/**
 * The single coordination point between the local Room database and the
 * iTime server.
 *
 * Push: FIFO outbox replay.  The engine injects the last observed server
 * version into each request, so an edit only conflicts when another device
 * really changed the same entity first.
 *
 * Pull: change-feed pages at [SyncMeta.CURSOR]; the first run (or a server
 * reset) bootstraps from the snapshot endpoint.
 */
class SyncEngine(
    private val db: ItimeDatabase,
    private val api: ItimeApi,
    private val json: Json,
    private val sessionStore: SessionStore,
    private val status: SyncStatusHolder,
    private val scope: CoroutineScope,
) {
    private val mutex = Mutex()
    private var scheduled: Job? = null

    private val _lastResetNotice = MutableStateFlow<String?>(null)
    val lastResetNotice get() = _lastResetNotice

    fun requestSync(reason: String) {
        if (sessionStore.current() == null) return
        if (scheduled?.isActive == true) return
        scheduled = scope.launch {
            runCatching { syncNow(reason) }
        }
    }

    /** One full sync cycle.  Safe to call concurrently: calls are serialised. */
    suspend fun syncNow(reason: String = "manual") {
        val session = sessionStore.current() ?: return
        mutex.withLock {
            status.update {
                it.copy(
                    phase = SyncPhase.Connecting,
                    lastError = null,
                )
            }
            try {
                refreshPendingCounts()
                pushAll()
                pull()
                val now = System.currentTimeMillis()
                db.settingsDao().upsertMeta(SyncMetaEntity(SyncMeta.LAST_SUCCESS_AT, now.toString()))
                refreshPendingCounts()
                status.update {
                    val conflicts = it.conflictCount
                    it.copy(
                        phase = when {
                            conflicts > 0 -> SyncPhase.Conflict
                            it.pendingCount > 0 -> SyncPhase.Pending
                            else -> SyncPhase.Synced
                        },
                        lastSuccessAt = now,
                    )
                }
            } catch (error: ApiException) {
                status.update {
                    it.copy(
                        phase = if (error.status == 0) SyncPhase.Offline else SyncPhase.Error,
                        lastError = error.message,
                    )
                }
            } catch (error: Exception) {
                status.update {
                    it.copy(phase = SyncPhase.Error, lastError = error.message ?: "同步失败")
                }
            }
        }
    }

    private suspend fun refreshPendingCounts() {
        val all = db.outboxDao().all()
        status.update {
            it.copy(
                pendingCount = all.count { op -> op.state == "pending" },
                conflictCount = all.count { op -> op.state == "conflict" },
            )
        }
    }

    // ------------------------------------------------------------- pushing

    private suspend fun pushAll() {
        var guard = 0
        while (guard++ < 200) {
            val op = db.outboxDao().nextPending() ?: return
            try {
                execute(op)
                db.outboxDao().delete(op.operationId)
                refreshPendingCounts()
            } catch (error: ApiException) {
                handlePushFailure(op, error)
                return
            }
        }
    }

    private suspend fun execute(op: OutboxEntity) {
        when (op.intent) {
            Intent.TASK_CREATE -> executeTaskCreate(op)
            Intent.TASK_UPDATE -> {
                val payload = json.decodeFromString(TaskUpdatePayload.serializer(), op.payloadJson)
                val task = api.updateTask(
                    op.entityId,
                    TaskUpdateBody(
                        operation_id = op.operationId,
                        expected_version = currentTaskVersion(op.entityId),
                        title = payload.title,
                        description = payload.description,
                        project_id = resolveProjectId(payload.projectId),
                        estimated_minutes = payload.estimatedMinutes,
                        priority = payload.priority,
                        labels = payload.labels,
                    ),
                )
                applyTask(task, force = true)
            }
            Intent.TASK_COMPLETE -> {
                val payload = json.decodeFromString(TaskCompletePayload.serializer(), op.payloadJson)
                val task = api.completeTask(
                    op.entityId,
                    TaskCompletionBody(
                        operation_id = op.operationId,
                        expected_version = currentTaskVersion(op.entityId),
                        completed = payload.completed,
                    ),
                )
                applyTask(task, force = true)
            }
            Intent.TASK_OCCURRENCE -> {
                val payload = json.decodeFromString(TaskOccurrencePayload.serializer(), op.payloadJson)
                val occurrence = db.occurrenceDao().find(op.entityId, payload.occurrenceKey)
                val task = api.setOccurrenceStatus(
                    op.entityId,
                    com.itime.app.data.remote.OccurrenceStatusBody(
                        operation_id = op.operationId,
                        expected_task_version = currentTaskVersion(op.entityId),
                        expected_occurrence_version = occurrence?.version ?: 0,
                        occurrence_key = payload.occurrenceKey,
                        status = payload.status,
                        completed_step_ids = payload.completedStepIds,
                    ),
                )
                applyTask(task, force = true)
                occurrence?.let {
                    if (payload.status == "completed") {
                        db.occurrenceDao().upsert(
                            it.copy(status = "completed", completedAt = Instant.now().toString()),
                        )
                    }
                }
            }
            Intent.TASK_LIFECYCLE -> {
                val payload = json.decodeFromString(TaskLifecyclePayload.serializer(), op.payloadJson)
                val version = currentTaskVersion(op.entityId)
                val task = when (payload.mode) {
                    "trash" -> api.deleteTask(
                        op.entityId,
                        TaskLifecycleBody(op.operationId, version),
                    )
                    "restore" -> api.restoreTask(
                        op.entityId,
                        TaskLifecycleBody(op.operationId, version),
                    )
                    "archive" -> api.archiveTask(
                        op.entityId,
                        TaskArchiveBody(op.operationId, version, archived = true),
                    )
                    else -> api.archiveTask(
                        op.entityId,
                        TaskArchiveBody(op.operationId, version, archived = false),
                    )
                }
                applyTask(task, force = true)
            }
            Intent.TASK_SCHEDULE -> {
                val payload = json.decodeFromString(TaskSchedulePayload.serializer(), op.payloadJson)
                val task = api.putSchedule(op.entityId, payload.schedule.toUpdateBody(op.operationId, currentTaskVersion(op.entityId)))
                applyTask(task, force = true)
            }
            Intent.TASK_STEP_CREATE -> {
                val payload = json.decodeFromString(StepOpPayload.serializer(), op.payloadJson)
                val task = api.createStep(
                    payload.taskId,
                    StepCreateBody(
                        operation_id = op.operationId,
                        expected_task_version = currentTaskVersion(payload.taskId),
                        title = payload.title,
                    ),
                )
                applyTask(task, force = true)
            }
            Intent.TASK_STEP_UPDATE -> {
                val payload = json.decodeFromString(StepOpPayload.serializer(), op.payloadJson)
                val task = api.updateStep(
                    payload.taskId,
                    payload.stepId ?: error("step id missing"),
                    StepUpdateBody(
                        operation_id = op.operationId,
                        expected_task_version = currentTaskVersion(payload.taskId),
                        title = payload.title,
                        completed = payload.completed,
                    ),
                )
                applyTask(task, force = true)
            }
            Intent.TASK_STEP_REORDER -> {
                val payload = json.decodeFromString(StepReorderPayload.serializer(), op.payloadJson)
                val task = api.reorderSteps(
                    payload.taskId,
                    StepReorderBody(
                        operation_id = op.operationId,
                        expected_task_version = currentTaskVersion(payload.taskId),
                        step_ids = payload.stepIds,
                    ),
                )
                applyTask(task, force = true)
            }
            Intent.TASK_STEP_DELETE -> {
                val payload = json.decodeFromString(StepOpPayload.serializer(), op.payloadJson)
                val task = api.deleteStep(
                    payload.taskId,
                    payload.stepId ?: error("step id missing"),
                    StepDeleteBody(
                        operation_id = op.operationId,
                        expected_task_version = currentTaskVersion(payload.taskId),
                    ),
                )
                applyTask(task, force = true)
            }
            Intent.SERIES_CREATE -> executeSeriesCreate(op)
            Intent.SERIES_UPDATE -> {
                val payload = json.decodeFromString(SeriesUpdatePayload.serializer(), op.payloadJson)
                val series = api.updateSeries(
                    op.entityId,
                    SeriesUpdateBody(
                        operation_id = op.operationId,
                        expected_version = currentSeriesVersion(op.entityId),
                        kind = payload.kind,
                        term_id = payload.termId,
                        name = payload.name,
                        teacher = payload.teacher,
                        description = payload.description,
                        location = payload.location,
                        busy = payload.busy,
                        time_zone = payload.timeZone ?: defaultZone(),
                        start_date = payload.startDate,
                        starts_at = payload.startsAt,
                        ends_at = payload.endsAt,
                        end_day_offset = payload.endDayOffset,
                        start_period_number = payload.startPeriodNumber,
                        end_period_number = payload.endPeriodNumber,
                        recurrence = payload.recurrence,
                    ),
                )
                applySeries(series, force = true)
            }
            Intent.CALENDAR_EXCEPTION_CREATE -> {
                val payload = json.decodeFromString(
                    CalendarExceptionCreatePayload.serializer(),
                    op.payloadJson,
                )
                val result = api.createCalendarException(
                    op.entityId,
                    com.itime.app.data.remote.CalendarExceptionCreateBody(
                        operation_id = op.operationId,
                        expected_series_version = currentSeriesVersion(op.entityId),
                        action = "cancelled",
                        occurrence_key = payload.occurrenceKey,
                    ),
                )
                db.withTransaction {
                    db.exceptionDao()
                        .findByOccurrence(op.entityId, payload.occurrenceKey)
                        ?.let { db.exceptionDao().delete(it.id) }
                    db.exceptionDao().upsertAll(listOf(result.exception.toEntity()))
                    db.seriesDao().upsertAll(listOf(result.series.toEntity()))
                }
            }
            Intent.SERIES_DELETE -> {
                api.deleteSeries(
                    op.entityId,
                    SeriesDeletePayloadBody(
                        operation_id = op.operationId,
                        expected_version = currentSeriesVersion(op.entityId),
                    ),
                )
                db.seriesDao().delete(op.entityId)
            }
            Intent.TERM_CREATE -> {
                val payload = json.decodeFromString(TermCreatePayload.serializer(), op.payloadJson)
                val term = api.createTerm(
                    TermCreateBody(
                        operation_id = op.operationId,
                        name = payload.name,
                        start_date = payload.startDate,
                        end_date = payload.endDate,
                        first_week_monday = payload.firstWeekMonday,
                        time_zone = payload.timeZone ?: defaultZone(),
                    ),
                )
                applyTerm(op.entityId, term)
            }
            Intent.SETTINGS_UPDATE -> {
                val payload = json.decodeFromString(SettingsUpdatePayload.serializer(), op.payloadJson)
                val settings = api.updateTimeSettings(
                    TimeSettingsUpdateBody(
                        operation_id = op.operationId,
                        expected_version = db.settingsDao().get()?.version ?: 0,
                        time_zone = payload.timeZone,
                    ),
                )
                db.settingsDao().upsert(settings.toEntity())
            }
            Intent.PROJECT_CREATE -> {
                val payload = json.decodeFromString(ProjectCreatePayload.serializer(), op.payloadJson)
                val project = api.createProject(
                    com.itime.app.data.remote.ProjectCreateBody(
                        operation_id = op.operationId,
                        kind = payload.kind,
                        name = payload.name,
                        description = payload.description,
                    ),
                )
                db.withTransaction {
                    // The project was created locally with a temporary id; move
                    // tasks that referenced it before normalising the row.
                    if (project.id != op.entityId) {
                        db.settingsDao().upsertMeta(
                            SyncMetaEntity("project.remap:${op.entityId}", project.id),
                        )
                        db.projectDao().delete(op.entityId)
                        db.taskDao().remapProject(
                            oldId = op.entityId,
                            projectId = project.id,
                            projectName = project.name,
                            projectKind = project.kind,
                        )
                        remapQueuedProjectReferences(op.entityId, project.id)
                    }
                    db.projectDao().upsertAll(listOf(project.toEntity()))
                }
            }
            else -> Unit
        }
    }

    private suspend fun executeTaskCreate(op: OutboxEntity) {
        val payload = json.decodeFromString(TaskCreatePayload.serializer(), op.payloadJson)
        var task: TaskDto = api.createTask(
            TaskCreateBody(
                operation_id = op.operationId,
                title = payload.title,
                description = payload.description,
                project_id = resolveProjectId(payload.projectId),
                estimated_minutes = payload.estimatedMinutes,
                priority = payload.priority,
                labels = payload.labels,
                steps = payload.steps.map { com.itime.app.data.remote.StepDraftBody(it) },
            ),
        )
        val schedule = payload.schedule
        if (schedule != null && schedule.dueKind != "none") {
            task = api.putSchedule(
                task.id,
                schedule.toUpdateBody(derivedOperationId(op.operationId, "schedule"), task.version),
            )
        }
        db.withTransaction {
            db.taskDao().delete(op.entityId)
            db.stepDao().deleteForTask(op.entityId)
            applyTaskInTransaction(task)
            db.outboxDao().remapEntity(op.entityId, task.id)
        }
    }

    private suspend fun executeSeriesCreate(op: OutboxEntity) {
        val payload = json.decodeFromString(SeriesCreatePayload.serializer(), op.payloadJson)
        val series = api.createSeries(
            SeriesCreateBody(
                operation_id = op.operationId,
                kind = payload.kind,
                term_id = payload.termId,
                name = payload.name,
                teacher = payload.teacher,
                description = payload.description,
                location = payload.location,
                busy = payload.busy,
                time_zone = payload.timeZone ?: defaultZone(),
                start_date = payload.startDate,
                starts_at = payload.startsAt,
                ends_at = payload.endsAt,
                end_day_offset = payload.endDayOffset,
                start_period_number = payload.startPeriodNumber,
                end_period_number = payload.endPeriodNumber,
                recurrence = payload.recurrence,
            ),
        )
        db.withTransaction {
            db.seriesDao().delete(op.entityId)
            db.seriesDao().upsert(series.toEntity())
            db.outboxDao().remapEntity(op.entityId, series.id)
        }
    }

    private suspend fun handlePushFailure(op: OutboxEntity, error: ApiException) {
        if (error.isConflict && op.intent == Intent.TASK_OCCURRENCE) {
            val payload = json.decodeFromString(TaskOccurrencePayload.serializer(), op.payloadJson)
            val match = occurrenceSnapshot(op.entityId, payload.occurrenceKey)
            if (match != null && match.status == payload.status) {
                db.occurrenceDao().upsert(match.toEntity(op.entityId))
                db.outboxDao().delete(op.operationId)
                refreshPendingCounts()
                return
            }
            db.outboxDao().markConflict(
                op.operationId,
                error.code,
                error.message,
                op.payloadJson,
                match?.let { json.encodeToString(OccurrenceStateDto.serializer(), it) },
            )
            status.update { it.copy(phase = SyncPhase.Conflict) }
            refreshPendingCounts()
            return
        }
        if (error.isConflict) {
            val server = runCatching { api.syncEntity(op.entityType, op.entityId) }.getOrNull()
            if (converges(op, server)) {
                server?.let { applyEntity(it) }
                db.outboxDao().delete(op.operationId)
                refreshPendingCounts()
                return
            }
            db.outboxDao().markConflict(
                op.operationId,
                error.code,
                error.message,
                op.payloadJson,
                server?.data?.toString(),
            )
            status.update { it.copy(phase = SyncPhase.Conflict) }
            refreshPendingCounts()
            return
        }
        if (error.isUnauthenticated) {
            status.update { it.copy(phase = SyncPhase.Offline, lastError = error.message) }
            return
        }
        db.outboxDao().markFailed(
            op.operationId,
            op.attempts + 1,
            if (error.retryable || error.status == 0) "pending" else "failed",
            error.code,
            error.message,
        )
        status.update {
            it.copy(
                phase = if (error.status == 0) SyncPhase.Offline else SyncPhase.Error,
                lastError = error.message,
            )
        }
    }

    /** Completion and idempotent writes converge without asking the user. */
    private suspend fun converges(op: OutboxEntity, server: SyncEntityDto?): Boolean {
        if (server == null) return false
        val data = server.data ?: return false
        return when (op.intent) {
            Intent.TASK_COMPLETE -> {
                val payload = json.decodeFromString(TaskCompletePayload.serializer(), op.payloadJson)
                val task = runCatching {
                    json.decodeFromJsonElement(TaskDto.serializer(), data)
                }.getOrNull() ?: return false
                (task.status == "completed") == payload.completed
            }
            Intent.TASK_OCCURRENCE -> {
                val payload = json.decodeFromString(TaskOccurrencePayload.serializer(), op.payloadJson)
                val occurrence = runCatching {
                    json.decodeFromJsonElement(OccurrenceStateDto.serializer(), data)
                }.getOrNull() ?: return false
                occurrence.status == payload.status
            }
            Intent.TASK_LIFECYCLE -> {
                val payload = json.decodeFromString(TaskLifecyclePayload.serializer(), op.payloadJson)
                val task = runCatching {
                    json.decodeFromJsonElement(TaskDto.serializer(), data)
                }.getOrNull() ?: return false
                when (payload.mode) {
                    "trash" -> task.lifecycle == "trashed"
                    "restore" -> task.lifecycle == "active"
                    "archive" -> task.lifecycle == "archived"
                    else -> task.lifecycle == "active"
                }
            }
            else -> false
        }
    }

    // -------------------------------------------------------------- pulling

    private suspend fun pull() {
        var cursor = db.settingsDao().meta(SyncMeta.CURSOR)
        if (cursor == null) {
            resyncSnapshot()
            return
        }
        var pages = 0
        while (pages++ < 100) {
            val page = api.syncChanges(cursor)
            if (page.reset_required) {
                _lastResetNotice.value = when (page.reset_reason) {
                    "cursor_expired" -> "同步记录已到期，已重新核对全部数据。"
                    "cursor_ahead" -> "服务端数据已恢复，已重新核对全部数据。"
                    else -> "已建立新的同步基线。"
                }
                resyncSnapshot()
                return
            }
            applyEvents(page.events)
            cursor = page.next_cursor
            db.settingsDao().upsertMeta(SyncMetaEntity(SyncMeta.CURSOR, cursor))
            if (!page.has_more) return
        }
    }

    suspend fun resyncSnapshot() {
        val snapshot = api.syncSnapshot()
        db.withTransaction {
            db.taskDao().clear()
            db.stepDao().clear()
            db.projectDao().clear()
            db.termDao().clear()
            db.termDao().clearPeriods()
            db.seriesDao().clear()
            db.exceptionDao().clear()
            db.additionDao().clear()
            db.occurrenceDao().clear()
            snapshot.tasks.forEach { task ->
                db.taskDao().upsert(task.toEntity())
                db.stepDao().upsertAll(task.steps.map { it.toEntity(task.id) })
            }
            db.projectDao().upsertAll(snapshot.projects.map { it.toEntity() })
            snapshot.terms.forEach { term ->
                db.termDao().upsertAll(listOf(term.toEntity()))
                db.termDao().upsertPeriods(term.toPeriods())
            }
            db.seriesDao().upsertAll(snapshot.series.map { it.toEntity() })
            db.exceptionDao().upsertAll(snapshot.calendar_exceptions.map { it.toEntity() })
            db.additionDao().upsertAll(snapshot.calendar_additions.map { it.toEntity() })
            snapshot.task_occurrences.forEach { occurrence ->
                occurrence.task_id?.let { taskId ->
                    db.occurrenceDao().upsert(occurrence.toEntity(taskId))
                }
            }
            db.settingsDao().upsert(snapshot.time_settings.toEntity())
            db.settingsDao().upsertMeta(SyncMetaEntity(SyncMeta.CURSOR, snapshot.cursor))
        }
        status.update { it.copy(resetNotice = _lastResetNotice.value) }
    }

    private suspend fun applyEvents(events: List<SyncEventDto>) {
        val seen = HashSet<String>()
        for (event in events) {
            val key = "${event.entity_type}:${event.entity_id}"
            if (!seen.add(key)) continue
            when (event.entity_type) {
                "task", "project", "term", "calendar_series", "calendar_exception",
                "calendar_addition", "task_occurrence", "time_settings" -> {
                    val entity = runCatching {
                        api.syncEntity(event.entity_type, event.entity_id)
                    }.getOrNull() ?: continue
                    applyEntity(entity)
                }
                "step" -> {
                    val step = runCatching {
                        api.syncEntity(event.entity_type, event.entity_id)
                    }.getOrNull() ?: continue
                    val taskId = step.task_id ?: continue
                    val task = runCatching { api.syncEntity("task", taskId) }.getOrNull() ?: continue
                    applyEntity(task)
                }
                else -> Unit // plans, reviews, memories, conversations are not mirrored locally
            }
        }
    }

    private suspend fun applyEntity(entity: SyncEntityDto) {
        if (entity.deleted) {
            when (entity.entity_type) {
                "task" -> db.taskDao().delete(entity.entity_id)
                "project" -> Unit
                "calendar_series" -> db.seriesDao().delete(entity.entity_id)
                "calendar_exception" -> db.exceptionDao().delete(entity.entity_id)
                "calendar_addition" -> db.additionDao().delete(entity.entity_id)
                "task_occurrence" -> db.occurrenceDao().delete(entity.entity_id)
                else -> Unit
            }
            return
        }
        val data = entity.data ?: return
        when (entity.entity_type) {
            "task" -> {
                val task = json.decodeFromJsonElement(TaskDto.serializer(), data)
                applyTask(task, force = false)
            }
            "project" -> {
                val project = json.decodeFromJsonElement(ProjectDto.serializer(), data)
                db.projectDao().upsertAll(listOf(project.toEntity()))
            }
            "term" -> {
                val term = json.decodeFromJsonElement(TermDto.serializer(), data)
                applyTerm(term.id, term)
            }
            "calendar_series" -> {
                val series = json.decodeFromJsonElement(SeriesDto.serializer(), data)
                db.seriesDao().upsert(series.toEntity())
            }
            "calendar_exception" -> {
                val item = json.decodeFromJsonElement(ExceptionDto.serializer(), data)
                db.exceptionDao().upsertAll(listOf(item.toEntity()))
            }
            "calendar_addition" -> {
                val item = json.decodeFromJsonElement(AdditionDto.serializer(), data)
                db.additionDao().upsertAll(listOf(item.toEntity()))
            }
            "task_occurrence" -> {
                val item = json.decodeFromJsonElement(OccurrenceStateDto.serializer(), data)
                val taskId = entity.task_id ?: item.task_id
                if (taskId != null) db.occurrenceDao().upsert(item.toEntity(taskId))
            }
            "time_settings" -> {
                val settings = json.decodeFromJsonElement(TimeSettingsDto.serializer(), data)
                db.settingsDao().upsert(settings.toEntity())
            }
        }
    }

    private suspend fun hasPendingOps(entityId: String): Boolean =
        db.outboxDao().forEntity(entityId).any {
            it.state == "pending" || it.state == "conflict"
        }

    /**
     * Applies a server projection.  While a local write for the same entity is
     * still queued (or waiting for conflict resolution) the local row keeps the
     * user's values; otherwise an incoming pull would silently revert an
     * unsynced edit.
     */
    private suspend fun applyTask(task: TaskDto, force: Boolean = false) {
        if (!force && hasPendingOps(task.id)) return
        db.withTransaction { applyTaskInTransaction(task) }
    }

    private suspend fun applyTaskInTransaction(task: TaskDto) {
        db.taskDao().upsert(task.toEntity())
        db.stepDao().deleteForTask(task.id)
        db.stepDao().upsertAll(task.steps.map { it.toEntity(task.id) })
    }

    private suspend fun applySeries(series: SeriesDto, force: Boolean = false) {
        if (!force && hasPendingOps(series.id)) return
        db.seriesDao().upsert(series.toEntity())
    }

    private suspend fun applyTerm(localId: String, term: TermDto) {
        db.withTransaction {
            db.termDao().upsertAll(listOf(term.toEntity()))
            db.termDao().upsertPeriods(term.toPeriods())
            if (localId != term.id) {
                db.outboxDao().remapEntity(localId, term.id)
            }
        }
    }

    /** Maps a temporary local project id to the server id once it is known. */
    private suspend fun resolveProjectId(localId: String?): String? {
        if (localId == null) return null
        return db.settingsDao().meta("project.remap:$localId") ?: localId
    }

    /** Rewrites queued task writes that still point at a temporary project id. */
    private suspend fun remapQueuedProjectReferences(oldId: String, realId: String) {
        db.outboxDao().all().forEach { queued ->
            if (queued.state != "pending" && queued.state != "failed") return@forEach
            when (queued.intent) {
                Intent.TASK_CREATE -> {
                    val payload = runCatching {
                        json.decodeFromString(TaskCreatePayload.serializer(), queued.payloadJson)
                    }.getOrNull() ?: return@forEach
                    if (payload.projectId == oldId) {
                        db.outboxDao().updatePayload(
                            queued.operationId,
                            json.encodeToString(TaskCreatePayload.serializer(), payload.copy(projectId = realId)),
                        )
                    }
                }
                Intent.TASK_UPDATE -> {
                    val payload = runCatching {
                        json.decodeFromString(TaskUpdatePayload.serializer(), queued.payloadJson)
                    }.getOrNull() ?: return@forEach
                    if (payload.projectId == oldId) {
                        db.outboxDao().updatePayload(
                            queued.operationId,
                            json.encodeToString(TaskUpdatePayload.serializer(), payload.copy(projectId = realId)),
                        )
                    }
                }
            }
        }
    }

    private suspend fun occurrenceSnapshot(
        taskId: String,
        occurrenceKey: String,
    ): OccurrenceStateDto? {
        val date = runCatching {
            java.time.LocalDate.parse(occurrenceKey.substringBefore('T'))
        }.getOrNull() ?: return null
        val items = runCatching {
            api.taskOccurrences(date.toString(), date.plusDays(1).toString())
        }.getOrNull() ?: return null
        return items.firstOrNull {
            it.occurrence_key == occurrenceKey && it.task_id == taskId
        }
    }

    private suspend fun currentTaskVersion(taskId: String): Int =
        db.taskDao().find(taskId)?.version ?: 1

    private suspend fun currentSeriesVersion(seriesId: String): Int =
        db.seriesDao().find(seriesId)?.version ?: 1

    private suspend fun defaultZone(): String =
        db.settingsDao().get()?.timeZone ?: "Asia/Shanghai"

    // ----------------------------------------------------- conflict resolution

    suspend fun resolveConflict(operationId: String, keepLocal: Boolean) {
        val op = db.outboxDao().find(operationId) ?: return
        if (keepLocal && op.intent == Intent.TASK_OCCURRENCE) {
            val payload = json.decodeFromString(TaskOccurrencePayload.serializer(), op.payloadJson)
            val match = occurrenceSnapshot(op.entityId, payload.occurrenceKey)
            if (match != null) {
                db.occurrenceDao().upsert(match.toEntity(op.entityId))
            } else {
                db.occurrenceDao().find(op.entityId, payload.occurrenceKey)?.let { local ->
                    db.occurrenceDao().upsert(local.copy(version = 0))
                }
            }
            db.outboxDao().resetToPending(operationId)
            refreshPendingCounts()
            syncNow("resolve-conflict")
            return
        }
        if (keepLocal) {
            // Rebase onto the current server version and retry.
            val server = runCatching { api.syncEntity(op.entityType, op.entityId) }.getOrNull()
            val version = server?.data?.let(::extractVersion) ?: return
            when (op.entityType) {
                Intent.ENTITY_TASK -> db.taskDao().updateVersion(op.entityId, version, pending = true)
                Intent.ENTITY_SERIES -> db.seriesDao().find(op.entityId)?.let {
                    db.seriesDao().upsert(it.copy(version = version))
                }
            }
            db.outboxDao().resetToPending(operationId)
        } else {
            val server = runCatching { api.syncEntity(op.entityType, op.entityId) }.getOrNull()
            db.outboxDao().delete(operationId)
            server?.let { entity ->
                when (entity.entity_type) {
                    "task" -> entity.data?.let {
                        applyTask(json.decodeFromJsonElement(TaskDto.serializer(), it), force = true)
                    }
                    "calendar_series" -> entity.data?.let {
                        applySeries(json.decodeFromJsonElement(SeriesDto.serializer(), it), force = true)
                    }
                    else -> applyEntity(entity)
                }
            }
        }
        refreshPendingCounts()
        syncNow("resolve-conflict")
    }

    /** Creates a project directly on the server (editor needs a real id at once). */
    suspend fun createProjectRemote(name: String): ProjectDto = api.createProject(
        com.itime.app.data.remote.ProjectCreateBody(
            operation_id = java.util.UUID.randomUUID().toString(),
            kind = "project",
            name = name,
        ),
    )

    /** Re-queues a write that previously failed with a permanent error. */
    suspend fun retryOperation(operationId: String) {
        db.outboxDao().resetToPending(operationId)
        refreshPendingCounts()
        syncNow("retry-failed")
    }

    /** Drops a local write the user chose not to send. */
    suspend fun discardOperation(operationId: String) {
        db.outboxDao().delete(operationId)
        refreshPendingCounts()
    }

    fun extractVersion(element: kotlinx.serialization.json.JsonElement): Int? =
        runCatching { element.jsonObject["version"]?.jsonPrimitive?.intOrNull }.getOrNull()

    private fun derivedOperationId(base: String, suffix: String): String =
        UUID.nameUUIDFromBytes("$base:$suffix".toByteArray()).toString()

    private fun ScheduleSpec.toUpdateBody(operationId: String, version: Int) = ScheduleUpdateBody(
        operation_id = operationId,
        expected_task_version = version,
        due_kind = dueKind,
        due_date = dueDate,
        due_time = dueTime,
        time_zone = timeZone,
        recurrence = recurrence,
    )
}
