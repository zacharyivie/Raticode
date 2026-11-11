package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class LiveHistoryTest {
    @Test fun refreshedMetadataReplacesOldPlainBubblesAndRemovedSummaryWithoutLosingPendingWork() {
        val local = JSONArray("""[{"id":"thought","thread":"thread","text":"bash","state":"Rem","history_index":0},
          {"id":"empty","thread":"thread","text":"","state":"Rem","history_index":1},
          {"id":"pending","thread":"thread","text":"New turn","state":"Queued on phone","history_index":99}]""")
        val page = JSONObject("""{"before":0,"total":1,"messages":[{"id":"thought","role":"assistant","text":"bash",
          "presentation":{"kind":"thought","groupId":"turn","trace":{"id":"tool","kind":"tool","title":"bash"}}}]}""")
        val merged = LiveHistory.merge(local, "thread", page)
        assertEquals(2, merged.length())
        assertEquals("thought", merged.getJSONObject(0).getJSONObject("presentation").getString("kind"))
        assertEquals("Queued on phone", merged.getJSONObject(1).getString("state"))
        assertEquals(merged.toString(), LiveHistory.merge(merged, "thread", page).toString())
    }

    @Test fun queuedAttachmentMetadataPersistsAfterRemovingLocalFileAndPreservesReceiptOnReplay() {
        val state = JSONObject("""{"files":{"file":{"name":"README.md","size":42,"mime":"text/markdown"}}}""")
        val (queued, event) = LiveJournal.prepare(state, "chat.submit", "thread", JSONObject("""{"text":"","attachment_ids":["file"]}"""))
        queued.remove("files")
        val message = queued.getJSONArray("messages").getJSONObject(0)
        assertEquals("README.md", message.getJSONArray("attachments").getJSONObject(0).getString("name"))
        val page = JSONObject().put("before", 0).put("messages", JSONArray().put(JSONObject().put("id", event.getString("request_id")).put("role", "user").put("text", "")))
        val merged = LiveHistory.merge(queued.getJSONArray("messages"), "thread", page).getJSONObject(0)
        assertEquals("Queued on phone", merged.getString("state"))
        assertEquals(42, merged.getJSONArray("attachments").getJSONObject(0).getInt("size"))
    }
}
