package com.itime.app.ui.screens.tasks

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
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.outlined.Checklist
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material3.Card
import androidx.compose.material3.Checkbox
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
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
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.itime.app.AppContainer
import com.itime.app.core.Times
import com.itime.app.data.SyncPhase
import com.itime.app.data.SyncState
import com.itime.app.data.local.TaskEntity
import com.itime.app.domain.TaskDraft
import com.itime.app.data.repository.toDraft
import com.itime.app.ui.components.EmptyState
import com.itime.app.ui.components.SyncChip
import kotlinx.coroutines.launch
import java.time.LocalDate
import java.time.format.DateTimeFormatter

private enum class TaskFilter(val label: String) {
    All("全部"),
    Today("今天"),
    Completed("已完成"),
    Trash("回收站"),
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TasksScreen(container: AppContainer, syncState: SyncState) {
    val active by container.tasks.observeActive().collectAsStateWithLifecycle(emptyList())
    val trashed by container.tasks.observeTrashed().collectAsStateWithLifecycle(emptyList())
    val scope = rememberCoroutineScope()
    var query by remember { mutableStateOf("") }
    var filter by remember { mutableStateOf(TaskFilter.All) }
    var showEditor by remember { mutableStateOf(false) }
    var editorTaskId by remember { mutableStateOf<String?>(null) }
    var editorDraft by remember { mutableStateOf(TaskDraft()) }
    val today = LocalDate.now()

    val visible = remember(active, trashed, query, filter) {
        val source = when (filter) {
            TaskFilter.Trash -> trashed
            TaskFilter.Completed -> active.filter { it.isCompleted }
            TaskFilter.Today -> active.filter {
                !it.isCompleted && Times.parseDate(it.dueDate)?.let { due -> !due.isAfter(today) } == true
            }
            else -> active.filter { !it.isCompleted }
        }
        if (query.isBlank()) {
            source
        } else {
            source.filter {
                it.title.contains(query, ignoreCase = true) ||
                    it.description.contains(query, ignoreCase = true)
            }
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("任务") },
                actions = {
                    SyncChip(syncState.phase, syncState.pendingCount, syncState.conflictCount)
                    IconButton(onClick = { scope.launch { container.sync.syncNow("tasks-refresh") } }) {
                        Icon(Icons.Outlined.Refresh, contentDescription = "立即同步")
                    }
                },
            )
        },
        floatingActionButton = {
            if (filter != TaskFilter.Trash) {
                ExtendedFloatingActionButton(
                    onClick = {
                        editorTaskId = null
                        editorDraft = TaskDraft()
                        showEditor = true
                    },
                    icon = { Icon(Icons.Filled.Add, contentDescription = null) },
                    text = { Text("新建任务") },
                )
            }
        },
    ) { padding ->
        Column(
            Modifier
                .fillMaxSize()
                .padding(padding),
        ) {
            OutlinedTextField(
                value = query,
                onValueChange = { query = it },
                placeholder = { Text("搜索任务") },
                leadingIcon = { Icon(Icons.Outlined.Search, contentDescription = null) },
                singleLine = true,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 4.dp),
            )
            Row(
                Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 4.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                TaskFilter.entries.forEach { option ->
                    FilterChip(
                        selected = filter == option,
                        onClick = { filter = option },
                        label = { Text(option.label) },
                    )
                }
            }
            if (visible.isEmpty()) {
                EmptyState(
                    icon = Icons.Outlined.Checklist,
                    title = when (filter) {
                        TaskFilter.Trash -> "回收站是空的"
                        TaskFilter.Completed -> "还没有完成的任务"
                        else -> "没有匹配的任务"
                    },
                    message = when (filter) {
                        TaskFilter.Trash -> "删除的任务会在这里保留 30 天。"
                        TaskFilter.Completed -> "完成一个任务后会显示在这里。"
                        else -> "换个筛选条件，或者新建一个任务。"
                    },
                )
            } else {
                PullToRefreshBox(
                    isRefreshing = syncState.phase == SyncPhase.Connecting ||
                        syncState.phase == SyncPhase.Syncing,
                    onRefresh = { scope.launch { container.sync.syncNow("tasks-pull") } },
                    modifier = Modifier.fillMaxSize(),
                ) {
                LazyColumn(
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(start = 16.dp, end = 16.dp, bottom = 120.dp),
                ) {
                    items(visible, key = { it.id }) { task ->
                        TaskRow(
                            task = task,
                            inTrash = filter == TaskFilter.Trash,
                            onToggle = {
                                scope.launch {
                                    if (filter == TaskFilter.Trash) {
                                        container.tasks.restore(task.id)
                                    } else {
                                        container.tasks.setCompleted(task.id, !task.isCompleted)
                                    }
                                }
                            },
                            onClick = {
                                editorTaskId = task.id
                                editorDraft = task.toDraft()
                                showEditor = true
                            },
                            onDelete = {
                                scope.launch { container.tasks.trash(task.id) }
                            },
                            onRestore = {
                                scope.launch { container.tasks.restore(task.id) }
                            },
                            onArchive = {
                                scope.launch { container.tasks.archive(task.id, true) }
                            },
                        )
                    }
                }
                }
            }
        }
    }

    if (showEditor) {
        TaskEditorSheet(
            repository = container.tasks,
            taskId = editorTaskId,
            initial = editorDraft,
            onDismiss = { showEditor = false },
        )
    }
}

@Composable
private fun TaskRow(
    task: TaskEntity,
    inTrash: Boolean,
    onToggle: () -> Unit,
    onClick: () -> Unit,
    onDelete: () -> Unit,
    onRestore: () -> Unit,
    onArchive: () -> Unit,
) {
    var menu by remember { mutableStateOf(false) }
    Card(
        onClick = onClick,
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
    ) {
        Row(
            Modifier
                .fillMaxWidth()
                .padding(horizontal = 4.dp, vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Checkbox(checked = task.isCompleted, onCheckedChange = { onToggle() })
            Column(Modifier.weight(1f)) {
                Text(
                    task.title,
                    style = MaterialTheme.typography.bodyLarge,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    color = if (task.isCompleted) {
                        MaterialTheme.colorScheme.onSurfaceVariant
                    } else {
                        MaterialTheme.colorScheme.onSurface
                    },
                )
                val detail = listOfNotNull(
                    Times.parseDate(task.dueDate)?.format(DateTimeFormatter.ofPattern("M 月 d 日")),
                    Times.parseTime(task.dueTime)?.format(DateTimeFormatter.ofPattern("HH:mm")),
                    task.estimatedMinutes?.let { "$it 分钟" },
                    task.labels.takeIf { it.isNotEmpty() }?.joinToString(" "),
                ).joinToString(" · ")
                if (detail.isNotBlank()) {
                    Text(
                        detail,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            if (task.pendingSync) {
                Text(
                    "待同步",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.tertiary,
                    modifier = Modifier.padding(end = 4.dp),
                )
            }
            Box {
                IconButton(onClick = { menu = true }) {
                    Icon(Icons.Outlined.MoreVert, contentDescription = "更多操作")
                }
                DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                    if (inTrash) {
                        DropdownMenuItem(
                            text = { Text("恢复") },
                            onClick = {
                                menu = false
                                onRestore()
                            },
                        )
                    } else {
                        DropdownMenuItem(
                            text = { Text("归档") },
                            onClick = {
                                menu = false
                                onArchive()
                            },
                        )
                        DropdownMenuItem(
                            text = { Text("删除") },
                            onClick = {
                                menu = false
                                onDelete()
                            },
                        )
                    }
                }
            }
        }
    }
    Spacer(Modifier.height(2.dp))
    Spacer(Modifier.width(0.dp))
}

