package com.itime.app.ui.screens.ai

import androidx.compose.foundation.clickable
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
import androidx.compose.material.icons.outlined.AttachFile
import androidx.compose.material.icons.outlined.ChatBubbleOutline
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material.icons.outlined.Image
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.rememberModalBottomSheetState
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.itime.app.AppContainer
import com.itime.app.data.SyncPhase
import com.itime.app.data.SyncState
import com.itime.app.data.remote.AiConversationDto
import com.itime.app.data.remote.AiMessageDto
import com.itime.app.data.remote.AiToolProposalDto
import com.itime.app.data.repository.AiRepository
import com.itime.app.ui.ai.AiErrorBar
import com.itime.app.ui.ai.ProposalCard
import com.itime.app.ui.ai.ProposalFormat
import com.itime.app.ui.components.EmptyState
import com.itime.app.ui.components.SyncChip
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.io.IOException

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AiScreen(container: AppContainer, syncState: SyncState) {
    val ai = remember { AiRepository(container.api) }
    val scope = rememberCoroutineScope()
    val listState = rememberLazyListState()
    val context = LocalContext.current
    var conversation by remember { mutableStateOf<AiConversationDto?>(null) }
    var messages by remember { mutableStateOf<List<AiMessageDto>>(emptyList()) }
    var proposal by remember { mutableStateOf<AiToolProposalDto?>(null) }
    var input by remember { mutableStateOf("") }
    var sending by remember { mutableStateOf(false) }
    var lastAttempt by remember { mutableStateOf<SendAttempt?>(null) }
    var deciding by remember { mutableStateOf(false) }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var attachSheet by remember { mutableStateOf(false) }
    var attaching by remember { mutableStateOf(false) }
    var attachment by remember { mutableStateOf<ChatAttachment?>(null) }
    var attachError by remember { mutableStateOf<String?>(null) }
    val expanded = remember { mutableStateListOf<String>() }
    val online = syncState.phase != SyncPhase.Offline

    val pickImage = androidx.activity.compose.rememberLauncherForActivityResult(
        contract = androidx.activity.result.contract.ActivityResultContracts.PickVisualMedia(),
    ) { uri ->
        if (uri != null) {
            scope.launch {
                attaching = true
                attachError = null
                runCatching {
                    val prepared = withContext(Dispatchers.IO) { AiImageFiles.prepare(context, uri) }
                    ai.uploadImage(prepared.bytes, prepared.fileName, prepared.contentType)
                }.onSuccess { mediaId ->
                    attachment = ChatAttachment.Image(mediaId, "课表截图 / 图片")
                    if (input.isBlank()) {
                        input = "请识别这张图片；如果是课表，帮我整理成本学期课表。"
                    }
                }.onFailure { attachError = it.message ?: "图片上传失败，请重试。" }
                attaching = false
            }
        }
    }
    val pickDocument = androidx.activity.compose.rememberLauncherForActivityResult(
        contract = androidx.activity.result.contract.ActivityResultContracts.OpenDocument(),
    ) { uri ->
        if (uri != null) {
            scope.launch {
                attaching = true
                attachError = null
                runCatching {
                    val document = withContext(Dispatchers.IO) { readDocument(context, uri) }
                    val text = ai.extractDocument(document.bytes, document.name, document.mime)
                    document to text
                }.onSuccess { (document, text) ->
                    attachment = ChatAttachment.Document(text, document.name, text.length)
                    if (input.isBlank()) input = "请阅读附件里的内容。"
                }.onFailure { attachError = it.message ?: "文件解析失败，请重试。" }
                attaching = false
            }
        }
    }

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

    fun submit(payload: String, echo: String, imageMediaId: String?) {
        val conv = conversation ?: return
        error = null
        sending = true
        lastAttempt = SendAttempt(payload, echo, imageMediaId)
        proposal = null
        messages = messages + AiMessageDto(
            id = "local-${System.currentTimeMillis()}",
            conversation_id = conv.id,
            role = "user",
            status = "complete",
            content = echo,
            sequence = messages.size,
        )
        scope.launch {
            try {
                val latest = ai.send(conv, payload, imageMediaId) { progress ->
                    if (progress.isNotEmpty()) messages = progress
                }
                messages = latest
                conversation = ai.ensureConversation()
                refreshProposal()
                container.sync.requestSync("ai-reply")
            } catch (failure: Exception) {
                error = failure.message ?: "AI 请求失败，请稍后重试。"
            } finally {
                sending = false
            }
        }
    }

    fun send() {
        if (sending || deciding || attaching) return
        val current = attachment
        val text = input.trim().ifEmpty {
            when (current) {
                is ChatAttachment.Image -> "请识别这张图片；如果是课表，帮我整理成本学期课表。"
                is ChatAttachment.Document -> "请阅读附件里的内容。"
                null -> ""
            }
        }
        if (text.isEmpty()) return
        // 文档附件把抽出的纯文本拼进消息；图片则用 media_id 交给服务端视觉通道。
        val payload = when (current) {
            is ChatAttachment.Document ->
                "$text\n\n【附件：${current.displayName}】\n${current.text}"
            else -> text
        }
        // 气泡里只显示用户自己写的话，避免把整份文档正文铺在聊天记录里。
        input = ""
        attachment = null
        attachError = null
        submit(payload, text, (current as? ChatAttachment.Image)?.mediaId)
    }

    fun decide(approve: Boolean) {
        val current = proposal ?: return
        val conv = conversation ?: return
        if (deciding) return
        deciding = true
        error = null
        scope.launch {
            try {
                val latest = ai.decide(conv.id, current, approve, knownMessages = messages.size) { progress ->
                    if (progress.isNotEmpty()) messages = progress
                }
                messages = latest
                proposal = null
                conversation = ai.ensureConversation()
                container.sync.requestSync("ai-decided")
            } catch (failure: Exception) {
                error = failure.message ?: "处理提案失败。"
            } finally {
                deciding = false
            }
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
                    Column(Modifier.padding(horizontal = 12.dp, vertical = 8.dp)) {
                        AttachmentChip(
                            attachment = attachment,
                            attaching = attaching,
                            error = attachError,
                            onRemove = {
                                attachment = null
                                attachError = null
                            },
                        )
                        Row(
                            Modifier
                                .fillMaxWidth()
                                .imePadding(),
                            verticalAlignment = Alignment.Bottom,
                        ) {
                            IconButton(
                                onClick = { attachSheet = true },
                                enabled = online && !sending && !deciding && !attaching,
                            ) {
                                Icon(Icons.Outlined.AttachFile, contentDescription = "添加附件")
                            }
                            OutlinedTextField(
                                value = input,
                                onValueChange = { input = it },
                                placeholder = {
                                    Text(
                                        if (online) "问点什么，或上传课表截图/文档" else "离线时不可用",
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
                                enabled = online && !sending && !deciding && !attaching &&
                                    (input.isNotBlank() || attachment != null),
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
                    message = "可以直接说“明天下午三点开组会”“把高数复习安排到周末”，" +
                        "也可以点左下角回形针上传课表截图或 PDF/Word 文档。",
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
            error?.let { message ->
                AiErrorBar(
                    message = message,
                    canRetry = lastAttempt != null,
                    retryEnabled = !sending && !deciding,
                    onRetry = {
                        val previous = lastAttempt ?: return@AiErrorBar
                        error = null
                        submit(previous.payload, previous.echo, previous.imageMediaId)
                    },
                    onDismiss = { error = null },
                    modifier = Modifier.align(Alignment.BottomCenter),
                )
            }
        }
    }

    if (attachSheet) {
        val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
        ModalBottomSheet(
            onDismissRequest = { attachSheet = false },
            sheetState = sheetState,
        ) {
            Column(Modifier.fillMaxWidth().padding(bottom = 24.dp)) {
                Text(
                    "添加附件",
                    style = MaterialTheme.typography.titleMedium,
                    modifier = Modifier.padding(horizontal = 20.dp, vertical = 8.dp),
                )
                ListItem(
                    headlineContent = { Text("图片 / 课表截图") },
                    supportingContent = { Text("直接读图；课表会自动整理成课程草稿") },
                    leadingContent = { Icon(Icons.Outlined.Image, contentDescription = null) },
                    modifier = Modifier.clickable(enabled = online && !attaching) {
                        attachSheet = false
                        pickImage.launch(
                            androidx.activity.result.PickVisualMediaRequest(
                                androidx.activity.result.contract.ActivityResultContracts
                                    .PickVisualMedia.ImageOnly,
                            ),
                        )
                    },
                )
                ListItem(
                    headlineContent = { Text("文档（PDF / Word / 文本）") },
                    supportingContent = { Text("抽出文字后一起发给 AI") },
                    leadingContent = { Icon(Icons.Outlined.Description, contentDescription = null) },
                    modifier = Modifier.clickable(enabled = online && !attaching) {
                        attachSheet = false
                        pickDocument.launch(
                            arrayOf(
                                "application/pdf",
                                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                "text/plain",
                                "text/markdown",
                                "text/csv",
                            ),
                        )
                    },
                )
            }
        }
    }
}

/** 待发送附件：图片带 media_id，文档带抽出的纯文本。 */
private sealed interface ChatAttachment {
    val displayName: String

    data class Image(val mediaId: String, override val displayName: String) : ChatAttachment

    data class Document(
        val text: String,
        override val displayName: String,
        val characters: Int,
    ) : ChatAttachment
}

/** 上一次发送的内容，用于错误重试时原样重发（附件已从界面清掉）。 */
private data class SendAttempt(
    val payload: String,
    val echo: String,
    val imageMediaId: String?,
)

private data class PickedDocument(val bytes: ByteArray, val name: String, val mime: String)

private fun readDocument(context: android.content.Context, uri: android.net.Uri): PickedDocument {
    val resolver = context.contentResolver
    val name = resolver
        .query(uri, arrayOf(android.provider.OpenableColumns.DISPLAY_NAME), null, null, null)
        ?.use { cursor -> if (cursor.moveToFirst()) cursor.getString(0) else null }
        ?.takeIf { it.isNotBlank() }
        ?: "document"
    val mime = resolver.getType(uri) ?: "application/octet-stream"
    val bytes = resolver.openInputStream(uri)?.use { it.readBytes() }
        ?: throw IOException("无法读取所选文件")
    if (bytes.size > 10 * 1024 * 1024) throw IOException("文件不能超过 10 MB")
    return PickedDocument(bytes, name, mime)
}

@Composable
private fun AttachmentChip(
    attachment: ChatAttachment?,
    attaching: Boolean,
    error: String?,
    onRemove: () -> Unit,
) {
    when {
        attaching -> Row(
            Modifier
                .fillMaxWidth()
                .padding(vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
            Spacer(Modifier.width(8.dp))
            Text("正在处理附件…", style = MaterialTheme.typography.labelMedium)
        }
        attachment != null -> Surface(
            color = MaterialTheme.colorScheme.secondaryContainer,
            shape = RoundedCornerShape(12.dp),
            modifier = Modifier
                .fillMaxWidth()
                .padding(vertical = 6.dp),
        ) {
            Row(
                Modifier.padding(start = 12.dp, end = 4.dp, top = 4.dp, bottom = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(
                    if (attachment is ChatAttachment.Image) {
                        Icons.Outlined.Image
                    } else {
                        Icons.Outlined.Description
                    },
                    contentDescription = null,
                    modifier = Modifier.size(18.dp),
                )
                Spacer(Modifier.width(8.dp))
                Column(Modifier.weight(1f)) {
                    Text(
                        attachment.displayName,
                        style = MaterialTheme.typography.labelLarge,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Text(
                        when (attachment) {
                            is ChatAttachment.Image -> "已上传，发送时交给 AI 读图"
                            is ChatAttachment.Document -> "已解析 ${attachment.characters} 字"
                        },
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                IconButton(onClick = onRemove) {
                    Icon(Icons.Outlined.Close, contentDescription = "移除附件")
                }
            }
        }
        error != null -> Text(
            error,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.error,
            modifier = Modifier.padding(vertical = 4.dp),
        )
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
