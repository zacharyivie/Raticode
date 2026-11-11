package dev.raticode.mobile

import android.app.Application
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.unit.dp
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.*
import dev.raticode.mobile.ui.RaticodeTheme
import dev.raticode.mobile.ui.RemConversation
import dev.raticode.mobile.ui.windowHeightDp
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import java.time.OffsetDateTime

/** Long Rem messages fold to a third of the screen with Show more, as on the desktop; short ones never fold. */
class RemFoldUiTest {
    @get:Rule val compose = createComposeRule()

    private val now = OffsetDateTime.now().toString()
    private val log = (0 until 60).joinToString("\n") { "[electron] at frame $it" }
    private val report = (0 until 40).joinToString("\n") { "- Finding $it with [a link](https://example.com/$it)." }

    private fun show(messages: List<RemMessage>): Int {
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        lateinit var client: LiveClient
        InstrumentationRegistry.getInstrumentation().runOnMainSync { client = LiveClient(app) }
        val home = RemHome("idle", "", "", 0, messages, emptyList(), replies = true)
        var screen = 0
        compose.setContent {
            screen = windowHeightDp()
            RaticodeTheme {
                Surface(color = MaterialTheme.colorScheme.background) {
                    Box(Modifier.fillMaxSize()) { RemConversation(client, home, onTask = {}, onThread = {}, onSuggest = {}, onReply = {}) }
                }
            }
        }
        compose.waitForIdle()
        return screen
    }

    private fun foldHeight(index: Int = 0) = compose.onAllNodesWithTag("remFold")[index].fetchSemanticsNode().size.height
    private fun dp(value: Int) = with(compose.density) { value.dp.roundToPx() }
    private fun toggle(index: Int = 0) = compose.onAllNodesWithTag("remFoldToggle")[index]

    @Test fun aLongPasteFoldsAndOpensWithShowMore() {
        val screen = show(listOf(
            RemMessage("paste", "user", log, now, null, ""),
            RemMessage("short", "rem", "Short reply.", now, null, ""),
        ))
        val limits = RemRich.collapseLimits(screen)
        // Folded to the preview, with every line still there for TalkBack and Copy.
        compose.onAllNodesWithTag("remFoldToggle").assertCountEquals(1)
        toggle().assertTextContains("Show more").assert(SemanticsMatcher.expectValue(SemanticsProperties.StateDescription, "Collapsed"))
        assertEquals(dp(limits.preview), foldHeight())
        compose.onNodeWithText("[electron] at frame 59", substring = true).assertExists()
        // The short reply has no toggle and its menu stays; so does the paste's.
        compose.onAllNodesWithContentDescription("Message actions").assertCountEquals(2)
        toggle().performClick()
        compose.waitForIdle()
        toggle().assertTextContains("Show less").assert(SemanticsMatcher.expectValue(SemanticsProperties.StateDescription, "Expanded"))
        assertTrue(foldHeight() > dp(limits.threshold))
        // Show less, now at the end of the long message, folds it and brings Show more back on screen.
        toggle().performScrollTo().performClick()
        compose.waitForIdle()
        toggle().assertTextContains("Show more").assertIsDisplayed()
        assertEquals(dp(limits.preview), foldHeight())
    }

    @Test fun remsNewestReplyOpensInFullAndOlderOnesFold() {
        show(listOf(
            RemMessage("old", "rem", "Older report.\n\n$report", now, null, ""),
            RemMessage("ask", "user", "Thanks", now, null, ""),
            RemMessage("new", "rem", "Newest report.\n\n$report", now, null, ""),
        ))
        compose.onNodeWithTag("remMessages").performScrollToIndex(1)
        compose.waitForIdle()
        toggle(0).assertTextContains("Show more")
        compose.onNodeWithTag("remMessages").performScrollToIndex(3)
        compose.waitForIdle()
        compose.onAllNodesWithTag("remFoldToggle").filter(hasText("Show less")).assertCountEquals(1)
    }
}
