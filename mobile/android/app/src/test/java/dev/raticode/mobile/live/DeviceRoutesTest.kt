package dev.raticode.mobile.live

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.net.ConnectException
import java.net.InetAddress
import java.net.SocketTimeoutException
import javax.net.ssl.SSLException

class DeviceRoutesTest {
    private fun endpoint(host: String, port: Int = 44233) = JSONObject().put("host", host).put("port", port)
    private fun state(tailscale: Boolean? = null, chosen: String? = null) = JSONObject().apply {
        if (tailscale != null) put("tailscale", JSONObject().put("enabled", tailscale).apply { chosen?.let { put("endpoint", endpoint(it)) } })
    }
    private fun peer(lan: String? = "192.168.1.203", reported: String? = null) = JSONObject().apply {
        lan?.let { put("lan_endpoint", endpoint(it)) }
        reported?.let { put("tailnet_endpoint", endpoint(it)) }
    }
    private fun failure(block: () -> Unit) = assertThrows(ConnectionFailure::class.java) { block() }.guidance

    @Test fun recognizesOnlyTailscaleRanges() {
        for (host in listOf("100.64.0.1", "100.101.102.103", "100.127.255.255", "fd7a:115c:a1e0::1", "fd7a:115c:a1e0:ab12:4843:cd96:6258:b240"))
            assertTrue(host, TailnetAddress.contains(InetAddress.getByName(host)))
        for (host in listOf("100.63.255.255", "100.128.0.0", "192.168.1.2", "10.0.0.1", "fd7a:115c:a1e1::1", "fd00::1", "8.8.8.8"))
            assertFalse(host, TailnetAddress.contains(InetAddress.getByName(host)))
    }

    @Test fun endpointsAreClassifiedByAddressAndNeverAcceptNamesOrPublicAddresses() {
        assertEquals(DevicePath.WIFI, DeviceInvitation.validateDesktopEndpoint(endpoint("192.168.1.203")).path)
        assertEquals(DevicePath.TAILSCALE, DeviceInvitation.validateDesktopEndpoint(endpoint("100.101.102.103")).path)
        assertEquals(DevicePath.TAILSCALE, DeviceInvitation.validateDesktopEndpoint(endpoint("fd7a:115c:a1e0::5")).path)
        for (host in listOf("8.8.8.8", "100.128.0.1", "fd00::1", "desktop.tail1234.ts.net", "desktop", "100.1", "0.0.0.0", "224.0.0.1"))
            assertThrows(host, Exception::class.java) { DeviceInvitation.validateDesktopEndpoint(endpoint(host)) }
        assertThrows(Exception::class.java) { DeviceInvitation.validateDesktopEndpoint(endpoint("100.101.102.103", 0)) }
        // The Wi-Fi dialog stays Wi-Fi only; the Tailscale dialog stays Tailscale only.
        assertThrows(Exception::class.java) { DeviceInvitation.validateLanEndpoint(endpoint("100.101.102.103")) }
        assertThrows(Exception::class.java) { DeviceInvitation.validateTailnetEndpoint(endpoint("192.168.1.203")) }
    }

    @Test fun tailscaleOffKeepsWifiOnlyBehavior() {
        assertEquals(listOf(DeviceEndpoint("192.168.1.203", 44233, DevicePath.WIFI)), DeviceRoutes.plan(state(), peer(), wifiAvailable = true))
        // A saved Tailscale address is ignored while the switch is off.
        assertEquals(listOf(DevicePath.WIFI), DeviceRoutes.plan(state(false, "100.101.102.103"), peer(), true).map { it.path })
        assertEquals(NO_WIFI_GUIDANCE, failure { DeviceRoutes.plan(state(false, "100.101.102.103"), peer(), false) })
        assertEquals(NO_ADDRESS_GUIDANCE, failure { DeviceRoutes.plan(state(), peer(lan = null), true) })
    }

    @Test fun tailscaleOnTriesWifiFirstThenTailscale() {
        val on = state(true, "100.101.102.103")
        assertEquals(listOf(DevicePath.WIFI, DevicePath.TAILSCALE), DeviceRoutes.plan(on, peer(), true).map { it.path })
        assertEquals(listOf(DevicePath.TAILSCALE, DevicePath.WIFI), DeviceRoutes.plan(on, peer(), true, preferTailscale = true).map { it.path })
        // Without Wi-Fi (mobile data) only Tailscale is tried; there is no unbound or cellular route.
        assertEquals(listOf(DeviceEndpoint("100.101.102.103", 44233, DevicePath.TAILSCALE)), DeviceRoutes.plan(on, peer(), false))
        assertEquals(TAILSCALE_NO_ADDRESS, failure { DeviceRoutes.plan(state(true), peer(), false) })
    }

    @Test fun chosenAddressWinsOverDesktopReportAndInvalidValuesAreIgnored() {
        assertEquals("100.101.102.103", DeviceRoutes.tailnetEndpoint(state(true, "100.101.102.103"), peer(reported = "100.90.1.1"))?.host)
        assertEquals("100.90.1.1", DeviceRoutes.tailnetEndpoint(state(true), peer(reported = "100.90.1.1"))?.host)
        assertNull(DeviceRoutes.tailnetEndpoint(state(true, "8.8.8.8"), peer(reported = "192.168.1.5")))
    }

    @Test fun tailscaleOnlyDesktopAddressRequiresTheSwitch() {
        val tailnetOnly = peer(lan = "fd7a:115c:a1e0::9")
        assertEquals(TAILSCALE_OFF, failure { DeviceRoutes.plan(state(), tailnetOnly, true) })
        assertEquals(listOf(DeviceEndpoint("fd7a:115c:a1e0::9", 44233, DevicePath.TAILSCALE)), DeviceRoutes.plan(state(true), tailnetOnly, true))
        // The same address entered and reported is tried once.
        assertEquals(1, DeviceRoutes.plan(state(true, "fd7a:115c:a1e0::9"), tailnetOnly, false).size)
    }

    @Test fun desktopReportsNeverEraseTheChosenTailscaleAddressOrTheWifiRoute() {
        val saved = state(true, "100.101.102.103").put("peer", peer()).put("lan_override", endpoint("192.168.1.50"))
        DeviceRoutes.applyReport(saved, JSONObject().put("lan_endpoint", endpoint("100.90.1.1")))
        assertEquals("192.168.1.203", saved.getJSONObject("peer").getJSONObject("lan_endpoint").getString("host"))
        assertTrue(saved.has("lan_override"))
        assertEquals("100.90.1.1", saved.getJSONObject("peer").getJSONObject("tailnet_endpoint").getString("host"))
        assertEquals("100.101.102.103", DeviceRoutes.tailnetEndpoint(saved, saved.getJSONObject("peer"))?.host)
        DeviceRoutes.applyReport(saved, JSONObject().put("lan_endpoint", endpoint("192.168.1.204")))
        assertEquals("192.168.1.204", saved.getJSONObject("peer").getJSONObject("lan_endpoint").getString("host"))
        assertFalse(saved.has("lan_override"))
        assertEquals("100.101.102.103", saved.getJSONObject("tailscale").getJSONObject("endpoint").getString("host"))
        DeviceRoutes.applyReport(saved, JSONObject().put("lan_endpoint", JSONObject.NULL))
        assertEquals("192.168.1.204", saved.getJSONObject("peer").getJSONObject("lan_endpoint").getString("host"))
    }

    @Test fun desktopTailnetReportFollowsTheDesktopListener() {
        val saved = state(true).put("peer", peer())
        DeviceRoutes.applyReport(saved, JSONObject().put("lan_endpoint", endpoint("192.168.1.203")).put("tailnet_endpoint", endpoint("100.90.1.1")))
        assertEquals("100.90.1.1", DeviceRoutes.tailnetEndpoint(saved, saved.getJSONObject("peer"))?.host)
        // An older desktop omits the field: keep the last report.
        DeviceRoutes.applyReport(saved, JSONObject().put("lan_endpoint", endpoint("192.168.1.203")))
        assertEquals("100.90.1.1", DeviceRoutes.tailnetEndpoint(saved, saved.getJSONObject("peer"))?.host)
        // Null means the desktop stopped listening on Tailscale.
        DeviceRoutes.applyReport(saved, JSONObject().put("lan_endpoint", endpoint("192.168.1.203")).put("tailnet_endpoint", JSONObject.NULL))
        assertNull(DeviceRoutes.tailnetEndpoint(saved, saved.getJSONObject("peer")))
    }

    @Test fun onlyAskForTheTailnetAddressWhileTheSwitchIsOn() {
        // Desktops without Tailscale support reject unknown request fields.
        assertFalse(DeviceRoutes.requestFields(state(), JSONObject()).has("include_tailnet_endpoint"))
        assertFalse(DeviceRoutes.requestFields(state(false), JSONObject()).has("include_tailnet_endpoint"))
        assertTrue(DeviceRoutes.requestFields(state(true), JSONObject()).getBoolean("include_tailnet_endpoint"))
    }

    @Test fun turningTheSwitchOffDropsQueuedTailnetRequestsOnly() {
        fun sync(state: JSONObject) = LiveJournal.prepare(state, "sync.request", null,
            DeviceRoutes.requestFields(state, JSONObject().put("after_sequence", JSONObject.NULL).put("limit", 100).put("include_connection", true))).first
        var saved = state(true)
        saved = sync(saved)
        saved = LiveJournal.prepare(saved, "catalog.request", null, JSONObject()).first
        val outbox = saved.getJSONArray("outbox")
        assertTrue(JSONObject(outbox.getString(0)).getJSONObject("payload").getBoolean("include_tailnet_endpoint"))

        // The switch-off save removes the request and keeps everything else, including the sequence counter.
        saved.getJSONObject("tailscale").put("enabled", false)
        assertTrue(DeviceRoutes.dropTailnetRequests(saved))
        val kept = saved.getJSONArray("outbox")
        assertEquals(listOf("catalog.request"), (0 until kept.length()).map { JSONObject(kept.getString(it)).getString("type") })
        assertEquals(2L, saved.getLong("sequence"))
        assertFalse(DeviceRoutes.dropTailnetRequests(saved))

        // The next sync is clean and takes a new, unique sequence number.
        saved = sync(saved)
        val events = saved.getJSONArray("outbox").let { a -> (0 until a.length()).map { JSONObject(a.getString(it)) } }
        assertTrue(events.none { it.getJSONObject("payload").has("include_tailnet_endpoint") })
        assertEquals(listOf(1L, 2L), events.map { it.getLong("sequence") })
        assertFalse(DeviceRoutes.dropTailnetRequests(state()))
    }

    @Test fun vpnRoutesMustCarryTheExactDesktopAddress() {
        fun route(prefix: String, length: Int, unicast: Boolean = true) = IpRoute(InetAddress.getByName(prefix), length, unicast)
        val desktop = InetAddress.getByName("100.101.102.103")
        assertTrue(IpRoute.carries(listOf(route("100.64.0.0", 10)), desktop))
        assertTrue(IpRoute.carries(listOf(route("100.101.102.103", 32)), desktop))
        assertFalse(IpRoute.carries(listOf(route("100.101.102.104", 32)), desktop))
        assertFalse(IpRoute.carries(listOf(route("fd7a:115c:a1e0::", 48)), desktop))
        assertTrue(IpRoute.carries(listOf(route("fd7a:115c:a1e0::", 48)), InetAddress.getByName("fd7a:115c:a1e0::9")))
        // A more specific excluded route wins over the covering one.
        assertFalse(IpRoute.carries(listOf(route("100.64.0.0", 10), route("100.101.0.0", 16, unicast = false)), desktop))
        assertFalse(IpRoute.carries(emptyList(), desktop))
    }

    @Test fun tailscaleFailuresGiveTailscaleGuidance() {
        val connect = connectionGuidance(ConnectionStage.CONNECTING, SocketTimeoutException(), DevicePath.TAILSCALE)
        assertTrue(connect.contains("through Tailscale")); assertFalse(connect.contains("same Wi-Fi"))
        assertTrue(connectionGuidance(ConnectionStage.CONNECTING, ConnectException("10.0.0.1 refused"), DevicePath.TAILSCALE).contains("Tailscale address"))
        assertTrue(connectionGuidance(ConnectionStage.AUTHENTICATION, SSLException("x"), DevicePath.TAILSCALE).contains("over Tailscale"))
        assertEquals(TAILSCALE_UNAVAILABLE, connectionGuidance(ConnectionStage.CONNECTING, ConnectionFailure(TAILSCALE_UNAVAILABLE), DevicePath.TAILSCALE))
        // Fixed text only: raw exception messages with addresses never reach the UI.
        assertFalse(connectionGuidance(ConnectionStage.CONNECTING, ConnectException("100.101.102.103 refused"), DevicePath.TAILSCALE).contains("100.101"))
    }
}
