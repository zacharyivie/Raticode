package dev.raticode.mobile.live

import org.json.JSONObject

/** Why tickets were blocked in the period, with how many. */
data class VerifiedCause(val cause: String, val label: String, val tickets: Int)

/**
 * Usage › Verified work: how often Rem's tickets passed review on their own in the chosen period.
 * The desktop works it out (gofer/ui/rem_verified_work.py) from records it already keeps; the phone only shows it.
 * Percentages and [repairsPerTicket] are null when there was nothing to divide by.
 */
data class VerifiedWork(
    val line: String, val accepted: Int, val firstPass: Int, val firstPassPercent: Int?, val repairs: Int, val repairsPerTicket: Double?,
    val acceptedByHand: Int, val finished: Int, val blocked: Int, val blockedPercent: Int?, val causes: List<VerifiedCause>,
    val checked: Int, val checkFailedFirst: Int, val tokensPerAccepted: Long?, val tokensPartial: Boolean,
    val stepIns: Int, val requestChanges: Int, val manualMoves: Int, val historySince: String,
    /** Check runs that failed, then passed on a rerun of the same commit; null from a desktop that doesn't count them. */
    val checkFlaky: Int? = null,
)

object UsageVerified {
    private fun JSONObject.count(key: String): Int = (opt(key) as? Number)?.toInt()?.coerceIn(0, 1_000_000) ?: 0
    private fun JSONObject.share(key: String): Int? = (opt(key) as? Number)?.toInt()?.takeIf { it in 0..100 }
    private fun JSONObject.text(key: String, limit: Int): String = optString(key).takeUnless { isNull(key) }.orEmpty().take(limit)

    /** Null from a desktop without the section. */
    fun parse(json: JSONObject?): VerifiedWork? {
        if (json == null || !json.has("accepted")) return null
        val check = json.optJSONObject("project_check") ?: JSONObject()
        val tokens = json.optJSONObject("tokens") ?: JSONObject()
        val steps = json.optJSONObject("step_ins") ?: JSONObject()
        val causes = json.optJSONArray("blocked_causes")?.let { a -> (0 until a.length()).mapNotNull { a.optJSONObject(it) } }.orEmpty()
        return VerifiedWork(
            json.text("line", 160), json.count("accepted"), json.count("first_pass"), json.share("first_pass_percent"), json.count("repairs"),
            (json.opt("repairs_per_ticket") as? Number)?.toDouble()?.takeIf { it.isFinite() && it >= 0 },
            json.count("accepted_by_hand"), json.count("finished"), json.count("blocked"), json.share("blocked_percent"),
            causes.take(10).map { VerifiedCause(it.text("cause", 40), it.text("label", 80), it.count("tickets")) }.filter { it.tickets > 0 },
            check.count("checked"), check.count("failed_first"),
            (tokens.opt("per_accepted") as? Number)?.toLong()?.takeIf { it >= 0 }, tokens.optBoolean("partial"),
            steps.count("total"), steps.count("request_changes"), steps.count("manual_moves"), json.text("history_since", 40),
            if (check.has("flaky")) check.count("flaky") else null,
        )
    }

    /** "1 of 3" for the headline, or null when nothing was accepted or blocked. */
    fun headline(work: VerifiedWork): String? = when {
        work.accepted > 0 -> "${work.firstPass} of ${work.accepted}"
        work.finished > 0 -> "0 accepted"
        else -> null
    }

    /** The figures under the headline as label and value, only those with something to say. */
    fun facts(work: VerifiedWork): List<Pair<String, String>> = buildList {
        if (work.accepted > 0) add("Repairs per ticket" to "%.2f".format(java.util.Locale.ROOT, work.repairsPerTicket ?: 0.0).trimEnd('0').trimEnd('.'))
        if (work.finished > 0) add("Blocked" to "${work.blocked} of ${work.finished}" + (work.blockedPercent?.let { " ($it%)" } ?: ""))
        if (work.checked > 0) add("Project check failed first try" to "${work.checkFailedFirst} of ${work.checked}")
        if (work.checked > 0) work.checkFlaky?.let { add("Flaky check runs" to "$it") }
        work.tokensPerAccepted?.let { add("Tokens per accepted ticket" to "%,d".format(java.util.Locale.ROOT, it) + if (work.tokensPartial) " or more" else "") }
        add("You stepped in" to "${work.stepIns} " + (if (work.stepIns == 1) "time" else "times") +
            " · ${work.requestChanges} change " + (if (work.requestChanges == 1) "request" else "requests") +
            ", ${work.manualMoves} " + (if (work.manualMoves == 1) "move" else "moves") + " by hand")
    }

    /** Why tickets were blocked, one row per cause as on the desktop: "No activity for 20 minutes" to "2". */
    fun causeRows(work: VerifiedWork): List<Pair<String, String>> =
        work.causes.map { (it.label.ifBlank { it.cause }) to "${it.tickets}" }

    /** The self-work report card's line: "3 of 4 tickets passed review first time." Blank without one. */
    fun reportLine(json: JSONObject?): String {
        if (json == null || (json.opt("accepted") as? Number)?.toInt()?.takeIf { it > 0 } == null) return ""
        val line = json.optString("line").trim().take(160).removeSuffix(".")
        return if (line.isBlank()) "" else "$line."
    }
}
