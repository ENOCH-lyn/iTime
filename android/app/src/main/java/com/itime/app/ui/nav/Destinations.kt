package com.itime.app.ui.nav

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.CalendarMonth
import androidx.compose.material.icons.outlined.ChatBubbleOutline
import androidx.compose.material.icons.outlined.Checklist
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material.icons.outlined.Today
import androidx.compose.ui.graphics.vector.ImageVector

/** Top-level destinations of the bottom navigation bar (Material 3 max 5). */
enum class TopDestination(
    val route: String,
    val label: String,
    val icon: ImageVector,
) {
    Today("today", "今天", Icons.Outlined.Today),
    Calendar("calendar", "日历", Icons.Outlined.CalendarMonth),
    Tasks("tasks", "任务", Icons.Outlined.Checklist),
    Ai("ai", "AI", Icons.Outlined.ChatBubbleOutline),
    Settings("settings", "设置", Icons.Outlined.Settings),
}

object Routes {
    const val LOGIN = "login"
    const val MAIN = "main"
    const val TASK_EDIT = "task-edit/{taskId}"
    const val TASK_DETAIL = "task/{taskId}"
    const val EVENT_DETAIL = "event/{seriesId}"

    fun taskEdit(taskId: String? = null) =
        if (taskId == null) "task-edit/new" else "task-edit/$taskId"

    fun taskDetail(taskId: String) = "task/$taskId"

    fun eventDetail(seriesId: String) = "event/$seriesId"
}
