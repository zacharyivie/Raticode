package dev.raticode.mobile.protocol.v2

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.net.Socket
import java.util.Base64
import javax.net.ssl.SSLContext

/** Requires the explicit loopback-only fixture bridge and adb reverse; never a user desktop. */
@RunWith(AndroidJUnit4::class)
class PythonTlsDeviceTest {
    private val fixtures = TlsProbeChecks { name ->
        InstrumentationRegistry.getInstrumentation().context.assets.open(name).use { it.readBytes() }
    }
    @Test fun androidClientToPython() = exercise(true)
    @Test fun pythonClientToAndroid() = exercise(false)
    @Test fun androidRejectsAbsentAlpn() = exercise(true, "none", true)
    @Test fun androidRejectsWrongAlpn() = exercise(true, "wrong", true)

    private fun exercise(client: Boolean, alpn: String = "correct", expectRejection: Boolean = false) {
        assumeTrue("Start the public-fixture bridge explicitly", InstrumentationRegistry.getArguments().getString("interop") == "true")
        println("TLS provider=${SSLContext.getInstance("TLSv1.3").provider.name}; API=${android.os.Build.VERSION.SDK_INT}")
        val tls = PinnedTlsEngine(client, fixtures.identity("mobile"), PinnedTlsEngine.pin(fixtures.certificate("desktop")))
        val encoder = Base64.getEncoder(); val decoder = Base64.getDecoder()
        Socket("127.0.0.1", 38643).use { socket ->
            socket.soTimeout = 15000
            val input = socket.getInputStream().bufferedReader()
            val output = socket.getOutputStream().bufferedWriter()
            output.write(JSONObject().put("role", if (client) "server" else "client").put("alpn", alpn).toString())
            output.newLine(); output.flush()
            fun exchange(bytes: ByteArray, send: ByteArray = byteArrayOf()): JSONObject {
                output.write(JSONObject().put("feed", encoder.encodeToString(bytes)).put("send", encoder.encodeToString(send)).toString())
                output.newLine(); output.flush()
                return JSONObject(requireNotNull(input.readLine()))
            }
            var wire = tls.step().encrypted
            var complete = false
            for (step in 0..20) {
                val result = exchange(wire)
                if (expectRejection && result.has("error")) return@use
                assertFalse(result.has("error"))
                try { wire = tls.step(decoder.decode(result.getString("ciphertext"))).encrypted }
                catch (error: javax.net.ssl.SSLException) { if (expectRejection) return@use else throw error }
                catch (error: IllegalStateException) { if (expectRejection) return@use else throw error }
                if (tls.authenticated && result.getBoolean("handshake") && wire.isEmpty()) { complete = true; break }
            }
            assertTrue(complete)
            assertFalse("Invalid ALPN completed authentication", expectRejection)
            val message = "public Android TLS probe".toByteArray()
            val ciphertext = tls.step(plaintext = message).encrypted
            val result = exchange(ciphertext, message)
            assertArrayEquals(message, decoder.decode(result.getJSONArray("plaintext").getString(0)))
            assertArrayEquals(message, tls.step(decoder.decode(result.getString("ciphertext"))).plaintext)
            assertTrue("Python rejects replay", exchange(ciphertext).has("error"))
        }
    }
}
