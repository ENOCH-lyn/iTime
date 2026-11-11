package com.itime.app.ui.screens.tasks

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
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
import androidx.compose.runtime.LaunchedEffect
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
import androidx.compose.foundation.rememberScrollState
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.itime.app.AppContainer
import com.itime.app.core.Times
import com.itime.app.data.SyncPhase
import com.itime.app.data.SyncState
import com.itime.app.data.local.TaskEntity
import com.itime.app.domain.TaskDraft
import com.itime.app.data.repository.toDraft
import com.itime.app.ui.components.EmptyState
import com.itime.app.ui.components.OfflineBanner
import com.itime.app.ui.components.TaskDetailContent
import com.itime.app.ui.components.TaskDetailSheet
import com.itime.app.ui.components.TaskRow
import com.itime.app.ui.components.SyncChip
import kotlinx.coroutines.launch
import java.time.LocalDate
import java.time.format.DateTimeFormatter

private enum class TaskFilter(val label: String) {
    All("全部"),
    Today("今天"),
    Completed("已完成"),
    Trash("回收站"),
    Archived("已归档"),
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TasksScreen(container: AppContainer, syncState: SyncState) {
    val active by container.tasks.observeActive().collectAsStateWithLifecycle(emptyList())
    val trashed by container.tasks.observeTrashed().collectAsStateWithLifecycle(emptyList())
    val archived by container.tasks.observeArchived().collectAsStateWithLifecycle(emptyList())
    val scope = rememberCoroutineScope()
    var query by remember { mutableStateOf("") }
    var filter by remember { mutableStateOf(TaskFilter.All) }
    var projectFilter by remember { mutableStateOf<String?>(null) }
    var detailTaskId by remember { mutableStateOf<String?>(null) }
    var selectedTaskId by remember { mutableStateOf<String?>(null) }
    val wide = androidx.compose.ui.platform.LocalConfiguration.current.screenWidthDp >= 600
    val projects by container.tasks.observeProjects()
        .collectAsStateWithLifecycle(emptyList())
    var showEditor by remember { mutableStateOf(false) }
    var editorTaskId by remember { mutableStateOf<String?>(null) }
    var editorDraft by remember { mutableStateOf(TaskDraft()) }
    val today = LocalDate.now()
    val pendingShare by container.pendingShare.collectAsStateWithLifecycle()

    LaunchedEffect(pendingShare) {
        val shared = pendingShare ?: return@LaunchedEffect
        editorTaskId = null
        editorDraft = TaskDraft(title = shared.take(200))
        showEditor = true
        container.pendingShare.value = null
    }

    val visible = remember(active, trashed, archived, query, filter, projectFilter) {
        val base = when (filter) {
            TaskFilter.Trash -> trashed
            TaskFilter.Archived -> archived
            TaskFilter.Completed -> active.filter { it.isCompleted }
            TaskFilter.Today -> active.filter {
                !it.isCompleted && Times.parseDate(it.dueDate)?.let { due -> !due.isAfter(today) } == true
            }
            else -> active.filter { !it.isCompleted }
        }
        val source = if (projectFilter == null) base else base.filter { it.projectId == projectFilter }
        if (query.isBlank()) {
            source
        } else {
            source.filter {
                it.title.contains(query, ignoreCase = true) ||
                    it.description.contains(query, ignoreCase = true) ||
                    (it.projectName?.contains(query, ignoreCase = true) ?: false) ||
                    it.labelsCsv.contains(query, ignoreCase = true)
            }
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("任务") },
                actions = {
                    SyncChip(syncState.phase, syncState.pendingCount, syncState.conflictCount)
                    IconButton(onClick = { scope.launch { container.sync.syncNow("tasks-refresh", force = true) } }) {
                        Icon(Icons.Outlined.Refresh, contentDescription = "立即同步")
                    }
                },
            )
        },
        floatingActionButton = {
            if (filter != TaskFilter.Trash && filter != TaskFilter.Archived) {
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
            if (projects.isNotEmpty()) {
                Row(
                    Modifier
                        .fillMaxWidth()
                        .horizontalScroll(rememberScrollState())
                        .padding(horizontal = 16.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    FilterChip(
                        selected = projectFilter == null,
                        onClick = { projectFilter = null },
                        label = { Text("全部项目") },
                    )
                    projects.forEach { project ->
                        FilterChip(
                            selected = projectFilter == project.id,
                            onClick = { projectFilter = project.id },
                            label = { Text(project.name) },
                        )
                    }
                }
            }
            if (visible.isEmpty()) {
                EmptyState(
                    icon = Icons.Outlined.Checklist,
                    title = when (filter) {
                        TaskFilter.Trash -> "回收站是空的"
                        TaskFilter.Archived -> "没有已归档的任务"
                        TaskFilter.Completed -> "还没有完成的任务"
                        else -> "没有匹配的任务"
                    },
                    message = when (filter) {
                        TaskFilter.Trash -> "删除的任务会在这里保留 30 天。"
                        TaskFilter.Archived -> "归档的任务会保留在这里，可随时恢复。"
                        TaskFilter.Completed -> "完成一个任务后会显示在这里。"
                        else -> "换个筛选条件，或者新建一个任务。"
                    },
                )
            } else {
                val listBody: @Composable () -> Unit = {
                if (syncState.phase == SyncPhase.Offline) {
                    OfflineBanner(
                        Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                    )
                }
                PullToRefreshBox(
                    isRefreshing = syncState.phase == SyncPhase.Connecting ||
                        syncState.phase == SyncPhase.Syncing,
                    onRefresh = { scope.launch { container.sync.syncNow("tasks-pull", force = true) } },
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
                            restorable = filter == TaskFilter.Trash || filter == TaskFilter.Archived,
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
                                if (wide) {
                                    selectedTaskId = task.id
                                } else {
                                    detailTaskId = task.id
                                }
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
                            onDuplicate = {
                                scope.launch { container.tasks.duplicate(task.id) }
                            },
                        )
                    }
                }
                }
                }
                if (wide) {
                    Row(
                        Modifier
                            .weight(1f)
                            .fillMaxWidth(),
                    ) {
                        Box(Modifier.weight(0.56f).fillMaxHeight()) { listBody() }
                        Box(Modifier.weight(0.44f).fillMaxHeight()) {
                            val selected = visible.firstOrNull { it.id == selectedTaskId }
                            TaskDetailContent(
                                task = selected,
                                onEdit = {
                                    selected?.let {
                                        editorTaskId = it.id
                                        editorDraft = it.toDraft()
                                        showEditor = true
                                    }
                                },
                                onComplete = {
                                    selected?.let { scope.launch { container.tasks.setCompleted(it.id, !it.isCompleted) } }
                                },
                                onDuplicate = {
                                    selected?.let { scope.launch { container.tasks.duplicate(it.id) } }
                                },
                                onArchive = {
                                    selected?.let { scope.launch { container.tasks.archive(it.id, true) } }
                                },
                                onTrash = {
                                    selected?.let { scope.launch { container.tasks.trash(it.id) } }
                                    selectedTaskId = null
                                },
                                modifier = Modifier.fillMaxSize(),
                            )
                        }
                    }
                } else {
                    listBody()
                }
            }
        }
    }

    visible.firstOrNull { it.id == detailTaskId }?.let { task ->
        TaskDetailSheet(
            task = task,
            onDismiss = { detailTaskId = null },
            onEdit = {
                detailTaskId = null
                editorTaskId = task.id
                editorDraft = task.toDraft()
                showEditor = true
            },
            onComplete = {
                scope.launch { container.tasks.setCompleted(task.id, !task.isCompleted) }
                detailTaskId = null
            },
            onDuplicate = {
                scope.launch { container.tasks.duplicate(task.id) }
                detailTaskId = null
            },
            onArchive = {
                scope.launch { container.tasks.archive(task.id, true) }
                detailTaskId = null
            },
            onTrash = {
                scope.launch { container.tasks.trash(task.id) }
                detailTaskId = null
            },
        )
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