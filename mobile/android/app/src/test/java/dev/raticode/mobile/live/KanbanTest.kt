package dev.raticode.mobile.live

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The global board and Rem's self-work on the phone: reading the desktop's answers and the rules for what is offered. */
class KanbanTest {
    private val boardJson = JSONObject("""{"digest":"d","earlier":2,
        "organizations":[{"organization_id":"acme","name":"Acme","state":"running","employees":[{"employee_id":"ana","name":"Ana","title":"Lead"}]},
                         {"organization_id":"beta","name":"Beta","state":"paused","employees":[]}],
        "tickets":[
          {"ticket_id":"1","scope":"rem","key":"REM-1","title":"Mine","status":"todo","priority":"low","assignee_id":"rem","assignee":"Rem","revision":3,"created_at":"2026-11-08T10:00:00Z"},
          {"ticket_id":"1","scope":"organization","organization_id":"acme","organization_name":"Acme","key":"ACM-1","title":"Theirs","status":"in_progress","priority":"high","assignee_id":"ana","assignee":"Ana","revision":7,"running":true},
          {"ticket_id":"2","scope":"rem","key":"REM-2","title":"Idea","status":"backlog","priority":"medium","proposed":true,"proposal_reason":"People asked","duplicate_of":"REM-1","revision":1,"created_at":"2026-11-08T11:00:00Z"},
          {"ticket_id":"3","scope":"organization","organization_id":"beta","organization_name":"Beta","title":"Stuck","status":"blocked","revision":1,"block_reason":"Needs a key"},
          {"ticket_id":"4","scope":"rem","title":"Gone","status":"cancelled","revision":1},
          {"title":"no id","status":"todo"}
        ]}""")
    private val board = DesktopViews.parse(Kanban.BOARD, boardJson) as KanbanBoard

    private val selfJson = JSONObject("""{"settings":{"enabled":true,"max_threads":3,"auto_pull_backlog":false,"idle_minutes":15,"revision":9},
        "limits":{"max_threads_min":1,"max_threads_max":6},
        "state":{"mode":"working","active_threads":2,"thread_limit":3,"proposals_waiting":1,"todo_remaining":4,"last_prompt_at":"2026-11-08T09:00:00Z","starts_at":"","detail":""},
        "report":{"since":"2026-11-08T09:15:00Z","updated_at":"2026-11-08T12:00:00Z","unseen":true,"summary":"Good progress.",
          "tickets_added":[{"ticket_id":"2","title":"Idea","scope":"rem","proposed":true,"at":"2026-11-08T11:00:00Z"},
                           {"ticket_id":"8","title":"Done idea","scope":"organization","organization_id":"acme","organization_name":"Acme","proposed":false,"at":""}],
          "work_done":[{"ticket_id":"1","title":"Mine","state":"done","summary":"Finished","thread_id":"t","at":"2026-11-08T11:30:00Z"}],
          "duplicates_merged":[{"ticket_id":"9","title":"Twin","kept_title":"Idea","reason":"Same work","at":"2026-11-08T11:40:00Z"}],
          "notes":[{"text":"Research stopped","at":"2026-11-08T11:50:00Z"}]}}""")
    private val selfWork = DesktopViews.parse(Kanban.SELF_WORK, selfJson) as SelfWorkView

    private fun ticket(id: String) = board.tickets.first { it.uid == id }

    @Test fun readsTheBoardAndSkipsTicketsWithoutAnId() {
        assertEquals(5, board.tickets.size)
        assertEquals(2, board.earlier)
        assertEquals("Ana", board.organizations.first().employees.single().name)
        assertEquals("critical", (DesktopViews.parse(Kanban.BOARD, JSONObject("""{"tickets":[{"ticket_id":"1","priority":"urgent"}]}""")) as KanbanBoard).tickets.single().priority)
        assertEquals("Acme", ticket("acme/1").owner)
        assertEquals("Rem", ticket("/1").owner)
        assertTrue(ticket("/2").proposed)
        assertEquals("People asked", ticket("/2").proposalReason)
    }

    @Test fun theSameTicketIdInTwoPlacesStaysTwoTickets() {
        assertEquals(board.tickets.size, board.tickets.map { it.uid }.toSet().size)
    }

    @Test fun boardLeavesOutProposalsAndCancelledWork() {
        assertEquals(listOf("/1", "acme/1", "beta/3"), Kanban.shown(board, Kanban.ALL, Kanban.ANY).map { it.uid })
        assertEquals(listOf("/2"), Kanban.proposed(board).map { it.uid })
    }

    @Test fun filtersByPlaceAndAssignee() {
        assertEquals(listOf("/1"), Kanban.shown(board, Kanban.SCOPE_REM, Kanban.ANY).map { it.uid })
        assertEquals(listOf("acme/1"), Kanban.shown(board, "acme", Kanban.ANY).map { it.uid })
        assertEquals(listOf("acme/1"), Kanban.shown(board, Kanban.ALL, "ana").map { it.uid })
        assertEquals(listOf("/1"), Kanban.shown(board, Kanban.ALL, Kanban.REM).map { it.uid })
        assertEquals(listOf("beta/3"), Kanban.shown(board, Kanban.ALL, Kanban.UNASSIGNED).map { it.uid })
    }

    @Test fun runningThenBlockedThenPriorityComesFirst() {
        assertEquals(listOf("acme/1", "beta/3", "/1"), Kanban.ordered(Kanban.shown(board, Kanban.ALL, Kanban.ANY)).map { it.uid })
    }

    @Test fun assigneesFollowThePlace() {
        assertEquals(listOf(Kanban.REM to "Rem"), Kanban.assignees(board, Kanban.SCOPE_REM, ""))
        assertEquals(listOf(Kanban.UNASSIGNED to "Unassigned", "ana" to "Ana"), Kanban.assignees(board, Kanban.SCOPE_ORG, "acme"))
        assertEquals(listOf(Kanban.SCOPE_REM to "Rem", "acme" to "Acme", "beta" to "Beta"), Kanban.places(board))
        assertTrue("ana" in Kanban.assigneeFilters(board, Kanban.ALL).map { it.first })
        assertFalse("ana" in Kanban.assigneeFilters(board, Kanban.SCOPE_REM).map { it.first })
    }

    @Test fun readsRealAnswersFromTheDesktop() {
        // Written by the desktop's own handlers (test_rem_kanban.py's phone tests build the same state), so a renamed field fails here.
        fun read(name: String) = JSONObject(javaClass.getResource("/desktop/$name")!!.readText())
        val real = DesktopViews.parse(Kanban.BOARD, read("kanban-board.json")) as KanbanBoard
        assertTrue(real.organizations.single().employees.any { it.id == "lead" })
        val rem = real.tickets.first { it.title == "Rem todo" }
        assertTrue(rem.isRem); assertEquals(Kanban.REM, rem.assigneeId); assertEquals("high", rem.priority); assertEquals("", rem.organizationId)
        assertEquals("The board shows it\nnpm test passes", rem.doneWhen)
        assertEquals(listOf("The board shows it", "npm test passes"), rem.doneWhenLines)
        assertEquals(emptyList<String>(), real.tickets.first { it.title == "Proposed idea" }.doneWhenLines)
        val org = real.tickets.first { it.title == "Org ticket" }
        assertFalse(org.isRem); assertEquals("critical", org.priority); assertEquals("Eng", org.owner); assertEquals("Eng signs it off", org.doneWhen)
        assertTrue(org.key.startsWith("ENG") || org.key.isNotBlank())
        assertEquals(listOf("Proposed idea", "Second idea"), Kanban.proposed(real).map { it.title }.sorted())
        assertEquals("Why it helps", real.tickets.first { it.title == "Proposed idea" }.proposalReason)
        assertEquals("done", real.tickets.first { it.title == "Done work" }.status)
        assertEquals(real.tickets.size, real.tickets.map { it.uid }.toSet().size)
        val view = DesktopViews.parse(Kanban.SELF_WORK, read("selfwork-read.json")) as SelfWorkView
        assertEquals(1, view.limits.min); assertEquals(16, view.limits.max)
        assertEquals(15, view.settings.idleMinutes)
        val report = view.report!!
        assertTrue(report.unseen); assertEquals(1, report.workDone.size); assertEquals("done", report.workDone.single().state)
        assertEquals(2, report.ticketsAdded.count { it.proposed })
        assertEquals("Research stopped for now", report.notes.single().text)
        assertEquals("2 tickets added · 2 to approve · 1 done", Kanban.reportHeadline(report))
        assertTrue(Kanban.status(view).isNotBlank())
    }

    private val peopleJson = JSONObject("""{"tickets":[
          {"ticket_id":"1","scope":"rem","title":"Mine","status":"todo","assignee_id":"rem","assignee":"Rem","project":"App","project_id":"p1"},
          {"ticket_id":"2","scope":"rem","title":"Other","status":"backlog","assignee_id":"rem","assignee":"Rem","project":"Site","project_id":"p2"},
          {"ticket_id":"9","scope":"rem","title":"Finished","status":"done","assignee_id":"rem","project":"App","project_id":"p1"},
          {"ticket_id":"3","scope":"organization","organization_id":"acme","organization_name":"Acme","title":"Ana's","status":"in_progress","assignee_id":"ana","assignee":"Ana","project":"Site","project_id":"p2"},
          {"ticket_id":"4","scope":"organization","organization_id":"beta","organization_name":"Beta","title":"Same id, other org","status":"todo","assignee_id":"ana"}],
        "organizations":[],
        "projects":[{"project_id":"p1","name":"App"},{"project_id":"p2","name":"Site"},{"project_id":"","name":"dropped"}],
        "participants":[
          {"participant_id":"agent:acme:ana","kind":"agent","name":"Ana","title":"Lead","organization_id":"acme","organization_name":"Acme","employee_id":"ana","projects":["p2"],"assignable":["p2"],"hidden_projects":0,"revision":4},
          {"participant_id":"agent:beta:ana","kind":"agent","name":"Bo","organization_id":"beta","organization_name":"Beta","employee_id":"ana","paused":true,"projects":[],"assignable":[],"hidden_projects":2,"revision":1},
          {"participant_id":"agent:acme:cy","kind":"agent","name":"Cy","organization_id":"acme","organization_name":"Acme","employee_id":"cy","projects":[],"assignable":["p2"],"hidden_projects":0,"revision":1},
          {"participant_id":"rem","kind":"rem","name":"Rem","title":"Coordinator","projects":["p1"],"assignable":["p1","p2"],"hidden_projects":1,"revision":7},
          {"name":"no id"}]}""")
    private val people = DesktopViews.parse(Kanban.BOARD, peopleJson) as KanbanBoard
    private fun who(id: String) = people.participants.first { it.id == id }

    @Test fun readsPeopleAndTheirProjects() {
        assertTrue(people.participantsKnown)
        assertEquals(4, people.participants.size)
        assertEquals(listOf("p1", "p2"), people.projects.map { it.id })
        assertTrue(who("rem").isRem); assertFalse(who("agent:acme:ana").isRem)
        assertEquals(listOf("p1", "p2"), who("rem").assignable)
        assertTrue(who("agent:beta:ana").paused); assertEquals(2, who("agent:beta:ana").hiddenProjects)
        assertEquals("p1", people.tickets.first().projectId)
    }

    @Test fun readsRealPeopleFromTheDesktop() {
        // Written by the desktop's own handlers.
        fun read(name: String) = JSONObject(javaClass.getResource("/desktop/$name")!!.readText())
        val real = DesktopViews.parse(Kanban.BOARD, read("kanban-board-people.json")) as KanbanBoard
        assertTrue(real.participantsKnown)
        val rem = Kanban.people(real).first()
        assertTrue(rem.isRem); assertEquals("Rem", rem.name); assertEquals("Coordinator", rem.title)
        assertEquals(listOf("project"), Kanban.projectNames(real, rem))
        assertEquals(2, Kanban.projectChoices(real, rem).size)
        val lead = Kanban.people(real).last()
        assertFalse(lead.isRem); assertEquals("Lead", lead.name); assertEquals("Eng", lead.organizationName); assertEquals("lead", lead.employeeId)
        assertTrue(lead.id.startsWith("agent:"))
        assertEquals("No projects, so no self-work.", Kanban.boundaryText(lead))
        assertEquals("Eng", real.projects.first { it.name == "project" }.organization)
        assertEquals("", Kanban.outsideProject(real.tickets.first { it.title == "Inside" }))
        assertTrue(Kanban.outsideProject(real.tickets.first { it.title == "Outside" }).startsWith("Rem isn't assigned to notes"))
        assertTrue(Kanban.outsideProject(real.tickets.first { it.title == "No project" }).startsWith("This ticket has no project"))
        val view = DesktopViews.parse(Kanban.SELF_WORK, read("selfwork-read-people.json")) as SelfWorkView
        assertEquals(1, view.state.assignedProjects); assertEquals(2, view.state.outsideProjects)
    }

    @Test fun aDesktopWithoutPeopleIsToldApart() {
        assertFalse(board.participantsKnown)
        assertTrue(board.participants.isEmpty())
    }

    @Test fun remComesFirstThenEachOrganizationsPeople() {
        assertEquals(listOf("rem", "agent:acme:ana", "agent:acme:cy", "agent:beta:ana"), Kanban.people(people).map { it.id })
        assertEquals(listOf("Rem", "Acme", "Beta"), Kanban.peopleSections(Kanban.people(people)).map { it.first })
    }

    @Test fun findsPeopleByNameTitleOrOrganization() {
        assertEquals(listOf("agent:acme:ana"), people.participants.filter { Kanban.matchesPerson(it, "lead") }.map { it.id })
        assertEquals(listOf("agent:acme:ana", "agent:acme:cy"), people.participants.filter { Kanban.matchesPerson(it, "acme") }.map { it.id })
        assertEquals(listOf("agent:acme:ana"), people.participants.filter { Kanban.matchesPerson(it, "ana acme") }.map { it.id })
        assertEquals(4, people.participants.count { Kanban.matchesPerson(it, "  ") })
    }

    @Test fun projectNamesFollowTheDesktopsOrderAndNeverShowAnId() {
        assertEquals(listOf("App"), Kanban.projectNames(people, who("rem")))
        val gone = who("rem").copy(projects = listOf("p2", "gone", "p1"))
        assertEquals(listOf("App", "Site", "A project that isn't open"), Kanban.projectNames(people, gone))
    }

    @Test fun saysWhereSomeoneCanWork() {
        assertEquals("No projects, so no self-work.", Kanban.boundaryText(who("agent:acme:cy")))
        assertEquals("2 projects this phone can't see.", Kanban.boundaryText(who("agent:beta:ana")))
        assertEquals("Plus 1 this phone can't see.", Kanban.boundaryText(who("rem")))
        assertEquals("", Kanban.boundaryText(who("agent:acme:ana")))
    }

    @Test fun projectChoicesKeepAnAssignedProjectThatCanNoLongerBeGiven() {
        val odd = who("agent:acme:ana").copy(projects = listOf("p1", "p2"), assignable = listOf("p2"))
        assertEquals(listOf("p1", "p2"), Kanban.projectChoices(people, odd).map { it.id })
        assertEquals(listOf("p2"), Kanban.projectChoices(people, who("agent:acme:cy")).map { it.id })
    }

    @Test fun togglingAndComparingProjects() {
        assertEquals(listOf("a", "b"), Kanban.toggled(listOf("a"), "b"))
        assertEquals(listOf("a"), Kanban.toggled(listOf("a", "b"), "b"))
        assertTrue(Kanban.sameProjects(listOf("a", "b"), listOf("b", "a")))
        assertFalse(Kanban.sameProjects(listOf("a"), listOf("a", "b")))
    }

    @Test fun savingSendsTheRevisionTheEditBeganFrom() {
        val params = Kanban.participantParams(who("rem"), listOf("p2", "p1", "p2"), revision = 3)
        assertEquals("rem", params.getString("participant_id"))
        assertEquals(3L, params.getLong("expected_revision"))
        assertEquals(listOf("p1", "p2"), (0 until params.getJSONArray("projects").length()).map { params.getJSONArray("projects").getString(it) })
        assertEquals(7L, Kanban.participantParams(who("rem"), emptyList()).getLong("expected_revision"))
        assertEquals(0, Kanban.participantParams(who("rem"), emptyList()).getJSONArray("projects").length())
    }

    @Test fun openTicketsBelongToThePersonNotJustTheirId() {
        assertEquals(listOf("/2", "/1").toSet(), Kanban.openTicketsOf(people, who("rem")).map { it.uid }.toSet())
        assertEquals(listOf("acme/3"), Kanban.openTicketsOf(people, who("agent:acme:ana")).map { it.uid })
        assertEquals(listOf("beta/4"), Kanban.openTicketsOf(people, who("agent:beta:ana")).map { it.uid })
        assertEquals("/1", people.tickets.first { it.uid == "/1" }.uid)
        assertEquals("agent:beta:ana", Kanban.assignedPerson(people, people.tickets.first { it.uid == "beta/4" })!!.id)
    }

    @Test fun warnsWhenSelfWorkWontTakeARemTicket() {
        val base = people.tickets.first { it.uid == "/1" }
        assertEquals("", Kanban.outsideProject(base.copy(selfWorkScope = "assigned")))
        assertTrue(Kanban.outsideProject(base.copy(selfWorkScope = "no_project")).startsWith("This ticket has no project"))
        assertTrue(Kanban.outsideProject(base.copy(selfWorkScope = "unassigned")).startsWith("Rem isn't assigned to App"))
        assertTrue(Kanban.outsideProject(base.copy(selfWorkScope = "unavailable")).startsWith("App isn't open"))
        assertEquals("", Kanban.outsideProject(base.copy(selfWorkScope = "unassigned", status = "done")))
        assertEquals("", Kanban.outsideProject(people.tickets.first { it.uid == "acme/3" }))
        assertEquals("", Kanban.outsideProject(people, "p1"))
        assertEquals("Rem isn't assigned to Site, so it won't pick this up during self-work.", Kanban.outsideProject(people, "p2"))
        assertEquals("With no project, Rem won't pick this up during self-work.", Kanban.outsideProject(people, ""))
        assertEquals("", Kanban.outsideProject(board, "p1"))
    }

    @Test fun aClosedProjectCanOnlyBeRemoved() {
        val rem = who("rem").copy(unavailable = listOf("p1"))
        assertFalse(Kanban.canAdd(rem, KanbanProject("p1", "App")))
        assertTrue(Kanban.canAdd(rem, KanbanProject("p2", "Site")))
        assertFalse(Kanban.canAdd(rem, KanbanProject("p2", "Site", available = false)))
        assertEquals("1 not open on your computer, so unused for now.", Kanban.boundaryText(rem.copy(hiddenProjects = 0)))
        assertEquals(listOf("A project that isn't open"), Kanban.projectChoices(people, who("rem").copy(projects = listOf("gone"), assignable = emptyList())).map { it.name })
        assertFalse(Kanban.projectChoices(people, who("rem").copy(projects = listOf("gone"), assignable = emptyList())).single().available)
    }

    @Test fun onePersonsBoardFilterAndTheProjectFilter() {
        assertEquals(Kanban.SCOPE_REM to Kanban.REM, Kanban.boardFilterFor(who("rem")))
        assertEquals("acme" to "ana", Kanban.boardFilterFor(who("agent:acme:ana")))
        assertEquals(listOf(Kanban.ALL, "App", "Site"), Kanban.projectFilters(people).map { it.first })
        assertEquals(listOf("/2", "acme/3"), Kanban.shown(people, Kanban.ALL, Kanban.ANY, "Site").map { it.uid })
        assertTrue(Kanban.projectFilters(board).isEmpty())
    }

    @Test fun selfWorkSaysHowManyProjectsRemHas() {
        val json = JSONObject(selfJson.toString()); json.getJSONObject("state").put("assigned_projects", 2).put("outside_projects", 3)
        val view = DesktopViews.parse(Kanban.SELF_WORK, json) as SelfWorkView
        assertEquals(2, view.state.assignedProjects); assertEquals(3, view.state.outsideProjects)
        assertNull(selfWork.state.assignedProjects)
    }

    @Test fun selfWorkKnowsWhenTheBoardIsOffAndOlderDesktopsCountAsOn() {
        // An older desktop never says, and always worked with the board.
        assertTrue(selfWork.state.boardEnabled)
        assertNull(Kanban.boardOffNote(selfWork))
        assertEquals(Kanban.afterTodos(false), Kanban.autoPullDetail(selfWork))
        val json = JSONObject(selfJson.toString()); json.getJSONObject("state").put("board_enabled", false)
        val off = DesktopViews.parse(Kanban.SELF_WORK, json) as SelfWorkView
        assertFalse(off.state.boardEnabled)
        assertTrue(Kanban.boardOffNote(off)!!.startsWith("The Kanban board is off on your computer"))
        assertTrue(Kanban.autoPullDetail(off).startsWith("Needs the Kanban board"))
        // The saved settings read the same either way.
        assertEquals(selfWork.settings, off.settings)
    }

    @Test fun readsARealSelfWorkAnswerWithTheBoardOff() {
        val sample = JSONObject(javaClass.getResource("/desktop/selfwork-read-board-off.json")!!.readText())
        val view = DesktopViews.parse(Kanban.SELF_WORK, sample) as SelfWorkView
        assertFalse(view.state.boardEnabled)
        assertEquals(0, view.state.proposalsWaiting)
        val research = view.report!!.workDone.first { it.state == "researched" }
        assertTrue(research.summary.startsWith("Found 1 idea"))
        assertTrue(view.report!!.ticketsAdded.isEmpty())
    }

    @Test fun participantChangesRefreshTheBoardAndOverviewAdvertisesThem() {
        assertEquals(Kanban.BOARD, DesktopViews.key(Kanban.PARTICIPANT, JSONObject()))
        assertTrue(Kanban.PARTICIPANT in DesktopViews.ACTIONS)
        assertTrue((DesktopViews.parse("overview", JSONObject().put("kanban_participants", true)) as DesktopOverview).kanbanParticipants)
        assertFalse((DesktopViews.parse("overview", JSONObject().put("kanban", true)) as DesktopOverview).kanbanParticipants)
    }

    @Test fun aProposalMustBeAnsweredBeforeItMoves() {
        assertTrue(Kanban.move(ticket("/2"), "todo") is OrganizationModel.Move.Refused)
    }

    @Test fun remTicketsFollowRemsRules() {
        val todo = ticket("/1")
        assertTrue(Kanban.move(todo, "backlog") is OrganizationModel.Move.Allowed)
        assertNull(Kanban.move(todo, "todo"))
        assertTrue((Kanban.move(todo, "running") as OrganizationModel.Move.Allowed).pull)
        assertTrue(Kanban.move(todo, "review") is OrganizationModel.Move.Refused)
        assertTrue(Kanban.move(todo, "done") is OrganizationModel.Move.Allowed)
        assertTrue(Kanban.move(todo.copy(running = true), "backlog") is OrganizationModel.Move.Refused)
        val blocked = todo.copy(status = "blocked", blockReason = "No key")
        assertTrue((Kanban.move(blocked, "todo") as OrganizationModel.Move.Allowed).reopen)
        assertTrue(Kanban.move(blocked, "running") is OrganizationModel.Move.Refused)
        // Blocked is where work lands on its own, never a place to drag it.
        assertNull(Kanban.move(blocked, "blocked"))
        assertTrue(Kanban.move(todo, "blocked") is OrganizationModel.Move.Refused)
        assertTrue((Kanban.move(todo.copy(status = "done"), "todo") as OrganizationModel.Move.Allowed).reopen)
    }

    @Test fun undoFollowsTheCasesTheDesktopIsTestedAgainst() {
        // undo-move.json is shared with frontend/src/lib/remKanban.test.mjs, so both apps offer Undo for the same moves.
        val cases = JSONObject(javaClass.getResource("/undo-move.json")!!.readText()).getJSONArray("cases")
        assertTrue(cases.length() > 0)
        val base = ticket("/1")
        for (i in 0 until cases.length()) {
            val case = cases.getJSONObject(i)
            val scope = case.getString("scope")
            val paused = case.getString("paused")
            val column = case.getString("column")
            val before = base.copy(scope = scope, status = case.getString("status"), paused = paused)
            val why = case.optString("why").let { if (it.isBlank()) "" else ": $it" }
            val name = "${scope.ifBlank { "no scope" }} ${before.status}${if (paused.isNotBlank()) " paused" else ""} to $column$why"
            assertEquals(name, case.getBoolean("undo"), Kanban.canUndoMove(before, column))
        }
    }

    @Test fun aMoveSaysWhatItDidInTheDesktopsWords() {
        val rem = ticket("/1").copy(key = "REM-12")
        assertEquals("REM-12 moved to Backlog.", Kanban.moveNotice(rem, "backlog"))
        assertEquals("REM-12 moved to To do.", Kanban.moveNotice(rem, "todo"))
        assertEquals("REM-12 moved to Done.", Kanban.moveNotice(rem, "done"))
        assertEquals("REM-12 cancelled.", Kanban.moveNotice(rem, "cancelled"))
        assertEquals("REM-12 started.", Kanban.moveNotice(rem, "running"))
        assertEquals("REM-12 resumed.", Kanban.moveNotice(rem.copy(paused = "user"), "running"))
        assertEquals("ACM-1 moved to In progress.", Kanban.moveNotice(ticket("acme/1").copy(status = "todo"), "running"))
        assertEquals("The ticket moved to Backlog.", Kanban.moveNotice(rem.copy(key = ""), "backlog"))
    }

    @Test fun undoSendsTheTicketBackAtItsNewRevision() {
        val before = ticket("/1").copy(key = "REM-12")
        val move = KanbanMove(before, "backlog")
        assertEquals("backlog", move.params().getString("column"))
        // Waits for the board to show the ticket at a newer revision.
        assertNull(Kanban.landed(move, board.tickets))
        val moved = before.copy(status = "backlog", revision = before.revision + 1)
        assertEquals(moved, Kanban.landed(move, listOf(ticket("acme/1"), moved)))
        val back = Kanban.undo(move, moved)!!
        assertTrue(back.back)
        val params = back.params()
        assertEquals("move", params.getString("action")); assertEquals("todo", params.getString("column"))
        assertEquals(moved.revision, params.getLong("expected_revision")); assertEquals(before.id, params.getString("ticket_id"))
        assertEquals("REM-12 moved back to To do.", Kanban.notice(back))
        // An Undo has no Undo of its own, and starting work has none.
        assertNull(Kanban.undo(back, moved.copy(status = "todo", revision = moved.revision + 1)))
        assertNull(Kanban.undo(KanbanMove(before, "running"), moved.copy(status = "in_progress")))
    }

    @Test fun onlyRemsOpenTicketsCanBeCancelledFromTheirPage() {
        val rem = ticket("/1")
        assertTrue(Kanban.canCancel(rem))
        assertTrue(Kanban.canCancel(rem.copy(status = "blocked")))
        assertFalse(Kanban.canCancel(rem.copy(status = "done")))
        assertFalse(Kanban.canCancel(rem.copy(status = "cancelled")))
        assertFalse(Kanban.canCancel(rem.copy(running = true)))
        assertFalse(Kanban.canCancel(rem.copy(paused = "user")))
        assertFalse(Kanban.canCancel(ticket("/2")))
        assertFalse(Kanban.canCancel(ticket("beta/3")))
    }

    @Test fun blockedWorkHasItsOwnColumn() {
        assertEquals(listOf("backlog", "todo", "running", "review", "blocked", "done"), Kanban.COLUMNS)
        assertEquals("Blocked", Kanban.COLUMN_LABELS["blocked"])
        assertEquals("blocked", Kanban.column(ticket("beta/3")))
        assertEquals("review", Kanban.column(ticket("/1").copy(status = "in_review")))
        // An organization's blocked ticket reopens straight into To do or Backlog, as on the desktop.
        assertTrue((Kanban.move(ticket("beta/3"), "todo") as OrganizationModel.Move.Allowed).reopen)
        assertTrue(Kanban.move(ticket("beta/3"), "review") is OrganizationModel.Move.Refused)
    }

    @Test fun organizationTicketsFollowTheirBoardsRules() {
        assertTrue(Kanban.move(ticket("acme/1"), "todo") is OrganizationModel.Move.Refused)
        assertTrue(Kanban.move(ticket("acme/1").copy(status = "in_review", running = false), "done") is OrganizationModel.Move.Review)
    }

    @Test fun remTicketsHandOverToAnOrganization() {
        val choices = Kanban.assignChoices(board, ticket("/1")).map { it.first }
        assertEquals(Kanban.REM, choices.first())
        val whole = choices.first { it == "${Kanban.HANDOVER}acme" }
        val worker = choices.first { it.endsWith("ana") }
        assertEquals("acme", Kanban.assignParams(ticket("/1"), whole).getString("to_organization_id"))
        assertEquals("", Kanban.assignParams(ticket("/1"), whole).getString("assignee_id"))
        assertEquals("ana", Kanban.assignParams(ticket("/1"), worker).getString("assignee_id"))
        assertEquals("acme", Kanban.assignParams(ticket("/1"), worker).getString("to_organization_id"))
        assertTrue(Kanban.canHandOver(ticket("/1")))
        assertFalse(Kanban.canHandOver(ticket("/2")))
        assertFalse(Kanban.canHandOver(ticket("/1").copy(running = true)))
        assertFalse(Kanban.canHandOver(ticket("/1").copy(status = "done")))
        assertEquals(Kanban.UNASSIGNED, Kanban.assignChoices(board, ticket("acme/1")).first().first)
        assertFalse(Kanban.assignChoices(board, ticket("acme/1")).any { it.first == Kanban.REM })
    }

    @Test fun actionsNameTheTicketAndItsRevision() {
        val assign = Kanban.assignParams(ticket("acme/1"), "ana")
        assertEquals("assign", assign.getString("action")); assertEquals("acme", assign.getString("organization_id"))
        assertEquals(7L, assign.getLong("expected_revision")); assertEquals("organization", assign.getString("scope"))
        val rem = Kanban.moveParams(ticket("/1"), "backlog")
        assertFalse(rem.has("organization_id")); assertEquals("backlog", rem.getString("column"))
        val decline = Kanban.decideParams(ticket("/2"), false, " not now ")
        assertEquals("decline", decline.getString("action")); assertEquals("not now", decline.getString("reason"))
        assertFalse(Kanban.decideParams(ticket("/2"), true, "x").has("reason"))
    }

    @Test fun editingARemTicketSendsOnlyWhatChangedAtItsRevision() {
        val mine = ticket("/1").copy(description = "Old details", kind = "develop", doneWhen = "It builds", doneWhenKnown = true)
        val start = Kanban.draftOf(mine)
        assertEquals(TicketDraft("Mine", "Old details", "low", "develop", "It builds", 3), start)
        fun edit(title: String = "Mine", description: String = "Old details", priority: String = "low", kind: String = "develop", doneWhen: String = "It builds") =
            Kanban.editParams(mine, start, start.copy(title = title, description = description, priority = priority, kind = kind, doneWhen = doneWhen))
        val params = edit(" Mine, renamed ", "New details", "critical", "research", " It builds \n\n The tests pass ")!!
        assertEquals("edit", params.getString("action")); assertEquals("1", params.getString("ticket_id")); assertEquals("rem", params.getString("scope"))
        assertEquals(3L, params.getLong("expected_revision")); assertFalse(params.has("organization_id"))
        assertEquals("Mine, renamed", params.getString("title")); assertEquals("New details", params.getString("description"))
        assertEquals("critical", params.getString("priority")); assertEquals("research", params.getString("type"))
        assertEquals("It builds\nThe tests pass", params.getString("done_when"))
        // Only the changed fields go; nothing changed, or no title, sends nothing.
        val one = edit(description = "Old details ", priority = "high", doneWhen = "It builds\n")!!
        assertEquals(setOf("action", "ticket_id", "scope", "expected_revision", "priority"), one.keys().asSequence().toSet())
        assertNull(edit())
        assertNull(edit(title = "  ", description = "Other"))
        // A description the board may have cut, and Done when from a desktop that doesn't send it, stay as they are.
        val long = mine.copy(description = "x".repeat(Kanban.DESCRIPTION_SHOWN), doneWhenKnown = false)
        val longStart = Kanban.draftOf(long)
        assertNull(Kanban.editParams(long, longStart, longStart.copy(description = "shorter", doneWhen = "A new check")))
        // Rem's tickets that aren't running or paused can be edited, proposals included; an organization's can't from here.
        assertTrue(Kanban.canEdit(mine)); assertTrue(Kanban.canEdit(ticket("/2")))
        assertFalse(Kanban.canEdit(mine.copy(running = true))); assertFalse(Kanban.canEdit(mine.copy(paused = "user")))
        assertFalse(Kanban.canEdit(mine.copy(status = "done"))); assertFalse(Kanban.canEdit(ticket("acme/1")))
        assertEquals(listOf("develop", "research", "maintain"), Kanban.editKinds(mine).map { it.first })
        assertEquals("errand", Kanban.editKinds(mine.copy(kind = "errand")).last().first)
        // The board says whether the desktop sends Done when.
        assertFalse(ticket("/1").doneWhenKnown)
        val sent = DesktopViews.parse(Kanban.BOARD, JSONObject("""{"tickets":[{"ticket_id":"5","scope":"rem","title":"T","status":"todo","done_when":""}]}""")) as KanbanBoard
        assertTrue(sent.tickets.single().doneWhenKnown)
    }

    @Test fun aTicketThatChangesWhileItsEditSheetIsOpenKeepsTheOtherChange() {
        val mine = ticket("/1").copy(description = "Old details", kind = "develop", doneWhen = "It builds", doneWhenKnown = true)
        val start = Kanban.draftOf(mine)
        // The user renames it while the desktop raises its priority and adds a check, at a newer revision.
        val typed = start.copy(title = "Mine, renamed")
        val newer = mine.copy(priority = "high", doneWhen = "It builds\nThe tests pass", revision = 4)
        // Sent against the snapshot the sheet opened with, the save carries the old revision, so the desktop refuses it as stale
        // instead of undoing the other change.
        val stale = Kanban.editParams(newer, start, typed)!!
        assertEquals(3L, stale.getLong("expected_revision"))
        assertEquals(setOf("action", "ticket_id", "scope", "expected_revision", "title"), stale.keys().asSequence().toSet())
        // Moved onto the newer copy, the untouched fields follow it and the typed title stays; Save sends only the title, at the new revision.
        val rebased = Kanban.rebaseDraft(start, typed, Kanban.draftOf(newer))
        assertEquals(TicketDraft("Mine, renamed", "Old details", "high", "develop", "It builds\nThe tests pass", 4), rebased)
        val params = Kanban.editParams(newer, Kanban.draftOf(newer), rebased)!!
        assertEquals(4L, params.getLong("expected_revision"))
        assertEquals(setOf("action", "ticket_id", "scope", "expected_revision", "title"), params.keys().asSequence().toSet())
        assertEquals("Mine, renamed", params.getString("title"))
        // A field the user changed stays theirs even when the desktop changed it too.
        assertEquals("medium", Kanban.rebaseDraft(start, typed.copy(priority = "medium"), Kanban.draftOf(newer)).priority)
    }

    @Test fun newTicketGoesToRemOrAnOrganization() {
        val rem = Kanban.createParams(Kanban.SCOPE_REM, " Title ", "", "high", "todo", Kanban.REM, project = "p1")
        assertEquals("p1", rem.getString("project_id"))
        assertFalse(Kanban.createParams("acme", "T", "", "low", "backlog", "", project = "p1").has("project_id"))
        assertEquals("rem", rem.getString("scope")); assertEquals("Title", rem.getString("title")); assertEquals("todo", rem.getString("status"))
        assertFalse(rem.has("organization_id")); assertEquals("rem", rem.getString("assignee_id")); assertEquals("develop", rem.getString("type"))
        val org = Kanban.createParams("acme", "T", "", "low", "backlog", Kanban.UNASSIGNED)
        assertEquals("organization", org.getString("scope")); assertEquals("acme", org.getString("organization_id"))
        assertEquals("backlog", org.getString("status")); assertFalse(org.has("assignee_id")); assertFalse(org.has("type"))
    }

    @Test fun readsSelfWork() {
        assertEquals(3, selfWork.settings.maxThreads)
        assertEquals(SelfWorkLimits(1, 6), selfWork.limits)
        assertEquals(2, selfWork.state.activeThreads)
        assertEquals(2, selfWork.report!!.ticketsAdded.size)
        assertEquals("acme", selfWork.report!!.ticketsAdded[1].organizationId)
        assertEquals("done", selfWork.report!!.workDone.single().state)
        assertEquals("Idea", selfWork.report!!.duplicatesMerged.single().keptTitle)
        assertEquals("Research stopped", selfWork.report!!.notes.single().text)
    }

    @Test fun settingsSendOnlyWhatChangedAtTheKnownRevision() {
        val params = Kanban.settingsParams(selfWork.settings, maxThreads = 5)
        assertEquals(9L, params.getLong("expected_revision")); assertEquals(5, params.getInt("max_threads"))
        assertFalse(params.has("enabled")); assertFalse(params.has("auto_pull_backlog"))
        assertTrue(Kanban.settingsParams(selfWork.settings, autoPull = true).getBoolean("auto_pull_backlog"))
    }

    @Test fun autoApprovalIsHiddenFromOlderDesktopsAndAsksBeforeTurningOn() {
        // This test's desktop answer predates the setting: no switch, and the ticket isn't marked.
        assertNull(selfWork.settings.autoApprove)
        assertFalse(ticket("/2").autoApproved)
        // A real desktop answer with it on, after research approved one idea on its own.
        val view = DesktopViews.parse(Kanban.SELF_WORK, JSONObject(javaClass.getResource("/desktop/selfwork-read-auto-approve.json")!!.readText())) as SelfWorkView
        assertEquals(true, view.settings.autoApprove)
        assertEquals(1, view.state.autoApprovedSession)
        val added = view.report!!.ticketsAdded.single()
        assertTrue(added.autoApproved); assertFalse(added.proposed)
        assertTrue(Kanban.reportHeadline(view.report).contains("1 auto-approved"))
        assertFalse(Kanban.reportHeadline(view.report).contains("to approve"))
        assertEquals("On: new ideas go to Backlog approved, marked Auto-approved, until self-work ends (1 this session). They wait in Backlog until you move them, or turn on backlog pulling.",
            Kanban.autoApproveDetail(view))
        val off = view.copy(settings = view.settings.copy(autoApprove = false))
        assertEquals("Off: ideas from Rem's research wait in Backlog for your approval.", Kanban.autoApproveDetail(off))
        assertTrue(Kanban.autoApproveDetail(view.copy(state = view.state.copy(boardEnabled = false))).startsWith("Needs the Kanban board"))
        val warning = Kanban.autoApproveWarning(off)
        assertTrue(warning.first().contains("for as long as self-work runs, without you reading them first"))
        assertFalse(warning.joinToString(" ").contains("a session,"))
        assertTrue(warning.last().contains("stay yours to decide"))
        // Turning it on carries the user's confirmation; turning it off doesn't need one.
        val on = Kanban.settingsParams(off.settings, autoApprove = true)
        assertTrue(on.getBoolean("auto_approve_proposals")); assertTrue(on.getBoolean("confirm_auto_approve"))
        val turnOff = Kanban.settingsParams(view.settings, autoApprove = false)
        assertFalse(turnOff.getBoolean("auto_approve_proposals")); assertFalse(turnOff.has("confirm_auto_approve"))
        assertFalse(Kanban.settingsParams(view.settings, maxThreads = 2).has("auto_approve_proposals"))
        // A ticket self-work approved is marked, and isn't a proposal waiting for the user.
        val marked = (DesktopViews.parse(Kanban.BOARD, JSONObject("""{"tickets":[{"ticket_id":"7","scope":"rem","key":"REM-7","title":"Voice","status":"backlog","auto_approved":true,"revision":1}]}""")) as KanbanBoard).tickets.single()
        assertTrue(marked.autoApproved); assertFalse(marked.proposed)
        assertTrue(Kanban.onBoard(marked))
    }

    @Test fun aProposalFromAPairedDesktopNamesItAndStillWaits() {
        val tickets = (DesktopViews.parse(Kanban.BOARD, JSONObject("""{"tickets":[
            {"ticket_id":"7","scope":"rem","key":"REM-7","title":"Voice","status":"backlog","proposed":true,"proposed_on":"Desk B","revision":1},
            {"ticket_id":"8","scope":"rem","key":"REM-8","title":"Local","status":"backlog","auto_approved":true,"revision":1}]}""")) as KanbanBoard).tickets
        val remote = tickets.first { it.id == "7" }
        assertEquals("Desk B", remote.proposedOn)
        assertTrue(remote.proposed); assertFalse(remote.autoApproved)
        assertTrue(Kanban.proposedOnNote(remote).contains("always waits for your approval"))
        assertFalse(Kanban.proposedOnNote(remote.copy(proposed = false)).contains("waits"))
        // A local pass, or an older desktop, names no other computer.
        assertEquals("", tickets.first { it.id == "8" }.proposedOn)
        val report = (DesktopViews.parse(Kanban.SELF_WORK, JSONObject("""{"report":{"tickets_added":[
            {"ticket_id":"7","title":"Voice","scope":"rem","proposed":true,"computer_name":"Desk B"}]}}""")) as SelfWorkView).report!!
        assertEquals("Desk B", report.ticketsAdded.single().computerName)
    }

    @Test fun aTicketThatViolatesDecisionsIsMarkedWithItsIdsAndReason() {
        val reason = "Violates decisions: D-4 (How are workflows written?): it mentions “toml workflows”."
        val parsed = (DesktopViews.parse(Kanban.BOARD, JSONObject("""{"tickets":[
            {"ticket_id":"8","scope":"rem","key":"REM-8","title":"TOML","status":"blocked","revision":2,"block_reason":"$reason",
             "violates_decisions":["D-4","D-7","D-9","D-11"],"violation_reason":"$reason"},
            {"ticket_id":"9","scope":"rem","key":"REM-9","title":"Docs","status":"todo","revision":1}]}""")) as KanbanBoard).tickets
        val marked = parsed.first { it.id == "8" }
        assertEquals(listOf("D-4", "D-7", "D-9", "D-11"), marked.violatesDecisions)
        assertEquals(reason, marked.violationReason)
        assertEquals("Violates D-4, D-7, D-9 +1", Kanban.violatesLine(marked))
        assertEquals(reason, Kanban.blockText(marked))
        // An older desktop sends no marker: nothing shows.
        val plain = parsed.first { it.id == "9" }
        assertTrue(plain.violatesDecisions.isEmpty()); assertEquals("", Kanban.violatesLine(plain))
    }

    @Test fun aGuardrailTicketCarriesItsTagAndOlderDesktopsSendNone() {
        val parsed = (DesktopViews.parse(Kanban.BOARD, JSONObject("""{"tickets":[
            {"ticket_id":"10","scope":"rem","key":"REM-10","title":"Test for the ruff rule","status":"backlog","revision":1,"type":"maintain","tags":["Guardrail"]},
            {"ticket_id":"11","scope":"rem","key":"REM-11","title":"Docs","status":"todo","revision":1}]}""")) as KanbanBoard).tickets
        val guarded = parsed.first { it.id == "10" }
        assertEquals(listOf("Guardrail"), guarded.tags); assertTrue(guarded.guardrail)
        val plain = parsed.first { it.id == "11" }
        assertTrue(plain.tags.isEmpty()); assertFalse(plain.guardrail)
    }

    @Test fun dailyCapIsHiddenFromOlderDesktopsAndSendsOnlyItsPercent() {
        // This test's desktop answer predates the cap: no row.
        assertNull(selfWork.settings.dailyCap)
        assertEquals("", selfWork.state.dailyAllowance)
        // The parts of a desktop answer with the cap on and no current usage reading.
        val view = DesktopViews.parse(Kanban.SELF_WORK, JSONObject("""{"settings":{"enabled":true,"max_threads":2,"auto_pull_backlog":false,"idle_minutes":15,
            "revision":4,"daily_allowance_percent":11},"limits":{"max_threads_min":1,"max_threads_max":16,"daily_allowance_percent_max":100},
            "state":{"mode":"waiting","detail":"","daily_allowance":{"cap_percent":11,"used_percent":2.5,"available":false,
            "message":"No current Claude Code usage reading, so self-work starts nothing new. Used today: about 2.5% of 11%."}}}""")) as SelfWorkView
        assertEquals(11, view.settings.dailyCap)
        assertEquals(100, view.limits.dailyCapMax)
        assertTrue(view.state.dailyAllowanceUnknown)
        assertTrue(view.state.dailyAllowance.startsWith("No current Claude Code usage reading"))
        assertTrue(Kanban.dailyCapDetail(view).contains("Your own use of that account while it runs counts too"))
        assertEquals("Off", Kanban.dailyCapLabel(0)); assertEquals("11%", Kanban.dailyCapLabel(11))
        val params = Kanban.settingsParams(view.settings, dailyCap = 12)
        assertEquals(12, params.getInt("daily_allowance_percent")); assertEquals(4L, params.getLong("expected_revision"))
        assertFalse(params.has("max_threads"))
        assertFalse(Kanban.settingsParams(view.settings, maxThreads = 3).has("daily_allowance_percent"))
        assertEquals(100, Kanban.settingsParams(view.settings, dailyCap = 250).getInt("daily_allowance_percent"))
    }

    @Test fun allowanceUseIsOneChoiceAndPaceToResetShowsTheDesktopsLine() {
        // An older desktop has only the daily cap: no Allowance use choice.
        assertNull(selfWork.settings.allowanceMode)
        val view = DesktopViews.parse(Kanban.SELF_WORK, JSONObject("""{"settings":{"enabled":true,"max_threads":2,"auto_pull_backlog":false,"idle_minutes":15,
            "revision":9,"daily_allowance_percent":0,"rate_limit_percent":80,"allowance_mode":"pace","pace_aim_percent":95,"pace_lead_hours":4},
            "limits":{"max_threads_min":1,"max_threads_max":16,"daily_allowance_percent_max":100,"pace_aim_percent_min":1,"pace_aim_percent_max":100,"pace_lead_hours_max":24},
            "state":{"mode":"waiting","detail":"","allowance_pace":{"mode":"pace","state":"ahead","starts":0,
            "message":"Holding new work: Codex's weekly allowance is 60% used, 3.4% ahead of its pace line (56.6%). Next start around 18:20."}}}""")) as SelfWorkView
        assertEquals("pace", view.settings.allowanceMode)
        assertEquals(95, view.settings.paceAim); assertEquals(4, view.settings.paceLead)
        assertEquals(1, view.limits.paceAimMin); assertEquals(24, view.limits.paceLeadMax)
        assertTrue(view.state.paceHolding)
        assertTrue(view.state.allowancePace.startsWith("Holding new work: Codex's weekly allowance is 60% used"))
        assertEquals(listOf("Off", "Daily cap", "Pace to reset"), Kanban.ALLOWANCE_MODES.map { it.second })
        assertTrue(Kanban.allowanceDetail("pace").contains("Running work and your own work are never held"))
        assertEquals("Rate limit use stops self-work at 80%, before the pace aim of 95%.", Kanban.paceRateWarning(view.settings, view.settings.rateLimitPercent))
        assertNull(Kanban.paceRateWarning(view.settings, 0))
        val chosen = Kanban.settingsParams(view.settings, allowanceMode = "daily")
        assertEquals("daily", chosen.getString("allowance_mode")); assertFalse(chosen.has("daily_allowance_percent"))
        assertFalse(Kanban.settingsParams(view.settings, allowanceMode = "weekly").has("allowance_mode"))
        assertEquals(100, Kanban.settingsParams(view.settings, paceAim = 120).getInt("pace_aim_percent"))
        // Any aim from 1 to 100: 50 gives self-work half the week, with no 80% floor.
        assertEquals(50, Kanban.settingsParams(view.settings, paceAim = 50).getInt("pace_aim_percent"))
        assertEquals(1, Kanban.settingsParams(view.settings, paceAim = 0).getInt("pace_aim_percent"))
        val half = DesktopViews.parse(Kanban.SELF_WORK, JSONObject("""{"settings":{"allowance_mode":"pace","pace_aim_percent":50},"limits":{},"state":{}}""")) as SelfWorkView
        assertEquals(50, half.settings.paceAim); assertEquals(1, half.limits.paceAimMin)
        assertEquals(24, Kanban.settingsParams(view.settings, paceLead = 30).getInt("pace_lead_hours"))
        // With room under the line, the line isn't a hold.
        val room = DesktopViews.parse(Kanban.SELF_WORK, JSONObject("""{"settings":{"allowance_mode":"pace"},"limits":{},
            "state":{"allowance_pace":{"mode":"pace","state":"room","starts":2,"message":"Pacing Codex's weekly allowance: 40% used, line 48.5%. Up to 2 tasks can start."}}}""")) as SelfWorkView
        assertFalse(room.state.paceHolding)
    }

    @Test fun selfWorkAccountsComeFromTheDesktopAndSendOneProvidersChange() {
        // An older desktop sends no rows, so the phone shows no Accounts group.
        assertTrue(selfWork.accounts.isEmpty())
        val view = DesktopViews.parse(Kanban.SELF_WORK, JSONObject("""{"settings":{"enabled":true,"max_threads":2,"auto_pull_backlog":false,"idle_minutes":15,
            "revision":6,"provider_accounts":{"claude_code":"gone"}},"limits":{"max_threads_min":1,"max_threads_max":16},"state":{"mode":"waiting","detail":""},
            "accounts":[{"provider":"claude_code","label":"Claude Code","choice":"gone","rem_setting":"auto","rem_setting_label":"Choose by usage","accounts":["home","work"]},
            {"provider":"","label":"Nameless","choice":"","accounts":[]}]}""")) as SelfWorkView
        val row = view.accounts.single()
        assertEquals("Claude Code", row.label); assertEquals(listOf("home", "work"), row.accounts)
        assertEquals(listOf("" to "Rem’s setting (Choose by usage)", "@default" to "Default sign-in", "auto" to "Choose by usage", "home" to "home",
            "work" to "work", "gone" to "gone (removed)"), Kanban.accountChoices(row))
        assertEquals("gone (removed)", Kanban.accountLabel(row))
        assertEquals("Rem’s setting (Choose by usage)", Kanban.accountLabel(row.copy(choice = "")))
        val params = Kanban.accountParams(view.settings, "claude_code", "@default")
        assertEquals(6L, params.getLong("expected_revision"))
        assertEquals("@default", params.getJSONObject("provider_accounts").getString("claude_code"))
        assertEquals(1, params.getJSONObject("provider_accounts").length())
        assertFalse(params.has("max_threads"))
    }

    @Test fun statusSaysWhatRemIsDoing() {
        assertEquals("Working in 2 of 2 threads", Kanban.status(selfWork.copy(state = selfWork.state.copy(threadLimit = 2))))
        assertEquals("2 threads working. 4 to-dos left.", Kanban.status(selfWork.copy(state = selfWork.state.copy(detail = "2 threads working. 4 to-dos left."))))
        assertEquals("Self-work is off", Kanban.status(selfWork.copy(settings = selfWork.settings.copy(enabled = false))))
        assertEquals("Starts after 15 minutes without a message from you", Kanban.status(selfWork.copy(state = selfWork.state.copy(mode = "waiting"))))
        assertEquals("Waiting for Raticode to connect.", Kanban.status(selfWork.copy(state = selfWork.state.copy(mode = "paused", detail = "Waiting for Raticode to connect."))))
        assertEquals("Self-work is off", Kanban.status(selfWork.copy(state = selfWork.state.copy(mode = "off"))))
    }

    @Test fun reportHeadlineOnlyWhenThereIsSomethingNew() {
        assertEquals("2 tickets added · 1 to approve · 1 done · 1 merged", Kanban.reportHeadline(selfWork.report))
        assertEquals("", Kanban.reportHeadline(selfWork.report!!.copy(unseen = false)))
        assertEquals("", Kanban.reportHeadline(null))
        assertEquals("", Kanban.reportHeadline(SelfWorkReport("", "", true, "Nothing to report yet.", emptyList(), emptyList())))
        assertEquals("Only a note", Kanban.reportHeadline(SelfWorkReport("", "", true, "", emptyList(), emptyList(), notes = listOf(ReportNote("Only a note\nmore", "")))))
    }

    @Test fun aShortActionAnswerDoesNotReplaceTheScreen() {
        assertTrue(Kanban.isShortAnswer(Kanban.BOARD, JSONObject().put("message", "ok")))
        assertFalse(Kanban.isShortAnswer(Kanban.BOARD, boardJson))
        assertTrue(Kanban.isShortAnswer(Kanban.SELF_WORK, JSONObject()))
        assertFalse(Kanban.isShortAnswer(Kanban.SELF_WORK, selfJson))
        assertFalse(Kanban.isShortAnswer("rem.home", JSONObject()))
    }

    @Test fun everyOpIsKnownAndActionsRefreshTheirScreen() {
        assertTrue(Kanban.READS.all { it in DesktopViews.READS })
        assertTrue(Kanban.ACTIONS.all { it in DesktopViews.ACTIONS })
        assertEquals(Kanban.BOARD, DesktopViews.key(Kanban.TICKET, JSONObject()))
        assertEquals(Kanban.SELF_WORK, DesktopViews.key(Kanban.SETTINGS, JSONObject()))
        assertEquals(Kanban.SELF_WORK, DesktopViews.key(Kanban.SEEN, JSONObject()))
    }

    @Test fun overviewSaysWhetherTheDesktopHasIt() {
        assertTrue((DesktopViews.parse("overview", JSONObject().put("kanban", true)) as DesktopOverview).kanban)
        assertFalse((DesktopViews.parse("overview", JSONObject()) as DesktopOverview).kanban)
        // An older desktop has the board but no ticket history; the phone says so instead of asking.
        assertTrue((DesktopViews.parse("overview", JSONObject().put("kanban", true).put("kanban_comments", true)) as DesktopOverview).kanbanComments)
        assertFalse((DesktopViews.parse("overview", JSONObject().put("kanban", true)) as DesktopOverview).kanbanComments)
    }

    @Test fun aTicketsHistoryIsReadByItsBoardId() {
        val rem = Kanban.timelineParams("/1")
        assertEquals("rem", rem.getString("scope")); assertEquals("1", rem.getString("ticket_id")); assertEquals("", rem.getString("organization_id"))
        val org = Kanban.timelineParams("acme/1")
        assertEquals("organization", org.getString("scope")); assertEquals("acme", org.getString("organization_id"))
        assertTrue(Kanban.TIMELINE in DesktopViews.READS)
        // Each ticket's history is its own screen, so two tickets never share a cached answer.
        assertEquals("kanban.timeline:acme/1", DesktopViews.key(Kanban.TIMELINE, org))
        assertEquals("kanban.timeline:/1", DesktopViews.key(Kanban.TIMELINE, rem))
        val comment = Kanban.commentParams(ticket("acme/1"), "  Check the CSV path  ")
        assertEquals("comment", comment.getString("action")); assertEquals("Check the CSV path", comment.getString("body"))
        assertTrue(Kanban.isComment(Kanban.TICKET, comment))
        assertFalse(Kanban.isComment(Kanban.TICKET, Kanban.moveParams(ticket("/1"), "todo")))
        assertEquals(Kanban.BOARD, DesktopViews.key(Kanban.TICKET, comment))
    }

    @Test fun readsARealTicketHistoryFromTheDesktop() {
        // Written by the desktop's own kanban.timeline handler for a ticket that reached review, then blocked on its checks.
        val json = JSONObject(javaClass.getResource("/desktop/kanban-timeline.json")!!.readText())
        val history = DesktopViews.parse(Kanban.timelineKey(Kanban.timelineParams("/x")), json) as TicketTimeline
        assertEquals("blocked", history.ticket!!.status); assertTrue(history.ticket!!.isRem); assertTrue(history.canComment)
        assertEquals(listOf("Work complete, moved to In review", "Blocked: review checks failed", ""), history.comments.map { it.title })
        val blocked = history.comments[1]
        assertEquals("bad", Kanban.tone(blocked)); assertEquals("In review → Blocked", Kanban.move(blocked))
        assertEquals("0 of 2 checks passed", Kanban.checksLine(blocked.checks))
        assertEquals("README still names the old flag.", blocked.details)
        assertEquals("review", Kanban.tone(history.comments[0])); assertTrue(history.comments[0].threadId.isNotBlank())
        val mine = history.comments.last()
        assertEquals("user", mine.author); assertEquals("You", mine.authorName); assertEquals("note", Kanban.tone(mine)); assertEquals("", Kanban.headline(mine))
    }

    @Test fun historyEntriesHaveHeadlinesAndChecksLines() {
        val moved = TicketComment("1", "", "system", "", "status", "", "", "", "blocked", "todo", emptyList())
        assertEquals("Moved to To do", Kanban.headline(moved)); assertEquals("idle", Kanban.tone(moved))
        assertEquals("All 2 checks passed", Kanban.checksLine(listOf(TicketCheck("a", "pass"), TicketCheck("b", "pass"))))
        assertEquals("The check passed", Kanban.checksLine(listOf(TicketCheck("a", "pass"))))
        assertEquals("", Kanban.checksLine(emptyList()))
        val empty = Kanban.parseTimeline(JSONObject())
        assertNull(empty.ticket); assertTrue(empty.comments.isEmpty()); assertTrue(empty.canComment)
    }

    @Test fun defaultsSurviveAnEmptyAnswer() {
        val empty = DesktopViews.parse(Kanban.SELF_WORK, JSONObject()) as SelfWorkView
        assertFalse(empty.settings.enabled); assertEquals(1, empty.settings.maxThreads); assertNull(empty.report)
        assertEquals(15, empty.settings.idleMinutes)
    }

    @Test fun aPausedTicketCanOnlyGoBackToInProgressWhichResumesIt() {
        val json = JSONObject("""{"tickets":[{"ticket_id":"7","scope":"rem","key":"REM-7","title":"Held","status":"blocked","revision":2,
            "running":false,"paused":"priority","block_reason":"Paused so your own work could run first."}]}""")
        val paused = (DesktopViews.parse(Kanban.BOARD, json) as KanbanBoard).tickets.single()
        assertEquals("priority", paused.paused)
        assertFalse(paused.running)
        assertTrue((Kanban.move(paused, "running") as OrganizationModel.Move.Allowed).pull)
        for (column in listOf("todo", "backlog", "done")) assertTrue(Kanban.move(paused, column) is OrganizationModel.Move.Refused)
        // An older desktop sends no paused field.
        assertEquals("", ticket("/1").paused)
        assertEquals("Paused for your work", RunPause.label(paused.paused))
    }

    @Test fun anOlderDesktopHasNoWindowsAndItsSwitchIsTheSavedOne() {
        assertNull(selfWork.schedule)
        assertTrue(selfWork.settings.savedEnabled)
        assertNull(Kanban.windowNote(selfWork))
        val off = DesktopViews.parse(Kanban.SELF_WORK, JSONObject(selfJson.toString()).apply { getJSONObject("settings").put("enabled", false) }) as SelfWorkView
        assertFalse(off.settings.savedEnabled)
        assertNull(Kanban.windowNote(off))
    }

    @Test fun readsTimedWindowsAndTheLastPeriod() {
        val json = JSONObject(selfJson.toString()).apply {
            getJSONObject("settings").put("saved_enabled", false)
            put("schedule", JSONObject("""{"timezone":"Asia/Irkutsk",
                "active":{"id":"w1","start_at":"2026-11-12T03:00:00Z","end_at":"2026-11-12T11:00:00Z","until":"","timezone":"Asia/Irkutsk",
                  "after_end":"restore","wait_for_idle":false,"label":"Until 7:00 PM today (Asia/Irkutsk)",
                  "end_label":"7:00 PM Thu 12 Nov (Asia/Irkutsk)","after_label":"then back to off"},
                "upcoming":[{"id":"w2","start_at":"2026-11-12T15:00:00Z","end_at":null,"until":"running_done","timezone":"Asia/Irkutsk",
                  "after_end":"off","wait_for_idle":true,"label":"From 11:00 PM","end_label":"when 2 running tasks finish","after_label":"then off"},
                  {"label":"no id"}],
                "last_period":{"id":"p1","started_at":"2026-11-11T01:00:00Z","ended_at":"2026-11-11T03:00:00Z","ended_by":"user",
                  "summary":"Started 3 tasks, finished 2, proposed 1 ticket.","message_id":"self-work-period-p1"}}"""))
        }
        val view = DesktopViews.parse(Kanban.SELF_WORK, json) as SelfWorkView
        assertTrue(view.settings.enabled); assertFalse(view.settings.savedEnabled)
        val schedule = view.schedule!!
        assertEquals("Asia/Irkutsk", schedule.timezone)
        val active = schedule.active!!
        assertEquals("Until 7:00 PM today (Asia/Irkutsk)", active.label)
        assertEquals("Ends 7:00 PM Thu 12 Nov (Asia/Irkutsk), then back to off", Kanban.windowDetail(active, 15))
        assertEquals("On for this window; goes back to off after it.", Kanban.windowNote(view))
        assertEquals("On for this window; stays on after it.", Kanban.windowNote(view.copy(schedule = schedule.copy(active = active.copy(afterEnd = "on")))))
        // A window without an id is left out; a condition window has no end time.
        val next = schedule.upcoming.single()
        assertEquals("", next.endAt); assertEquals("running_done", next.until)
        assertEquals("Ends when 2 running tasks finish, then off · Waits for 15 quiet minutes first", Kanban.windowDetail(next, 15))
        val period = schedule.lastPeriod!!
        assertEquals("self-work-period-p1", period.messageId)
        assertEquals("Turned off", Kanban.periodEnd(period))
        assertEquals("Its window ended", Kanban.periodEnd(period.copy(endedBy = "window")))
        // Self-work off with its switch off: nothing to note.
        assertNull(Kanban.windowNote(view.copy(settings = view.settings.copy(enabled = false))))
    }

    @Test fun readsWhenSelfWorkStopsAndBuildsTheEditorsParams() {
        // An older desktop sends no stop: the page offers no stop condition.
        assertNull(selfWork.stop)
        val json = JSONObject(selfJson.toString()).put("stop", JSONObject("""{"revision":4,"active":true,
            "condition":"REM-205 to REM-212 are done","no_later_than":"2026-11-21T05:00:00+00:00",
            "no_later_than_label":"12:00 AM tomorrow (America/New_York)","timezone":"America/New_York",
            "label":"Stops when “REM-205 to REM-212 are done”, or at 12:00 AM tomorrow (America/New_York) at the latest",
            "judgment":{"met":false,"evidence":"REM-210 is still in review.","at":"2026-11-21T01:40:00+00:00","at_label":"8:40 PM today (America/New_York)"},
            "judging":false,"judge_failed":false,"stopped":null,
            "last":{"condition":"REM-3 is done","no_later_than":null,"no_later_than_label":"","stopped_by":"done_when","ended_by":"done_when",
              "ended_at":"2026-11-20T20:00:00+00:00","judgment":{"met":true,"evidence":"REM-3 is done.","at_label":"3:00 PM today"}}}"""))
        val stop = (DesktopViews.parse(Kanban.SELF_WORK, json) as SelfWorkView).stop!!
        assertTrue(stop.active); assertEquals(4, stop.revision)
        assertEquals("REM-205 to REM-212 are done", stop.condition)
        assertEquals("Last check 8:40 PM today (America/New_York): not met. REM-210 is still in review.", Kanban.stopJudgmentLine(stop))
        assertEquals("Checking the condition now…", Kanban.stopJudgmentLine(stop.copy(judging = true)))
        assertEquals("Not checked yet: Rem checks it after each self-work task finishes. The last check failed; Rem tries again in 10 minutes.",
            Kanban.stopJudgmentLine(stop.copy(judgment = null, judgeFailed = true)))
        assertEquals("Stopped: “REM-3 is done” was met. REM-3 is done.", Kanban.lastStopLine(stop.last!!))
        assertEquals("Self-work ended before “REM-3 is done” was met.", Kanban.lastStopLine(stop.last!!.copy(stoppedBy = "")))
        assertEquals("Its stop condition was met", Kanban.periodEnd(SelfWorkPeriod("p", "", "", "done_when", "", "")))
        // Save: the condition, a clock time the desktop resolves, and the revision the edit began from.
        val saved = Kanban.stopParams(stop, "  REM-1 is done ", "23:30", "tomorrow")
        assertEquals(4, saved.getLong("expected_revision"))
        assertEquals("REM-1 is done", saved.getString("condition"))
        assertEquals("""{"time":"23:30","day":"tomorrow"}""", saved.getJSONObject("no_later_than").toString())
        assertEquals("""{"time":"00:00"}""", Kanban.stopParams(null, "x", "00:00", "").getJSONObject("no_later_than").toString())
        assertTrue(Kanban.stopParams(null, "x", "", "").isNull("no_later_than"))
        assertFalse(Kanban.stopParams(null, "x", "", "").has("expected_revision"))
        assertEquals("both", Kanban.clearStopParams(stop).getString("clear"))
        assertTrue(Kanban.validClock("7:05")); assertTrue(Kanban.validClock("23:59"))
        assertFalse(Kanban.validClock("24:00")); assertFalse(Kanban.validClock("7pm"))
        assertTrue(Kanban.STOP in Kanban.ACTIONS)
        assertEquals(Kanban.SELF_WORK, DesktopViews.key(Kanban.STOP, JSONObject()))
    }

    @Test fun anEmptyScheduleShowsNothing() {
        val view = DesktopViews.parse(Kanban.SELF_WORK, JSONObject(selfJson.toString())
            .put("schedule", JSONObject().put("active", JSONObject.NULL).put("upcoming", org.json.JSONArray()).put("last_period", JSONObject.NULL))) as SelfWorkView
        val schedule = view.schedule!!
        assertTrue(schedule.isEmpty)
        assertNull(schedule.active)
    }

    @Test fun selfWorkSaysHowManyTasksArePaused() {
        val json = JSONObject(selfJson.toString()).apply {
            getJSONObject("state").put("paused_threads", 2).put("paused_for_you", 1)
        }
        val view = DesktopViews.parse(Kanban.SELF_WORK, json) as SelfWorkView
        assertEquals(2, view.state.pausedThreads)
        assertEquals(1, view.state.pausedForYou)
        assertEquals(0, selfWork.state.pausedThreads)
    }

    @Test fun aTicketShowsWhatItWaitsForAndTheNoticeWhenOneWasCancelled() {
        // Written by the desktop's phone handler: Starter workflows waits for a To do ticket and a Done one; Trigger gallery for a cancelled one.
        val real = DesktopViews.parse(Kanban.BOARD, JSONObject(javaClass.getResource("/desktop/kanban-board-waits.json")!!.readText())) as KanbanBoard
        val starters = real.tickets.first { it.title == "Starter workflows" }
        assertEquals(listOf("REM-1" to "todo", "REM-2" to "done"), starters.waitsFor.map { it.key to it.status })
        assertTrue(starters.waiting); assertEquals("", starters.waitNotice)
        assertEquals("Waits for: Polling triggers", Kanban.waitsLine(starters))
        val gallery = real.tickets.first { it.title == "Trigger gallery" }
        assertTrue(gallery.waitNotice.startsWith("REM-3 “Old plan” was cancelled"))
        val first = real.tickets.first { it.title == "Polling triggers" }
        assertEquals("", Kanban.waitsLine(first)); assertFalse(first.waiting)
        // The picker offers the same board's tickets, not itself or ones already listed; the edit replaces the whole list.
        assertEquals(listOf("REM-5"), Kanban.waitChoices(real, starters).map { it.key })
        val params = Kanban.waitsParams(starters, starters.waitsFor.map { it.id } - starters.waitsFor.first().id)
        assertEquals("edit", params.getString("action")); assertEquals("rem", params.getString("scope"))
        assertEquals(starters.revision, params.getLong("expected_revision"))
        assertEquals(listOf(starters.waitsFor[1].id), (0 until params.getJSONArray("waits_for").length()).map { params.getJSONArray("waits_for").getString(it) })
        assertTrue(Kanban.canEditWaits(starters))
        assertFalse(Kanban.canEditWaits(starters.copy(status = "done")))
        assertFalse(Kanban.canEditWaits(starters.copy(scope = Kanban.SCOPE_ORG, organizationId = "eng", running = true)))
        assertTrue(Kanban.canEditWaits(starters.copy(running = true)))
    }

    @Test fun anOlderDesktopsTicketWaitsForNothingAndBadKeysAreDropped() {
        val old = DesktopViews.parse(Kanban.BOARD, JSONObject("""{"tickets":[{"ticket_id":"1","scope":"rem","key":"REM-1","title":"Old","status":"todo"},
            {"ticket_id":"2","scope":"rem","key":"REM-2","title":"New","status":"todo","waiting":true,
             "waits_for":[{"ticket_id":"1","key":"REM-1","title":"Old","status":"todo"},{"ticket_id":"9","key":"<b>x","title":"Odd","status":"removed"},{"key":"REM-3"}]}]}""")) as KanbanBoard
        assertEquals(emptyList<TicketWait>(), old.tickets.first { it.id == "1" }.waitsFor)
        assertEquals(listOf("REM-1", ""), old.tickets.first { it.id == "2" }.waitsFor.map { it.key })
        assertEquals("Waits for: Old and 1 more", Kanban.waitsLine(old.tickets.first { it.id == "2" }))
    }

    @Test fun readsSelfWorkObjectivesAndTheObjectiveEachTicketServes() {
        // An older desktop sends no objectives block, and the page shows no card.
        assertNull(selfWork.objectives)
        // The view as rem_self_work.py sends it: rem_objectives.view() with the prompt list beside it. The done one comes
        // first here so the phone's open-first order shows.
        val json = JSONObject(selfJson.toString()).put("objectives", JSONObject("""{"revision":6,"updated_at":"2026-11-24T10:00:00+00:00","offset":0,
            "total":2,"open":1,"done":1,"stale":0,
            "items":[
              {"id":"a1b2c3d4e5f6","text":"Bolster existing logic","done_when":"Every board read has a test","done":true,"done_by":"rem",
               "done_evidence":"REM-40 and REM-41 landed with tests.","created_by":"rem","source":"self_work","created_at":"2026-11-24T08:00:00+00:00"},
              {"id":"0f9e8d7c6b5a","text":"Improve UX quality","done_when":"The phone shows objectives","done":false,"created_by":"user",
               "source":"user","created_at":"2026-11-24T09:00:00+00:00"}],
            "prompt":[{"id":"0f9e8d7c6b5a","text":"Improve UX quality","done":false,"done_when":"The phone shows objectives"}]}"""))
        val objectives = (DesktopViews.parse(Kanban.SELF_WORK, json) as SelfWorkView).objectives!!
        assertEquals(listOf("Improve UX quality", "Bolster existing logic"), objectives.items.map { it.text })
        val (open, done) = objectives.items
        assertFalse(open.done); assertEquals("The phone shows objectives", open.doneWhen)
        assertTrue(done.done); assertEquals("rem", done.doneBy); assertEquals("REM-40 and REM-41 landed with tests.", done.doneEvidence); assertFalse(done.stale)
        assertEquals("1/2", Kanban.objectivesCount(objectives))
        assertEquals(2, objectives.current.size); assertTrue(objectives.staleItems.isEmpty())
        assertEquals("Done when: The phone shows objectives", Kanban.objectiveDetails(open))
        assertEquals("Done when: Every board read has a test\nRem checked it off: REM-40 and REM-41 landed with tests.\nWritten by Rem.", Kanban.objectiveDetails(done))
        assertEquals("Open-ended: no finish line, so Rem never checks it off.\nYou checked it off.\nStale: done in an earlier self-work session, so it doesn't count toward this one.",
            Kanban.objectiveDetails(open.copy(doneWhen = "", done = true, doneBy = "user", stale = true, createdBy = "user")))
        // Nothing current: the desktop's empty text, which knows whether earlier sessions finished some.
        val none = SelfWorkObjectives(emptyList(), 0, 0, 0, 0)
        assertEquals("", Kanban.objectivesCount(none))
        assertEquals("None yet. When self-work starts with none, Rem writes its own, each with a finish line.", Kanban.objectivesEmpty(none))
        val onlyStale = SelfWorkObjectives(listOf(done.copy(stale = true)), 0, 0, 1, 1)
        assertEquals("None for this session yet.", Kanban.objectivesEmpty(onlyStale))
        assertTrue(onlyStale.current.isEmpty()); assertEquals(1, onlyStale.staleItems.size)

        // The board: one ticket advances an objective, one self-work leaves while objectives are open.
        val tickets = (DesktopViews.parse(Kanban.BOARD, JSONObject("""{"tickets":[
            {"ticket_id":"1","scope":"rem","key":"REM-1","title":"Objectives on the phone","status":"todo","revision":1,
             "objective":{"id":"0f9e8d7c6b5a","text":"Improve UX quality"},"objective_left":""},
            {"ticket_id":"2","scope":"rem","key":"REM-2","title":"Rename a setting","status":"backlog","revision":1,"objective":null,
             "objective_left":"It doesn't advance an open self-work objective, so self-work leaves it where it is. It renames a label."},
            {"ticket_id":"3","scope":"rem","key":"REM-3","title":"Shipped","status":"done","revision":1,"objective":{"id":"0f9e8d7c6b5a","text":"Improve UX quality"}},
            {"ticket_id":"4","scope":"rem","key":"REM-4","title":"Older desktop","status":"todo","revision":1}]}""")) as KanbanBoard).tickets
        val serves = Kanban.objectiveLine(tickets[0])!!
        assertFalse(serves.off); assertEquals("Improve UX quality", serves.short)
        assertEquals("Advances the self-work objective “Improve UX quality”.", Kanban.objectivePageText(serves))
        val left = Kanban.objectiveLine(tickets[1])!!
        assertTrue(left.off); assertEquals("Not on an objective: self-work leaves it", left.short)
        assertEquals("It doesn't advance an open self-work objective, so self-work leaves it where it is. It renames a label. Move it to In progress to start it anyway.",
            Kanban.objectivePageText(left))
        // A done ticket and one from an older desktop say nothing about objectives.
        assertNull(Kanban.objectiveLine(tickets[2])); assertNull(Kanban.objectiveLine(tickets[3]))
    }
}
