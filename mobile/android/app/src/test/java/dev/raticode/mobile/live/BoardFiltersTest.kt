package dev.raticode.mobile.live

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Board filters kept on the phone: one set per board, read back after a restart, with gone values falling back one at a time. */
class BoardFiltersTest {
    private val board = DesktopViews.parse(Kanban.BOARD, JSONObject("""{"digest":"d","earlier":0,
        "organizations":[{"organization_id":"acme","name":"Acme","state":"running","employees":[{"employee_id":"ana","name":"Ana","title":"Lead"}]}],
        "tickets":[
          {"ticket_id":"1","scope":"rem","key":"REM-1","title":"Mine","status":"todo","priority":"low","assignee_id":"rem","assignee":"Rem","revision":3,"project":"Raticode"},
          {"ticket_id":"2","scope":"organization","organization_id":"acme","organization_name":"Acme","key":"ACM-1","title":"Theirs","status":"todo","priority":"high","assignee_id":"ana","assignee":"Ana","revision":7,"project":"Site"}
        ]}""")) as KanbanBoard

    private fun read(name: String) = JSONObject(javaClass.getResource("/desktop/$name")!!.readText())
    private val org = DesktopViews.parse("organizations.read:x", read("organizations-read-v2.json")) as Organization
    private val plan = org.plan!!

    @Test fun theGlobalBoardsFiltersComeBackAfterARestart() {
        val filters = GlobalBoardFilters(scope = Kanban.SCOPE_REM, assignee = Kanban.REM, project = "Raticode", column = "review")
        assertEquals(filters, BoardFilters.parseGlobal(BoardFilters.encode(filters, 1L)))
        assertEquals(3, filters.active)
        assertEquals(GlobalBoardFilters(column = "review"), filters.cleared())
    }

    @Test fun anOrganizationsFiltersComeBackAfterARestart() {
        val filters = OrganizationBoardFilters(project = "app", worker = "w1", needsMe = true, column = "done")
        assertEquals(filters, BoardFilters.parseOrganization(BoardFilters.encode(filters, 1L)))
        assertEquals(3, filters.active)
        assertEquals(OrganizationBoardFilters(column = "done"), filters.cleared())
        assertEquals("organization:acme", BoardFilters.organization("acme"))
    }

    @Test fun nothingSavedOrSomethingDamagedStartsWithEverything() {
        assertEquals(GlobalBoardFilters(), BoardFilters.parseGlobal(null))
        assertEquals(GlobalBoardFilters(), BoardFilters.parseGlobal("{not json"))
        assertEquals(OrganizationBoardFilters(), BoardFilters.parseOrganization(""))
        // Wrong types and unknown columns fall back on their own; the rest is kept.
        assertEquals(GlobalBoardFilters(scope = "acme"), BoardFilters.parseGlobal("""{"scope":"acme","assignee":7,"column":"later"}"""))
        assertEquals(OrganizationBoardFilters(worker = "w1"), BoardFilters.parseOrganization("""{"worker":"w1","needs_me":"yes","project":${"\"" + "x".repeat(600) + "\""}}"""))
        assertEquals("board", BoardFilters.parseTab(null))
        assertEquals("board", BoardFilters.parseTab("chart"))
        assertEquals("people", BoardFilters.parseTab("people"))
    }

    @Test fun theSavedTabsAreTheBoardsSections() {
        assertEquals(setOf(dev.raticode.mobile.ui.KanbanTabs.BOARD, dev.raticode.mobile.ui.KanbanTabs.PROPOSED, dev.raticode.mobile.ui.KanbanTabs.PEOPLE,
            dev.raticode.mobile.ui.KanbanTabs.SELF), BoardFilters.TABS)
    }

    @Test fun aGoneOrganizationPersonOrProjectShowsEverythingAndKeepsTheRest() {
        val kept = GlobalBoardFilters(scope = "acme", assignee = "ana", project = "Site", column = "done")
        assertEquals(kept, BoardFilters.live(kept, board))
        // The organization is gone: the board shows everyone's tickets, still for the same person and project.
        assertEquals(kept.copy(scope = Kanban.ALL), BoardFilters.live(kept.copy(scope = "gone"), board))
        // Rem's tickets only, with an organization's worker saved: that person isn't on Rem's tickets, so anyone.
        assertEquals(kept.copy(scope = Kanban.SCOPE_REM, assignee = Kanban.ANY), BoardFilters.live(kept.copy(scope = Kanban.SCOPE_REM), board))
        // A person who left: the organization stays.
        assertEquals(kept.copy(assignee = Kanban.ANY), BoardFilters.live(kept.copy(assignee = "left"), board))
        // A project with no tickets left on the board has no menu entry, so it can't hide every ticket.
        assertEquals(kept.copy(project = Kanban.ALL), BoardFilters.live(kept.copy(project = "Old"), board))
        assertEquals(GlobalBoardFilters(scope = Kanban.SCOPE_REM, assignee = Kanban.REM), BoardFilters.live(GlobalBoardFilters(scope = Kanban.SCOPE_REM, assignee = Kanban.REM), board))
    }

    @Test fun aGoneProjectOrWorkerOnAnOrganizationsBoardFallsBackAlone() {
        val worker = org.employees.first().id
        val project = plan.projects.single().id
        val kept = OrganizationBoardFilters(project = project, worker = worker, needsMe = true)
        assertEquals(kept, BoardFilters.live(kept, org, plan))
        assertEquals(kept.copy(project = ""), BoardFilters.live(kept.copy(project = "removed"), org, plan))
        assertEquals(kept.copy(worker = ""), BoardFilters.live(kept.copy(worker = "left"), org, plan))
    }

    @Test fun onlyTheMostRecentlyChangedBoardsAreKept() {
        val saved = (0 until 120).associate { BoardFilters.organization("o$it") to BoardFilters.encode(OrganizationBoardFilters(needsMe = true), it.toLong()) } +
            (BoardFilters.GLOBAL_TAB to "people") + (BoardFilters.GLOBAL to BoardFilters.encode(GlobalBoardFilters(scope = "rem"), 0L))
        val dropped = BoardFilters.overflow(saved, BoardFilters.organization("new"))
        assertEquals(21, dropped.size)
        assertTrue("the global board and its tab stay", dropped.none { it == BoardFilters.GLOBAL_TAB || it == BoardFilters.GLOBAL })
        assertTrue("the oldest go first", dropped.containsAll((0 until 21).map { BoardFilters.organization("o$it") }))
        assertEquals(emptyList<String>(), BoardFilters.overflow(mapOf(BoardFilters.GLOBAL to "{}"), BoardFilters.GLOBAL))
    }
}
