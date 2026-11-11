package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** The phone's Rem task list puts what ran last on top and leaves finished one-time tasks to the chat's links. */
class RemTaskListTest {
    private val once = RemSchedule("once", "", "", emptyList(), 0)
    private val daily = RemSchedule("daily", "", "09:00", emptyList(), 0)

    private fun run(state: String, created: String = "", finished: String = "", acknowledged: Boolean = false) =
        RemRun("r-$state-$created", state, created, finished, "", false, acknowledged, "thread", "")
    private fun task(id: String, schedule: RemSchedule = once, active: RemRun? = null, last: RemRun? = null, next: String = "") =
        RemTask(id, id, "errand", "enabled", 1, schedule, next, "", active, last)

    @Test fun mostRecentlyRunComesFirst() {
        val tasks = listOf(
            task("never-a", daily),
            task("older", daily, last = run("succeeded", "2026-10-30T08:00:00+00:00", "2026-10-30T08:05:00+00:00")),
            task("running-early", daily, active = run("executing", "2026-11-01T09:00:00+00:00"), last = run("executing", "2026-11-01T09:00:00+00:00")),
            // Started first but finished last: it ran more recently than "newer-start".
            task("long", daily, last = run("succeeded", "2026-10-31T06:00:00+00:00", "2026-10-31T11:00:00+00:00")),
            task("newer-start", daily, last = run("succeeded", "2026-10-31T10:00:00+00:00", "2026-10-31T10:05:00+00:00")),
            // Same instant written in another offset, one minute later.
            task("offset", daily, last = run("failed", "2026-10-31T07:00:00-05:00", "2026-10-31T07:01:00-05:00", acknowledged = true)),
            task("running-late", daily, active = run("queued", "2026-11-01T10:00:00+00:00")),
            task("never-b", daily),
            task("no-finish-time", daily, last = run("cancelled", "2026-10-31T09:00:00+00:00")))
        assertEquals(listOf("running-late", "running-early", "offset", "long", "newer-start", "no-finish-time", "older", "never-a", "never-b"),
            RemTaskList.shown(tasks).map { it.id })
    }

    @Test fun finishedOneTimeTasksLeaveTheList() {
        val done = "2026-10-31T10:00:00+00:00"
        assertTrue(RemTaskList.finishedOnce(task("ok", last = run("succeeded", done, done))))
        assertTrue(RemTaskList.finishedOnce(task("stopped", last = run("cancelled", done, done))))
        assertTrue(RemTaskList.finishedOnce(task("dismissed", last = run("failed", done, done, acknowledged = true))))
        // Still needs the user, still running, waiting for its time, never run, or repeating: listed.
        assertFalse(RemTaskList.finishedOnce(task("failed", last = run("failed", done, done))))
        assertFalse(RemTaskList.finishedOnce(task("blocked", last = run("blocked", done, done))))
        assertFalse(RemTaskList.finishedOnce(task("again", active = run("executing", done), last = run("executing", done))))
        assertFalse(RemTaskList.finishedOnce(task("later", last = run("succeeded", done, done), next = "2026-11-02T09:00:00+00:00")))
        assertFalse(RemTaskList.finishedOnce(task("new")))
        assertFalse(RemTaskList.finishedOnce(task("no-finish-time", last = run("cancelled", done))))
        assertFalse(RemTaskList.finishedOnce(task("daily", daily, last = run("succeeded", done, done))))
        assertEquals(listOf("failed"), RemTaskList.shown(listOf(task("ok", last = run("succeeded", done, done)), task("failed", last = run("failed", done, done)))).map { it.id })
    }

    @Test fun desktopRetentionFieldsDecide() {
        val done = "2026-10-31T10:00:00+00:00"
        // A desktop with task retention sends ran_at (creation time for a task that never ran) and listed on rem.task.
        val tasks = listOf(
            task("old-run", daily, last = run("succeeded", "2026-10-29T10:00:00+00:00", "2026-10-29T10:05:00+00:00")).copy(ranAt = "2026-10-29T10:05:00+00:00"),
            task("created-today", daily).copy(ranAt = "2026-11-01T08:00:00+00:00"),
            task("running", daily, active = run("executing", "2026-10-28T10:00:00+00:00")).copy(ranAt = "2026-10-28T10:00:00+00:00"))
        assertEquals(listOf("running", "created-today", "old-run"), RemTaskList.shown(tasks).map { it.id })
        assertTrue(RemTaskList.finishedOnce(task("hidden", last = run("failed", done, done)).copy(listed = false)))
        assertFalse(RemTaskList.finishedOnce(task("kept", last = run("succeeded", done, done)).copy(listed = true)))
    }

    @Test fun chatKeepsLinksToHiddenTasks() {
        val json = JSONObject()
            .put("messages", JSONArray().put(JSONObject().put("id", "m1").put("role", "rem").put("body", "Done.")
                .put("work", JSONObject().put("task_id", "t1").put("run_id", "r1").put("thread_id", "w1").put("title", "Rename branch").put("state", "succeeded"))))
            .put("tasks", JSONArray().put(JSONObject().put("task_id", "t1").put("title", "Rename branch").put("type", "errand").put("state", "enabled")
                .put("schedule", JSONObject().put("kind", "once")).put("cleanup_at", "2026-11-30T10:00:00+00:00")
                .put("last_run", JSONObject().put("run_id", "r1").put("state", "succeeded").put("created_at", "2026-10-31T09:00:00+00:00")
                    .put("finished_at", "2026-10-31T10:00:00+00:00").put("thread_id", "w1"))))
        val home = DesktopViews.parse("rem.home", json) as RemHome
        assertEquals(RemWork("t1", "r1", "w1", "Rename branch"), home.messages.single().work)
        val removed = JSONObject().put("messages", JSONArray().put(JSONObject().put("id", "m2").put("role", "rem")
            .put("work", JSONObject().put("task_id", "t0").put("thread_id", "w0").put("task_available", false).put("thread_available", false))))
        assertEquals(RemWork("t0", "", "w0", "", taskAvailable = false, threadAvailable = false),
            (DesktopViews.parse("rem.home", removed) as RemHome).messages.single().work)
        assertEquals("2026-11-30T10:00:00+00:00", home.tasks.single().cleanupAt)
        assertEquals(emptyList<RemTask>(), RemTaskList.shown(home.tasks))
        assertEquals("rem.task:t1", DesktopViews.key("rem.task", JSONObject().put("task_id", "t1")))
        assertEquals("rem.thread:w1", DesktopViews.key("rem.thread", JSONObject().put("thread_id", "w1")))
        val view = DesktopViews.parse("rem.task", JSONObject().put("task_id", "t1").put("listed", false).put("ran_at", "2026-10-31T10:00:00+00:00")) as RemTask
        assertEquals(false to "2026-10-31T10:00:00+00:00", view.listed to view.ranAt)
        // An older desktop omits the retention fields.
        val older = DesktopViews.parse("rem.task", JSONObject().put("task_id", "t1")) as RemTask
        assertEquals(Triple(null, "", ""), Triple(older.listed, older.ranAt, older.cleanupAt))
    }
}
