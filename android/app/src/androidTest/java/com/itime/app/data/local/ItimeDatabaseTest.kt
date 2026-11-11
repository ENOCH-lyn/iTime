package com.itime.app.data.local

import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Verifies the offline cache contract on a real Android SQLite database:
 * tasks cascade their steps, trash/active queries stay separated and the
 * outbox keeps conflict state per operation.
 */
@RunWith(AndroidJUnit4::class)
class ItimeDatabaseTest {
    private lateinit var db: ItimeDatabase

    @Before
    fun setUp() {
        db = Room.inMemoryDatabaseBuilder(
            ApplicationProvider.getApplicationContext(),
            ItimeDatabase::class.java,
        ).allowMainThreadQueries().build()
    }

    @After
    fun tearDown() {
        db.close()
    }

    @Test
    fun taskWithStepsRoundTripsAndCascades() = runBlocking {
        db.taskDao().upsert(
            TaskEntity(
                id = "t1",
                title = "离线任务",
                dueDate = "2026-11-12",
                lifecycle = "active",
                version = 2,
                labelsCsv = "学习,本周",
            ),
        )
        db.stepDao().upsertAll(
            listOf(
                StepEntity(id = "s1", taskId = "t1", title = "第一步", position = 0),
                StepEntity(id = "s2", taskId = "t1", title = "第二步", position = 1, completed = true),
            ),
        )

        val loaded = db.taskDao().observeWithSteps("t1").first()
        assertEquals("离线任务", loaded?.task?.title)
        assertEquals(listOf("学习", "本周"), loaded?.task?.labels)
        assertEquals(2, loaded?.steps?.size)
        assertTrue(loaded!!.steps.last().completed)

        db.taskDao().delete("t1")
        assertNull(db.taskDao().find("t1"))
        assertTrue(db.stepDao().forTask("t1").isEmpty())
    }

    @Test
    fun trashAndActiveQueriesDoNotMix() = runBlocking {
        db.taskDao().upsert(TaskEntity(id = "a", title = "进行中", lifecycle = "active"))
        db.taskDao().upsert(
            TaskEntity(id = "b", title = "已删除", lifecycle = "trashed", deletedAt = "2026-11-11T10:00:00Z"),
        )

        assertEquals(listOf("a"), db.taskDao().observeActive().first().map { it.id })
        assertEquals(listOf("b"), db.taskDao().observeTrashed().first().map { it.id })
    }

    @Test
    fun outboxTracksPendingAndConflictCounts() = runBlocking {
        db.outboxDao().upsert(
            OutboxEntity(
                operationId = "op-1",
                entityType = "task",
                entityId = "t1",
                intent = "task.update",
                payloadJson = "{}",
                baseVersion = 2,
                createdAt = 1,
            ),
        )
        db.outboxDao().upsert(
            OutboxEntity(
                operationId = "op-2",
                entityType = "task",
                entityId = "t2",
                intent = "task.complete",
                payloadJson = "{}",
                baseVersion = 1,
                createdAt = 2,
            ),
        )
        assertEquals(2, db.outboxDao().all().count { it.state == "pending" })

        db.outboxDao().markConflict("op-2", "task_version_conflict", "冲突", "{}", "{\"version\":3}")
        assertEquals(1, db.outboxDao().all().count { it.state == "conflict" })
        assertEquals("op-1", db.outboxDao().nextPending()?.operationId)

        db.outboxDao().resetToPending("op-2")
        db.outboxDao().delete("op-2")
        assertEquals(listOf("op-1"), db.outboxDao().all().map { it.operationId })
    }

    @Test
    fun seriesUpsertKeepsRecurrenceFields() = runBlocking {
        db.seriesDao().upsert(
            SeriesEntity(
                id = "s1",
                name = "信号与系统",
                kind = "course",
                startDate = "2026-09-07",
                startsAt = "08:00:00",
                endsAt = "09:40:00",
                recFrequency = "weekly",
                recWeekdaysCsv = "5",
                recWeekNumbersCsv = "1,2,3",
            ),
        )
        val stored = db.seriesDao().find("s1")
        assertEquals(listOf(5), stored?.weekdays)
        assertEquals(listOf(1, 2, 3), stored?.weekNumbers)
        assertEquals(1, db.seriesDao().observeActive().first().size)
    }
}
