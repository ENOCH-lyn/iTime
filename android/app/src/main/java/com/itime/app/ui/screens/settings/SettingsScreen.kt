package com.itime.app.ui.screens.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.Divider
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.itime.app.AppContainer
import com.itime.app.core.Times
import com.itime.app.data.SyncPhase
import com.itime.app.data.SyncState
import com.itime.app.data.local.OutboxEntity
import com.itime.app.ui.components.SectionHeader
import com.itime.app.ui.components.SyncChip
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(container: AppContainer, syncState: SyncState) {
    val session by container.auth.session.collectAsStateWithLifecycle()
    val conflicts by container.database.outboxDao().observeConflicts()
        .collectAsStateWithLifecycle(emptyList())
    val settings by container.calendar.observeTimeSettings().collectAsStateWithLifecycle(null)
    val scope = rememberCoroutineScope()
    var serverUrl by remember { mutableStateOf(container.sessionStore.baseUrl()) }
    var showLogout by remember { mutableStateOf(false) }
    var resolving by remember { mutableStateOf<OutboxEntity?>(null) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("设置") },
                actions = {
                    SyncChip(syncState.phase, syncState.pendingCount, syncState.conflictCount)
                },
            )
        },
    ) { padding ->
        Column(
            Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState()),
        ) {
            SectionHeader("账号")
            ListItem(
                headlineContent = { Text(session?.email ?: "未登录") },
                supportingContent = {
                    Text(
                        if (session?.userId != null) "设备会话已保存" else "请重新登录",
                        style = MaterialTheme.typography.bodySmall,
                    )
                },
            )
            Spacer(Modifier.height(8.dp))
            OutlinedButton(
                onClick = { showLogout = true },
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp),
            ) { Text("退出登录") }

            SectionHeader("同步")
            ListItem(
                headlineContent = { Text(syncPhaseLabel(syncState)) },
                supportingContent = {
                    Text(
                        buildString {
                            append("待同步 ${syncState.pendingCount} 项")
                            if (syncState.conflictCount > 0) append(" · 冲突 ${syncState.conflictCount} 项")
                            syncState.lastSuccessAt?.let {
                                append(" · 上次同步 ")
                                append(
                                    Instant.ofEpochMilli(it)
                                        .atZone(ZoneId.systemDefault())
                                        .format(DateTimeFormatter.ofPattern("HH:mm:ss")),
                                )
                            }
                        },
                        style = MaterialTheme.typography.bodySmall,
                    )
                },
            )
            syncState.lastError?.let { error ->
                Text(
                    error,
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(horizontal = 16.dp),
                )
            }
            Button(
                onClick = { scope.launch { container.sync.syncNow("settings-manual") } },
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp),
            ) { Text("立即同步") }

            if (conflicts.isNotEmpty()) {
                SectionHeader("需要你决定")
                Text(
                    "这些改动与其他设备上的修改冲突，选择保留哪一份。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 16.dp),
                )
                conflicts.forEach { conflict ->
                    Card(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 16.dp, vertical = 4.dp),
                    ) {
                        Column(Modifier.padding(12.dp)) {
                            Text(
                                conflictLabel(conflict),
                                style = MaterialTheme.typography.bodyMedium,
                            )
                            Text(
                                conflict.lastErrorMessage ?: "服务器上的数据已被修改。",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                            Row(
                                Modifier.padding(top = 8.dp),
                                horizontalArrangement = Arrangement.spacedBy(8.dp),
                            ) {
                                OutlinedButton(onClick = { resolving = conflict }) {
                                    Text("处理")
                                }
                            }
                        }
                    }
                }
            }

            SectionHeader("服务器")
            OutlinedTextField(
                value = serverUrl,
                onValueChange = { serverUrl = it },
                label = { Text("服务器地址") },
                supportingText = { Text("模拟器用 10.0.2.2:8000，真机用电脑局域网 IP。") },
                singleLine = true,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp),
            )
            TextButton(
                onClick = {
                    scope.launch {
                        container.auth.updateBaseUrl(serverUrl.trim())
                    }
                },
                modifier = Modifier.padding(horizontal = 8.dp),
            ) { Text("保存地址") }

            SectionHeader("时间")
            ListItem(
                headlineContent = { Text("时区") },
                supportingContent = { Text(settings?.timeZone ?: ZoneId.systemDefault().id) },
            )
            ListItem(
                headlineContent = { Text("本周起始") },
                supportingContent = { Text("周一") },
            )

            SectionHeader("关于")
            ListItem(
                headlineContent = { Text("iTime Android") },
                supportingContent = { Text("原生 Material 3 客户端 · 离线优先 · 与网页端同步") },
            )
            Spacer(Modifier.height(32.dp))
        }
    }

    if (showLogout) {
        AlertDialog(
            onDismissRequest = { showLogout = false },
            title = { Text("退出登录？") },
            text = { Text("本机保存的日程与任务会被清除，未同步的改动会丢失。") },
            confirmButton = {
                TextButton(onClick = {
                    showLogout = false
                    scope.launch { container.auth.logout() }
                }) { Text("退出") }
            },
            dismissButton = {
                TextButton(onClick = { showLogout = false }) { Text("取消") }
            },
        )
    }

    resolving?.let { conflict ->
        AlertDialog(
            onDismissRequest = { resolving = null },
            title = { Text("保留哪一份数据？") },
            text = {
                Text(
                    "「保留本地」会用手机上的改动覆盖云端；" +
                        "「保留云端」会丢弃手机上的这一次改动。",
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    val op = conflict
                    resolving = null
                    scope.launch { container.sync.resolveConflict(op.operationId, keepLocal = true) }
                }) { Text("保留本地") }
            },
            dismissButton = {
                Row {
                    TextButton(onClick = {
                        val op = conflict
                        resolving = null
                        scope.launch { container.sync.resolveConflict(op.operationId, keepLocal = false) }
                    }) { Text("保留云端") }
                    TextButton(onClick = { resolving = null }) { Text("稍后") }
                }
            },
        )
    }
}

private fun syncPhaseLabel(state: SyncState): String = when (state.phase) {
    SyncPhase.Idle -> "等待同步"
    SyncPhase.Connecting -> "正在连接"
    SyncPhase.Syncing -> "正在同步"
    SyncPhase.Synced -> "已同步"
    SyncPhase.Pending -> "有改动待同步"
    SyncPhase.Conflict -> "有冲突待处理"
    SyncPhase.Offline -> "离线（本地可用）"
    SyncPhase.Error -> "同步出错"
}

private fun conflictLabel(conflict: OutboxEntity): String = when (conflict.intent) {
    "task.update" -> "任务修改"
    "task.complete" -> "任务完成状态"
    "task.schedule" -> "任务日期"
    "calendar_series.update" -> "日程修改"
    else -> "本地改动"
}
