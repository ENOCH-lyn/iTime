package com.itime.app.domain

import com.itime.app.data.local.TermEntity
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.LocalDate

class TermsTest {

    private fun term(
        id: String = "t1",
        name: String = "2026-2027 秋季学期",
        start: String = "2026-09-07",
        end: String = "2027-01-24",
        monday: String = "2026-09-07",
    ) = TermEntity(
        id = id,
        name = name,
        startDate = start,
        endDate = end,
        firstWeekMonday = monday,
    )

    @Test
    fun `end date is the last day of the last week`() {
        assertEquals(
            LocalDate.of(2027, 1, 24),
            Terms.endDate(LocalDate.of(2026, 9, 7), 20),
        )
    }

    @Test
    fun `weeks round up partial weeks`() {
        assertEquals(20, Terms.weeksBetween(LocalDate.of(2026, 9, 7), LocalDate.of(2027, 1, 24)))
        assertEquals(1, Terms.weeksBetween(LocalDate.of(2026, 9, 7), LocalDate.of(2026, 9, 7)))
        // 周一到周二还是一周，跨到下一周才是第二周。
        assertEquals(1, Terms.weeksBetween(LocalDate.of(2026, 9, 7), LocalDate.of(2026, 9, 8)))
        assertEquals(2, Terms.weeksBetween(LocalDate.of(2026, 9, 7), LocalDate.of(2026, 9, 14)))
    }

    @Test
    fun `phase follows today against the term range`() {
        val item = term()
        assertEquals(Terms.Phase.Upcoming, Terms.phase(item, LocalDate.of(2026, 8, 1)))
        assertEquals(Terms.Phase.Active, Terms.phase(item, LocalDate.of(2026, 10, 1)))
        assertEquals(Terms.Phase.Finished, Terms.phase(item, LocalDate.of(2027, 3, 1)))
    }

    @Test
    fun `active term picks the latest started when ranges overlap`() {
        val main = term(id = "a", name = "主学期")
        val exchange = term(
            id = "b",
            name = "交换学期",
            start = "2026-10-05",
            end = "2026-12-20",
            monday = "2026-09-28",
        )
        assertEquals(
            exchange,
            Terms.activeAt(listOf(main, exchange), LocalDate.of(2026, 11, 1)),
        )
        assertNull(Terms.activeAt(listOf(main), LocalDate.of(2027, 5, 1)))
    }

    @Test
    fun `week number counts from the first week monday`() {
        val item = term()
        assertEquals(1, Terms.weekNumber(item, LocalDate.of(2026, 9, 7)))
        assertEquals(2, Terms.weekNumber(item, LocalDate.of(2026, 9, 14)))
        assertEquals(5, Terms.weekNumber(item, LocalDate.of(2026, 10, 7)))
        assertNull(Terms.weekNumber(item, LocalDate.of(2026, 9, 1)))
    }

    @Test
    fun `duplicate name is rejected ignoring case and spaces`() {
        val existing = listOf(term(name = "2026-2027 秋季学期"))
        assertEquals(
            "已有同名学期「2026-2027 秋季学期」，直接编辑那一个就行。",
            Terms.duplicateError(
                existing,
                editingId = null,
                name = "  2026-2027 秋季学期 ",
                firstWeekMonday = LocalDate.of(2027, 2, 22),
                weeks = 20,
            ),
        )
    }

    @Test
    fun `editing a term does not collide with itself`() {
        val existing = term(id = "t1", name = "本赛季")
        assertNull(
            Terms.duplicateError(
                listOf(existing),
                editingId = "t1",
                name = "本赛季",
                firstWeekMonday = LocalDate.of(2026, 9, 7),
                weeks = 20,
            ),
        )
    }

    @Test
    fun `identical week span under a different name is rejected`() {
        val existing = listOf(term(name = "秋季学期"))
        assertEquals(
            "「秋季学期」的起止周和这里完全一样，可能是重复创建。",
            Terms.duplicateError(
                existing,
                editingId = null,
                name = "2026 秋",
                firstWeekMonday = LocalDate.of(2026, 9, 7),
                weeks = 20,
            ),
        )
    }

    @Test
    fun `overlapping ranges are reported but never blocked`() {
        val main = term(id = "a", name = "主学期")
        assertNull(
            Terms.duplicateError(
                listOf(main),
                editingId = null,
                name = "交换学期",
                firstWeekMonday = LocalDate.of(2026, 9, 28),
                weeks = 12,
            ),
        )
        assertEquals(
            main,
            Terms.overlapping(
                listOf(main),
                editingId = null,
                firstWeekMonday = LocalDate.of(2026, 9, 28),
                weeks = 12,
            ),
        )
    }
}
