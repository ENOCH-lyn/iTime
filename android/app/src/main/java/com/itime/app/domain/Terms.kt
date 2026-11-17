package com.itime.app.domain

import com.itime.app.core.Times
import com.itime.app.data.local.TermEntity
import java.time.LocalDate
import java.time.temporal.ChronoUnit

/**
 * 学期上的纯计算：起止周、当前周、查重提示。
 *
 * 界面上用的周次口径必须和课程周次一致（都从第一周周一数起），
 * 所以这些换算集中在这里，测试也只测这一份规则。
 */
object Terms {

    /** 学期相对“今天”的相位，用于列表上的状态标签。 */
    enum class Phase { Upcoming, Active, Finished }

    /** 第一周周一 + 周数 = 最后一天（含）。 */
    fun endDate(firstWeekMonday: LocalDate, weeks: Int): LocalDate =
        firstWeekMonday.plusWeeks(weeks.coerceAtLeast(1).toLong()).minusDays(1)

    /** 起止日期折回周数；不足一周按一周算。 */
    fun weeksBetween(start: LocalDate, end: LocalDate): Int {
        if (end < start) return 1
        val days = ChronoUnit.DAYS.between(start, end) + 1
        return ((days + 6) / 7).toInt().coerceAtLeast(1)
    }

    fun phase(term: TermEntity, today: LocalDate): Phase {
        val start = Times.parseDate(term.startDate) ?: return Phase.Active
        val end = Times.parseDate(term.endDate) ?: return Phase.Active
        return when {
            today < start -> Phase.Upcoming
            today > end -> Phase.Finished
            else -> Phase.Active
        }
    }

    /** 今天落在哪个学期里；有多个（例如交换学期）时取最近开始的。 */
    fun activeAt(terms: List<TermEntity>, today: LocalDate): TermEntity? =
        terms.filter { phase(it, today) == Phase.Active }
            .maxByOrNull { Times.parseDate(it.firstWeekMonday) ?: LocalDate.MIN }

    /** 今天在这个学期里是第几周；学期开始前返回 null。 */
    fun weekNumber(term: TermEntity, date: LocalDate): Int? {
        val first = Times.parseDate(term.firstWeekMonday) ?: return null
        if (date < first) return null
        return (ChronoUnit.DAYS.between(first, date) / 7 + 1).toInt()
    }

    /**
     * 保存学期前的查重。返回提示文案，null 表示可以保存。
     *
     * 只拦明显重复：同名（忽略大小写与首尾空格），或起止周完全一样。
     * 日期范围重叠不算错——交换学期、小学期本来就会和主学期重叠。
     */
    fun duplicateError(
        terms: List<TermEntity>,
        editingId: String?,
        name: String,
        firstWeekMonday: LocalDate,
        weeks: Int,
    ): String? {
        val trimmed = name.trim()
        val others = terms.filter { it.id != editingId }
        val sameName = others.firstOrNull { it.name.trim().equals(trimmed, ignoreCase = true) }
        if (sameName != null) {
            return "已有同名学期「${sameName.name}」，直接编辑那一个就行。"
        }
        val end = endDate(firstWeekMonday, weeks)
        val sameSpan = others.firstOrNull { other ->
            Times.parseDate(other.firstWeekMonday) == firstWeekMonday &&
                Times.parseDate(other.endDate) == end
        }
        if (sameSpan != null) {
            return "「${sameSpan.name}」的起止周和这里完全一样，可能是重复创建。"
        }
        return null
    }

    /** 和已有学期的日期有重叠时返回那个学期，仅用于提示，不阻止保存。 */
    fun overlapping(
        terms: List<TermEntity>,
        editingId: String?,
        firstWeekMonday: LocalDate,
        weeks: Int,
    ): TermEntity? {
        val start = firstWeekMonday
        val end = endDate(firstWeekMonday, weeks)
        return terms.firstOrNull { other ->
            if (other.id == editingId) return@firstOrNull false
            val otherStart = Times.parseDate(other.startDate) ?: return@firstOrNull false
            val otherEnd = Times.parseDate(other.endDate) ?: return@firstOrNull false
            otherStart <= end && otherEnd >= start
        }
    }
}
