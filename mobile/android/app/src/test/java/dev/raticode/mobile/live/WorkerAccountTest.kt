package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * A worker's provider account on the phone. organizations-read-v2.json is a real read from a desktop that predates the
 * choice, so it stands in for an older desktop; the newer read adds the accounts and each worker's account_id.
 */
class WorkerAccountTest {
    private fun read(name: String) = JSONObject(javaClass.getResource("/desktop/$name")!!.readText())
    private fun org(json: JSONObject) = DesktopViews.parse("organizations.read:x", json) as Organization
    private val older = org(read("organizations-read-v2.json"))

    private fun newer(): Organization {
        val json = read("organizations-read-v2.json")
        json.getJSONArray("employees").getJSONObject(0).put("account_id", "aaaaaaaaaaaaaaaa")
        json.getJSONArray("employees").getJSONObject(1).put("account_id", "dddddddddddddddd")
        json.put("accounts", JSONArray()
            .put(JSONObject().put("provider", "codex").put("account_id", "aaaaaaaaaaaaaaaa").put("name", "work"))
            .put(JSONObject().put("provider", "codex").put("account_id", "bbbbbbbbbbbbbbbb").put("name", "spare"))
            .put(JSONObject().put("provider", "claude_code").put("account_id", "cccccccccccccccc").put("name", "personal")))
        return org(json)
    }

    @Test fun anOlderDesktopOffersNoAccountChoice() {
        assertNull(older.accounts)
        val lead = older.employees.first { it.id == "lead" }
        assertEquals("", lead.accountId)
        assertNull(OrganizationModel.workerAccounts(older, lead))
        assertEquals(OrganizationModel.DEFAULT_SIGN_IN, OrganizationModel.accountLabel(older, lead))
    }

    @Test fun aWorkerChoosesAmongItsOwnProvidersAccounts() {
        val org = newer()
        val lead = org.employees.first { it.id == "lead" }
        assertEquals(listOf("work", "spare"), OrganizationModel.workerAccounts(org, lead)!!.map { it.name })
        assertEquals("work", OrganizationModel.accountLabel(org, lead))
        // A saved account that was removed says what its runs do instead.
        val dev = org.employees.first { it.id == "dev" }
        assertEquals("Removed account (runs on the default sign-in)", OrganizationModel.accountLabel(org, dev))
    }

    @Test fun sendsWhatTheProtocolFixtureHolds() {
        // src/gofer/devices/protocol/v2/fixtures/desktop.action.organizations-worker-account.json
        val fixture = JSONObject("""{"organization_id":"org1","employee_id":"lead","account":"0123456789abcdef"}""")
        val params = OrganizationModel.accountParams("org1", "lead", "0123456789abcdef")
        assertEquals(fixture.keys().asSequence().toSet(), params.keys().asSequence().toSet())
        fixture.keys().forEach { assertEquals(fixture.get(it), params.get(it)) }
        // The default sign-in is an empty account, never a missing one.
        assertEquals("", OrganizationModel.accountParams("org1", "lead", "").getString("account"))
    }
}
