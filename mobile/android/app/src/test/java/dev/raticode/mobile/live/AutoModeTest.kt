package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Auto mode on the phone. organizations-read-v2.json is a real read from a desktop that predates Auto mode, so it stands
 * in for an older desktop; the newer read is the same with the fields the desktop now sends.
 */
class AutoModeTest {
    private fun read(name: String) = JSONObject(javaClass.getResource("/desktop/$name")!!.readText())
    private fun org(json: JSONObject) = DesktopViews.parse("organizations.read:x", json) as Organization
    private val older = org(read("organizations-read-v2.json"))

    private fun newer(): JSONObject = read("organizations-read-v2.json")
        .put("auto_mode", true)
        .put("auto_halt", JSONObject().put("summary", "Start the organization").put("kind", "impossible")
            .put("reason", "It needs a sign-in to the statements portal.").put("user_action", "Sign in on this computer.")
            .put("request_id", "r9").put("at", "2026-11-11T22:00:00+00:00"))
        .put("auto_decisions", JSONArray()
            .put(JSONObject().put("summary", "Change Avery's permissions").put("decision", "modify").put("reason", "Sandboxed instead.")
                .put("recommendation", "").put("applied", true).put("error", "").put("at", "2026-11-11T21:59:00+00:00"))
            .put(JSONObject().put("summary", "Remove the approval step").put("decision", "decline").put("reason", "Needs review first.")
                .put("recommendation", "Assign the Risk Officer.").put("applied", true).put("error", "").put("at", "2026-11-11T21:58:00+00:00")))
        .put("requests", JSONArray()
            .put(JSONObject().put("request_id", "a").put("summary", "Raise the budget").put("at", "2026-11-11T21:00:00+00:00").put("waiting_on", "you"))
            .put(JSONObject().put("request_id", "b").put("summary", "Resume Mira").put("at", "2026-11-11T21:01:00+00:00").put("waiting_on", "rem"))
            .put(JSONObject().put("request_id", "c").put("summary", "Start").put("at", "2026-11-11T21:02:00+00:00").put("waiting_on", "you")))

    @Test fun anOlderDesktopSaysNothingAboutAutoMode() {
        val plan = older.plan!!
        assertNull(plan.autoMode)
        assertNull(plan.autoHalt)
        assertTrue(plan.autoDecisions.isEmpty())
        // Without waiting_on every request waits on the user, as before.
        assertTrue(plan.requests.none { it.reviewedByRem })
    }

    @Test fun readsTheSettingTheStopAndRemsDecisions() {
        val plan = org(newer()).plan!!
        assertEquals(true, plan.autoMode)
        val halt = plan.autoHalt!!
        assertEquals("impossible", halt.kind)
        assertEquals("To continue: Sign in on this computer.", AutoMode.toContinue(halt))
        assertEquals(listOf("Approved with changes", "Declined"), plan.autoDecisions.map { AutoMode.label(it.decision) })
        assertEquals("Assign the Risk Officer.", plan.autoDecisions[1].recommendation)
        assertEquals(listOf("Approved", "Stopped", "Left for you"), listOf("approve", "halt", "left_for_you").map(AutoMode::label))
    }

    @Test fun requestsRemReviewsAreLeftOutOfApproveAll() {
        val requests = org(newer()).plan!!.requests
        assertEquals(listOf(false, true, false), requests.map { it.reviewedByRem })
        assertEquals(listOf("a", "c"), OrgRequests.frozen(requests))
        assertTrue(OrgRequests.offerAll(requests))
        // Only one waits on the user: Approve all isn't offered for it alone.
        assertFalse(OrgRequests.offerAll(requests.filter { it.id != "c" }))
    }

    @Test fun theOverviewSaysWhetherTheDesktopTakesTheSwitch() {
        assertTrue((DesktopViews.parse("overview", JSONObject().put("organizations_v2", true).put("organization_auto_mode", true)) as DesktopOverview).organizationAutoMode)
        assertFalse((DesktopViews.parse("overview", JSONObject().put("organizations_v2", true)) as DesktopOverview).organizationAutoMode)
        assertTrue(AutoMode.OP in DesktopViews.ACTIONS && AutoMode.OP !in DesktopViews.READS)
        assertEquals("organizations.read:o1", DesktopViews.key(AutoMode.OP, JSONObject().put("organization_id", "o1")))
    }

    @Test fun sendsWhatTheProtocolFixtureHoldsAndConfirmsOnlyWhenTurningOn() {
        // src/gofer/devices/protocol/v2/fixtures/desktop.action.organizations-auto-mode.json
        val fixture = JSONObject("""{"organization_id":"org1","expected_revision":5,"enabled":true,"confirm":true}""")
        val params = AutoMode.params("org1", 5, true)
        assertEquals(fixture.keys().asSequence().toSet(), params.keys().asSequence().toSet())
        fixture.keys().forEach { assertEquals(fixture.get(it).toString(), params.get(it).toString()) }
        val off = AutoMode.params("org1", 5, false)
        assertFalse(off.getBoolean("enabled"))
        assertFalse(off.getBoolean("confirm"))
    }

    @Test fun theConfirmationSaysWhatItAllowsAndWhatStaysTheUsers() {
        assertTrue(AutoMode.CONFIRM.startsWith("Rem approves, changes or declines this organization's requests instead of you."))
        listOf("dangerous", "credential", "sign-in", "hardware", "internet", "Budget and run-limit increases", "Auto mode itself stay yours")
            .forEach { assertTrue(it, it in AutoMode.CONFIRM) }
    }
}
