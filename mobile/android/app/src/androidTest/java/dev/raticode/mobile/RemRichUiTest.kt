package dev.raticode.mobile

import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.*
import dev.raticode.mobile.ui.RaticodeTheme
import dev.raticode.mobile.ui.RemConversation
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import java.time.OffsetDateTime

/** Rem's rich replies on the phone: result card, details, follow-ups, actions, progress and the reply being written. */
class RemRichUiTest {
    @get:Rule val compose = createComposeRule()

    private fun screenshot(name: String) {
        compose.waitForIdle()
        Thread.sleep(600)
        InstrumentationRegistry.getInstrumentation().uiAutomation.executeShellCommand("screencap -p /data/local/tmp/raticode-$name").use {
            android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
        }
    }

    private val report = "/home/rem/brain/Reports/vector-search.html"
    private val now = OffsetDateTime.now()
    private val details = """
        | Option | Setup | Recall |
        |---|---|---|
        | SQLite | none | 0.91 |
        | ~~Qdrant~~ | server | 0.94 |

        - [x] Compared setup
        - [ ] Benchmarked on device

        ```python
        # Index a note
        db.execute("INSERT INTO notes VALUES (?, ?)", (path, text))
        ```

        ```rem-chart
        {"type":"bar","title":"Recall","labels":["SQLite","LanceDB","Qdrant"],"series":[{"name":"Recall","values":[0.91,0.93,0.94]}]}
        ```
    """.trimIndent()

    private fun home(replying: Boolean = false) = RemHome(if (replying) "replying" else "idle", "claude_code", "claude-opus-5-5", 0, listOf(
        RemMessage("ask", "user", "Compare vector search options", now.minusMinutes(30).toString(), null, ""),
        RemMessage("update", "rem", "Done. SQLite full-text search plus a small vector index is plenty.\n\n[vector-search](<$report>)",
            now.minusMinutes(2).toString(), RemWork("task1", "run1", "thread1", "Compare vector search options"), "",
            details = details, suggestions = listOf("Set up the SQLite option", "Why not Qdrant?"),
            meta = RemMeta("claude_code", "claude-sonnet-5-5", 6.4),
            result = RemResult("Compare vector search options", "research", "succeeded", RemChecks(3, 0, 3),
                listOf(RemArtifact("vector-search.html", "Vector search for Second Brain", "html", 41230, report)),
                listOf(RemSource("https://www.sqlite.org/fts5.html", "SQLite FTS5 Extension", "cited"),
                    RemSource("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "Talk", "found")), "thread1")),
        RemMessage("ack", "rem", "Started the sync research.", now.minusMinutes(1).toString(), RemWork("task2", "run2", "thread2", "Local-first sync"), ""),
    ), listOf(
        RemTask("task2", "Local-first sync", "research", "enabled", 1, RemSchedule("once", "", "", emptyList(), 0), "", "",
            RemRun("run2", "verifying", now.minusMinutes(4).toString(), "", "", false, false, "thread2", "review2",
                phaseLabel = "Checking the work", checks = RemChecks(1, 0, 3)), null),
    ), turnLabel = if (replying) "Reading earlier messages" else "", draftReply = if (replying) "Here's what I found so f" else "")

    @Test fun richReplyPartsWorkOnThePhone() {
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        lateinit var client: LiveClient
        InstrumentationRegistry.getInstrumentation().runOnMainSync { client = LiveClient(app) }
        val suggested = mutableListOf<String>()
        val sent = mutableListOf<String>()
        val replied = mutableListOf<RemReference>()
        var canSend by mutableStateOf(true)
        var state by mutableStateOf(home())
        var dark by mutableStateOf(false)
        compose.setContent {
            RaticodeTheme(darkTheme = dark) {
                Surface(color = MaterialTheme.colorScheme.background) {
                    RemConversation(client, state, onTask = {}, onThread = {}, onSuggest = { suggested += it },
                        onFollowUp = { text: String -> sent += text }.takeIf { canSend }, onReply = { replied += it })
                }
            }
        }
        // The result card replaces the raw report link and says its checks in words.
        compose.onNodeWithText("✓ 3 of 3 checks passed").assertExists()
        compose.onNodeWithText("Vector search for Second Brain").assertExists()
        compose.onAllNodesWithText("vector-search", substring = false).assertCountEquals(0)
        compose.onNodeWithContentDescription("Video: Talk, youtube.com").assertExists()
        // Live progress for the running task, each step said in words.
        compose.onNodeWithContentDescription("Check in progress").assertExists()
        compose.onNodeWithText("Checking the work", substring = true).assertExists()
        screenshot("rich-collapsed.png")

        compose.onNodeWithText("▸ Details").performScrollTo().performClick()
        compose.onNodeWithText("SQLite").assertExists()
        compose.onNodeWithText("Show data table").performScrollTo().performClick()
        compose.onNodeWithText("Hide data table").assertExists()
        compose.onNodeWithContentDescription("Not done").assertExists()
        screenshot("rich-details.png")

        // Follow-ups belong to the newest Rem message only; here that is the acknowledgement, which has none.
        compose.onAllNodesWithTag("remFollowUps").assertCountEquals(0)
        state = home().let { it.copy(messages = it.messages.dropLast(1)) }
        compose.waitForIdle()
        // A suggested reply sends at once instead of filling the message box.
        // performScrollTo would only scroll the chip row sideways, so use the chip's click action directly.
        compose.onNodeWithText("Why not Qdrant?").performSemanticsAction(androidx.compose.ui.semantics.SemanticsActions.OnClick)
        assertEquals(listOf("Why not Qdrant?"), sent)
        assertEquals(emptyList<String>(), suggested)
        // When the phone can't send (offline, or Rem is replying), the chips stay visible but disabled.
        canSend = false
        compose.onNodeWithText("Set up the SQLite option").assertIsNotEnabled()
        canSend = true
        compose.onNodeWithText("Set up the SQLite option").assertIsEnabled()
        // Reply links the next message to this one; it doesn't put text in the box.
        compose.onAllNodesWithContentDescription("Message actions").onLast().performScrollTo().performClick()
        compose.onNodeWithText("Reply").performClick()
        compose.onNodeWithText("claude-sonnet-5-5 · 6.4 s").assertDoesNotExist()
        assertEquals(listOf("update"), replied.map { it.id })
        assertEquals("Done. SQLite full-text search plus a small vector index is plenty. vector-search", replied.single().body)
        assertEquals(emptyList<String>(), suggested)
        assertEquals(listOf("Why not Qdrant?"), sent)

        // Copy last: Android shows its own clipboard preview over the bottom of the screen afterwards.
        compose.onNodeWithText("Copy code").performScrollTo().performClick()
        compose.onNodeWithText("Copied").assertExists()

        // While Rem replies, the draft and what it is doing show; the draft is hidden from TalkBack.
        // The list stays where you were reading, so scroll down to the reply.
        state = home(replying = true).let { it.copy(messages = it.messages.dropLast(1)) }
        compose.onNodeWithTag("remMessages").performScrollToNode(hasText("Reading earlier messages…"))
        compose.onNodeWithText("Reading earlier messages…").assertExists()
        compose.onNodeWithTag("remDraft", useUnmergedTree = true).assertExists()
        dark = true
        screenshot("rich-replying-dark.png")
    }

    @Test fun actionChipsRunTheirControls() {
        val report = RemArtifact("r.html", "Report", "html", 1, "/r/r.html")
        val update = RemMessage("u", "rem", "The review stopped.", now.toString(), RemWork("t1", "r1", "thread", "Check notes"), "",
            result = RemResult("Check notes", "errand", "blocked", RemChecks(0, 0, 2), listOf(report), emptyList(), ""), kind = "task_update")
        var task by mutableStateOf(RemTask("t1", "Check notes", "errand", "enabled", 1, RemSchedule("daily", "", "09:00", emptyList(), 0), "", "",
            null, RemRun("r1", "blocked", now.toString(), now.toString(), "", false, false, "thread", "review", reviewRetryable = true)))
        val tapped = mutableListOf<String>()
        compose.setContent {
            RaticodeTheme {
                Surface(color = MaterialTheme.colorScheme.background) {
                    dev.raticode.mobile.ui.RemResultActionRow(RemRich.resultActions(update, listOf(task), live = true, reviewRetry = true)) { tapped += it.id }
                }
            }
        }
        compose.onNodeWithText("Open report").performClick()
        compose.onNodeWithText("Retry review").performClick()
        compose.onNodeWithText("Run again").performClick()
        assertEquals(listOf("open", "retry", "run"), tapped)
        screenshot("rich-actions.png")
        // Once the review is running again, only the report stays.
        task = task.copy(activeRun = RemRun("r1", "verifying", now.toString(), "", "", false, false, "thread", "review"))
        compose.onNodeWithText("Open report").assertExists()
        compose.onNodeWithText("Retry review").assertDoesNotExist()
        compose.onNodeWithText("Run again").assertDoesNotExist()
    }
}
