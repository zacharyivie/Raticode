package dev.raticode.mobile

import android.app.Application
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.*
import dev.raticode.mobile.ui.RaticodeTheme
import dev.raticode.mobile.ui.RemComposer
import dev.raticode.mobile.ui.RemConversation
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import java.time.OffsetDateTime

/** Replying to a message on the phone: the message box says which one, and the sent reply shows and finds it. */
class RemRepliesUiTest {
    @get:Rule val compose = createComposeRule()

    @Test fun repliesShowTheirMessageAndTheBoxSaysWhichOne() {
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        lateinit var client: LiveClient
        InstrumentationRegistry.getInstrumentation().runOnMainSync { client = LiveClient(app) }
        val now = OffsetDateTime.now().toString()
        val original = RemMessage("rem-1", "rem", "The deploy failed on step three.", now, null, "")
        val filler = (0 until 30).map { RemMessage("f$it", if (it % 2 == 0) "user" else "rem", "Filler $it", now, null, "") }
        val reply = RemMessage("u-1", "user", "Why?", now, null, "", refersTo = "rem-1", referenced = RemRich.replyTarget(original))
        val lost = RemMessage("u-2", "user", "And that one?", now, null, "", refersTo = "gone", referenced = RemReference("gone", unavailable = true))
        val home = RemHome("idle", "", "", 0, listOf(original) + filler + listOf(reply, lost), emptyList(), replies = true)
        var replyTo by mutableStateOf<RemReference?>(null)
        compose.setContent {
            RaticodeTheme {
                Surface(color = MaterialTheme.colorScheme.background) {
                    Column(Modifier.fillMaxSize()) {
                        Box(Modifier.weight(1f)) { RemConversation(client, home, onTask = {}, onThread = {}, onSuggest = {}, onReply = { replyTo = it }) }
                        RemComposer(client, home, "", replyTo, onReplyTo = { replyTo = it }) {}
                    }
                }
            }
        }
        // A message that has left the chat says so; the reply stays.
        compose.onNodeWithContentDescription("In reply to an earlier message: No longer in this chat").assertExists()
        // Reply on your own message: the box says who wrote it, and the link can be dropped.
        compose.onAllNodesWithContentDescription("Message actions").onLast().performScrollTo().performClick()
        compose.onNodeWithText("Reply").performClick()
        assertEquals("u-2", replyTo?.id)
        compose.onNodeWithContentDescription("Replying to You: And that one?").assertExists()
        compose.onNodeWithContentDescription("Don't reply to this message").performClick()
        compose.onNodeWithTag("remReplyBar").assertDoesNotExist()
        // The sent reply's quote scrolls up to the original, far above.
        compose.onNodeWithContentDescription("In reply to Rem: The deploy failed on step three.").performScrollTo().performClick()
        compose.waitForIdle()
        compose.onNodeWithText("Filler 0").assertIsDisplayed()
    }
}
