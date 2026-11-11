package dev.raticode.mobile

import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.*
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File
import java.util.UUID

/** Opt-in disposable Python peer only. No paid provider calls. */
class LiveDeviceTest {
    @Test fun keystorePairResumeSyncAndChat() {
        val args = InstrumentationRegistry.getArguments()
        val path = args.getString("deviceInvitationFile")
        assumeTrue("Requires scripts/live-device-fixture.py", path != null)
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val store = DeviceStore(context)
        store.reset()
        val state = store.load()
        store.save(state)
        val invite = DeviceInvitation.parse(File(path!!).readText())
        val wifi = WifiRoute(context)
        val wifiDeadline = System.currentTimeMillis() + 5000
        while (!wifi.available && System.currentTimeMillis() < wifiDeadline) Thread.sleep(50)
        assertTrue("Device sockets require Wi-Fi", wifi.available)
        try {
        DeviceConnection(store, state, invite, wifi::bind, {}).use { session ->
            session.beforeReadyPersist = { throw java.io.IOException("Disposable lost-ready interruption") }
            assertThrows(java.io.IOException::class.java) { session.establish() }
            assertTrue(store.load().has("pending_peer")); assertFalse(store.load().has("peer"))
        }
        Thread.sleep(500)
        DeviceConnection(store, store.load(), null, wifi::bind, {}).use { session ->
            session.establish()
            Thread.sleep(2000) // disposable fixture grants thread after durable activation
            fun event(type: String, sequence: Int, thread: String?, payload: JSONObject) = JSONObject()
                .put("version", 2).put("id", UUID.randomUUID().toString()).put("request_id", UUID.randomUUID().toString())
                .put("thread_id", thread ?: JSONObject.NULL).put("sequence", sequence).put("type", type).put("payload", payload)
            session.send(event("sync.request", 0, null, JSONObject().put("after_sequence", JSONObject.NULL).put("limit", 100)))
            val snapshot = session.receive()
            assertEquals("sync.snapshot", snapshot.getString("type"))
            val threads = snapshot.getJSONObject("payload").getJSONArray("threads")
            assertEquals(1, threads.length())
            val thread = threads.getJSONObject(0).getString("thread_id")
            val request = event("chat.submit", 1, thread, JSONObject().put("text", "Fixture question").put("attachment_ids", JSONArray()))
            session.send(request)
            var accepted = false
            var replied = false
            repeat(6) {
                if (!replied) {
                    val response = session.receive()
                    if (response.getString("type") == "chat.accepted") accepted = true
                    if (response.getString("type") == "chat.message") {
                        assertEquals("Disposable fixture response", response.getJSONObject("payload").getString("text")); replied = true
                    }
                }
            }
            assertTrue(accepted); assertTrue(replied)
            run {
                session.send(request) // same immutable event is safe to retry
                fun nextType(type: String): JSONObject {
                    repeat(20) { val record = session.receive(); if (record.getString("type") == type) return record }
                    error("Missing $type")
                }
                val fileId = UUID.randomUUID().toString()
                val bytes = ByteArray(20000) { (it % 251).toByte() }
                session.send(event("file.offer", 2, thread, JSONObject().put("file_id", fileId).put("name", "fixture.bin")
                    .put("mime", "application/octet-stream").put("size", bytes.size).put("sha256", dev.raticode.mobile.data.LocalVault.digest(bytes))
                    .put("expires_at", System.currentTimeMillis()/1000+600)))
                assertEquals("receiving", nextType("file.status").getJSONObject("payload").getString("state"))
                for ((index, offset) in listOf(0, 16384).withIndex()) {
                    val end = minOf(offset+16384, bytes.size)
                    session.send(event("file.chunk", 3+index, thread, JSONObject().put("file_id", fileId).put("offset", offset)
                        .put("data", java.util.Base64.getUrlEncoder().withoutPadding().encodeToString(bytes.copyOfRange(offset,end))).put("eof", end == bytes.size)))
                    assertEquals(end.toLong(), nextType("file.status").getJSONObject("payload").getLong("received_size"))
                }
                session.send(event("file.request", 5, thread, JSONObject().put("file_id", fileId)))
                var received = 0
                for (index in 0..1) {
                    val chunk = nextType("file.chunk").getJSONObject("payload")
                    assertEquals(received, chunk.getInt("offset"))
                    val decoded = java.util.Base64.getUrlDecoder().decode(chunk.getString("data"))
                    assertArrayEquals(bytes.copyOfRange(received, received+decoded.size), decoded)
                    received += decoded.size
                    session.send(event("file.status", 6+index, thread, JSONObject().put("file_id", fileId)
                        .put("state", if(received==bytes.size) "available" else "receiving").put("received_size", received)))
                }
                assertEquals(bytes.size, received) // desktop treats the final status as an acknowledgment only
            }
        }
        } finally { wifi.close() }
        store.reset()
    }
}
