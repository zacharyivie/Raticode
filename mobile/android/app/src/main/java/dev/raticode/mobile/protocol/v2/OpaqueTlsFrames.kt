package dev.raticode.mobile.protocol.v2

import dev.raticode.mobile.protocol.StrictJson
import dev.raticode.mobile.protocol.fields
import dev.raticode.mobile.protocol.integer
import dev.raticode.mobile.protocol.string
import dev.raticode.mobile.protocol.uuid
import org.json.JSONObject
import java.nio.ByteBuffer
import java.security.MessageDigest
import java.util.Base64
import java.util.TreeMap

/** Untrusted carrier fields order opaque TLS bytes. They never establish identity or receipts. */
data class OpaqueTlsFrame(val sessionId: String, val mode: String, val direction: String, val index: Long, val body: ByteArray) {
    fun encode(): ByteArray = JSONObject().put("version", 2).put("session_id", sessionId)
        .put("mode", mode).put("direction", direction).put("index", index)
        .put("body", Base64.getUrlEncoder().withoutPadding().encodeToString(body))
        .toString().toByteArray(Charsets.UTF_8).also { parse(it) }

    companion object {
        const val MAX_FRAME_BYTES = 3072
        const val MAX_BODY_BYTES = 2048
        const val MAX_INDEX = 9007199254740991L
        fun parse(bytes: ByteArray): OpaqueTlsFrame {
            require(bytes.size <= MAX_FRAME_BYTES)
            val json = StrictJson.parse(bytes)
            json.fields("version", "session_id", "mode", "direction", "index", "body")
            require(json.integer("version") == 2L)
            val mode = json.string("mode", 6).also { require(it in setOf("pair", "resume")) }
            val direction = json.string("direction", 22).also {
                require(it in setOf("initiator_to_responder", "responder_to_initiator"))
            }
            val encoded = json.string("body", 2731)
            val body = Base64.getUrlDecoder().decode(encoded)
            require(body.size in 1..MAX_BODY_BYTES)
            require(Base64.getUrlEncoder().withoutPadding().encodeToString(body) == encoded)
            return OpaqueTlsFrame(json.uuid("session_id"), mode, direction, json.integer("index"), body)
        }
    }
}

/** Discard this object and its TLS engine on any error, loss deadline, or transport migration. */
class OpaqueTlsReorder(private val sessionId: String, private val mode: String, private val direction: String) {
    private val waiting = TreeMap<Long, ByteArray>()
    private val recent = linkedMapOf<Long, ByteArray>()
    private var next = 0L
    private var gapSince: Long? = null
    private var lastNow = 0L
    private var failed = false

    fun accept(bytes: ByteArray, monotonicMillis: Long): List<ByteArray> = guarded {
        checkDeadline(monotonicMillis)
        val frame = OpaqueTlsFrame.parse(bytes)
        require(frame.sessionId == sessionId && frame.mode == mode && frame.direction == direction)
        if (frame.index < next) {
            recent[frame.index]?.let { require(MessageDigest.isEqual(it, digest(frame.body))) }
            return@guarded emptyList()
        }
        require(frame.index - next <= MAX_PENDING_FRAMES)
        val output = mutableListOf<ByteArray>()
        if (frame.index == next) {
            emit(frame.body, output)
            while (waiting.containsKey(next)) emit(waiting.remove(next)!!, output)
        } else {
            val previous = waiting[frame.index]
            if (previous != null) require(previous.contentEquals(frame.body))
            else {
                require(waiting.size < MAX_PENDING_FRAMES)
                require(waiting.values.sumOf { it.size } + frame.body.size <= MAX_PENDING_BYTES)
                waiting[frame.index] = frame.body
            }
        }
        if (waiting.isEmpty()) gapSince = null
        else if (gapSince == null) gapSince = monotonicMillis
        output
    }

    fun expire(monotonicMillis: Long) { guarded { checkDeadline(monotonicMillis) } }

    private fun checkDeadline(now: Long) {
        require(now >= lastNow)
        lastNow = now
        gapSince?.let { require(now - it < GAP_TIMEOUT_MILLIS) { "TLS carrier gap expired" } }
    }
    private fun emit(bytes: ByteArray, output: MutableList<ByteArray>) {
        require(next <= OpaqueTlsFrame.MAX_INDEX) { "TLS carrier index exhausted" }
        recent[next] = digest(bytes)
        if (recent.size > MAX_PENDING_FRAMES) recent.remove(recent.keys.first())
        next++
        output.add(bytes)
    }
    private fun <T> guarded(block: () -> T): T {
        check(!failed)
        return try { block() } catch (error: Exception) {
            failed = true; waiting.clear(); recent.clear(); throw error
        }
    }
    private fun digest(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes)
    companion object {
        const val MAX_PENDING_FRAMES = 32
        const val MAX_PENDING_BYTES = 65536
        const val GAP_TIMEOUT_MILLIS = 30000L
    }
}

/** Length framing inside authenticated TLS, before strict JSON and application authorization. */
class TlsJsonRecords {
    private val header = ByteBuffer.allocate(4)
    private var body: ByteBuffer? = null
    private var failed = false

    fun feed(plaintext: ByteArray): List<JSONObject> {
        check(!failed)
        try {
            require(plaintext.size <= MAX_RECORD_BYTES)
            val records = mutableListOf<JSONObject>()
            for (byte in plaintext) {
                val target = body
                if (target == null) {
                    header.put(byte)
                    if (!header.hasRemaining()) {
                        header.flip()
                        val size = header.int
                        require(size in 1..MAX_RECORD_BYTES)
                        body = ByteBuffer.allocate(size)
                        header.clear()
                    }
                } else {
                    target.put(byte)
                    if (!target.hasRemaining()) {
                        require(records.size < 256)
                        records.add(StrictJson.parse(target.array()))
                        body = null
                    }
                }
            }
            return records
        } catch (error: Exception) { failed = true; body = null; throw error }
    }

    fun endOfStream() {
        if (failed || body != null || header.position() != 0) {
            failed = true
            body = null
            error("Truncated record")
        }
    }

    companion object {
        const val MAX_RECORD_BYTES = 65536
        fun encode(json: ByteArray): ByteArray {
            StrictJson.parse(json)
            return ByteBuffer.allocate(4 + json.size).putInt(json.size).put(json).array()
        }
    }
}
