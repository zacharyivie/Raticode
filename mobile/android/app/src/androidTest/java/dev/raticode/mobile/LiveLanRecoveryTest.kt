package dev.raticode.mobile

import android.app.Application
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.width
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModelStore
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.*
import dev.raticode.mobile.ui.LiveScreen
import dev.raticode.mobile.ui.RaticodeTheme
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import java.io.File
import java.util.Base64
import java.util.concurrent.atomic.AtomicInteger

class LiveLanRecoveryTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()

    @Test fun pendingPairShowsWifiAddressRecoveryWithoutPrivateErrors() {
        val landscape = InstrumentationRegistry.getArguments().getString("lanOrientation") == "landscape"
        val orientation = if (landscape) android.content.pm.ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE else android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
        compose.runOnUiThread { compose.activity.requestedOrientation = orientation }
        compose.waitUntil(10000) { compose.activity.resources.configuration.orientation ==
            if (landscape) android.content.res.Configuration.ORIENTATION_LANDSCAPE else android.content.res.Configuration.ORIENTATION_PORTRAIT }
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        val store = DeviceStore(app); store.reset()
        store.save(store.load().put("pending_peer", JSONObject().put("lan_endpoint",
            JSONObject().put("host", "192.168.1.10").put("port", 12345))))
        val owners = ViewModelStore()
        val calls = AtomicInteger()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app) { _, _, _, _, _, _ ->
                calls.incrementAndGet(); throw java.net.ConnectException("private exception text")
            }
            owners.put("live", client)
            compose.activity.setContent { RaticodeTheme { Box(Modifier.width(320.dp)) { LiveScreen(liveClient = client, startPage = "Threads") } } }
            client.connect()
        }
        try {
            compose.waitUntil(10000) { client.pendingPair && client.lanAvailable && client.status.contains("Desktop address could not be reached over Wi-Fi") }
            assertFalse(client.status.contains("private exception text"))
            compose.onNodeWithText("Desktop address: 192.168.1.10, port 12345").performScrollTo().assertIsDisplayed()
            compose.onNodeWithText("Raticode connects only over Wi-Fi", substring = true).performScrollTo().assertIsDisplayed()
            compose.onNodeWithText("Try local network").assertDoesNotExist()
            val before = calls.get()
            compose.onNodeWithText("Recover pending pairing").performScrollTo().performClick()
            compose.waitUntil(5000) { calls.get() > before }
            compose.onNodeWithText("Set desktop address").performScrollTo().performClick()
            compose.onNodeWithText("Desktop IP address").performTextReplacement("8.8.8.8")
            compose.onNodeWithText("Connect", substring = false).assertIsNotEnabled()
            compose.onNodeWithText("Desktop IP address").performTextReplacement("192.168.1.11")
            compose.onNodeWithText("Port", substring = false).performTextReplacement("44233")
            val beforeAddress = calls.get()
            compose.onNodeWithText("Connect", substring = false).assertIsEnabled().performClick()
            compose.waitUntil(5000) { client.lanHost == "192.168.1.11" && calls.get() > beforeAddress }
        } finally { compose.runOnUiThread { owners.clear() }; DeviceStore(app).reset() }
    }

    @Test fun staleQrAddressRecoversPendingEnrollmentThroughManualWifiAddress() {
        val path = InstrumentationRegistry.getArguments().getString("deviceInvitationFile")
        assumeTrue("Requires a fresh disposable fixture", path != null)
        val invitation = DeviceInvitation.parse(File(path!!).readText())
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        val store = DeviceStore(app); store.reset()
        val state = store.load(); store.identity(state)
        val peer = JSONObject(invitation.json.toString()).apply {
            remove("secret")
            put("initiator_topic", getString("pair_initiator_topic")); put("responder_topic", getString("pair_responder_topic"))
        }
        store.save(state.put("pending_peer", peer).put("pending_invitation", Base64.getUrlEncoder().withoutPadding().encodeToString(invitation.bytes)))
        val owners = ViewModelStore()
        val staleCalls = AtomicInteger()
        val enrollmentCalls = AtomicInteger()
        val connectionCalls = AtomicInteger()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app) { savedStore, saved, original, target, bind, status ->
                connectionCalls.incrementAndGet()
                // Treat the QR's address as stale until the user enters the current one.
                if (!saved.has("peer") && !saved.has("lan_override")) {
                    staleCalls.incrementAndGet(); throw java.net.ConnectException("Disposable stale address")
                }
                if (original != null) { assertArrayEquals(invitation.bytes, original.bytes); enrollmentCalls.incrementAndGet() }
                if (saved.has("peer")) assertFalse("Authenticated endpoint should supersede the manual hint", saved.has("lan_override"))
                DeviceConnection(savedStore, saved, original, bind, status, target = target)
            }
            owners.put("live", client)
            compose.activity.setContent { RaticodeTheme { Box(Modifier.width(320.dp)) { LiveScreen(liveClient = client, startPage = "Threads") } } }
            client.connect()
        }
        try {
            compose.waitUntil(10000) { client.pendingPair && staleCalls.get() >= 2 && client.status.contains("Wi-Fi") }
            compose.onNodeWithText("Set desktop address").performScrollTo().performClick()
            compose.onNodeWithText("Desktop IP address").performTextReplacement("10.0.2.2")
            compose.onNodeWithText("Port", substring = false).performTextReplacement("38473")
            compose.onNodeWithText("Connect", substring = false).performClick()
            compose.waitUntil(20000) { client.connected && client.paired && client.remReady == true }
            assertEquals("Wi-Fi", client.route)
            assertEquals(1, enrollmentCalls.get())
            val beforeReconnect = connectionCalls.get()
            compose.runOnUiThread { client.disconnect(); client.connect() }
            compose.waitUntil(10000) { client.connected && connectionCalls.get() > beforeReconnect && client.status == "Authenticated desktop via Wi-Fi" }
        } finally { compose.runOnUiThread { owners.clear() }; DeviceStore(app).reset() }
    }
}
