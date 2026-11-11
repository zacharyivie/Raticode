package dev.raticode.mobile

import dev.raticode.mobile.protocol.*
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.util.Base64

class ProtocolTest {
    private val id = "12345678-1234-4234-8234-123456789abc"
    private fun invitation(expiry: Long = 1100, relay: String = "https://ntfy.sh") = JSONObject().apply {
        put("version", 1); put("invitation_id", id); put("desktop_id", id); put("desktop_name", "My desktop")
        put("relay", relay); put("expires_at", expiry)
        put("desktop_key", Base64.getUrlEncoder().withoutPadding().encodeToString(ByteArray(32) { 1 }))
        put("secret", Base64.getUrlEncoder().withoutPadding().encodeToString(ByteArray(32) { 2 }))
    }
    private fun uri(json: JSONObject) = PairingInvitation.PREFIX + Base64.getUrlEncoder().withoutPadding().encodeToString(json.toString().toByteArray())
    @Test fun validInvitationProducesOnlySafePreview() {
        val preview = PairingInvitation.parse(uri(invitation()), 1000)
        assertEquals("My desktop", preview.desktopName)
        assertEquals(64, preview.fingerprint.length)
        assertFalse(preview.toString().contains("secret"))
    }
    @Test fun invalidInvitationsFailClosed() {
        val invalid = listOf(
            uri(invitation(1000)), uri(invitation(1301)), uri(invitation(relay = "http://ntfy.sh")),
            uri(invitation(relay = "https://name:password@ntfy.sh")), uri(invitation(relay = "https://ntfy.sh/topic")),
            uri(invitation().put("version", 2)), uri(invitation().put("command", "run")),
            uri(invitation().put("secret", "tiny")), "https://example.com", "raticode://pair?v=1&data=" + "x".repeat(4096),
        )
        invalid.forEach { assertThrows(Exception::class.java) { PairingInvitation.parse(it, 1000) } }
    }
    private fun envelope() = JSONObject().apply {
        put("version", 1); put("id", id); put("thread_id", id); put("request_id", id)
        put("sequence", 0); put("type", "chat.submit")
        put("payload", JSONObject("{\"text\":\"Start a swarm\",\"attachment_ids\":[]}"))
    }
    @Test fun validMessageParsesButCannotReachNetwork() {
        val event = Protocol.parse(envelope().toString().toByteArray())
        assertEquals("chat.submit", event.type)
        assertThrows(IllegalStateException::class.java) { DesktopTransport().send(event) }
    }
    @Test fun everySharedEventFixtureParsesOnAndroid() {
        Protocol.types.forEach { type ->
            val bytes = requireNotNull(javaClass.classLoader!!.getResourceAsStream("$type.json")).use { it.readBytes() }
            assertEquals(type, Protocol.parse(bytes).type)
        }
    }
    @Test fun duplicateMembersInvalidUtf8AndCoercedNumbersAreRejected() {
        val valid = envelope().toString()
        val duplicate = valid.replace("\"version\":1", "\"version\":1,\"version\":1")
        listOf(duplicate, valid.replace("\"sequence\":0", "\"sequence\":\"0\""), valid + " trailing").forEach {
            assertThrows(Exception::class.java) { Protocol.parse(it.toByteArray()) }
        }
        assertThrows(Exception::class.java) { Protocol.parse(byteArrayOf(0xc3.toByte(), 0x28)) }
        assertThrows(Exception::class.java) { Protocol.parse(envelope().put("payload", JSONObject("{\"text\":12,\"attachment_ids\":[]}")).toString().toByteArray()) }
    }
    @Test fun malformedEnvelopesAreRejected() {
        val invalid = listOf(envelope().put("version", 2), envelope().put("sequence", -1), envelope().put("type", "shell.exec"),
            envelope().put("request_id", JSONObject.NULL), envelope().put("id", "bad"), envelope().put("payload", JSONObject("{\"text\":\"\",\"attachment_ids\":[]}")))
        invalid.forEach { assertThrows(Exception::class.java) { Protocol.parse(it.toString().toByteArray()) } }
        assertThrows(IllegalArgumentException::class.java) { Protocol.parse(ByteArray(65537)) }
    }
}
