package com.itime.app.ui.screens.calendar

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.outlined.CalendarToday
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.DatePicker
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TimePicker
import androidx.compose.material3.rememberDatePickerState
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.material3.rememberTimePickerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.itime.app.data.local.TermEntity
import com.itime.app.data.repository.CalendarRepository
import com.itime.app.domain.RecurrenceSpec
import com.itime.app.domain.SeriesDraft
import com.itime.app.domain.TermPeriods
import com.itime.app.domain.Terms
import com.itime.app.domain.WeekNumbers
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

private val weekdayNames = listOf(1 to "一", 2 to "二", 3 to "三", 4 to "四", 5 to "五", 6 to "六", 7 to "日")

/** 课程取学期前 N 周作为默认周次。 */
private fun fullWeeks(term: TermEntity?): String {
    if (term == null) return ""
    val start = com.itime.app.core.Times.parseDate(term.startDate) ?: return ""
    val end = com.itime.app.core.Times.parseDate(term.endDate) ?: return ""
    val weeks = Terms.weeksBetween(start, end)
    return if (weeks <= 0) "" else "1-$weeks"
}

/**
 * Creates a course or fixed calendar series.
 *
 * 课程按 Web 端的语义组织：必须选学期、按学期周次展开、时间可以按节次（用学期
 * 节次表）或按具体时刻；固定日程仍是最简单的「日期 + 时刻 + 重复」。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SeriesEditorSheet(
    repository: CalendarRepository,
    initialDate: LocalDate,
    initial: SeriesDraft? = null,
    existingId: String? = null,
    onDismiss: () -> Unit,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val scope = rememberCoroutineScope()
    val terms by repository.observeTerms().collectAsStateWithLifecycle(emptyList())
    val allPeriods by repository.observeTermPeriods().collectAsStateWithLifecycle(emptyList())
    val today = LocalDate.now()

    // 默认就开「课程」：课程是主用法；没有学期时表单里会提示先建学期。
    var kind by remember { mutableStateOf(initial?.kind ?: "course") }
    var name by remember { mutableStateOf(initial?.name ?: "") }
    var teacher by remember { mutableStateOf(initial?.teacher ?: "") }
    var location by remember { mutableStateOf(initial?.location ?: "") }
    var date by remember { mutableStateOf(initial?.startDate ?: initialDate) }
    var startTime by remember { mutableStateOf(initial?.startsAt ?: LocalTime.of(8, 0)) }
    var endTime by remember { mutableStateOf(initial?.endsAt ?: LocalTime.of(9, 40)) }
    var termId by remember { mutableStateOf(initial?.termId.orEmpty()) }
    // 新建课程默认按节次（有节次表时）；编辑已有的按时刻课程则保持按时刻。
    var timeMode by remember {
        mutableStateOf(
            if (initial == null || initial.startPeriodNumber != null) "period" else "clock",
        )
    }
    var startPeriod by remember { mutableIntStateOf(initial?.startPeriodNumber ?: 1) }
    var endPeriod by remember {
        mutableIntStateOf(initial?.endPeriodNumber ?: initial?.startPeriodNumber ?: 1)
    }
    var weekText by remember {
        mutableStateOf(WeekNumbers.format(initial?.weekNumbers ?: emptyList()))
    }
    var weekly by remember { mutableStateOf(initial?.recurrence?.repeats ?: true) }
    var weekdays by remember {
        mutableStateOf(initial?.recurrence?.weekdays ?: listOf(date.dayOfWeek.value))
    }
    var termMenu by remember { mutableStateOf(false) }
    var showDatePicker by remember { mutableStateOf(false) }
    var showStartPicker by remember { mutableStateOf(false) }
    var showEndPicker by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    val effectiveTermId = termId.ifBlank {
        (Terms.activeAt(terms, today) ?: terms.firstOrNull())?.id.orEmpty()
    }
    val selectedTerm = terms.firstOrNull { it.id == effectiveTermId }
    val termPeriods = allPeriods
        .filter { it.termId == effectiveTermId }
        .sortedBy { it.periodNumber }
    val termPeriodsReady = termPeriods.isNotEmpty()
    val usePeriod = kind == "course" && timeMode == "period" && termPeriodsReady
    val startPeriodRow = termPeriods.firstOrNull { it.periodNumber == startPeriod }
    val endPeriodRow = termPeriods.firstOrNull { it.periodNumber == endPeriod }

    // 选好学期后把周次默认填成「整个学期」，用户不用自己数周数。
    LaunchedEffect(kind, effectiveTermId) {
        if (kind == "course" && weekText.isBlank()) {
            weekText = fullWeeks(selectedTerm)
        }
    }

    fun save() {
        if (name.trim().isEmpty()) {
            error = "请填写名称。"
            return
        }
        if (kind == "course") {
            if (effectiveTermId.isBlank()) {
                error = "课程要先选学期；没有学期就去日历 → ⋮ → 学期与节次里建一个。"
                return
            }
            if (weekdays.isEmpty()) {
                error = "选一个上课的星期。"
                return
            }
            val weeks = WeekNumbers.parse(weekText, maxWeek = 60)
            if (weeks.isEmpty()) {
                error = "课表周次不能为空，例如 1-16。"
                return
            }
            if (usePeriod && (startPeriodRow == null || endPeriodRow == null)) {
                error = "所选节次不在这个学期的节次表里。"
                return
            }
        }
        val periodStart = if (usePeriod) TermPeriods.parseTime(startPeriodRow!!.startsAt) else null
        val periodEnd = if (usePeriod) TermPeriods.parseTime(endPeriodRow!!.endsAt) else null
        val weeks = WeekNumbers.parse(weekText, maxWeek = 60)
        // 课程首个上课日 = 学期第一周周一 + 最早周次偏移 + 最早星期偏移；服务端按这个口径校验。
        val courseStart = selectedTerm?.let { term ->
            com.itime.app.core.Times.parseDate(term.firstWeekMonday)?.plusDays(
                ((weeks.minOrNull() ?: 1) - 1) * 7L + ((weekdays.minOrNull() ?: 1) - 1).toLong(),
            )
        } ?: date
        scope.launch {
            val draft = SeriesDraft(
                kind = kind,
                termId = if (kind == "course") effectiveTermId else null,
                name = name.trim(),
                teacher = teacher.trim(),
                location = location.trim(),
                description = initial?.description ?: "",
                startDate = courseStart,
                startsAt = periodStart ?: startTime,
                endsAt = periodEnd ?: endTime,
                startPeriodNumber = if (usePeriod) startPeriod else null,
                endPeriodNumber = if (usePeriod) endPeriod else null,
                recurrence = if (kind == "course") {
                    RecurrenceSpec(frequency = "weekly", interval = 1, weekdays = weekdays.sorted())
                } else if (weekly) {
                    RecurrenceSpec(frequency = "weekly", weekdays = weekdays.sorted())
                } else {
                    RecurrenceSpec(frequency = "none")
                },
                weekNumbers = if (kind == "course") weeks else emptyList(),
            )
            runCatching {
                if (existingId == null) {
                    repository.createSeries(draft)
                } else {
                    repository.updateSeries(existingId, draft)
                }
            }.onSuccess {
                onDismiss()
            }.onFailure {
                error = it.message ?: "保存失败，请稍后重试。"
            }
        }
    }

    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheetState) {
        Column(
            Modifier
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .imePadding()
                .navigationBarsPadding()
                .padding(horizontal = 20.dp)
                .padding(bottom = 24.dp),
        ) {
            Text(
                if (existingId == null) "新建日程" else "编辑日程",
                style = MaterialTheme.typography.titleMedium,
            )
            Spacer(Modifier.height(8.dp))
            SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
                listOf("course" to "课程", "fixed" to "日程").forEachIndexed { index, (value, label) ->
                    SegmentedButton(
                        selected = kind == value,
                        onClick = {
                            kind = value
                            if (value == "course" && timeMode == "clock" && termPeriodsReady) {
                                timeMode = "period"
                            }
                        },
                        shape = SegmentedButtonDefaults.itemShape(index, 2),
                    ) { Text(label) }
                }
            }
            Spacer(Modifier.height(12.dp))
            OutlinedTextField(
                value = name,
                onValueChange = { name = it },
                label = { Text(if (kind == "course") "课程名" else "日程名称") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(12.dp))
            OutlinedTextField(
                value = location,
                onValueChange = { location = it },
                label = { Text("地点") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            if (kind == "course") {
                Spacer(Modifier.height(12.dp))
                OutlinedTextField(
                    value = teacher,
                    onValueChange = { teacher = it },
                    label = { Text("教师") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(12.dp))
                Text("学期", style = MaterialTheme.typography.labelLarge)
                Spacer(Modifier.height(4.dp))
                if (terms.isEmpty()) {
                    Text(
                        "还没有学期。课程要挂在学期上：日历 → ⋮ → 学期与节次里先建一个。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                    )
                } else {
                    Box {
                        OutlinedButton(
                            onClick = { termMenu = true },
                            modifier = Modifier.fillMaxWidth(),
                        ) {
                            Text(
                                selectedTerm?.name ?: "选择学期",
                                modifier = Modifier.weight(1f),
                            )
                            Icon(Icons.Filled.ArrowDropDown, contentDescription = null)
                        }
                        DropdownMenu(
                            expanded = termMenu,
                            onDismissRequest = { termMenu = false },
                        ) {
                            terms.forEach { term ->
                                DropdownMenuItem(
                                    text = { Text(term.name) },
                                    onClick = {
                                        termId = term.id
                                        termMenu = false
                                        weekText = fullWeeks(term)
                                        timeMode = if (
                                            allPeriods.any { it.termId == term.id }
                                        ) {
                                            "period"
                                        } else {
                                            "clock"
                                        }
                                    },
                                )
                            }
                        }
                    }
                    if (weekText.isBlank()) {
                        Text(
                            "周次默认取整个学期（第 1 到最后一���），可以改成 1-16,18 这种写法。",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        TextButton(onClick = { weekText = fullWeeks(selectedTerm) }) {
                            Text("取整个学期：${fullWeeks(selectedTerm).ifBlank { "—" }}")
                        }
                    }
                }
                Spacer(Modifier.height(12.dp))
                OutlinedTextField(
                    value = weekText,
                    onValueChange = { weekText = it },
                    label = { Text("周次（如 1-8,10-16）") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(12.dp))
                Text("上课星期", style = MaterialTheme.typography.labelLarge)
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    weekdayNames.forEach { (value, label) ->
                        FilterChip(
                            selected = value in weekdays,
                            onClick = {
                                weekdays = if (value in weekdays) weekdays - value else weekdays + value
                            },
                            label = { Text(label) },
                        )
                    }
                }
                Spacer(Modifier.height(12.dp))
                Text("时间填写方式", style = MaterialTheme.typography.labelLarge)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilterChip(
                        selected = usePeriod,
                        onClick = { if (termPeriodsReady) timeMode = "period" },
                        enabled = termPeriodsReady,
                        label = { Text("按节次") },
                    )
                    FilterChip(
                        selected = !usePeriod,
                        onClick = { timeMode = "clock" },
                        label = { Text("按时刻") },
                    )
                }
                if (!termPeriodsReady) {
                    Text(
                        "这个学期还没设节次表，只能在学期与节次里补。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            } else {
                Spacer(Modifier.height(12.dp))
                OutlinedTextField(
                    value = date.format(DateTimeFormatter.ofPattern("yyyy 年 M 月 d 日")),
                    onValueChange = {},
                    readOnly = true,
                    label = { Text("开始日期") },
                    trailingIcon = {
                        IconButton(onClick = { showDatePicker = true }) {
                            Icon(Icons.Outlined.CalendarToday, contentDescription = "选择日期")
                        }
                    },
                    modifier = Modifier.fillMaxWidth(),
                )
            }
            if (usePeriod) {
                Spacer(Modifier.height(8.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    PeriodPicker(
                        label = "开始节次",
                        periods = termPeriods.map { it.periodNumber },
                        selected = startPeriod,
                        modifier = Modifier.weight(1f),
                        onSelect = {
                            startPeriod = it
                            if (endPeriod < it) endPeriod = it
                        },
                    )
                    PeriodPicker(
                        label = "结束节次",
                        periods = termPeriods.map { it.periodNumber },
                        selected = endPeriod,
                        modifier = Modifier.weight(1f),
                        onSelect = { if (it >= startPeriod) endPeriod = it },
                    )
                }
                Text(
                    buildString {
                        append("时间取节次表：")
                        append(startPeriodRow?.let { "${TermPeriods.format(java.time.LocalTime.parse(it.startsAt))} – " } ?: "")
                        append(endPeriodRow?.let { TermPeriods.format(java.time.LocalTime.parse(it.endsAt)) } ?: "")
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            } else {
                Spacer(Modifier.height(12.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    OutlinedTextField(
                        value = startTime.format(DateTimeFormatter.ofPattern("HH:mm")),
                        onValueChange = {},
                        readOnly = true,
                        label = { Text("开始") },
                        trailingIcon = {
                            IconButton(onClick = { showStartPicker = true }) {
                                Icon(Icons.Outlined.Schedule, contentDescription = "开始时间")
                            }
                        },
                        modifier = Modifier.weight(1f),
                    )
                    OutlinedTextField(
                        value = endTime.format(DateTimeFormatter.ofPattern("HH:mm")),
                        onValueChange = {},
                        readOnly = true,
                        label = { Text("结束") },
                        trailingIcon = {
                            IconButton(onClick = { showEndPicker = true }) {
                                Icon(Icons.Outlined.Schedule, contentDescription = "结束时间")
                            }
                        },
                        modifier = Modifier.weight(1f),
                    )
                }
                if (kind == "fixed") {
                    Spacer(Modifier.height(12.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        FilterChip(
                            selected = !weekly,
                            onClick = { weekly = false },
                            label = { Text("仅一次") },
                        )
                        FilterChip(
                            selected = weekly,
                            onClick = { weekly = true },
                            label = { Text("每周重复") },
                        )
                    }
                }
            }
            if (kind == "fixed" && weekly) {
                Spacer(Modifier.height(8.dp))
                Text("重复星期", style = MaterialTheme.typography.labelLarge)
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    weekdayNames.forEach { (value, label) ->
                        FilterChip(
                            selected = value in weekdays,
                            onClick = {
                                weekdays = if (value in weekdays) weekdays - value else weekdays + value
                            },
                            label = { Text(label) },
                        )
                    }
                }
            }
            if (error != null) {
                Spacer(Modifier.height(8.dp))
                Text(
                    error!!,
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            Spacer(Modifier.height(20.dp))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                TextButton(onClick = onDismiss) { Text("取消") }
                Spacer(Modifier.width(8.dp))
                Button(onClick = { save() }) { Text("保存") }
            }
        }
    }

    if (showDatePicker) {
        val state = rememberDatePickerState(
            initialSelectedDateMillis = date.atStartOfDay(ZoneId.of("UTC")).toInstant().toEpochMilli(),
        )
        DatePickerDialog(
            onDismissRequest = { showDatePicker = false },
            confirmButton = {
                TextButton(onClick = {
                    state.selectedDateMillis?.let {
                        date = Instant.ofEpochMilli(it).atZone(ZoneId.of("UTC")).toLocalDate()
                    }
                    showDatePicker = false
                }) { Text("确定") }
            },
            dismissButton = {
                TextButton(onClick = { showDatePicker = false }) { Text("取消") }
            },
        ) { DatePicker(state = state) }
    }
    if (showStartPicker) {
        val state = rememberTimePickerState(startTime.hour, startTime.minute, true)
        AlertDialog(
            onDismissRequest = { showStartPicker = false },
            title = { Text("开始时间") },
            text = { TimePicker(state = state) },
            confirmButton = {
                TextButton(onClick = {
                    startTime = LocalTime.of(state.hour, state.minute)
                    showStartPicker = false
                }) { Text("确定") }
            },
            dismissButton = {
                TextButton(onClick = { showStartPicker = false }) { Text("取消") }
            },
        )
    }
    if (showEndPicker) {
        val state = rememberTimePickerState(endTime.hour, endTime.minute, true)
        AlertDialog(
            onDismissRequest = { showEndPicker = false },
            title = { Text("结束时间") },
            text = { TimePicker(state = state) },
            confirmButton = {
                TextButton(onClick = {
                    endTime = LocalTime.of(state.hour, state.minute)
                    showEndPicker = false
                }) { Text("确定") }
            },
            dismissButton = {
                TextButton(onClick = { showEndPicker = false }) { Text("取消") }
            },
        )
    }
}

/** 节次下拉：显示「第 N 节」并带上节次时间。 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun PeriodPicker(
    label: String,
    periods: List<Int>,
    selected: Int,
    modifier: Modifier = Modifier,
    onSelect: (Int) -> Unit,
) {
    var open by remember { mutableStateOf(false) }
    Box(modifier) {
        OutlinedButton(onClick = { open = true }, modifier = Modifier.fillMaxWidth()) {
            Text("$label 第 $selected 节", modifier = Modifier.weight(1f))
            Icon(Icons.Filled.ArrowDropDown, contentDescription = null)
        }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            periods.forEach { number ->
                DropdownMenuItem(
                    text = { Text("第 $number 节") },
                    onClick = {
                        onSelect(number)
                        open = false
                    },
                )
            }
        }
    }
}
