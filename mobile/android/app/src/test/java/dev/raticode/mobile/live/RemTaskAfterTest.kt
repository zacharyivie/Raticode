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
