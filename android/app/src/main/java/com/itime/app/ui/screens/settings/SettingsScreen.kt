package com.itime.app.ui.screens.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.clickable
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
import com.itime.app.BuildConfig
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
    val failed by container.database.outboxDao().observeFailed()
        .collectAsStateWithLifecycle(emptyList())
    val settings by container.calendar.observeTimeSettings().collectAsStateWithLifecycle(null)
    val scope = rememberCoroutineScope()
    var serverUrl by remember { mutableStateOf(container.sessionStore.baseUrl()) }
    var showLogout by remember { mutableStateOf(false) }
    var showResync by remember { mutableStateOf(false) }
    var resyncNotice by remember { mutableStateOf<String?>(null) }
    var resolving by remember { mutableStateOf<OutboxEntity?>(null) }
    var showZonePicker by remember { mutableStateOf(false) }
    var serverUrlError by remember { mutableStateOf<String?>(null) }

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
                onClick = { scope.launch { container.sync.syncNow("settings-manual", force = true) } },
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp),
            ) { Text("立即同步") }
            TextButton(
                onClick = { showResync = true },
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp),
            ) { Text("重新核对数据") }
            resyncNotice?.let { notice ->
                Text(
                    notice,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 16.dp),
                )
            }

            if (showResync) {
                AlertDialog(
                    onDismissRequest = { showResync = false },
                    title = { Text("重新核对数据？") },
                    text = {
                        Text(
                            "将从服务端重新拉取任务、项目与日程，覆盖本地已同步数据；" +
                                "本地尚未同步的改动仍保留在上传队列中。",
                        )
                    },
                    confirmButton = {
                        TextButton(
                            onClick = {
                                showResync = false
                                scope.launch {
                                    runCatching { container.sync.resyncSnapshot() }
                                        .onSuccess {
                                            container.sync.requestSync("settings-resync")
                                            resyncNotice = "已重新核对"
                                        }
                                        .onFailure { resyncNotice = it.message ?: "核对失败" }
                                }
                            },
                        ) { Text("开始") }
                    },
                    dismissButton = {
                        TextButton(onClick = { showResync = false }) { Text("取消") }
                    },
                )
            }

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
                                operationLabel(conflict),
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

            if (failed.isNotEmpty()) {
                SectionHeader("同步失败")
                Text(
                    "这些改动无法自动发送，可以重试或丢弃。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 16.dp),
                )
                failed.forEach { operation ->
                    Card(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 16.dp, vertical = 4.dp),
                    ) {
                        Column(Modifier.padding(12.dp)) {
                            Text(operationLabel(operation), style = MaterialTheme.typography.bodyMedium)
                            Text(
                                operation.lastErrorMessage ?: "服务器拒绝了这次改动。",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.error,
                            )
                            Row(
                                Modifier.padding(top = 8.dp),
                                horizontalArrangement = Arrangement.spacedBy(8.dp),
                            ) {
                                OutlinedButton(onClick = {
                                    scope.launch { container.sync.retryOperation(operation.operationId) }
                                }) { Text("重试") }
                                OutlinedButton(onClick = {
                                    scope.launch { container.sync.discardOperation(operation.operationId) }
                                }) { Text("丢弃") }
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
            serverUrlError?.let { message ->
                Text(
                    message,
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(horizontal = 16.dp),
                )
            }
            TextButton(
                onClick = {
                    val normalized = com.itime.app.domain.BaseUrl.normalize(serverUrl)
                    if (normalized == null) {
                        serverUrlError = "请输入 http(s)://主机:端口 形式的地址。"
                        return@TextButton
                    }
                    serverUrlError = null
                    serverUrl = normalized
                    scope.launch {
                        container.auth.updateBaseUrl(normalized)
                    }
                },
                modifier = Modifier.padding(horizontal = 8.dp),
            ) { Text("保存地址") }

            SectionHeader("时间")
            ListItem(
                headlineContent = { Text("时区") },
                supportingContent = { Text(settings?.timeZone ?: ZoneId.systemDefault().id) },
                trailingContent = {
                    TextButton(onClick = { showZonePicker = true }) { Text("修改") }
                },
            )
            ListItem(
                headlineContent = { Text("本周起始") },
                supportingContent = { Text("周一") },
            )

            SectionHeader("关于")
            ListItem(
                headlineContent = { Text("iTime Android") },
                supportingContent = {
                    Text(
                        "版本 ${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE}) · " +
                            "原生 Material 3 客户端 · 离线优先 · 与网页端同步",
                    )
                },
            )
            Spacer(Modifier.height(32.dp))
        }
    }

    if (showZonePicker) {
        val options = listOf(
            "Asia/Shanghai" to "中国标准时间（北京）",
            "Asia/Tokyo" to "日本标准时间",
            "Asia/Singapore" to "新加坡时间",
            "Europe/London" to "英国时间",
            "America/New_York" to "美国东部时间",
            "UTC" to "协调世界时",
        )
        AlertDialog(
            onDismissRequest = { showZonePicker = false },
            title = { Text("选择时区") },
            text = {
                Column {
                    options.forEach { (id, label) ->
                        ListItem(
                            headlineContent = { Text(label) },
                            supportingContent = { Text(id) },
                            trailingContent = {
                                if ((settings?.timeZone ?: ZoneId.systemDefault().id) == id) {
                                    Text("当前")
                                }
                            },
                            modifier = Modifier.clickable {
                                scope.launch {
                                    container.calendar.updateTimeZone(id)
                                    showZonePicker = false
                                }
                            },
                        )
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = { showZonePicker = false }) { Text("关闭") }
            },
        )
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

private fun operationLabel(conflict: OutboxEntity): String = when (conflict.intent) {
    "task.update" -> "任务修改"
    "task.complete" -> "任务完成状态"
    "task.schedule" -> "任务日期"
    "calendar_series.update" -> "日程修改"
    "task.step.create" -> "新增步骤"
    "task.step.update" -> "步骤勾选"
    "task.step.delete" -> "删除步骤"
    "task.step.reorder" -> "步骤排序"
    "time_settings.update" -> "时区设置"
    else -> "本地改动"
}
