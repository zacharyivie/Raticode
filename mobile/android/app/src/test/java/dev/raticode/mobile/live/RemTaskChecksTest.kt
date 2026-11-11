package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

/** A task's failed and unresolved checks read the same as in the desktop's task pane. */
class RemTaskChecksTest {
    private fun check(id: String, result: String, evidence: String = "", description: String = "Check $id") =
        JSONObject().put("id", id).put("description", description).put("result", result).put("evidence", evidence)

    private fun taskJson(run: JSONObject?) = JSONObject().put("task_id", "t").put("title", "Export").put("last_run", run)
        .put("runs", JSONArray().put(JSONObject().put("run_id", "old").put("state", "blocked")))

    @Test fun parsesEachCheckOfTheNewestRunWithItsReason() {
        val run = JSONObject().put("run_id", "r").put("state", "blocked").put("criteria", JSONArray()
            .put(check("a", "fail", "x".repeat(4000)))
            .put(check("b", "unknown", "Not accepted: no receipts."))
            .put(JSONObject().put("result", "pass")))
        val task = DesktopViews.parse("rem.task:t", taskJson(run)) as RemTask
        val details = task.lastRun!!.details!!
        assertEquals(listOf("a", "b", ""), details.map { it.id })
        assertEquals(1500, details[0].evidence.length)
        assertEquals("", details[2].description)
        // Older runs and older desktops send counts only.
        assertNull(task.runs.single().details)
        assertNull((DesktopViews.parse("rem.task:t", taskJson(JSONObject().put("run_id", "r").put("state", "blocked"))) as RemTask).lastRun!!.details)
    }

    @Test fun groupsFailedUnresolvedUncheckedAndPassedChecks() {
        val groups = RemTaskChecks.groups(listOf(
            RemCheck("a", "Tests pass", "fail", "3 rows missing."),
            RemCheck("b", "Docs updated", "unknown", "Not accepted: no receipts."),
            RemCheck("c", "Lint clean", "pass", "ruff passed."),
            RemCheck("d", "Not reviewed", "unknown", "  "),
            RemCheck("", "", "fail", ""),
        ))
        assertEquals(listOf("Tests pass" to "3 rows missing.", "Check 5" to ""), groups.failed.map { it.title to it.reason })
        assertEquals("check-4", groups.failed[1].key)
        assertEquals(listOf("Docs updated"), groups.unresolved.map { it.title })
        assertEquals(listOf("Lint clean"), groups.passed.map { it.title })
        assertEquals(listOf("Not reviewed"), groups.unchecked.map { it.title })
        assertTrue(groups.reviewed)
        assertEquals("1 of 5 passed, 2 failed, 1 unresolved, 1 not checked", RemTaskChecks.summary(groups))
    }

    @Test fun summaryKeepsTheDesktopsWording() {
        fun summary(vararg results: String) = RemTaskChecks.summary(RemTaskChecks.groups(results.mapIndexed { i, r -> RemCheck("$i", "", r, "") }))
        assertNull(summary())
        assertEquals("Not checked yet", summary("unknown"))
        assertEquals("3 checks not run yet", summary("unknown", "unknown", "unknown"))
        assertEquals("2 of 2 checks passed", summary("pass", "pass"))
        assertEquals("0 of 2 passed, 2 failed", summary("fail", "fail"))
        assertFalse(RemTaskChecks.groups(listOf(RemCheck("a", "", "unknown", ""))).reviewed)
    }

    @Test fun longReasonsFold() {
        assertFalse(RemTaskChecks.longReason("Short."))
        assertTrue(RemTaskChecks.longReason("x".repeat(241)))
        assertTrue(RemTaskChecks.longReason("a\nb\nc\nd"))
    }

    @Test fun showsTheRunBeingFixedOrElseTheLastRun() {
        fun run(id: String, state: String) = RemRun(id, state, "", "", "", false, false, "", "")
        fun task(active: RemRun?, last: RemRun?) = RemTask("t", "T", "errand", "enabled", 1, RemSchedule("once", "", "", emptyList(), 0), "", "", active, last)
        val last = run("last", "blocked")
        assertSame(last, RemTaskChecks.runToShow(task(null, last)))
        assertEquals("fix", RemTaskChecks.runToShow(task(run("fix", "repairing"), last))?.id)
        // A review in progress has no findings to show yet.
        assertNull(RemTaskChecks.runToShow(task(run("now", "verifying"), last)))
        // An active_run that has ended is not running.
        assertSame(last, RemTaskChecks.runToShow(task(run("ended", "blocked"), last)))
    }
}
