package com.itime.app.data

import com.itime.app.data.local.SeriesEntity
import com.itime.app.data.local.TaskEntity
import com.itime.app.data.local.toEntity
import com.itime.app.data.remote.LabelDto
import com.itime.app.data.remote.ProjectRefDto
import com.itime.app.data.remote.RecurrenceDto
import com.itime.app.data.remote.ScheduleDto
import com.itime.app.data.remote.StepProgressDto
import com.itime.app.data.remote.TaskDto
import com.itime.app.data.remote.TermDto
import com.itime.app.data.local.toPeriods
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MappersTest {
    @Test
    fun `task dto flattens schedule recurrence and labels`() {
        val dto = TaskDto(
            id = "t-1",
            title = "写实验报告",
            description = "先整理数据",
            project = ProjectRefDto(id = "p-1", kind = "project", name = "课程"),
            estimated_minutes = 45,
            priority = 2,
            status = "open",
            version = 3,
            labels = listOf(LabelDto("实验"), LabelDto("本周")),
            step_progress = StepProgressDto(total = 2, completed = 1),
            schedule = ScheduleDto(
                configured = true,
                due_kind = "datetime",
                due_date = "2026-11-12",
                due_time = "15:00:00",
                due_at = "2026-11-12T07:00:00Z",
                time_zone = "Asia/Shanghai",
                recurrence = RecurrenceDto(frequency = "weekly", weekdays = listOf(2, 4)),
            ),
        )
        val entity: TaskEntity = dto.toEntity()
        assertEquals("课程", entity.projectName)
        assertEquals(listOf("实验", "本周"), entity.labels)
        assertEquals("datetime", entity.dueKind)
        assertEquals("2026-11-12", entity.dueDate)
        assertEquals("15:00:00", entity.dueTime)
        assertEquals("weekly", entity.recFrequency)
        assertEquals(listOf(2, 4), entity.recWeekdaysCsv.split(',').map { it.toInt() })
        assertTrue(entity.scheduleConfigured)
        assertFalse(entity.isCompleted)
    }

    @Test
    fun `series dto keeps course week numbers and weekday list`() {
        val dto = com.itime.app.data.remote.SeriesDto(
            id = "s-1",
            kind = "course",
            term_id = "term-1",
            name = "数据结构",
            teacher = "王老师",
            location = "实验楼 B203",
            start_date = "2026-09-07",
            starts_at = "10:00:00",
            ends_at = "11:40:00",
            recurrence = RecurrenceDto(
                frequency = "weekly",
                weekdays = listOf(3),
                week_numbers = listOf(1, 2, 3, 4, 5),
            ),
        )
        val entity: SeriesEntity = dto.toEntity()
        assertEquals("数据结构", entity.name)
        assertEquals(listOf(3), entity.weekdays)
        assertEquals(listOf(1, 2, 3, 4, 5), entity.weekNumbers)
        assertTrue(entity.isCourse)
    }

    @Test
    fun `term periods map with period order preserved`() {
        val dto = TermDto(
            id = "term-1",
            name = "2026 秋",
            start_date = "2026-09-07",
            end_date = "2027-01-10",
            first_week_monday = "2026-09-07",
            time_zone = "Asia/Shanghai",
            periods = listOf(
                com.itime.app.data.remote.TermPeriodDto(1, "第 1 节", "08:00:00", "08:45:00"),
                com.itime.app.data.remote.TermPeriodDto(2, "第 2 节", "08:55:00", "09:40:00"),
            ),
        )
        val periods = dto.toPeriods()
        assertEquals(listOf(1, 2), periods.map { it.periodNumber })
        assertEquals("term-1", periods.first().termId)
    }
}
