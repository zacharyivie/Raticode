package dev.raticode.mobile

import android.app.Application
import androidx.lifecycle.ViewModelStore
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.*
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

class LiveRouteTest {
    @Test fun refusedWifiBeforeBindingRecoversWithTheOriginalInvitation() {
        val path = InstrumentationRegistry.getArguments().getString("deviceInvitationFile")
        assumeTrue(path != null)
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val app = instrumentation.targetContext.applicationContext as Application
        val pendingUnclaimed = InstrumentationRegistry.getArguments().getString("pendingUnclaimed") == "true"
        val original = DeviceInvitation.parse(File(path!!).readText())
        val initial = DeviceStore(app); initial.reset()
        if (pendingUnclaimed) {
            val state = initial.load(); initial.identity(state)
            val peer = org.json.JSONObject(original.json.toString()).apply {
                remove("secret"); put("initiator_topic", getString("pair_initiator_topic")); put("responder_topic", getString("pair_responder_topic"))
            }
            initial.save(state.put("pending_peer", peer).put("pending_invitation", java.util.Base64.getUrlEncoder().withoutPadding().encodeToString(original.bytes)))
        }
        val stores = ViewModelStore()
        val calls = java.util.concurrent.atomic.AtomicInteger()
        val recovered = java.util.concurrent.atomic.AtomicBoolean()
        lateinit var client: LiveClient
        instrumentation.runOnMainSync {
            client = LiveClient(app) { store, state, invitation, target, bind, status ->
                if (calls.incrementAndGet() == 1) {
                    if (pendingUnclaimed) throw java.net.ConnectException("Disposable Wi-Fi route unavailable")
                    // Constructor records the exact pending invitation before its socket is refused.
                    DeviceConnection(store, state, invitation, bind, status, target = target, carrierFactory = {
                        throw java.net.ConnectException("Disposable Wi-Fi route unavailable")
                    })
                } else {
                    if (invitation != null) { assertArrayEquals(original.bytes, invitation.bytes); recovered.set(true) }
                    DeviceConnection(store, state, invitation, bind, status, target = target)
                }
            }
            stores.put("live", client)
            if (pendingUnclaimed) client.connect() else { client.preview(File(path).readText()); client.pair() }
        }
        try {
            if (!pendingUnclaimed) {
                val failed = System.currentTimeMillis() + 15000
                while (!(client.pendingPair && calls.get() == 1 && client.status.contains("Wi-Fi")) && System.currentTimeMillis() < failed) Thread.sleep(50)
                assertTrue(client.pendingPair); assertFalse(client.paired)
                // The pending identity never reached desktop; recovery reuses the exact QR bytes.
                instrumentation.runOnMainSync { client.connect() }
            }
            val deadline = System.currentTimeMillis() + 30000
            while (!client.paired && System.currentTimeMillis() < deadline) Thread.sleep(50)
            assertTrue(recovered.get()); assertTrue(client.paired)
            assertEquals("Wi-Fi", client.route)
        } finally { instrumentation.runOnMainSync { stores.clear() }; DeviceStore(app).reset() }
    }
}
