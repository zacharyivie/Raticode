package dev.raticode.mobile.live

import dev.raticode.mobile.data.FileMetadata
import dev.raticode.mobile.protocol.*
import org.json.JSONObject
import java.util.Base64
import java.util.UUID

/** Metadata and offsets commit with the inbox cursor; chunks are encrypted before that commit. */
class FileTransfers(private val store: DeviceStore) {
    fun offer(state: JSONObject, thread: String, name: String, bytes: ByteArray, id: String = UUID.randomUUID().toString()): JSONObject {
        if (bytes.size > MAX_FILE) throw LiveStorageLimit("Files must be 10 MiB or smaller.")
        val files = state.optJSONObject("files") ?: JSONObject()
        quota(files, bytes.size.toLong())
        require(UUID.fromString(id).toString() == id && !files.has(id))
        val offer = JSONObject().put("file_id", id).put("name", FileMetadata.safeName(name)).put("mime", "application/octet-stream")
            .put("size", bytes.size).put("sha256", FileMetadata.digest(bytes)).put("expires_at", System.currentTimeMillis()/1000 + UPLOAD_SECONDS)
        store.writeBlob(id, bytes)
        files.put(id, JSONObject(offer.toString()).put("thread", thread).put("direction", "upload").put("state", "offered").put("offset", 0))
        state.put("files", files)
        return offer
    }
    /** Persist this reservation before writing imported bytes. It does not offer a file to the peer. */
    fun reserveImport(state: JSONObject, id: String) {
        require(UUID.fromString(id).toString() == id)
        check(state.optJSONObject("files")?.has(id) != true)
        val reservations = state.optJSONObject("pending_imports") ?: JSONObject()
        if (reservations.length() >= 128) throw LiveStorageLimit("Unfinished file imports need cleanup. Free device storage and restart live sync before importing more.")
        check(!reservations.has(id))
        reservations.put(id, true); state.put("pending_imports", reservations)
    }
    fun finishImport(state: JSONObject, id: String) {
        state.optJSONObject("pending_imports")?.remove(id)
        if (state.optJSONObject("pending_imports")?.length() == 0) state.remove("pending_imports")
    }
    /** Only committed reservations can reclaim uncommitted blobs after interruption. */
    fun recoverImports(state: JSONObject): Boolean {
        val reservations = state.optJSONObject("pending_imports") ?: return false
        require(reservations.length() <= 128)
        val files = state.optJSONObject("files")
        val ids = reservations.keys().asSequence().toSet()
        ids.forEach { require(UUID.fromString(it).toString() == it) }
        // If a file is committed, preserve it even if an old reservation survived.
        store.cleanupTransfers(ids.filter { files?.has(it) != true }.toSet(), emptySet())
        state.remove("pending_imports")
        return true
    }
    private fun quota(files: JSONObject, size: Long) {
        val active = files.keys().asSequence().filter { files.getJSONObject(it).getString("state") !in setOf("failed", "cancelled") }.toList()
        if (active.size >= 100) throw LiveStorageLimit("The 100-file limit is reached. Remove finished files before importing or downloading more.")
        if (active.sumOf { files.getJSONObject(it).getLong("size") } + size > 50L * 1024 * 1024)
            throw LiveStorageLimit("The 50 MiB file quota is full. Export files you want to keep, then remove finished files from this phone.")
    }
    fun receive(state: JSONObject, event: JSONObject): JSONObject? {
        val payload = event.getJSONObject("payload")
        val id = payload.uuid("file_id")
        val files = state.optJSONObject("files") ?: JSONObject().also { state.put("files", it) }
        val type = event.getString("type")
        if (state.optJSONObject("expired_files")?.has(id) == true) return null
        if (type == "file.offer") {
            payload.fields("file_id", "name", "mime", "size", "sha256", "expires_at")
            val size = payload.integer("size"); require(size in 0..MAX_FILE)
            require(payload.integer("expires_at") in (System.currentTimeMillis()/1000 + 1)..(System.currentTimeMillis()/1000 + 86400))
            require(payload.string("sha256", 64).matches(Regex("[a-f0-9]{64}")))
            require(payload.string("name", 160) == FileMetadata.safeName(payload.getString("name")))
            payload.string("mime", 120)
            val old = files.optJSONObject(id)
            if (old != null) { for (field in payload.keys()) require(old.get(field) == payload.get(field)); return null }
            quota(files, size)
            files.put(id, JSONObject(payload.toString()).put("thread", event.uuid("thread_id")).put("direction", "download").put("state", "offered").put("offset", 0))
            return null
        }
        val file = files.getJSONObject(id)
        require(file.getString("thread") == event.uuid("thread_id"))
        when (type) {
            "file.status" -> status(file, payload, System.currentTimeMillis()/1000)
            "file.chunk" -> {
                require(file.getLong("expires_at") > System.currentTimeMillis()/1000)
                payload.fields("file_id", "offset", "data", "eof")
                require(file.getString("direction") == "download" && file.getString("state") !in setOf("cancelled", "failed"))
                val offset = payload.integer("offset"); val encoded = payload.string("data", 21846)
                val bytes = Base64.getUrlDecoder().decode(encoded)
                try {
                    require(bytes.size <= CHUNK && Base64.getUrlEncoder().withoutPadding().encodeToString(bytes) == encoded)
                    val end = offset + bytes.size
                    require(end <= file.getLong("size"))
                    require(payload.get("eof") is Boolean && payload.getBoolean("eof") == (end == file.getLong("size")))
                    require(bytes.isNotEmpty() || file.getLong("size") == 0L)
                    if (offset < file.getLong("offset")) {
                        val old = if (file.getString("state") == "available") store.readBlob(id) else store.readBlob("$id-$offset")
                        try { require(if (file.getString("state") == "available") old.copyOfRange(offset.toInt(), end.toInt()).contentEquals(bytes) else old.contentEquals(bytes)) } finally { old.fill(0) }
                        return null
                    }
                    require(offset == file.getLong("offset"))
                    store.writeBlob("$id-$offset", bytes)
                    file.put("offset", end).put("state", "receiving")
                    if (payload.getBoolean("eof")) {
                        val full = ByteArray(file.getInt("size"))
                        try {
                            var at = 0
                            while (at < full.size) {
                                val chunk = store.readBlob("$id-$at")
                                try { require(chunk.isNotEmpty() && at + chunk.size <= full.size); chunk.copyInto(full, at); at += chunk.size } finally { chunk.fill(0) }
                            }
                            require(FileMetadata.digest(full) == file.getString("sha256"))
                            store.writeBlob(id, full); file.put("state", "available")
                        } finally { full.fill(0) }
                    }
                } finally { bytes.fill(0) }
            }
        }
        return null
    }
    fun expire(state: JSONObject) {
        val files = state.optJSONObject("files") ?: return
        for (id in files.keys().asSequence().toList()) {
            val file = files.getJSONObject(id)
            if (!LiveJournal.referencesFile(state, id) && file.getLong("expires_at") <= System.currentTimeMillis()/1000 &&
                !(file.getString("direction") == "download" && file.getString("state") == "available")) {
                val tombstones = state.optJSONObject("expired_files") ?: JSONObject().also { state.put("expired_files", it) }
                rememberRemoved(tombstones, id)
                files.remove(id)
            }
        }
    }
    /** Commit tombstones first. Blob deletion may then be retried safely after a crash. */
    fun remove(state: JSONObject, id: String) {
        require(UUID.fromString(id).toString() == id)
        val files = state.optJSONObject("files") ?: return
        val file = files.optJSONObject(id) ?: return
        check(file.getString("state") in setOf("available", "failed", "cancelled")) { "Cancel this transfer before removing it." }
        if (LiveJournal.referencesFile(state, id)) throw LiveStorageLimit("This file is used by a queued request. Reconnect and wait for its receipt before removing it.")
        val tombstones = state.optJSONObject("expired_files") ?: JSONObject().also { state.put("expired_files", it) }
        rememberRemoved(tombstones, id)
        files.remove(id)
    }
    private fun rememberRemoved(tombstones: JSONObject, id: String) {
        if (!tombstones.has(id) && tombstones.length() >= LiveJournal.MAX_TOMBSTONES)
            throw LiveStorageLimit("This pairing has reached its file safety limit. Recover pending work on desktop before revoking and pairing again.")
        tombstones.put(id, true)
    }
    fun cleanup(state: JSONObject) {
        // Only committed tombstones and terminal records authorize deletion.
        val removed = state.optJSONObject("expired_files")?.keys()?.asSequence()?.toMutableSet() ?: mutableSetOf()
        val completed = mutableSetOf<String>()
        val files = state.optJSONObject("files")
        sending?.first?.let { id -> if (files?.optJSONObject(id)?.optString("state") !in setOf("offered", "receiving")) release() }
        files?.keys()?.forEach { id ->
            when (files.getJSONObject(id).getString("state")) {
                "available" -> completed.add(id)
                "cancelled", "failed" -> removed.add(id)
            }
        }
        store.cleanupTransfers(removed, completed)
    }
    /** One chunk of an upload to queue; [end] becomes the file's `sent` once it is in the outbox. */
    data class Chunk(val id: String, val thread: String, val payload: JSONObject, val end: Long)
    /**
     * The next upload chunk, chosen by [UploadQueue.next]. It doesn't change [state]. The upload's bytes are decrypted
     * and checked once and kept in memory while it sends, instead of once per 16 KiB chunk: the Keystore decrypting a
     * whole 7 MB file for every chunk made each one take over a second on a phone. An upload whose stored copy can't
     * be read or no longer matches is marked failed in [state], so it can't hold up the others.
     */
    fun nextUpload(state: JSONObject): Chunk? {
        val files = state.optJSONObject("files") ?: return null
        while (true) {
            val id = UploadQueue.next(files, System.currentTimeMillis()/1000) ?: return null
            val file = files.getJSONObject(id)
            val bytes = try { source(id, file) } catch (failure: IllegalStateException) { throw failure }
                catch (_: Exception) { file.put("state", "failed"); continue }
            val offset = UploadQueue.sent(file).toInt(); val end = minOf(bytes.size, offset + CHUNK)
            return Chunk(id, file.getString("thread"), JSONObject().put("file_id", id).put("offset", offset)
                .put("data", Base64.getUrlEncoder().withoutPadding().encodeToString(bytes.copyOfRange(offset, end))).put("eof", end == bytes.size), end.toLong())
        }
    }
    // The verified bytes of the upload being sent. Only the device-sync thread uses them.
    private var sending: Pair<String, ByteArray>? = null
    private fun source(id: String, file: JSONObject): ByteArray {
        sending?.let { (held, bytes) -> if (held == id) return bytes }
        release()
        val bytes = store.readBlob(id)
        if (bytes.size != file.getInt("size") || FileMetadata.digest(bytes) != file.getString("sha256")) { bytes.fill(0); throw IllegalArgumentException("Upload changed on this phone") }
        sending = id to bytes
        return bytes
    }
    /** Zeroes the bytes kept for an upload: [id]'s, or any when null. */
    fun release(id: String? = null) {
        sending?.takeIf { id == null || it.first == id }?.let { it.second.fill(0); sending = null }
    }
    /**
     * A stored file's verified bytes: a finished download, or a file this phone is sending or has sent and still
     * holds. Previews and Open use it, so a file the user attached from this phone can be shown again.
     */
    fun read(state: JSONObject, id: String): ByteArray {
        val file = state.getJSONObject("files").getJSONObject(id)
        require(holds(file.getString("direction"), file.getString("state")))
        sending?.let { (held, bytes) -> if (held == id) return bytes.copyOf() }
        return store.readBlob(id).also { require(it.size == file.getInt("size") && FileMetadata.digest(it) == file.getString("sha256")) }
    }
    fun export(state: JSONObject, id: String): ByteArray {
        val file = state.getJSONObject("files").getJSONObject(id)
        require(file.getString("direction") == "download" && file.getString("state") == "available")
        return store.readBlob(id).also { require(it.size == file.getInt("size") && FileMetadata.digest(it) == file.getString("sha256")) }
    }
    companion object {
        /** Applies a desktop `file.status` to [file]. */
        fun status(file: JSONObject, payload: JSONObject, nowSeconds: Long) {
            payload.fields("file_id", "state", "received_size")
            val state = payload.getString("state")
            require(state in setOf("receiving", "available", "cancelled", "failed"))
            // A transfer cancelled on this phone stays cancelled: answers to chunks sent before it change nothing.
            if (file.getString("state") in setOf("cancelled", "failed")) return
            // The desktop reports a cancelled transfer as 0 bytes received; keep the count this phone had, and keep a
            // finished file.
            if (state in setOf("cancelled", "failed")) { if (file.getString("state") != "available") file.put("state", state); return }
            require(file.getLong("expires_at") > nowSeconds)
            val offset = payload.integer("received_size")
            require(offset in file.getLong("offset")..file.getLong("size"))
            if (state == "available") require(offset == file.getLong("size"))
            file.put("offset", offset).put("state", state)
        }
        /** Whether this phone has a file's bytes: downloads once finished, uploads until they fail, are cancelled or expire. */
        fun holds(direction: String, state: String) =
            if (direction == "download") state == "available" else state in setOf("offered", "receiving", "available")
        const val MAX_FILE = 10L * 1024 * 1024; const val CHUNK = 16384
        /**
         * How long an upload offer lasts. The desktop refuses one that ends more than a day after its own clock, so
         * a full day failed whenever the phone's clock ran even a fraction of a second ahead. Ten minutes short of a
         * day leaves room for that.
         */
        const val UPLOAD_SECONDS = 86400L - 600
    }
}
