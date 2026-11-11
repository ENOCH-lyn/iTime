package com.itime.app.domain

import org.junit.Assert.assertEquals
import org.junit.Test

class WeekNumbersTest {

    @Test
    fun `parses ranges and single weeks`() {
        assertEquals((1..8).toList() + (10..16).toList(), WeekNumbers.parse("1-8,10-16"))
        assertEquals(listOf(3, 5, 7), WeekNumbers.parse("3, 5，7"))
    }

    @Test
    fun `drops invalid tokens and merges duplicates`() {
        assertEquals(listOf(1, 2, 3), WeekNumbers.parse("1,2,3,abc,0,99"))
        assertEquals(listOf(2, 3), WeekNumbers.parse("3,2-3,2"))
        assertEquals(emptyList<Int>(), WeekNumbers.parse("  "))
    }

    @Test
    fun `respects the max week bound`() {
        assertEquals((1..20).toList(), WeekNumbers.parse("1-30", maxWeek = 20))
    }

    @Test
    fun `formats contiguous weeks back into ranges`() {
        assertEquals("1-8,10-16", WeekNumbers.format((1..8).toList() + (10..16).toList()))
        assertEquals("3,5,7", WeekNumbers.format(listOf(7, 3, 5)))
        assertEquals("", WeekNumbers.format(emptyList()))
        assertEquals("1", WeekNumbers.format(listOf(1)))
    }

    @Test
    fun `format and parse round trip`() {
        val weeks = listOf(1, 2, 3, 5, 8, 9, 10)
        assertEquals(weeks, WeekNumbers.parse(WeekNumbers.format(weeks)))
    }
}
