package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject

/**
 * Files attached to the user's messages in Rem's chat, from the desktop window or any phone, fetched from the
 * desktop with `rem.attachment` when this phone doesn't hold them. The desktop keeps every attachment, so the copy
 * here is a cache: the journal maps each attachment ID to the download that holds it (`rem_attachment_files`), and
 * the oldest copies are removed to make room for new ones. The phone's own uploads are used while it still has them.
 */
object RemAttachments {
    const val OP = "rem.attachment"
    const val KEY = "$OP:"
    const val STATE = "rem_attachment_files"
    /** Copies fetched here may fill the phone's file space only this far, leaving room for uploads and Rem's files. */
    const val BUDGET = 40L * 1024 * 1024
    const val COUNT = 90
    private const val REMEMBERED = 500

    /** The download holding [attachment], if one was fetched. */
    fun fileFor(state: JSONObject, attachment: String): String? = state.optJSONObject(STATE)?.optJSONObject(attachment)?.optString("file")?.ifBlank { null }

    fun files(state: JSONObject): Map<String, String> {
        val map = state.optJSONObject(STATE) ?: return emptyMap()
        return map.keys().asSequence().mapNotNull { key -> map.optJSONObject(key)?.optString("file")?.ifBlank { null }?.let { key to it } }.toMap()
    }

    /** Records that [file] holds [attachment], newest last; entries whose download is gone are dropped. */
    fun remember(state: JSONObject, attachment: String, file: String) {
        val map = state.optJSONObject(STATE) ?: JSONObject().also { state.put(STATE, it) }
        val files = state.optJSONObject("files")
        for (key in map.keys().asSequence().toList()) if (files?.has(map.getJSONObject(key).optString("file")) != true) map.remove(key)
        val order = map.keys().asSequence().maxOfOrNull { map.getJSONObject(it).optLong("n") } ?: 0L
        map.put(attachment, JSONObject().put("file", file).put("n", order + 1))
        while (map.length() > REMEMBERED) oldest(map)?.let { map.remove(it) } ?: break
    }

    private fun oldest(map: JSONObject): String? = map.keys().asSequence().minByOrNull { map.getJSONObject(it).optLong("n") }

    /**
     * The fetched copies to remove so [size] more bytes fit in [BUDGET] and [COUNT], oldest first; null when even
     * removing all of them isn't enough. [first] are other cached downloads to remove before attachments, such as
     * files opened in the file explorer. Only finished downloads that no queued request needs are removed.
     */
    fun evictions(state: JSONObject, size: Long, first: List<String> = emptyList()): List<String>? {
        val files = state.optJSONObject("files") ?: JSONObject()
        val active = files.keys().asSequence().map { files.getJSONObject(it) }.filter { it.getString("state") !in setOf("failed", "cancelled") }.toList()
        var bytes = active.sumOf { it.getLong("size") } + size
        var count = active.size + 1
        if (bytes <= BUDGET && count <= COUNT) return emptyList()
        val map = state.optJSONObject(STATE) ?: JSONObject()
        val attachments = map.keys().asSequence().sortedBy { map.getJSONObject(it).optLong("n") }
            .map { map.getJSONObject(it).getString("file") }
        val removable = (first.asSequence() + attachments).distinct()
            .filter { id -> files.optJSONObject(id)?.let { it.getString("direction") == "download" && it.getString("state") == "available" } == true }
            .filter { !LiveJournal.referencesFile(state, it) }.toList()
        val chosen = mutableListOf<String>()
        for (id in removable) {
            if (bytes <= BUDGET && count <= COUNT) break
            chosen += id; bytes -= files.getJSONObject(id).getLong("size"); count--
        }
        return chosen.takeIf { bytes <= BUDGET && count <= COUNT }
    }

    /** Forgets the copies in [removed], which the phone no longer has. */
    fun forget(state: JSONObject, removed: Collection<String>) {
        val map = state.optJSONObject(STATE) ?: return
        for (key in map.keys().asSequence().toList()) if (map.getJSONObject(key).optString("file") in removed) map.remove(key)
    }

    /**
     * Drops queued fetches before connecting: the desktop may be older and not know them, and a fetch is asked again
     * when its file is shown. Returns whether any were dropped.
     */
    fun dropQueued(state: JSONObject): Boolean {
        val outbox = state.optJSONArray("outbox") ?: return false
        val kept = JSONArray()
        for (i in 0 until outbox.length()) {
            val event = JSONObject(outbox.getString(i))
            if (event.optString("type") != "desktop.action" || event.optJSONObject("payload")?.optString("op") != OP) kept.put(outbox.getString(i))
        }
        if (kept.length() == outbox.length()) return false
        state.put("outbox", kept)
        return true
    }

    /** Why a fetch didn't give a file, from the desktop's answer. */
    fun failure(code: String, detail: String): Fetch = when (code) {
        "not_found" -> Fetch.Gone
        else -> Fetch.Refused(detail.ifBlank { "Your computer couldn't send this file." })
    }

    /** A fetch that hasn't produced a download (yet). */
    sealed interface Fetch {
        data object Asking : Fetch
        /** The desktop no longer has it. */
        data object Gone : Fetch
        /** There's no room for it on the phone, even after removing older fetched copies. */
        data object NoRoom : Fetch
        data class Refused(val detail: String) : Fetch
    }

    /** What a tile for an attachment this phone doesn't hold says, and whether a tap can fetch it. */
    data class Remote(val meta: String, val note: String, val fetchable: Boolean)

    fun remote(name: String, size: Long, fetch: Fetch?, supported: Boolean, connected: Boolean): Remote = when {
        fetch == Fetch.Gone -> Remote("Not on your computer", "$name is no longer on your computer, so it can't be shown here.", false)
        fetch is Fetch.Refused -> Remote("Couldn't download", fetch.detail, connected && supported)
        fetch == Fetch.NoRoom -> Remote("Phone storage full", "Raticode's file space on this phone is full. Remove downloaded or " +
            "sent files you no longer need, then tap $name again.", connected && supported)
        size > FileTransfers.MAX_FILE -> Remote("Open on your computer", "$name is larger than the 10 MB the phone can hold. Open it on your computer.", false)
        !supported -> Remote("Not on this phone", "This phone doesn't have a copy of $name. Update Raticode on your computer to " +
            "get files from Rem's chat on the phone, or open it on your computer.", false)
        !connected -> Remote("Not connected", "$name is on your computer. Connect to it to download the file here.", false)
        fetch == Fetch.Asking -> Remote("Downloading…", "", false)
        else -> Remote("Tap to download", "", true)
    }
}
