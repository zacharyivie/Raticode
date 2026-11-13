package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Approving Rem's requests from the phone. organizations-read-v2.json is a real read from a desktop that predates
 * `organizations.requests`, so its requests carry no IDs and stand in for an older desktop.
 */
class OrgRequestsTest {
    private fun read(name: String) = JSONObject(javaClass.getResource("/desktop/$name")!!.readText())
    private fun org(json: JSONObject) = DesktopViews.parse("organizations.read:x", json) as Organization
    private fun request(id: String, summary: String, at: String = "2026-11-11T20:00:00+00:00") =
        JSONObject().put("request_id", id).put("summary", summary).put("at", at)

    @Test fun readsRequestIdsAndAnOlderDesktopSendsNone() {
        val older = read("organizations-read-v2.json").put("requests", JSONArray().put(JSONObject().put("summary", "Start the organization").put("at", "x")))
        val olderPlan = org(older).plan!!
        assertEquals("", olderPlan.requests.single().id)
        // Without IDs there's nothing the phone could send, so Approve all isn't offered.
        assertFalse(OrgRequests.offerAll(olderPlan.requests + olderPlan.requests))
        val newer = org(read("organizations-read-v2.json").put("requests", JSONArray().put(request("a", "Start the organization")).put(request("b", "Remove the approval step"))))
        assertEquals(listOf("a", "b"), newer.plan!!.requests.map { it.id })
        assertTrue(OrgRequests.offerAll(newer.plan!!.requests))
        assertFalse(OrgRequests.offerAll(newer.plan!!.requests.take(1)))
    }

    @Test fun theOverviewSaysWhetherTheDesktopTakesDecisions() {
        assertTrue((DesktopViews.parse("overview", JSONObject().put("organizations_v2", true).put("organization_requests", true)) as DesktopOverview).organizationRequests)
        assertFalse((DesktopViews.parse("overview", JSONObject().put("organizations_v2", true)) as DesktopOverview).organizationRequests)
        assertTrue(OrgRequests.OP in DesktopViews.ACTIONS && OrgRequests.OP !in DesktopViews.READS)
        // The answer is the organization's read, which replaces the open organization.
        assertEquals("organizations.read:o1", DesktopViews.key(OrgRequests.OP, JSONObject().put("organization_id", "o1")))
    }

    @Test fun sendsExactlyTheListShownAsTheProtocolFixtureHolds() {
        // src/gofer/devices/protocol/v2/fixtures/desktop.action.organizations-requests.json
        val fixture = JSONObject("""{"organization_id":"org1","decision":"approve","request_ids":["8af2f1b7-d7ce-4309-9e19-1a1d4c9edf32","dbad867e-0000-4000-8000-000000000001"]}""")
        val shown = listOf(OrgRequest("Change permissions", "", "8af2f1b7-d7ce-4309-9e19-1a1d4c9edf32"), OrgRequest("Remove a step", "", "dbad867e-0000-4000-8000-000000000001"))
        val params = OrgRequests.params("org1", OrgRequests.frozen(shown), "approve")
        assertEquals(fixture.keys().asSequence().toSet(), params.keys().asSequence().toSet())
        fixture.keys().forEach { assertEquals(fixture.get(it).toString(), params.get(it).toString()) }
        // Blank and repeated IDs never go out, and the desktop's limit of 20 holds.
        assertEquals(listOf("a"), OrgRequests.frozen(listOf(OrgRequest("x", "", "a"), OrgRequest("y", "", ""), OrgRequest("z", "", "a"))))
        assertEquals(20, OrgRequests.frozen((1..25).map { OrgRequest("r", "", "id$it") }).size)
    }

    @Test fun resultsReadAsTheDesktopWordsThem() {
        val answer = read("organizations-read-v2.json").put("request_results", JSONArray()
            .put(JSONObject().put("request_id", "a").put("summary", "Change permissions").put("status", "approved").put("message", ""))
            .put(JSONObject().put("request_id", "b").put("summary", "Start the organization").put("status", "skipped")
                .put("message", "Not applied, because another request in this list didn't apply.")))
        val results = OrgRequests.parseResults(answer)!!
        assertEquals(listOf("approved", "skipped"), results.map { it.status })
        assertEquals("Approved 1 of 2, 1 request left waiting.", OrgRequests.summary(results))
        assertEquals("Left waiting", OrgRequests.label("skipped"))
        assertEquals("Approved all 2 requests.", OrgRequests.summary(results.map { it.copy(status = "approved") }))
        assertEquals("Declined 1 request.", OrgRequests.summary(listOf(results[0].copy(status = "declined"))))
        // A plain organization read has no results, so an older answer leaves the last ones alone.
        assertNull(OrgRequests.parseResults(read("organizations-read-v2.json")))
    }
}
