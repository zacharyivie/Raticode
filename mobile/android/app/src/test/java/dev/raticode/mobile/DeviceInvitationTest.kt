package dev.raticode.mobile

import dev.raticode.mobile.live.DeviceInvitation
import dev.raticode.mobile.live.InvitationException
import dev.raticode.mobile.live.InvitationProblem
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.util.Base64
import java.util.UUID

class DeviceInvitationTest {
    private fun invite(change: (JSONObject) -> Unit = {}): String {
        val token = Base64.getUrlEncoder().withoutPadding().encodeToString(ByteArray(32) { 1 })
        val j = JSONObject().put("version", 2).put("profile", "raticode-tls13-v2").put("invitation_id", UUID.randomUUID()).put("responder_id", UUID.randomUUID())
            .put("responder_name", "Desktop").put("responder_role", "desktop").put("responder_spki_sha256", "1".repeat(64)).put("secret", token)
            .put("expires_at", 1100).put("relay", "https://ntfy.sh").put("pair_initiator_topic", token)
            .put("pair_responder_topic", Base64.getUrlEncoder().withoutPadding().encodeToString(ByteArray(32) { 2 }))
            .put("lan_endpoint", JSONObject().put("host", "192.168.1.2").put("port", 1234))
        change(j)
        return "raticode://pair?v=2&data=" + Base64.getUrlEncoder().withoutPadding().encodeToString(j.toString().toByteArray())
    }
    /** Anything but a well-formed literal would make Java look the host up in DNS, so DNS could pick the address. */
    @Test fun endpointsMustBeWellFormedIpLiterals() {
        fun endpoint(host: String) = JSONObject().put("host", host).put("port", 8767)
        for (good in listOf("192.168.1.20", "10.0.0.5", "100.101.102.103", "fe80::1", "fd7a:115c:a1e0::1", "::ffff:192.168.1.2",
            "fe80:0:0:0:0:0:0:1")) {
            DeviceInvitation.validateDesktopEndpoint(endpoint(good))
        }
        for (bad in listOf("999.1.1.1", "192.168.1.256", "dead:beef", "1:2", "fe80::1::2", "fd00:::1", "fd00:12345::1",
            "::ffff:192.168.1.999", "fd00:0:0:0:0:0:0:0:1", ":1:2")) {
            assertThrows(bad, IllegalArgumentException::class.java) { DeviceInvitation.validateDesktopEndpoint(endpoint(bad)) }
        }
    }

    @Test fun acceptsBoundedPrivateEndpoint() { assertEquals("Desktop", DeviceInvitation.parse(invite(), 1000).name) }
    @Test fun expiryFailuresExplainClockRecoveryWithoutExtendingTrust() {
        for ((expiry, problem) in listOf(1000 to InvitationProblem.EXPIRED, 999 to InvitationProblem.EXPIRED,
            1301 to InvitationProblem.CLOCK_MISMATCH)) {
            val failure = assertThrows(InvitationException::class.java) {
                DeviceInvitation.parse(invite { it.put("expires_at", expiry) }, 1000)
            }
            assertEquals(problem, failure.problem)
        }
        DeviceInvitation.parse(invite { it.put("expires_at", 1001) }, 1000)
        DeviceInvitation.parse(invite { it.put("expires_at", 1300) }, 1000)
        assertEquals(InvitationProblem.UNSUPPORTED_VERSION, assertThrows(InvitationException::class.java) {
            DeviceInvitation.parse("raticode://pair?v=1&data=old", 1000)
        }.problem)
    }
    @Test fun validPreviewPreservesBindingBytesAndRejectsAlteredEnvelope() {
        val uri = invite()
        val preview = DeviceInvitation.parse(uri, 1000)
        assertArrayEquals(Base64.getUrlDecoder().decode(uri.substringAfter("&data=")), preview.bytes)
        for (bad in listOf(uri + "=", uri + "&command=run", invite { it.put("command", "run") },
            invite { it.put("pair_responder_topic", it.getString("pair_initiator_topic")) })) {
            assertThrows(Exception::class.java) { DeviceInvitation.parse(bad, 1000) }
        }
    }
    @Test fun rejectsInvitationWithoutWifiAddress() {
        assertEquals(InvitationProblem.NO_LOCAL_ADDRESS, assertThrows(InvitationException::class.java) {
            DeviceInvitation.parse(invite { it.put("lan_endpoint", JSONObject.NULL) }, 1000)
        }.problem)
    }
    @Test fun rejectsExpiredPublicAndActionEndpoints() {
        for (host in listOf("8.8.8.8", "0.0.0.0", "224.0.0.1", "example.com", "127.0.0.1/path"))
            assertThrows(Exception::class.java) { DeviceInvitation.parse(invite { it.getJSONObject("lan_endpoint").put("host", host) }, 1000) }
        assertThrows(Exception::class.java) { DeviceInvitation.parse(invite { it.put("expires_at", 999) }, 1000) }
        assertThrows(Exception::class.java) { DeviceInvitation.parse(invite { it.put("relay", "https://ntfy.sh:8443") }, 1000) }
        assertThrows(Exception::class.java) { DeviceInvitation.parse(invite { it.put("responder_name", "fake\u202eDesktop") }, 1000) }
    }
}
