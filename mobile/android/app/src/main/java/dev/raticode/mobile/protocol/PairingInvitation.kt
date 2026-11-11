package dev.raticode.mobile.protocol

import org.json.JSONObject
import java.net.URI
import java.util.Base64
import java.util.UUID

/** Parsing an invitation does not establish trust, contact a relay, or run a command. */
data class PairingInvitation(val desktopName: String, val desktopId: String, val relay: String, val expiresAt: Long, val fingerprint: String) {
    companion object {
        const val PREFIX = "raticode://pair?v=1&data="
        fun parse(value: String, nowSeconds: Long = System.currentTimeMillis() / 1000): PairingInvitation {
            require(value.length <= 4096 && value.startsWith(PREFIX)) { "This is not a Raticode v1 pairing code." }
            val encoded = value.removePrefix(PREFIX)
            require(encoded.matches(Regex("[A-Za-z0-9_-]+"))) { "Malformed pairing code." }
            val decoded = Base64.getUrlDecoder().decode(encoded)
            require(Base64.getUrlEncoder().withoutPadding().encodeToString(decoded) == encoded)
            val json = StrictJson.parse(decoded)
            require(json.keys().asSequence().toSet() == setOf("version", "invitation_id", "desktop_id", "desktop_name", "relay", "expires_at", "desktop_key", "secret")) { "Unsupported pairing fields." }
            require(json.integer("version") == 1L)
            listOf("invitation_id", "desktop_id").forEach { require(UUID.fromString(json.getString(it)).toString() == json.getString(it)) }
            val name = json.string("desktop_name", 80)
            require(name.length in 1..80 && name.none { it.isISOControl() || Character.getType(it) == Character.FORMAT.toInt() }) { "Invalid desktop name." }
            val expires = json.integer("expires_at")
            require(expires > nowSeconds && expires <= nowSeconds + 300) { "Pairing code expired or has an invalid lifetime. Create a new code on your desktop." }
            val relay = URI(json.string("relay", 2048))
            require(relay.scheme == "https" && !relay.host.isNullOrBlank() && relay.rawUserInfo == null && relay.rawQuery == null && relay.rawFragment == null && relay.path in listOf("", "/") && relay.port in listOf(-1, 443)) { "The relay must be an HTTPS origin." }
            fun key(field: String): ByteArray {
                val raw = json.string(field, 43)
                require(raw.matches(Regex("[A-Za-z0-9_-]{43}")))
                return Base64.getUrlDecoder().decode(raw).also { require(it.size == 32 && Base64.getUrlEncoder().withoutPadding().encodeToString(it) == raw) }
            }
            val publicKey = key("desktop_key")
            key("secret").fill(0)
            val fingerprint = java.security.MessageDigest.getInstance("SHA-256").digest(publicKey).joinToString("") { "%02x".format(it) }
            // Only a safe preview leaves the parser. Invitation secrets are never persisted.
            return PairingInvitation(name, json.getString("desktop_id"), relay.toString(), expires, fingerprint)
        }
    }
}
