package dev.raticode.mobile.live

import dev.raticode.mobile.protocol.*
import org.json.JSONObject
import java.net.URI
import java.util.Base64

enum class InvitationProblem(val guidance: String) {
    UNSUPPORTED_VERSION("This invitation uses an older pairing format. Generate a new invitation in the current desktop's Device settings."),
    EXPIRED("This invitation has expired. Generate a new invitation in desktop Device settings. If a fresh code still fails, enable automatic date and time on both devices."),
    CLOCK_MISMATCH("The invitation is too far in the future. Enable automatic date and time on both devices, then generate a new invitation."),
    NO_LOCAL_ADDRESS("This invitation has no Wi-Fi address. Update Raticode Desktop, connect it to your Wi-Fi network, then generate a new invitation."),
}

class InvitationException(val problem: InvitationProblem) : IllegalArgumentException(problem.guidance)

class DeviceInvitation private constructor(val bytes: ByteArray, val json: JSONObject) {
    val name: String get() = json.getString("responder_name")
    val pin: String get() = json.getString("responder_spki_sha256")
    companion object {
        /** Accepts an IP literal on the local network or in Tailscale's ranges; never a hostname or public address. */
        fun validateDesktopEndpoint(endpoint: JSONObject): DeviceEndpoint {
                endpoint.fields("host", "port")
                val host = endpoint.string("host", 253)
                require(host.none { it.isWhitespace() || it.isISOControl() || it in "/?#@" })
                require(host.matches(Regex("[0-9a-fA-F:.]+")))
                // Shortened IPv4 forms such as 100.1 are not addresses the desktop would report.
                require(':' in host || host.matches(Regex("\\d{1,3}(\\.\\d{1,3}){3}")))
                val address = java.net.InetAddress.getByName(host)
                require(!address.isAnyLocalAddress && !address.isMulticastAddress)
                val port = endpoint.integer("port")
                require(port in 1..65535)
                val path = when {
                    TailnetAddress.contains(address) -> DevicePath.TAILSCALE
                    address.isLoopbackAddress || address.isSiteLocalAddress || address.isLinkLocalAddress -> DevicePath.WIFI
                    else -> throw IllegalArgumentException("Not a local or Tailscale address")
                }
                return DeviceEndpoint(host, port.toInt(), path)
        }

        fun validateLanEndpoint(endpoint: JSONObject) { require(validateDesktopEndpoint(endpoint).path == DevicePath.WIFI) }

        fun validateTailnetEndpoint(endpoint: JSONObject) = validateDesktopEndpoint(endpoint).also { require(it.path == DevicePath.TAILSCALE) }

        fun parse(value: String, now: Long = System.currentTimeMillis() / 1000): DeviceInvitation {
            if (value.startsWith("raticode://pair?v=1&")) throw InvitationException(InvitationProblem.UNSUPPORTED_VERSION)
            require(value.length <= 4096 && value.startsWith("raticode://pair?v=2&data="))
            val encoded = value.removePrefix("raticode://pair?v=2&data=")
            val bytes = Base64.getUrlDecoder().decode(encoded)
            require(Base64.getUrlEncoder().withoutPadding().encodeToString(bytes) == encoded)
            val j = StrictJson.parse(bytes)
            j.fields("version", "profile", "invitation_id", "responder_id", "responder_name", "responder_role", "responder_spki_sha256", "secret", "expires_at", "relay", "pair_initiator_topic", "pair_responder_topic", "lan_endpoint")
            require(j.integer("version") == 2L && j.string("profile", 19) == "raticode-tls13-v2")
            j.uuid("invitation_id"); j.uuid("responder_id")
            require(j.string("responder_role", 7) == "desktop")
            require(j.string("responder_name", 80).none { it.isISOControl() || Character.getType(it) == Character.FORMAT.toInt() })
            require(j.string("responder_spki_sha256", 64).matches(Regex("[a-f0-9]{64}")))
            val expires = j.integer("expires_at")
            if (expires <= now) throw InvitationException(InvitationProblem.EXPIRED)
            if (expires > now + 300) throw InvitationException(InvitationProblem.CLOCK_MISMATCH)
            listOf("secret", "pair_initiator_topic", "pair_responder_topic").forEach {
                val text = j.string(it, 43)
                val decoded = Base64.getUrlDecoder().decode(text)
                require(decoded.size == 32 && Base64.getUrlEncoder().withoutPadding().encodeToString(decoded) == text)
            }
            require(j.getString("pair_initiator_topic") != j.getString("pair_responder_topic"))
            val relay = URI(j.string("relay", 512))
            require(relay.scheme == "https" && relay.host != null && relay.rawUserInfo == null && relay.rawQuery == null && relay.rawFragment == null && relay.rawPath.isNullOrEmpty() && relay.port in listOf(-1, 443))
            // The phone connects over Wi-Fi or, when enabled, Tailscale. The relay field is a legacy v2 value and is never contacted.
            if (j.isNull("lan_endpoint")) throw InvitationException(InvitationProblem.NO_LOCAL_ADDRESS)
            validateDesktopEndpoint(j.getJSONObject("lan_endpoint"))
            return DeviceInvitation(bytes, j)
        }
    }
}
