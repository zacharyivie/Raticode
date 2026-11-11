package dev.raticode.mobile.live

import org.junit.Assert.*
import org.junit.Test
import java.net.ServerSocket
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

class LanCarrierTest {
    @Test fun refusedSocketReportsReachabilityBeforeAuthentication() {
        val port = ServerSocket(0).use { it.localPort }
        val failure = assertThrows(ConnectionFailure::class.java) { LanCarrier("127.0.0.1", port) {} }
        assertTrue(failure.guidance.contains("Desktop address could not be reached"))
        assertFalse(failure.guidance.contains("authentication"))
    }

    @Test fun missingWifiStopsBeforeAnySocketConnects() {
        ServerSocket(0).use { server ->
            server.soTimeout = 300
            val failure = assertThrows(ConnectionFailure::class.java) {
                LanCarrier("127.0.0.1", server.localPort) { throw ConnectionFailure(NO_WIFI_GUIDANCE) }
            }
            assertEquals(NO_WIFI_GUIDANCE, failure.guidance)
            assertThrows(java.net.SocketTimeoutException::class.java) { server.accept() }
        }
    }

    @Test fun delayedHeaderAndBodySurvivePollingTimeoutsWithoutLosingNextFrame() {
        ServerSocket(0).use { server ->
            val worker = Executors.newSingleThreadExecutor()
            try {
                val sent = worker.submit {
                    server.accept().use { socket ->
                        val output = socket.getOutputStream()
                        output.write(byteArrayOf(0)); output.flush()
                        Thread.sleep(1300)
                        output.write(byteArrayOf(0, 0, 3, 11)); output.flush()
                        Thread.sleep(1300)
                        output.write(byteArrayOf(22, 33, 0, 0, 0, 1, 44)); output.flush()
                    }
                }
                LanCarrier("127.0.0.1", server.localPort) {}.use { carrier ->
                    assertNull(carrier.receive())
                    assertNull(carrier.receive())
                    assertArrayEquals(byteArrayOf(11, 22, 33), carrier.receive())
                    assertArrayEquals(byteArrayOf(44), carrier.receive())
                }
                sent.get(5, TimeUnit.SECONDS)
            } finally { worker.shutdownNow() }
        }
    }

    @Test fun oversizedFrameIsRejectedBeforeReadingItsBody() {
        ServerSocket(0).use { server ->
            val worker = Executors.newSingleThreadExecutor()
            try {
                val sent = worker.submit {
                    server.accept().use { socket ->
                        java.io.DataOutputStream(socket.getOutputStream()).writeInt(3073)
                    }
                }
                LanCarrier("127.0.0.1", server.localPort) {}.use { carrier ->
                    assertThrows(IllegalArgumentException::class.java) { carrier.receive() }
                }
                sent.get(5, TimeUnit.SECONDS)
            } finally { worker.shutdownNow() }
        }
    }
}
