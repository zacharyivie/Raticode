package dev.raticode.mobile.protocol.v2

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class SessionBindingTest {
    private fun binding() = JSONObject().put("version", 2).put("type", "session.bind")
        .put("profile", "raticode-tls13-v2").put("session_id", "00000000-0000-4000-8000-000000000001")
        .put("mode", "pair").put("initiator_id", "00000000-0000-4000-8000-000000000002")
        .put("responder_id", "00000000-0000-4000-8000-000000000003").put("initiator_role", "controller")
        .put("responder_role", "desktop").put("invitation_sha256", "a".repeat(64)).put("secret", "A".repeat(43))
    private fun bytes(json: JSONObject) = json.toString().toByteArray()
    private fun rejected(block: () -> Unit) {
        var rejected = false
        try { block() } catch (_: Exception) { rejected = true }
        assertTrue(rejected)
    }
    @Test fun matchingBindingIsSingleUse() {
        val expected = bytes(binding()); val checker = SessionBinding(expected)
        checker.verifyFirstRecord(expected)
        rejected { checker.verifyFirstRecord(expected) }
    }
    @Test fun wrongSecretHashRolesAndSessionFail() {
        for ((key, value) in mapOf("secret" to "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE",
            "invitation_sha256" to "b".repeat(64), "initiator_role" to "desktop",
            "session_id" to "00000000-0000-4000-8000-000000000004", "responder_role" to "controller")) {
            val checker = SessionBinding(bytes(binding()))
            rejected { checker.verifyFirstRecord(bytes(binding().put(key, value))) }
            rejected { checker.verifyFirstRecord(bytes(binding())) }
        }
    }
    @Test fun resumeHasNoEnrollmentSecret() {
        rejected { SessionBinding(bytes(binding().put("mode", "resume"))) }
        val resume = bytes(binding().put("mode", "resume").put("secret", JSONObject.NULL).put("invitation_sha256", JSONObject.NULL))
        SessionBinding(resume).verifyFirstRecord(resume)
    }
}
