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
import org.junit.Rule
import org.junit.Test
import java.util.Collections

/**
 * The emulator has no VPN, so the real Tailscale route must refuse to open a socket. Wi-Fi
 * attempts are refused by the factory; Tailscale attempts call the real socket binding.
 */
class LiveTailscaleTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()

    private fun start(lan: String, targets: MutableList<DeviceEndpoint>): Pair<LiveClient, ViewModelStore> {
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        val store = DeviceStore(app); store.reset()
        store.save(store.load().put("pending_peer", JSONObject().put("lan_endpoint", JSONObject().put("host", lan).put("port", 12345))))
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app) { _, _, _, target, bind, _ ->
                targets.add(target)
                if (target.path == DevicePath.TAILSCALE) java.net.Socket().use { bind(it) }
                throw java.net.ConnectException("private exception text")
            }
            owners.put("live", client)
            compose.activity.setContent { RaticodeTheme { Box(Modifier.width(320.dp)) { LiveScreen(liveClient = client, startPage = "Threads") } } }
            client.connect()
        }
        return client to owners
    }

    private fun finish(owners: ViewModelStore) {
        compose.runOnUiThread { owners.clear() }
        DeviceStore(InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application).reset()
    }

    @Test fun switchIsOffByDefaultAndTailscaleFailsClosedWithoutAVpn() {
        val targets = Collections.synchronizedList(mutableListOf<DeviceEndpoint>())
        val (client, owners) = start("192.168.1.10", targets)
        try {
            compose.waitUntil(10000) { client.status.contains("Desktop address could not be reached over Wi-Fi") }
            assertFalse(client.tailscaleEnabled)
            assertTrue(targets.isNotEmpty() && targets.all { it.path == DevicePath.WIFI })
            compose.onNodeWithTag("tailscaleToggle").performScrollTo().assertIsOff().performClick()
            compose.waitUntil(5000) { client.tailscaleEnabled }
            compose.onNodeWithTag("tailscaleToggle").assertIsOn()
            compose.onNodeWithText("No Tailscale address saved", substring = true).performScrollTo().assertIsDisplayed()

            compose.onNodeWithText("Set Tailscale address").performScrollTo().performClick()
            compose.onNodeWithText("Desktop Tailscale IP address").performTextReplacement("192.168.1.20")
            compose.onNodeWithText("Port", substring = false).performTextReplacement("12345")
            compose.onNodeWithText("Save").assertIsNotEnabled()
            compose.onNodeWithText("Desktop Tailscale IP address").performTextReplacement("desktop.tail1234.ts.net")
            compose.onNodeWithText("Save").assertIsNotEnabled()
            compose.onNodeWithText("Desktop Tailscale IP address").performTextReplacement("100.101.102.103")
            compose.onNodeWithText("Save").assertIsEnabled().performClick()

            compose.waitUntil(10000) { client.tailscaleHost == "100.101.102.103" && client.status.contains(TAILSCALE_UNAVAILABLE) }
            assertTrue(client.tailscaleChosen)
            assertFalse(client.connected)
            assertFalse(client.status.contains("private exception text"))
            // Wi-Fi is tried first, then Tailscale, which opened no socket.
            val last = targets.takeLast(2)
            assertEquals(listOf(DevicePath.WIFI, DevicePath.TAILSCALE), last.map { it.path })
            assertEquals(DeviceEndpoint("100.101.102.103", 12345, DevicePath.TAILSCALE), last[1])
            compose.onNodeWithText("Desktop Tailscale address: 100.101.102.103, port 12345").performScrollTo().assertIsDisplayed()

            compose.onNodeWithTag("tailscaleToggle").performScrollTo().performClick()
            compose.waitUntil(5000) { !client.tailscaleEnabled }
            val before = targets.size
            compose.onNodeWithText("Recover pending pairing").performScrollTo().performClick()
            compose.waitUntil(10000) { targets.size > before && client.status.contains("over Wi-Fi") }
            assertTrue(targets.drop(before).all { it.path == DevicePath.WIFI })
            // Off keeps the chosen address for later.
            assertEquals("100.101.102.103", client.tailscaleHost)
        } finally { finish(owners) }
    }

    /**
     * Needs a fresh fixture from a desktop without Tailscale support, which closes the session
     * on `include_tailnet_endpoint`. The state handed to each connection is the outbox it resends.
     */
    @Test fun olderDesktopRecoversOverWifiWhenTheSwitchIsTurnedOff() {
        val path = InstrumentationRegistry.getArguments().getString("deviceInvitationFile")
        org.junit.Assume.assumeTrue("Requires a fresh disposable fixture", path != null)
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        DeviceStore(app).reset()
        fun flagged(state: JSONObject) = (state.optJSONArray("outbox") ?: org.json.JSONArray()).let { outbox ->
            (0 until outbox.length()).any { JSONObject(outbox.getString(it)).optJSONObject("payload")?.has("include_tailnet_endpoint") == true }
        }
        val handed = Collections.synchronizedList(mutableListOf<JSONObject>())
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app) { store, state, invitation, target, bind, status ->
                handed.add(JSONObject(state.toString()))
                DeviceConnection(store, state, invitation, bind, status, target = target)
            }
            owners.put("live", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client, startPage = "Threads") } }
            client.preview(java.io.File(path!!).readText()); client.pair()
        }
        try {
            compose.waitUntil(20000) { client.connected && client.threads.isNotEmpty() }
            // The next sync asks for the Tailscale address; this desktop drops the session, and
            // the reconnect carries the same queued request.
            client.setTailscale(true)
            compose.waitUntil(30000) { synchronized(handed) { handed.any(::flagged) } }
            client.setTailscale(false)
            val from = handed.size
            compose.waitUntil(20000) { client.connected }
            // Two sync rounds on the older desktop without losing the session.
            Thread.sleep(12000)
            assertTrue(client.status, client.connected)
            assertTrue(synchronized(handed) { handed.drop(from).none(::flagged) })
        } finally {
            compose.runOnUiThread { owners.clear() }
            val store = DeviceStore(app)
            val left = runCatching { store.load() }.getOrNull()
            store.reset()
            if (left != null) assertFalse("No queued request may still ask for the Tailscale address", flagged(left))
        }
    }

    @Test fun tailscaleOnlyDesktopAddressNeedsTheSwitch() {
        val targets = Collections.synchronizedList(mutableListOf<DeviceEndpoint>())
        val (client, owners) = start("fd7a:115c:a1e0::9", targets)
        try {
            compose.waitUntil(10000) { client.status.contains(TAILSCALE_OFF) }
            assertTrue("No socket without the switch", targets.isEmpty())
            compose.onNodeWithTag("tailscaleToggle").performScrollTo().performClick()
            compose.waitUntil(10000) { client.status.contains(TAILSCALE_UNAVAILABLE) }
            assertTrue(targets.isNotEmpty() && targets.all { it == DeviceEndpoint("fd7a:115c:a1e0::9", 12345, DevicePath.TAILSCALE) })
            assertFalse(client.connected)
        } finally { finish(owners) }
    }
}
