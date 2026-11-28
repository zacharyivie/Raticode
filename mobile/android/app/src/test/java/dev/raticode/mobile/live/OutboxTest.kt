package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class OutboxTest {
    private fun event(seq: Int, type: String, op: String) =
        JSONObject().put("seq", seq).put("type", type).put("payload", JSONObject().put("op", op)).toString()
    private fun seqs(state: JSONObject): List<Int> {
        val outbox = state.getJSONArray("outbox")
        return (0 until outbox.length()).map { JSONObject(outbox.getString(it)).getInt("seq") }
    }

    @Test fun nothingMatchedReturnsFalseAndLeavesTheStateUntouched() {
        val outbox = JSONArray().put(event(1, "desktop.read", "a")).put(event(2, "rem.message", "b"))
        val state = JSONObject().put("outbox", outbox).put("next_seq", 3)
        val before = state.toString()
        assertFalse(Outbox.drop(state) { it.optString("type") == "sync.request" })
        assertEquals(before, state.toString())
        assertSame(outbox, state.getJSONArray("outbox"))
    }

    @Test fun aMissingOutboxReturnsFalse() {
        val state = JSONObject().put("next_seq", 1)
        assertFalse(Outbox.drop(state) { true })
        assertFalse(state.has("outbox"))
    }

    @Test fun theKeptEventsStayInOrderAsTheirOriginalText() {
        // Spacing that JSONObject would not write back shows the kept entries are the queued text itself.
        val spaced = """{ "seq": 4, "type": "rem.message", "payload": { "op": "x" } }"""
        val outbox = JSONArray().put(event(1, "desktop.read", "a")).put(event(2, "desktop.read", "drop"))
            .put(event(3, "desktop.action", "drop")).put(spaced).put(event(5, "desktop.read", "drop"))
        val state = JSONObject().put("outbox", outbox)
        // The filter sees the whole event: its type and a payload field.
        assertTrue(Outbox.drop(state) { it.optString("type") == "desktop.read" && it.getJSONObject("payload").optString("op") == "drop" })
        assertEquals(listOf(1, 3, 4), seqs(state))
        assertEquals(spaced, state.getJSONArray("outbox").getString(2))
    }

    @Test fun droppingEverythingLeavesAnEmptyOutbox() {
        val state = JSONObject().put("outbox", JSONArray().put(event(1, "desktop.read", "a")))
        assertTrue(Outbox.drop(state) { true })
        assertEquals(0, state.getJSONArray("outbox").length())
    }
}
