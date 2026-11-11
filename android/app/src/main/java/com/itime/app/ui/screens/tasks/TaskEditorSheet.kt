package com.itime.app.ui.screens.tasks

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.outlined.CalendarToday
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.KeyboardArrowDown
import androidx.compose.material.icons.outlined.KeyboardArrowUp
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material3.Checkbox
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
import androidx.compose.ui.Alignment
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.itime.app.data.local.StepEntity
import com.itime.app.data.repository.TaskRepository
import com.itime.app.domain.TaskDraft
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

private val dateLabel = DateTimeFormatter.ofPattern("M 月 d 日 EEEE")

/**
 * Material 3 bottom sheet used to create and edit a task.
 *
 * Saving writes through [TaskRepository], which persists locally first and
 * queues the server write, so the editor works offline.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TaskEditorSheet(
    repository: TaskRepository,
    taskId: String?,
    initial: TaskDraft,
    onDismiss: () -> Unit,
    onDeleted: (() -> Unit)? = null,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val scope = rememberCoroutineScope()
    var title by remember { mutableStateOf(initial.title) }
    var notes by remember { mutableStateOf(initial.description) }
    var dueDate by remember { mutableStateOf(initial.dueDate) }
    var dueTime by remember { mutableStateOf(initial.dueTime) }
    var priority by remember { mutableStateOf(initial.priority) }
    var estimate by remember { mutableStateOf(initial.estimatedMinutes?.toString() ?: "") }
    var showDatePicker by remember { mutableStateOf(false) }
    var newStep by remember { mutableStateOf("") }
    val steps by remember(taskId) {
        if (taskId == null) flowOf(emptyList<StepEntity>()) else repository.observeSteps(taskId)
    }.collectAsStateWithLifecycle(initialValue = emptyList())
    var showTimePicker by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    fun save() {
        val cleanTitle = title.trim()
        if (cleanTitle.isEmpty()) {
            error = "请填写任务标题。"
            return
        }
        scope.launch {
            val draft = initial.copy(
                title = cleanTitle,
                description = notes.trim(),
                priority = priority,
                estimatedMinutes = estimate.trim().toIntOrNull()?.coerceIn(1, 525600),
                dueDate = dueDate,
                dueTime = if (dueDate == null) null else dueTime,
            )
            if (taskId == null) {
                repository.create(draft)
            } else {
                repository.update(taskId, draft)
                repository.updateSchedule(taskId, draft)
            }
            onDismiss()
        }
    }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
    ) {
        Column(
            Modifier
                .fillMaxWidth()
                .fillMaxHeight(0.92f)
                .navigationBarsPadding()
                .imePadding()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 24.dp)
                .padding(bottom = 24.dp),
        ) {
            Text(
                if (taskId == null) "新建任务" else "编辑任务",
                style = MaterialTheme.typography.titleLarge,
            )
            Spacer(Modifier.height(16.dp))
            OutlinedTextField(
                value = title,
                onValueChange = { title = it },
                label = { Text("标题") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(12.dp))
            OutlinedTextField(
                value = notes,
                onValueChange = { notes = it },
                label = { Text("备注") },
                minLines = 2,
                maxLines = 4,
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(16.dp))
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                OutlinedTextField(
                    value = dueDate?.format(DateTimeFormatter.ISO_LOCAL_DATE) ?: "未设置",
                    onValueChange = {},
                    readOnly = true,
                    label = { Text("日期") },
                    trailingIcon = {
                        IconButton(onClick = { showDatePicker = true }) {
                            Icon(Icons.Outlined.CalendarToday, contentDescription = "选择日期")
                        }
                    },
                    modifier = Modifier.weight(1f),
                )
                OutlinedTextField(
                    value = dueTime?.format(DateTimeFormatter.ofPattern("HH:mm")) ?: "全天",
                    onValueChange = {},
                    readOnly = true,
                    label = { Text("时间") },
                    trailingIcon = {
                        IconButton(
                            onClick = { showTimePicker = true },
                            enabled = dueDate != null,
                        ) {
                            Icon(Icons.Outlined.Schedule, contentDescription = "选择时间")
                        }
                    },
                    modifier = Modifier.weight(1f),
                )
            }
            if (dueDate != null || dueTime != null) {
                TextButton(onClick = {
                    dueDate = null
                    dueTime = null
                }) {
                    Text("清除日期")
                }
            }
            Spacer(Modifier.height(8.dp))
            Text("优先级", style = MaterialTheme.typography.labelLarge)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf(0 to "无", 1 to "低", 2 to "中", 3 to "高").forEach { (value, label) ->
                    FilterChip(
                        selected = priority == value,
                        onClick = { priority = value },
                        label = { Text(label) },
                    )
                }
            }
            Spacer(Modifier.height(12.dp))
            OutlinedTextField(
                value = estimate,
                onValueChange = { estimate = it.filter(Char::isDigit) },
                label = { Text("预计用时（分钟）") },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            if (taskId != null) {
                Spacer(Modifier.height(16.dp))
                Text("步骤", style = MaterialTheme.typography.labelLarge)
                steps.forEach { step ->
                    Row(
                        Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Checkbox(
                            checked = step.completed,
                            onCheckedChange = { checked ->
                                scope.launch { repository.setStepCompleted(taskId, step.id, checked) }
                            },
                        )
                        Text(
                            step.title,
                            style = MaterialTheme.typography.bodyMedium,
                            modifier = Modifier.weight(1f),
                        )
                        IconButton(
                            onClick = { scope.launch { repository.moveStep(taskId, step.id, up = true) } },
                            enabled = steps.firstOrNull()?.id != step.id,
                        ) {
                            Icon(Icons.Outlined.KeyboardArrowUp, contentDescription = "上移步骤")
                        }
                        IconButton(
                            onClick = { scope.launch { repository.moveStep(taskId, step.id, up = false) } },
                            enabled = steps.lastOrNull()?.id != step.id,
                        ) {
                            Icon(Icons.Outlined.KeyboardArrowDown, contentDescription = "下移步骤")
                        }
                        IconButton(onClick = {
                            scope.launch { repository.deleteStep(taskId, step.id) }
                        }) {
                            Icon(Icons.Outlined.Delete, contentDescription = "删除步骤")
                        }
                    }
                }
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    OutlinedTextField(
                        value = newStep,
                        onValueChange = { newStep = it },
                        placeholder = { Text("添加步骤") },
                        singleLine = true,
                        modifier = Modifier.weight(1f),
                    )
                    IconButton(
                        onClick = {
                            repository.let { repo ->
                                scope.launch {
                                    repo.addStep(taskId, newStep)
                                    newStep = ""
                                }
                            }
                        },
                        enabled = newStep.isNotBlank(),
                    ) {
                        Icon(Icons.Filled.Add, contentDescription = "添加步骤")
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
            initialSelectedDateMillis = (dueDate ?: LocalDate.now())
                .atStartOfDay(ZoneId.of("UTC"))
                .toInstant()
                .toEpochMilli(),
        )
        DatePickerDialog(
            onDismissRequest = { showDatePicker = false },
            confirmButton = {
                TextButton(
                    onClick = {
                        state.selectedDateMillis?.let { millis ->
                            dueDate = Instant.ofEpochMilli(millis)
                                .atZone(ZoneId.of("UTC"))
                                .toLocalDate()
                        }
                        showDatePicker = false
                    },
                ) { Text("确定") }
            },
            dismissButton = {
                TextButton(onClick = { showDatePicker = false }) { Text("取消") }
            },
        ) {
            DatePicker(state = state)
        }
    }

    if (showTimePicker) {
        val state = rememberTimePickerState(
            initialHour = dueTime?.hour ?: 9,
            initialMinute = dueTime?.minute ?: 0,
            is24Hour = true,
        )
        androidx.compose.material3.AlertDialog(
            onDismissRequest = { showTimePicker = false },
            title = { Text("选择时间") },
            text = { TimePicker(state = state) },
            confirmButton = {
                TextButton(
                    onClick = {
                        dueTime = LocalTime.of(state.hour, state.minute)
                        showTimePicker = false
                    },
                ) { Text("确定") }
            },
            dismissButton = {
                TextButton(onClick = { showTimePicker = false }) { Text("取消") }
            },
        )
    }
}
