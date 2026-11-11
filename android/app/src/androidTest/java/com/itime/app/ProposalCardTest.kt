package com.itime.app

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.itime.app.data.remote.AiToolProposalDto
import com.itime.app.ui.ai.ProposalCard
import com.itime.app.ui.theme.ItimeTheme
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonObject
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** Approve/reject contract of the AI tool proposal card. */
@RunWith(AndroidJUnit4::class)
class ProposalCardTest {

    @get:Rule
    val composeRule = createComposeRule()

    private fun proposal(): AiToolProposalDto = AiToolProposalDto(
        id = "proposal-1",
        tool_name = "create_task",
        preview = buildJsonObject {
            put("summary", "明天 14:00–14:45 安排「整理实验报告」")
            putJsonObject("details") {
                put("local_date", "2026-11-12")
                put("starts_at", "14:00:00")
                put("ends_at", "14:45:00")
                put("estimated_minutes", 45)
            }
        },
    )

    private fun render(busy: Boolean = false, onApprove: () -> Unit = {}, onReject: () -> Unit = {}) {
        composeRule.setContent {
            ItimeTheme(dynamicColor = false) {
                ProposalCard(
                    proposal = proposal(),
                    busy = busy,
                    onApprove = onApprove,
                    onReject = onReject,
                )
            }
        }
    }

    @Test
    fun cardShowsToolSummaryAndDetails() {
        render()
        composeRule.onNodeWithText("新建任务").assertIsDisplayed()
        composeRule.onNodeWithText("明天 14:00–14:45 安排「整理实验报告」").assertIsDisplayed()
        composeRule.onNodeWithText("2026-11-12 · 14:00–14:45 · 45 分钟").assertIsDisplayed()
    }

    @Test
    fun approveButtonInvokesCallback() {
        var approved = false
        render(onApprove = { approved = true })
        composeRule.onNodeWithText("批准").assertIsEnabled().performClick()
        assertTrue(approved)
    }

    @Test
    fun rejectButtonInvokesCallback() {
        var rejected = false
        render(onReject = { rejected = true })
        composeRule.onNodeWithText("拒绝").assertIsEnabled().performClick()
        assertTrue(rejected)
    }

    @Test
    fun busyCardDisablesBothActions() {
        render(busy = true)
        composeRule.onNodeWithText("拒绝").assertIsNotEnabled()
    }
}
