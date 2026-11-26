package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.UUID

class UnreadableEventTest {
    private val threadId = UUID.randomUUID().toString()

    private fun event(type: String, sequence: Long, thread: String?, payload: JSONObject) =
        JSONObject().put("version", 2).put("id", UUID.randomUUID().toString()).put("thread_id", thread ?: JSONObject.NULL)
            .put("request_id", UUID.randomUUID().toString()).put("sequence", sequence).put("type", type).put("payload", payload)

    private fun snapshot(sequence: Long, extra: String? = null): JSONObject {
        val thread = JSONObject().put("thread_id", threadId).put("revision", 1).put("title", "Plans").put("project_id", JSONObject.NULL)
            .put("resource_ids", JSONArray()).put("provider", "claude_code").put("model", "opus")
        extra?.let { thread.put(it, true) }
        return event("sync.snapshot", sequence, null,
            JSONObject().put("revision", 1).put("through_sequence", sequence - 1).put("threads", JSONArray().put(thread)))
    }

    private fun status(sequence: Long) =
        event("request.status", sequence, threadId, JSONObject().put("status", "running").put("detail", ""))

    private fun receive(state: JSONObject, event: JSONObject) =
        LiveJournal.receive(state, event, event.getString("id"), event.getLong("sequence"), event.getString("type"))

    @Test fun snapshotWithAnExtraThreadFieldIsRecordedButNotAppliedAndTheNextEventApplies() {
        val state = JSONObject().put("cursor", 4).put("threads", JSONArray())
        val unreadable = snapshot(6, extra = "pinned_by_newer_desktop")
        assertThrows(Exception::class.java) { IncomingValidation.validate(unreadable) }

        assertEquals(LiveJournal.Receipt.SKIPPED, receive(state, unreadable))
        val recent = state.getJSONArray("received_recent")
        assertEquals(1, recent.length())
        assertEquals(unreadable.getString("id"), recent.getJSONArray(0).getString(0))
        // Nothing from the payload lands: no threads, and the cursor doesn't trust its through_sequence.
        assertEquals(0, state.getJSONArray("threads").length())
        assertEquals(4L, state.getLong("cursor"))
        // The same event again is a duplicate, not a second skip.
        assertEquals(LiveJournal.Receipt.DUPLICATE, receive(state, unreadable))

        val valid = status(7)
        assertEquals(LiveJournal.Receipt.APPLY, receive(state, valid))
        assertTrue(state.getJSONObject("received").has(valid.getString("id")))
    }

    @Test fun skippedEventMovesTheCursorPastItLikeAHandledEvent() {
        val state = JSONObject().put("cursor", 4)
        val unreadable = status(5).apply { getJSONObject("payload").put("status", "teleported") }

        assertEquals(LiveJournal.Receipt.SKIPPED, receive(state, unreadable))
        assertTrue(state.getJSONObject("received").has(unreadable.getString("id")))
        assertEquals(5L, state.getLong("cursor"))
        assertFalse(state.getJSONObject("received").length() > 1)

        assertEquals(LiveJournal.Receipt.APPLY, receive(state, status(6)))
        // An out-of-order event still fails the sequence check and so still ends the connection.
        assertThrows(IllegalArgumentException::class.java) { receive(state, status(5)) }
    }
}
