package com.itime.app.data.local

import androidx.room.Embedded
import androidx.room.Entity
import androidx.room.ForeignKey
import androidx.room.Index
import androidx.room.PrimaryKey
import androidx.room.Relation

/**
 * Local mirror of the server entities that the app can read offline.
 *
 * Nightly/scheduled sync replaces rows with the server projection; unsynced
 * local edits are tracked separately in [OutboxEntity] so a row can be shown
 * with an optimistic value while its write is still queued.
 */
@Entity(tableName = "tasks", indices = [Index("lifecycle"), Index("status"), Index("dueDate")])
data class TaskEntity(
    @PrimaryKey val id: String,
    val title: String,
    val description: String = "",
    val projectId: String? = null,
    val projectName: String? = null,
    val projectKind: String? = null,
    val estimatedMinutes: Int? = null,
    val estimateSource: String? = null,
    val priority: Int = 0,
    val status: String = "open",
    val completedAt: String? = null,
    val lifecycle: String = "active",
    val archivedAt: String? = null,
    val deletedAt: String? = null,
    val purgeAfter: String? = null,
    val version: Int = 1,
    val labelsCsv: String = "",
    val createdAt: String? = null,
    val updatedAt: String? = null,
    // schedule projection
    val dueKind: String = "none",
    val dueDate: String? = null,
    val dueTime: String? = null,
    val dueAt: String? = null,
    val scheduleTz: String? = null,
    val scheduleConfigured: Boolean = false,
    val lastOccurrenceDate: String? = null,
    val recFrequency: String = "none",
    val recInterval: Int = 1,
    val recWeekdaysCsv: String = "",
    val recMonth: Int? = null,
    val recDayOfMonth: Int? = null,
    val recUntil: String? = null,
    val recCount: Int? = null,
    val splittable: Boolean = true,
    val minimumBlockMinutes: Int = 15,
    // local bookkeeping
    val pendingSync: Boolean = false,
) {
    val labels: List<String>
        get() = labelsCsv.split(',').map { it.trim() }.filter { it.isNotEmpty() }

    val isCompleted: Boolean get() = status == "completed"
    val isRepeating: Boolean get() = recFrequency != "none"
}

@Entity(
    tableName = "task_steps",
    foreignKeys = [
        ForeignKey(
            entity = TaskEntity::class,
            parentColumns = ["id"],
            childColumns = ["taskId"],
            onDelete = ForeignKey.CASCADE,
        ),
    ],
    indices = [Index("taskId")],
)
data class StepEntity(
    @PrimaryKey val id: String,
    val taskId: String,
    val title: String,
    val position: Int = 0,
    val completed: Boolean = false,
    val completedAt: String? = null,
)

data class TaskWithSteps(
    @Embedded val task: TaskEntity,
    @Relation(parentColumn = "id", entityColumn = "taskId")
    val steps: List<StepEntity> = emptyList(),
)

@Entity(tableName = "projects")
data class ProjectEntity(
    @PrimaryKey val id: String,
    val kind: String = "project",
    val name: String,
    val description: String = "",
    val version: Int = 1,
    val updatedAt: String? = null,
)

@Entity(tableName = "terms")
data class TermEntity(
    @PrimaryKey val id: String,
    val name: String,
    val startDate: String,
    val endDate: String,
    val firstWeekMonday: String,
    val timeZone: String = "Asia/Shanghai",
    val version: Int = 1,
    val updatedAt: String? = null,
)

@Entity(tableName = "term_periods", primaryKeys = ["termId", "periodNumber"])
data class TermPeriodEntity(
    val termId: String,
    val periodNumber: Int,
    val name: String,
    val startsAt: String,
    val endsAt: String,
)

@Entity(tableName = "calendar_series", indices = [Index("termId"), Index("lifecycle")])
data class SeriesEntity(
    @PrimaryKey val id: String,
    val kind: String = "fixed",
    val termId: String? = null,
    val name: String,
    val teacher: String = "",
    val description: String = "",
    val location: String = "",
    val busy: Boolean = true,
    val sourceType: String = "manual",
    val timeZone: String = "Asia/Shanghai",
    val startDate: String,
    val startsAt: String,
    val endsAt: String,
    val endDayOffset: Int = 0,
    val startPeriodNumber: Int? = null,
    val endPeriodNumber: Int? = null,
    val recFrequency: String = "weekly",
    val recInterval: Int = 1,
    val recWeekdaysCsv: String = "",
    val recMonth: Int? = null,
    val recDayOfMonth: Int? = null,
    val recUntil: String? = null,
    val recCount: Int? = null,
    val recWeekNumbersCsv: String = "",
    val lifecycle: String = "active",
    val version: Int = 1,
    val updatedAt: String? = null,
    val pendingSync: Boolean = false,
) {
    val weekdays: List<Int>
        get() = recWeekdaysCsv.split(',').mapNotNull { it.trim().toIntOrNull() }

    val weekNumbers: List<Int>
        get() = recWeekNumbersCsv.split(',').mapNotNull { it.trim().toIntOrNull() }

    val isCourse: Boolean get() = kind == "course"
}

@Entity(tableName = "calendar_exceptions", indices = [Index("seriesId")])
data class ExceptionEntity(
    @PrimaryKey val id: String,
    val seriesId: String,
    val occurrenceKey: String,
    val originalStartDate: String,
    val action: String = "cancelled",
    val movedStartDate: String? = null,
    val movedStartsAt: String? = null,
    val movedEndsAt: String? = null,
    val movedEndDayOffset: Int? = null,
    val movedTimeZone: String? = null,
    val overrideName: String? = null,
    val overrideLocation: String? = null,
    val overrideDescription: String? = null,
    val overrideBusy: Boolean? = null,
    val version: Int = 1,
)

@Entity(tableName = "calendar_additions", indices = [Index("seriesId")])
data class AdditionEntity(
    @PrimaryKey val id: String,
    val seriesId: String,
    val occurrenceKey: String,
    val localStartDate: String,
    val startsAt: String? = null,
    val endsAt: String? = null,
    val endDayOffset: Int = 0,
    val timeZone: String? = null,
    val status: String = "confirmed",
    val overrideName: String? = null,
    val overrideLocation: String? = null,
    val overrideBusy: Boolean? = null,
    val version: Int = 1,
)

@Entity(tableName = "task_occurrences", indices = [Index("taskId")])
data class OccurrenceEntity(
    @PrimaryKey val id: String,
    val taskId: String,
    val occurrenceKey: String,
    val originalDate: String,
    val kind: String = "base",
    val effectiveDate: String? = null,
    val effectiveTime: String? = null,
    val effectiveAt: String? = null,
    val effectiveTimeZone: String? = null,
    val rescheduled: Boolean = false,
    val status: String = "open",
    val completedAt: String? = null,
    val completedStepIdsCsv: String = "",
    val makeupForKey: String? = null,
    val makeupForDate: String? = null,
    val version: Int = 1,
)

@Entity(tableName = "time_settings")
data class TimeSettingsEntity(
    @PrimaryKey val key: String = "settings",
    val initialized: Boolean = false,
    val timeZone: String? = null,
    val weatherLocation: String? = null,
    val weekStartsOn: Int = 1,
    val activityDayStartMinute: Int = 240,
    val version: Int = 0,
)

@Entity(tableName = "sync_meta")
data class SyncMetaEntity(
    @PrimaryKey val key: String,
    val value: String,
)

/**
 * One queued local write.  The payload is the exact request body that will be
 * sent once the device is online; ``baseVersion`` is the entity version the
 * payload was built on, which is what lets the server detect a real conflict.
 */
@Entity(tableName = "outbox", indices = [Index("entityId"), Index("state")])
data class OutboxEntity(
    @PrimaryKey val operationId: String,
    val entityType: String,
    val entityId: String,
    val intent: String,
    val payloadJson: String,
    val baseVersion: Int,
    val createdAt: Long,
    val attempts: Int = 0,
    val state: String = "pending",
    val lastErrorCode: String? = null,
    val lastErrorMessage: String? = null,
    val conflictLocalJson: String? = null,
    val conflictServerJson: String? = null,
)
