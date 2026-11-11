package com.itime.app.ui.screens.calendar

import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.material.icons.outlined.CalendarToday
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material3.Button
import androidx.compose.material3.DatePicker
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
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
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.itime.app.data.repository.CalendarRepository
import com.itime.app.domain.RecurrenceSpec
import com.itime.app.domain.SeriesDraft
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/**
 * Creates a course or fixed calendar series.  Weekly recurrence is the common
 * case on phones, so the sheet exposes a simple 每周 + 星期 selector instead of
 * the full rule editor available on the Web client.
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
    var kind by remember { mutableStateOf(initial?.kind ?: "fixed") }
    var name by remember { mutableStateOf(initial?.name ?: "") }
    var teacher by remember { mutableStateOf(initial?.teacher ?: "") }
    var location by remember { mutableStateOf(initial?.location ?: "") }
    var date by remember { mutableStateOf(initial?.startDate ?: initialDate) }
    var startTime by remember { mutableStateOf(initial?.startsAt ?: LocalTime.of(8, 0)) }
    var endTime by remember { mutableStateOf(initial?.endsAt ?: LocalTime.of(9, 40)) }
    var weekly by remember { mutableStateOf(initial?.recurrence?.repeats ?: true) }
    var weekdays by remember {
        mutableStateOf(initial?.recurrence?.weekdays ?: listOf(date.dayOfWeek.value))
    }
    var showDatePicker by remember { mutableStateOf(false) }
    var showStartPicker by remember { mutableStateOf(false) }
    var showEndPicker by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    fun save() {
        if (name.trim().isEmpty()) {
            error = "请填写名称。"
            return
        }
        scope.launch {
            val draft = SeriesDraft(
                kind = kind,
                termId = initial?.termId,
                name = name.trim(),
                teacher = teacher.trim(),
                location = location.trim(),
                description = initial?.description ?: "",
                startDate = date,
                startsAt = startTime,
                endsAt = endTime,
                recurrence = if (weekly) {
                    RecurrenceSpec(frequency = "weekly", weekdays = weekdays.sorted())
                } else {
                    RecurrenceSpec(frequency = "none")
                },
                weekNumbers = initial?.weekNumbers ?: emptyList(),
            )
            if (existingId == null) {
                repository.createSeries(draft)
            } else {
                repository.updateSeries(existingId, draft)
            }
            onDismiss()
        }
    }

    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheetState) {
        Column(
            Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .imePadding()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 24.dp)
                .padding(bottom = 24.dp),
        ) {
            Text(if (existingId == null) "新建日程" else "编辑日程", style = MaterialTheme.typography.titleLarge)
            Spacer(Modifier.height(12.dp))
            SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
                SegmentedButton(
                    selected = kind == "fixed",
                    onClick = { kind = "fixed" },
                    shape = SegmentedButtonDefaults.itemShape(0, 2),
                ) { Text("固定安排") }
                SegmentedButton(
                    selected = kind == "course",
                    onClick = { kind = "course" },
                    shape = SegmentedButtonDefaults.itemShape(1, 2),
                ) { Text("课程") }
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
            }
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
            if (weekly) {
                Spacer(Modifier.height(8.dp))
                Text("重复星期", style = MaterialTheme.typography.labelLarge)
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    listOf(
                        1 to "一", 2 to "二", 3 to "三", 4 to "四",
                        5 to "五", 6 to "六", 7 to "日",
                    ).forEach { (value, label) ->
                        FilterChip(
                            selected = value in weekdays,
                            onClick = {
                                weekdays = if (value in weekdays) {
                                    weekdays - value
                                } else {
                                    weekdays + value
                                }
                            },
                            label = { Text(label) },
                        )
                    }
                }
            }
            if (error != null) {
                Spacer(Modifier.height(8.dp))
                Text(error!!, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
            }
            Spacer(Modifier.height(20.dp))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                TextButton(onClick = onDismiss) { Text("取消") }
                Spacer(Modifier.width(8.dp))
                Button(onClick = ::save) { Text("保存") }
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
        androidx.compose.material3.AlertDialog(
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
        androidx.compose.material3.AlertDialog(
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
