package dev.raticode.mobile

import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.*
import dev.raticode.mobile.ui.LocalRemTickets
import dev.raticode.mobile.ui.LocalTicketOpener
import dev.raticode.mobile.ui.RaticodeTheme
import dev.raticode.mobile.ui.RemConversation
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import java.time.OffsetDateTime

/** Board ticket keys in Rem's chat show as pills that open the ticket; self-work's cards are grouped and list their tickets. */
class TicketPillsUiTest {
    @get:Rule val compose = createComposeRule()

    private fun screenshot(name: String) {
        compose.waitForIdle()
        Thread.sleep(600)
        InstrumentationRegistry.getInstrumentation().uiAutomation.executeShellCommand("screencap -p /data/local/tmp/raticode-$name").use {
            android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
        }
    }

    private val now = OffsetDateTime.now()
    private val fix = RemTicket("t5", "REM-5", "Fix the sync conflict on reconnect", "in_progress", "rem", "")
    private val idea = RemTicket("t12", "REM-12", "Voice shortcuts", "backlog", "rem", "", proposed = true)
    private fun ref(ticket: RemTicket, role: String) = RemTicketRef(ticket.ticketId, ticket.key, ticket.title, "rem", "", role)
    private fun card(id: String, title: String, kind: String, tickets: List<RemTicketRef>) =
        RemMessage("queued-$id", "rem", "Started $title while you were away.", now.minusMinutes(1).toString(),
            RemWork("task-$id", "run-$id", "thread-$id", title, tickets = tickets, selfWork = kind), "", selfWork = RemSelfWork(kind, "pass-1"))

    private fun home() = RemHome("idle", "claude_code", "claude-opus-5-5", 0, listOf(
        RemMessage("ask", "user", "How is REM-5 going?", now.minusMinutes(3).toString(), null, ""),
        RemMessage("answer", "rem", "REM-5 is in progress. REM-404 isn't a ticket, and `REM-5` in code stays as written.",
            now.minusMinutes(2).toString(), null, ""),
        card("a", "Fix the sync conflict on reconnect", "ticket", listOf(ref(fix, "worked"))),
        card("b", "Research new features for project", "research",
            listOf(ref(idea, "created"), RemTicketRef("t13", "REM-13", "Offline mode", "rem", "", "created"))),
    ), emptyList(), tickets = mapOf("REM-5" to fix, "REM-12" to idea))

    @Test fun ticketPillsOpenTheirTicketAndSelfWorkCardsAreGrouped() {
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        lateinit var client: LiveClient
        InstrumentationRegistry.getInstrumentation().runOnMainSync { client = LiveClient(app) }
        val opened = mutableListOf<String>()
        var state by mutableStateOf(home())
        var dark by mutableStateOf(false)
        compose.setContent {
            RaticodeTheme(darkTheme = dark) {
                Surface(color = MaterialTheme.colorScheme.background) {
                    CompositionLocalProvider(LocalRemTickets provides state.tickets, LocalTicketOpener provides { ticket: RemTicket -> opened += ticket.uid }) {
                        RemConversation(client, state, onTask = {}, onThread = {}, onSuggest = {})
                    }
                }
            }
        }
        // A key of a ticket on the board is a pill TalkBack reads as a button with its title and status.
        val pill = compose.onNodeWithContentDescription("Ticket REM-5: Fix the sync conflict on reconnect (In progress)", useUnmergedTree = true)
        pill.assertExists().assert(SemanticsMatcher.expectValue(SemanticsProperties.Role, Role.Button))
        // Other key-shaped text and code stay as written.
        compose.onNodeWithText("REM-404 isn't a ticket", substring = true).assertExists()
        // Self-work's two cards are one message under one line, each listing its tickets.
        compose.onNodeWithTag("remSelfWorkBatch").assertExists()
        compose.onNodeWithText("Started 2 things while you were away: 1 ticket and 1 research run.").assertExists()
        compose.onAllNodesWithContentDescription("Ticket REM-5: Fix the sync conflict on reconnect (In progress). Worked on by this task", useUnmergedTree = true)
            .assertCountEquals(1)
        compose.onNodeWithContentDescription("Ticket REM-12: Voice shortcuts (Backlog, waiting for your approval). Created by this task", useUnmergedTree = true)
            .performScrollTo().assertExists()
        // A ticket deleted since keeps its key and old title and doesn't open.
        compose.onNodeWithContentDescription("REM-13: Offline mode, no longer on the board", useUnmergedTree = true).assertExists()
            .assert(SemanticsMatcher.keyNotDefined(SemanticsActions.OnClick))
        screenshot("ticket-pills.png")

        // Tapping a pill opens that ticket on the board.
        compose.onNodeWithContentDescription("Ticket REM-12: Voice shortcuts (Backlog, waiting for your approval). Created by this task", useUnmergedTree = true)
            .performSemanticsAction(SemanticsActions.OnClick)
        compose.onNodeWithTag("remMessages").performScrollToNode(hasText("REM-404", substring = true))
        compose.onNodeWithContentDescription("Ticket REM-5: Fix the sync conflict on reconnect (In progress)", useUnmergedTree = true)
            .performSemanticsAction(SemanticsActions.OnClick)
        assertEquals(listOf("/t12", "/t5"), opened)

        // A renamed ticket shows its new title; one no longer on the board turns back into text.
        state = home().copy(tickets = mapOf("REM-5" to fix.copy(title = "Fix sync after reconnect")))
        compose.waitForIdle()
        compose.onNodeWithContentDescription("Ticket REM-5: Fix sync after reconnect (In progress)", useUnmergedTree = true).assertExists()
        compose.onAllNodesWithContentDescription("Ticket REM-12", substring = true, useUnmergedTree = true).assertCountEquals(0)
        compose.onNodeWithContentDescription("REM-12: Voice shortcuts, no longer on the board", useUnmergedTree = true).assertExists()
        dark = true
        screenshot("ticket-pills-dark.png")
    }
}
