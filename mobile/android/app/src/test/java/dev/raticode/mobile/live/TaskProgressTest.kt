package dev.raticode.mobile.live

import dev.raticode.mobile.live.TaskProgress.Asked
import dev.raticode.mobile.live.TaskProgress.Claims
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Which runs count as started from this phone, and what the progress notification says about them. */
class TaskProgressTest {
    private val now = 1_000_000_000L
    private val sent = "2026-11-09T10:00:00+00:00"
    private val after = "2026-11-09T10:00:05+00:00"
    private val before = "2026-11-09T09:00:00+00:00"
    private val once = RemSchedule("once", "", "", emptyList(), 0)

    private fun run(id: String, state: String = "executing", createdAt: String = after, check: String = "") =
        RemRun(id, state, createdAt, "", "", false, false, "thread-$id", check)
    private fun task(id: String, active: RemRun?, title: String = "Fix the payroll export", schedule: RemSchedule = once) =
        RemTask(id, title, "maintain", "enabled", 1, schedule, "", "", active, null)
    private fun user(id: String, createdAt: String = sent) = RemMessage(id, "user", "Please fix it", createdAt, null, "")
    private fun rem(run: String, task: String, id: String = "queued-$run") =
        RemMessage(id, "rem", "Started.", after, RemWork(task, run, "thread-$run", "Fix the payroll export"), "")
    private fun home(messages: List<RemMessage>, tasks: List<RemTask>) = RemHome("idle", "", "", 0, messages, tasks)
    private val mine = Claims(requests = listOf(Asked("phone-1", now - 1000)))

    @Test fun aRunRemStartedForThisPhonesMessageIsClaimed() {
        val claims = TaskProgress.claim(mine, home(listOf(user("phone-1"), rem("run-1", "task-1")), listOf(task("task-1", run("run-1")))), now)
        assertEquals(listOf("run-1"), claims.runs)
    }

    @Test fun selfWorkAndOtherPeoplesRunsAreNeverClaimed() {
        val selfWork = task("self-work", run("run-self"))
        val fromDesktop = task("desktop-task", run("run-desk"))
        val claims = TaskProgress.claim(mine, home(
            listOf(user("phone-1"), user("desktop-1"), rem("run-desk", "desktop-task")), listOf(selfWork, fromDesktop)), now)
        assertTrue("self-work posts no chat message, and the desktop's message answers its own request: ${claims.runs}", claims.runs.isEmpty())
    }

    @Test fun aRunThatWasAlreadyGoingOrScheduledDoesNotCount() {
        val older = home(listOf(user("phone-1"), rem("run-old", "task-1", "reply-phone-1")), listOf(task("task-1", run("run-old", createdAt = before))))
        assertTrue(TaskProgress.claim(mine, older, now).runs.isEmpty())
        val weekly = RemSchedule("weekly", "", "09:00", listOf("monday"), 0)
        val scheduled = home(listOf(user("phone-1"), rem("run-1", "task-1")), listOf(task("task-1", run("run-1"), schedule = weekly)))
        assertTrue(TaskProgress.claim(mine, scheduled, now).runs.isEmpty())
        val later = RemSchedule("once", "2026-11-09T17:00:00+00:00", "", emptyList(), 0)
        val booked = home(listOf(user("phone-1"), rem("run-1", "task-1")), listOf(task("task-1", run("run-1"), schedule = later)))
        assertTrue(TaskProgress.claim(mine, booked, now).runs.isEmpty())
    }

    @Test fun aRequestIsOnlyFollowedForTenMinutes() {
        val stale = Claims(requests = listOf(Asked("phone-1", now - TaskProgress.WATCH_MS - 1)))
        val answer = home(listOf(user("phone-1"), rem("run-1", "task-1")), listOf(task("task-1", run("run-1"))))
        val claims = TaskProgress.claim(stale, answer, now)
        assertTrue(claims.runs.isEmpty())
        assertTrue("old requests are dropped", claims.requests.isEmpty())
        assertFalse(TaskProgress.watching(stale, now))
    }

    @Test fun theTaskScreensActionsAreClaimedWithoutAChatMessage() {
        fun note(op: String, params: JSONObject) = JSONObject().also { TaskProgress.note(it, op, params, "request", now) }.let(TaskProgress::load)
        val tasks = listOf(task("task-1", run("run-1")), task("task-2", run("run-2")), task("task-3", run("run-3"), title = "Brand new"))
        val run = TaskProgress.claim(note("rem.task.run", JSONObject().put("task_id", "task-1")), home(emptyList(), tasks), now)
        assertEquals(listOf("run-1"), run.runs)
        assertTrue("the request is answered", run.asked.isEmpty())
        assertEquals(listOf("run-2"), TaskProgress.claim(note("rem.run.retry_review", JSONObject().put("run_id", "run-2")), home(emptyList(), tasks), now).runs)
        assertEquals(listOf("run-3"), TaskProgress.claim(note("rem.task.create", JSONObject().put("title", "Brand new")), home(emptyList(), tasks), now).runs)
        assertEquals(Claims(), JSONObject().also { TaskProgress.note(it, "rem.task.pause", JSONObject(), "x", now) }.let(TaskProgress::load))
    }

    @Test fun anAskedRunStaysAskedUntilItShowsUp() {
        val asked = Claims(asked = listOf(Asked("task:task-1", now - 1000)))
        val claims = TaskProgress.claim(asked, home(emptyList(), listOf(task("task-1", null))), now)
        assertEquals(asked.asked, claims.asked)
        assertTrue(TaskProgress.watching(claims, now))
    }

    @Test fun anEndedRunIsForgotten() {
        val ended = TaskProgress.claim(Claims(runs = listOf("run-1")), home(emptyList(), listOf(task("task-1", null))), now)
        assertTrue(ended.runs.isEmpty())
        assertFalse("no more reading once nothing is left to follow", TaskProgress.watching(ended, now))
    }

    @Test fun aDismissedRunDoesNotComeBackUntilANewRunStarts() {
        val tasks = listOf(task("task-1", run("run-1")))
        val claims = TaskProgress.hide(Claims(runs = listOf("run-1")), listOf("run-1"))
        val read = TaskProgress.claim(claims, home(emptyList(), tasks), now)
        assertTrue(TaskProgress.running(read, home(emptyList(), tasks)).isEmpty())
        assertNull(TaskProgress.frame(TaskProgress.running(read, home(emptyList(), tasks)), false))
        assertFalse("nothing left to look at", TaskProgress.watching(read, now))
        val next = TaskProgress.claim(read.copy(runs = read.runs + "run-2"), home(emptyList(), listOf(task("task-1", run("run-2")))), now)
        assertEquals(listOf("run-2"), TaskProgress.running(next, home(emptyList(), listOf(task("task-1", run("run-2"))))).map { it.id })
    }

    @Test fun claimsSurviveTheJournal() {
        val claims = Claims(listOf(Asked("phone-1", 7)), listOf(Asked("task:a", 8)), listOf("run-1"), listOf("run-2"))
        val state = JSONObject()
        TaskProgress.save(state, claims)
        assertEquals(claims, TaskProgress.load(JSONObject(state.toString())))
        assertEquals(Claims(), TaskProgress.load(JSONObject()))
    }

    @Test fun whatTheStepsSay() {
        fun at(state: String, check: String = "") = TaskProgress.frame(listOf(TaskProgress.Run("r", "T", state, after, check.isNotEmpty())), false)!!
        val queued = at("queued")
        assertEquals("▶ Plan · ○ Work · ○ Check · ○ Report", queued.text)
        assertEquals("Plan", queued.chip)
        assertEquals(0, queued.done)
        val working = at("executing")
        assertEquals("✓ Plan · ▶ Work · ○ Check · ○ Report", working.text)
        assertEquals("Work", working.chip)
        assertEquals("Plan done, Work in progress, Check not started, Report not started", working.spoken)
        val checking = at("verifying")
        assertEquals("✓ Plan · ✓ Work · ▶ Check · ○ Report", checking.text)
        assertEquals("Check", checking.chip)
        assertEquals(2, checking.done)
        assertEquals(java.time.OffsetDateTime.parse(after).toInstant().toEpochMilli(), working.startedAt)
    }

    @Test fun aRunCheckingFinishedWorkAgainIsNotShownAtPlan() {
        // Queued again to recheck work that is done, such as after the desktop restarted.
        val run = TaskProgress.Run("r", "T", "queued", after, true, step = "check")
        val frame = TaskProgress.frame(listOf(run), false)!!
        assertEquals("✓ Plan · ✓ Work · ▶ Check · ○ Report", frame.text)
        assertEquals("Check", frame.chip)
        // The step comes from the desktop's run.
        val task = task("t", RemRun("r", "queued", after, "", "", false, false, "thread-r", "review", step = "check"))
        val claims = Claims(runs = listOf("r"))
        assertEquals("check", TaskProgress.running(claims, home(emptyList(), listOf(task))).single().step)
    }

    @Test fun onlyTheWordsOptInNamesTheTask() {
        val one = listOf(TaskProgress.Run("run-1", "Fix the **payroll** export", "executing", after, false))
        val generic = TaskProgress.frame(one, showWords = false)!!
        assertEquals("Rem is working on your task", generic.title)
        assertFalse(generic.words)
        assertFalse("payroll" in generic.spoken + generic.text + generic.chip)
        val named = TaskProgress.frame(one, showWords = true)!!
        assertEquals("Fix the payroll export", named.title)
        assertTrue(named.words)
        assertEquals(generic, named.withoutWords())
        assertEquals("Rem is working on a task", TaskProgress.LOCK_TEXT)
    }

    @Test fun oneNotificationStandsForSeveralRunsNewestFirst() {
        val runs = listOf(TaskProgress.Run("new", "Newest", "executing", after, false), TaskProgress.Run("old", "Oldest", "verifying", before, true))
        val generic = TaskProgress.frame(runs, false)!!
        assertEquals("Rem is working on 2 tasks", generic.title)
        assertEquals(listOf("new", "old"), generic.runs)
        assertEquals("Newest and 1 more", TaskProgress.frame(runs, true)!!.title)
        val tasks = listOf(task("a", run("old", "verifying", before), "Oldest"), task("b", run("new"), "Newest"))
        assertEquals(listOf("new", "old"), TaskProgress.running(Claims(runs = listOf("old", "new")), home(emptyList(), tasks)).map { it.id })
    }

    @Test fun anUnknownRunStateShowsNothing() {
        assertNull(TaskProgress.frame(listOf(TaskProgress.Run("r", "T", "paused", after, false)), false))
        assertNull(TaskProgress.frame(emptyList(), true))
    }
}
