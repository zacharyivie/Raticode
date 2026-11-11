package dev.raticode.mobile.live

import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.Inet4Address
import java.net.InetAddress

/**
 * Wake-on-LAN for a sleeping desktop. The packet is unencrypted by design and holds only the
 * network card's hardware address. It has no reply, so nothing here can say whether it worked.
 * See "Wake the computer" in docs/architecture.md for the security note.
 */
internal object WakeOnLan {
    /** The field in the desktop's `overview` answer. Present only while the desktop setting is on. */
    const val FIELD = "wake_mac"
    /** The usual Wake-on-LAN port. Sleeping cards match the packet's contents, not its port. */
    const val PORT = 9
    /** How long the button stays disabled after a tap, so a double tap can't send twice. */
    const val PAUSE_MILLIS = 30_000L
    const val SENT = "Wake signal sent. Raticode can't tell whether your computer received it, and will keep trying to connect."
    const val NOT_SENT = "The wake signal couldn't be sent. Check this phone's Wi-Fi and try again."
    const val LIMITS = "Works only on your computer's Wi-Fi network, not over Tailscale. Your computer's firmware must allow Wake-on-LAN."

    private val FORMAT = Regex("[0-9a-f]{2}(:[0-9a-f]{2}){5}")

    /** Everything one packet needs. [desktop] picks the Wi-Fi network; it is not the packet's destination. */
    data class Target(val mac: List<Byte>, val desktop: Inet4Address)

    /** The six bytes, or null for anything a real network card wouldn't answer to. */
    fun mac(value: String?): List<Byte>? {
        val text = value?.lowercase()?.takeIf { FORMAT.matches(it) } ?: return null
        val bytes = text.split(':').map { it.toInt(16).toByte() }
        val group = bytes[0].toInt() and 1 != 0
        return bytes.takeUnless { group || it.all { byte -> byte == 0.toByte() } }
    }

    /** Six 0xFF bytes, then the hardware address sixteen times: 102 bytes. */
    fun packet(mac: List<Byte>): ByteArray {
        require(mac.size == 6)
        return ByteArray(6) { 0xFF.toByte() } + List(16) { mac }.flatten().toByteArray()
    }

    /**
     * Saves what an authenticated `overview` reported, or removes the saved address when it
     * reported none, so turning the desktop setting off takes it off the phone too.
     */
    fun remember(state: JSONObject, reported: String?) {
        val peer = state.optJSONObject("peer") ?: return
        if (mac(reported) != null) peer.put(FIELD, reported!!.lowercase()) else peer.remove(FIELD)
    }

    /**
     * The saved address and the desktop's private Wi-Fi address, or null when this desktop can't
     * be woken from here. A Tailscale address never qualifies: a tunnel can't carry a broadcast.
     */
    fun target(state: JSONObject): Target? {
        val peer = state.optJSONObject("peer") ?: return null
        val mac = mac(peer.opt(FIELD) as? String) ?: return null
        val endpoint = DeviceRoutes.endpoint(state.optJSONObject("lan_override"))
            ?: DeviceRoutes.endpoint(peer.optJSONObject("lan_endpoint"))
            ?: return null
        if (endpoint.path != DevicePath.WIFI) return null
        val address = runCatching { InetAddress.getByName(endpoint.host) }.getOrNull() as? Inet4Address ?: return null
        return if (address.isSiteLocalAddress) Target(mac, address) else null
    }

    /** True when this phone address, with its prefix length, shares a subnet that can hold a broadcast with [desktop]. */
    fun sameSubnet(link: IpRoute, desktop: Inet4Address): Boolean =
        link.prefix is Inet4Address && link.length in 8..30 && link.matches(desktop)

    /** The subnet's broadcast address, for example 192.168.1.255 for 192.168.1.20/24. */
    fun broadcast(link: IpRoute): Inet4Address {
        require(link.prefix is Inet4Address && link.length in 8..30)
        val hostBits = (0xFFFFFFFFL shr link.length).toInt()
        val base = link.prefix.address.fold(0) { value, byte -> (value shl 8) or (byte.toInt() and 0xFF) }
        val value = base or hostBits
        return InetAddress.getByAddress(ByteArray(4) { (value ushr (24 - it * 8)).toByte() }) as Inet4Address
    }

    /**
     * Whether a page other than Desktop offers the button. The banner has no use without a button
     * that can act, so a phone that can't wake this desktop sees nothing new when a connection fails.
     */
    fun showsBanner(paired: Boolean, cantConnect: Boolean, wakeAvailable: Boolean, typing: Boolean, page: String, topLevel: Set<String>): Boolean =
        paired && cantConnect && wakeAvailable && !typing && page != "Desktop" && page != "Browser" && (page in topLevel || page == "Chat")

    /** Sends exactly one datagram on a socket the caller has already bound to the Wi-Fi network. */
    fun send(socket: DatagramSocket, destination: InetAddress, payload: ByteArray, port: Int = PORT) {
        socket.broadcast = true
        socket.send(DatagramPacket(payload, payload.size, destination, port))
    }
}
