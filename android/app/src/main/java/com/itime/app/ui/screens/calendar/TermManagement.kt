package com.itime.app.ui.screens.calendar

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.outlined.ArrowBack
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.DatePicker
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberDatePickerState
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.itime.app.core.Times
import com.itime.app.data.local.SeriesEntity
import com.itime.app.data.local.TermEntity
import com.itime.app.data.local.TermPeriodEntity
import com.itime.app.domain.Terms
import kotlinx.coroutines.launch
import java.time.DayOfWeek
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.time.temporal.TemporalAdjusters

/** 学期面板当前展示的是列表还是表单；表单里 term 为 null 表示新建。 */
private data class TermEditorRequest(val term: TermEntity?)

private val dateFormat = DateTimeFormatter.ofPattern("yyyy 年 M 月 d 日")
private val shortDateFormat = DateTimeFormatter.ofPattern("yyyy/M/d")

/**
 * 学期管理：列出全部学期（旧学期也留着），能改名、改起止周、删除。
 *
 * 课表截图导入和课程周次都靠学期，所以入口只有一个，建、改、删都在这里，
 * 避免“再建一个”这种重复。删除会连带删除该学期的课程，服务端同此语义。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TermManagerSheet(
    terms: List<TermEntity>,
    periods: List<TermPeriodEntity>,
    series: List<SeriesEntity>,
    zone: ZoneId,
    onDismiss: () -> Unit,
    onCreate: suspend (String, LocalDate, Int) -> Unit,
    onUpdate: suspend (TermEntity, String, LocalDate, Int) -> Unit,
    onDelete: suspend (TermEntity) -> Unit,
) {
    val scope = rememberCoroutineScope()
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    var editor by remember { mutableStateOf<TermEditorRequest?>(null) }
    var deleteTarget by remember { mutableStateOf<TermEntity?>(null) }
    var error by remember { mutableStateOf<String?>(null) }

    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheetState) {
        val request = editor
        if (request == null) {
            TermListContent(
                terms = terms,
                periods = periods,
                series = series,
                zone = zone,
                error = error,
                onCreate = { editor = TermEditorRequest(null) },
                onEdit = { editor = TermEditorRequest(it) },
                onDelete = { deleteTarget = it },
                onClose = onDismiss,
            )
        } else {
            TermFormContent(
                editing = request.term,
                terms = terms,
                defaultTimeZone = zone,
                title = if (request.term == null) "新建学期" else "编辑学期",
                description = "学期决定课程的周次和日期。写错了改这里，不用删了重建。",
                confirmLabel = if (request.term == null) "创建学期" else "保存修改",
                onBack = { editor = null },
                onSave = { name, monday, weeks ->
                    val target = request.term
                    if (target == null) {
                        onCreate(name, monday, weeks)
                    } else {
                        onUpdate(target, name, monday, weeks)
                    }
                    error = null
                    editor = null
                },
            )
        }
    }

    deleteTarget?.let { term ->
        val courses = series.count { it.termId == term.id }
        AlertDialog(
            onDismissRequest = { deleteTarget = null },
            title = { Text("删除学期") },
            text = {
                Text(
                    if (courses > 0) {
                        "删除「${term.name}」？这学期下的 $courses 门课程会一起删除，无法恢复。"
                    } else {
                        "删除「${term.name}」？这个学期下还没有课程。"
                    },
                )
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        deleteTarget = null
                        scope.launch {
                            runCatching { onDelete(term) }
                                .onFailure { error = it.message ?: "删除失败，稍后再试。" }
                        }
                    },
                ) {
                    Text("删除", color = MaterialTheme.colorScheme.error)
                }
            },
            dismissButton = {
                TextButton(onClick = { deleteTarget = null }) { Text("取消") }
            },
        )
    }
}

/** 只建一个学期就走的入口（例如还没有学期就要导入课表截图）。 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TermFormSheet(
    terms: List<TermEntity>,
    defaultTimeZone: ZoneId,
    title: String,
    description: String,
    confirmLabel: String,
    onSave: suspend (String, LocalDate, Int) -> Unit,
    onSaved: () -> Unit,
    onDismiss: () -> Unit,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheetState) {
        TermFormContent(
            editing = null,
            terms = terms,
            defaultTimeZone = defaultTimeZone,
            title = title,
            description = description,
            confirmLabel = confirmLabel,
            onBack = onDismiss,
            onSave = { name, monday, weeks ->
                onSave(name, monday, weeks)
                onSaved()
            },
        )
    }
}

@Composable
private fun TermListContent(
    terms: List<TermEntity>,
    periods: List<TermPeriodEntity>,
    series: List<SeriesEntity>,
    zone: ZoneId,
    error: String?,
    onCreate: () -> Unit,
    onEdit: (TermEntity) -> Unit,
    onDelete: (TermEntity) -> Unit,
    onClose: () -> Unit,
) {
    val today = LocalDate.now(zone)
    Column(
        Modifier
            .fillMaxWidth()
            .padding(horizontal = 20.dp)
            .padding(bottom = 24.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                "学期",
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.weight(1f),
            )
            TextButton(onClick = onClose) { Text("完成") }
        }
        Text(
            "旧学期留着不影响课表，只用来折算周次；不用的学期可以删。节次表暂时在 Web 的日历设置里改。",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        error?.let {
            Spacer(Modifier.height(8.dp))
            Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
        }
        Spacer(Modifier.height(12.dp))
        if (terms.isEmpty()) {
            Surface(
                color = MaterialTheme.colorScheme.surfaceVariant,
                shape = RoundedCornerShape(12.dp),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(Modifier.padding(16.dp)) {
                    Text("还没有学期", style = MaterialTheme.typography.titleSmall)
                    Spacer(Modifier.height(4.dp))
                    Text(
                        "课表截图导入、课程显示第几周都要用学期，先建一个。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        } else {
            Column(
                Modifier
                    .fillMaxWidth()
                    .heightIn(max = 400.dp)
                    .verticalScroll(rememberScrollState()),
            ) {
                terms.sortedByDescending { it.firstWeekMonday }.forEach { term ->
                    TermRow(
                        term = term,
                        today = today,
                        courseCount = series.count { it.termId == term.id },
                        periodCount = periods.count { it.termId == term.id },
                        onEdit = { onEdit(term) },
                        onDelete = { onDelete(term) },
                    )
                    HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                }
            }
        }
        Spacer(Modifier.height(16.dp))
        Button(onClick = onCreate, modifier = Modifier.fillMaxWidth()) {
            Icon(Icons.Filled.Add, contentDescription = null)
            Text("新建学期", modifier = Modifier.padding(start = 8.dp))
        }
    }
}

@Composable
private fun TermRow(
    term: TermEntity,
    today: LocalDate,
    courseCount: Int,
    periodCount: Int,
    onEdit: () -> Unit,
    onDelete: () -> Unit,
) {
    val phase = Terms.phase(term, today)
    val week = if (phase == Terms.Phase.Active) Terms.weekNumber(term, today) else null
    val start = Times.parseDate(term.startDate)
    val end = Times.parseDate(term.endDate)
    val weeks = if (start != null && end != null) Terms.weeksBetween(start, end) else 0
    val dates = if (start != null && end != null) {
        "${start.format(shortDateFormat)} – ${end.format(shortDateFormat)}"
    } else {
        null
    }
    val stats = buildList {
        if (weeks > 0) add("$weeks 周")
        add("$courseCount 门课")
        if (periodCount == 0) add("未设节次") else add("$periodCount 个节次")
    }.joinToString(" · ")

    Row(
        Modifier
            .fillMaxWidth()
            .clickable(onClick = onEdit)
            .padding(vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    term.name,
                    style = MaterialTheme.typography.titleSmall,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f, fill = false),
                )
                TermBadge(phase = phase, week = week, modifier = Modifier.padding(start = 8.dp))
            }
            Spacer(Modifier.height(2.dp))
            // 日期与统计分两行，窄屏上不会把「未设节次」折成半截。
            dates?.let {
                Text(
                    it,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                )
            }
            Text(
                stats,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
            )
        }
        IconButton(onClick = onEdit) {
            Icon(Icons.Outlined.Edit, contentDescription = "编辑学期")
        }
        IconButton(onClick = onDelete) {
            Icon(Icons.Outlined.Delete, contentDescription = "删除学期")
        }
    }
}

@Composable
private fun TermBadge(phase: Terms.Phase, week: Int?, modifier: Modifier = Modifier) {
    val (label, container, content) = when (phase) {
        Terms.Phase.Active -> Triple(
            if (week != null) "进行中 · 第 $week 周" else "进行中",
            MaterialTheme.colorScheme.secondaryContainer,
            MaterialTheme.colorScheme.onSecondaryContainer,
        )
        Terms.Phase.Upcoming -> Triple(
            "未开始",
            MaterialTheme.colorScheme.surfaceVariant,
            MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Terms.Phase.Finished -> Triple(
            "已结束",
            MaterialTheme.colorScheme.surfaceVariant,
            MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
    Surface(color = container, shape = RoundedCornerShape(50), modifier = modifier) {
        Text(
            label,
            style = MaterialTheme.typography.labelSmall,
            color = content,
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp),
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun TermFormContent(
    editing: TermEntity?,
    terms: List<TermEntity>,
    defaultTimeZone: ZoneId,
    title: String,
    description: String,
    confirmLabel: String,
    onBack: () -> Unit,
    onSave: suspend (String, LocalDate, Int) -> Unit,
) {
    val scope = rememberCoroutineScope()
    val today = LocalDate.now(defaultTimeZone)
    var name by remember { mutableStateOf(editing?.name.orEmpty()) }
    var monday by remember {
        mutableStateOf(
            editing?.firstWeekMonday?.let { Times.parseDate(it) }
                ?: today.with(TemporalAdjusters.previousOrSame(DayOfWeek.MONDAY)),
        )
    }
    var weeks by remember {
        mutableIntStateOf(
            editing?.let { term ->
                val start = Times.parseDate(term.startDate)
                val end = Times.parseDate(term.endDate)
                if (start != null && end != null) Terms.weeksBetween(start, end) else 20
            } ?: 20,
        )
    }
    var pickerOpen by remember { mutableStateOf(false) }
    var snapNote by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    val duplicate = Terms.duplicateError(terms, editing?.id, name, monday, weeks)
    val overlap = Terms.overlapping(terms, editing?.id, monday, weeks)
    val end = Terms.endDate(monday, weeks)

    Column(
        Modifier
            .fillMaxWidth()
            .padding(horizontal = 20.dp)
            .padding(bottom = 28.dp)
            .verticalScroll(rememberScrollState()),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onBack) {
                Icon(Icons.Outlined.ArrowBack, contentDescription = "返回学期列表")
            }
            Text(title, style = MaterialTheme.typography.titleMedium)
        }
        Text(
            description,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(start = 4.dp),
        )
        Spacer(Modifier.height(16.dp))
        OutlinedTextField(
            value = name,
            onValueChange = {
                name = it
                error = null
            },
            label = { Text("学期名称") },
            placeholder = { Text("例如：2026-2027 秋季学期") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(12.dp))
        TextButton(onClick = { pickerOpen = true }) {
            Text("第一周周一：${monday.format(dateFormat)}")
        }
        snapNote?.let {
            Text(
                it,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(start = 12.dp),
            )
        }
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("共 $weeks 周", style = MaterialTheme.typography.bodyMedium)
                Text(
                    "最后一天 ${end.format(dateFormat)}",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            TextButton(onClick = { if (weeks > 1) weeks -= 1 }) { Text("-1") }
            TextButton(onClick = { if (weeks < 60) weeks += 1 }) { Text("+1") }
        }
        Spacer(Modifier.height(4.dp))
        when {
            duplicate != null -> Text(
                duplicate,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
            )
            error != null -> Text(
                error!!,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
            )
            overlap != null -> Text(
                "和「${overlap.name}」的日期有重叠，按各自的第一周折算周次。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Spacer(Modifier.height(12.dp))
        Button(
            onClick = {
                if (busy) return@Button
                val trimmed = name.trim()
                if (trimmed.isEmpty()) {
                    error = "请填写学期名称。"
                    return@Button
                }
                if (duplicate != null) {
                    error = duplicate
                    return@Button
                }
                busy = true
                error = null
                scope.launch {
                    runCatching { onSave(trimmed, monday, weeks) }
                        .onFailure {
                            error = it.message ?: "保存失败，请稍后重试。"
                            busy = false
                        }
                }
            },
            enabled = !busy,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text(if (busy) "正在保存…" else confirmLabel)
        }
        TextButton(onClick = onBack, modifier = Modifier.fillMaxWidth()) { Text("取消") }
    }

    if (pickerOpen) {
        val initialMillis = monday.atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli()
        val state = rememberDatePickerState(initialSelectedDateMillis = initialMillis)
        DatePickerDialog(
            onDismissRequest = { pickerOpen = false },
            confirmButton = {
                TextButton(onClick = {
                    val millis = state.selectedDateMillis
                    if (millis != null) {
                        val picked = Instant.ofEpochMilli(millis)
                            .atZone(ZoneId.of("UTC"))
                            .toLocalDate()
                        val snapped = picked.with(
                            TemporalAdjusters.previousOrSame(DayOfWeek.MONDAY),
                        )
                        snapNote = if (snapped != picked) {
                            "已对齐到 ${snapped.format(dateFormat)}（那一周的周一）"
                        } else {
                            null
                        }
                        monday = snapped
                        error = null
                    }
                    pickerOpen = false
                }) { Text("确定") }
            },
            dismissButton = {
                TextButton(onClick = { pickerOpen = false }) { Text("取消") }
            },
        ) { DatePicker(state = state) }
    }
}
