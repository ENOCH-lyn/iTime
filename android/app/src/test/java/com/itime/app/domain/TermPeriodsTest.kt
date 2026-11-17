package com.itime.app.domain

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.LocalTime

class TermPeriodsTest {

    @Test
    fun `defaults match the web ten period table`() {
        val defaults = TermPeriods.defaults()
        assertEquals(10, defaults.size)
        assertEquals(1, defaults.first().periodNumber)
        assertEquals("第1节", defaults.first().name)
        assertEquals(LocalTime.of(8, 30), defaults.first().startsAt)
        assertEquals(LocalTime.of(9, 15), defaults.first().endsAt)
        assertEquals(LocalTime.of(20, 40), defaults.last().endsAt)
        assertNull(TermPeriods.validate(defaults))
    }

    @Test
    fun `wire format is HH mm ss`() {
        assertEquals("08:30:00", TermPeriods.toWire(LocalTime.of(8, 30)))
        assertEquals("19:05:00", TermPeriods.toWire(LocalTime.of(19, 5)))
        assertEquals(LocalTime.of(8, 30), TermPeriods.parseTime("08:30:00"))
        assertEquals(LocalTime.of(19, 5), TermPeriods.parseTime("19:05"))
    }

    @Test
    fun `adjacent periods are allowed`() {
        val rows = listOf(
            PeriodInput(1, "第1节", LocalTime.of(8, 0), LocalTime.of(8, 45)),
            PeriodInput(2, "第2节", LocalTime.of(8, 45), LocalTime.of(9, 30)),
        )
        assertNull(TermPeriods.validate(rows))
    }

    @Test
    fun `overlapping periods are rejected`() {
        val rows = listOf(
            PeriodInput(1, "第1节", LocalTime.of(8, 0), LocalTime.of(8, 50)),
            PeriodInput(2, "第2节", LocalTime.of(8, 45), LocalTime.of(9, 30)),
        )
        assertEquals("「第1节」和「第2节」的时间重叠了。", TermPeriods.validate(rows))
    }

    @Test
    fun `end must be after start`() {
        assertEquals(
            "「第1节」的结束时间要晚于开始时间。",
            TermPeriods.validate(listOf(PeriodInput(1, "第1节", LocalTime.of(9, 0), LocalTime.of(9, 0)))),
        )
    }

    @Test
    fun `numbers and names must be unique`() {
        assertEquals(
            "节次编号 1 重复了。",
            TermPeriods.validate(
                listOf(
                    PeriodInput(1, "第1节", LocalTime.of(8, 0), LocalTime.of(8, 45)),
                    PeriodInput(1, "第2节", LocalTime.of(8, 45), LocalTime.of(9, 30)),
                ),
            ),
        )
        assertEquals(
            "节次名称「第1节」和编号 1 重复了。",
            TermPeriods.validate(
                listOf(
                    PeriodInput(1, "第1节", LocalTime.of(8, 0), LocalTime.of(8, 45)),
                    PeriodInput(2, " 第1节 ", LocalTime.of(8, 45), LocalTime.of(9, 30)),
                ),
            ),
        )
    }

    @Test
    fun `blank name and too many periods are rejected`() {
        assertEquals(
            "第 1 个节次还没有名称。",
            TermPeriods.validate(listOf(PeriodInput(1, "  ", LocalTime.of(8, 0), LocalTime.of(8, 45)))),
        )
        val many = (1..31).map {
            PeriodInput(it, "第${it}节", LocalTime.of(8, 0), LocalTime.of(8, 45))
        }
        assertEquals("一个学期最多设置 30 个节次。", TermPeriods.validate(many))
    }

    @Test
    fun `empty table is valid`() {
        assertNull(TermPeriods.validate(emptyList()))
    }
}
