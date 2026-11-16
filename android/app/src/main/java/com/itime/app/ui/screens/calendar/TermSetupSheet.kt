package com.itime.app.ui.screens.calendar

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.DatePicker
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberDatePickerState
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.time.temporal.TemporalAdjusters

/**
 * 课表识别前的最小化学期设置：只需要名称与第一周周一。
 *
 * 服务端用「第一周周一 + 学期结束日」推算课程周次，没有学期时识别出的课程
 * 全是缺日期的无效候选，所以这里先建学期再选图片。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TermSetupSheet(
    defaultTimeZone: ZoneId,
    onCreate: suspend (name: String, firstWeekMonday: LocalDate, weeks: Int) -> Unit,
    onReady: () -> Unit,
    onDismiss: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val today = LocalDate.now(defaultTimeZone)
    var name by remember { mutableStateOf("本学期") }
    var monday by remember {
        mutableStateOf(today.with(TemporalAdjusters.previousOrSame(java.time.DayOfWeek.MONDAY)))
    }
    var weeks by remember { mutableStateOf(20) }
    var pickerOpen by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheetState) {
        Column(
            Modifier
                .fillMaxWidth()
                .padding(horizontal = 20.dp)
                .padding(bottom = 28.dp),
        ) {
            Text("先设置本学期", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(6.dp))
            Text(
                "课表截图只有星期和节次，识别时要用学期的第一周推算具体日期。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(16.dp))
            OutlinedTextField(
                value = name,
                onValueChange = { name = it },
                label = { Text("学期名称") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(12.dp))
            TextButton(onClick = { pickerOpen = true }) {
                Text(
                    "第一周周一：${monday.format(DateTimeFormatter.ofPattern("yyyy 年 M 月 d 日"))}",
                )
            }
            Row(Modifier.fillMaxWidth()) {
                Text(
                    "共 $weeks 周",
                    style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.weight(1f),
                )
                TextButton(onClick = { if (weeks > 1) weeks -= 1 }) { Text("-1") }
                TextButton(onClick = { if (weeks < 30) weeks += 1 }) { Text("+1") }
            }
            error?.let {
                Spacer(Modifier.height(8.dp))
                Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
            }
            Spacer(Modifier.height(12.dp))
            Button(
                onClick = {
                    if (busy) return@Button
                    if (name.isBlank()) {
                        error = "请填写学期名称。"
                        return@Button
                    }
                    busy = true
                    error = null
                    scope.launch {
                        runCatching { onCreate(name.trim(), monday, weeks) }
                            .onSuccess { onReady() }
                            .onFailure { error = it.message ?: "学期创建失败，请稍后重试。" }
                        busy = false
                    }
                },
                enabled = !busy,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(if (busy) "正在创建…" else "创建并选择课表截图")
            }
            TextButton(onClick = onDismiss, modifier = Modifier.fillMaxWidth()) { Text("取消") }
        }
    }

    if (pickerOpen) {
        val initialMillis = monday.atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli()
        val state = rememberDatePickerState(initialSelectedDateMillis = initialMillis)
        DatePickerDialog(
            onDismissRequest = { pickerOpen = false },
            confirmButton = {
                TextButton(onClick = {
                    val millis = state.selectedDateMillis
                    if (millis != null) {
                        monday = Instant.ofEpochMilli(millis)
                            .atZone(ZoneId.of("UTC"))
                            .toLocalDate()
                    }
                    pickerOpen = false
                }) { Text("确定") }
            },
            dismissButton = {
                TextButton(onClick = { pickerOpen = false }) { Text("取消") }
            },
        ) { DatePicker(state = state) }
    }
}
