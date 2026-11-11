package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class LiveJournalTest {
    private fun message(id: String, state: String, thread: String = "thread") =
        JSONObject().put("id", id).put("thread", thread).put("text", "private body").put("state", state)
    private fun pending(id: String, payload: JSONObject = JSONObject()) =
        JSONObject().put("request_id", id).put("payload", payload).toString()

    @Test fun clearingHistoryKeepsUnresolvedWorkDraftsCursorAndReplayEvidence() {
        val state = JSONObject().put("cursor", 71).put("received", JSONObject().put("event", "hash"))
            .put("drafts", JSONObject().put("thread", "keep draft"))
            .put("outbox", JSONArray().put(pending("queued")))
            .put("messages", JSONArray().put(message("reply", "Rem")).put(message("finished", "Completed"))
                .put(message("queued", "Completed")).put(message("accepted", "Accepted by desktop"))
                .put(message("uncertain", "outcome_unknown")).put(message("other", "Rem", "another")))
        val original = state.toString()
        val next = LiveJournal.clearHistory(state, "thread")
        assertEquals(original, state.toString())
        assertEquals(listOf("queued", "accepted", "uncertain", "other"), (0 until next.getJSONArray("messages").length()).map {
            next.getJSONArray("messages").getJSONObject(it).getString("id")
        })
        for (key in listOf("drafts", "cursor", "outbox", "received")) assertEquals(state.get(key).toString(), next.get(key).toString())
    }

    @Test fun queueFullAndHistoryFullNeverConsumeDraftOrAllocateSequence() {
        val outbox = JSONArray()
        repeat(LiveJournal.MAX_OUTBOX) { outbox.put(pending("$it")) }
        val state = JSONObject().put("outbox", outbox).put("sequence", 71).put("drafts", JSONObject().put("thread", "keep draft"))
        val original = state.toString()
        val failure = assertThrows(LiveStorageLimit::class.java) { LiveJournal.prepare(state, "chat.submit", "thread", JSONObject().put("text", "keep draft")) }
        assertTrue(failure.message!!.contains("Reconnect"))
        assertEquals(original, state.toString())
        state.put("outbox", JSONArray()).put("messages", JSONArray().apply { repeat(LiveJournal.MAX_MESSAGES) { put(message("$it", "Rem")) } })
        val fullHistory = state.toString()
        assertThrows(LiveStorageLimit::class.java) { LiveJournal.prepare(state, "chat.submit", "thread", JSONObject().put("text", "keep draft")) }
        assertEquals(fullHistory, state.toString())
    }

    @Test fun durableQueueCopyClearsOnlyTheSubmittedDraftAndPreservesRetryBytes() {
        val state = JSONObject().put("sequence", 5).put("drafts", JSONObject().put("thread", "newer typing"))
        val payload = JSONObject().put("text", "submitted").put("attachment_ids", JSONArray())
        val (next, event) = LiveJournal.prepare(state, "chat.submit", "thread", payload)
        assertEquals("newer typing", next.getJSONObject("drafts").getString("thread"))
        assertFalse(state.has("outbox"))
        val persisted = JSONObject(next.toString())
        assertEquals(event.toString(), persisted.getJSONArray("outbox").getString(0))
        assertEquals(event.getString("request_id"), persisted.getJSONArray("messages").getJSONObject(0).getString("id"))
        assertEquals(6, persisted.getInt("sequence"))
        state.getJSONObject("drafts").put("thread", "submitted")
        assertEquals("", LiveJournal.prepare(state, "chat.submit", "thread", payload).first.getJSONObject("drafts").getString("thread"))
    }

    @Test fun formerEventLimitDoesNotLoseDeduplicationAndSafetyLimitFailsClosed() {
        val received = JSONObject()
        repeat(10001) { assertTrue(LiveJournal.recordReceived(received, "id-$it", "hash-$it")) }
        val reloaded = JSONObject(received.toString())
        assertFalse(LiveJournal.recordReceived(reloaded, "id-0", "hash-0"))
        assertThrows(IllegalArgumentException::class.java) { LiveJournal.recordReceived(reloaded, "id-0", "changed") }
        for (i in 10001 until LiveJournal.MAX_TOMBSTONES) reloaded.put("id-$i", "hash-$i")
        assertThrows(LiveStorageLimit::class.java) { LiveJournal.recordReceived(reloaded, "new", "new-hash") }
        assertFalse(reloaded.has("new"))
        assertFalse(LiveJournal.recordReceived(reloaded, "id-0", "hash-0"))
    }

    @Test fun queueByteLimitUsesUtf8AndDoesNotMutateOriginal() {
        val state = JSONObject().put("outbox", JSONArray().put("é".repeat(LiveJournal.MAX_OUTBOX_BYTES / 2)))
        assertThrows(LiveStorageLimit::class.java) { LiveJournal.prepare(state, "sync.request", null, JSONObject()) }
        assertEquals(1, state.getJSONArray("outbox").length())
        assertFalse(state.has("sequence"))
    }
    @Test fun creationReceiptAtomicallyQueuesFirstMessageAndReplayCannotDuplicateIt() {
        val persisted = JSONObject().put("sequence", 12).put("creations", JSONObject().put("create-request",
            JSONObject().put("text", "First message").put("draft", "")))
        val original = persisted.toString()
        val (next, outgoing) = LiveJournal.resolveCreation(JSONObject(original), "create-request", "new-thread-id")
        assertEquals(original, persisted.toString())
        assertEquals("chat.submit", outgoing!!.getString("type"))
        assertEquals("new-thread-id", outgoing.getString("thread_id"))
        val reloaded = JSONObject(next.toString())
        assertEquals(outgoing.toString(), reloaded.getJSONArray("outbox").getString(0))
        assertEquals("First message", reloaded.getJSONArray("messages").getJSONObject(0).getString("text"))
        val replay = LiveJournal.resolveCreation(reloaded, "create-request", "new-thread-id")
        assertNull(replay.second)
        assertEquals(1, replay.first.getJSONArray("outbox").length())
    }

    @Test fun rejectedCreationRestoresDraftAndAttachmentCreationDoesNotSendIt() {
        val state = JSONObject().put("creations", JSONObject().put("request", JSONObject().put("text", "Keep this message").put("draft", "")))
        val failed = LiveJournal.resolveCreation(state, "request", null)
        assertNull(failed.second)
        assertEquals("Keep this message", failed.first.getJSONObject("drafts").getString("new-thread"))
        state.getJSONObject("creations").getJSONObject("request").put("text", "").put("draft", "Attach before sending")
        val attached = LiveJournal.resolveCreation(state, "request", "new-thread-id")
        assertNull(attached.second)
        assertEquals("Attach before sending", attached.first.getJSONObject("drafts").getString("new-thread-id"))
        assertFalse(attached.first.has("outbox"))
    }

    @Test fun fullHistoryLeavesCreationIntentUntouchedForRecovery() {
        val state = JSONObject().put("creations", JSONObject().put("request", JSONObject().put("text", "Do not lose this")))
            .put("messages", JSONArray().apply { repeat(LiveJournal.MAX_MESSAGES) { put(message("$it", "Rem")) } })
        val original = state.toString()
        assertThrows(LiveStorageLimit::class.java) { LiveJournal.resolveCreation(state, "request", "thread") }
        assertEquals(original, state.toString())
    }
}
