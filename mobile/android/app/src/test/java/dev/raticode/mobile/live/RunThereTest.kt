package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Run there: the paired desktop a waiting run or ticket suggests, as the phone reads it and sends the pick. */
class RunThereTest {
    private val offer = JSONObject().put("computer_id", "00000000-0000-4000-8000-000000000005").put("computer_name", "Desktop B")
        .put("project", "Raticode").put("text", "Desktop B has room: Run there").put("pick", true).put("grant", "")

    private fun home(run: JSONObject) = DesktopViews.parse("rem.home", JSONObject().put("tasks", JSONArray().put(
        JSONObject().put("task_id", "t1").put("title", "Sync research").put("type", "research").put("state", "enabled")
            .put("active_run", run)))) as RemHome

    @Test
    fun waitingRunCarriesTheSuggestionAndThePickNamesItsComputer() {
        val run = home(JSONObject().put("run_id", "r1").put("state", "queued").put("waiting_reason", "Waiting for a free worker")
            .put("suggestion", offer)).tasks.single().activeRun!!
        val suggestion = run.suggestion!!
        assertEquals("Desktop B", suggestion.computerName)
        assertEquals("Desktop B has room:", suggestion.lead)
        assertEquals("Desktop B has room: Run there", suggestion.text)
        assertTrue(suggestion.pick)
        val pick = RunThere.forRun(run.id, suggestion, supported = true)!!
        assertEquals("r1", pick.getString("run_id"))
        assertEquals("00000000-0000-4000-8000-000000000005", pick.getString("computer_id"))
        assertFalse(pick.has("organization_id"))
        // An older desktop doesn't take the action, so nothing is offered.
        assertNull(RunThere.forRun(run.id, suggestion, supported = false))
        assertTrue(RunThere.blocked(RunThere.OP, desktopRunsThere = false))
        assertTrue(DesktopViews.ACTIONS.contains(RunThere.OP))
    }

    @Test
    fun olderDesktopAndBadComputerIdsCarryNoSuggestion() {
        assertNull(home(JSONObject().put("run_id", "r1").put("state", "queued")).tasks.single().activeRun!!.suggestion)
        assertNull(RunThere.parse(JSONObject(offer.toString()).put("computer_id", "../desk")))
        assertNull(RunThere.parse(JSONObject(offer.toString()).put("computer_id", "")))
    }

    @Test
    fun ticketWithoutAnOwnerTargetSaysWhatToGrantAndOffersNoPick() {
        val grant = "To run it there, grant Worker 0 a fleet execution target on Desktop B for Raticode"
        val noTarget = RunThere.parse(JSONObject(offer.toString()).put("pick", false).put("grant", grant)
            .put("text", "Desktop B has room. $grant"))!!
        assertFalse(noTarget.pick)
        assertEquals("Desktop B has room. $grant", noTarget.text)
        assertNull(RunThere.forTicket("org-1", "t-1", noTarget, supported = true))
        val pick = RunThere.forTicket("org-1", "t-1", RunThere.parse(offer), supported = true)!!
        assertEquals("org-1", pick.getString("organization_id"))
        assertEquals("t-1", pick.getString("task_id"))
        assertFalse(pick.has("run_id"))
    }

    @Test
    fun overviewSaysWhetherTheDesktopTakesRunThere() {
        assertTrue((DesktopViews.parse("overview", JSONObject().put("run_there", true)) as DesktopOverview).runThere)
        assertFalse((DesktopViews.parse("overview", JSONObject()) as DesktopOverview).runThere)
    }
}
