package com.itime.app

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.itime.app.ui.screens.calendar.DayCell
import com.itime.app.ui.theme.ItimeTheme
import java.time.LocalDate
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * The month cell must expose exactly one descriptive accessibility node so
 * TalkBack reads "11 月 11 日，2 项安排，已选中" instead of loose digits.
 */
@RunWith(AndroidJUnit4::class)
class DayCellSemanticsTest {

    @get:Rule
    val composeRule = createComposeRule()

    @Test
    fun selectedDayAnnouncesDateAndCount() {
        composeRule.setContent {
            ItimeTheme(dynamicColor = false) {
                DayCell(
                    day = LocalDate.of(2026, 11, 11),
                    selected = true,
                    count = 2,
                    onClick = {},
                )
            }
        }
        composeRule
            .onNodeWithContentDescription("11 月 11 日，2 项安排，已选中")
            .assertIsDisplayed()
    }

    @Test
    fun emptyDayAnnouncesNoSchedule() {
        composeRule.setContent {
            ItimeTheme(dynamicColor = false) {
                DayCell(
                    day = LocalDate.of(2026, 11, 12),
                    selected = false,
                    count = 0,
                    onClick = {},
                )
            }
        }
        composeRule
            .onNodeWithContentDescription("11 月 12 日，无安排")
            .assertIsDisplayed()
    }
}
