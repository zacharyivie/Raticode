package dev.raticode.mobile

import androidx.compose.material3.Surface
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.Kanban
import dev.raticode.mobile.live.KanbanBoard
import dev.raticode.mobile.live.KanbanTicket
import dev.raticode.mobile.live.TicketWait
import dev.raticode.mobile.ui.AUTO_APPROVED_NOTE
import dev.raticode.mobile.ui.RaticodeTheme
import dev.raticode.mobile.ui.TicketDetailView
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/**
 * A board ticket's page reads at a glance: status chips and the column strip up top, facts as tiles, the long reason folded
 * to its first sentence, the auto-approval note folded into a chip, related tickets as tappable rows and the actions pinned.
 */
class TicketDetailUiTest {
    @get:Rule val compose = createComposeRule()

    private fun screenshot(name: String) {
        compose.waitForIdle()
        Thread.sleep(600)
        InstrumentationRegistry.getInstrumentation().uiAutomation.executeShellCommand("screencap -p /data/local/tmp/raticode-$name").use {
            android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
        }
    }

    private val why = "grok_accounts.py already declares device_sign_in_args=('login','--device-auth'). xAI documents that device code login " +
        "'follows RFC 8628', 'Grok prints a URL and a short user code' and you 'Complete login on any device with a browser'. REM-196's branch " +
        "(bbb5303e) adds phone_sign_in per adapter but enables it only for Codex. Rem skips accounts that need sign-in, so a signed-out Grok " +
        "account stays unused until you're back at the desktop."

    private fun ticket(id: String, key: String, title: String, status: String, waits: List<TicketWait> = emptyList(), autoApproved: Boolean = false,
                       reason: String = "", proposed: Boolean = false, doneWhen: String = "") =
        KanbanTicket(id, Kanban.SCOPE_REM, "", "", key, title, "Let the phone start a Grok device sign-in.", status, "high", Kanban.REM, "Rem", proposed,
            reason, "", "Rem", "", "", 3, false, "", kind = "develop", project = "Raticode", autoApproved = autoApproved, waitsFor = waits, doneWhen = doneWhen)

    private val grok = ticket("t202", "REM-202", "Sign a Grok account in from the phone", "done",
        waits = listOf(TicketWait("t196", "REM-196", "Sign a Codex account in from the phone", "done")), autoApproved = true, reason = why)
    private val codex = ticket("t196", "REM-196", "Sign a Codex account in from the phone", "done")
    private val later = ticket("t210", "REM-210", "Sign every provider in from the phone", "todo",
        waits = listOf(TicketWait("t202", "REM-202", "Sign a Grok account in from the phone", "done")))
    private val board = KanbanBoard(listOf(grok, codex, later), emptyList(), 0)

    private fun show(t: KanbanTicket, sent: MutableList<JSONObject> = mutableListOf(), opened: MutableList<String> = mutableListOf()) = compose.setContent {
        RaticodeTheme(darkTheme = true) { Surface { TicketDetailView(t, board, null, historySupported = true, connected = true, busy = false,
            onAction = { sent += it }, onOpenTicket = { opened += it }) } }
    }

    @Test fun theTicketReadsAtAGlanceAndKeepsEverything() {
        val opened = mutableListOf<String>()
        show(grok, opened = opened)
        compose.onNodeWithTag("kanbanStatusChip").assertTextContains("Done")
        compose.onNodeWithTag("stageStrip").assertContentDescriptionContains("Stage: Done")
        // The assignee's name stays visible beside the Change menu.
        compose.onNode(hasText("Rem") and hasAnyAncestor(hasTestTag("kanbanAssignee")), useUnmergedTree = true).assertIsDisplayed()
        assertTrue(compose.onNode(hasText("Rem") and hasAnyAncestor(hasTestTag("kanbanAssignee")), useUnmergedTree = true).fetchSemanticsNode().size.width > 0)
        compose.onNodeWithTag("kanbanFactPriority").assertContentDescriptionEquals("Priority: High")
        compose.onNodeWithTag("kanbanFactProject").assertContentDescriptionEquals("Project: Raticode")
        compose.onNodeWithTag("kanbanFactKind").assertContentDescriptionEquals("Kind: Build")
        compose.onNodeWithTag("kanbanFactWaits").assertContentDescriptionEquals("Waits for: All 1 done")
        // The auto-approval note is folded into its chip until asked for.
        compose.onAllNodesWithText(AUTO_APPROVED_NOTE).assertCountEquals(0)
        // The long reason shows its first sentence and Show more.
        compose.onNodeWithTag("kanbanWhyText-preview").assertTextEquals("grok_accounts.py already declares device_sign_in_args=('login','--device-auth').")
        compose.onAllNodesWithText("Rem skips accounts that need sign-in", substring = true).assertCountEquals(0)
        // The actions stay pinned at the bottom.
        compose.onNodeWithTag("kanbanMoveOpen").assertIsDisplayed()
        screenshot("ticket-detail-top.png")

        compose.onNodeWithTag("kanbanAutoApproved").performClick()
        compose.onNodeWithText(AUTO_APPROVED_NOTE).assertExists()
        compose.onNodeWithTag("kanbanWhyText-toggle").performScrollTo().performClick()
        compose.onNodeWithText("Rem skips accounts that need sign-in", substring = true).assertExists()
        screenshot("ticket-detail-open.png")

        // What it waits for and what waits for it open their tickets.
        compose.onNodeWithTag("kanbanWait").performScrollTo().performClick()
        compose.onNodeWithTag("kanbanWaitedOn").performScrollTo()
        compose.onNodeWithTag("kanbanWaitedOn").assertTextContains("REM-210", substring = true)
        screenshot("ticket-detail-related.png")
        compose.onNodeWithTag("kanbanWaitedOn").performClick()
        assertEquals(listOf("/t196", "/t210"), opened)
        compose.onNodeWithTag("kanbanHistory").performScrollTo().assertExists()
    }

    @Test fun tappingTheColumnOrMoveOpensTheMoveSheet() {
        val sent = mutableListOf<JSONObject>()
        show(later, sent)
        compose.onNodeWithTag("kanbanFactColumn").performClick()
        compose.onNodeWithTag("kanbanMove-backlog").assertExists().performClick()
        assertEquals("move", sent.single().optString("action"))
        assertEquals("backlog", sent.single().optString("column"))
        compose.onNodeWithTag("kanbanMoveOpen").performClick()
        compose.onNodeWithTag("kanbanMove-done").assertExists()
    }

    @Test fun aProposalPinsApproveAndDecline() {
        val sent = mutableListOf<JSONObject>()
        show(ticket("t300", "REM-300", "Fold long notes", "backlog", reason = "Short reason.", proposed = true), sent)
        compose.onNodeWithTag("kanbanStatusChip").assertTextContains("Proposed")
        compose.onAllNodesWithTag("kanbanMoveOpen").assertCountEquals(0)
        compose.onNodeWithText("Short reason.").assertExists()
        compose.onNodeWithTag("kanbanTicketApprove").assertIsDisplayed().performClick()
        assertEquals("approve", sent.single().optString("action"))
        assertEquals("t300", sent.single().optString("ticket_id"))
        screenshot("ticket-detail-proposal.png")
    }

    @Test fun aProposalShowsItsDoneWhenOneCheckPerLine() {
        show(ticket("t301", "REM-301", "Faster board", "backlog", reason = "Short reason.", proposed = true,
            doneWhen = "The board opens in under 1 s\n\n  npm test passes  "))
        compose.onNodeWithTag("kanbanDoneWhen").performScrollTo().assertIsDisplayed()
        compose.onNode(hasText("Done when") and hasAnyAncestor(hasTestTag("kanbanDoneWhen")), useUnmergedTree = true).assertExists()
        compose.onNode(hasText("The board opens in under 1 s") and hasAnyAncestor(hasTestTag("kanbanDoneWhen")), useUnmergedTree = true).assertExists()
        compose.onNode(hasText("npm test passes") and hasAnyAncestor(hasTestTag("kanbanDoneWhen")), useUnmergedTree = true).assertExists()
        screenshot("ticket-detail-done-when.png")
    }

    @Test fun aTicketWithoutAFinishLineHasNoDoneWhen() {
        show(later)
        compose.onAllNodesWithTag("kanbanDoneWhen").assertCountEquals(0)
    }
}
