package dev.raticode.mobile.protocol.v2

import dev.raticode.mobile.protocol.StrictJson
import dev.raticode.mobile.protocol.fields
import dev.raticode.mobile.protocol.integer
import dev.raticode.mobile.protocol.string
import dev.raticode.mobile.protocol.uuid
import org.json.JSONObject
import java.security.MessageDigest
import java.util.Base64

/** Checks the first decrypted record against trusted invitation/registry expectations.
 * This does not consume an invitation, confirm trust or authorize application dispatch.
 */
class SessionBinding(expectedJson: ByteArray) {
    private val expected = validate(expectedJson)
    private var attempted = false

    @Synchronized
    fun verifyFirstRecord(receivedJson: ByteArray) {
        check(!attempted) { "Session binding already attempted" }
        attempted = true
        val received = validate(receivedJson)
        for (key in FIELDS) {
            if (key == "secret") continue
            require(expected.get(key) == received.get(key)) { "Session binding mismatch" }
        }
        if (!expected.isNull("secret")) {
            require(MessageDigest.isEqual(secret(expected), secret(received))) { "Session binding mismatch" }
        }
    }

    companion object {
        private val FIELDS = arrayOf("type", "version", "profile", "session_id", "mode", "initiator_id",
            "responder_id", "initiator_role", "responder_role", "invitation_sha256", "secret")
        private fun validate(bytes: ByteArray): JSONObject {
            val json = StrictJson.parse(bytes)
            json.fields(*FIELDS)
            require(json.integer("version") == 2L)
            require(json.string("type", 12) == "session.bind")
            require(json.string("profile", 19) == "raticode-tls13-v2")
            json.uuid("session_id")
            require(json.uuid("initiator_id") != json.uuid("responder_id"))
            require(json.string("initiator_role", 10) in setOf("controller", "desktop"))
            require(json.string("responder_role", 7) == "desktop")
            when (json.string("mode", 6)) {
                "pair" -> {
                    require(json.string("invitation_sha256", 64).matches(Regex("[0-9a-f]{64}")))
                    secret(json)
                }
                "resume" -> require(json.isNull("invitation_sha256") && json.isNull("secret"))
                else -> error("Unsupported session mode")
            }
            return json
        }
        private fun secret(json: JSONObject): ByteArray {
            val encoded = json.string("secret", 43)
            return Base64.getUrlDecoder().decode(encoded).also {
                require(it.size == 32 && Base64.getUrlEncoder().withoutPadding().encodeToString(it) == encoded)
            }
        }
    }
}
