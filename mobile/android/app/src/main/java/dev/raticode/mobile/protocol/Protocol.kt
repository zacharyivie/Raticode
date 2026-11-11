package dev.raticode.mobile.protocol

import org.json.JSONObject
import java.util.UUID

/** Application envelope after authenticated decryption, never a relay payload. */
data class ProtocolEvent(val id: String, val threadId: String, val requestId: String?, val sequence: Long, val type: String, val payload: JSONObject)
object Protocol {
    val types = setOf("chat.submit", "chat.accepted", "chat.message", "request.cancel", "request.status", "thread.snapshot", "swarm.snapshot", "approval.required", "approval.resolve", "file.offer", "file.request", "error")
    fun parse(plaintext: ByteArray): ProtocolEvent {
        require(plaintext.size <= 64 * 1024) { "Envelope exceeds 64 KiB" }
        val root = StrictJson.parse(plaintext)
        require(root.keys().asSequence().toSet() == setOf("version", "id", "thread_id", "request_id", "sequence", "type", "payload"))
        require(root.integer("version") == 1L)
        fun id(key: String): String = root.getString(key).also { require(UUID.fromString(it).toString() == it) }
        val type = root.getString("type").also { require(it in types) }
        val sequence = root.integer("sequence")
        val requestId = if (root.isNull("request_id")) null else id("request_id")
        if (type in setOf("chat.submit", "chat.accepted", "request.cancel", "request.status", "approval.resolve")) require(requestId != null)
        val payload = root.getJSONObject("payload")
        PayloadValidation.validate(type, payload)
        if (type == "chat.submit") {
            require(payload.keys().asSequence().toSet() == setOf("text", "attachment_ids"))
            require(payload.getString("text").length <= 4000)
            val files = payload.getJSONArray("attachment_ids")
            require(files.length() <= 1)
            if (files.length() == 1) require(UUID.fromString(files.getString(0)).toString() == files.getString(0))
            require(payload.getString("text").isNotBlank() || files.length() > 0)
        }
        return ProtocolEvent(id("id"), id("thread_id"), requestId, sequence, type, payload)
    }
}

/** No real transport is enabled before an authenticated, reviewed desktop session exists. */
class DesktopTransport {
    fun send(@Suppress("UNUSED_PARAMETER") event: ProtocolEvent): Nothing =
        error("Desktop E2EE pairing is not available. No message was transmitted.")
}
