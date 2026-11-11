package dev.raticode.mobile.live

import dev.raticode.mobile.live.RemThreadRows.Failure
import dev.raticode.mobile.live.RemThreadRows.Instruction
import dev.raticode.mobile.live.RemThreadRows.Reply
import dev.raticode.mobile.live.RemThreadRows.Thoughts
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class RemThreadRowsTest {
    private fun step(title: String, detail: String = "", command: String = "", status: String = "complete") =
        JSONObject().put("kind", "tool").put("title", title).put("detail", detail).put("body", "").put("command", command).put("status", status)

    private fun thread(running: Boolean, vararg messages: JSONObject) = DesktopViews.parse("rem.thread:w",
        JSONObject().put("thread_id", "w").put("title", "Fix the build").put("running", running).put("earlier", 0)
            .put("messages", JSONArray(messages.toList()))) as RemThread

    private fun message(id: String, role: String, body: String, kind: String = "") =
        JSONObject().put("id", id).put("role", role).put("body", body).put("kind", kind)

    private fun group(id: String, count: Int, vararg steps: JSONObject) =
        message(id, "assistant", "Worked through $count steps.", "thoughts").put("count", count).put("steps", JSONArray(steps.toList()))

    @Test fun groupsFoldOnceAReplyFollowsAndTheLastOneShowsItIsRunning() {
        val rows = RemThreadRows.build(thread(true,
            message("ask", "user", "Fix the build"),
            group("g1", 120, step("Bash", "Run the tests"), step("Read", command = "cat app.kt")),
            message("done", "assistant", "## Fixed", "final"),
            message("ask2", "user", "Check the loading state"),
            group("g2", 2, step("Bash", status = "running"))))
        assertEquals(listOf("ask", "g1", "done", "ask2", "g2"), rows.map { it.id })
        val first = rows[1] as Thoughts
        // The desktop sends the newest steps of a long run; the count is the whole run.
        assertEquals(120, first.count); assertEquals(2, first.steps.size)
        assertTrue(first.replied); assertFalse(first.running)
        val last = rows[4] as Thoughts
        assertFalse(last.replied); assertTrue(last.running)
        assertTrue(rows[0] is Instruction && rows[2] is Reply)
        assertEquals("Run the tests", RemThreadRows.stepDetail(first.steps[0]))
        assertEquals("cat app.kt", RemThreadRows.stepDetail(first.steps[1]))
    }

    @Test fun aFinishedThreadNeverShowsWorkInProgress() {
        val rows = RemThreadRows.build(thread(false, message("ask", "user", "Go"), group("g", 1, step("Bash", status = "running"))))
        val only = rows[1] as Thoughts
        assertFalse(only.running); assertFalse(only.replied)
    }

    @Test fun errorsAndEmptyRepliesAreHandled() {
        val rows = RemThreadRows.build(thread(false,
            message("ask", "user", "Go"), group("g", 1, step("Bash")),
            message("blank", "assistant", "  ", "final"), message("err", "assistant", "The worker stopped.", "error")))
        assertEquals(listOf("ask", "g", "err"), rows.map { it.id })
        assertTrue(rows[2] is Failure)
        // An error isn't a reply, so the thoughts before it stay open, as on the desktop.
        assertFalse((rows[1] as Thoughts).replied)
    }

    @Test fun anOlderDesktopsSeparateThoughtsAreFoldedOnThePhone() {
        val rows = RemThreadRows.build(thread(false,
            message("ask", "user", "Go"),
            message("t1", "assistant", "Thinking", "thought"), message("t2", "assistant", "Bash", "thought"),
            message("t3", "assistant", "", "thought"),
            message("done", "assistant", "Done.", "final")))
        assertEquals(listOf("ask", "t1", "done"), rows.map { it.id })
        val folded = rows[1] as Thoughts
        assertEquals(2, folded.count); assertEquals(listOf("Thinking", "Bash"), folded.steps.map { it.title })
        assertTrue(folded.replied)
    }

    @Test fun longInstructionsStartFolded() {
        assertFalse(RemThreadRows.longInstruction("Fix the build"))
        assertTrue(RemThreadRows.longInstruction("x".repeat(601)))
        assertTrue(RemThreadRows.longInstruction((1..9).joinToString("\n") { "line $it" }))
    }

    @Test fun instructionsSayWhetherRemOrTheUserWroteThem() {
        val rows = RemThreadRows.build(thread(false,
            message("goal", "user", "Fix the build").put("sender", "rem"),
            message("done", "assistant", "Fixed", "final"),
            message("mine", "user", "Also check the loading state").put("sender", "user"),
            // An older desktop sends no sender, and an unknown one isn't trusted.
            message("old", "user", "Thanks"),
            message("odd", "user", "Hm").put("sender", "someone")))
        val instructions = rows.filterIsInstance<Instruction>()
        assertEquals(listOf("rem", "user", "", ""), instructions.map { it.author })
        assertEquals(listOf("goal", "mine", "old", "odd"), instructions.map { it.id })
    }
}
