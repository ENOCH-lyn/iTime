package com.itime.app

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.itime.app.ui.ai.AiErrorBar
import com.itime.app.ui.theme.ItimeTheme
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** Error strip above the AI composer: retry visibility and callbacks. */
@RunWith(AndroidJUnit4::class)
class AiErrorBarTest {

    @get:Rule
    val composeRule = createComposeRule()

    private fun render(
        canRetry: Boolean,
        retryEnabled: Boolean = true,
        onRetry: () -> Unit = {},
        onDismiss: () -> Unit = {},
    ) {
        composeRule.setContent {
            ItimeTheme(dynamicColor = false) {
                AiErrorBar(
                    message = "网络不可用，已保留本地数据。",
                    canRetry = canRetry,
                    retryEnabled = retryEnabled,
                    onRetry = onRetry,
                    onDismiss = onDismiss,
                )
            }
        }
    }

    @Test
    fun retryButtonShowsAndInvokesCallback() {
        var retried = false
        render(canRetry = true, onRetry = { retried = true })
        composeRule.onNodeWithText("重试").assertIsDisplayed().performClick()
        assertTrue(retried)
    }

    @Test
    fun retryHiddenWhenNothingToResend() {
        render(canRetry = false)
        composeRule.onNodeWithText("知道了").assertIsDisplayed()
        assertTrue(composeRule.onAllNodesWithText("重试").fetchSemanticsNodes().isEmpty())
    }

    @Test
    fun dismissInvokesCallback() {
        var dismissed = false
        render(canRetry = true, onDismiss = { dismissed = true })
        composeRule.onNodeWithText("知道了").performClick()
        assertTrue(dismissed)
    }

    @Test
    fun disabledRetryDoesNotFire() {
        var retried = false
        render(canRetry = true, retryEnabled = false, onRetry = { retried = true })
        composeRule.onNodeWithText("重试").performClick()
        assertTrue(!retried)
    }
}
