package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

/**
 * The phone's request numbers when a reply is applied while more requests are queued, using the same journal
 * steps as LiveClient: apply() copies the journal, may queue a request part-way through, then saves its copy.
 */
class CommitGateTest {
    /** A stand-in for LiveClient's journal handling, with a desktop that keeps every number it has seen. */
    private class Phone(val gate: CommitGate?) {
        var saved: JSONObject = JSONObject()
        val stored = mutableListOf<String>()
        val sent = mutableListOf<JSONObject>()
        val desktopSequences = mutableSetOf<Long>()
        val refused = mutableListOf<JSONObject>()

        fun save(next: JSONObject) { stored += next.toString(); saved = next }

        /** queueDesktop: number and save from the live journal, then send. */
        fun queue(type: String, payload: JSONObject = JSONObject()) {
            val commit = {
                val (next, event) = LiveJournal.prepare(saved, type, null, payload)
                save(next)
                send(event)
            }
            gate?.afterCommit(commit) ?: commit()
        }

        fun send(event: JSONObject) {
            sent += event
            // The desktop's check in application.py: a new request may not reuse a number.
            if (!desktopSequences.add(event.getLong("sequence"))) refused += event
        }

        /** apply(): work on a copy, let an answer start the next preview, then save the copy. */
        fun applyPreviewAnswer(answered: String, thenQueue: Int = 1, fail: Boolean = false) {
            val body = {
                val next = JSONObject(saved.toString())
                val outbox = next.getJSONArray("outbox")
                val kept = JSONArray()
                for (i in 0 until outbox.length()) if (JSONObject(outbox.getString(i)).getString("request_id") != answered) kept.put(outbox.getString(i))
                next.put("outbox", kept)
                repeat(thenQueue) { queue("desktop.read", JSONObject().put("op", "media.preview")) }
                if (fail) throw IllegalStateException("bad event")
                save(next)
            }
            if (gate != null) gate.during(body) else body()
        }

        fun requestIds() = (0 until saved.getJSONArray("outbox").length()).map { JSONObject(saved.getJSONArray("outbox").getString(it)).getString("request_id") }
    }

    @Test fun withoutTheGateANestedRequestPutsTheNumberBack() {
        // How the "already has a different request with this number" refusal happened before the fix.
        val phone = Phone(gate = null)
        repeat(3) { phone.queue("desktop.read", JSONObject().put("op", "media.preview")) }
        phone.applyPreviewAnswer(phone.sent[0].getString("request_id"))
        assertEquals(3L, phone.saved.getLong("sequence"))
        phone.queue("desktop.action", JSONObject().put("op", "rem.message"))
        assertEquals(listOf(3L), phone.refused.map { it.getLong("sequence") })
        assertEquals("rem.message", phone.refused.single().getJSONObject("payload").getString("op"))
    }

    @Test fun previewsStartedWhileAnAnswerIsAppliedKeepNumbersRisingAndStayQueued() {
        val phone = Phone(CommitGate())
        repeat(3) { phone.queue("desktop.read", JSONObject().put("op", "media.preview")) }
        // Eight sources: every answer after the first three starts the next preview.
        for (i in 0 until 5) phone.applyPreviewAnswer(phone.sent[i].getString("request_id"))
        // The user sends a Rem message between answers, and the background poll keeps running.
        phone.queue("desktop.action", JSONObject().put("op", "rem.message"))
        phone.queue("desktop.read", JSONObject().put("op", "rem.home"))
        assertEquals(emptyList<JSONObject>(), phone.refused)
        val numbers = phone.sent.map { it.getLong("sequence") }
        assertEquals((0L until 10L).toList(), numbers)
        assertEquals(10L, phone.saved.getLong("sequence"))
        // Every request still waiting for its answer is in the saved queue, so a reconnect sends it again.
        assertEquals(phone.sent.drop(5).map { it.getString("request_id") }, phone.requestIds())
        // Each save is a complete journal whose number never goes down.
        val savedNumbers = phone.stored.map { JSONObject(it).getLong("sequence") }
        assertEquals(savedNumbers.sorted(), savedNumbers)
    }

    @Test fun aResentQueueAfterReconnectRepeatsTheSameRequestsAndNumbers() {
        val phone = Phone(CommitGate())
        repeat(3) { phone.queue("desktop.read", JSONObject().put("op", "media.preview")) }
        phone.applyPreviewAnswer(phone.sent[0].getString("request_id"), thenQueue = 2)
        // Reconnect: LiveClient sends the saved queue as stored; the desktop treats a known ID with the same body as a repeat.
        val before = phone.sent.associateBy { it.getString("id") }
        val outbox = phone.saved.getJSONArray("outbox")
        for (i in 0 until outbox.length()) {
            val resent = JSONObject(outbox.getString(i))
            val original = before.getValue(resent.getString("id"))
            assertEquals(original.keys().asSequence().toSet(), resent.keys().asSequence().toSet())
            // Payloads here hold one field, so their text is the same whatever the key order.
            for (key in original.keys()) assertEquals(original.get(key).toString(), resent.get(key).toString())
        }
        assertEquals(4, outbox.length())
        // Reloading the journal from storage continues after the last number used.
        val reloaded = JSONObject(phone.stored.last())
        assertEquals(5L, reloaded.getLong("sequence"))
        phone.saved = reloaded
        phone.queue("sync.request")
        assertEquals(5L, phone.sent.last().getLong("sequence"))
        assertEquals(emptyList<JSONObject>(), phone.refused)
    }

    @Test fun heldRequestsStillRunWhenApplyingFails() {
        val phone = Phone(CommitGate())
        phone.queue("desktop.read", JSONObject().put("op", "media.preview"))
        val failure = runCatching { phone.applyPreviewAnswer(phone.sent[0].getString("request_id"), fail = true) }.exceptionOrNull()
        assertEquals("bad event", failure?.message)
        // The event's copy was never saved, so the live journal still has the first request and gains the held one.
        assertEquals(2L, phone.saved.getLong("sequence"))
        assertEquals(phone.sent.map { it.getString("request_id") }, phone.requestIds())
        assertEquals(emptyList<JSONObject>(), phone.refused)
    }

    @Test fun actionsRunAtOnceOutsideAndInOrderAfterwards() {
        val gate = CommitGate()
        val order = mutableListOf<String>()
        gate.afterCommit { order += "now" }
        val result = gate.during {
            gate.afterCommit { order += "first"; gate.afterCommit { order += "nested" } }
            gate.afterCommit { order += "second" }
            assertTrue(gate.holding)
            order += "body"
            42
        }
        assertEquals(42, result)
        assertFalse(gate.holding)
        assertEquals(listOf("now", "body", "first", "nested", "second"), order)
    }

    @Test fun everyHeldActionRunsAndTheFirstFailureIsReported() {
        val gate = CommitGate()
        val ran = mutableListOf<String>()
        val storage = LiveStorageLimit("queue full")
        val failure = runCatching {
            gate.during {
                gate.afterCommit { ran += "a"; throw storage }
                gate.afterCommit { ran += "b"; throw IllegalStateException("later") }
                gate.afterCommit { ran += "c" }
            }
        }.exceptionOrNull()
        assertSame(storage, failure)
        assertEquals(listOf("later"), failure!!.suppressed.map { it.message })
        assertEquals(listOf("a", "b", "c"), ran)
        assertFalse(gate.holding)
    }

    @Test fun applyingIsNeverNested() {
        val gate = CommitGate()
        val failure = runCatching { gate.during { gate.during { } } }.exceptionOrNull()
        assertTrue(failure is IllegalStateException)
        assertFalse(gate.holding)
    }
}
