package com.itime.app.ui.components

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.outlined.Repeat
import androidx.compose.material3.Card
import androidx.compose.material3.Checkbox
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.itime.app.core.Times
import com.itime.app.data.local.TaskEntity
import java.time.format.DateTimeFormatter

/**
 * One task card: completion checkbox, title, meta line and the overflow menu.
 * Extracted so the toggle/overflow contract can be tested without the screen.
 */
@Composable
fun TaskRow(
    task: TaskEntity,
    inTrash: Boolean,
    restorable: Boolean = inTrash,
    onToggle: () -> Unit,
    onClick: () -> Unit,
    onDelete: () -> Unit,
    onRestore: () -> Unit,
    onArchive: () -> Unit,
    onDuplicate: () -> Unit = {},
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
            if (task.isRepeating) {
                Icon(
                    Icons.Outlined.Repeat,
                    contentDescription = "重复任务按次完成，请在日历中操作",
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 12.dp),
                )
            } else {
                Checkbox(checked = task.isCompleted, onCheckedChange = { onToggle() })
            }
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
                    task.projectName,
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
                    if (inTrash || restorable) {
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
                            text = { Text("复制") },
                            onClick = {
                                menu = false
                                onDuplicate()
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
}
