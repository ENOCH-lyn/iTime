package com.itime.app.ui.screens.ai

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
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.ChatBubbleOutline
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.itime.app.AppContainer
import com.itime.app.data.SyncState
import com.itime.app.ui.components.SyncChip

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AiScreen(container: AppContainer, syncState: SyncState) {
    val settings by container.calendar.observeTimeSettings().collectAsStateWithLifecycle(null)
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("AI") },
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
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Spacer(Modifier.height(8.dp))
            Card(
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.secondaryContainer,
                ),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(Modifier.padding(16.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(
                            Icons.Outlined.ChatBubbleOutline,
                            contentDescription = null,
                            tint = MaterialTheme.colorScheme.onSecondaryContainer,
                        )
                        Spacer(Modifier.height(0.dp))
                        Text(
                            "  对话式 AI 需要联网",
                            style = MaterialTheme.typography.titleMedium,
                            color = MaterialTheme.colorScheme.onSecondaryContainer,
                        )
                    }
                    Spacer(Modifier.height(8.dp))
                    Text(
                        "AI 对话与课表识别在服务端执行，离线时不可用；" +
                            "任务的增删改和日历查看都可以离线完成。",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSecondaryContainer,
                    )
                }
            }
            Text(
                "当前版本先保证离线数据与同步可靠，AI 对话界面在后续迭代接入。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                "时区：${settings?.timeZone ?: "系统默认"}",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}
