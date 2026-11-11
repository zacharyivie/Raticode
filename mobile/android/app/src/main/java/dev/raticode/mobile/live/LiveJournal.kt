package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

/** Local quota failures are recoverable without dropping the authenticated connection. */
class LiveStorageLimit(message: String) : IllegalStateException(message)

/** Journal-only operations. Callers commit the returned copy before publishing or sending it. */
object LiveJournal {
    const val MAX_MESSAGES = 2000
    const val MAX_OUTBOX = 128
    const val MAX_OUTBOX_BYTES = 8 * 1024 * 1024
    const val MAX_TOMBSTONES = 100000
    const val MAX_JOURNAL_BYTES = 32 * 1024 * 1024
    private val terminal = setOf("Rem", "Completed", "Desktop rejected request", "completed", "failed", "cancelled", "rejected")

    fun requireMessageRoom(messages: JSONArray) {
        if (messages.length() >= MAX_MESSAGES) throw LiveStorageLimit("Live history is full. Clear completed history in a thread to make room. Pending messages will be kept.")
    }

    fun prepare(state: JSONObject, type: String, thread: String?, payload: JSONObject, requestId: String? = null): Pair<JSONObject, JSONObject> {
        val next = JSONObject(state.toString())
        val outbox = next.optJSONArray("outbox") ?: JSONArray()
        if (outbox.length() >= MAX_OUTBOX) throw LiveStorageLimit("The offline queue is full. Reconnect to deliver queued requests, then send again. Your draft is kept.")
        val sequence = next.optLong("sequence", 0)
        check(sequence < Long.MAX_VALUE)
        val event = JSONObject().put("version", 2).put("id", UUID.randomUUID().toString()).put("request_id", requestId ?: UUID.randomUUID().toString())
            .put("sequence", sequence).put("thread_id", thread ?: JSONObject.NULL).put("type", type).put("payload", payload)
        val encoded = event.toString()
        val pendingBytes = (0 until outbox.length()).sumOf { outbox.getString(it).toByteArray(Charsets.UTF_8).size.toLong() }
        if (pendingBytes + encoded.toByteArray(Charsets.UTF_8).size > MAX_OUTBOX_BYTES)
            throw LiveStorageLimit("The offline queue byte limit is reached. Reconnect to deliver queued requests. Your draft is kept.")
        outbox.put(encoded); next.put("outbox", outbox).put("sequence", sequence + 1)
        if (type == "chat.submit") {
            LiveHistory.makeRoom(next, thread, 1)
            val messages = next.optJSONArray("messages") ?: JSONArray()
            messages.put(JSONObject().put("id", event.getString("request_id")).put("thread", thread)
                .put("text", payload.getString("text")).put("state", "Queued on phone")
                .put("attachments", LiveHistory.attachmentSnapshot(next, payload.optJSONArray("attachment_ids") ?: JSONArray())))
            next.put("messages", messages)
            // A newer draft may have arrived while this send waited in the command queue.
            val drafts = next.optJSONObject("drafts")
            if (drafts?.optString(thread!!) == payload.getString("text")) drafts.put(thread, "")
        }
        return next to event
    }

    /** Resolve only an authenticated creation receipt, in the same transaction as its outbox removal. */
    fun resolveCreation(state: JSONObject, request: String, thread: String?): Pair<JSONObject, JSONObject?> {
        val creation = state.optJSONObject("creations")?.optJSONObject(request) ?: return state to null
        var next = JSONObject(state.toString())
        next.getJSONObject("creations").remove(request)
        val text = creation.optString("text")
        var outgoing: JSONObject? = null
        if (text.isNotBlank() && thread != null) {
            val prepared = prepare(next, "chat.submit", thread, JSONObject().put("text", text).put("attachment_ids", JSONArray()))
            next = prepared.first; outgoing = prepared.second
        }
        val draft = creation.optString("draft").ifBlank { if (thread == null) text else "" }
        if (draft.isNotBlank()) {
            val drafts = next.optJSONObject("drafts") ?: JSONObject().also { next.put("drafts", it) }
            // The home composer is disabled until this receipt resolves its pending creation.
            drafts.put(thread ?: "new-thread", draft)
        }
        return next to outgoing
    }

    /** Snapshots replace state and recur every few seconds, so their duplicate check is a recent window. */
    val SNAPSHOT_TYPES = setOf("sync.snapshot", "thread.history.snapshot", "catalog.snapshot", "thread.search.snapshot",
        "fleet.snapshot", "swarm.snapshot", "approval.snapshot", "desktop.result")
    const val RECENT_SNAPSHOTS = 512

    fun recordRecent(state: JSONObject, id: String, hash: String): Boolean {
        val recent = state.optJSONArray("received_recent") ?: JSONArray()
        for (i in 0 until recent.length()) {
            val entry = recent.getJSONArray(i)
            if (entry.getString(0) == id) { require(entry.getString(1) == hash) { "Conflicting event identity" }; return false }
        }
        val kept = JSONArray()
        for (i in maxOf(0, recent.length() - RECENT_SNAPSHOTS + 1) until recent.length()) kept.put(recent.get(i))
        state.put("received_recent", kept.put(JSONArray().put(id).put(hash)))
        return true
    }

    fun recordReceived(received: JSONObject, id: String, hash: String): Boolean {
        if (received.has(id)) { require(received.getString(id) == hash) { "Conflicting event identity" }; return false }
        if (received.length() >= MAX_TOMBSTONES)
            throw LiveStorageLimit("This pairing has reached its event safety limit. Keep pending work, recover its outcome on desktop, then revoke and pair again. Clearing history cannot remove replay protection.")
        received.put(id, hash)
        return true
    }

    fun clearHistory(state: JSONObject, thread: String): JSONObject {
        val next = JSONObject(state.toString())
        val pending = next.optJSONArray("outbox") ?: JSONArray()
        val requests = (0 until pending.length()).map { JSONObject(pending.getString(it)).optString("request_id") }.toSet()
        val messages = next.optJSONArray("messages") ?: JSONArray()
        val retained = JSONArray()
        for (i in 0 until messages.length()) {
            val message = messages.getJSONObject(i)
            if (message.optString("thread") != thread || message.optString("state") !in terminal || message.optString("id") in requests)
                retained.put(message)
        }
        next.put("messages", retained)
        // Do not reset cursors, request IDs, drafts, outbox bytes or received hashes.
        return next
    }

    fun referencesFile(state: JSONObject, id: String): Boolean {
        if (HeldSends.references(state, id)) return true
        val pending = state.optJSONArray("outbox") ?: return false
        return (0 until pending.length()).any {
            val payload = JSONObject(pending.getString(it)).getJSONObject("payload")
            // Rem chat messages carry their files inside the desktop action's params.
            val attachments = payload.optJSONArray("attachment_ids") ?: payload.optJSONObject("params")?.optJSONArray("attachment_ids") ?: JSONArray()
            payload.optString("file_id") == id || (0 until attachments.length()).any { index -> attachments.optString(index) == id }
        }
    }
}
