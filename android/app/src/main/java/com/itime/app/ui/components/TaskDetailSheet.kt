package com.itime.app.ui.components

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Checklist
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.itime.app.core.Times
import com.itime.app.data.local.TaskEntity
import java.time.format.DateTimeFormatter

/**
 * Read-first task body shared by the phone bottom sheet and the wide-screen
 * two-pane detail column. Shows metadata and the common actions.
 */
@Composable
fun TaskDetailContent(
    task: TaskEntity?,
    onEdit: () -> Unit,
    onComplete: () -> Unit,
    onDuplicate: () -> Unit,
    onArchive: () -> Unit,
    onTrash: () -> Unit,
    modifier: Modifier = Modifier,
) {
    if (task == null) {
        EmptyState(
            icon = Icons.Outlined.Checklist,
            title = "选择一条任务",
            message = "在左侧点选任务后，这里显示详情与操作。",
        )
        return
    }
    Column(
        modifier
            .fillMaxWidth()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 24.dp, vertical = 20.dp),
    ) {
        Text(task.title, style = MaterialTheme.typography.titleLarge)
        Spacer(Modifier.height(8.dp))
        val meta = listOfNotNull(
            task.projectName,
            Times.parseDate(task.dueDate)?.format(DateTimeFormatter.ofPattern("M 月 d 日")),
            Times.parseTime(task.dueTime)?.format(DateTimeFormatter.ofPattern("HH:mm")),
            task.estimatedMinutes?.let { "$it 分钟" },
        ).joinToString(" · ")
        if (meta.isNotBlank()) {
            Text(
                meta,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (task.labels.isNotEmpty()) {
            Spacer(Modifier.height(4.dp))
            Text(
                "标签：" + task.labels.joinToString(" "),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (task.description.isNotBlank()) {
            Spacer(Modifier.height(12.dp))
            Text(task.description, style = MaterialTheme.typography.bodyMedium)
        }
        if (task.isRepeating) {
            Spacer(Modifier.height(8.dp))
            Text(
                "重复任务按次完成，请在日历中操作。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Spacer(Modifier.height(20.dp))
        Button(onClick = onEdit, modifier = Modifier.fillMaxWidth()) { Text("编辑") }
        Row(
            Modifier
                .fillMaxWidth()
                .padding(top = 8.dp),
        ) {
            if (!task.isRepeating) {
                OutlinedButton(
                    onClick = onComplete,
                    modifier = Modifier.weight(1f),
                ) { Text(if (task.isCompleted) "标为未完成" else "完成") }
                Spacer(Modifier.padding(horizontal = 4.dp))
            }
            OutlinedButton(
                onClick = onDuplicate,
                modifier = Modifier.weight(1f),
            ) { Text("复制") }
        }
        Row(
            Modifier
                .fillMaxWidth()
                .padding(top = 8.dp),
        ) {
            OutlinedButton(
                onClick = onArchive,
                modifier = Modifier.weight(1f),
            ) { Text("归档") }
            Spacer(Modifier.padding(horizontal = 4.dp))
            TextButton(
                onClick = onTrash,
                modifier = Modifier.weight(1f),
            ) { Text("删除") }
        }
    }
}

/** Phone bottom sheet wrapper around [TaskDetailContent]. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TaskDetailSheet(
    task: TaskEntity,
    onDismiss: () -> Unit,
    onEdit: () -> Unit,
    onComplete: () -> Unit,
    onDuplicate: () -> Unit,
    onArchive: () -> Unit,
    onTrash: () -> Unit,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheetState) {
        TaskDetailContent(
            task = task,
            onEdit = onEdit,
            onComplete = onComplete,
            onDuplicate = onDuplicate,
            onArchive = onArchive,
            onTrash = onTrash,
        )
    }
}
