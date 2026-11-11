package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.util.UUID

/** Long background sync, resource toggles, approvals and the swarm console. */
class BackgroundParityTest {
    private fun event(type: String, payload: JSONObject, thread: String? = null) =
        JSONObject().put("type", type).put("request_id", UUID.randomUUID().toString())
            .put("thread_id", thread ?: JSONObject.NULL).put("payload", payload)

    @Test fun snapshotDuplicatesUseABoundedWindow() {
        val state = JSONObject()
        repeat(LiveJournal.RECENT_SNAPSHOTS + 100) { assertTrue(LiveJournal.recordRecent(state, "id-$it", "hash-$it")) }
        assertEquals(LiveJournal.RECENT_SNAPSHOTS, state.getJSONArray("received_recent").length())
        val last = "id-${LiveJournal.RECENT_SNAPSHOTS + 99}"
        assertFalse(LiveJournal.recordRecent(state, last, "hash-${LiveJournal.RECENT_SNAPSHOTS + 99}"))
        assertThrows(IllegalArgumentException::class.java) { LiveJournal.recordRecent(state, last, "changed") }
        assertTrue("sync.snapshot" in LiveJournal.SNAPSHOT_TYPES && "chat.message" !in LiveJournal.SNAPSHOT_TYPES)
    }

    @Test fun fullHistoryEvictsOldCachedThreadsButNeverPendingWork() {
        val messages = JSONArray()
        fun add(thread: String, count: Int, state: String = "Rem", cached: Boolean = true) = repeat(count) { i ->
            messages.put(JSONObject().put("id", "$thread-$state-$i").put("thread", thread).put("text", "x").put("state", state)
                .apply { if (cached) put("history_index", i) })
        }
        add("old", 900); add("pending", 5, "Queued on phone", cached = false); add("middle", 900); add("open", 195)
        val state = JSONObject().put("messages", messages).put("history_before", JSONObject().put("old", 3).put("middle", 2))
        LiveHistory.makeRoom(state, "open", 10)
        val threads = (0 until state.getJSONArray("messages").length()).map { state.getJSONArray("messages").getJSONObject(it).getString("thread") }
        assertFalse("old" in threads)
        assertEquals(5, threads.count { it == "pending" })
        assertEquals(900, threads.count { it == "middle" })
        assertFalse(state.getJSONObject("history_before").has("old"))
        // Byte budget also evicts cached pages of other threads.
        LiveHistory.makeRoom(state, "open", 0, maxBytes = 30_000)
        assertFalse("middle" in (0 until state.getJSONArray("messages").length()).map { state.getJSONArray("messages").getJSONObject(it).getString("thread") })
    }

    @Test fun tooManyUnsentMessagesStillStopsSafely() {
        val messages = JSONArray()
        repeat(LiveJournal.MAX_MESSAGES) { messages.put(JSONObject().put("id", "q$it").put("thread", "t").put("text", "x").put("state", "Queued on phone")) }
        assertThrows(LiveStorageLimit::class.java) { LiveHistory.makeRoom(JSONObject().put("messages", messages), "other", 1) }
    }

    @Test fun resourceTogglesAndRemoteRecordsAreStrictlyBounded() {
        val resource = JSONObject().put("id", UUID.randomUUID().toString()).put("name", "Commands").put("kind", "builtin").put("enabled", true)
        val history = JSONObject().put("before", 0).put("has_more", false).put("messages", JSONArray())
            .put("resources", JSONArray().put(resource)).put("resource_ids", JSONArray().put(resource.getString("id")))
        IncomingValidation.validate(event("thread.history.snapshot", history, UUID.randomUUID().toString()))
        resource.put("command", "/bin/sh")
        assertThrows(IllegalArgumentException::class.java) { IncomingValidation.validate(event("thread.history.snapshot", history, UUID.randomUUID().toString())) }

        val approval = JSONObject().put("approval_id", UUID.randomUUID().toString()).put("action_digest", "a".repeat(64))
            .put("source", "swarm").put("title", "Previews").put("summary", "Merge?").put("requested_at", "now").put("instruction_required", true)
        val approvals = JSONObject().put("remote_allowed", true).put("approvals", JSONArray().put(approval))
        IncomingValidation.validate(event("approval.snapshot", approvals))
        approval.put("action_digest", "not-a-digest")
        assertThrows(IllegalArgumentException::class.java) { IncomingValidation.validate(event("approval.snapshot", approvals)) }

        val swarm = JSONObject().put("swarm_id", UUID.randomUUID().toString()).put("title", "Previews").put("status", "paused")
            .put("detail", "Add previews").put("progress", 40)
            .put("messages", JSONArray().put(JSONObject().put("sender", "Lead").put("body", "Started").put("created_at", "now")))
        val swarms = JSONObject().put("revision", 1).put("remote_allowed", true).put("swarms", JSONArray().put(swarm))
        IncomingValidation.validate(event("swarm.snapshot", swarms))
        swarm.put("progress", 101)
        assertThrows(IllegalArgumentException::class.java) { IncomingValidation.validate(event("swarm.snapshot", swarms)) }
        swarm.put("progress", 40).put("status", "exploded")
        assertThrows(IllegalArgumentException::class.java) { IncomingValidation.validate(event("swarm.snapshot", swarms)) }
    }
}
