package dev.raticode.mobile.live

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.LinkProperties
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import java.net.DatagramSocket
import java.net.Inet4Address
import java.net.Socket
import java.util.concurrent.ConcurrentHashMap

internal const val NO_WIFI_GUIDANCE = "Connect this phone to the same Wi-Fi network as your desktop. Raticode connects only over Wi-Fi."

/**
 * Binds desktop sockets to a connected Wi-Fi network, so device traffic never uses
 * mobile data or a VPN. The default request excludes VPNs; internet access is not required.
 */
class WifiRoute(context: Context, private val onLinks: () -> Unit = {}, private val onBoundLost: () -> Unit = {}, private val onAvailable: () -> Unit = {}) : AutoCloseable {
    private val connectivity = context.getSystemService(ConnectivityManager::class.java)
    private val networks = ConcurrentHashMap.newKeySet<Network>()
    /** This phone's own addresses on each Wi-Fi network, for the Wake computer subnet check. */
    private val links = ConcurrentHashMap<Network, List<IpRoute>>()
    @Volatile private var bound: Network? = null
    private val callback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) { networks.add(network); onAvailable() }
        // A socket on a lost network is dead even if no error has arrived yet.
        override fun onLost(network: Network) { networks.remove(network); links.remove(network); if (network == bound) onBoundLost(); onLinks() }
        override fun onLinkPropertiesChanged(network: Network, properties: LinkProperties) {
            links[network] = properties.linkAddresses.map { IpRoute(it.address, it.prefixLength) }
            onLinks()
        }
    }
    init {
        connectivity.registerNetworkCallback(NetworkRequest.Builder()
            .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
            .removeCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            .build(), callback)
    }
    val available: Boolean get() = networks.isNotEmpty()
    fun bind(socket: Socket) {
        val network = networks.firstOrNull() ?: throw ConnectionFailure(NO_WIFI_GUIDANCE)
        network.bindSocket(socket)
        bound = network
    }
    private fun linkTo(desktop: Inet4Address) = links.entries.firstNotNullOfOrNull { (network, addresses) ->
        addresses.firstOrNull { WakeOnLan.sameSubnet(it, desktop) }?.let { network to it }
    }
    fun sharesSubnetWith(desktop: Inet4Address): Boolean = linkTo(desktop) != null
    /** Sends [payload] once to the desktop subnet's broadcast address, on the matching Wi-Fi network and no other route. */
    fun broadcast(desktop: Inet4Address, payload: ByteArray): Boolean {
        val (network, link) = linkTo(desktop) ?: return false
        DatagramSocket().use { socket ->
            network.bindSocket(socket)
            WakeOnLan.send(socket, WakeOnLan.broadcast(link), payload)
        }
        return true
    }
    override fun close() { runCatching { connectivity.unregisterNetworkCallback(callback) } }
}
