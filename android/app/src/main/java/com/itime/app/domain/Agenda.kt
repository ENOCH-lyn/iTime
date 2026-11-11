package com.itime.app.domain

import com.itime.app.core.Times
import com.itime.app.data.local.AdditionEntity
import com.itime.app.data.local.ExceptionEntity
import com.itime.app.data.local.OccurrenceEntity
import com.itime.app.data.local.SeriesEntity
import com.itime.app.data.local.TaskEntity
import com.itime.app.data.local.TermEntity
import java.time.DayOfWeek
import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneId
import java.time.temporal.ChronoUnit
import kotlin.math.abs

/** One rendered entry in the agenda / calendar grid. */
data class AgendaItem(
    val id: String,
    val sourceKind: String,
    val sourceId: String,
    val title: String,
    val subtitle: String,
    val location: String,
    val start: Instant,
    val end: Instant,
    val date: LocalDate,
    val startTime: LocalTime?,
    val endTime: LocalTime?,
    val allDay: Boolean,
    val completed: Boolean,
    val occurrenceKey: String?,
    val colorSeed: Int,
    val busy: Boolean = true,
)

/**
 * Offline recurrence expansion.
 *
 * The Web client lets the server compute occurrences; the App must also work
 * with no network, so the same rules are applied locally: recurrence rules,
 * course week numbers, single-occurrence exceptions/addition and repeating
 * task occurrence state.
 */
object Agenda {

    fun build(
        from: LocalDate,
        to: LocalDate,
        zone: ZoneId,
        series: List<SeriesEntity>,
        terms: Map<String, TermEntity>,
        exceptions: List<ExceptionEntity>,
        additions: List<AdditionEntity>,
        tasks: List<TaskEntity>,
        occurrences: List<OccurrenceEntity>,
        includeCompletedTasks: Boolean = false,
    ): List<AgendaItem> {
        val items = ArrayList<AgendaItem>()
        items += seriesItems(from, to, zone, series, terms, exceptions, additions)
        items += taskItems(from, to, zone, tasks, occurrences, includeCompletedTasks)
        return items.sortedWith(compareBy({ it.start }, { it.title }))
    }

    // ------------------------------------------------------------- series

    private fun seriesItems(
        from: LocalDate,
        to: LocalDate,
        zone: ZoneId,
        series: List<SeriesEntity>,
        terms: Map<String, TermEntity>,
        exceptions: List<ExceptionEntity>,
        additions: List<AdditionEntity>,
    ): List<AgendaItem> {
        val byException = exceptions.associateBy { it.seriesId to it.originalStartDate }
        val byAddition = additions.groupBy { it.seriesId }
        val result = ArrayList<AgendaItem>()

        for (item in series) {
            if (item.lifecycle != "active") continue
            val pattern = item.pattern()
            val seriesZone = Times.zoneOf(item.timeZone)
            val startTime = Times.parseTime(item.startsAt) ?: LocalTime.of(8, 0)
            val endTime = Times.parseTime(item.endsAt) ?: startTime.plusHours(1)
            val term = item.termId?.let(terms::get)

            for (date in expand(pattern, from.minusDays(1), to.plusDays(1))) {
                if (!pattern.allowsWeek(date, term)) continue
                val exception = byException[item.id to date.toString()]
                if (exception != null && exception.action == "cancelled") continue

                val localDate = exception?.movedStartDate?.let(Times::parseDate) ?: date
                val localStart = exception?.movedStartsAt?.let(Times::parseTime) ?: startTime
                val localEnd = exception?.movedEndsAt?.let(Times::parseTime) ?: endTime
                val endOffset = exception?.movedEndDayOffset ?: item.endDayOffset
                val zoneForItem = exception?.movedTimeZone?.let(Times::zoneOf) ?: seriesZone

                if (localDate < from || localDate > to) continue
                result += AgendaItem(
                    id = "series:${item.id}:$date",
                    sourceKind = "series",
                    sourceId = item.id,
                    title = exception?.overrideName ?: item.name,
                    subtitle = buildString {
                        if (item.teacher.isNotBlank()) append(item.teacher)
                        if (item.isCourse) {
                            if (isNotEmpty()) append(" · ")
                            append("课程")
                        }
                    },
                    location = exception?.overrideLocation ?: item.location,
                    start = Times.combine(localDate, localStart, zoneForItem),
                    end = Times.combine(localDate.plusDays(endOffset.toLong()), localEnd, zoneForItem),
                    date = localDate,
                    startTime = localStart,
                    endTime = localEnd,
                    allDay = false,
                    completed = false,
                    occurrenceKey = "$date" + "T" + localStart,
                    colorSeed = item.id.hashCode(),
                    busy = exception?.overrideBusy ?: item.busy,
                )
            }

            for (addition in byAddition[item.id].orEmpty()) {
                if (addition.status == "cancelled") continue
                val date = Times.parseDate(addition.localStartDate) ?: continue
                if (date < from || date > to) continue
                val zoneForItem = addition.timeZone?.let(Times::zoneOf) ?: seriesZone
                val addStart = addition.startsAt?.let(Times::parseTime) ?: startTime
                val addEnd = addition.endsAt?.let(Times::parseTime) ?: endTime
                result += AgendaItem(
                    id = "series:${item.id}:add:${addition.id}",
                    sourceKind = "series",
                    sourceId = item.id,
                    title = addition.overrideName ?: item.name,
                    subtitle = if (item.teacher.isNotBlank()) item.teacher else "补课",
                    location = addition.overrideLocation ?: item.location,
                    start = Times.combine(date, addStart, zoneForItem),
                    end = Times.combine(date.plusDays(addition.endDayOffset.toLong()), addEnd, zoneForItem),
                    date = date,
                    startTime = addStart,
                    endTime = addEnd,
                    allDay = false,
                    completed = false,
                    occurrenceKey = addition.occurrenceKey,
                    colorSeed = item.id.hashCode(),
                    busy = addition.overrideBusy ?: item.busy,
                )
            }
        }
        return result
    }

    // -------------------------------------------------------------- tasks

    private fun taskItems(
        from: LocalDate,
        to: LocalDate,
        zone: ZoneId,
        tasks: List<TaskEntity>,
        occurrences: List<OccurrenceEntity>,
        includeCompleted: Boolean,
    ): List<AgendaItem> {
        val states = occurrences.groupBy { it.taskId }
        val result = ArrayList<AgendaItem>()
        for (task in tasks) {
            if (task.lifecycle != "active") continue
            val zoneForTask = Times.zoneOf(task.scheduleTz)
            val dueDate = Times.parseDate(task.dueDate) ?: continue
            val dueTime = Times.parseTime(task.dueTime)
            val pattern = task.pattern()
            val durations = task.estimatedMinutes ?: 30

            for (date in expand(pattern, from.minusDays(1), to.plusDays(1))) {
                if (date < from || date > to) continue
                val state = states[task.id].orEmpty().firstOrNull {
                    Times.parseDate(it.originalDate) == date
                }
                if (state?.status == "skipped") continue
                val completed = if (state != null) state.status == "completed" else task.isCompleted
                if (completed && !includeCompleted) continue
                val effectiveDate = state?.effectiveDate?.let(Times::parseDate) ?: date
                val effectiveTime = state?.effectiveTime?.let(Times::parseTime) ?: dueTime
                val start = if (effectiveTime == null) {
                    Times.startOfDay(effectiveDate, zoneForTask)
                } else {
                    Times.combine(effectiveDate, effectiveTime, zoneForTask)
                }
                result += AgendaItem(
                    id = "task:${task.id}:$date",
                    sourceKind = "task",
                    sourceId = task.id,
                    title = task.title,
                    subtitle = task.projectName ?: "",
                    location = "",
                    start = start,
                    end = start.plusSeconds(durations * 60L),
                    date = effectiveDate,
                    startTime = effectiveTime,
                    endTime = effectiveTime?.plusMinutes(durations.toLong()),
                    allDay = effectiveTime == null,
                    completed = completed,
                    occurrenceKey = state?.occurrenceKey ?: date.toString(),
                    colorSeed = task.id.hashCode(),
                    busy = false,
                )
            }
        }
        return result
    }

    // ----------------------------------------------------------- patterns

    data class Pattern(
        val start: LocalDate,
        val frequency: String,
        val interval: Int,
        val weekdays: List<Int>,
        val until: LocalDate?,
        val count: Int?,
        val weekNumbers: List<Int>,
    ) {
        fun allowsWeek(date: LocalDate, term: TermEntity?): Boolean {
            if (weekNumbers.isEmpty()) return true
            val first = term?.let { Times.parseDate(it.firstWeekMonday) } ?: return true
            if (date < first) return false
            val week = (ChronoUnit.DAYS.between(first, date) / 7 + 1).toInt()
            return week in weekNumbers
        }
    }

    private fun SeriesEntity.pattern() = Pattern(
        start = Times.parseDate(startDate) ?: LocalDate.now(),
        frequency = recFrequency,
        interval = recInterval.coerceAtLeast(1),
        weekdays = weekdays,
        until = Times.parseDate(recUntil),
        count = recCount,
        weekNumbers = weekNumbers,
    )

    private fun TaskEntity.pattern() = Pattern(
        start = Times.parseDate(dueDate) ?: LocalDate.now(),
        frequency = recFrequency,
        interval = recInterval.coerceAtLeast(1),
        weekdays = recWeekdaysCsv.split(',').mapNotNull { it.trim().toIntOrNull() },
        until = Times.parseDate(recUntil),
        count = recCount,
        weekNumbers = emptyList(),
    )

    /** Expand a recurrence rule into candidate dates inside the window. */
    fun expand(pattern: Pattern, from: LocalDate, to: LocalDate): List<LocalDate> {
        if (pattern.start > to) return emptyList()
        val end = pattern.until?.takeIf { it < to } ?: to
        val result = ArrayList<LocalDate>()
        if (pattern.frequency == "none") {
            if (pattern.start in from..to) result += pattern.start
            return result
        }
        val weekdays = when {
            pattern.weekdays.isNotEmpty() -> pattern.weekdays
            pattern.frequency == "weekdays" -> listOf(1, 2, 3, 4, 5)
            else -> listOf(pattern.start.dayOfWeek.value)
        }
        val started = pattern.start.coerceAtLeast(from.minusDays(1))
        var date = started
        var produced = 0
        var guard = 0
        while (date <= end && guard++ < 5000) {
            val matches = when (pattern.frequency) {
                "daily" -> {
                    val days = ChronoUnit.DAYS.between(pattern.start, date)
                    days >= 0 && days % pattern.interval == 0L
                }
                "weekdays", "weekly" -> {
                    val weeks = ChronoUnit.DAYS.between(weekStart(pattern.start), weekStart(date)) / 7
                    weeks >= 0 && weeks % pattern.interval == 0L && date.dayOfWeek.value in weekdays
                }
                "monthly" -> {
                    val months = ChronoUnit.MONTHS.between(pattern.start.withDayOfMonth(1), date.withDayOfMonth(1))
                    months >= 0 && months % pattern.interval == 0L && date.dayOfMonth == pattern.start.dayOfMonth
                }
                "yearly" -> {
                    val years = ChronoUnit.YEARS.between(pattern.start, date)
                    years >= 0 && years % pattern.interval == 0L &&
                        date.month == pattern.start.month && date.dayOfMonth == pattern.start.dayOfMonth
                }
                else -> false
            }
            if (matches) {
                produced += 1
                if (pattern.count != null && produced > pattern.count) break
                if (date >= from && date <= to) result += date
            }
            date = date.plusDays(1)
        }
        return result
    }

    private fun weekStart(date: LocalDate): LocalDate =
        date.minusDays((date.dayOfWeek.value - DayOfWeek.MONDAY.value).toLong())
}
