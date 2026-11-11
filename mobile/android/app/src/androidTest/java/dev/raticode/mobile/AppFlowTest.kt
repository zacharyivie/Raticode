package dev.raticode.mobile

import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import org.junit.Rule
import org.junit.Test

class AppFlowTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    @Test fun productionStartsWithRealPairingAndNoDemo() {
        compose.onNodeWithText("Scan desktop QR").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("Try local demo").assertDoesNotExist()
        compose.onNodeWithText("Conversations").assertDoesNotExist()
    }
}
