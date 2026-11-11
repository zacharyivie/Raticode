package dev.raticode.mobile.live

import android.os.SystemClock
import androidx.annotation.RequiresApi
import dev.raticode.mobile.protocol.v2.*
import dev.raticode.mobile.protocol.*
import org.json.JSONObject
import java.security.MessageDigest
import java.util.UUID

interface DeviceCarrier : AutoCloseable {
    fun send(bytes: ByteArray)
    fun receive(): ByteArray?
}

/**
 * One ephemeral, pinned end-to-end session over Wi-Fi or Tailscale. The route changes only the
 * socket address; TLS pins and pairing are identical on both. No TLS state survives reconnects.
 */
@RequiresApi(29)
class DeviceConnection(
    private val store: DeviceStore,
    private val state: JSONObject,
    private val invitation: DeviceInvitation?,
    private val bindSocket: (java.net.Socket) -> Unit,
    private val status: (String) -> Unit,
    carrierFactory: (() -> DeviceCarrier)? = null,
    target: DeviceEndpoint? = null,
) : AutoCloseable {
    var bindingSent = false; private set
    internal var beforeReadyPersist: (() -> Unit)? = null
    private val identity = store.identity(state)
    private val peer = invitation?.json ?: state.optJSONObject("peer") ?: state.getJSONObject("pending_peer")
    init {
        if (invitation != null) {
            val pending = JSONObject(peer.toString()).apply {
                remove("secret")
                put("initiator_topic", getString("pair_initiator_topic")); put("responder_topic", getString("pair_responder_topic"))
            }
            val encoded = java.util.Base64.getUrlEncoder().withoutPadding().encodeToString(invitation.bytes)
            val next = JSONObject(state.toString()).put("pending_peer", pending).put("pending_invitation", encoded)
            store.save(next); state.put("pending_peer", pending).put("pending_invitation", encoded)
        }
    }
    private val mode = if (invitation == null) "resume" else "pair"
    private val session = UUID.randomUUID().toString()
    private val pin = peer.getString("responder_spki_sha256").chunked(2).map { it.toInt(16).toByte() }.toByteArray()
    private val engine = PinnedTlsEngine(true, identity, pin)
    private val reorder = OpaqueTlsReorder(session, mode, "responder_to_initiator")
    private val records = TlsJsonRecords()
    private val incoming = java.util.ArrayDeque<JSONObject>()
    @Volatile private var closed = false
    private var index = 0L
    private var stage = ConnectionStage.AUTHENTICATION
    // A user-supplied route changes only the socket address, never the QR bytes or pin.
    private val endpoint = target ?: DeviceRoutes.endpoint(state.optJSONObject("lan_override"))
        ?: DeviceRoutes.endpoint(peer.optJSONObject("lan_endpoint"))
    private val path = endpoint?.path ?: DevicePath.WIFI
    // Legacy relay fields in older pairings are never contacted.
    private val carrier: DeviceCarrier = carrierFactory?.invoke() ?: endpoint?.let {
        LanCarrier(it.host, it.port, it.path, bindSocket)
    } ?: throw ConnectionFailure(NO_ADDRESS_GUIDANCE)
    val route = path.label
    private val watch = ReplyWatch()
    /** True when the desktop has not answered anything for a while after the phone sent. */
    fun stalled(now: Long = SystemClock.elapsedRealtime()) = watch.stalled(now)
    private fun pump(wire: ByteArray = byteArrayOf(), plain: ByteArray = byteArrayOf()) {
        val progress = engine.step(wire, plain)
        if (progress.encrypted.isNotEmpty()) watch.sent(SystemClock.elapsedRealtime())
        for (offset in progress.encrypted.indices step 2048) {
            val chunk = progress.encrypted.copyOfRange(offset, minOf(offset + 2048, progress.encrypted.size))
            carrier.send(OpaqueTlsFrame(session, mode, "initiator_to_responder", index++, chunk).encode())
        }
        incoming.addAll(records.feed(progress.plaintext))
    }
    private fun receiveUntil(deadline: Long): JSONObject {
        while (incoming.isEmpty()) {
            if (SystemClock.elapsedRealtime() >= deadline) throw java.net.SocketTimeoutException()
            val wire = carrier.receive()?.also { watch.heard() }
            reorder.expire(SystemClock.elapsedRealtime())
            if (wire != null) for (chunk in reorder.accept(wire, SystemClock.elapsedRealtime())) pump(chunk)
        }
        return incoming.removeFirst()
    }
    fun send(record: JSONObject) {
        val bytes = TlsJsonRecords.encode(record.toString().toByteArray())
        for (offset in bytes.indices step 16384) pump(plain = bytes.copyOfRange(offset, minOf(offset + 16384, bytes.size)))
    }
    fun poll(): JSONObject? {
        if (incoming.isNotEmpty()) return incoming.removeFirst()
        carrier.receive()?.let { frame -> watch.heard(); reorder.accept(frame, SystemClock.elapsedRealtime()).forEach { pump(it) } }
        reorder.expire(SystemClock.elapsedRealtime())
        return if (incoming.isEmpty()) null else incoming.removeFirst()
    }
    fun receive(timeoutMillis: Long = 30000) = receiveUntil(SystemClock.elapsedRealtime() + timeoutMillis)
    fun establish() {
        status("Authenticating desktop via $route")
        try { establishSession() }
        catch (failure: LiveStorageLimit) { throw failure }
        catch (failure: Exception) { throw ConnectionFailure(connectionGuidance(stage, failure, path)) }
    }
    private fun establishSession() {
        check(!closed)
        val deadline = SystemClock.elapsedRealtime() + 30000
        pump()
        while (!engine.authenticated) {
            if (SystemClock.elapsedRealtime() >= deadline) throw java.net.SocketTimeoutException()
            carrier.receive()?.let { frame -> reorder.accept(frame, SystemClock.elapsedRealtime()).forEach { pump(it) } }
            reorder.expire(SystemClock.elapsedRealtime())
        }
        stage = ConnectionStage.BINDING
        val binding = JSONObject().put("type", "session.bind").put("version", 2).put("profile", "raticode-tls13-v2")
            .put("session_id", session).put("mode", mode).put("initiator_id", state.getString("device_id"))
            .put("responder_id", peer.getString("responder_id")).put("initiator_role", "controller").put("responder_role", "desktop")
            .put("invitation_sha256", invitation?.let { hex(MessageDigest.getInstance("SHA-256").digest(it.bytes)) } ?: JSONObject.NULL)
            .put("secret", invitation?.json?.getString("secret") ?: JSONObject.NULL)
        val verifier = SessionBinding(binding.toString().toByteArray())
        send(binding)
        bindingSent = true
        verifier.verifyFirstRecord(receive().toString().toByteArray())
        var record = receive()
        if (record.getString("type") == "session.pending") {
            record.fields("type", "device_id", "expires_at")
            require(record.uuid("device_id") == state.getString("device_id"))
            require(record.integer("expires_at") > System.currentTimeMillis()/1000)
            status("Confirm this phone's identity on the desktop")
            stage = ConnectionStage.CONFIRMATION
            record = receive(300000)
        }
        stage = ConnectionStage.READY
        record.fields("type", "device_id", "role", "capabilities", "initiator_topic", "responder_topic", "trust_revision")
        check(record.getString("type") == "session.ready")
        require(record.integer("trust_revision") > 0)
        record.uuid("device_id")
        check(record.getString("device_id") == state.getString("device_id"))
        val caps = record.getJSONArray("capabilities")
        MobileCapabilities.validateGrant(record.getString("role"), (0 until caps.length()).map { caps.getString(it) }.toSet(), false)
        val saved = JSONObject(peer.toString()).apply {
            remove("secret"); remove("pair_initiator_topic"); remove("pair_responder_topic"); remove("invitation_id"); remove("expires_at")
            put("initiator_topic", record.getString("initiator_topic")); put("responder_topic", record.getString("responder_topic"))
            put("trust_revision", record.getLong("trust_revision")); put("capabilities", caps)
        }
        check(!closed)
        if (invitation == null && state.has("peer")) {
            for (field in listOf("initiator_topic", "responder_topic", "trust_revision", "capabilities"))
                require(saved.get(field).toString() == peer.get(field).toString()) { "Unexpected trust change" }
        }
        for (field in listOf("initiator_topic", "responder_topic")) {
            val token = saved.getString(field)
            require(token.matches(Regex("[A-Za-z0-9_-]{43}")))
            require(java.util.Base64.getUrlDecoder().decode(token).size == 32)
        }
        require(saved.getString("initiator_topic") != saved.getString("responder_topic"))
        beforeReadyPersist?.invoke()
        val next = JSONObject(state.toString()).put("peer", saved).apply { remove("pending_peer"); remove("pending_invitation") }
        store.save(next)
        state.put("peer", saved); state.remove("pending_peer"); state.remove("pending_invitation")
        send(JSONObject().put("type", "session.ready_ack").put("device_id", state.getString("device_id")).put("trust_revision", record.getLong("trust_revision")))
        status("Authenticated desktop via $route")
    }
    override fun close() { closed = true; carrier.close() }
    companion object { private fun hex(bytes: ByteArray) = bytes.joinToString("") { "%02x".format(it) } }
}
