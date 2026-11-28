package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject

/** The journal's queue of unsent events in `state["outbox"]`: each entry is one event's JSON text, resent in order on reconnect. */
internal object Outbox {
    /**
     * Removes the queued events [drop] matches, so a reconnect doesn't resend ones an older desktop would refuse. The
     * rest keep their order and original text. Returns true when anything was removed; otherwise [state] is untouched.
     */
    fun drop(state: JSONObject, drop: (event: JSONObject) -> Boolean): Boolean {
        val outbox = state.optJSONArray("outbox") ?: return false
        val kept = JSONArray()
        for (i in 0 until outbox.length()) {
            val text = outbox.getString(i)
            if (!drop(JSONObject(text))) kept.put(text)
        }
        if (kept.length() == outbox.length()) return false
        state.put("outbox", kept)
        return true
    }
}
