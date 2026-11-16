package com.itime.app.ui.screens.calendar

import android.net.Uri
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.itime.app.core.Times
import com.itime.app.data.remote.ApiException
import com.itime.app.data.remote.ImportCandidateDto
import com.itime.app.data.remote.ImportDetailDto
import com.itime.app.data.repository.CalendarImportRepository
import com.itime.app.data.repository.ImportStage
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private sealed interface ImportUiState {
    data object Reading : ImportUiState
    data class Busy(val stage: ImportStage) : ImportUiState
    data class Review(val detail: ImportDetailDto) : ImportUiState
    data class Done(val imported: Int, val skipped: Int) : ImportUiState
    data class Failed(val message: String) : ImportUiState
}

/**
 * 课表截图导入面板：上传 → 识别 → 逐条预览 → 一次确认写入。
 *
 * 识别由服务端 AI worker 完成，期间只保留一个批次；确认后服务端直接写
 * calendar_series，客户端靠一次强制同步把新课表拉进本地库。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CalendarImportSheet(
    repository: CalendarImportRepository,
    uri: Uri,
    onDismiss: () -> Unit,
    onImported: (Int) -> Unit = {},
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    var state by remember(uri) { mutableStateOf<ImportUiState>(ImportUiState.Reading) }
    var attempt by remember(uri) { mutableIntStateOf(0) }
    var confirming by remember(uri) { mutableStateOf(false) }

    LaunchedEffect(uri, attempt) {
        state = ImportUiState.Reading
        val payload = runCatching { withContext(Dispatchers.IO) { ImportImage.load(context, uri) } }
        val image = payload.getOrElse { error ->
            state = ImportUiState.Failed(error.message ?: "无法读取所选图片")
            return@LaunchedEffect
        }
        val result = runCatching {
            repository.recognize(image.bytes, image.fileName, image.contentType) {
                state = ImportUiState.Busy(it)
            }
        }
        state = result.fold(
            onSuccess = { ImportUiState.Review(it) },
            onFailure = { ImportUiState.Failed(it.readableMessage()) },
        )
    }

    fun confirm(detail: ImportDetailDto) {
        if (confirming) return
        confirming = true
        scope.launch {
            val result = runCatching { repository.confirm(detail) }
            result.fold(
                onSuccess = { imported ->
                    val skipped = detail.candidates.count { it.state != "valid" }
                    state = ImportUiState.Done(imported, skipped)
                    onImported(imported)
                },
                onFailure = { state = ImportUiState.Failed(it.readableMessage()) },
            )
            confirming = false
        }
    }

    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheetState) {
        Column(
            Modifier
                .fillMaxWidth()
                .padding(horizontal = 20.dp)
                .padding(bottom = 28.dp),
        ) {
            when (val current = state) {
                is ImportUiState.Reading, is ImportUiState.Busy -> BusyBody(current)
                is ImportUiState.Review -> ReviewBody(
                    detail = current.detail,
                    confirming = confirming,
                    onConfirm = { confirm(current.detail) },
                    onCancel = onDismiss,
                )
                is ImportUiState.Done -> DoneBody(current, onClose = onDismiss)
                is ImportUiState.Failed -> FailedBody(
                    message = current.message,
                    onRetry = {
                        attempt += 1
                        confirming = false
                    },
                    onClose = onDismiss,
                )
            }
        }
    }
}

@Composable
private fun BusyBody(state: ImportUiState) {
    Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
        Spacer(Modifier.height(12.dp))
        CircularProgressIndicator(Modifier.size(40.dp))
        Spacer(Modifier.height(16.dp))
        Text(
            if (state is ImportUiState.Busy && state.stage == ImportStage.Uploading) {
                "正在上传课表截图…"
            } else {
                "正在识别课表，通常需要 10–30 秒…"
            },
            style = MaterialTheme.typography.bodyLarge,
        )
        Spacer(Modifier.height(8.dp))
        Text(
            "识别期间可以离开此页面，结果会保留在导入记录里。",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(20.dp))
    }
}

@Composable
private fun ReviewBody(
    detail: ImportDetailDto,
    confirming: Boolean,
    onConfirm: () -> Unit,
    onCancel: () -> Unit,
) {
    val valid = detail.candidates.filter { it.state == "valid" }
    Text("识别到 ${detail.candidates.size} 个课程", style = MaterialTheme.typography.titleMedium)
    Spacer(Modifier.height(4.dp))
    Text(
        "可导入 ${valid.size} 个，其余 ${detail.candidates.size - valid.size} 个会自动跳过。",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    Spacer(Modifier.height(12.dp))
    LazyColumn(Modifier.heightIn(max = 380.dp)) {
        itemsIndexed(detail.candidates, key = { _, item -> item.id }) { index, candidate ->
            CandidateRow(candidate)
            if (index != detail.candidates.lastIndex) HorizontalDivider()
        }
    }
    Spacer(Modifier.height(16.dp))
    Button(
        onClick = onConfirm,
        enabled = valid.isNotEmpty() && !confirming,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Text(
            when {
                confirming -> "正在写入…"
                valid.isEmpty() -> "没有可导入的课程"
                else -> "导入 ${valid.size} 个日程"
            },
        )
    }
    TextButton(onClick = onCancel, modifier = Modifier.fillMaxWidth()) { Text("取消") }
}

@Composable
private fun CandidateRow(candidate: ImportCandidateDto) {
    val draft = candidate.draft
    Row(
        Modifier
            .fillMaxWidth()
            .padding(vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Column(Modifier.weight(1f)) {
            Text(
                draft?.name?.takeIf { it.isNotBlank() } ?: "未命名课程",
                style = MaterialTheme.typography.bodyLarge,
                fontWeight = FontWeight.Medium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                draft.describe(),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
            candidate.error?.message?.takeIf { it.isNotBlank() }?.let { message ->
                Text(
                    message,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.error,
                )
            }
        }
        Text(
            when (candidate.state) {
                "valid" -> "可导入"
                "duplicate" -> "重复"
                "invalid" -> "无效"
                else -> candidate.state
            },
            style = MaterialTheme.typography.labelSmall,
            color = if (candidate.state == "valid") {
                MaterialTheme.colorScheme.primary
            } else {
                MaterialTheme.colorScheme.onSurfaceVariant
            },
        )
    }
}

@Composable
private fun DoneBody(state: ImportUiState.Done, onClose: () -> Unit) {
    Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
        Spacer(Modifier.height(8.dp))
        Icon(
            Icons.Outlined.CheckCircle,
            contentDescription = null,
            tint = MaterialTheme.colorScheme.primary,
            modifier = Modifier.size(40.dp),
        )
        Spacer(Modifier.height(12.dp))
        Text("已导入 ${state.imported} 个日程", style = MaterialTheme.typography.titleMedium)
        if (state.skipped > 0) {
            Spacer(Modifier.height(4.dp))
            Text(
                "另有 ${state.skipped} 项重复或无效，已跳过。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Spacer(Modifier.height(16.dp))
        Button(onClick = onClose, modifier = Modifier.fillMaxWidth()) { Text("完成") }
    }
}

@Composable
private fun FailedBody(message: String, onRetry: () -> Unit, onClose: () -> Unit) {
    Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
        Spacer(Modifier.height(8.dp))
        Icon(
            Icons.Outlined.ErrorOutline,
            contentDescription = null,
            tint = MaterialTheme.colorScheme.error,
            modifier = Modifier.size(36.dp),
        )
        Spacer(Modifier.height(12.dp))
        Text("课表识别未完成", style = MaterialTheme.typography.titleMedium)
        Spacer(Modifier.height(6.dp))
        Text(
            message,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(16.dp))
        Button(onClick = onRetry, modifier = Modifier.fillMaxWidth()) { Text("重试") }
        TextButton(onClick = onClose, modifier = Modifier.fillMaxWidth()) { Text("关闭") }
    }
}

private fun com.itime.app.data.remote.ImportDraftDto?.describe(): String {
    if (this == null) return "缺少可写入的字段"
    val weekdays = listOf("周一", "周二", "周三", "周四", "周五", "周六", "周日")
    val days = recurrence?.weekdays.orEmpty()
        .mapNotNull { weekdays.getOrNull(it - 1) }
        .joinToString("、")
    val start = Times.parseTime(starts_at)?.let { format(it) }
    val end = Times.parseTime(ends_at)?.let { format(it) }
    val scope = when {
        days.isNotBlank() -> days
        recurrence?.frequency == "daily" -> "每天"
        recurrence?.frequency == "weekly" -> "每周"
        else -> "单次"
    }
    val clock = when {
        start == null -> "全天"
        end == null -> start
        else -> "$start–$end"
    }
    val place = location.takeIf { it.isNotBlank() }?.let { " · $it" }.orEmpty()
    return "$scope · $clock$place"
}

private fun format(time: java.time.LocalTime): String =
    time.format(java.time.format.DateTimeFormatter.ofPattern("HH:mm"))

private fun Throwable.readableMessage(): String = when (this) {
    is ApiException -> message
    else -> message ?: "操作未完成，请稍后重试。"
}
