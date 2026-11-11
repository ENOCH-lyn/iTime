package com.itime.app

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.itime.app.data.local.TaskEntity
import com.itime.app.ui.components.TaskRow
import com.itime.app.ui.theme.ItimeTheme
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** Task row rendering, completion toggle and overflow menu contract. */
@RunWith(AndroidJUnit4::class)
class TaskRowTest {

    @get:Rule
    val composeRule = createComposeRule()

    private fun task(
        title: String = "整理实验报告",
        projectName: String? = "courseproject",
        dueDate: String? = "2026-11-12",
        dueTime: String? = "14:00:00",
        estimate: Int? = 45,
        labelsCsv: String = "学习,本周",
        pending: Boolean = false,
    ) = TaskEntity(
        id = "task-1",
        title = title,
        projectName = projectName,
        dueKind = if (dueDate == null) "none" else "datetime",
        dueDate = dueDate,
        dueTime = dueTime,
        estimatedMinutes = estimate,
        labelsCsv = labelsCsv,
        pendingSync = pending,
    )

    private fun render(
        task: TaskEntity = task(),
        inTrash: Boolean = false,
        restorable: Boolean = inTrash,
        onToggle: () -> Unit = {},
        onDelete: () -> Unit = {},
        onRestore: () -> Unit = {},
        onArchive: () -> Unit = {},
    ) {
        composeRule.setContent {
            ItimeTheme(dynamicColor = false) {
                TaskRow(
                    task = task,
                    inTrash = inTrash,
                    restorable = restorable,
                    onToggle = onToggle,
                    onClick = {},
                    onDelete = onDelete,
                    onRestore = onRestore,
                    onArchive = onArchive,
                )
            }
        }
    }

    @Test
    fun rowShowsTitleAndMetaLine() {
        render()
        composeRule.onNodeWithText("整理实验报告").assertIsDisplayed()
        composeRule
            .onNodeWithText("courseproject · 11 月 12 日 · 14:00 · 45 分钟 · 学习 本周")
            .assertIsDisplayed()
    }

    @Test
    fun pendingTaskShowsSyncBadge() {
        render(task(pending = true))
        composeRule.onNodeWithText("待同步").assertIsDisplayed()
    }

    @Test
    fun checkboxInvokesToggle() {
        var toggled = false
        render(onToggle = { toggled = true })
        composeRule.onNodeWithContentDescription("更多操作").assertIsDisplayed()
        // The checkbox is the first checkable node in the row.
        composeRule.onNodeWithText("整理实验报告").assertIsDisplayed()
        composeRule.onAllNodes(androidx.compose.ui.test.isToggleable())[0].performClick()
        assertTrue(toggled)
    }

    @Test
    fun activeRowMenuOffersArchiveAndDelete() {
        var archived = false
        var deleted = false
        render(onArchive = { archived = true }, onDelete = { deleted = true })
        composeRule.onNodeWithContentDescription("更多操作").performClick()
        composeRule.onNodeWithText("归档").performClick()
        assertTrue(archived)
        assertTrue(!deleted)
    }

    @Test
    fun archivedRowMenuOffersRestore() {
        var restored = false
        render(restorable = true, onRestore = { restored = true })
        composeRule.onNodeWithContentDescription("更多操作").performClick()
        composeRule.onNodeWithText("恢复").performClick()
        assertTrue(restored)
    }

    @Test
    fun trashRowMenuOffersRestore() {
        var restored = false
        render(inTrash = true, onRestore = { restored = true })
        composeRule.onNodeWithContentDescription("更多操作").performClick()
        composeRule.onNodeWithText("恢复").performClick()
        assertTrue(restored)
    }
}
