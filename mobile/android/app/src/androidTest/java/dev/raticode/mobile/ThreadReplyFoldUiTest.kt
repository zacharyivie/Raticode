package dev.raticode.mobile

import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import dev.raticode.mobile.live.LiveClient.LiveMessage
import dev.raticode.mobile.ui.ChatHistoryItem
import dev.raticode.mobile.ui.RaticodeTheme
import dev.raticode.mobile.ui.buildChatItems
import dev.raticode.mobile.ui.newestReplyId
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

/** In a desktop thread, an earlier long reply folds to a preview with Show more; the newest reply stays open, as in Rem's chat. */
class ThreadReplyFoldUiTest {
    @get:Rule val compose = createComposeRule()

    private val report = "## Findings\n\n" + (1..40).joinToString("\n") { "- Finding $it: the screen reads as one block of text." }

    @Test fun earlierLongRepliesFoldAndTheNewestStaysOpen() {
        val items = buildChatItems(listOf(
            LiveMessage("ask-1", "thread", "Audit the phone UI", "Completed"),
            LiveMessage("reply-1", "thread", report, "Rem", kind = "final"),
            LiveMessage("ask-2", "thread", "And the review?", "Completed"),
            LiveMessage("reply-2", "thread", report.replace("Findings", "Review"), "Rem", kind = "final"),
        ))
        val newest = newestReplyId(items)
        assertEquals(items.last().id, newest)
        compose.setContent {
            RaticodeTheme {
                Surface(color = MaterialTheme.colorScheme.background) {
                    LazyColumn(Modifier.fillMaxSize().testTag("list")) {
                        items(items, key = { it.id }) { ChatHistoryItem(it, null, newest = it.id == newest) }
                    }
                }
            }
        }
        // Each long reply has a toggle: the earlier one offers Show more, the newest Show less.
        compose.onNodeWithTag("list").performScrollToIndex(1)
        compose.onAllNodesWithTag("remFoldToggle").onFirst().assertTextContains("Show more")
        compose.onNodeWithTag("list").performScrollToIndex(items.lastIndex)
        compose.onNodeWithTag("list").performScrollToNode(hasText("Show less"))
        compose.onNodeWithText("Show less").assertIsDisplayed()
        // Opening the earlier reply shows all of it.
        compose.onNodeWithTag("list").performScrollToIndex(1)
        compose.onAllNodesWithText("Show more").onFirst().performClick()
        compose.onNodeWithTag("list").performScrollToNode(hasText("Finding 40", substring = true))
    }
}
