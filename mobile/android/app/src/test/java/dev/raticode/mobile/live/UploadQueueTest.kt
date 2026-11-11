package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class UploadQueueTest {
    private val now = 1_000_000L
    private fun upload(size: Long, state: String = "receiving", offset: Long = 0, sent: Long? = null, expires: Long = now + 3600,
                       thread: String = "rem-files", name: String = "photo.png") =
        JSONObject().put("direction", "upload").put("state", state).put("size", size).put("offset", offset).put("expires_at", expires)
            .put("thread", thread).put("name", name).apply { sent?.let { put("sent", it) } }
    private fun chunk(request: String, file: String) =
        JSONObject().put("type", "file.chunk").put("request_id", request).put("payload", JSONObject().put("file_id", file)).toString()

    @Test fun theUploadWithLeastLeftGoesFirstSoAPictureIsNotStuckBehindAVideo() {
        // The incident: a 7 MB file offered first, then a 694 KB picture. Both are uploading.
        val files = JSONObject().put("video", upload(7_143_985, offset = 4_096_000, sent = 4_161_536)).put("picture", upload(694_411))
        assertEquals("picture", UploadQueue.next(files, now))
        // Once the picture's chunks are all queued, the video carries on from where it was.
        files.getJSONObject("picture").put("sent", 694_411)
        assertEquals("video", UploadQueue.next(files, now))
        assertEquals(4_161_536, UploadQueue.sent(files.getJSONObject("video")))
    }

    @Test fun onlyUploadsTheDesktopAcceptedAndThatCanStillFinishAreSent() {
        val files = JSONObject().put("offered", upload(10, state = "offered")).put("cancelled", upload(10, state = "cancelled"))
            .put("failed", upload(10, state = "failed")).put("done", upload(10, state = "available"))
            .put("expired", upload(10, expires = now)).put("download", upload(10).put("direction", "download"))
        assertNull(UploadQueue.next(files, now))
    }

    @Test fun anEmptyFileSendsItsOneChunkOnce() {
        val files = JSONObject().put("empty", upload(0))
        assertEquals("empty", UploadQueue.next(files, now))
        files.getJSONObject("empty").put("sent", 0)
        assertNull(UploadQueue.next(files, now))
    }

    @Test fun whereTheNextChunkStartsNeverGoesBackBeforeWhatTheDesktopConfirmed() {
        // An outbox chunk from an older build has no `sent`; the confirmed offset still counts.
        assertEquals(32768, UploadQueue.sent(upload(100_000, offset = 32768)))
        assertEquals(65536, UploadQueue.sent(upload(100_000, offset = 32768, sent = 65536)))
    }

    @Test fun theWindowCountsOnlyQueuedChunks() {
        val state = JSONObject().put("outbox", JSONArray().put(chunk("a", "f")).put(chunk("b", "f"))
            .put(JSONObject().put("type", "desktop.read").put("request_id", "c").put("payload", JSONObject()).toString()))
        assertEquals(2, UploadQueue.inFlight(state))
        assertEquals(0, UploadQueue.inFlight(JSONObject()))
        assertTrue(UploadQueue.WINDOW in 2..8)
    }

    @Test fun aRefusedChunkFailsItsUploadInsteadOfLeavingItWaiting() {
        val state = JSONObject().put("files", JSONObject().put("f", upload(100)).put("g", upload(100)).put("done", upload(100, state = "available")))
        val pending = JSONArray().put(chunk("r1", "f")).put(chunk("r2", "done"))
            .put(JSONObject().put("type", "desktop.read").put("request_id", "r3").put("payload", JSONObject()).toString())
        assertNull(UploadQueue.failRefused(state, pending, "r3"))
        assertNull(UploadQueue.failRefused(state, pending, "unknown"))
        assertNull(UploadQueue.failRefused(state, pending, "r2"))
        assertEquals("f", UploadQueue.failRefused(state, pending, "r1"))
        assertEquals("failed", state.getJSONObject("files").getJSONObject("f").getString("state"))
        assertEquals("receiving", state.getJSONObject("files").getJSONObject("g").getString("state"))
        assertEquals("available", state.getJSONObject("files").getJSONObject("done").getString("state"))
    }

    @Test fun cancellingStopsOnlyAnUnfinishedUpload() {
        val state = JSONObject().put("files", JSONObject().put("up", upload(10)).put("new", upload(10, state = "offered"))
            .put("done", upload(10, state = "available")).put("down", upload(10).put("direction", "download")))
        assertTrue(UploadQueue.cancel(state, "up"))
        assertTrue(UploadQueue.cancel(state, "new"))
        assertFalse(UploadQueue.cancel(state, "done"))
        assertFalse(UploadQueue.cancel(state, "down"))
        assertFalse(UploadQueue.cancel(state, "missing"))
        assertNull(UploadQueue.next(state.getJSONObject("files"), now))
    }

    @Test fun progressIsTheShareTheDesktopConfirmed() {
        assertEquals(0f, UploadQueue.progress("offered", 0, 694_411), 0f)
        assertEquals(0.5f, UploadQueue.progress("receiving", 347_205, 694_410), 0.0001f)
        assertEquals(1f, UploadQueue.progress("available", 0, 0), 0f)
        assertEquals(0f, UploadQueue.progress("receiving", 0, 0), 0f)
        assertEquals(1f, UploadQueue.progress("receiving", 900, 100), 0f)
    }

    @Test fun aDesktopCancelReportingNothingReceivedEndsTheTransferWithoutRewindingIt() {
        val status = { state: String, received: Long -> JSONObject().put("file_id", "f").put("state", state).put("received_size", received) }
        // Before, this failed validation, and the desktop resent it on every connection.
        val partial = upload(100_000, offset = 49152)
        FileTransfers.status(partial, status("cancelled", 0), now)
        assertEquals("cancelled", partial.getString("state"))
        assertEquals(49152, partial.getLong("offset"))
        // Answers to chunks sent before a cancel on this phone change nothing.
        FileTransfers.status(partial, status("receiving", 65536), now)
        assertEquals("cancelled", partial.getString("state"))
        assertEquals(49152, partial.getLong("offset"))
        // A finished file stays finished.
        val done = upload(10, state = "available", offset = 10)
        FileTransfers.status(done, status("cancelled", 0), now)
        assertEquals("available", done.getString("state"))
        // Ordinary progress still has to move forward.
        val going = upload(100_000, offset = 16384)
        FileTransfers.status(going, status("receiving", 32768), now)
        assertEquals(32768, going.getLong("offset"))
        assertThrows(IllegalArgumentException::class.java) { FileTransfers.status(going, status("receiving", 16384), now) }
        assertThrows(IllegalArgumentException::class.java) { FileTransfers.status(going, status("available", 50000), now) }
    }
}

class HeldSendsTest {
    private val now = 1_000_000L
    private fun file(state: String, name: String = "photo.png", thread: String = "rem-files", expires: Long = now + 60, direction: String = "upload") =
        JSONObject().put("state", state).put("name", name).put("thread", thread).put("expires_at", expires).put("direction", direction)

    @Test fun aMessageWaitsForItsFilesAndGoesWhenAllHaveArrived() {
        val files = JSONObject().put("a", file("available")).put("b", file("receiving"))
        assertEquals(HeldSends.Check.Wait, HeldSends.decide(files, listOf("a", "b"), "rem-files", now))
        files.put("b", file("available"))
        assertEquals(HeldSends.Check.Send, HeldSends.decide(files, listOf("a", "b"), "rem-files", now))
    }

    @Test fun aFileThatWontArriveSendsTheMessageBackWithItsName() {
        for (state in listOf("failed", "cancelled")) {
            val files = JSONObject().put("a", file("available")).put("b", file(state, "clip.mp4"))
            assertEquals(HeldSends.Check.Fail("clip.mp4"), HeldSends.decide(files, listOf("a", "b"), "rem-files", now))
        }
        assertEquals(HeldSends.Check.Fail("photo.png"), HeldSends.decide(JSONObject().put("a", file("receiving", expires = now)), listOf("a"), "rem-files", now))
        assertEquals(HeldSends.Check.Fail("A file"), HeldSends.decide(JSONObject(), listOf("gone"), "rem-files", now))
        // Only this phone's uploads on Rem's file channel can go with a Rem message.
        assertTrue(HeldSends.decide(JSONObject().put("a", file("available", thread = "other")), listOf("a"), "rem-files", now) is HeldSends.Check.Fail)
        assertTrue(HeldSends.decide(JSONObject().put("a", file("available", direction = "download")), listOf("a"), "rem-files", now) is HeldSends.Check.Fail)
        assertTrue(HeldSends.failure("clip.mp4").startsWith("clip.mp4 didn't finish uploading"))
    }

    @Test fun oneMessageWaitsAtATimeAndKeepsItsFilesFromBeingRemoved() {
        val state = JSONObject().put("files", JSONObject().put("a", file("receiving")))
        val params = JSONObject().put("body", "Here it is").put("refers_to", "m1")
        HeldSends.hold(state, "Here it is", params, listOf("a"), "rem-files", JSONObject().put("id", "m1"))
        assertThrows(IllegalStateException::class.java) { HeldSends.hold(state, "again", params, listOf("a"), "rem-files", null) }
        assertEquals(listOf("a"), HeldSends.ids(state.getJSONObject(HeldSends.KEY)))
        assertEquals(HeldSends.Check.Wait, HeldSends.decide(state, now))
        assertTrue(LiveJournal.referencesFile(state, "a"))
        assertFalse(LiveJournal.referencesFile(state, "b"))
        // The params wait without attachment_ids, which are added when it goes.
        assertFalse(state.getJSONObject(HeldSends.KEY).getJSONObject("params").has("attachment_ids"))
        assertNull(HeldSends.decide(JSONObject(), now))
    }
}
