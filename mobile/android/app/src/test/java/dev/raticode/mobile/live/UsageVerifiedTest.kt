package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

/** Usage › Verified work and the self-work report's line, as the desktop sends them (gofer/devices/desktop_control.py). */
class UsageVerifiedTest {
    private val answer = JSONObject("""{
        "days": 30, "activity": {"calls": 3},
        "verified_work": {
            "line": "1 of 3 tickets passed review first time", "accepted": 3, "first_pass": 1, "first_pass_percent": 33,
            "repairs": 3, "repairs_per_ticket": 1.0, "accepted_by_hand": 0, "finished": 4, "blocked": 1, "blocked_percent": 25,
            "blocked_causes": [{"cause": "review", "label": "Review checks failed", "tickets": 1}, {"cause": "other", "label": "Other", "tickets": 0}],
            "project_check": {"checked": 2, "failed_first": 1, "failed_first_percent": 50, "flaky": 1},
            "step_ins": {"total": 1, "request_changes": 0, "manual_moves": 1, "tickets": 1},
            "tokens": {"total": 6000, "tickets": 3, "per_accepted": 2000, "partial": false},
            "history_since": "2026-10-01T00:00:00+00:00"
        }
    }""")

    @Test fun parsesTheSameNumbersTheDesktopShows() {
        val work = (DesktopViews.parse("usage.overview", answer) as DesktopUsage).verifiedWork!!
        assertEquals("1 of 3 tickets passed review first time", work.line)
        assertEquals(3, work.accepted); assertEquals(1, work.firstPass); assertEquals(33, work.firstPassPercent)
        assertEquals(1.0, work.repairsPerTicket!!, 0.0)
        assertEquals(listOf(VerifiedCause("review", "Review checks failed", 1)), work.causes)
        assertEquals(2, work.checked); assertEquals(1, work.checkFailedFirst); assertEquals(1, work.checkFlaky)
        assertEquals(2000L, work.tokensPerAccepted)
        assertEquals("1 of 3", UsageVerified.headline(work))
        assertEquals(listOf(
            "Repairs per ticket" to "1",
            "Blocked" to "1 of 4 (25%)",
            "Project check failed first try" to "1 of 2",
            "Flaky check runs" to "1",
            "Tokens per accepted ticket" to "2,000",
            "You stepped in" to "1 time · 0 change requests, 1 move by hand",
        ), UsageVerified.facts(work))
    }

    @Test fun aFailedTaskShowsAsItsOwnCauseRow() {
        // The desktop names a failed task by its message (gofer/ui/rem_verified_work.py).
        val work = UsageVerified.parse(JSONObject("""{
            "accepted": 0, "finished": 3, "blocked": 3,
            "blocked_causes": [
                {"cause": "no_activity", "label": "No activity for 20 minutes", "tickets": 2},
                {"cause": "failed", "label": "Other failure", "tickets": 1}
            ]
        }"""))!!
        assertEquals(listOf("No activity for 20 minutes" to "2", "Other failure" to "1"), UsageVerified.causeRows(work))
    }

    @Test fun anOlderDesktopOrAnEmptyPeriodShowsNoHeadline() {
        assertNull((DesktopViews.parse("usage.overview", JSONObject("""{"days": 30}""")) as DesktopUsage).verifiedWork)
        val empty = UsageVerified.parse(JSONObject("""{"accepted": 0, "finished": 0, "line": "No tickets were accepted in this period."}"""))!!
        assertNull(UsageVerified.headline(empty))
        assertNull(empty.repairsPerTicket)
        assertEquals(listOf("You stepped in" to "0 times · 0 change requests, 0 moves by hand"), UsageVerified.facts(empty))
        // A desktop that doesn't count flaky runs: no figure rather than a 0 it never counted.
        val older = UsageVerified.parse(JSONObject("""{"accepted": 1, "finished": 1, "project_check": {"checked": 1, "failed_first": 0}}"""))!!
        assertNull(older.checkFlaky)
        assertFalse(UsageVerified.facts(older).any { it.first == "Flaky check runs" })
    }

    @Test fun theSelfWorkReportCarriesItsLineOnlyWithAcceptedTickets() {
        fun report(verified: String?) = JSONObject("""{"period_id":"p1","items":[]${verified?.let { ""","verified_work":$it""" } ?: ""}}""")
        fun line(verified: String?) = (DesktopViews.parse("rem.home", JSONObject().put("messages", JSONArray()
            .put(JSONObject().put("id", "m").put("role", "rem").put("kind", "self_work_report").put("body", "Report")
                .put("self_work_report", report(verified))))) as RemHome).messages[0].selfWorkReport!!.verifiedLine
        assertEquals("3 of 4 tickets passed review first time.", line("""{"line":"3 of 4 tickets passed review first time","accepted":4,"first_pass":3}"""))
        assertEquals("", line("""{"line":"No tickets were accepted in this period.","accepted":0}"""))
        assertEquals("", line(null))
    }
}
