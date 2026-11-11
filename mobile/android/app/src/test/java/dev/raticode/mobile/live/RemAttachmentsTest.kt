package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class RemAttachmentsTest {
    private val mb = 1024L * 1024
    private fun file(size: Long, state: String = "available", direction: String = "download") =
        JSONObject().put("size", size).put("state", state).put("direction", direction).put("thread", "rem-files")
    private fun action(op: String, request: String, params: JSONObject = JSONObject()) = JSONObject().put("type", "desktop.action")
        .put("request_id", request).put("payload", JSONObject().put("op", op).put("params", params)).toString()

    @Test fun aFetchedCopyIsRememberedNewestLastAndStaleEntriesGo() {
        val state = JSONObject().put("files", JSONObject().put("f1", file(10)).put("f2", file(10)))
        RemAttachments.remember(state, "771f264b", "f1")
        RemAttachments.remember(state, "phone-upload", "f2")
        assertEquals(mapOf("771f264b" to "f1", "phone-upload" to "f2"), RemAttachments.files(state))
        assertEquals("f1", RemAttachments.fileFor(state, "771f264b"))
        // A download the phone no longer has is forgotten the next time a copy is stored.
        state.getJSONObject("files").remove("f1")
        state.getJSONObject("files").put("f3", file(10))
        RemAttachments.remember(state, "third", "f3")
        assertNull(RemAttachments.fileFor(state, "771f264b"))
        assertEquals(setOf("phone-upload", "third"), RemAttachments.files(state).keys)
        RemAttachments.forget(state, listOf("f2"))
        assertEquals(setOf("third"), RemAttachments.files(state).keys)
    }

    @Test fun roomIsMadeByRemovingTheOldestFetchedCopiesOnly() {
        val files = JSONObject().put("old", file(15 * mb)).put("newer", file(15 * mb))
            .put("upload", file(8 * mb, direction = "upload")).put("cancelled", file(9 * mb, state = "cancelled"))
        val state = JSONObject().put("files", files)
        RemAttachments.remember(state, "a", "old")
        RemAttachments.remember(state, "b", "newer")
        // 38 MB held; another 1 MB fits in the 40 MB budget, so nothing goes.
        assertEquals(emptyList<String>(), RemAttachments.evictions(state, 1 * mb))
        // 5 MB more needs the oldest copy removed, never the upload or the newer copy.
        assertEquals(listOf("old"), RemAttachments.evictions(state, 5 * mb))
        assertEquals(listOf("old", "newer"), RemAttachments.evictions(state, 30 * mb))
        // Even removing every copy leaves too little room beside the upload.
        assertNull(RemAttachments.evictions(state, 33 * mb))
        // A copy a queued message still needs stays.
        state.put("outbox", JSONArray().put(JSONObject().put("type", "file.request").put("payload", JSONObject().put("file_id", "old")).toString()))
        assertEquals(listOf("newer"), RemAttachments.evictions(state, 5 * mb))
    }

    @Test fun roomCountsFilesToo() {
        val files = JSONObject()
        repeat(RemAttachments.COUNT) { files.put("f$it", file(1)) }
        val state = JSONObject().put("files", files)
        assertNull(RemAttachments.evictions(state, 1))
        RemAttachments.remember(state, "a", "f0")
        assertEquals(listOf("f0"), RemAttachments.evictions(state, 1))
    }

    @Test fun queuedFetchesAreDroppedBeforeConnectingAndNothingElse() {
        val state = JSONObject().put("outbox", JSONArray().put(action("rem.attachment", "r1")).put(action("rem.message", "r2"))
            .put(JSONObject().put("type", "desktop.read").put("payload", JSONObject().put("op", "rem.attachment")).toString()))
        assertTrue(RemAttachments.dropQueued(state))
        val left = state.getJSONArray("outbox")
        assertEquals(2, left.length())
        assertEquals("r2", JSONObject(left.getString(0)).getString("request_id"))
        assertFalse(RemAttachments.dropQueued(state))
    }

    @Test fun aFileOnlyOnTheComputerSaysWhatCanBeDone() {
        val ready = RemAttachments.remote("image.png", 82946, null, supported = true, connected = true)
        assertEquals("Tap to download", ready.meta); assertTrue(ready.fetchable)
        assertEquals("Downloading…", RemAttachments.remote("image.png", 1, RemAttachments.Fetch.Asking, true, true).meta)
        val offline = RemAttachments.remote("image.png", 1, null, supported = true, connected = false)
        assertEquals("Not connected", offline.meta); assertFalse(offline.fetchable); assertTrue(offline.note.contains("Connect"))
        val old = RemAttachments.remote("image.png", 1, null, supported = false, connected = true)
        assertEquals("Not on this phone", old.meta); assertTrue(old.note.contains("Update Raticode"))
        val gone = RemAttachments.remote("image.png", 1, RemAttachments.failure("not_found", "x"), true, true)
        assertEquals("Not on your computer", gone.meta); assertFalse(gone.fetchable)
        val big = RemAttachments.remote("clip.mp4", FileTransfers.MAX_FILE + 1, null, true, true)
        assertEquals("Open on your computer", big.meta); assertFalse(big.fetchable)
        val refused = RemAttachments.remote("a.png", 1, RemAttachments.failure("unavailable", "The desktop's space is full."), true, true)
        assertEquals("Couldn't download", refused.meta); assertEquals("The desktop's space is full.", refused.note); assertTrue(refused.fetchable)
        val full = RemAttachments.remote("a.png", 1, RemAttachments.Fetch.NoRoom, true, true)
        assertEquals("Phone storage full", full.meta); assertTrue(full.fetchable)
    }

    @Test fun fetchesHaveTheirOwnRequestKeyAndOp() {
        assertEquals("rem.attachment:771f264b", DesktopViews.key("rem.attachment", JSONObject().put("file_id", "771f264b").put("message_id", "m")))
        assertTrue("rem.attachment" in DesktopViews.ACTIONS)
        val view = DesktopViews.parse("overview", JSONObject().put("attachment_files", true)) as DesktopOverview
        assertTrue(view.attachmentFiles)
        assertFalse((DesktopViews.parse("overview", JSONObject()) as DesktopOverview).attachmentFiles)
    }
}
