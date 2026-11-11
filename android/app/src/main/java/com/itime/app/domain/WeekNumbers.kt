package com.itime.app.domain

/**
 * Parses and formats course week selections such as "1-8,10-16".
 * Pure helpers so the editor and the tests share the same rules.
 */
object WeekNumbers {

    /** "1-8,10-16" -> [1..8, 10..16]; invalid or out-of-range tokens are dropped. */
    fun parse(text: String, maxWeek: Int = 30): List<Int> {
        val weeks = sortedSetOf<Int>()
        text.split(',', '，', ';', '；').forEach { raw ->
            val token = raw.trim()
            if (token.isEmpty()) return@forEach
            val parts = token.split('-', '–', '~').map { it.trim() }
            when {
                parts.size == 1 -> parts[0].toIntOrNull()?.let { week ->
                    if (week in 1..maxWeek) weeks += week
                }
                parts.size == 2 -> {
                    val start = parts[0].toIntOrNull()
                    val end = parts[1].toIntOrNull()
                    if (start != null && end != null && start <= end) {
                        for (week in start..end) {
                            if (week in 1..maxWeek) weeks += week
                        }
                    }
                }
            }
        }
        return weeks.toList()
    }

    /** [1..8, 10..16] -> "1-8,10-16". */
    fun format(weeks: List<Int>): String {
        val sorted = weeks.distinct().sorted()
        if (sorted.isEmpty()) return ""
        val parts = mutableListOf<String>()
        var start = sorted.first()
        var previous = start
        for (week in sorted.drop(1)) {
            if (week == previous + 1) {
                previous = week
                continue
            }
            parts += if (start == previous) "$start" else "$start-$previous"
            start = week
            previous = week
        }
        parts += if (start == previous) "$start" else "$start-$previous"
        return parts.joinToString(",")
    }
}
