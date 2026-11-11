package dev.raticode.mobile

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import dev.raticode.mobile.ui.ReceivedFileActions
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class ReceivedFileActionsTest {
    @get:Rule val compose = createComposeRule()

    @Test fun downloadedFilesOfferSaveAndOpen() {
        val taps = mutableListOf<String>()
        var state by mutableStateOf<String?>("offered")
        var connected by mutableStateOf(false)
        compose.setContent {
            ReceivedFileActions("report.pdf", state, 42, connected, onDownload = { taps += "download" },
                onSave = { taps += "save" }, onOpen = { taps += "open" })
        }
        compose.onNodeWithContentDescription("Download report.pdf").assertIsNotEnabled()
        connected = true
        compose.onNodeWithContentDescription("Download report.pdf").performClick()
        state = "receiving"
        compose.onNodeWithText("42%").assertExists()
        state = "available"
        compose.onNodeWithContentDescription("Save report.pdf").performClick()
        compose.onNodeWithContentDescription("Open report.pdf").performClick()
        assertEquals(listOf("download", "save", "open"), taps)
        state = "failed"
        compose.onNodeWithText("Ask Rem to send it again").assertExists()
    }
}
