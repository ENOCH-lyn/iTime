package com.itime.app.domain

import com.itime.app.data.local.OccurrenceEntity
import com.itime.app.data.local.SeriesEntity
import com.itime.app.data.local.TaskEntity
import com.itime.app.data.local.TermEntity
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate
import java.time.ZoneId

class AgendaTest {
    private val zone = ZoneId.of("Asia/Shanghai")

    private fun series(
        id: String = "s1",
        weekdays: String = "1,3",
        start: String = "2026-11-09",
        frequency: String = "weekly",
    ) = SeriesEntity(
        id = id,
        name = "高等数学",
        kind = "course",
        startDate = start,
        startsAt = "09:00:00",
        endsAt = "10:40:00",
        recFrequency = frequency,
        recInterval = 1,
        recWeekdaysCsv = weekdays,
        timeZone = "Asia/Shanghai",
    )

    @Test
    fun `weekly rule expands only selected weekdays`() {
        val items = Agenda.build(
            from = LocalDate.parse("2026-11-09"),
            to = LocalDate.parse("2026-11-15"),
            zone = zone,
            series = listOf(series()),
            terms = emptyMap(),
            exceptions = emptyList(),
            additions = emptyList(),
            tasks = emptyList(),
            occurrences = emptyList(),
        )
        assertEquals(listOf("2026-11-09", "2026-11-11"), items.map { it.date.toString() })
        assertEquals("09:00", items.first().startTime?.toString())
        // The server expects the occurrence key to carry seconds ("T09:00:00").
        items.forEach { item ->
            assertEquals("${item.date}T09:00:00", item.occurrenceKey)
        }
    }

    @Test
    fun `course week numbers restrict occurrences to the given weeks`() {
        val term = TermEntity(
            id = "t1",
            name = "2026 秋",
            startDate = "2026-09-07",
            endDate = "2027-01-10",
            firstWeekMonday = "2026-09-07",
        )
        val item = series().copy(termId = "t1", recWeekNumbersCsv = "1,2,3")
        val items = Agenda.build(
            from = LocalDate.parse("2026-11-09"),
            to = LocalDate.parse("2026-11-15"),
            zone = zone,
            series = listOf(item),
            terms = mapOf("t1" to term),
            exceptions = emptyList(),
            additions = emptyList(),
            tasks = emptyList(),
            occurrences = emptyList(),
        )
        // 2026-11-09 is week 10 of that term, so nothing renders.
        assertTrue(items.isEmpty())
    }

    @Test
    fun `daily interval and count stop after the requested repetitions`() {
        val pattern = Agenda.Pattern(
            start = LocalDate.parse("2026-11-09"),
            frequency = "daily",
            interval = 2,
            weekdays = emptyList(),
            until = null,
            count = 3,
            weekNumbers = emptyList(),
        )
        val dates = Agenda.expand(pattern, LocalDate.parse("2026-11-01"), LocalDate.parse("2026-11-30"))
        assertEquals(listOf("2026-11-09", "2026-11-11", "2026-11-13"), dates.map { it.toString() })
    }

    @Test
    fun `cancelled exception hides one occurrence and moved one changes the day`() {
        val base = series()
        val cancelled = com.itime.app.data.local.ExceptionEntity(
            id = "e1",
            seriesId = "s1",
            occurrenceKey = "2026-11-09T09:00:00",
            originalStartDate = "2026-11-09",
            action = "cancelled",
        )
        val moved = com.itime.app.data.local.ExceptionEntity(
            id = "e2",
            seriesId = "s1",
            occurrenceKey = "2026-11-11T09:00:00",
            originalStartDate = "2026-11-11",
            action = "moved",
            movedStartDate = "2026-11-12",
            movedStartsAt = "14:00:00",
            movedEndsAt = "15:40:00",
        )
        val items = Agenda.build(
            from = LocalDate.parse("2026-11-09"),
            to = LocalDate.parse("2026-11-15"),
            zone = zone,
            series = listOf(base),
            terms = emptyMap(),
            exceptions = listOf(cancelled, moved),
            additions = emptyList(),
            tasks = emptyList(),
            occurrences = emptyList(),
        )
        assertEquals(listOf("2026-11-12"), items.map { it.date.toString() })
        assertEquals("14:00", items.first().startTime?.toString())
    }

    @Test
    fun `completed repeating task instance disappears but other instances remain`() {
        val task = TaskEntity(
            id = "task-1",
            title = "背单词",
            dueDate = "2026-11-09",
            dueTime = "20:00",
            scheduleConfigured = true,
            recFrequency = "weekly",
            recInterval = 1,
            recWeekdaysCsv = "1",
            scheduleTz = "Asia/Shanghai",
            estimatedMinutes = 20,
        )
        val done = OccurrenceEntity(
            id = "o1",
            taskId = "task-1",
            occurrenceKey = "2026-11-09T20:00:00",
            originalDate = "2026-11-09",
            status = "completed",
            completedAt = "2026-11-09T12:10:00Z",
        )
        val items = Agenda.build(
            from = LocalDate.parse("2026-11-09"),
            to = LocalDate.parse("2026-11-22"),
            zone = zone,
            series = emptyList(),
            terms = emptyMap(),
            exceptions = emptyList(),
            additions = emptyList(),
            tasks = listOf(task),
            occurrences = listOf(done),
        )
        assertEquals(listOf("2026-11-16"), items.map { it.date.toString() })
        assertTrue(items.none { it.completed })
    }
}
