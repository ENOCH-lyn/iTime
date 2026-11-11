package com.itime.app.ui.screens.calendar

import androidx.compose.foundation.background
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
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.outlined.ChevronLeft
import androidx.compose.material.icons.outlined.ChevronRight
import androidx.compose.material.icons.outlined.Event
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.itime.app.AppContainer
import com.itime.app.core.Times
import com.itime.app.data.SyncState
import com.itime.app.domain.Agenda
import com.itime.app.domain.AgendaItem
import com.itime.app.ui.components.EmptyState
import com.itime.app.ui.components.SyncChip
import kotlinx.coroutines.launch
import java.time.DayOfWeek
import java.time.LocalDate
import java.time.YearMonth
import java.time.format.DateTimeFormatter
import java.time.format.TextStyle
import java.util.Locale

private enum class CalendarMode(val label: String) { Week("周"), Month("月") }

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CalendarScreen(container: AppContainer, syncState: SyncState) {
    val series by container.calendar.observeSeries().collectAsStateWithLifecycle(emptyList())
    val terms by container.calendar.observeTerms().collectAsStateWithLifecycle(emptyList())
    val exceptions by container.calendar.observeAllExceptions().collectAsStateWithLifecycle(emptyList())
    val additions by container.calendar.observeAdditions().collectAsStateWithLifecycle(emptyList())
    val tasks by container.tasks.observeActive().collectAsStateWithLifecycle(emptyList())
    val occurrences by container.database.occurrenceDao().observeAll()
        .collectAsStateWithLifecycle(emptyList())
    val settings by container.calendar.observeTimeSettings().collectAsStateWithLifecycle(null)
    val zone = Times.zoneOf(settings?.timeZone)
    val today = LocalDate.now(zone)
    val scope = rememberCoroutineScope()

    var mode by remember { mutableStateOf(CalendarMode.Week) }
    var selected by remember { mutableStateOf(today) }
    var anchor by remember { mutableStateOf(today) }
    var detail by remember { mutableStateOf<AgendaItem?>(null) }
    var showSeriesEditor by remember { mutableStateOf(false) }
    var editingSeries by remember { mutableStateOf<com.itime.app.data.local.SeriesEntity?>(null) }

    val weekStart = anchor.with(DayOfWeek.MONDAY)
    val month = YearMonth.from(anchor)
    val gridStart = month.atDay(1).with(DayOfWeek.MONDAY)
    val gridEnd = gridStart.plusDays(41)
    val rangeStart = if (mode == CalendarMode.Week) weekStart else gridStart
    val rangeEnd = if (mode == CalendarMode.Week) weekStart.plusDays(6) else gridEnd

    val items = remember(mode, rangeStart, rangeEnd, series, tasks, exceptions, additions, occurrences, terms) {
        Agenda.build(
            from = rangeStart,
            to = rangeEnd,
            zone = zone,
            series = series,
            terms = terms.associateBy { it.id },
            exceptions = exceptions,
            additions = additions,
            tasks = tasks,
            occurrences = occurrences,
        )
    }
    val byDay = remember(items) { items.groupBy { it.date } }
    val dayItems = byDay[selected].orEmpty()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("日历") },
                actions = {
                    SyncChip(syncState.phase, syncState.pendingCount, syncState.conflictCount)
                    IconButton(onClick = { scope.launch { container.sync.syncNow("calendar-refresh") } }) {
                        Icon(Icons.Outlined.Refresh, contentDescription = "立即同步")
                    }
                },
            )
        },
        floatingActionButton = {
            ExtendedFloatingActionButton(
                onClick = { showSeriesEditor = true },
                icon = { Icon(Icons.Filled.Add, contentDescription = null) },
                text = { Text("新建日程") },
            )
        },
    ) { padding ->
        Column(
            Modifier
                .fillMaxSize()
                .padding(padding),
        ) {
            Row(
                Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                IconButton(onClick = {
                    anchor = if (mode == CalendarMode.Week) anchor.minusWeeks(1) else anchor.minusMonths(1)
                    selected = if (mode == CalendarMode.Week) weekStart.minusWeeks(1) else month.minusMonths(1).atDay(1)
                }) { Icon(Icons.Outlined.ChevronLeft, contentDescription = "上一页") }
                Text(
                    if (mode == CalendarMode.Week) {
                        val end = weekStart.plusDays(6)
                        "${weekStart.monthValue}/${weekStart.dayOfMonth} – ${end.monthValue}/${end.dayOfMonth}"
                    } else {
                        "${month.year} 年 ${month.monthValue} 月"
                    },
                    style = MaterialTheme.typography.titleMedium,
                    modifier = Modifier.weight(1f),
                    textAlign = TextAlign.Center,
                )
                IconButton(onClick = {
                    anchor = if (mode == CalendarMode.Week) anchor.plusWeeks(1) else anchor.plusMonths(1)
                    selected = if (mode == CalendarMode.Week) weekStart.plusWeeks(1) else month.plusMonths(1).atDay(1)
                }) { Icon(Icons.Outlined.ChevronRight, contentDescription = "下一页") }
                FilledTonalButton(onClick = {
                    anchor = today
                    selected = today
                }) { Text("今天") }
            }
            SingleChoiceSegmentedButtonRow(
                Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp),
            ) {
                CalendarMode.entries.forEachIndexed { index, item ->
                    SegmentedButton(
                        selected = mode == item,
                        onClick = { mode = item },
                        shape = SegmentedButtonDefaults.itemShape(index, CalendarMode.entries.size),
                    ) { Text(item.label) }
                }
            }
            Spacer(Modifier.height(8.dp))
            if (mode == CalendarMode.Week) {
                Row(
                    Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 12.dp),
                    horizontalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    (0..6).forEach { offset ->
                        val day = weekStart.plusDays(offset.toLong())
                        DayCell(
                            day = day,
                            selected = day == selected,
                            count = byDay[day].orEmpty().size,
                            modifier = Modifier.weight(1f),
                        ) { selected = day }
                    }
                }
            } else {
                WeekdayHeader()
                MonthGrid(
                    gridStart = gridStart,
                    month = month,
                    selected = selected,
                    byDay = byDay,
                ) { selected = it }
            }
            Spacer(Modifier.height(8.dp))
            Row(
                Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    "${selected.monthValue} 月 ${selected.dayOfMonth} 日 · " +
                        selected.dayOfWeek.getDisplayName(TextStyle.FULL, Locale.CHINA),
                    style = MaterialTheme.typography.titleSmall,
                    modifier = Modifier.weight(1f),
                )
                Text(
                    "${dayItems.size} 项",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if (dayItems.isEmpty()) {
                EmptyState(
                    icon = Icons.Outlined.Event,
                    title = "这一天没有安排",
                    message = "点右下角新建日程，或从任务页添加任务。",
                )
            } else {
                LazyColumn(
                    Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(start = 16.dp, end = 16.dp, bottom = 96.dp),
                ) {
                    items(dayItems, key = { it.id }) { item ->
                        CalendarItemRow(item) { detail = item }
                    }
                }
            }
        }
    }

    if (showSeriesEditor) {
        SeriesEditorSheet(
            repository = container.calendar,
            initialDate = selected,
            onDismiss = { showSeriesEditor = false },
        )
    }

    editingSeries?.let { series ->
        SeriesEditorSheet(
            repository = container.calendar,
            initialDate = selected,
            initial = com.itime.app.data.repository.seriesDraftFrom(series),
            existingId = series.id,
            onDismiss = { editingSeries = null },
        )
    }

    detail?.let { item ->
        val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
        ModalBottomSheet(
            onDismissRequest = { detail = null },
            sheetState = sheetState,
        ) {
            Column(
                Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 24.dp)
                    .padding(bottom = 32.dp),
            ) {
                Text(item.title, style = MaterialTheme.typography.titleLarge)
                Spacer(Modifier.height(8.dp))
                Text(
                    buildString {
                        append(item.startTime?.format(DateTimeFormatter.ofPattern("HH:mm")) ?: "全天")
                        item.endTime?.let { append(" – ${it.format(DateTimeFormatter.ofPattern("HH:mm"))}") }
                        append(" · ${item.date.format(DateTimeFormatter.ofPattern("M 月 d 日"))}")
                    },
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (item.subtitle.isNotBlank()) {
                    Spacer(Modifier.height(4.dp))
                    Text(item.subtitle, style = MaterialTheme.typography.bodyMedium)
                }
                if (item.location.isNotBlank()) {
                    Spacer(Modifier.height(4.dp))
                    Text("地点：${item.location}", style = MaterialTheme.typography.bodyMedium)
                }
                Spacer(Modifier.height(16.dp))
                if (item.sourceKind == "task") {
                    Button(
                        onClick = {
                            scope.launch {
                                container.tasks.setCompleted(item.sourceId, true)
                                detail = null
                            }
                        },
                        modifier = Modifier.fillMaxWidth(),
                    ) { Text("标记完成") }
                    TextButton(
                        onClick = {
                            scope.launch {
                                container.tasks.trash(item.sourceId)
                                detail = null
                            }
                        },
                        modifier = Modifier.fillMaxWidth(),
                    ) { Text("删除任务") }
                } else {
                    val series = series.firstOrNull { it.id == item.sourceId }
                    if (series != null && series.sourceType == "manual") {
                        Button(
                            onClick = {
                                editingSeries = series
                                detail = null
                            },
                            modifier = Modifier.fillMaxWidth(),
                        ) { Text("编辑日程") }
                    }
                    TextButton(
                        onClick = {
                            scope.launch {
                                container.calendar.deleteSeries(item.sourceId)
                                detail = null
                            }
                        },
                        modifier = Modifier.fillMaxWidth(),
                    ) { Text("删除整个日程") }
                }
            }
        }
    }
}

@Composable
private fun WeekdayHeader() {
    Row(
        Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp),
    ) {
        listOf("一", "二", "三", "四", "五", "六", "日").forEach { label ->
            Text(
                label,
                style = MaterialTheme.typography.labelSmall,
                textAlign = TextAlign.Center,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.weight(1f),
            )
        }
    }
}

@Composable
private fun MonthGrid(
    gridStart: LocalDate,
    month: YearMonth,
    selected: LocalDate,
    byDay: Map<LocalDate, List<AgendaItem>>,
    onSelect: (LocalDate) -> Unit,
) {
    Column(Modifier.padding(horizontal = 12.dp)) {
        repeat(6) { row ->
            Row(Modifier.fillMaxWidth()) {
                repeat(7) { column ->
                    val day = gridStart.plusDays((row * 7 + column).toLong())
                    val inMonth = day.month == month.month
                    Box(
                        Modifier
                            .weight(1f)
                            .height(52.dp)
                            .padding(2.dp),
                        contentAlignment = Alignment.Center,
                    ) {
                        Surface(
                            shape = CircleShape,
                            color = if (day == selected) {
                                MaterialTheme.colorScheme.primary
                            } else {
                                MaterialTheme.colorScheme.surface
                            },
                            modifier = Modifier.size(40.dp),
                            onClick = { onSelect(day) },
                        ) {
                            Column(
                                horizontalAlignment = Alignment.CenterHorizontally,
                                verticalArrangement = Arrangement.Center,
                            ) {
                                Text(
                                    day.dayOfMonth.toString(),
                                    style = MaterialTheme.typography.bodyMedium,
                                    color = when {
                                        day == selected -> MaterialTheme.colorScheme.onPrimary
                                        inMonth -> MaterialTheme.colorScheme.onSurface
                                        else -> MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.5f)
                                    },
                                )
                                val count = byDay[day].orEmpty().size
                                if (count > 0) {
                                    Box(
                                        Modifier
                                            .padding(top = 2.dp)
                                            .size(4.dp)
                                            .background(
                                                color = if (day == selected) {
                                                    MaterialTheme.colorScheme.onPrimary
                                                } else {
                                                    MaterialTheme.colorScheme.primary
                                                },
                                                shape = CircleShape,
                                            ),
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun DayCell(
    day: LocalDate,
    selected: Boolean,
    count: Int,
    modifier: Modifier = Modifier,
    onClick: () -> Unit,
) {
    Surface(
        onClick = onClick,
        shape = MaterialTheme.shapes.medium,
        color = if (selected) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surface,
        modifier = modifier,
    ) {
        Column(
            Modifier.padding(vertical = 8.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(
                day.dayOfWeek.getDisplayName(TextStyle.NARROW, Locale.CHINA),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(2.dp))
            Text(
                day.dayOfMonth.toString(),
                style = MaterialTheme.typography.titleMedium,
                fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
            )
            if (count > 0) {
                Box(
                    Modifier
                        .padding(top = 4.dp)
                        .size(4.dp)
                        .background(MaterialTheme.colorScheme.primary, CircleShape),
                )
            }
        }
    }
}

@Composable
private fun CalendarItemRow(item: AgendaItem, onClick: () -> Unit) {
    Card(
        onClick = onClick,
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
        colors = CardDefaults.cardColors(
            containerColor = if (item.sourceKind == "series") {
                MaterialTheme.colorScheme.secondaryContainer
            } else {
                MaterialTheme.colorScheme.surfaceVariant
            },
        ),
    ) {
        Row(
            Modifier
                .fillMaxWidth()
                .padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.width(52.dp)) {
                Text(
                    item.startTime?.format(DateTimeFormatter.ofPattern("HH:mm")) ?: "全天",
                    style = MaterialTheme.typography.labelLarge,
                )
                if (item.endTime != null) {
                    Text(
                        item.endTime.format(DateTimeFormatter.ofPattern("HH:mm")),
                        style = MaterialTheme.typography.labelSmall,
                    )
                }
            }
            Column(Modifier.weight(1f)) {
                Text(
                    item.title,
                    style = MaterialTheme.typography.bodyLarge,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                val detail = listOfNotNull(
                    item.subtitle.takeIf { it.isNotBlank() },
                    item.location.takeIf { it.isNotBlank() },
                ).joinToString(" · ")
                if (detail.isNotBlank()) {
                    Text(
                        detail,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
            Text(
                if (item.sourceKind == "series") "日程" else "任务",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}
