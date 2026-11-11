package com.itime.app.data.sync

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The outbox stores payloads as JSON, so these round-trip tests guard against
 * field renames/default changes that would strand already queued writes.
 */
class PayloadsTest {

    private val json = Json {
        ignoreUnknownKeys = true
        explicitNulls = false
        encodeDefaults = true
    }

    @Test
    fun `task create payload keeps project and steps`() {
        val payload = TaskCreatePayload(
            title = "整理实验报告",
            description = "先写实验目的",
            projectId = "p-1",
            estimatedMinutes = 45,
            priority = 2,
            labels = listOf("学习", "本周"),
            steps = listOf("写目的", "写结论"),
        )
        val decoded = json.decodeFromString(
            TaskCreatePayload.serializer(),
            json.encodeToString(TaskCreatePayload.serializer(), payload),
        )
        assertEquals(payload, decoded)
    }

    @Test
    fun `step payload defaults stay backwards compatible`() {
        val decoded = json.decodeFromString(
            StepOpPayload.serializer(),
            """{"taskId":"t-1"}""",
        )
        assertEquals("t-1", decoded.taskId)
        assertEquals(null, decoded.stepId)
        assertEquals("", decoded.title)
        assertEquals(false, decoded.completed)
    }

    @Test
    fun `moved occurrence payload carries timezone`() {
        val payload = CalendarExceptionMovePayload(
            occurrenceKey = "2026-11-11T10:00:00",
            movedStartDate = "2026-11-12",
            movedStartsAt = "10:00:00",
            movedEndsAt = "11:40:00",
            timeZone = "Asia/Shanghai",
        )
        val text = json.encodeToString(CalendarExceptionMovePayload.serializer(), payload)
        assertTrue(text.contains("\"timeZone\":\"Asia/Shanghai\""))
        assertEquals(
            payload,
            json.decodeFromString(CalendarExceptionMovePayload.serializer(), text),
        )
    }

    @Test
    fun `schedule payload round trips weekly week numbers`() {
        val payload = TaskSchedulePayload(
            ScheduleSpec(
                dueKind = "datetime",
                dueDate = "2026-11-12",
                dueTime = "20:00:00",
                timeZone = "Asia/Shanghai",
                recurrence = com.itime.app.data.remote.RecurrenceBody(
                    frequency = "weekly",
                    interval = 1,
                    weekdays = listOf(3),
                    week_numbers = listOf(1, 2, 3),
                ),
            ),
        )
        val decoded = json.decodeFromString(
            TaskSchedulePayload.serializer(),
            json.encodeToString(TaskSchedulePayload.serializer(), payload),
        )
        assertEquals(payload, decoded)
    }

    @Test
    fun `unknown fields in stored payloads are ignored`() {
        val decoded = json.decodeFromString(
            TaskCompletePayload.serializer(),
            """{"completed":true,"legacyField":1}""",
        )
        assertTrue(decoded.completed)
    }
}
