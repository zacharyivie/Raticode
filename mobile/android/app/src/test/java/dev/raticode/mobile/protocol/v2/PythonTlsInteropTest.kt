package dev.raticode.mobile.protocol.v2

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File
import java.util.Base64

/** Runs against this checkout's desktop source. RATICODE_DESKTOP_PROBE_ROOT names another one and requires it to work. */
class PythonTlsInteropTest {
    private val fixtures = TlsProbeChecks { name -> javaClass.classLoader!!.getResourceAsStream(name)!!.use { it.readBytes() } }
    private val b64 = Base64.getEncoder()
    private val decoder = Base64.getDecoder()

    private fun python(client: Boolean, wrongPin: Boolean = false, identity: String = "desktop", alpn: String = "correct"): Process {
        val root = System.getenv("RATICODE_DESKTOP_PROBE_ROOT")
        // Unit tests run from mobile/android/app. Never search upward: from a worktree that finds the main checkout.
        val directory = if (root != null) File(root) else File("../../..").canonicalFile
        if (root == null) {
            assumeTrue("Needs desktop source", File(directory, "scripts/device_tls_probe.py").isFile)
            assumeTrue("Needs the repository .venv", File(directory, ".venv/bin/python").canExecute())
        }
        return ProcessBuilder(File(directory, ".venv/bin/python").path, "-u", "scripts/device_tls_probe.py",
            "--role", if (client) "client" else "server", "--identity", identity, "--peer",
            if (wrongPin) "other" else "mobile", "--alpn", alpn).directory(directory).apply {
            environment()["PYTHONPATH"] = File(directory, "src").path
            redirectError(ProcessBuilder.Redirect.INHERIT)
        }.start()
    }

    private fun exercise(client: Boolean, replay: Boolean = false, tamper: Boolean = false) {
        val kotlin = PinnedTlsEngine(client, fixtures.identity("mobile"), PinnedTlsEngine.pin(fixtures.certificate("desktop")))
        val process = python(!client)
        try {
            val input = process.inputStream.bufferedReader()
            val output = process.outputStream.bufferedWriter()
            fun exchange(bytes: ByteArray, send: ByteArray = byteArrayOf()): JSONObject {
                output.write(JSONObject().put("feed", b64.encodeToString(bytes)).put("send", b64.encodeToString(send)).toString())
                output.newLine(); output.flush()
                return JSONObject(requireNotNull(input.readLine()) { "Python probe ended" })
            }
            var wire = kotlin.step().encrypted
            var complete = false
            for (step in 0..20) {
                val response = exchange(wire)
                assertFalse(response.has("error"))
                wire = kotlin.step(decoder.decode(response.getString("ciphertext"))).encrypted
                if (kotlin.authenticated && response.getBoolean("handshake") && wire.isEmpty()) {
                    complete = true
                    break
                }
            }
            assertTrue("Cross-language mutual TLS handshake", complete)
            val message = "public probe Kotlin to Python".toByteArray()
            val ciphertext = kotlin.step(plaintext = message).encrypted
            if (tamper) {
                ciphertext[ciphertext.lastIndex] = (ciphertext.last().toInt() xor 1).toByte()
                assertTrue(exchange(ciphertext).has("error"))
                return
            }
            val reply = "public probe Python to Kotlin".toByteArray()
            val result = exchange(ciphertext, reply)
            val plain = result.getJSONArray("plaintext")
            assertArrayEquals(message, (0 until plain.length()).flatMap { decoder.decode(plain.getString(it)).toList() }.toByteArray())
            assertArrayEquals(reply, kotlin.step(decoder.decode(result.getString("ciphertext"))).plaintext)
            if (replay) assertTrue(exchange(ciphertext).has("error"))
        } finally { process.destroyForcibly(); process.waitFor() }
    }

    @Test(timeout = 30000) fun kotlinClientAndPythonServer() = exercise(true)
    @Test(timeout = 30000) fun pythonClientAndKotlinServer() = exercise(false)
    @Test(timeout = 30000) fun pythonRejectsTlsReplay() = exercise(true, replay = true)
    @Test(timeout = 30000) fun pythonRejectsTlsTampering() = exercise(true, tamper = true)

    private fun rejectedHandshake(identity: String = "desktop", alpn: String = "correct", wrongPin: Boolean = false) {
        val kotlin = PinnedTlsEngine(true, fixtures.identity("mobile"), PinnedTlsEngine.pin(fixtures.certificate(identity)))
        val process = python(false, wrongPin, identity, alpn)
        try {
            val input = process.inputStream.bufferedReader()
            val output = process.outputStream.bufferedWriter()
            var wire = kotlin.step().encrypted
            var rejected = false
            for (step in 0..20) {
                output.write(JSONObject().put("feed", b64.encodeToString(wire)).toString())
                output.newLine(); output.flush()
                val response = JSONObject(requireNotNull(input.readLine()) { "Probe failed before TLS result" })
                if (response.has("error")) { rejected = true; break }
                try { wire = kotlin.step(decoder.decode(response.getString("ciphertext"))).encrypted }
                catch (_: javax.net.ssl.SSLException) { rejected = true; break }
                catch (_: IllegalStateException) { rejected = true; break }
            }
            assertTrue("Invalid authenticated peer was accepted", rejected)
        } finally { process.destroyForcibly(); process.waitFor() }
    }
    @Test(timeout = 30000) fun wrongAlpnFails() = rejectedHandshake(alpn = "wrong")
    @Test(timeout = 30000) fun absentAlpnFails() = rejectedHandshake(alpn = "none")
    @Test(timeout = 30000) fun expiredPinnedCertificateFails() = rejectedHandshake(identity = "expired")
    @Test(timeout = 30000) fun p384PinnedCertificateFails() = rejectedHandshake(identity = "p384")
    @Test(timeout = 30000) fun pythonRejectsWrongMobilePin() = rejectedHandshake(wrongPin = true)
}
