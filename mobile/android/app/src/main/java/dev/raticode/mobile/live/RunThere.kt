package dev.raticode.mobile.live

import org.json.JSONObject

/**
 * Run there: a paired desktop with room, offered to a Rem task or organization ticket that waits while its computer is full.
 *
 * The desktop finds it and keeps it on the waiting run (`suggestion` in `rem.home`) or ticket (`suggestion` in
 * `organizations.read`). Nothing starts on the other desktop until the user taps **Run there**, which sends
 * `computers.run_there`. A Rem task gets pinned there; a ticket only gets the fleet execution target the owner already made
 * there for its worker. Without such a target [Suggestion.pick] is false and [Suggestion.grant] says what to grant.
 *
 * Only desktops reporting `run_there` in their overview take the action.
 */
object RunThere {
    const val OP = "computers.run_there"
    const val LABEL = "Run there"
    const val UPDATE_DESKTOP = "Update Raticode on your computer to run waiting work on another of your computers."

    data class Suggestion(val computerId: String, val computerName: String, val project: String, val text: String,
                          val pick: Boolean, val grant: String) {
        /** "Desktop B has room:", before the Run there button. */
        val lead get() = "$computerName has room:"
    }

    /** The suggestion as the desktop sent it, bounded; null when there is none or it names no computer. */
    fun parse(json: JSONObject?): Suggestion? {
        json ?: return null
        val computer = json.optString("computer_id").take(64)
        if (computer.isBlank() || !computer.all { it.isLetterOrDigit() || it == '-' }) return null
        val name = json.optString("computer_name").take(150).ifBlank { "Another computer" }
        val pick = json.optBoolean("pick")
        val grant = json.optString("grant").take(400)
        val text = json.optString("text").take(400).ifBlank { if (pick) "$name has room: $LABEL" else "$name has room. $grant".trim() }
        return Suggestion(computer, name, json.optString("project").take(200), text, pick, grant)
    }

    /** The request for a Rem run, or null when there is nothing to pick. */
    fun forRun(runId: String, suggestion: Suggestion?, supported: Boolean): JSONObject? =
        if (!supported || suggestion == null || !suggestion.pick || runId.isBlank()) null
        else JSONObject().put("run_id", runId).put("computer_id", suggestion.computerId)

    /** The request for an organization ticket, or null when there is nothing to pick. */
    fun forTicket(organizationId: String, taskId: String, suggestion: Suggestion?, supported: Boolean): JSONObject? =
        if (!supported || suggestion == null || !suggestion.pick || organizationId.isBlank() || taskId.isBlank()) null
        else JSONObject().put("organization_id", organizationId).put("task_id", taskId).put("computer_id", suggestion.computerId)

    fun blocked(op: String, desktopRunsThere: Boolean) = op == OP && !desktopRunsThere
}
