package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject

/** Authenticated history replaces its page, including metadata, without changing pending receipts. */
object LiveHistory {
    /** Encrypted local budget for cached desktop history across threads. */
    const val CACHE_BYTES = 8 * 1024 * 1024
    private val completed = setOf("Rem", "Desktop", "Completed", "completed", "failed", "cancelled", "rejected", "Desktop rejected request")

    fun merge(local: JSONArray, thread: String, payload: JSONObject): JSONArray {
        val incoming = payload.getJSONArray("messages")
        val start = payload.getLong("before")
        val end = start + incoming.length()
        val total = payload.optLong("total", Long.MAX_VALUE)
        val ids = (0 until incoming.length()).map { incoming.getJSONObject(it).getString("id") }.toSet()
        val previous = (0 until local.length()).map { local.getJSONObject(it) }
        val byId = previous.associateBy { it.getString("id") }
        val retained = previous.filterNot { m ->
            m.getString("thread") == thread && (m.getString("id") in ids ||
                (m.optString("state") in completed && m.has("history_index") && (m.getLong("history_index") in start until end || m.getLong("history_index") >= total)))
        }.toMutableList()
        for (i in 0 until incoming.length()) {
            val wire = incoming.getJSONObject(i)
            val m = JSONObject(byId[wire.getString("id")]?.toString() ?: "{}")
            m.put("id", wire.getString("id")).put("thread", thread).put("text", wire.getString("text"))
                .put("history_index", start + i)
            if (!m.has("state")) m.put("state", when (wire.getString("role")) { "assistant" -> "Rem"; "system" -> "Desktop"; else -> "Completed" })
            for (key in listOf("source_id", "offset", "presentation", "attachments"))
                if (wire.has(key)) m.put(key, wire.get(key))
            retained.add(m)
        }
        return JSONArray(retained.filter { it.getString("thread") != thread } + retained.filter { it.getString("thread") == thread }.sortedBy { it.optLong("history_index", Long.MAX_VALUE) })
    }

    /**
     * Desktop owns history, so the phone's copy is a cache. Drop cached pages of the
     * least recently loaded other threads until [incoming] more messages fit. Queued,
     * unresolved and local-only messages are never dropped. Reopening a thread reloads it.
     */
    fun makeRoom(state: JSONObject, keep: String?, incoming: Int = 0, maxBytes: Int = CACHE_BYTES) {
        var messages = (state.optJSONArray("messages") ?: JSONArray()).let { a -> (0 until a.length()).map { a.getJSONObject(it) } }
        fun bytes() = messages.sumOf { it.toString().length.toLong() }
        fun full() = messages.size + incoming > LiveJournal.MAX_MESSAGES || bytes() > maxBytes
        if (!full()) return
        val evicted = mutableSetOf<String>()
        // Later positions were merged more recently; evict from the oldest thread first.
        val order = messages.map { it.getString("thread") }.withIndex().associate { it.value to it.index }
            .entries.sortedBy { it.value }.map { it.key }.filter { it != keep }
        for (thread in order) {
            if (!full()) break
            val before = messages.size
            messages = messages.filterNot { it.getString("thread") == thread && it.has("history_index") && it.optString("state") in completed }
            if (messages.size != before) evicted.add(thread)
        }
        if (messages.size + incoming > LiveJournal.MAX_MESSAGES)
            throw LiveStorageLimit("Too many unsent or unresolved messages are stored on this phone. Reconnect to deliver them first.")
        state.put("messages", JSONArray(messages))
        state.optJSONObject("history_before")?.let { before -> evicted.forEach { before.remove(it) } }
    }

    fun attachmentSnapshot(state: JSONObject, ids: JSONArray): JSONArray = JSONArray((0 until ids.length()).map { i ->
        val id = ids.getString(i)
        val file = state.optJSONObject("files")?.optJSONObject(id)
        JSONObject().put("id", id).put("name", file?.optString("name") ?: "Attachment")
            .put("size", file?.optLong("size") ?: 0).put("mime", file?.optString("mime") ?: "application/octet-stream")
    })
}
