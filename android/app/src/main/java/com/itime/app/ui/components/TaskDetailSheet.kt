package com.itime.app.ui.components

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
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
 * Read-first task detail sheet: shows metadata and the common actions without
 * opening the full editor. Tapping 编辑 falls back to the editor sheet.
 */
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
        Column(
            Modifier
                .fillMaxWidth()
                .padding(horizontal = 24.dp)
                .padding(bottom = 32.dp),
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
}
