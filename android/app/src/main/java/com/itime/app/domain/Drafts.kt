package com.itime.app.domain

import java.time.LocalDate
import java.time.LocalTime

/** Recurrence rule captured by the editor, translated to the server shape on save. */
data class RecurrenceSpec(
    val frequency: String = "none",
    val interval: Int = 1,
    val weekdays: List<Int> = emptyList(),
    val until: LocalDate? = null,
    val count: Int? = null,
) {
    val repeats: Boolean get() = frequency != "none"
}

data class TaskDraft(
    val title: String = "",
    val description: String = "",
    val projectId: String? = null,
    val estimatedMinutes: Int? = null,
    val priority: Int = 0,
    val labels: List<String> = emptyList(),
    val steps: List<String> = emptyList(),
    val dueDate: LocalDate? = null,
    val dueTime: LocalTime? = null,
    val recurrence: RecurrenceSpec = RecurrenceSpec(),
)

data class SeriesDraft(
    val kind: String = "fixed",
    val termId: String? = null,
    val name: String = "",
    val teacher: String = "",
    val location: String = "",
    val description: String = "",
    val busy: Boolean = true,
    val startDate: LocalDate,
    val startsAt: LocalTime,
    val endsAt: LocalTime,
    val endDayOffset: Int = 0,
    val startPeriodNumber: Int? = null,
    val endPeriodNumber: Int? = null,
    val recurrence: RecurrenceSpec = RecurrenceSpec(frequency = "weekly"),
) {
    val term: Boolean get() = kind == "course"
}
