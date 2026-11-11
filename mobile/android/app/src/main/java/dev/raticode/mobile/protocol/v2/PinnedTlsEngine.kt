package dev.raticode.mobile.protocol.v2

import androidx.annotation.RequiresApi
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.security.MessageDigest
import java.security.AlgorithmParameters
import java.security.interfaces.ECPublicKey
import java.security.spec.ECGenParameterSpec
import java.security.spec.ECParameterSpec
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import javax.net.ssl.KeyManager
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLEngineResult
import javax.net.ssl.SSLException
import javax.net.ssl.X509TrustManager

/** TLS feasibility boundary. No network, enrollment, durable receipt, or application dispatch. */
@RequiresApi(29)
class PinnedTlsEngine(
    client: Boolean,
    identity: Array<KeyManager>,
    peerSpkiSha256: ByteArray,
) {
    private val engine = SSLContext.getInstance("TLSv1.3").apply {
        // A new context per connection prevents reusing cached TLS sessions across routes.
        init(identity, arrayOf(PinnedPeerTrust(peerSpkiSha256)), null)
    }.createSSLEngine().apply {
        useClientMode = client
        enabledProtocols = arrayOf("TLSv1.3")
        sslParameters = sslParameters.apply { applicationProtocols = arrayOf("raticode/2") }
        if (!client) needClientAuth = true
        beginHandshake()
    }
    private val incoming = ByteBuffer.allocate(MAX_BUFFER)
    private var failed = false
    var authenticated: Boolean = false
        private set

    data class Progress(val encrypted: ByteArray, val plaintext: ByteArray)

    /** Caller owns scheduling and a handshake deadline. Serialize all calls. */
    @Synchronized
    fun step(encrypted: ByteArray = byteArrayOf(), plaintext: ByteArray = byteArrayOf()): Progress {
        check(!failed) { "TLS session is closed" }
        try {
            require(encrypted.size <= incoming.remaining()) { "TLS input limit" }
            require(plaintext.size <= MAX_PLAINTEXT) { "TLS output limit" }
            require(plaintext.isEmpty() || authenticated) { "TLS handshake incomplete" }
            incoming.put(encrypted)
            incoming.flip()
            val source = ByteBuffer.wrap(plaintext)
            val wire = ByteArrayOutputStream()
            val clear = ByteArrayOutputStream()
            val target = ByteBuffer.allocate(MAX_BUFFER)
            var stalled = false
            var iterations = 0
            while (!stalled) {
                check(++iterations <= 256) { "TLS progress limit" }
                val status = engine.handshakeStatus
                if (status == SSLEngineResult.HandshakeStatus.NEED_TASK) {
                    var task = engine.delegatedTask
                    while (task != null) { task.run(); task = engine.delegatedTask }
                    continue
                }
                if (status == SSLEngineResult.HandshakeStatus.NOT_HANDSHAKING && !authenticated) {
                    check(engine.session.protocol == "TLSv1.3")
                    check(engine.applicationProtocol == "raticode/2")
                    check(engine.session.peerCertificates.isNotEmpty())
                    authenticated = true
                }
                target.clear()
                val wrapping = status == SSLEngineResult.HandshakeStatus.NEED_WRAP ||
                    (status == SSLEngineResult.HandshakeStatus.NOT_HANDSHAKING && source.hasRemaining())
                val result = if (wrapping) engine.wrap(source, target) else {
                    if (!incoming.hasRemaining()) break
                    engine.unwrap(incoming, target)
                }
                when (result.status) {
                    SSLEngineResult.Status.CLOSED -> throw SSLException("TLS session closed")
                    SSLEngineResult.Status.BUFFER_OVERFLOW -> throw SSLException("TLS buffer limit")
                    SSLEngineResult.Status.BUFFER_UNDERFLOW -> stalled = true
                    SSLEngineResult.Status.OK -> Unit
                }
                target.flip()
                val bytes = ByteArray(target.remaining()).also { target.get(it) }
                if (wrapping) wire.write(bytes) else clear.write(bytes)
                if (wire.size() > MAX_BUFFER || clear.size() > MAX_BUFFER) throw SSLException("TLS flight limit")
                if (result.bytesConsumed() == 0 && result.bytesProduced() == 0 &&
                    result.handshakeStatus == status) stalled = true
            }
            check(!source.hasRemaining()) { "TLS output not consumed" }
            incoming.compact()
            return Progress(wire.toByteArray(), clear.toByteArray())
        } catch (failure: Exception) {
            failed = true
            authenticated = false
            incoming.clear()
            incoming.put(ByteArray(MAX_BUFFER))
            engine.closeOutbound()
            throw failure
        }
    }

    companion object {
        const val MAX_BUFFER = 65536
        const val MAX_PLAINTEXT = 16384
        fun pin(certificate: X509Certificate): ByteArray =
            MessageDigest.getInstance("SHA-256").digest(certificate.publicKey.encoded)
    }
}

/** A configured self-signed device identity, never system CAs or trust-on-first-use. */
@RequiresApi(29)
internal class PinnedPeerTrust(expected: ByteArray) : X509TrustManager {
    private val pin = expected.copyOf().also { require(it.size == 32) }
    override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
    override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) = checkPeer(chain)
    override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) = checkPeer(chain)

    private fun checkPeer(chain: Array<X509Certificate>) {
        if (chain.size != 1) throw CertificateException("Expected one device certificate")
        val certificate = chain.single()
        certificate.checkValidity()
        val key = certificate.publicKey as? ECPublicKey ?: throw CertificateException("Expected EC identity")
        val p256 = AlgorithmParameters.getInstance("EC").apply { init(ECGenParameterSpec("secp256r1")) }
            .getParameterSpec(ECParameterSpec::class.java)
        if (key.params.curve != p256.curve || key.params.generator != p256.generator ||
            key.params.order != p256.order || key.params.cofactor != p256.cofactor) {
            throw CertificateException("Expected P-256 identity")
        }
        if (!MessageDigest.isEqual(pin, PinnedTlsEngine.pin(certificate))) {
            throw CertificateException("Device identity mismatch")
        }
        certificate.verify(certificate.publicKey)
    }
}
