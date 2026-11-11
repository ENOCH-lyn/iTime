package com.itime.app.ui.ai

import com.itime.app.data.remote.AiToolProposalDto
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ProposalFormatTest {
    private fun proposal(
        tool: String = "create_task",
        details: (kotlinx.serialization.json.JsonObjectBuilder.() -> Unit)? = null,
    ): AiToolProposalDto = AiToolProposalDto(
        id = "p-1",
        tool_name = tool,
        preview = buildJsonObject {
            put("summary", "明天 14:00–14:45 安排「整理实验报告」")
            if (details != null) {
                putJsonObject("details", details)
            }
        },
    )

    @Test
    fun `details render date time range and duration`() {
        val card = proposal {
            put("local_date", "2026-11-12")
            put("starts_at", "14:00:00")
            put("ends_at", "14:45:00")
            put("estimated_minutes", 45)
        }
        assertEquals("2026-11-12 · 14:00–14:45 · 45 分钟", ProposalFormat.details(card))
    }

    @Test
    fun `details skip missing fields instead of printing null`() {
        val card = proposal {
            put("local_date", "2026-11-12")
            put("estimated_minutes", 30)
        }
        assertEquals("2026-11-12 · 30 分钟", ProposalFormat.details(card))
    }

    @Test
    fun `title prefers task title then series title`() {
        val taskCard = proposal {
            put("task_title", "整理实验报告")
        }
        assertEquals("任务：整理实验报告", ProposalFormat.title(taskCard))

        val seriesCard = proposal {
            put("series_title", "数据结构")
        }
        assertEquals("日程：数据结构", ProposalFormat.title(seriesCard))

        assertEquals(null, ProposalFormat.title(proposal()))
    }

    @Test
    fun `details are null when the payload has none`() {
        assertNull(ProposalFormat.details(proposal()))
    }

    @Test
    fun `tool label and summary fall back for unknown tools`() {
        val card = proposal(tool = "create_timetable_courses")
        assertEquals("导入课表", ProposalFormat.toolLabel(card.tool_name))
        assertEquals("导入课表", ProposalFormat.toolLabel("create_timetable_courses"))
        assertEquals("AI 修改建议", ProposalFormat.toolLabel("something_new"))
        assertEquals(
            "明天 14:00–14:45 安排「整理实验报告」",
            ProposalFormat.summary(card),
        )
        assertEquals(
            "AI 准备修改你的数据，确认后才会生效。",
            ProposalFormat.summary(AiToolProposalDto(id = "p-2")),
        )
    }
}
