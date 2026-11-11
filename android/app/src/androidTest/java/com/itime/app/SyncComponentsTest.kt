package com.itime.app

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.itime.app.data.SyncPhase
import com.itime.app.ui.components.ConflictBanner
import com.itime.app.ui.components.OfflineBanner
import com.itime.app.ui.components.SyncChip
import com.itime.app.ui.theme.ItimeTheme
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** State labels of the shared sync widgets used on every screen. */
@RunWith(AndroidJUnit4::class)
class SyncComponentsTest {

    @get:Rule
    val composeRule = createComposeRule()

    private fun render(content: @androidx.compose.runtime.Composable () -> Unit) {
        composeRule.setContent {
            ItimeTheme(dynamicColor = false) { content() }
        }
    }

    @Test
    fun syncedChipShowsSyncedLabel() {
        render { SyncChip(SyncPhase.Synced, pending = 0, conflicts = 0) }
        composeRule.onNodeWithText("已同步").assertIsDisplayed()
    }

    @Test
    fun pendingChipShowsPendingLabel() {
        render { SyncChip(SyncPhase.Synced, pending = 3, conflicts = 0) }
        composeRule.onNodeWithText("待同步 3").assertIsDisplayed()
    }

    @Test
    fun conflictsTakePriorityOverPending() {
        render { SyncChip(SyncPhase.Synced, pending = 3, conflicts = 2) }
        composeRule.onNodeWithText("有 2 项待处理").assertIsDisplayed()
    }

    @Test
    fun offlineChipShowsOfflineLabel() {
        render { SyncChip(SyncPhase.Offline, pending = 0, conflicts = 0) }
        composeRule.onNodeWithText("离线").assertIsDisplayed()
    }

    @Test
    fun conflictBannerInvokesResolveCallback() {
        var resolved = false
        render {
            ConflictBanner(count = 2, onResolve = { resolved = true })
        }
        composeRule.onNodeWithText("有 2 项改动与其他设备冲突").assertIsDisplayed()
        composeRule.onNodeWithText("处理").performClick()
        assertTrue(resolved)
    }

    @Test
    fun offlineBannerExplainsOfflineBehaviour() {
        render { OfflineBanner() }
        composeRule
            .onNodeWithText("当前离线，改动会保存在手机上，联网后自动同步。")
            .assertIsDisplayed()
    }
}
