package dev.raticode.mobile

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import dev.raticode.mobile.live.Kanban
import dev.raticode.mobile.live.KanbanTicket
import dev.raticode.mobile.ui.KanbanMoveNotice
import dev.raticode.mobile.ui.KanbanMoves
import dev.raticode.mobile.ui.RaticodeTheme
import dev.raticode.mobile.ui.TicketDetailView
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

/**
 * A move on the global board says what it did once the board shows it, with Undo where the desktop offers it, and Rem's
 * ticket page can cancel a ticket and restore a cancelled one.
 */
class KanbanMoveNoticeUiTest {
    @get:Rule val compose = createComposeRule()

    private fun ticket(status: String, revision: Long = 3, scope: String = Kanban.SCOPE_REM, key: String = "REM-12") =
        KanbanTicket("t12", scope, if (scope == Kanban.SCOPE_REM) "" else "acme", if (scope == Kanban.SCOPE_REM) "" else "Acme", key,
            "Tidy the board", "", status, "medium", Kanban.REM, "Rem", false, "", "", "Rem", "", "", revision, false, "")

    private var tickets by mutableStateOf(emptyList<KanbanTicket>())
    private val sent = mutableListOf<JSONObject>()
    private val moves = KanbanMoves { sent += it }

    private fun showNotice(start: KanbanTicket) {
        tickets = listOf(start)
        compose.mainClock.autoAdvance = false
        compose.setContent { RaticodeTheme(darkTheme = true) { Box(Modifier.fillMaxSize()) { KanbanMoveNotice(moves, tickets, failed = false) } } }
    }

    private fun after(ms: Long) { compose.mainClock.advanceTimeBy(ms); compose.waitForIdle() }

    @Test fun aMoveNamesTheTicketAndUndoSendsItBack() {
        showNotice(ticket("todo"))
        compose.runOnIdle { moves.move(tickets.single(), "backlog") }
        assertEquals("backlog", sent.single().getString("column"))
        // Nothing to say until the board shows the move went through.
        after(500)
        compose.onAllNodesWithText("REM-12 moved to Backlog.").assertCountEquals(0)

        compose.runOnIdle { tickets = listOf(ticket("backlog", 4)) }
        after(500)
        compose.onNodeWithText("REM-12 moved to Backlog.").assertIsDisplayed()
        // Undo stays past a plain notice's 4 seconds.
        after(5_000)
        compose.onNodeWithText("Undo").assertIsDisplayed().performClick()
        after(500)
        assertEquals(2, sent.size)
        assertEquals("move", sent.last().getString("action"))
        assertEquals("todo", sent.last().getString("column"))
        assertEquals(4L, sent.last().getLong("expected_revision"))

        compose.runOnIdle { tickets = listOf(ticket("todo", 5)) }
        after(500)
        compose.onNodeWithText("REM-12 moved back to To do.").assertIsDisplayed()
        compose.onAllNodesWithText("Undo").assertCountEquals(0)
    }

    @Test fun cancellingSaysSoAndStartingWorkHasNoUndo() {
        showNotice(ticket("todo"))
        compose.runOnIdle { moves.move(tickets.single(), "cancelled") }
        compose.runOnIdle { tickets = listOf(ticket("cancelled", 4)) }
        after(500)
        compose.onNodeWithText("REM-12 cancelled.").assertIsDisplayed()
        compose.onNodeWithText("Undo").assertIsDisplayed()

        compose.runOnIdle { moves.move(ticket("todo", 4), "running") }
        compose.runOnIdle { tickets = listOf(ticket("in_progress", 5)) }
        after(500)
        compose.onNodeWithText("REM-12 started.").assertIsDisplayed()
        compose.onAllNodesWithText("Undo").assertCountEquals(0)
        compose.onAllNodesWithText("REM-12 cancelled.").assertCountEquals(0)
    }

    private fun showPage(t: KanbanTicket, moved: MutableList<String>) = compose.setContent {
        RaticodeTheme(darkTheme = true) { Surface { TicketDetailView(t, null, null, historySupported = true, connected = true, busy = false,
            onAction = {}, onMove = { moved += it }) } }
    }

    @Test fun remsOpenTicketCanBeCancelledFromItsPage() {
        val moved = mutableListOf<String>()
        showPage(ticket("todo"), moved)
        compose.onNodeWithTag("kanbanCancelTicket").assertIsDisplayed().assertTextEquals("Cancel ticket").performClick()
        assertEquals(listOf("cancelled"), moved)
        compose.onAllNodesWithTag("kanbanRestore").assertCountEquals(0)
    }

    @Test fun aCancelledTicketOffersRestoreToBacklog() {
        val moved = mutableListOf<String>()
        showPage(ticket("cancelled"), moved)
        compose.onAllNodesWithTag("kanbanCancelTicket").assertCountEquals(0)
        compose.onAllNodesWithTag("kanbanMoveOpen").assertCountEquals(0)
        compose.onNodeWithTag("kanbanRestore").assertIsDisplayed().assertTextEquals("Restore to Backlog").performClick()
        assertEquals(listOf("backlog"), moved)
    }

    @Test fun aDoneTicketHasNoCancelButton() {
        showPage(ticket("done"), mutableListOf())
        compose.onAllNodesWithTag("kanbanCancelTicket").assertCountEquals(0)
    }

    @Test fun anOrganizationsTicketHasNoCancelButton() {
        showPage(ticket("todo", scope = Kanban.SCOPE_ORG, key = "ACM-1"), mutableListOf())
        compose.onAllNodesWithTag("kanbanCancelTicket").assertCountEquals(0)
        compose.onNodeWithTag("kanbanMoveOpen").assertIsDisplayed()
    }
}
