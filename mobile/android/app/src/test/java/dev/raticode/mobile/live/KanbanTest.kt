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
        val org = real.tickets.first { it.title == "Org ticket" }
        assertFalse(org.isRem); assertEquals("critical", org.priority); assertEquals("Eng", org.owner)
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
        // Written by the desktop's own handlers at the revision named in docs/mobile-desktop-v2.md.
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
}
