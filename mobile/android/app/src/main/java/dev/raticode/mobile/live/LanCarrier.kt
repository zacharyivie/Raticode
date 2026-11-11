package dev.raticode.mobile.live

import java.io.DataOutputStream
import java.io.EOFException
import java.net.InetSocketAddress
import java.net.Socket
import java.net.SocketTimeoutException

/**
 * Wi-Fi answers at once or not at all. A Tailscale path that has been idle, for example after the
 * phone wakes, may first need a tunnel handshake or a relay, which can take several seconds.
 */
internal fun connectTimeout(path: DevicePath) = if (path == DevicePath.TAILSCALE) 10_000 else 4000

/** Preserve partial TCP packets across polling ticks, with a bounded frame deadline. */
class LanCarrier(host: String, port: Int, path: DevicePath = DevicePath.WIFI, bindSocket: (Socket) -> Unit) : DeviceCarrier {
    private val socket = Socket().apply {
        try { bindSocket(this); connect(InetSocketAddress(host, port), connectTimeout(path)); soTimeout = 1000 }
        catch (failure: ConnectionFailure) { close(); throw failure }
        catch (failure: Exception) {
            close()
            throw ConnectionFailure(connectionGuidance(ConnectionStage.CONNECTING, failure, path))
        }
    }
    private val input = socket.getInputStream()
    private val output = DataOutputStream(socket.getOutputStream())
    private val header = ByteArray(4)
    private var headerSize = 0
    private var body: ByteArray? = null
    private var bodySize = 0
    private var frameStarted = 0L

    override fun send(bytes: ByteArray) {
        require(bytes.size in 1..3072)
        output.writeInt(bytes.size); output.write(bytes); output.flush()
    }

    override fun receive(): ByteArray? {
        try {
            while (true) {
                if (headerSize > 0 && System.nanoTime() - frameStarted >= 30_000_000_000L)
                    throw java.io.IOException("LAN frame deadline exceeded")
                val target = body ?: header
                val offset = if (body == null) headerSize else bodySize
                val count = input.read(target, offset, target.size - offset)
                if (count < 0) throw EOFException("Desktop disconnected")
                if (body == null) {
                    if (headerSize == 0) frameStarted = System.nanoTime()
                    headerSize += count
                    if (headerSize == 4) {
                        val size = java.nio.ByteBuffer.wrap(header).int
                        require(size in 1..3072)
                        body = ByteArray(size)
                    }
                } else {
                    bodySize += count
                    if (bodySize == target.size) {
                        headerSize = 0; bodySize = 0; body = null
                        return target
                    }
                }
            }
        } catch (_: SocketTimeoutException) {
            return null
        }
    }

    override fun close() = socket.close()
}
