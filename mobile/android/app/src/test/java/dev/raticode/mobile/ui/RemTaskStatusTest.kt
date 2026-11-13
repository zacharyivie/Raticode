package dev.raticode.mobile.ui

import dev.raticode.mobile.live.RemRun
import dev.raticode.mobile.live.RemSchedule
import dev.raticode.mobile.live.RemSleep
import dev.raticode.mobile.live.RemTask
import dev.raticode.mobile.ui.RemTaskStatus.Kind
import dev.raticode.mobile.ui.RemTaskStatus.Tone
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** A task's status, color family and group match the desktop's lib/remHome.js taskActivity and groupTasks. */
class RemTaskStatusTest {
    private val stamp: (String, Boolean) -> String = { value, past -> if (past) "at $value" else "on $value" }
    private fun run(state: String, needsUser: Boolean = true, acknowledged: Boolean = false, summary: String = "") =
        RemRun("r-$state", state, "2026-11-09T10:00:00+00:00", "2026-11-09T11:00:00+00:00", summary, needsUser, acknowledged, "", "")
    private fun task(id: String, state: String = "enabled", active: RemRun? = null, last: RemRun? = null, next: String = "", type: String = "errand") =
        RemTask(id, id, type, state, 1, RemSchedule("once", "", "", emptyList(), 0), next, "", active, last)
    private fun activity(task: RemTask) = RemTaskStatus.activity(task, stamp)

    @Test fun runStatesUseTheDesktopsWordsAndTones() {
        assertEquals(RemTaskStatus.RunInfo("Working", Tone.ACTIVE, active = true, spinning = true), RemTaskStatus.run("executing"))
        assertEquals("Waiting to start", RemTaskStatus.run("queued").label)
        assertFalse(RemTaskStatus.run("queued").spinning)
        assertEquals("Checking the work", RemTaskStatus.run("verifying").label)
        assertEquals("Fixing what the check found", RemTaskStatus.run("repairing").label)
        assertEquals(Tone.ATTENTION, RemTaskStatus.run("blocked").tone)
        assertEquals("Needs you", RemTaskStatus.run("blocked").label)
        // Blocked without a question for the user couldn't finish, in the danger color.
        assertEquals(RemTaskStatus.RunInfo("Couldn't finish", Tone.DANGER, active = false, spinning = false), RemTaskStatus.run("blocked", needsUser = false))
        assertEquals("Didn't finish", RemTaskStatus.run("failed").label)
        assertEquals(Tone.DANGER, RemTaskStatus.run("failed").tone)
        assertEquals("Stopped", RemTaskStatus.run("cancelled").label)
        assertEquals("Result unclear", RemTaskStatus.run("outcome_unknown").label)
        assertEquals(Tone.SUCCESS, RemTaskStatus.run("succeeded").tone)
        assertEquals("Status unavailable", RemTaskStatus.run("something_new").label)
    }

    @Test fun aSleepingWorkerReadsAsTheDesktopSaysIt() {
        val sleep = RemSleep("CI run 4294", "2026-11-11T02:45:00+00:00", 2, 25, 720)
        val asleep = activity(task("z", active = run("queued").copy(waitingReason = "Sleeping until 02:45 UTC · waiting for CI run 4294", sleep = sleep)))
        assertEquals(Kind.QUEUED, asleep.kind); assertFalse(asleep.spinning); assertEquals(Tone.NEUTRAL, asleep.tone)
        assertEquals("Sleeping · waiting for CI run 4294, next check on 2026-11-11T02:45:00+00:00", asleep.label)
        assertEquals(RemTaskStatus.SLEEPING, asleep.state)
        assertEquals("Woke 2 times · 25 min of 12 h wait budget used", RemTaskStatus.sleepBudget(sleep))
        assertEquals("First sleep · 0 min of 1.5 h wait budget used", RemTaskStatus.sleepBudget(RemSleep("x", "", 0, 0, 90)))
        // An older desktop sends no sleep: the phone shows its waiting reason, as before.
        val older = activity(task("o", active = run("queued").copy(waitingReason = "Waiting for CI run 4294 before checking again")))
        assertEquals("Waiting for CI run 4294 before checking again", older.label); assertEquals("queued", older.state)
        // A sleep only counts while the run is parked.
        assertEquals(Kind.RUNNING, activity(task("a", active = run("executing").copy(sleep = sleep))).kind)
        assertEquals(null, RemSleep.parse(null))
        assertEquals(sleep, RemSleep.parse(org.json.JSONObject().put("waiting_for", "CI run 4294").put("until", "2026-11-11T02:45:00+00:00")
            .put("wakes", 2).put("slept_minutes", 25).put("budget_minutes", 720)))
    }

    @Test fun activityTellsWorkingStoppedBlockedAndDoneApart() {
        val working = activity(task("w", active = run("executing").copy(phaseLabel = "Running the tests")))
        assertEquals(Kind.RUNNING, working.kind); assertTrue(working.spinning); assertEquals("Running the tests", working.label)
        val queued = activity(task("q", active = run("queued").copy(waitingReason = "Waiting for a free slot")))
        assertEquals(Kind.QUEUED, queued.kind); assertFalse(queued.spinning); assertEquals("Waiting for a free slot", queued.label)
        val stopping = activity(task("s", active = run("cancelling")))
        assertEquals(Kind.RUNNING, stopping.kind); assertEquals("Stopping", stopping.label); assertEquals(Tone.NEUTRAL, stopping.tone)
        val blocked = activity(task("b", last = run("blocked", summary = "**Which** [branch](x)?")))
        assertEquals(Kind.ATTENTION, blocked.kind); assertEquals(Tone.ATTENTION, blocked.tone); assertEquals("Which branch?", blocked.label)
        val failed = activity(task("f", last = run("failed", needsUser = false)))
        assertEquals(Kind.ATTENTION, failed.kind); assertEquals(Tone.DANGER, failed.tone)
        // Dismissed, a failure is just its outcome.
        val dismissed = activity(task("d", last = run("failed", acknowledged = true)))
        assertEquals(Kind.DONE, dismissed.kind); assertEquals("Didn't finish", dismissed.label)
        val stopped = activity(task("c", last = run("cancelled")))
        assertEquals(Kind.DONE, stopped.kind); assertEquals("Stopped", stopped.label); assertEquals("cancelled", stopped.state)
        val done = activity(task("ok", last = run("succeeded")))
        assertEquals(Kind.DONE, done.kind); assertEquals(Tone.SUCCESS, done.tone); assertEquals("Done at 2026-11-09T11:00:00+00:00", done.label)
        assertEquals(Kind.PAUSED, activity(task("p", state = "paused", last = run("succeeded"))).kind)
        assertEquals("Next on 2026-11-10T09:00:00+00:00", activity(task("n", next = "2026-11-10T09:00:00+00:00")).label)
        assertEquals(Kind.IDLE, activity(task("i")).kind)
    }

    @Test fun groupsFollowTheDesktopSidebar() {
        val tasks = listOf(
            task("done", last = run("succeeded")), task("next", next = "2026-11-10T09:00:00+00:00"),
            task("blocked", last = run("blocked")), task("paused", state = "paused"),
            task("working", active = run("executing")), task("waiting", active = run("queued")),
        )
        val groups = RemTaskStatus.groups(tasks, stamp)
        assertEquals(listOf("now", "attention", "scheduled", "paused", "done"), groups.map { it.id })
        assertEquals(listOf("Working on", "Needs you", "Coming up", "Paused", "Done"), groups.map { it.label })
        // The phone's order inside a group stays as given.
        assertEquals(listOf("working", "waiting"), groups.first().tasks.map { it.first.id })
        // Failures that ask nothing of the user are Updates.
        assertEquals("Updates", RemTaskStatus.groups(listOf(task("f", last = run("failed", needsUser = false))), stamp).single().label)
        assertTrue(RemTaskStatus.groups(emptyList(), stamp).isEmpty())
    }

    @Test fun summaryMatchesTheDesktopStatusLine() {
        assertEquals("1 thing needs you", RemTaskStatus.summary(listOf(task("b", last = run("blocked")), task("w", active = run("executing")))))
        assertEquals("Working on 2 things", RemTaskStatus.summary(listOf(task("w", active = run("executing")), task("q", active = run("queued")))))
        assertEquals("Working on 1 thing", RemTaskStatus.summary(listOf(task("w", active = run("executing")), task("f", last = run("failed", needsUser = false)))))
        assertEquals("All caught up", RemTaskStatus.summary(listOf(task("n", next = "2026-11-10T09:00:00+00:00"))))
        assertEquals("Here when you need me", RemTaskStatus.summary(emptyList()))
    }

    @Test fun selfWorkReportStatesReadAsWhatHappenedToTheTicket() {
        assertEquals("Done", reportStatus("done").second.label)
        assertEquals("succeeded", reportStatus("done").first)
        assertEquals("Picked back up", reportStatus("unblocked").second.label)
        assertEquals("Still needs you", reportStatus("still_blocked").second.label)
        assertEquals("blocked", reportStatus("still_blocked").first)
        assertEquals("Needs you", reportStatus("blocked").second.label)
    }

    @Test fun aPausedRunReadsAsPausedByYouOrForYourWorkAndOffersResume() {
        val paused = run("paused").copy(pausedBy = "user")
        val byYou = task("a", last = paused)
        assertEquals(Kind.PAUSED, activity(byYou).kind)
        assertEquals("Paused", activity(byYou).label)
        assertEquals("Paused for your work", activity(task("b", last = paused.copy(pausedBy = "priority"))).label)
        assertEquals(listOf("Paused"), RemTaskStatus.groups(listOf(byYou), stamp).map { it.label })
        // Neither attention nor finished, and the run controls pause what runs and resume what is paused.
        assertFalse(RemTaskStatus.run("paused").active)
        assertEquals(dev.raticode.mobile.live.RunPause.Control.Resume(paused.id), dev.raticode.mobile.live.RunPause.control(byYou, supported = true))
        assertEquals(dev.raticode.mobile.live.RunPause.Control.Pause("r-executing"),
            dev.raticode.mobile.live.RunPause.control(task("c", active = run("executing")), supported = true))
        assertEquals(dev.raticode.mobile.live.RunPause.Control.None,
            dev.raticode.mobile.live.RunPause.control(task("d", active = run("cancelling")), supported = true))
        // An older desktop can't pause runs, so the phone offers nothing.
        assertEquals(dev.raticode.mobile.live.RunPause.Control.None,
            dev.raticode.mobile.live.RunPause.control(task("c", active = run("executing")), supported = false))
    }
}
