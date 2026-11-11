package dev.raticode.mobile.live

import android.content.Context
import android.net.ConnectivityManager
import android.net.LinkProperties
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.RouteInfo
import android.os.Build
import org.json.JSONObject
import java.net.Inet4Address
import java.net.Inet6Address
import java.net.InetAddress
import java.net.Socket
import java.util.concurrent.ConcurrentHashMap

internal const val TAILSCALE_UNAVAILABLE = "Tailscale is not connected on this phone. Open the Tailscale app, sign in and turn it on, then retry. Raticode never sends desktop traffic over mobile data outside Tailscale."
internal const val TAILSCALE_OFF = "This desktop address is on Tailscale. Turn on Connect through Tailscale in Desktop settings, or connect both devices to the same Wi-Fi."
internal const val TAILSCALE_NO_ADDRESS = "Connect this phone to the same Wi-Fi as your desktop, or enter the desktop's Tailscale address in Desktop settings."

/** The phone network a desktop address must use. Neither path falls back to the other's network. */
enum class DevicePath(val label: String) { WIFI("Wi-Fi"), TAILSCALE("Tailscale") }

/** A validated desktop socket address. Tailscale addresses go only through Tailscale; private ones only through Wi-Fi. */
data class DeviceEndpoint(val host: String, val port: Int, val path: DevicePath) {
    fun json(): JSONObject = JSONObject().put("host", host).put("port", port)
}

/** Tailscale's documented ranges: IPv4 100.64.0.0/10 and IPv6 fd7a:115c:a1e0::/48. */
object TailnetAddress {
    private val v6Prefix = byteArrayOf(0xfd.toByte(), 0x7a, 0x11, 0x5c, 0xa1.toByte(), 0xe0.toByte())
    fun contains(address: InetAddress): Boolean = when (address) {
        is Inet4Address -> address.address.let { it[0] == 100.toByte() && (it[1].toInt() and 0xc0) == 0x40 }
        is Inet6Address -> address.address.copyOfRange(0, 6).contentEquals(v6Prefix)
        else -> false
    }
}

/** A route prefix copied from Android link properties, so matching is testable without the framework. */
data class IpRoute(val prefix: InetAddress, val length: Int, val unicast: Boolean = true) {
    fun matches(address: InetAddress): Boolean {
        val a = prefix.address; val b = address.address
        if (a.size != b.size || length !in 0..a.size * 8) return false
        val whole = length / 8; val rest = length % 8
        for (i in 0 until whole) if (a[i] != b[i]) return false
        if (rest == 0) return true
        val mask = (0xff shl (8 - rest)) and 0xff
        return (a[whole].toInt() and mask) == (b[whole].toInt() and mask)
    }
    companion object {
        /** Longest-prefix match. An excluded (non-unicast) route means the network does not carry the address. */
        fun carries(routes: List<IpRoute>, address: InetAddress): Boolean =
            routes.filter { it.matches(address) }.maxByOrNull { it.length }?.unicast == true
    }
}

internal object DeviceRoutes {
    /** Parses a stored endpoint, or null when it is missing or no longer valid. */
    fun endpoint(value: JSONObject?): DeviceEndpoint? = value?.let { runCatching { DeviceInvitation.validateDesktopEndpoint(it) }.getOrNull() }

    fun tailscaleEnabled(state: JSONObject) = state.optJSONObject("tailscale")?.optBoolean("enabled") == true

    /** The user's chosen Tailscale address wins over one the desktop reported; neither is erased by a Wi-Fi snapshot. */
    fun tailnetEndpoint(state: JSONObject, peer: JSONObject?): DeviceEndpoint? =
        endpoint(state.optJSONObject("tailscale")?.optJSONObject("endpoint"))?.takeIf { it.path == DevicePath.TAILSCALE }
            ?: endpoint(peer?.optJSONObject("tailnet_endpoint"))?.takeIf { it.path == DevicePath.TAILSCALE }

    /**
     * Applies addresses from an authenticated `sync.snapshot` connection block. A Wi-Fi address
     * replaces a manual one; null keeps the working address. A Tailscale address is kept apart,
     * so it never erases the Wi-Fi route or the address the user chose. A null
     * `tailnet_endpoint` means the desktop stopped listening on Tailscale.
     */
    fun applyReport(state: JSONObject, connection: JSONObject) {
        val peer = state.optJSONObject("peer") ?: return
        connection.optJSONObject("lan_endpoint")?.let { reported ->
            if (endpoint(reported)?.path == DevicePath.TAILSCALE) peer.put("tailnet_endpoint", reported)
            else { peer.put("lan_endpoint", reported); state.remove("lan_override") }
        }
        if (connection.has("tailnet_endpoint")) {
            val reported = connection.optJSONObject("tailnet_endpoint")
            if (reported == null) peer.remove("tailnet_endpoint") else peer.put("tailnet_endpoint", reported)
        }
    }

    /** Asked for only while the switch is on, because desktops without Tailscale support reject the field. */
    fun requestFields(state: JSONObject, request: JSONObject): JSONObject =
        if (tailscaleEnabled(state)) request.put("include_tailnet_endpoint", true) else request

    /**
     * Removes queued sync requests that ask for the Tailscale address. An older desktop closes the
     * session on them without answering, and the outbox resends everything on reconnect, so one
     * left behind would block Wi-Fi sync too. They are read-only, so dropping one is safe; the
     * desktop needs only unique sequence numbers, and the next sync queues a clean request.
     * Returns true when the outbox changed.
     */
    fun dropTailnetRequests(state: JSONObject): Boolean {
        val outbox = state.optJSONArray("outbox") ?: return false
        val kept = org.json.JSONArray()
        for (i in 0 until outbox.length()) {
            val event = JSONObject(outbox.getString(i))
            if (event.optString("type") == "sync.request" && event.optJSONObject("payload")?.has("include_tailnet_endpoint") == true) continue
            kept.put(outbox.getString(i))
        }
        if (kept.length() == outbox.length()) return false
        state.put("outbox", kept)
        return true
    }

    /**
     * Ordered addresses to try. Wi-Fi comes first unless Tailscale was the last route that worked
     * on this Wi-Fi network. Throws fixed guidance when no route may be used.
     */
    fun plan(state: JSONObject, peer: JSONObject?, wifiAvailable: Boolean, preferTailscale: Boolean = false): List<DeviceEndpoint> {
        val lan = endpoint(state.optJSONObject("lan_override")) ?: endpoint(peer?.optJSONObject("lan_endpoint"))
        val tailscaleOn = tailscaleEnabled(state)
        val wifi = listOfNotNull(lan?.takeIf { it.path == DevicePath.WIFI && wifiAvailable })
        val tailnet = if (!tailscaleOn) emptyList() else
            listOfNotNull(tailnetEndpoint(state, peer), lan?.takeIf { it.path == DevicePath.TAILSCALE }).distinct()
        val plan = if (preferTailscale) tailnet + wifi else wifi + tailnet
        if (plan.isNotEmpty()) return plan
        throw ConnectionFailure(when {
            lan == null && tailnetEndpoint(state, peer) == null -> NO_ADDRESS_GUIDANCE
            !tailscaleOn && lan?.path == DevicePath.TAILSCALE -> TAILSCALE_OFF
            tailscaleOn -> TAILSCALE_NO_ADDRESS
            else -> NO_WIFI_GUIDANCE
        })
    }
}

/**
 * Tracks VPN networks that route Tailscale addresses. A socket is bound to such a network only
 * when its routes carry the exact desktop address, so a missing or excluded tunnel fails closed
 * instead of using mobile data or Wi-Fi directly.
 */
class TailscaleRoute(context: Context, private val onBoundLost: () -> Unit = {}, private val onAvailable: () -> Unit = {}) : AutoCloseable {
    private val connectivity = context.getSystemService(ConnectivityManager::class.java)
    private val routes = ConcurrentHashMap<Network, List<IpRoute>>()
    @Volatile private var bound: Network? = null
    private val callback = object : ConnectivityManager.NetworkCallback() {
        override fun onLinkPropertiesChanged(network: Network, properties: LinkProperties) {
            val before = available
            routes[network] = properties.routes.map { route ->
                IpRoute(route.destination.address, route.destination.prefixLength,
                    // VPNs can exclude routes only from Android 13, so older routes are all unicast.
                    Build.VERSION.SDK_INT < 33 || route.type == RouteInfo.RTN_UNICAST)
            }
            if (!before && available) onAvailable()
        }
        // Tailscale rebuilds its VPN when its settings change; a socket on the old one is dead.
        override fun onLost(network: Network) { routes.remove(network); if (network == bound) onBoundLost() }
    }
    init {
        // Default requests exclude VPNs; this one asks only for them. Apps excluded from the
        // VPN never see it here.
        connectivity.registerNetworkCallback(NetworkRequest.Builder()
            .addTransportType(NetworkCapabilities.TRANSPORT_VPN)
            .removeCapability(NetworkCapabilities.NET_CAPABILITY_NOT_VPN)
            .removeCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            .build(), callback)
    }
    val available: Boolean get() = routes.values.any { list -> list.any { it.unicast && TailnetAddress.contains(it.prefix) } }
    fun bind(socket: Socket, host: String) {
        val address = InetAddress.getByName(host)
        check(TailnetAddress.contains(address))
        val network = routes.entries.firstOrNull { IpRoute.carries(it.value, address) }?.key
            ?: throw ConnectionFailure(TAILSCALE_UNAVAILABLE)
        network.bindSocket(socket)
        bound = network
    }
    override fun close() { runCatching { connectivity.unregisterNetworkCallback(callback) } }
}
