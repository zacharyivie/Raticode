package dev.raticode.mobile

import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.live.RemHome
import dev.raticode.mobile.live.RemMessage
import dev.raticode.mobile.ui.RaticodeTheme
import dev.raticode.mobile.ui.RemConversation
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import java.time.ZonedDateTime

/** Rem chat stamps every dated message, keeps undated ones in place, and divides back-to-back Rem replies. */
class RemChatStampsUiTest {
    @get:Rule val compose = createComposeRule()

    private fun screenshot(name: String) {
        compose.waitForIdle()
        Thread.sleep(600)
        InstrumentationRegistry.getInstrumentation().uiAutomation.executeShellCommand("screencap -p /data/local/tmp/raticode-$name").use {
            android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
        }
    }

    @Test fun stampsAndDividers() {
        val today = ZonedDateTime.now().withHour(9).withMinute(5).withSecond(0).withNano(0)
        val yesterday = today.minusDays(1).withHour(16).withMinute(30)
        fun message(id: String, role: String, body: String, at: String) = RemMessage(id, role, body, at, null, "")
        val home = RemHome("idle", "codex", "", 0, listOf(
            message("old-ask", "user", "Old question", yesterday.toOffsetDateTime().toString()),
            message("old-reply", "rem", "Old answer", yesterday.toOffsetDateTime().toString()),
            message("ask", "user", "New question", today.toOffsetDateTime().toString()),
            message("reply", "rem", "First reply", today.toOffsetDateTime().toString()),
            message("update", "rem", "Second reply", today.plusMinutes(1).toOffsetDateTime().toString()),
            message("undated", "rem", "Third reply", "not a date"),
        ), emptyList())
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        lateinit var client: LiveClient
        InstrumentationRegistry.getInstrumentation().runOnMainSync { client = LiveClient(app) }
        var dark by mutableStateOf(false)
        compose.setContent {
            RaticodeTheme(darkTheme = dark) {
                Surface(color = MaterialTheme.colorScheme.background) { RemConversation(client, home, onTask = {}, onThread = {}, onSuggest = {}) }
            }
        }

        compose.onNodeWithText("Third reply").assertIsDisplayed()
        val stamps = compose.onAllNodesWithTag("remStamp").fetchSemanticsNodes()
        val labels = stamps.map { it.config[SemanticsProperties.Text].joinToString() }
        val spoken = stamps.map { it.config[SemanticsProperties.ContentDescription].joinToString() }
        // Five dated messages get a stamp; the undated reply gets none and no "Earlier" label.
        assertEquals(labels.toString(), 5, labels.size)
        assertTrue(labels.toString(), labels.take(2).all { it.startsWith("Yesterday, ") })
        assertTrue(labels.toString(), labels.drop(2).none { it.contains(",") })
        assertTrue(spoken.toString(), spoken.all { Regex("\\d{4}, ").containsMatchIn(it) })
        compose.onNodeWithText("Earlier").assertDoesNotExist()
        compose.onNodeWithText("Yesterday").assertExists()
        compose.onNodeWithText("Today").assertExists()
        // Only Rem messages right after another Rem message get the faint rule.
        assertEquals(2, compose.onAllNodesWithTag("remFollows").fetchSemanticsNodes().size)
        screenshot("rem-stamps-light.png")
        dark = true
        screenshot("rem-stamps-dark.png")
    }
}
