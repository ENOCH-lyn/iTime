package com.itime.app.ui.ai

import com.itime.app.data.remote.AiToolProposalDto
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/**
 * Pure formatting helpers for AI approval cards.
 *
 * Kept outside the composable so the payload-to-label mapping can be unit
 * tested without an Android runtime.
 */
object ProposalFormat {

    fun toolLabel(toolName: String): String = when (toolName) {
        "create_task" -> "新建任务"
        "update_task" -> "修改任务"
        "complete_task" -> "完成任务"
        "delete_task" -> "删除任务"
        "create_calendar_series" -> "新建日程"
        "update_calendar_series" -> "修改日程"
        "delete_calendar_series" -> "删除日程"
        "create_calendar_exception" -> "调整单次课程"
        "create_timetable_courses" -> "导入课表"
        else -> "AI 修改建议"
    }

    /** "2026-11-12 · 14:00–14:45 · 45 分钟" or null when the payload is empty. */
    fun details(proposal: AiToolProposalDto): String? {
        val details = runCatching {
            proposal.preview?.get("details")?.jsonObject
        }.getOrNull() ?: return null
        fun text(key: String): String? = runCatching {
            details[key]?.jsonPrimitive?.content
        }.getOrNull()?.takeIf { it.isNotBlank() && it != "null" }
        val date = text("local_date")
        val start = text("starts_at")?.take(5)
        val end = text("ends_at")?.take(5)
        val minutes = text("estimated_minutes")
        val parts = buildList {
            if (date != null) add(date)
            if (start != null) add(if (end != null) "$start–$end" else start)
            if (minutes != null) add("$minutes 分钟")
        }
        return parts.joinToString(" · ").takeIf { it.isNotBlank() }
    }

    fun summary(proposal: AiToolProposalDto): String = runCatching {
        proposal.preview?.get("summary")?.jsonPrimitive?.content
    }.getOrNull()?.takeIf { it.isNotBlank() }
        ?: "AI 准备修改你的数据，确认后才会生效。"
}
