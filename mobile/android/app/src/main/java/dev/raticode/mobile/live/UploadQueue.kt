package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject

/**
 * Which upload chunk goes next, and how far each upload has got. Uploads with the fewest bytes left go first, so a
 * picture isn't stuck behind a large video, and up to [WINDOW] chunks wait for the desktop's answer at once.
 * An upload's `offset` is what the desktop confirmed; `sent` is the end of the last chunk queued in the outbox.
 */
object UploadQueue {
    const val WINDOW = 4
    private val ENDED = setOf("cancelled", "failed")

    fun inFlight(state: JSONObject): Int {
        val outbox = state.optJSONArray("outbox") ?: return 0
        return (0 until outbox.length()).count { JSONObject(outbox.getString(it)).optString("type") == "file.chunk" }
    }

    /** Where the next chunk of [file] starts. */
    fun sent(file: JSONObject): Long = maxOf(file.getLong("offset"), file.optLong("sent", 0))

    /** The upload to send a chunk of next, or null when every chunk is queued or nothing is uploading. */
    fun next(files: JSONObject, nowSeconds: Long): String? = files.keys().asSequence().map { it to files.getJSONObject(it) }
        .filter { (_, file) ->
            file.getString("direction") == "upload" && file.getString("state") == "receiving" && file.getLong("expires_at") > nowSeconds &&
                // An empty file still sends one chunk, so it counts as queued only once `sent` is set.
                !(file.has("sent") && sent(file) >= file.getLong("size"))
        }
        .minByOrNull { (_, file) -> file.getLong("size") - sent(file) }?.first

    /**
     * Marks the upload behind a refused `file.offer` or `file.chunk` as failed, so it stops instead of waiting forever.
     * [pending] is the outbox before the refusal removed the request. Returns the file's ID when it changed.
     */
    fun failRefused(state: JSONObject, pending: JSONArray, request: String): String? {
        val original = (0 until pending.length()).map { JSONObject(pending.getString(it)) }
            .firstOrNull { it.optString("request_id") == request && it.optString("type") in setOf("file.offer", "file.chunk") } ?: return null
        val id = original.getJSONObject("payload").getString("file_id")
        val file = state.optJSONObject("files")?.optJSONObject(id) ?: return null
        if (file.getString("direction") != "upload" || file.getString("state") in ENDED || file.getString("state") == "available") return null
        file.put("state", "failed")
        return id
    }

    /** Stops an unfinished upload on this phone at once; its `file.cancel` tells the desktop. Returns whether it changed. */
    fun cancel(state: JSONObject, id: String): Boolean {
        val file = state.optJSONObject("files")?.optJSONObject(id) ?: return false
        if (file.getString("direction") != "upload" || file.getString("state") !in setOf("offered", "receiving")) return false
        file.put("state", "cancelled")
        return true
    }

    /** How much of an upload the desktop confirmed, from 0 to 1. */
    fun progress(state: String, confirmed: Long, size: Long): Float = when {
        state == "available" -> 1f
        size <= 0L -> 0f
        else -> (confirmed.toFloat() / size).coerceIn(0f, 1f)
    }
}

/**
 * A Rem message sent while its files were still uploading. It waits in the encrypted journal under `rem_held` and
 * goes to the desktop as an ordinary `rem.message` once every file has arrived, in the same save that removes it,
 * so it is sent once. If a file fails, is cancelled or expires, the message comes back with Edit instead.
 */
object HeldSends {
    const val KEY = "rem_held"
    sealed interface Check {
        data object Send : Check
        data object Wait : Check
        /** [name] is the file that won't arrive. */
        data class Fail(val name: String) : Check
    }

    fun decide(files: JSONObject?, ids: List<String>, thread: String?, nowSeconds: Long): Check {
        var waiting = false
        for (id in ids) {
            val file = files?.optJSONObject(id) ?: return Check.Fail("A file")
            val name = file.optString("name").ifBlank { "A file" }
            if (file.getString("direction") != "upload" || file.getString("thread") != thread) return Check.Fail(name)
            when (file.getString("state")) {
                "available" -> {}
                "offered", "receiving" -> if (file.getLong("expires_at") <= nowSeconds) return Check.Fail(name) else waiting = true
                else -> return Check.Fail(name)
            }
        }
        return if (waiting) Check.Wait else Check.Send
    }

    /** [params] are the `rem.message` params without `attachment_ids`; [thread] is the Rem chat file channel. */
    fun hold(state: JSONObject, text: String, params: JSONObject, ids: List<String>, thread: String, reply: JSONObject?) {
        check(!state.has(KEY)) { "Another message is waiting for its files" }
        state.put(KEY, JSONObject().put("text", text).put("params", params).put("files", JSONArray(ids)).put("thread", thread)
            .apply { reply?.let { put("reply", it) } })
    }

    /** What to do with the held message in [state] now. */
    fun decide(state: JSONObject, nowSeconds: Long): Check? = state.optJSONObject(KEY)?.let { held ->
        decide(state.optJSONObject("files"), ids(held), held.getString("thread"), nowSeconds)
    }

    fun ids(held: JSONObject): List<String> = held.getJSONArray("files").let { a -> (0 until a.length()).map { a.getString(it) } }

    /** Whether a held message still needs [id], so it isn't removed or expired before the message goes. */
    fun references(state: JSONObject, id: String): Boolean = state.optJSONObject(KEY)?.let { id in ids(it) } == true

    fun failure(name: String) = "$name didn't finish uploading. Tap Edit, attach it again, then send."
}
