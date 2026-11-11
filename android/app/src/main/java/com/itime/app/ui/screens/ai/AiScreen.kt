package com.itime.app.ui.screens.ai

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.Send
import androidx.compose.material.icons.outlined.AddComment
import androidx.compose.material.icons.outlined.ChatBubbleOutline
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.itime.app.AppContainer
import com.itime.app.data.SyncPhase
import com.itime.app.data.SyncState
import com.itime.app.data.remote.AiConversationDto
import com.itime.app.data.remote.AiMessageDto
import com.itime.app.data.remote.AiToolProposalDto
import com.itime.app.data.repository.AiRepository
import com.itime.app.ui.ai.ProposalCard
import com.itime.app.ui.ai.ProposalFormat
import com.itime.app.ui.components.EmptyState
import com.itime.app.ui.components.SyncChip
import kotlinx.coroutines.launch
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AiScreen(container: AppContainer, syncState: SyncState) {
    val ai = remember { AiRepository(container.api) }
    val scope = rememberCoroutineScope()
    val listState = rememberLazyListState()
    var conversation by remember { mutableStateOf<AiConversationDto?>(null) }
    var messages by remember { mutableStateOf<List<AiMessageDto>>(emptyList()) }
    var proposal by remember { mutableStateOf<AiToolProposalDto?>(null) }
    var input by remember { mutableStateOf("") }
    var sending by remember { mutableStateOf(false) }
    var deciding by remember { mutableStateOf(false) }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val expanded = remember { mutableStateListOf<String>() }
    val online = syncState.phase != SyncPhase.Offline

    suspend fun refreshProposal() {
        val last = messages.lastOrNull() ?: run {
            proposal = null
            return
        }
        val next = if (last.role == "assistant") ai.proposalFor(last) else null
        proposal = next?.takeIf { it.status == "pending" }
    }

    suspend fun reload() {
        loading = true
        runCatching {
            val conv = conversation ?: ai.ensureConversation().also { conversation = it }
            messages = ai.messages(conv.id)
            refreshProposal()
        }.onFailure { error = it.message }
        loading = false
    }

    LaunchedEffect(online) {
        if (online && messages.isEmpty()) reload()
    }

    LaunchedEffect(messages.size) {
        if (messages.isNotEmpty()) listState.animateScrollToItem(messages.lastIndex)
    }

    fun send() {
        val text = input.trim()
        if (text.isEmpty() || sending || deciding) return
        val conv = conversation ?: return
        input = ""
        error = null
        sending = true
        proposal = null
        messages = messages + AiMessageDto(
            id = "local-${System.currentTimeMillis()}",
            conversation_id = conv.id,
            role = "user",
            status = "complete",
            content = text,
            sequence = messages.size,
        )
        scope.launch {
            runCatching {
                ai.send(conv, text) { progress -> if (progress.isNotEmpty()) messages = progress }
            }.onSuccess { latest ->
                messages = latest
                conversation = ai.ensureConversation()
                refreshProposal()
                container.sync.requestSync("ai-reply")
            }.onFailure { error = it.message ?: "AI 请求失败，请稍后重试。" }
            sending = false
        }
    }

    fun decide(approve: Boolean) {
        val current = proposal ?: return
        val conv = conversation ?: return
        if (deciding) return
        deciding = true
        error = null
        scope.launch {
            runCatching {
                ai.decide(conv.id, current, approve, knownMessages = messages.size) { progress ->
                    if (progress.isNotEmpty()) messages = progress
                }
            }.onSuccess { latest ->
                messages = latest
                proposal = null
                conversation = ai.ensureConversation()
                container.sync.requestSync("ai-decided")
            }.onFailure { error = it.message ?: "处理提案失败。" }
            deciding = false
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("AI") },
                actions = {
                    SyncChip(syncState.phase, syncState.pendingCount, syncState.conflictCount)
                    IconButton(
                        onClick = {
                            scope.launch {
                                runCatching { ai.createConversation() }
                                    .onSuccess { fresh ->
                                        conversation = fresh
                                        messages = emptyList()
                                        proposal = null
                                        error = null
                                    }
                                    .onFailure { error = it.message }
                            }
                        },
                        enabled = online && !loading,
                    ) { Icon(Icons.Outlined.AddComment, contentDescription = "新建对话") }
                    IconButton(
                        onClick = { scope.launch { reload() } },
                        enabled = online && !loading,
                    ) { Icon(Icons.Outlined.Refresh, contentDescription = "重新加载对话") }
                },
            )
        },
        bottomBar = {
            Column {
                proposal?.let { pending ->
                    ProposalCard(
                        proposal = pending,
                        busy = deciding,
                        onApprove = { decide(true) },
                        onReject = { decide(false) },
                    )
                }
                Surface(tonalElevation = 2.dp) {
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 12.dp, vertical = 8.dp)
                            .imePadding(),
                        verticalAlignment = Alignment.Bottom,
                    ) {
                        OutlinedTextField(
                            value = input,
                            onValueChange = { input = it },
                            placeholder = {
                                Text(
                                    if (online) "问点什么，例如：帮我安排今晚复习高数" else "离线时不可用",
                                )
                            },
                            enabled = online && !sending && !deciding,
                            maxLines = 4,
                            shape = RoundedCornerShape(24.dp),
                            modifier = Modifier.weight(1f),
                        )
                        Spacer(Modifier.width(8.dp))
                        FilledIconButton(
                            onClick = ::send,
                            enabled = online && !sending && !deciding && input.isNotBlank(),
                        ) {
                            if (sending) {
                                CircularProgressIndicator(
                                    modifier = Modifier.size(18.dp),
                                    strokeWidth = 2.dp,
                                    color = MaterialTheme.colorScheme.onPrimary,
                                )
                            } else {
                                Icon(Icons.AutoMirrored.Outlined.Send, contentDescription = "发送")
                            }
                        }
                    }
                }
            }
        },
    ) { padding ->
        Box(
            Modifier
                .fillMaxSize()
                .padding(padding),
        ) {
            when {
                !online -> EmptyState(
                    icon = Icons.Outlined.Refresh,
                    title = "AI 需要联网",
                    message = "AI 对话在服务端执行。恢复网络后会自动同步任务与日历，AI 也可继续使用。",
                )
                loading && messages.isEmpty() -> Box(
                    Modifier.fillMaxSize(),
                    contentAlignment = Alignment.Center,
                ) { CircularProgressIndicator() }
                messages.isEmpty() -> EmptyState(
                    icon = Icons.Outlined.ChatBubbleOutline,
                    title = "开始和 iTime 对话",
                    message = "可以直接说“明天下午三点开组会”“把高数复习安排到周末”；涉及改动会先给你确认卡片。",
                )
                else -> LazyColumn(
                    state = listState,
                    contentPadding = PaddingValues(16.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    items(messages, key = { it.id }) { message ->
                        MessageBubble(
                            message = message,
                            expanded = message.id in expanded,
                            onToggleReasoning = {
                                if (message.id in expanded) expanded.remove(message.id)
                                else expanded.add(message.id)
                            },
                        )
                    }
                }
            }
            error?.let {
                Surface(
                    color = MaterialTheme.colorScheme.errorContainer,
                    modifier = Modifier
                        .fillMaxWidth()
                        .align(Alignment.BottomCenter),
                ) {
                    Row(
                        Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(
                            it,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onErrorContainer,
                            modifier = Modifier.weight(1f),
                        )
                        TextButton(onClick = { error = null }) { Text("知道了") }
                    }
                }
            }
        }
    }
}

@Composable
private fun MessageBubble(
    message: AiMessageDto,
    expanded: Boolean,
    onToggleReasoning: () -> Unit,
) {
    val isUser = message.role == "user"
    Row(
        Modifier.fillMaxWidth(),
        horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start,
    ) {
        Card(
            shape = RoundedCornerShape(
                topStart = 20.dp,
                topEnd = 20.dp,
                bottomStart = if (isUser) 20.dp else 4.dp,
                bottomEnd = if (isUser) 4.dp else 20.dp,
            ),
            colors = CardDefaults.cardColors(
                containerColor = if (isUser) {
                    MaterialTheme.colorScheme.primaryContainer
                } else {
                    MaterialTheme.colorScheme.surfaceVariant
                },
            ),
            modifier = Modifier.fillMaxWidth(0.86f),
        ) {
            Column(Modifier.padding(horizontal = 14.dp, vertical = 10.dp)) {
                if (!isUser && !message.reasoning_content.isNullOrBlank()) {
                    TextButton(
                        onClick = onToggleReasoning,
                        contentPadding = PaddingValues(0.dp),
                    ) {
                        Text(
                            if (expanded) "收起思考过程" else "查看思考过程",
                            style = MaterialTheme.typography.labelSmall,
                        )
                    }
                    if (expanded) {
                        Text(
                            message.reasoning_content.orEmpty(),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 12,
                            overflow = TextOverflow.Ellipsis,
                        )
                        Spacer(Modifier.height(6.dp))
                    }
                }
                Text(
                    message.content.ifBlank {
                        when (message.status) {
                            "queued", "pending" -> "正在思考…"
                            else -> ""
                        }
                    },
                    style = MaterialTheme.typography.bodyMedium,
                    color = if (isUser) {
                        MaterialTheme.colorScheme.onPrimaryContainer
                    } else {
                        MaterialTheme.colorScheme.onSurfaceVariant
                    },
                )
            }
        }
    }
}
