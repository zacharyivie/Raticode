package dev.raticode.mobile.live

import org.json.JSONObject

/**
 * Whether an organization's workers pull accepted Backlog tickets into To do when they run out of work
 * (gofer/ui/organization_backlog.py). The desktop decides which tickets move; the phone only shows the setting
 * and turns it on or off.
 */
object BacklogPulls {
    const val OP = "organizations.backlog_pull"
    const val UPDATE_DESKTOP = "Update Raticode on your computer to see and change Backlog pulls here."
    const val ON = "A worker with nothing it can start pulls one Backlog ticket into To do: its own, or an unassigned one in its " +
        "projects, once the tickets it depends on are done. Highest priority goes first."
    const val OFF = "Backlog tickets wait until you or Rem move them to To do."
    const val KEPT = "Worker proposals, tickets waiting for approval and tickets moved back to Backlog by hand always stay there."

    /** The setting from an organization read: null on a desktop that doesn't send it. */
    fun parse(json: JSONObject): Boolean? = if (json.has("pull_backlog")) json.optBoolean("pull_backlog", true) else null

    fun params(organizationId: String, revision: Long, enabled: Boolean): JSONObject =
        JSONObject().put("organization_id", organizationId).put("expected_revision", revision).put("enabled", enabled)

    /**
     * Why a Backlog ticket moves or stays, from the desktop's reason, as the desktop's backlogHint words it. An older
     * desktop sends `backlog` or nothing, and its workers never pull, so the old wording stays.
     */
    fun hint(reason: String, worker: String, running: Boolean, pulling: Boolean?): String {
        val stays = "In Backlog, so it won't run. Move it to To do when it should start."
        if (pulling != true || reason.isBlank() || reason == "backlog") return stays
        return when {
            reason.startsWith("Proposed by a worker") -> "A worker's proposal. Move it to To do to accept it; workers never pull proposals."
            reason.startsWith("Kept in Backlog") -> "Kept in Backlog because it was moved back here. Move it to To do when it should start."
            reason == "Waiting for plan approval" -> "Waiting for you to approve its plan. Then $worker pulls it when free."
            reason.startsWith("Waiting for your decision") -> "Waiting for your decision on a request about it."
            reason.startsWith("In Backlog. Assign it") -> "In Backlog with no worker. Assign it, or a free worker assigned to its project takes it."
            reason.startsWith("In Backlog until") ->
                "In Backlog. ${worker.replaceFirstChar { it.uppercase() }} pulls it into To do when free${if (running) "" else " once the organization runs"}."
            Regex("cancelled|no longer exists").containsMatchIn(reason) -> if (reason.endsWith(".")) reason else "$reason."
            reason.startsWith("Waiting for ") ->
                "In Backlog, ${reason.replaceFirstChar { it.lowercase() }.removeSuffix(".")}. Then $worker pulls it when free."
            else -> stays
        }
    }

    /** The tone of [hint]: `you` when it waits on the user, otherwise `wait`. */
    fun tone(reason: String, pulling: Boolean?): String = when {
        pulling != true -> "wait"
        reason.startsWith("Proposed by a worker") || reason == "Waiting for plan approval" || reason.startsWith("Waiting for your decision") ||
            reason.startsWith("In Backlog. Assign it") || Regex("cancelled|no longer exists").containsMatchIn(reason) -> "you"
        else -> "wait"
    }
}
