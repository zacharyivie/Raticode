package dev.raticode.mobile.live

import org.json.JSONObject

/**
 * An organization's Auto mode (gofer/ui/organization_auto.py): Rem's review decides the organization's requests
 * instead of the user, and stops the organization only when it must. The phone shows whether it's on, why it stopped,
 * and Rem's recent decisions, and turns it on (after a confirmation) or off with `organizations.auto_mode`.
 */
object AutoMode {
    const val OP = "organizations.auto_mode"
    const val UPDATE_DESKTOP = "Update Raticode on your computer to use Auto mode."
    const val CONFIRM = "Rem approves, changes or declines this organization's requests instead of you. It stops the organization " +
        "only when something is dangerous and Rem can't make it safe without stopping everything, or when it can't go on without " +
        "you, such as a missing credential, sign-in, hardware or internet. Budget and run-limit increases, git push and " +
        "folder-isolation policy, and Auto mode itself stay yours."
    const val ON = "Rem decides this organization's requests. It stops only for danger it can't fix or what only you can do."
    const val OFF = "Requests wait for you to approve or decline them."
    const val REVIEWING = "Rem is reviewing (Auto mode)"

    /** Why Auto mode stopped the organization, and what the user must do. */
    data class Halt(val summary: String, val kind: String, val reason: String, val userAction: String, val requestId: String, val at: String)

    /** One of Rem's recent decisions, newest first. */
    data class Decision(val summary: String, val decision: String, val reason: String, val recommendation: String,
                        val applied: Boolean, val error: String, val at: String)

    /** Whether Auto mode is on: null on a desktop that doesn't send it. */
    fun parse(json: JSONObject): Boolean? = if (json.has("auto_mode")) json.optBoolean("auto_mode") else null

    fun parseHalt(json: JSONObject): Halt? = json.optJSONObject("auto_halt")?.let {
        Halt(it.optString("summary"), it.optString("kind"), it.optString("reason"), it.optString("user_action"),
            it.optString("request_id"), it.optString("at"))
    }

    fun parseDecisions(json: JSONObject): List<Decision> {
        val items = json.optJSONArray("auto_decisions") ?: return emptyList()
        return (0 until items.length()).mapNotNull { items.optJSONObject(it) }.map {
            Decision(it.optString("summary"), it.optString("decision"), it.optString("reason"), it.optString("recommendation"),
                it.optBoolean("applied"), it.optString("error"), it.optString("at"))
        }
    }

    /** Turning it on always carries the user's confirmation; turning it off needs none. */
    fun params(organizationId: String, revision: Long, enabled: Boolean): JSONObject =
        JSONObject().put("organization_id", organizationId).put("expected_revision", revision).put("enabled", enabled).put("confirm", enabled)

    fun label(decision: String): String = when (decision) {
        "approve" -> "Approved"
        "modify" -> "Approved with changes"
        "decline" -> "Declined"
        "halt" -> "Stopped"
        "left_for_you" -> "Left for you"
        else -> decision.replaceFirstChar { it.uppercase() }
    }

    /** The stop card's last line. */
    fun toContinue(halt: Halt): String = "To continue: ${halt.userAction}"
}
