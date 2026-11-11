package dev.raticode.mobile.protocol.v2

import org.junit.Assert.*
import java.io.ByteArrayInputStream
import java.security.KeyFactory
import java.security.KeyStore
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import java.security.spec.PKCS8EncodedKeySpec
import java.util.Base64
import javax.net.ssl.KeyManager
import javax.net.ssl.KeyManagerFactory

/** Public fixture keys only. Never referenced by the application source set. */
class TlsProbeChecks(private val read: (String) -> ByteArray) {
    fun certificate(name: String): X509Certificate = CertificateFactory.getInstance("X.509")
        .generateCertificate(ByteArrayInputStream(read("$name.cert.pem"))) as X509Certificate

    fun identity(name: String): Array<KeyManager> {
        val pem = read("$name.test-key.pem").toString(Charsets.US_ASCII)
        val der = Base64.getMimeDecoder().decode(pem.substringAfter("-----BEGIN PRIVATE KEY-----")
            .substringBefore("-----END PRIVATE KEY-----"))
        val key = KeyFactory.getInstance("EC").generatePrivate(PKCS8EncodedKeySpec(der))
        val store = KeyStore.getInstance(KeyStore.getDefaultType()).apply {
            load(null)
            setKeyEntry(name, key, "test".toCharArray(), arrayOf(certificate(name)))
        }
        return KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm()).apply {
            init(store, "test".toCharArray())
        }.keyManagers
    }

    fun peer(client: Boolean, wrongPin: Boolean = false): PinnedTlsEngine = PinnedTlsEngine(
        client, identity(if (client) "mobile" else "desktop"),
        if (wrongPin) ByteArray(32) else PinnedTlsEngine.pin(certificate(if (client) "desktop" else "mobile")),
    )

    fun connect(client: PinnedTlsEngine, server: PinnedTlsEngine, fragment: Int = 2048) {
        var toServer = client.step().encrypted
        var toClient = byteArrayOf()
        repeat(30) {
            toServer.toList().chunked(fragment).forEach {
                val response = server.step(it.toByteArray())
                assertEquals(0, response.plaintext.size)
                toClient += response.encrypted
            }
            toServer = byteArrayOf()
            toClient.toList().chunked(fragment).forEach {
                val response = client.step(it.toByteArray())
                assertEquals(0, response.plaintext.size)
                toServer += response.encrypted
            }
            toClient = byteArrayOf()
            if (client.authenticated && server.authenticated && toServer.isEmpty()) return
        }
        error("TLS handshake failed to finish")
    }

    fun roundTrip() {
        val client = peer(true); val server = peer(false)
        connect(client, server, 1)
        val message = "public TLS test: Rem".toByteArray()
        val encrypted = client.step(plaintext = message).encrypted
        assertFalse(encrypted.toString(Charsets.UTF_8).contains("Rem"))
        assertArrayEquals(message, server.step(encrypted).plaintext)
        assertArrayEquals(message, client.step(server.step(plaintext = message).encrypted).plaintext)
    }

    fun wrongPin() {
        assertFails { connect(peer(true, true), peer(false)) }
        assertFails { connect(peer(true), peer(false, true)) }
    }

    fun tamperAndReplay() {
        val client = peer(true); val server = peer(false)
        connect(client, server)
        val ciphertext = client.step(plaintext = "test".toByteArray()).encrypted
        assertEquals("test", server.step(ciphertext).plaintext.toString(Charsets.UTF_8))
        assertFails { server.step(ciphertext) }
        assertFalse(server.authenticated)
        assertFails { server.step() }
        val secondClient = peer(true); val secondServer = peer(false)
        connect(secondClient, secondServer)
        val altered = secondClient.step(plaintext = "test".toByteArray()).encrypted
        altered[altered.lastIndex] = (altered.last().toInt() xor 1).toByte()
        assertFails { secondServer.step(altered) }
        val freshClient = peer(true); val freshServer = peer(false)
        connect(freshClient, freshServer)
        assertFails { freshServer.step(ciphertext) }
    }

    fun boundsAndPrematureData() {
        assertFails { peer(true).step(plaintext = "test".toByteArray()) }
        assertFails { peer(true).step(ByteArray(PinnedTlsEngine.MAX_BUFFER + 1)) }
    }

    fun invalidCertificateProfiles() {
        for (name in listOf("expired", "p384")) {
            val certificate = certificate(name)
            val trust = PinnedPeerTrust(PinnedTlsEngine.pin(certificate))
            assertFails { trust.checkServerTrusted(arrayOf(certificate), "EC") }
            assertFails { trust.checkClientTrusted(arrayOf(certificate), "EC") }
        }
        val certificate = certificate("desktop")
        assertFails { PinnedPeerTrust(PinnedTlsEngine.pin(certificate)).checkServerTrusted(arrayOf(certificate, certificate), "EC") }
    }

    fun maximumRecordsThroughTls() {
        val client = peer(true); val server = peer(false)
        connect(client, server)
        val maximum = ("{\"v\":\"" + "a".repeat(65528) + "\"}").toByteArray()
        val framed = TlsJsonRecords.encode(maximum) + TlsJsonRecords.encode("{\"v\":1}".toByteArray())
        val ciphertext = java.io.ByteArrayOutputStream()
        framed.toList().chunked(PinnedTlsEngine.MAX_PLAINTEXT).forEach {
            ciphertext.write(client.step(plaintext = it.toByteArray()).encrypted)
        }
        val decoder = TlsJsonRecords()
        val records = mutableListOf<org.json.JSONObject>()
        ciphertext.toByteArray().toList().chunked(2048).forEach {
            records.addAll(decoder.feed(server.step(it.toByteArray()).plaintext))
        }
        decoder.endOfStream()
        assertEquals(2, records.size)
        assertEquals(65528, records[0].getString("v").length)
        assertEquals(1, records[1].getInt("v"))
    }

    private fun assertFails(block: () -> Unit) {
        var rejected = false
        try { block() } catch (_: Exception) { rejected = true }
        assertTrue("Invalid TLS input was accepted", rejected)
    }
}
