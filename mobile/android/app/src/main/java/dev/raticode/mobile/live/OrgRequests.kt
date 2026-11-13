package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject

/**
 * Approving and declining Rem's requests in an organization from the phone (`organizations.requests`). The phone sends
 * the exact request IDs it showed; the desktop approves them in its safe order (gofer/ui/organization_consent.py) and
 * answers with each one's result, so a request that arrived after the list was shown is never approved.
 */
object OrgRequests {
    const val OP = "organizations.requests"
    const val UPDATE_DESKTOP = "Approve or decline it in Organizations on your computer, where you can review what it changes. " +
        "Update Raticode on your computer to approve requests here."
    const val ORDER = "Raticode applies changes first and starts the organization last. A change made for an older version of " +
        "the organization is left waiting instead of undoing newer changes."
    const val LATER = "Requests that arrive after you open this list aren't included."

    data class Result(val requestId: String, val summary: String, val status: String, val message: String)

    fun params(organizationId: String, requestIds: List<String>, decision: String): JSONObject =
        JSONObject().put("organization_id", organizationId).put("decision", decision).put("request_ids", JSONArray(requestIds))

    /** Each request's outcome from an `organizations.requests` answer, or null for any other organization read. */
    fun parseResults(json: JSONObject): List<Result>? {
        val items = json.optJSONArray("request_results") ?: return null
        return (0 until items.length()).mapNotNull { items.optJSONObject(it) }.map {
            Result(it.optString("request_id"), it.optString("summary"), it.optString("status"), it.optString("message"))
        }
    }

    /** The result's state in words, as the desktop's Approve all shows it. */
    fun label(status: String): String = when (status) {
        "approved" -> "Approved"
        "declined" -> "Declined"
        "failed" -> "Failed"
        "skipped" -> "Left waiting"
        else -> status.replaceFirstChar { it.uppercase() }
    }

    /** One sentence for the whole run, matching the desktop's approveAllSummary. */
    fun summary(results: List<Result>): String {
        val approved = results.count { it.status == "approved" }
        val declined = results.count { it.status == "declined" }
        val failed = results.count { it.status == "failed" }
        val skipped = results.count { it.status == "skipped" }
        if (declined > 0 && approved == 0 && failed == 0 && skipped == 0) return "Declined ${count(declined, "request")}."
        if (failed == 0 && skipped == 0) return if (approved == 1) "Approved 1 request." else "Approved all ${count(approved, "request")}."
        return listOfNotNull("Approved $approved of ${results.size}", if (failed > 0) "$failed failed" else null,
            if (skipped > 0) "${count(skipped, "request")} left waiting" else null).joinToString(", ") + "."
    }

    /** Whether to offer Approve all: several requests wait. Each request keeps its own Approve and Decline. */
    fun offerAll(requests: List<OrgRequest>): Boolean = requests.count { it.id.isNotBlank() && !it.reviewedByRem } >= 2

    /** The IDs to send for Approve all: exactly the requests shown when the list opened. */
    fun frozen(requests: List<OrgRequest>): List<String> =
        requests.filter { !it.reviewedByRem }.map { it.id }.filter { it.isNotBlank() }.distinct().take(20)

    /** The requests that wait on the user: in Auto mode, Rem's review decides the rest. */
    fun forYou(requests: List<OrgRequest>): List<OrgRequest> = requests.filter { !it.reviewedByRem }

    private fun count(n: Int, noun: String) = "$n ${if (n == 1) noun else noun + "s"}"
}
