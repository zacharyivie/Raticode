package dev.raticode.mobile.live

import dev.raticode.mobile.ui.RemTaskStatus
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Tasks that start after other tasks read, group and act as on the desktop (lib/remHome.js and remRich.js). */
class RemTaskAfterTest {
    private fun prerequisite(state: String, runState: String, retryable: Boolean, reason: String = "") = JSONObject()
        .put("task_id", "audit").put("title", "Audit").put("state", state).put("reason", reason).put("run_id", "r1")
        .put("run_state", runState).put("retryable", retryable)

    private fun home(state: String, reason: String, item: JSONObject, messages: JSONArray = JSONArray()) = DesktopViews.parse("rem.home", JSONObject()
        .put("messages", messages)
        .put("tasks", JSONArray().put(JSONObject().put("task_id", "fix").put("title", "Fix").put("type", "develop").put("state", "enabled")
            .put("schedule", JSONObject().put("kind", "once"))
            .put("dependency", JSONObject().put("state", state).put("reason", reason).put("prerequisites", JSONArray().put(item)))))) as RemHome

    @Test fun aWaitingTaskIsComingUpAndSaysWhatItWaitsFor() {
        val view = home("waiting", "Waiting for “Audit” to finish", prerequisite("waiting", "executing", false))
        val task = view.tasks.single()
        assertEquals("waiting", task.dependency!!.state)
        val activity = RemTaskStatus.activity(task)
        assertEquals(RemTaskStatus.Kind.WAITING, activity.kind)
        assertEquals("Waiting for “Audit” to finish", activity.label)
        assertEquals(listOf("scheduled"), RemTaskStatus.groups(view.tasks).map { it.id })
        assertEquals("Running" to RemTaskStatus.Tone.NEUTRAL, RemTaskStatus.prerequisite(task.dependency!!.prerequisites.single()))
        assertEquals("Here when you need me", RemTaskStatus.summary(view.tasks))
    }

    @Test fun aHeldTaskNeedsYouAndOffersRetryAndStartNow() {
        val reason = "On hold: “Audit” was blocked. Retry it, or start this task now."
        val card = JSONObject().put("id", "after-held-fix-1").put("role", "rem").put("kind", "task_update").put("body", "On hold")
            .put("work", JSONObject().put("task_id", "fix").put("run_id", "").put("thread_id", "").put("title", "Fix").put("state", "held"))
        val view = home("held", reason, prerequisite("held", "blocked", true, "“Audit” was blocked"), JSONArray().put(card))
        val task = view.tasks.single()
        val activity = RemTaskStatus.activity(task)
        assertEquals(RemTaskStatus.Kind.ATTENTION, activity.kind)
        assertEquals(reason, activity.label)
        assertEquals("1 thing needs you", RemTaskStatus.summary(view.tasks))
        assertEquals("Needs you" to RemTaskStatus.Tone.ATTENTION, RemTaskStatus.prerequisite(task.dependency!!.prerequisites.single()))
        val message = view.messages.single()
        assertTrue(message.id in RemRich.liveWork(view.messages))
        assertEquals(listOf("retry-after:audit" to "audit", "run" to "fix"),
            RemRich.resultActions(message, view.tasks, live = true, reviewRetry = true).map { it.id to it.taskId })
        assertEquals(listOf("Retry “Audit”", "Start now"), RemRich.resultActions(message, view.tasks, live = true, reviewRetry = true).map { it.label })
    }

    @Test fun conditionsOtherThanTasksAreLabelledAndNeverRetried() {
        fun condition(kind: String, title: String, state: String = "waiting", taskId: String = "", runState: String = "", retryable: Boolean = false) =
            JSONObject().put("kind", kind).put("task_id", taskId).put("title", title).put("state", state).put("reason", "Waiting")
                .put("run_id", if (kind == "run") "r9" else "").put("run_state", runState).put("retryable", retryable)
        val items = JSONArray()
            .put(condition("run", "Audit", taskId = "audit", runState = "executing"))
            .put(condition("time", "7:00 AM Fri (Asia/Irkutsk)"))
            .put(condition("self_work", "Self-work window"))
            // A desktop that wrongly marks a condition with no task retryable still gets no Retry.
            .put(condition("idle", "Nothing else running", retryable = true))
        val card = JSONObject().put("id", "c").put("role", "rem").put("kind", "task_update").put("body", "Waiting")
            .put("work", JSONObject().put("task_id", "fix").put("run_id", "").put("thread_id", "").put("title", "Fix"))
        val view = DesktopViews.parse("rem.home", JSONObject().put("messages", JSONArray().put(card))
            .put("tasks", JSONArray().put(JSONObject().put("task_id", "fix").put("title", "Fix").put("type", "develop").put("state", "enabled")
                .put("schedule", JSONObject().put("kind", "once"))
                .put("dependency", JSONObject().put("state", "waiting").put("reason", "").put("prerequisites", items))))) as RemHome
        val dependency = view.tasks.single().dependency!!
        assertEquals(listOf("run", "time", "self_work", "idle"), dependency.prerequisites.map { it.kind })
        assertEquals(listOf("Running", "Not yet", "Waiting", "Others running"), dependency.prerequisites.map { RemTaskStatus.prerequisite(it).first })
        assertTrue(dependency.prerequisites.none { it.canRetry })
        assertEquals(listOf(true, false, false, false), dependency.prerequisites.map { it.opensTask })
        assertEquals(listOf("run" to "fix"), RemRich.resultActions(view.messages.single(), view.tasks, live = true, reviewRetry = true).map { it.id to it.taskId })
        assertEquals("It starts by itself once all of these are met.", RemTaskStatus.startsAfterNote(dependency.prerequisites))
        // A run that has ended, however it ended, and conditions that are met.
        val met = dependency.prerequisites.map { it.copy(state = "met") }
        assertEquals(listOf("Ended", "Time reached", "Ended", "Nothing running"), met.map { RemTaskStatus.prerequisite(it).first })
        assertEquals("Stopped", RemTaskStatus.prerequisite(dependency.prerequisites[0].copy(runState = "cancelled")).first)
        // With no reason from the desktop, a task waiting only on time says so in general words.
        assertEquals("Waiting to start", RemTaskStatus.activity(view.tasks.single().copy(dependency = dependency.copy(prerequisites = dependency.prerequisites.drop(1)))).label)
        assertEquals("Waiting for another task", RemTaskStatus.activity(view.tasks.single()).label)
    }

    @Test fun anEntryWithoutAKindIsANamedTask() {
        val view = home("held", "On hold", prerequisite("held", "blocked", true))
        val item = view.tasks.single().dependency!!.prerequisites.single()
        assertEquals(RemPrerequisite.TASK, item.kind)
        assertTrue(item.canRetry)
        assertEquals("It starts by itself once each of these finishes successfully.", RemTaskStatus.startsAfterNote(listOf(item)))
        assertEquals("It starts by itself once all of these are met. Tasks named here must finish successfully.",
            RemTaskStatus.startsAfterNote(listOf(item, item.copy(kind = RemPrerequisite.TIME, taskId = ""))))
        // Retry needs the task's ID.
        assertTrue(!item.copy(taskId = "").canRetry)
    }

    @Test fun aHoldCardGivesWayToItsTasksFirstRun() {
        fun card(id: String, run: String) = RemMessage(id, "rem", "", "", RemWork("fix", run, "", "Fix"), "")
        val other = RemMessage("o", "rem", "", "", RemWork("audit", "r0", "", "Audit"), "")
        assertEquals(setOf("h", "o"), RemRich.liveWork(listOf(card("h", ""), other)))
        assertEquals(setOf("o", "s"), RemRich.liveWork(listOf(card("h", ""), other, card("s", "r1"))))
    }

    @Test fun anOlderDesktopSendsNoDependencyAndNothingChanges() {
        val view = DesktopViews.parse("rem.home", JSONObject().put("tasks", JSONArray().put(JSONObject().put("task_id", "t").put("title", "T")
            .put("state", "enabled").put("schedule", JSONObject().put("kind", "once"))))) as RemHome
        assertNull(view.tasks.single().dependency)
        assertEquals(RemTaskStatus.Kind.IDLE, RemTaskStatus.activity(view.tasks.single()).kind)
    }

    @Test fun aQueuedRequestSaysWhatItWaitedFor() {
        val view = DesktopViews.parse("rem.home", JSONObject().put("messages", JSONArray()
            .put(JSONObject().put("id", "q").put("role", "user").put("body", "Fix the findings").put("queued_after", JSONArray().put("CLI audit")))
            .put(JSONObject().put("id", "r").put("role", "rem").put("body", "Hi").put("queued_after", JSONArray().put("ignored"))))) as RemHome
        assertEquals(listOf("CLI audit"), view.messages[0].queuedAfter)
        assertEquals(emptyList<String>(), view.messages[1].queuedAfter)
        assertEquals("Queued until “CLI audit” finished", RemRich.queuedNote(view.messages[0].queuedAfter))
    }
}
