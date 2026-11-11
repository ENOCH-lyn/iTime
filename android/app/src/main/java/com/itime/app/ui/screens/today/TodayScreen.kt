package com.itime.app.ui.screens.today

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.Today
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.itime.app.AppContainer
import com.itime.app.core.Times
import com.itime.app.data.SyncPhase
import com.itime.app.data.SyncState
import com.itime.app.domain.Agenda
import com.itime.app.domain.AgendaItem
import com.itime.app.domain.TaskDraft
import com.itime.app.data.repository.toDraft
import com.itime.app.ui.components.EmptyState
import com.itime.app.ui.components.SyncChip
import com.itime.app.ui.screens.tasks.TaskEditorSheet
import kotlinx.coroutines.launch
import java.time.LocalDate
import java.time.LocalTime
import java.time.format.DateTimeFormatter
import java.time.format.TextStyle
import java.util.Locale

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TodayScreen(
    container: AppContainer,
    syncState: SyncState,
    onOpenCalendar: () -> Unit,
) {
    val tasks by container.tasks.observeActive().collectAsStateWithLifecycle(emptyList())
    val series by container.calendar.observeSeries().collectAsStateWithLifecycle(emptyList())
    val terms by container.calendar.observeTerms().collectAsStateWithLifecycle(emptyList())
    val exceptions by container.calendar.observeAllExceptions().collectAsStateWithLifecycle(emptyList())
    val additions by container.calendar.observeAdditions().collectAsStateWithLifecycle(emptyList())
    val occurrences by container.database.occurrenceDao().observeAll()
        .collectAsStateWithLifecycle(emptyList())
    val settings by container.calendar.observeTimeSettings().collectAsStateWithLifecycle(null)
    val zone = Times.zoneOf(settings?.timeZone)
    val today = LocalDate.now(zone)
    val scope = rememberCoroutineScope()
    var editing by remember { mutableStateOf<String?>(null) }
    var showEditor by remember { mutableStateOf(false) }
    var editorDraft by remember { mutableStateOf(TaskDraft()) }
    val editorTaskId = remember { mutableStateOf<String?>(null) }

    val agenda = remember(today, series, tasks, exceptions, additions, occurrences, terms) {
        Agenda.build(
            from = today,
            to = today,
            zone = zone,
            series = series,
            terms = terms.associateBy { it.id },
            exceptions = exceptions,
            additions = additions,
            tasks = tasks,
            occurrences = occurrences,
        )
    }
    val todo = remember(tasks, today) {
        tasks.filter { task ->
            val due = Times.parseDate(task.dueDate)
            !task.isCompleted && due != null && !due.isAfter(today)
        }.sortedBy { it.dueDate ?: "" }
    }
    val doneToday = tasks.count { task ->
        task.isCompleted && task.completedAt?.startsWith(today.toString()) == true
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("今天", style = MaterialTheme.typography.titleLarge)
                        Text(
                            "${today.monthValue} 月 ${today.dayOfMonth} 日 · " +
                                today.dayOfWeek.getDisplayName(TextStyle.FULL, Locale.CHINA),
                            style = MaterialTheme.typography.labelMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                },
                actions = {
                    SyncChip(syncState.phase, syncState.pendingCount, syncState.conflictCount)
                    IconButton(onClick = {
                        scope.launch { container.sync.syncNow("today-refresh") }
                    }) {
                        Icon(Icons.Outlined.Refresh, contentDescription = "立即同步")
                    }
                },
            )
        },
        floatingActionButton = {
            ExtendedFloatingActionButton(
                onClick = {
                    editorTaskId.value = null
                    editorDraft = TaskDraft(dueDate = today)
                    showEditor = true
                },
                icon = { Icon(Icons.Filled.Add, contentDescription = null) },
                text = { Text("新建任务") },
            )
        },
    ) { padding ->
        if (agenda.isEmpty() && todo.isEmpty()) {
            Box(Modifier.padding(padding)) {
                EmptyState(
                    icon = Icons.Outlined.Today,
                    title = "今天还没有安排",
                    message = "添加一个任务，或在日历里安排课程与日程。",
                    actionLabel = "打开日历",
                    onAction = onOpenCalendar,
                )
            }
            return@Scaffold
        }
        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
            contentPadding = PaddingValues(start = 16.dp, end = 16.dp, bottom = 96.dp),
        ) {
            if (syncState.phase == SyncPhase.Offline) {
                item {
                    Card(
                        colors = CardDefaults.cardColors(
                            containerColor = MaterialTheme.colorScheme.surfaceVariant,
                        ),
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(vertical = 8.dp),
                    ) {
                        Text(
                            "当前离线，改动会保存在手机上，联网后自动同步。",
                            style = MaterialTheme.typography.bodySmall,
                            modifier = Modifier.padding(12.dp),
                        )
                    }
                }
            }
            item {
                Spacer(Modifier.height(8.dp))
                Row(
                    Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    StatCard("今日日程", agenda.size.toString(), Modifier.weight(1f))
                    StatCard("待完成任务", todo.size.toString(), Modifier.weight(1f))
                    StatCard("已完成", doneToday.toString(), Modifier.weight(1f))
                }
                Spacer(Modifier.height(8.dp))
            }
            item {
                Text(
                    "今日日程",
                    style = MaterialTheme.typography.titleSmall,
                    color = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.padding(top = 16.dp, bottom = 4.dp),
                )
            }
            items(agenda, key = { it.id }) { item ->
                AgendaRow(item) {
                    if (item.sourceKind == "task") {
                        scope.launch { container.tasks.setCompleted(item.sourceId, true) }
                    }
                }
            }
            if (agenda.isEmpty()) {
                item {
                    Text(
                        "今天没有课程或日程。",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(vertical = 8.dp),
                    )
                }
            }
            item {
                Text(
                    "待办任务",
                    style = MaterialTheme.typography.titleSmall,
                    color = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.padding(top = 20.dp, bottom = 4.dp),
                )
            }
            items(todo, key = { it.id }) { task ->
                Card(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(vertical = 4.dp),
                    onClick = {
                        editorTaskId.value = task.id
                        editorDraft = task.toDraft()
                        showEditor = true
                    },
                ) {
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 8.dp, vertical = 4.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Checkbox(
                            checked = false,
                            onCheckedChange = {
                                scope.launch { container.tasks.setCompleted(task.id, true) }
                            },
                        )
                        Column(Modifier.weight(1f)) {
                            Text(
                                task.title,
                                style = MaterialTheme.typography.bodyLarge,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                            val due = Times.parseDate(task.dueDate)
                            val time = Times.parseTime(task.dueTime)
                            val label = buildString {
                                if (due != null && due.isBefore(today)) append("已逾期 · ")
                                if (time != null) append(time.format(DateTimeFormatter.ofPattern("HH:mm"))) else append("全天")
                                if (task.estimatedMinutes != null) append(" · ${task.estimatedMinutes} 分钟")
                            }
                            Text(
                                label,
                                style = MaterialTheme.typography.bodySmall,
                                color = if (due != null && due.isBefore(today)) {
                                    MaterialTheme.colorScheme.error
                                } else {
                                    MaterialTheme.colorScheme.onSurfaceVariant
                                },
                            )
                        }
                        if (task.priority > 0) {
                            Text(
                                when (task.priority) {
                                    3 -> "高"
                                    2 -> "中"
                                    else -> "低"
                                },
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.tertiary,
                                modifier = Modifier.padding(end = 8.dp),
                            )
                        }
                    }
                }
            }
            if (todo.isEmpty()) {
                item {
                    Text(
                        "没有到期的待办任务。",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(vertical = 8.dp),
                    )
                }
            }
        }
    }

    if (showEditor) {
        TaskEditorSheet(
            repository = container.tasks,
            taskId = editorTaskId.value,
            initial = editorDraft,
            onDismiss = { showEditor = false },
        )
    }
}

@Composable
private fun StatCard(title: String, value: String, modifier: Modifier = Modifier) {
    Card(
        modifier = modifier,
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.secondaryContainer,
        ),
    ) {
        Column(Modifier.padding(12.dp)) {
            Text(
                value,
                style = MaterialTheme.typography.headlineSmall,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onSecondaryContainer,
            )
            Text(
                title,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSecondaryContainer,
            )
        }
    }
}

@Composable
private fun AgendaRow(item: AgendaItem, onComplete: () -> Unit) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
    ) {
        Row(
            Modifier
                .fillMaxWidth()
                .padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.width(56.dp)) {
                Text(
                    item.startTime?.format(DateTimeFormatter.ofPattern("HH:mm")) ?: "全天",
                    style = MaterialTheme.typography.labelLarge,
                )
                if (item.endTime != null) {
                    Text(
                        item.endTime.format(DateTimeFormatter.ofPattern("HH:mm")),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            Column(Modifier.weight(1f)) {
                Text(
                    item.title,
                    style = MaterialTheme.typography.bodyLarge,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                val detail = listOfNotNull(
                    item.subtitle.takeIf { it.isNotBlank() },
                    item.location.takeIf { it.isNotBlank() },
                ).joinToString(" · ")
                if (detail.isNotBlank()) {
                    Text(
                        detail,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                if (item.sourceKind == "task") {
                    Text(
                        "任务",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            if (item.sourceKind == "task" && !item.completed) {
                IconButton(onClick = onComplete) {
                    Icon(
                        Icons.Outlined.CheckCircle,
                        contentDescription = "完成任务",
                        tint = MaterialTheme.colorScheme.primary,
                    )
                }
            }
        }
    }
}

