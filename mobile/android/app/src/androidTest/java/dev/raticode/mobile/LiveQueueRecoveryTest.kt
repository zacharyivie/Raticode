package dev.raticode.mobile

import android.app.Application
import android.os.Looper
import androidx.lifecycle.ViewModelStore
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.DeviceStore
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.live.LiveJournal
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

class LiveQueueRecoveryTest {
    private val app get() = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
    private fun awaitCondition(condition: () -> Boolean) {
        val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(10)
        while (!condition() && System.nanoTime() < deadline) Thread.sleep(25)
        assertTrue("Condition did not complete before deadline", condition())
    }
    private fun onMain(action: () -> Unit) = InstrumentationRegistry.getInstrumentation().runOnMainSync(action)

    @Test fun successfulSendCallsBackAfterCommitOnMainAndDraftQueueCoalesces() {
        val thread = UUID.randomUUID().toString()
        val seed = DeviceStore(app); seed.reset()
        seed.save(seed.load().put("threads", JSONArray().put(JSONObject().put("thread_id", thread).put("title", "Local storage test"))))
        seed.close()
        val owner = ViewModelStore()
        lateinit var client: LiveClient
        onMain { client = LiveClient(app); owner.put("storage-test", client) }
        try {
            awaitCondition { client.threads.isNotEmpty() }
            onMain { repeat(1000) { client.draft(thread, "draft $it") } }
            awaitCondition { client.drafts[thread] == "draft 999" }
            val committed = CountDownLatch(1)
            val mainCallback = AtomicBoolean()
            onMain { client.send(thread, "draft 999", onCommitted = {
                mainCallback.set(Looper.myLooper() == Looper.getMainLooper())
                committed.countDown()
            }) }
            assertTrue(committed.await(10, TimeUnit.SECONDS))
            assertTrue(mainCallback.get())
            awaitCondition { !client.sending && client.messages.size == 1 }
        } finally { onMain { owner.clear() } }
        val restored = DeviceStore(app)
        val state = restored.load()
        assertEquals(1, state.getJSONArray("outbox").length())
        val request = JSONObject(state.getJSONArray("outbox").getString(0))
        assertEquals("draft 999", request.getJSONObject("payload").getString("text"))
        assertEquals(request.getString("request_id"), state.getJSONArray("messages").getJSONObject(0).getString("id"))
        assertEquals("", state.getJSONObject("drafts").getString(thread))
        restored.reset(); restored.close()
    }

    @Test fun fullOutboxKeepsDraftAndDoesNotCallCommitCallback() {
        val thread = UUID.randomUUID().toString()
        val seed = DeviceStore(app); seed.reset()
        val outbox = JSONArray().apply { repeat(LiveJournal.MAX_OUTBOX) { put(JSONObject().put("request_id", "$it").toString()) } }
        seed.save(seed.load().put("outbox", outbox).put("threads", JSONArray().put(JSONObject().put("thread_id", thread).put("title", "Full queue test"))))
        seed.close()
        val owner = ViewModelStore()
        lateinit var client: LiveClient
        val called = AtomicBoolean()
        onMain { client = LiveClient(app); owner.put("storage-test", client) }
        try {
            awaitCondition { client.threads.isNotEmpty() }
            onMain { client.send(thread, "keep this message", onCommitted = { called.set(true) }) }
            awaitCondition { client.status.contains("offline queue is full") && !client.sending }
            assertEquals("keep this message", client.drafts[thread])
            assertFalse(called.get())
            assertTrue(client.messages.isEmpty())
        } finally { onMain { owner.clear() } }
        val restored = DeviceStore(app)
        val state = restored.load()
        assertEquals("keep this message", state.getJSONObject("drafts").getString(thread))
        assertEquals(LiveJournal.MAX_OUTBOX, state.getJSONArray("outbox").length())
        restored.reset(); restored.close()
    }
}
