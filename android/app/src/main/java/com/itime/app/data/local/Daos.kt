package com.itime.app.data.local

import androidx.room.Dao
import androidx.room.Query
import androidx.room.Transaction
import androidx.room.Upsert
import kotlinx.coroutines.flow.Flow

@Dao
interface TaskDao {
    @Query(
        """
        SELECT * FROM tasks
        WHERE lifecycle = 'active' AND deletedAt IS NULL
        ORDER BY
            CASE WHEN dueDate IS NULL THEN 1 ELSE 0 END,
            dueDate ASC,
            CASE priority WHEN 3 THEN 0 WHEN 2 THEN 1 WHEN 1 THEN 2 ELSE 3 END,
            createdAt ASC
        """,
    )
    fun observeActive(): Flow<List<TaskEntity>>

    @Query(
        """
        SELECT * FROM tasks
        WHERE lifecycle = 'archived' AND deletedAt IS NULL
        ORDER BY updatedAt DESC
        """,
    )
    fun observeArchived(): Flow<List<TaskEntity>>

    @Query("SELECT * FROM tasks WHERE deletedAt IS NOT NULL AND deletedAt != '' ORDER BY deletedAt DESC")
    fun observeTrashed(): Flow<List<TaskEntity>>

    @Query("SELECT * FROM tasks WHERE id = :id")
    fun observe(id: String): Flow<TaskEntity?>

    @Query("SELECT * FROM tasks WHERE id = :id")
    suspend fun find(id: String): TaskEntity?

    @Transaction
    @Query("SELECT * FROM tasks WHERE id = :id")
    fun observeWithSteps(id: String): Flow<TaskWithSteps?>

    @Query("SELECT * FROM tasks")
    suspend fun all(): List<TaskEntity>

    @Upsert
    suspend fun upsert(task: TaskEntity)

    @Upsert
    suspend fun upsertAll(tasks: List<TaskEntity>)

    @Query("DELETE FROM tasks WHERE id = :id")
    suspend fun delete(id: String)

    @Query("DELETE FROM tasks")
    suspend fun clear()

    @Query("UPDATE tasks SET version = :version, pendingSync = :pending WHERE id = :id")
    suspend fun updateVersion(id: String, version: Int, pending: Boolean)

    @Query("UPDATE tasks SET pendingSync = :pending WHERE id = :id")
    suspend fun setPending(id: String, pending: Boolean)
}

@Dao
interface StepDao {
    @Query("SELECT * FROM task_steps WHERE taskId = :taskId ORDER BY position")
    fun observeForTask(taskId: String): Flow<List<StepEntity>>

    @Query("SELECT * FROM task_steps WHERE taskId = :taskId ORDER BY position")
    suspend fun forTask(taskId: String): List<StepEntity>

    @Query("SELECT * FROM task_steps WHERE id = :id")
    suspend fun find(id: String): StepEntity?

    @Upsert
    suspend fun upsert(step: StepEntity)

    @Query("DELETE FROM task_steps WHERE id = :id")
    suspend fun delete(id: String)

    @Upsert
    suspend fun upsertAll(steps: List<StepEntity>)

    @Query("DELETE FROM task_steps WHERE taskId = :taskId")
    suspend fun deleteForTask(taskId: String)

    @Query("DELETE FROM task_steps")
    suspend fun clear()

    @Query("UPDATE task_steps SET completed = :completed, completedAt = :completedAt WHERE id = :id")
    suspend fun setCompleted(id: String, completed: Boolean, completedAt: String?)
}

@Dao
interface ProjectDao {
    @Query("SELECT * FROM projects ORDER BY name")
    fun observeAll(): Flow<List<ProjectEntity>>

    @Upsert
    suspend fun upsertAll(projects: List<ProjectEntity>)

    @Query("DELETE FROM projects")
    suspend fun clear()
}

@Dao
interface TermDao {
    @Query("SELECT * FROM terms ORDER BY startDate DESC")
    fun observeAll(): Flow<List<TermEntity>>

    @Query("SELECT * FROM term_periods WHERE termId = :termId ORDER BY periodNumber")
    fun observePeriods(termId: String): Flow<List<TermPeriodEntity>>

    @Query("SELECT * FROM term_periods WHERE termId = :termId ORDER BY periodNumber")
    suspend fun periods(termId: String): List<TermPeriodEntity>

    @Query("SELECT * FROM terms WHERE id = :id")
    suspend fun find(id: String): TermEntity?

    @Upsert
    suspend fun upsertAll(terms: List<TermEntity>)

    @Upsert
    suspend fun upsertPeriods(periods: List<TermPeriodEntity>)

    @Query("DELETE FROM terms")
    suspend fun clear()

    @Query("DELETE FROM term_periods")
    suspend fun clearPeriods()
}

@Dao
interface SeriesDao {
    @Query(
        """
        SELECT * FROM calendar_series
        WHERE lifecycle = 'active'
        ORDER BY startDate, startsAt
        """,
    )
    fun observeActive(): Flow<List<SeriesEntity>>

    @Query("SELECT * FROM calendar_series WHERE id = :id")
    fun observe(id: String): Flow<SeriesEntity?>

    @Query("SELECT * FROM calendar_series WHERE id = :id")
    suspend fun find(id: String): SeriesEntity?

    @Query("SELECT * FROM calendar_series")
    suspend fun all(): List<SeriesEntity>

    @Upsert
    suspend fun upsert(series: SeriesEntity)

    @Upsert
    suspend fun upsertAll(series: List<SeriesEntity>)

    @Query("DELETE FROM calendar_series WHERE id = :id")
    suspend fun delete(id: String)

    @Query("DELETE FROM calendar_series")
    suspend fun clear()
}

@Dao
interface ExceptionDao {
    @Query("SELECT * FROM calendar_exceptions WHERE seriesId = :seriesId")
    fun observeForSeries(seriesId: String): Flow<List<ExceptionEntity>>

    @Query("SELECT * FROM calendar_exceptions")
    fun observeAll(): Flow<List<ExceptionEntity>>

    @Upsert
    suspend fun upsertAll(items: List<ExceptionEntity>)

    @Query("DELETE FROM calendar_exceptions")
    suspend fun clear()

    @Query("DELETE FROM calendar_exceptions WHERE id = :id")
    suspend fun delete(id: String)
}

@Dao
interface AdditionDao {
    @Query("SELECT * FROM calendar_additions")
    fun observeAll(): Flow<List<AdditionEntity>>

    @Upsert
    suspend fun upsertAll(items: List<AdditionEntity>)

    @Query("DELETE FROM calendar_additions")
    suspend fun clear()

    @Query("DELETE FROM calendar_additions WHERE id = :id")
    suspend fun delete(id: String)
}

@Dao
interface OccurrenceDao {
    @Query("SELECT * FROM task_occurrences WHERE taskId = :taskId")
    fun observeForTask(taskId: String): Flow<List<OccurrenceEntity>>

    @Query("SELECT * FROM task_occurrences")
    fun observeAll(): Flow<List<OccurrenceEntity>>

    @Query("SELECT * FROM task_occurrences WHERE taskId = :taskId AND occurrenceKey = :key LIMIT 1")
    suspend fun find(taskId: String, key: String): OccurrenceEntity?

    @Upsert
    suspend fun upsert(item: OccurrenceEntity)

    @Upsert
    suspend fun upsertAll(items: List<OccurrenceEntity>)

    @Query("DELETE FROM task_occurrences WHERE taskId = :taskId")
    suspend fun deleteForTask(taskId: String)

    @Query("DELETE FROM task_occurrences")
    suspend fun clear()

    @Query("DELETE FROM task_occurrences WHERE id = :id")
    suspend fun delete(id: String)
}

@Dao
interface SettingsDao {
    @Query("SELECT * FROM time_settings WHERE `key` = 'settings'")
    fun observe(): Flow<TimeSettingsEntity?>

    @Query("SELECT * FROM time_settings WHERE `key` = 'settings'")
    suspend fun get(): TimeSettingsEntity?

    @Upsert
    suspend fun upsert(settings: TimeSettingsEntity)

    @Upsert
    suspend fun upsertMeta(meta: SyncMetaEntity)

    @Query("SELECT value FROM sync_meta WHERE `key` = :key")
    suspend fun meta(key: String): String?

    @Query("SELECT * FROM sync_meta")
    fun observeMeta(): Flow<List<SyncMetaEntity>>
}

@Dao
interface OutboxDao {
    @Query("SELECT * FROM outbox ORDER BY createdAt ASC")
    fun observeAll(): Flow<List<OutboxEntity>>

    @Query("SELECT COUNT(*) FROM outbox")
    fun observeCount(): Flow<Int>

    @Query("SELECT COUNT(*) FROM outbox WHERE state = 'conflict'")
    fun observeConflictCount(): Flow<Int>

    @Query("SELECT * FROM outbox WHERE state = 'pending' ORDER BY createdAt ASC LIMIT 1")
    suspend fun nextPending(): OutboxEntity?

    @Query("SELECT * FROM outbox WHERE state = 'conflict' ORDER BY createdAt ASC LIMIT 1")
    suspend fun firstConflict(): OutboxEntity?

    @Query("SELECT * FROM outbox WHERE operationId = :id")
    suspend fun find(id: String): OutboxEntity?

    @Query("SELECT * FROM outbox WHERE entityId = :entityId")
    suspend fun forEntity(entityId: String): List<OutboxEntity>

    @Query("SELECT * FROM outbox WHERE state = 'conflict' ORDER BY createdAt ASC")
    fun observeConflicts(): Flow<List<OutboxEntity>>

    @Query("SELECT * FROM outbox")
    suspend fun all(): List<OutboxEntity>

    @Upsert
    suspend fun upsert(item: OutboxEntity)

    @Query("DELETE FROM outbox WHERE operationId = :id")
    suspend fun delete(id: String)

    @Query("DELETE FROM outbox")
    suspend fun clear()

    @Query("UPDATE outbox SET attempts = :attempts, state = :state, lastErrorCode = :code, lastErrorMessage = :message WHERE operationId = :id")
    suspend fun markFailed(id: String, attempts: Int, state: String, code: String?, message: String?)

    @Query(
        "UPDATE outbox SET state = 'conflict', lastErrorCode = :code, lastErrorMessage = :message, conflictLocalJson = :local, conflictServerJson = :server WHERE operationId = :id",
    )
    suspend fun markConflict(
        id: String,
        code: String?,
        message: String?,
        local: String?,
        server: String?,
    )

    @Query("UPDATE outbox SET state = 'pending' WHERE operationId = :id")
    suspend fun resetToPending(id: String)

    @Query("UPDATE outbox SET entityId = :newId WHERE entityId = :oldId")
    suspend fun remapEntity(oldId: String, newId: String)
}
