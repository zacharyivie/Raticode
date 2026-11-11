package dev.raticode.mobile

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.unit.dp
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.LiveClient.LiveMessage
import dev.raticode.mobile.ui.ChatHistoryItem
import dev.raticode.mobile.ui.RaticodeTheme
import dev.raticode.mobile.ui.buildChatItems
import org.junit.Rule
import org.junit.Test

/** A representative desktop thread drawn at phone widths, grouped and formatted like the desktop thread view. */
class ThreadFormattingUiTest {
    @get:Rule val compose = createComposeRule()

    private fun screenshot(name: String) {
        compose.waitForIdle()
        Thread.sleep(600)
        InstrumentationRegistry.getInstrumentation().uiAutomation.executeShellCommand("screencap -p /data/local/tmp/raticode-$name").use {
            android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
        }
    }

    private val thread = run {
        fun thought(id: String, group: String, trace: Map<String, String> = emptyMap(), text: String = "") =
            LiveMessage(id, "thread", text, "Rem", kind = "thought", groupId = group, trace = trace)
        val bash = mapOf("id" to "call-1", "kind" to "tool", "title" to "bash", "category" to "shell", "input" to "./gradlew assembleDebug")
        listOf(
            LiveMessage("ask", "thread", "Why does the Android build fail on CI?", "Completed"),
            thought("t1", "turn-1", text = "**Checking the build**\n\nI'll run Gradle the way CI does."),
            thought("t2", "turn-1", bash),
            thought("t3", "turn-1", bash + mapOf("title" to "Tool result", "output" to "BUILD FAILED: SDK 36 not found", "status" to "error")),
            // A second stream ID for the same turn. Desktop keeps it in the same group.
            thought("t4", "turn-1b", text = "The runner image lacks the Android 16 platform."),
            LiveMessage("run", "thread", "", "Rem", kind = "turn-summary", completedAt = "2026-11-01T14:05:00Z", durationMs = 62_400),
            LiveMessage("reply", "thread", "## Cause\n\nCI installs **SDK 35**, but `compileSdk` is 36.\n\n- Add `platforms;android-36` to the setup step\n- Re-run the job\n\n```bash\nsdkmanager \"platforms;android-36\"\n```", "Rem", kind = "final"),
            LiveMessage("restart", "thread", "The previous process was interrupted. Resuming.", "Desktop"),
            LiveMessage("note", "thread", "Model changed to gpt-5.5", "Desktop"),
            LiveMessage("follow", "thread", "Also update the README", "Queued on phone"),
            thought("t5", "turn-2", text = "Reading README.md"),
            LiveMessage("running", "thread", "", "Rem", kind = "turn-summary", running = true, startedAt = System.currentTimeMillis() - 12_000),
        )
    }

    @Test fun representativeThreadAtPhoneWidths() {
        var dark by mutableStateOf(false)
        var width by mutableStateOf(320)
        compose.setContent {
            RaticodeTheme(darkTheme = dark) {
                Surface(color = MaterialTheme.colorScheme.background) { Box(Modifier.fillMaxSize()) {
                    Box(Modifier.width(width.dp).fillMaxHeight()) {
                        LazyColumn(contentPadding = PaddingValues(horizontal = 14.dp, vertical = 16.dp), verticalArrangement = Arrangement.spacedBy(16.dp),
                            modifier = Modifier.testTag("liveMessages")) {
                            items(buildChatItems(thread), key = { it.id }) { ChatHistoryItem(it, null) }
                        }
                    }
                } }
            }
        }
        for ((theme, isDark) in listOf("light" to false, "dark" to true)) for (dp in listOf(320, 412)) {
            dark = isDark; width = dp
            screenshot("thread-format-$theme-$dp.png")
        }
        dark = false; width = 320

        // One group per run of thoughts, even across two stream IDs; it folds once the reply exists.
        compose.onAllNodesWithText("Show thoughts").assertCountEquals(1)
        compose.onNodeWithTag("liveMessages").performScrollToNode(hasText("Reading README.md"))
        compose.onAllNodesWithText("Hide thoughts").assertCountEquals(1)
        compose.onNodeWithText("Reading README.md").assertIsDisplayed()
        screenshot("thread-format-light-320-end.png")
        compose.onNodeWithText("The runner image lacks the Android 16 platform.").assertDoesNotExist()
        // No sender labels, no finished delivery state, no restart notice.
        compose.onAllNodesWithText("Rem").assertCountEquals(0)
        compose.onAllNodesWithText("You").assertCountEquals(0)
        compose.onAllNodesWithText("Completed").assertCountEquals(0)
        compose.onNodeWithText("The previous process was interrupted. Resuming.").assertDoesNotExist()
        compose.onNodeWithText("Running for", substring = true).assertExists()
        compose.onNodeWithText("Queued on phone").assertIsDisplayed()
        compose.onNodeWithTag("liveMessages").performScrollToNode(hasText("Model changed to gpt-5.5"))
        compose.onNodeWithText("Model changed to gpt-5.5").assertIsDisplayed()
        compose.onNodeWithTag("liveMessages").performScrollToNode(hasText("Ran for 1m 2s", substring = true))
        compose.onNodeWithText("Ran for 1m 2s", substring = true).assertIsDisplayed()
        compose.onNodeWithTag("liveMessages").performScrollToNode(hasText("Copy Markdown"))
        compose.onNodeWithText("Copy Markdown").assertIsDisplayed()
        compose.onAllNodesWithContentDescription("Copy message").onFirst().assertExists()

        compose.onNodeWithTag("liveMessages").performScrollToNode(hasText("Show thoughts"))
        compose.onNodeWithText("Show thoughts").performClick()
        compose.onNodeWithText("Running bash commands").assertIsDisplayed()
        compose.onNodeWithText("The runner image lacks the Android 16 platform.").assertIsDisplayed()
        screenshot("thread-format-light-320-thoughts.png")
    }
}
