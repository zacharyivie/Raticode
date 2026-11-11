package dev.raticode.mobile.live

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.Inet4Address
import java.net.InetAddress
import java.net.SocketTimeoutException

class WakeOnLanTest {
    private val mac = "aa:bb:cc:dd:ee:01"
    private val macBytes = listOf(0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0x01).map { it.toByte() }
    private fun ip(text: String) = InetAddress.getByName(text) as Inet4Address
    private fun link(text: String, length: Int) = IpRoute(InetAddress.getByName(text), length)
    private fun endpoint(host: String) = JSONObject().put("host", host).put("port", 44233)
    private fun state(lan: String? = "192.168.1.203", saved: String? = mac, override: String? = null) = JSONObject().apply {
        put("peer", JSONObject().apply { lan?.let { put("lan_endpoint", endpoint(it)) }; saved?.let { put(WakeOnLan.FIELD, it) } })
        override?.let { put("lan_override", endpoint(it)) }
    }

    @Test fun packetIsSixFfBytesThenTheAddressSixteenTimes() {
        val packet = WakeOnLan.packet(macBytes)
        assertEquals(102, packet.size)
        assertTrue(packet.take(6).all { it == 0xFF.toByte() })
        for (i in 0 until 16) assertEquals(macBytes, packet.slice(6 + i * 6 until 12 + i * 6))
    }

    @Test fun packetCarriesNothingButTheHardwareAddress() {
        // Every byte after the header comes from the address, so nothing else can leak.
        assertEquals(setOf(0xFF.toByte()) + macBytes.toSet(), WakeOnLan.packet(macBytes).toSet())
    }

    @Test fun acceptsOnlyAddressesACardCouldAnswer() {
        assertEquals(macBytes, WakeOnLan.mac(mac))
        assertEquals(macBytes, WakeOnLan.mac("AA:BB:CC:DD:EE:01"))
        for (bad in listOf(null, "", "null", "aa:bb:cc:dd:ee", "aa:bb:cc:dd:ee:01:02", "aa-bb-cc-dd-ee-01", "gg:bb:cc:dd:ee:01", " aa:bb:cc:dd:ee:01",
            "00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff", "01:00:5e:00:00:01"))
            assertNull(bad, WakeOnLan.mac(bad))
    }

    @Test fun reportingAnAddressSavesItAndNotReportingOneRemovesIt() {
        val state = state(saved = null)
        WakeOnLan.remember(state, "AA:BB:CC:DD:EE:01")
        assertEquals(mac, state.getJSONObject("peer").getString(WakeOnLan.FIELD))
        WakeOnLan.remember(state, null)
        assertFalse(state.getJSONObject("peer").has(WakeOnLan.FIELD))
    }

    @Test fun aBadReportRemovesTheSavedAddressRatherThanKeepingAStaleOne() {
        val state = state()
        WakeOnLan.remember(state, "not a mac")
        assertFalse(state.getJSONObject("peer").has(WakeOnLan.FIELD))
    }

    @Test fun nothingIsSavedWithoutAPairedDesktop() {
        val state = JSONObject()
        WakeOnLan.remember(state, mac)
        assertEquals(0, state.length())
        assertNull(WakeOnLan.target(state))
    }

    @Test fun targetUsesTheDesktopsWifiAddress() {
        assertEquals(WakeOnLan.Target(macBytes, ip("192.168.1.203")), WakeOnLan.target(state()))
    }

    @Test fun aManualWifiAddressReplacesTheReportedOneLikeConnectionsDo() {
        assertEquals(ip("192.168.7.9"), WakeOnLan.target(state(override = "192.168.7.9"))?.desktop)
    }

    @Test fun noTargetWhileTheDesktopSettingIsOff() { assertNull(WakeOnLan.target(state(saved = null))) }

    @Test fun neverTargetsTailscale() {
        assertNull(WakeOnLan.target(state(lan = "100.101.102.103")))
        assertNull(WakeOnLan.target(state(override = "100.101.102.103")))
        assertNull(WakeOnLan.target(state(lan = "fd7a:115c:a1e0::5")))
    }

    @Test fun needsAPrivateIpv4WifiAddress() {
        for (host in listOf("127.0.0.1", "169.254.1.2", "fe80::1", "fd00::1"))
            assertNull(host, WakeOnLan.target(state(lan = host)))
        assertNull(WakeOnLan.target(state(lan = null)))
    }

    @Test fun subnetCheckUsesThePhonesOwnPrefix() {
        val desktop = ip("192.168.1.203")
        assertTrue(WakeOnLan.sameSubnet(link("192.168.1.20", 24), desktop))
        assertFalse(WakeOnLan.sameSubnet(link("192.168.2.20", 24), desktop))
        assertTrue(WakeOnLan.sameSubnet(link("192.168.2.20", 16), desktop))
        assertFalse(WakeOnLan.sameSubnet(link("192.168.1.20", 25), desktop))
    }

    @Test fun subnetCheckRefusesLinksThatCannotBroadcast() {
        val desktop = ip("192.168.1.203")
        assertFalse(WakeOnLan.sameSubnet(link("192.168.1.202", 31), desktop))
        assertFalse(WakeOnLan.sameSubnet(link("192.168.1.203", 32), desktop))
        assertFalse(WakeOnLan.sameSubnet(link("fe80::1", 64), desktop))
        // Anything wider than a /8 is a misconfiguration, not a home network.
        assertFalse(WakeOnLan.sameSubnet(link("192.168.1.20", 7), desktop))
    }

    @Test fun broadcastIsTheSubnetsOwn() {
        assertEquals(ip("192.168.1.255"), WakeOnLan.broadcast(link("192.168.1.20", 24)))
        assertEquals(ip("192.168.1.127"), WakeOnLan.broadcast(link("192.168.1.20", 25)))
        assertEquals(ip("10.255.255.255"), WakeOnLan.broadcast(link("10.1.2.3", 8)))
        assertEquals(ip("172.16.3.255"), WakeOnLan.broadcast(link("172.16.2.9", 22)))
        assertEquals(ip("192.168.1.203"), WakeOnLan.broadcast(link("192.168.1.200", 30)))
    }

    @Test fun sendPutsExactlyOnePacketOnTheSocket() {
        val receiver = DatagramSocket(0, InetAddress.getLoopbackAddress()).apply { soTimeout = 2000 }
        receiver.use {
            val payload = WakeOnLan.packet(macBytes)
            DatagramSocket().use { socket -> WakeOnLan.send(socket, InetAddress.getLoopbackAddress(), payload, receiver.localPort) }
            val packet = DatagramPacket(ByteArray(512), 512)
            receiver.receive(packet)
            assertEquals(102, packet.length)
            assertArrayEquals(payload, packet.data.copyOf(packet.length))
            receiver.soTimeout = 200
            assertThrows(SocketTimeoutException::class.java) { receiver.receive(DatagramPacket(ByteArray(512), 512)) }
        }
    }

    @Test fun theWordsNeverClaimItWorked() {
        for (text in listOf(WakeOnLan.SENT, WakeOnLan.NOT_SENT, WakeOnLan.LIMITS))
            for (claim in listOf("is waking", "woke", "has woken", "awake", "succeeded", "confirmed"))
                assertFalse("$text: $claim", text.lowercase().contains(claim))
        assertTrue(WakeOnLan.SENT.startsWith("Wake signal sent."))
        assertTrue(WakeOnLan.LIMITS.contains("Tailscale"))
        assertTrue(WakeOnLan.LIMITS.contains("firmware"))
    }

    private val bar = setOf("Rem", "Threads", "Organizations", "Swarms", "More")
    private fun banner(paired: Boolean = true, cantConnect: Boolean = true, available: Boolean = true, typing: Boolean = false, page: String = "Rem") =
        WakeOnLan.showsBanner(paired, cantConnect, available, typing, page, bar)

    @Test fun bannerNeedsAFailedConnectionAndABlockedDesktopThatCanBeWoken() {
        assertTrue(banner())
        assertTrue(banner(page = "Chat"))
        assertFalse(banner(cantConnect = false))
        assertFalse(banner(paired = false))
    }

    @Test fun aPhoneThatCantWakeThisDesktopSeesNoBannerOnAnyPage() {
        // Setting off, an older desktop, or a phone off the desktop's subnet all arrive as this.
        for (page in bar + setOf("Chat", "Desktop", "Browser", "Settings"))
            assertFalse(page, banner(available = false, page = page))
    }

    @Test fun bannerStaysOffPagesThatDontNeedIt() {
        assertFalse(banner(page = "Desktop"))
        assertFalse(banner(page = "Browser"))
        assertFalse(banner(page = "Settings"))
        assertFalse(banner(typing = true))
    }

    @Test fun overviewCarriesTheAddressOnlyWhenTheDesktopSendsAString() {
        fun parse(json: JSONObject) = DesktopViews.parse("overview", json) as DesktopOverview
        assertEquals(mac, parse(JSONObject().put(WakeOnLan.FIELD, mac)).wakeMac)
        assertNull(parse(JSONObject()).wakeMac)
        assertNull(parse(JSONObject().put(WakeOnLan.FIELD, JSONObject.NULL)).wakeMac)
        assertNull(parse(JSONObject().put(WakeOnLan.FIELD, 7)).wakeMac)
    }
}
