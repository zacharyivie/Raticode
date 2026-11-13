package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The five-section Organizations view on the phone. The samples are real answers from the desktop's phone protocol
 * (desktop tests/unit/test_device_organizations_v2.py builds the same organization), so a renamed field fails here.
 */
class OrganizationModelTest {
    private fun read(name: String) = JSONObject(javaClass.getResource("/desktop/$name")!!.readText())
    private val org = DesktopViews.parse("organizations.read:x", read("organizations-read-v2.json")) as Organization
    private val plan = org.plan!!

    private fun task(id: String, status: String, epic: String = "", assignee: String = "", running: Boolean = false, approval: Boolean = false,
                     blockReason: String = "", review: Boolean = status == "in_review") =
        OrganizationTask(id, id, "", status, "medium", "", 3, running, approval, review, false, blockReason, "", "", emptyList(),
            TicketExtras("ORG-$id", assignee, epic, "app", "", approval, emptyList(), emptyList(), "", emptyMap(), emptyList(), ""))

    @Test fun readsEverySectionFromTheDesktop() {
        assertEquals("Ship a calm phone app", plan.mission)
        assertEquals(setOf("beta", "m1"), plan.milestones.map { it.id }.toSet())
        assertEquals("m1", plan.epics.single().milestoneId)
        assertEquals("Phone app", plan.projects.single().name)
        assertEquals("Careful", plan.workers["lead"]!!.persona)
        assertEquals("lead", org.employees.first { it.id == "dev" }.reportsTo)
        val ticket = org.tasks.first { it.title == "Follow the log" }
        assertEquals("e1", ticket.extras!!.epicId)
        assertEquals("ORG-1", OrganizationModel.key(ticket))
        assertEquals(listOf(plan.notes.first { it.kind == "finding" }.id), ticket.extras!!.notes)
        assertEquals("Mobile", plan.notes.first { it.kind == "finding" }.topic)
        val note = DesktopViews.parse("organizations.note:x:y", read("organizations-note.json")) as OrgNoteDetail
        assertEquals("Reconnect on unlock.", note.body)
        assertEquals(listOf(plan.notes.first { it.kind == "finding" }.id), note.links)
    }

    // DESK-2: an older desktop sent each status change as a plain comment that copied the one above it.
    private val report = "I ran the permission trial in plan mode. Its done-when isn't met.\n\n| Capability | Result |"
    private val olderDesk2 = listOf(
        OrganizationComment("Director", "2026-11-11T20:05:36.001518+00:00", report, ""),
        OrganizationComment("Director", "2026-11-11T20:05:36.001833+00:00", "I ran the permission trial in plan mode. Its done-when isn't met.", ""),
        OrganizationComment("You", "2026-11-11T20:09:12.379912+00:00", "Accepted", "accept"),
        OrganizationComment("You", "2026-11-11T20:09:12.380346+00:00", "Accepted", ""))

    @Test fun anOlderDesktopsCopiedStatusEntriesShowOnce() {
        val shown = OrganizationModel.visibleComments(olderDesk2)
        assertEquals(listOf(report, "Accepted"), shown.map { it.body })
        assertEquals("accept", shown.last().decision)
    }

    @Test fun repeatedWordsLaterOrFromSomeoneElseAreKept() {
        val comments = listOf(
            OrganizationComment("You", "2026-01-01T00:00:00Z", "Ping", ""),
            OrganizationComment("Mira", "2026-01-01T00:00:01Z", "Ping", ""),
            OrganizationComment("Mira", "2026-01-01T01:00:01Z", "Ping", ""))
        assertEquals(comments, OrganizationModel.visibleComments(comments))
        assertTrue(OrganizationModel.repeats("Ported the importer and…", "Ported the importer and ran every test."))
        assertFalse(OrganizationModel.repeats("", "Anything"))
    }

    @Test fun aCurrentDesktopMarksStatusEntriesAndCountsTheConversation() {
        val json = JSONObject(read("organizations-read-v2.json").toString())
        val first = json.getJSONArray("tasks").getJSONObject(0)
        first.put("comment_count", 4).put("comments", JSONArray()
            .put(JSONObject().put("actor", "Director").put("at", "2026-11-11T20:05:36Z").put("body", report))
            .put(JSONObject().put("actor", "Director").put("at", "2026-11-11T20:05:36Z").put("body", "").put("kind", "status")
                .put("title", "Work complete, moved to In review").put("from", "in_progress").put("to", "in_review")))
        val parsed = (DesktopViews.parse("organizations.read:x", json) as Organization).tasks.first { it.id == first.getString("task_id") }
        assertEquals(4, parsed.commentCount)
        val status = OrganizationModel.visibleComments(parsed.comments).last()
        assertEquals("status", status.kind)
        assertEquals("Work complete, moved to In review", OrganizationModel.statusHeadline(status))
        assertEquals("In progress → In review", OrganizationModel.statusMove(status))
    }

    @Test fun anOlderDesktopHasNoPlan() {
        val older = JSONObject(read("organizations-read-v2.json").toString()).apply { remove("v2") }
        val parsed = DesktopViews.parse("organizations.read:x", older) as Organization
        assertNull(parsed.plan)
        assertNotNull(parsed.tasks.first().extras)
        val oldest = JSONObject().put("organization_id", "o").put("tasks", JSONArray().put(JSONObject().put("task_id", "t").put("status", "todo")))
        val oldView = DesktopViews.parse("organizations.read:o", oldest) as Organization
        assertNull(oldView.plan)
        assertNull(oldView.tasks.single().extras)
    }

    @Test fun boardColumnsAndMovesFollowTheDesktop() {
        assertEquals("todo", OrganizationModel.columnOf("blocked"))
        assertEquals("running", OrganizationModel.columnOf("in_progress"))
        assertNull(OrganizationModel.columnOf("cancelled"))
        val backlog = task("1", "backlog")
        assertEquals(OrganizationModel.Move.Allowed("todo"), OrganizationModel.move(backlog, "todo"))
        assertEquals(OrganizationModel.Move.Allowed("running", pull = true), OrganizationModel.move(backlog, "running"))
        assertNull(OrganizationModel.move(backlog, "backlog"))
        assertTrue(OrganizationModel.move(backlog, "review") is OrganizationModel.Move.Refused)
        assertTrue(OrganizationModel.move(backlog, "done") is OrganizationModel.Move.Refused)
        val review = task("2", "in_review")
        assertEquals(OrganizationModel.Move.Review(), OrganizationModel.move(review, "done"))
        assertTrue(OrganizationModel.move(review, "todo") is OrganizationModel.Move.Refused)
        assertTrue(OrganizationModel.move(review, "running") is OrganizationModel.Move.Refused)
        val done = task("3", "done")
        assertEquals(OrganizationModel.Move.Allowed("backlog", reopen = true), OrganizationModel.move(done, "backlog"))
        val blocked = task("4", "blocked", blockReason = "Needs a key")
        assertTrue(OrganizationModel.move(blocked, "running") is OrganizationModel.Move.Refused)
        assertEquals(OrganizationModel.Move.Allowed("backlog", reopen = true), OrganizationModel.move(blocked, "backlog"))
        val running = task("5", "in_progress", running = true)
        assertTrue(OrganizationModel.move(running, "todo") is OrganizationModel.Move.Refused)
        assertEquals("Needs a key", OrganizationModel.flag(blocked))
        assertEquals("Needs your approval before it runs", OrganizationModel.flag(task("6", "todo", approval = true)))
    }

    @Test fun progressCountsOnlyDoneTicketsAndAveragesUpward() {
        val withTickets = org.copy(tasks = listOf(task("a", "done", "e1"), task("b", "todo", "e1"), task("c", "in_progress", "e1"), task("d", "done", "e1")))
        assertEquals(0.5, OrganizationModel.epicProgress(withTickets, "e1"), 1e-9)
        assertEquals(0.5, OrganizationModel.milestoneProgress(withTickets, plan, "m1"), 1e-9)
        assertEquals(0.5, OrganizationModel.orgMilestoneProgress(withTickets, plan, "beta"), 1e-9)
        assertEquals(0.5, OrganizationModel.projectProgress(withTickets, plan, "app"), 1e-9)
        assertEquals("active", OrganizationModel.epicState(withTickets, plan.epics.single()))
        val finished = withTickets.copy(tasks = withTickets.tasks.map { it.copy(status = "done") })
        assertEquals("done", OrganizationModel.epicState(finished, plan.epics.single()))
        assertEquals("planned", OrganizationModel.epicState(org.copy(tasks = listOf(task("x", "backlog", "e1"))), plan.epics.single()))
        assertEquals(2, OrganizationModel.openTickets(withTickets).size)
    }

    @Test fun workersFormATreeAndShowWhatTheyAreDoing() {
        val rows = OrganizationModel.reportingTree(org.employees)
        assertEquals(listOf("lead" to 0, "dev" to 1), rows.map { it.employee.id to it.depth })
        assertEquals(1, rows.first().reports)
        // A reporting loop still lists everyone once.
        val loop = listOf(OrganizationEmployee("a", "A", "", "", "", false, "b"), OrganizationEmployee("b", "B", "", "", "", false, "a"))
        assertEquals(setOf("a", "b"), OrganizationModel.reportingTree(loop).map { it.employee.id }.toSet())
        val lead = org.employees.first { it.id == "lead" }
        assertEquals("idle" to "Idle", OrganizationModel.workerStatus(org, lead))
        assertEquals("working" to "Working", OrganizationModel.workerStatus(org.copy(tasks = listOf(task("r", "in_progress", assignee = "lead", running = true))), lead))
        assertEquals("needs" to "To review", OrganizationModel.workerStatus(org.copy(tasks = listOf(task("v", "in_review", assignee = "lead"))), lead))
        assertEquals("paused" to "Held", OrganizationModel.workerStatus(org, org.employees.first { it.id == "dev" }))
    }

    @Test fun needsYouListsReviewsBeforeFlags() {
        val busy = org.copy(tasks = listOf(task("f", "blocked", blockReason = "Stuck"), task("r", "in_review"), task("ok", "todo")))
        val needs = OrganizationModel.needsYou(busy, plan)
        assertTrue(needs[0] is OrganizationModel.NeedsYou.Review)
        assertTrue(needs[1] is OrganizationModel.NeedsYou.Flag)
        assertEquals(2, needs.size)
        assertEquals("1 failed, 2 without verified evidence", OrganizationModel.reviewCaution(task("c", "in_review").let {
            it.copy(extras = it.extras!!.copy(evidence = mapOf("ok" to 1, "bad" to 1, "none" to 2))) }))
    }

    @Test fun notesLinkBothWaysAndGroupByTopic() {
        val finding = plan.notes.first { it.kind == "finding" }
        val decision = plan.notes.first { it.kind == "decision" }
        // The decision links to the finding; the finding shows the decision too, and the ticket that plans to use it.
        assertEquals(listOf(decision.id), OrganizationModel.neighbors(org, plan, finding.id).notes.map { it.id })
        assertEquals(listOf("Follow the log"), OrganizationModel.neighbors(org, plan, finding.id).tickets.map { it.title })
        assertEquals(listOf(finding.id), OrganizationModel.neighbors(org, plan, decision.id).notes.map { it.id })
        assertEquals(mapOf(decision.id to 1, finding.id to 1), OrganizationModel.degree(plan))
        assertEquals(setOf("General", "Mobile"), OrganizationModel.topics(plan.notes).map { it.first }.toSet())
    }

    @Test fun colorsAndInitialsAreStable() {
        assertEquals(OrganizationModel.hashColor("lead"), OrganizationModel.hashColor("lead"))
        assertEquals("SC", OrganizationModel.initials("Sam  Carter"))
        assertEquals("?", OrganizationModel.initials(""))
        assertEquals(OrganizationModel.PROJECT_COLORS[0], OrganizationModel.projectColor(plan, "app"))
    }

    @Test fun startHintsUseTheDesktopsWords() {
        val t = task("h", "todo", assignee = "lead").let { it.copy(extras = it.extras!!.copy(startHint = "Ready")) }
        assertEquals("Ready. It starts when you start the organization.", OrganizationModel.startHint(t, org))
        assertEquals("Starts next: Lead picks it up within a few seconds.", OrganizationModel.startHint(t, org.copy(state = "running")))
        assertEquals("", OrganizationModel.startHint(task("n", "todo"), org))
        assertFalse(OrganizationModel.startHint(t.copy(extras = t.extras!!.copy(startHint = "Employee is held")), org).isBlank())
    }

    @Test fun startHintsCarryTheDesktopsTone() {
        fun tone(reason: String, state: String = org.state) =
            OrganizationModel.startTone(task("h", "todo").let { it.copy(extras = it.extras!!.copy(startHint = reason)) }, org.copy(state = state))
        assertEquals("you", tone("Ready", "paused"))
        assertEquals("run", tone("Ready", "running"))
        assertEquals("you", tone("Assign an employee"))
        assertEquals("you", tone("Waiting for plan approval"))
        assertEquals("wait", tone("Next up: Fix the build"))
        assertEquals("wait", tone("Queued: two tickets ahead"))
        assertEquals("you", tone("Reassign it to an active worker"))
        assertEquals("wait", tone("Waiting for the review slot"))
        assertEquals("", tone(""))
    }

    @Test fun commentAuthorsReadAsNames() {
        assertEquals("You", OrganizationModel.actorName("user", org))
        assertEquals("Raticode", OrganizationModel.actorName("system", org))
        assertEquals("Rem", OrganizationModel.actorName("Rem (self-work)", org))
        assertEquals("Lead", OrganizationModel.actorName("employee:lead", org))
        assertEquals("gone", OrganizationModel.actorName("employee:gone", org))
    }
}
