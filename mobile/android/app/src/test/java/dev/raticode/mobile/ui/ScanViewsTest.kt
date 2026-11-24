package dev.raticode.mobile.ui

import dev.raticode.mobile.live.Kanban
import dev.raticode.mobile.live.KanbanBoard
import dev.raticode.mobile.live.KanbanTicket
import dev.raticode.mobile.live.OrganizationTask
import dev.raticode.mobile.live.TicketWait
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** The plain logic behind the scannable detail screens: previews, the stage strip, related tickets and pinned actions. */
class ScanViewsTest {
    private val why = "grok_accounts.py already declares device_sign_in_args=('login','--device-auth'). xAI documents that device code login " +
        "'follows RFC 8628'. REM-196's branch (bbb5303e) adds phone_sign_in per adapter but enables it only for Codex."

    @Test fun aPreviewIsTheFirstSentence() {
        assertEquals("grok_accounts.py already declares device_sign_in_args=('login','--device-auth').", ScanText.firstSentence(why))
        assertTrue(ScanText.hasMore(why))
    }

    @Test fun aPreviewDropsMarkdownAndLinkTargets() {
        assertEquals("Read the pairing guide first.", ScanText.firstSentence("**Read** the [pairing guide](https://raticode.dev/mobile) first. Then pair."))
        assertEquals("One item and another", ScanText.firstSentence("- One item\n- and another"))
    }

    @Test fun aShortTextHasNothingMore() {
        assertEquals("Researched 1 project.", ScanText.firstSentence("Researched 1 project."))
        assertFalse(ScanText.hasMore("Researched 1 project."))
        assertFalse(ScanText.hasMore("  "))
        assertEquals("", ScanText.firstSentence(""))
    }

    @Test fun aLongSentenceIsCutWithAnEllipsis() {
        val long = "word ".repeat(80).trim()
        val preview = ScanText.firstSentence(long, max = 40)
        assertTrue(preview.endsWith("…"))
        assertTrue(preview.length <= 40)
        assertTrue(ScanText.hasMore(long, max = 40))
    }

    @Test fun decimalsAndVersionsDontEndASentence() {
        assertEquals("Mobile 0.34.0 shipped with 1.5 MB less.", ScanText.firstSentence("Mobile 0.34.0 shipped with 1.5 MB less. It also adds icons."))
    }

    @Test fun theStripHasFiveStepsAndBlockedIsOffIt() {
        assertEquals(listOf("backlog", "todo", "running", "review", "done"), ScanText.STEPS)
        assertEquals(2, ScanText.stepOf("running"))
        assertEquals(4, ScanText.stepOf("done"))
        assertEquals(-1, ScanText.stepOf("blocked"))
        assertEquals(-1, ScanText.stepOf(null))
    }

    private fun ticket(id: String, key: String, status: String, waits: List<TicketWait> = emptyList(), org: String = "") =
        KanbanTicket(id, if (org.isBlank()) Kanban.SCOPE_REM else Kanban.SCOPE_ORG, org, if (org.isBlank()) "" else "Acme", key, "Ticket $key", "", status, "medium",
            if (org.isBlank()) Kanban.REM else "", if (org.isBlank()) "Rem" else "", false, "", "", "", "", "", 1, false, "", waitsFor = waits)

    @Test fun waitedOnByListsTicketsThatWaitForThisOneOpenFirst() {
        val target = ticket("a", "REM-196", "done")
        val wait = TicketWait("a", "REM-196", "Ticket REM-196", "done")
        val board = KanbanBoard(listOf(target,
            ticket("b", "REM-210", "done", listOf(wait)),
            ticket("c", "REM-202", "todo", listOf(wait)),
            ticket("d", "REM-203", "cancelled", listOf(wait)),
            ticket("e", "REM-204", "todo"),
            // Same ticket ID on another organization's board isn't this one.
            ticket("f", "ACM-1", "todo", listOf(TicketWait("a", "ACM-0", "x", "done")), org = "acme")), emptyList(), 0)
        assertEquals(listOf("REM-202", "REM-210"), Kanban.waitedOnBy(board, target).map { it.key })
    }

    @Test fun factsHaveShortWords() {
        val t = ticket("c", "REM-202", "todo", listOf(TicketWait("a", "REM-196", "x", "done"), TicketWait("b", "REM-197", "y", "todo")))
        assertEquals(1 to 2, Kanban.waitsDone(t))
        assertEquals("Build", Kanban.kindLabel("develop"))
        assertEquals("Errand", Kanban.kindLabel("errand"))
        assertEquals("Custom", Kanban.kindLabel("custom"))
        assertEquals("Critical", Kanban.priorityLabel("critical"))
    }

    private fun task(status: String, running: Boolean = false, approval: Boolean = false, review: Boolean = false) =
        OrganizationTask("t", "Ship it", "", status, "medium", "", 1, running, approval, review, false, "", "", "", emptyList())

    private fun actions(task: OrganizationTask) = orgTicketActions(task, dev.raticode.mobile.live.OrganizationModel.columnOf(task.status), {}, {}, {}, {})

    @Test fun reviewPutsAcceptAndRequestChangesFirstAndKeepsEveryOtherAction() {
        val all = actions(task("in_review", review = true))
        assertEquals(listOf("Accept", "Request changes", "Move", "Comment", "Cancel ticket"), all.map { it.label })
        // Cancel never takes a pinned slot.
        assertTrue(all.last().destructive)
    }

    @Test fun aPlanWaitingForApprovalLeadsWithApprovePlan() {
        assertEquals(listOf("Approve plan", "Start next", "Move", "Comment", "Cancel ticket"), actions(task("todo", approval = true)).map { it.label })
    }

    @Test fun aFinishedTicketOffersReopenAndNoCancel() {
        assertEquals(listOf("Reopen", "Move", "Comment"), actions(task("done")).map { it.label })
        assertEquals(listOf("Reopen", "Comment"), actions(task("cancelled")).map { it.label })
    }
}
