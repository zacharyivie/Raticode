package dev.raticode.mobile

import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.*
import dev.raticode.mobile.data.LocalVault
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.util.Base64
import java.util.UUID

class LiveStorageTest {
    private val context get() = InstrumentationRegistry.getInstrumentation().targetContext
    private fun id() = UUID.randomUUID().toString()
    @Test fun protectedChunksSurviveRestartAndAuthenticateWholeFileBeforeExport() {
        var store = DeviceStore(context); store.reset()
        var transfers = FileTransfers(store)
        var state = store.load()
        val file = id(); val thread = id(); val bytes = ByteArray(20000) { (it % 251).toByte() }
        fun event(type: String, payload: JSONObject) = JSONObject().put("type", type).put("thread_id", thread).put("payload", payload)
        transfers.receive(state, event("file.offer", JSONObject().put("file_id", file).put("name", "fixture.bin").put("mime", "application/octet-stream")
            .put("size", bytes.size).put("sha256", LocalVault.digest(bytes)).put("expires_at", System.currentTimeMillis()/1000+600)))
        fun chunk(offset: Int, end: Int) = event("file.chunk", JSONObject().put("file_id", file).put("offset", offset)
            .put("data", Base64.getUrlEncoder().withoutPadding().encodeToString(bytes.copyOfRange(offset, end))).put("eof", end == bytes.size))
        transfers.receive(state, chunk(0, 16384)); store.save(state)
        assertThrows(Exception::class.java) { transfers.export(state, file) }
        store.close(); store = DeviceStore(context); state = store.load(); transfers = FileTransfers(store)
        transfers.receive(state, chunk(0, 16384)) // same-offset replay is idempotent
        val wrong = chunk(0, 16384); wrong.getJSONObject("payload").put("data", Base64.getUrlEncoder().withoutPadding().encodeToString(ByteArray(16384)))
        assertThrows(Exception::class.java) { transfers.receive(state, wrong) }
        transfers.receive(state, chunk(16384, bytes.size)); store.save(state)
        assertArrayEquals(bytes, transfers.export(state, file))
        store.reset()
    }
    @Test fun corruptedFinalHashNeverBecomesAvailable() {
        val store = DeviceStore(context); store.reset(); val transfers = FileTransfers(store); val state = store.load()
        val file = id(); val thread = id()
        transfers.receive(state, JSONObject().put("type", "file.offer").put("thread_id", thread).put("payload", JSONObject()
            .put("file_id", file).put("name", "bad.bin").put("mime", "application/octet-stream").put("size", 1)
            .put("sha256", "0".repeat(64)).put("expires_at", System.currentTimeMillis()/1000+600)))
        assertThrows(Exception::class.java) { transfers.receive(state, JSONObject().put("type", "file.chunk").put("thread_id", thread).put("payload", JSONObject()
            .put("file_id", file).put("offset", 0).put("data", "AQ").put("eof", true))) }
        assertThrows(Exception::class.java) { transfers.export(state, file) }
        store.reset()
    }
    @Test fun replacementStoreRejectsOldOwnerAndMissingKeyCannotReplaceTrust() {
        val old = DeviceStore(context); old.reset(); val state = old.load().put("peer", JSONObject().put("test", true)); old.save(state)
        val replacement = DeviceStore(context)
        assertThrows(Exception::class.java) { old.save(JSONObject()) }
        val keyStore = java.security.KeyStore.getInstance("AndroidKeyStore").apply { load(null); deleteEntry("raticode.device.data.v2") }
        assertThrows(Exception::class.java) { replacement.load() }
        assertFalse(keyStore.containsAlias("raticode.device.data.v2"))
        replacement.reset()
    }
    @Test fun removalCommitsBeforeDeletingAndOldOffersCannotResurrectFiles() {
        val store = DeviceStore(context); store.reset()
        val transfers = FileTransfers(store); val state = store.load()
        val thread = id(); val bytes = byteArrayOf(1, 2, 3)
        val offer = transfers.offer(state, thread, "keep.bin", bytes)
        // The desktop refuses an offer ending more than a day after its clock, so the phone leaves room for clock drift.
        val now = System.currentTimeMillis() / 1000
        assertTrue(offer.getLong("expires_at") in (now + 86400 - 660)..(now + 86400 - 540))
        val file = offer.getString("file_id")
        state.getJSONObject("files").getJSONObject(file).put("state", "available").put("offset", bytes.size)
        store.save(state)
        val next = JSONObject(state.toString())
        transfers.remove(next, file)
        assertArrayEquals(bytes, store.readBlob(file))
        assertTrue(store.load().getJSONObject("files").has(file))
        store.save(next); transfers.cleanup(next)
        assertThrows(Exception::class.java) { store.readBlob(file) }
        val reloaded = store.load()
        transfers.receive(reloaded, JSONObject().put("type", "file.offer").put("thread_id", thread).put("payload", offer))
        assertFalse(reloaded.getJSONObject("files").has(file))
        assertTrue(reloaded.getJSONObject("expired_files").has(file))
        store.reset()
    }

    @Test fun expiryCannotDeleteDurableBlobsBeforeMetadataCommitOrQueuedAttachmentReceipt() {
        val store = DeviceStore(context); store.reset(); val transfers = FileTransfers(store); val state = store.load()
        val thread = id(); val bytes = byteArrayOf(4, 5, 6)
        val offer = transfers.offer(state, thread, "pending.bin", bytes); val file = offer.getString("file_id")
        state.getJSONObject("files").getJSONObject(file).put("expires_at", 1).put("state", "available")
        state.put("outbox", org.json.JSONArray().put(JSONObject().put("request_id", id())
            .put("payload", JSONObject().put("attachment_ids", org.json.JSONArray().put(file))).toString()))
        store.save(state)
        val next = JSONObject(state.toString())
        transfers.expire(next)
        assertTrue(next.getJSONObject("files").has(file))
        assertThrows(LiveStorageLimit::class.java) { transfers.remove(next, file) }
        next.put("outbox", org.json.JSONArray())
        transfers.expire(next)
        assertFalse(next.getJSONObject("files").has(file))
        assertArrayEquals(bytes, store.readBlob(file))
        assertTrue(store.load().getJSONObject("files").has(file))
        store.save(next); transfers.cleanup(next)
        assertThrows(Exception::class.java) { store.readBlob(file) }
        store.reset()
    }

    @Test fun explicitRemovalRestoresFileQuotaAndKeepsUnrelatedFiles() {
        val store = DeviceStore(context); store.reset(); val transfers = FileTransfers(store); val state = store.load()
        val files = JSONObject()
        val completed = id(); val thread = id()
        repeat(100) { index -> files.put(if (index == 0) completed else id(), JSONObject().put("state", "available").put("size", 0)) }
        state.put("files", files)
        val unrelated = id(); store.writeBlob(unrelated, byteArrayOf(7))
        val failure = assertThrows(LiveStorageLimit::class.java) { transfers.offer(state, thread, "new.bin", byteArrayOf()) }
        assertTrue(failure.message!!.contains("100-file"))
        transfers.remove(state, completed); store.save(state); transfers.cleanup(state)
        transfers.offer(state, thread, "new.bin", byteArrayOf())
        assertEquals(100, state.getJSONObject("files").length())
        assertArrayEquals(byteArrayOf(7), store.readBlob(unrelated))
        store.reset()
    }

    @Test fun interruptedImportReclaimsOnlyJournaledReservationsAndPreservesCommittedFiles() {
        var store = DeviceStore(context); store.reset()
        var transfers = FileTransfers(store); var state = store.load()
        val abandoned = id(); val committed = id(); val unrelated = id()
        transfers.reserveImport(state, abandoned)
        transfers.reserveImport(state, committed)
        store.save(state) // Crash recovery can identify bytes even before the import's final commit.
        store.writeBlob(abandoned, byteArrayOf(1))
        store.writeBlob(unrelated, byteArrayOf(2))
        transfers.offer(state, id(), "committed.bin", byteArrayOf(3), committed)
        store.save(state) // A lingering reservation must never authorize deletion of a committed file.
        store.close()
        store = DeviceStore(context); transfers = FileTransfers(store); state = store.load()
        val next = JSONObject(state.toString())
        assertTrue(transfers.recoverImports(next))
        assertThrows(Exception::class.java) { store.readBlob(abandoned) }
        assertArrayEquals(byteArrayOf(2), store.readBlob(unrelated))
        assertArrayEquals(byteArrayOf(3), store.readBlob(committed))
        // Cleanup interruption before save remains safely repeatable.
        assertTrue(store.load().has("pending_imports"))
        assertTrue(transfers.recoverImports(store.load()))
        store.save(next)
        assertFalse(store.load().has("pending_imports"))
        assertFalse(transfers.recoverImports(next))
        store.reset(); store.close()
    }

}
