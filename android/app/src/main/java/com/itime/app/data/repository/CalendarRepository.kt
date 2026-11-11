package com.itime.app.data.repository

import androidx.room.withTransaction
import com.itime.app.data.local.AdditionEntity
import com.itime.app.data.local.ExceptionEntity
import com.itime.app.data.local.ItimeDatabase
import com.itime.app.data.local.OutboxEntity
import com.itime.app.data.local.ProjectEntity
import com.itime.app.data.local.SeriesEntity
import com.itime.app.data.local.TermEntity
import com.itime.app.data.local.TermPeriodEntity
import com.itime.app.data.local.TimeSettingsEntity
import com.itime.app.data.remote.RecurrenceBody
import com.itime.app.data.sync.Intent
import com.itime.app.data.sync.SeriesCreatePayload
import com.itime.app.data.sync.SeriesDeletePayload
import com.itime.app.data.sync.SeriesUpdatePayload
import com.itime.app.data.sync.SettingsUpdatePayload
import com.itime.app.data.sync.SyncEngine
import com.itime.app.data.sync.TermCreatePayload
import com.itime.app.domain.SeriesDraft
import kotlinx.coroutines.flow.Flow
import kotlinx.serialization.json.Json
import java.time.LocalDate
import java.util.UUID

class CalendarRepository(
    private val db: ItimeDatabase,
    private val json: Json,
    private val sync: SyncEngine,
) {
    fun observeSeries(): Flow<List<SeriesEntity>> = db.seriesDao().observeActive()
    fun observeSeries(id: String): Flow<SeriesEntity?> = db.seriesDao().observe(id)
    fun observeExceptions(seriesId: String): Flow<List<ExceptionEntity>> =
        db.exceptionDao().observeForSeries(seriesId)

    fun observeAllExceptions(): Flow<List<ExceptionEntity>> = db.exceptionDao().observeAll()
    fun observeAdditions(): Flow<List<AdditionEntity>> = db.additionDao().observeAll()
    fun observeTerms(): Flow<List<TermEntity>> = db.termDao().observeAll()
    fun observeProjects(): Flow<List<ProjectEntity>> = db.projectDao().observeAll()
    fun observeTimeSettings(): Flow<TimeSettingsEntity?> = db.settingsDao().observe()

    suspend fun createSeries(draft: SeriesDraft): String {
        val localId = UUID.randomUUID().toString()
        val operationId = UUID.randomUUID().toString()
        val payload = draft.toPayload()
        db.withTransaction {
            db.seriesDao().upsert(draft.toEntity(localId))
            db.outboxDao().upsert(
                OutboxEntity(
                    operationId = operationId,
                    entityType = Intent.ENTITY_SERIES,
                    entityId = localId,
                    intent = Intent.SERIES_CREATE,
                    payloadJson = json.encodeToString(SeriesCreatePayload.serializer(), payload),
                    baseVersion = 0,
                    createdAt = 0L,
                ),
            )
        }
        sync.requestSync("local-create-series")
        return localId
    }

    suspend fun updateSeries(id: String, draft: SeriesDraft) {
        val existing = db.seriesDao().find(id) ?: return
        val payload = draft.toUpdatePayload()
        db.withTransaction {
            db.seriesDao().upsert(draft.toEntity(id).copy(version = existing.version, pendingSync = true))
            db.outboxDao().upsert(
                OutboxEntity(
                    operationId = UUID.randomUUID().toString(),
                    entityType = Intent.ENTITY_SERIES,
                    entityId = id,
                    intent = Intent.SERIES_UPDATE,
                    payloadJson = json.encodeToString(SeriesUpdatePayload.serializer(), payload),
                    baseVersion = existing.version,
                    createdAt = System.currentTimeMillis(),
                ),
            )
        }
        sync.requestSync("local-update-series")
    }

    suspend fun deleteSeries(id: String) {
        val existing = db.seriesDao().find(id) ?: return
        db.withTransaction {
            db.seriesDao().delete(id)
            db.outboxDao().upsert(
                OutboxEntity(
                    operationId = UUID.randomUUID().toString(),
                    entityType = Intent.ENTITY_SERIES,
                    entityId = id,
                    intent = Intent.SERIES_DELETE,
                    payloadJson = json.encodeToString(
                        SeriesDeletePayload.serializer(),
                        SeriesDeletePayload(),
                    ),
                    baseVersion = existing.version,
                    createdAt = System.currentTimeMillis(),
                ),
            )
        }
        sync.requestSync("local-delete-series")
    }

    suspend fun createTerm(
        name: String,
        startDate: LocalDate,
        endDate: LocalDate,
        firstWeekMonday: LocalDate,
        timeZone: String,
    ): String {
        val localId = UUID.randomUUID().toString()
        db.withTransaction {
            db.termDao().upsertAll(
                listOf(
                    TermEntity(
                        id = localId,
                        name = name,
                        startDate = startDate.toString(),
                        endDate = endDate.toString(),
                        firstWeekMonday = firstWeekMonday.toString(),
                        timeZone = timeZone,
                    ),
                ),
            )
            db.outboxDao().upsert(
                OutboxEntity(
                    operationId = UUID.randomUUID().toString(),
                    entityType = Intent.ENTITY_TERM,
                    entityId = localId,
                    intent = Intent.TERM_CREATE,
                    payloadJson = json.encodeToString(
                        TermCreatePayload.serializer(),
                        TermCreatePayload(
                            name = name,
                            startDate = startDate.toString(),
                            endDate = endDate.toString(),
                            firstWeekMonday = firstWeekMonday.toString(),
                            timeZone = timeZone,
                        ),
                    ),
                    baseVersion = 0,
                    createdAt = 0L,
                ),
            )
        }
        sync.requestSync("local-create-term")
        return localId
    }

    suspend fun updateTimeZone(timeZone: String) {
        val settings = db.settingsDao().get()
        db.withTransaction {
            db.settingsDao().upsert(
                (settings ?: TimeSettingsEntity()).copy(timeZone = timeZone),
            )
            db.outboxDao().upsert(
                OutboxEntity(
                    operationId = UUID.randomUUID().toString(),
                    entityType = Intent.ENTITY_SETTINGS,
                    entityId = settings?.let { "settings" } ?: "settings",
                    intent = Intent.SETTINGS_UPDATE,
                    payloadJson = json.encodeToString(
                        SettingsUpdatePayload.serializer(),
                        SettingsUpdatePayload(timeZone),
                    ),
                    baseVersion = settings?.version ?: 0,
                    createdAt = System.currentTimeMillis(),
                ),
            )
        }
        sync.requestSync("local-timezone")
    }

    suspend fun periods(termId: String): List<TermPeriodEntity> = db.termDao().periods(termId)
}

private fun SeriesDraft.toPayload(): SeriesCreatePayload = SeriesCreatePayload(
    kind = kind,
    termId = termId,
    name = name.trim(),
    teacher = teacher.trim(),
    description = description.trim(),
    location = location.trim(),
    busy = busy,
    timeZone = null,
    startDate = startDate.toString(),
    startsAt = startsAt.toString(),
    endsAt = endsAt.toString(),
    endDayOffset = endDayOffset,
    startPeriodNumber = startPeriodNumber,
    endPeriodNumber = endPeriodNumber,
    recurrence = recurrence.toBody(),
)

private fun SeriesDraft.toUpdatePayload(): SeriesUpdatePayload = SeriesUpdatePayload(
    kind = kind,
    termId = termId,
    name = name.trim(),
    teacher = teacher.trim(),
    description = description.trim(),
    location = location.trim(),
    busy = busy,
    timeZone = null,
    startDate = startDate.toString(),
    startsAt = startsAt.toString(),
    endsAt = endsAt.toString(),
    endDayOffset = endDayOffset,
    startPeriodNumber = startPeriodNumber,
    endPeriodNumber = endPeriodNumber,
    recurrence = recurrence.toBody(),
)

private fun SeriesDraft.toEntity(id: String): SeriesEntity = SeriesEntity(
    id = id,
    kind = kind,
    termId = termId,
    name = name.trim(),
    teacher = teacher.trim(),
    description = description.trim(),
    location = location.trim(),
    busy = busy,
    startDate = startDate.toString(),
    startsAt = startsAt.toString(),
    endsAt = endsAt.toString(),
    endDayOffset = endDayOffset,
    startPeriodNumber = startPeriodNumber,
    endPeriodNumber = endPeriodNumber,
    recFrequency = recurrence.frequency,
    recInterval = recurrence.interval,
    recWeekdaysCsv = recurrence.weekdays.joinToString(","),
    recUntil = recurrence.until?.toString(),
    recCount = recurrence.count,
    version = 0,
    pendingSync = true,
)

fun seriesDraftFrom(series: SeriesEntity): SeriesDraft = SeriesDraft(
    kind = series.kind,
    termId = series.termId,
    name = series.name,
    teacher = series.teacher,
    location = series.location,
    description = series.description,
    busy = series.busy,
    startDate = com.itime.app.core.Times.parseDate(series.startDate) ?: LocalDate.now(),
    startsAt = com.itime.app.core.Times.parseTime(series.startsAt) ?: java.time.LocalTime.of(9, 0),
    endsAt = com.itime.app.core.Times.parseTime(series.endsAt) ?: java.time.LocalTime.of(10, 0),
    endDayOffset = series.endDayOffset,
    startPeriodNumber = series.startPeriodNumber,
    endPeriodNumber = series.endPeriodNumber,
    recurrence = com.itime.app.domain.RecurrenceSpec(
        frequency = series.recFrequency,
        interval = series.recInterval,
        weekdays = series.weekdays,
        until = com.itime.app.core.Times.parseDate(series.recUntil),
        count = series.recCount,
    ),
)
