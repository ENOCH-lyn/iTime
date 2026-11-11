package com.itime.app

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.itime.app.data.local.TaskEntity
import com.itime.app.ui.components.TaskDetailSheet
import com.itime.app.ui.theme.ItimeTheme
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** Read-first task detail sheet: metadata and action contract. */
@RunWith(AndroidJUnit4::class)
class TaskDetailSheetTest {

    @get:Rule
    val composeRule = createComposeRule()

    private fun task(repeating: Boolean = false) = TaskEntity(
        id = "task-1",
        title = "整理实验报告",
        projectName = "courseproject",
        dueKind = "datetime",
        dueDate = "2026-11-12",
        dueTime = "14:00:00",
        estimatedMinutes = 45,
        labelsCsv = "学习",
        description = "先写实验目的。",
        recFrequency = if (repeating) "daily" else "none",
    )

    private fun render(
        repeating: Boolean = false,
        onEdit: () -> Unit = {},
        onComplete: () -> Unit = {},
        onDuplicate: () -> Unit = {},
        onArchive: () -> Unit = {},
        onTrash: () -> Unit = {},
    ) {
        composeRule.setContent {
            ItimeTheme(dynamicColor = false) {
                TaskDetailSheet(
                    task = task(repeating),
                    onDismiss = {},
                    onEdit = onEdit,
                    onComplete = onComplete,
                    onDuplicate = onDuplicate,
                    onArchive = onArchive,
                    onTrash = onTrash,
                )
            }
        }
    }

    @Test
    fun sheetShowsTitleMetaLabelsAndDescription() {
        render()
        composeRule.onNodeWithText("整理实验报告").assertIsDisplayed()
        composeRule.onNodeWithText("courseproject · 11 月 12 日 · 14:00 · 45 分钟").assertIsDisplayed()
        composeRule.onNodeWithText("标签：学习").assertIsDisplayed()
        composeRule.onNodeWithText("先写实验目的。").assertIsDisplayed()
    }

    @Test
    fun editAndDuplicateInvokeCallbacks() {
        var edited = false
        var duplicated = false
        render(onEdit = { edited = true }, onDuplicate = { duplicated = true })
        composeRule.onNodeWithText("编辑").performClick()
        assertTrue(edited)
        composeRule.onNodeWithText("复制").performClick()
        assertTrue(duplicated)
    }

    @Test
    fun trashInvokesCallback() {
        var trashed = false
        render(onTrash = { trashed = true })
        composeRule.onNodeWithText("删除").performClick()
        assertTrue(trashed)
    }

    @Test
    fun repeatingTaskHidesCompleteAction() {
        render(repeating = true)
        composeRule.onNodeWithText("重复任务按次完成，请在日历中操作。").assertIsDisplayed()
        assertTrue(composeRule.onAllNodesWithText("完成").fetchSemanticsNodes().isEmpty())
    }
}
