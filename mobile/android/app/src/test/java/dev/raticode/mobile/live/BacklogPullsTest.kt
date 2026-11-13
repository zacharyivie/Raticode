package dev.raticode.mobile.live

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Backlog pulls on the phone. organizations-read-v2.json is a real read from a desktop that predates the setting, so
 * it stands in for an older desktop; the newer read is the same with the field the desktop now sends.
 */
class BacklogPullsTest {
    private fun read(name: String) = JSONObject(javaClass.getResource("/desktop/$name")!!.readText())
    private fun org(json: JSONObject) = DesktopViews.parse("organizations.read:x", json) as Organization
    private val older = org(read("organizations-read-v2.json"))

    private fun backlog(org: Organization, reason: String, assignee: String = "lead") =
        OrganizationTask("b", "Staged", "", "backlog", "medium", "", 3, false, false, false, false, "", "", "", emptyList(),
            TicketExtras("ORG-9", assignee, "", "app", "", false, emptyList(), emptyList(), reason, emptyMap(), emptyList(), ""))

    @Test fun readsTheSettingAndAnOlderDesktopSaysNothing() {
        assertNull(older.plan!!.pullBacklog)
        assertEquals(true, org(read("organizations-read-v2.json").put("pull_backlog", true)).plan!!.pullBacklog)
        assertEquals(false, org(read("organizations-read-v2.json").put("pull_backlog", false)).plan!!.pullBacklog)
    }

    @Test fun theOverviewSaysWhetherTheDesktopTakesTheSwitch() {
        assertTrue((DesktopViews.parse("overview", JSONObject().put("organizations_v2", true).put("organization_backlog_pull", true)) as DesktopOverview).organizationBacklogPull)
        assertFalse((DesktopViews.parse("overview", JSONObject().put("organizations_v2", true)) as DesktopOverview).organizationBacklogPull)
        assertTrue(BacklogPulls.OP in DesktopViews.ACTIONS && BacklogPulls.OP !in DesktopViews.READS)
        // A save answers with the organization's read, which replaces the open organization.
        assertEquals("organizations.read:o1", DesktopViews.key(BacklogPulls.OP, JSONObject().put("organization_id", "o1")))
    }

    @Test fun sendsWhatTheProtocolFixtureHolds() {
        // src/gofer/devices/protocol/v2/fixtures/desktop.action.organizations-backlog-pull.json
        val fixture = JSONObject("""{"organization_id":"org1","expected_revision":5,"enabled":false}""")
        val params = BacklogPulls.params("org1", 5, false)
        assertEquals(fixture.keys().asSequence().toSet(), params.keys().asSequence().toSet())
        // JSON numbers compare by value: the fixture parses an Int, the phone sends the revision as a Long.
        fixture.keys().forEach { assertEquals(fixture.get(it).toString(), params.get(it).toString()) }
    }

    @Test fun backlogHintsSayWhetherAWorkerPullsTheTicket() {
        val on = org(read("organizations-read-v2.json").put("pull_backlog", true))
        val running = on.copy(state = "running")
        val lead = on.employees.first { it.id == "lead" }.name
        assertEquals("In Backlog. $lead pulls it into To do when free.",
            OrganizationModel.startHint(backlog(running, "In Backlog until its worker is free to pull it"), running))
        assertEquals("In Backlog. $lead pulls it into To do when free once the organization runs.",
            OrganizationModel.startHint(backlog(on.copy(state = "paused"), "In Backlog until its worker is free to pull it"), on.copy(state = "paused")))
        assertEquals("A worker's proposal. Move it to To do to accept it; workers never pull proposals.",
            OrganizationModel.startHint(backlog(running, "Proposed by a worker. Move it to To do to accept it."), running))
        assertEquals("you", OrganizationModel.startTone(backlog(running, "Proposed by a worker. Move it to To do to accept it."), running))
        assertTrue(OrganizationModel.startHint(backlog(running, "Kept in Backlog: it was moved back here, so workers leave it"), running)
            .startsWith("Kept in Backlog because it was moved back here"))
        assertEquals("In Backlog, waiting for ORG-1. Then $lead pulls it when free.",
            OrganizationModel.startHint(backlog(running, "Waiting for ORG-1"), running))
        assertEquals("wait", OrganizationModel.startTone(backlog(running, "Waiting for ORG-1"), running))
        assertEquals("In Backlog with no worker. Assign it, or a free worker assigned to its project takes it.",
            OrganizationModel.startHint(backlog(running, "In Backlog. Assign it, or a free worker assigned to its project takes it", assignee = ""), running))
        // Turned off, or an older desktop whose workers never pull: the old wording stays.
        val off = org(read("organizations-read-v2.json").put("pull_backlog", false))
        val stays = "In Backlog, so it won't run. Move it to To do when it should start."
        assertEquals(stays, OrganizationModel.startHint(backlog(off, "In Backlog until its worker is free to pull it"), off))
        assertEquals(stays, OrganizationModel.startHint(backlog(older, "backlog"), older))
        assertEquals(stays, OrganizationModel.startHint(backlog(older, ""), older))
    }
}
