package dev.raticode.mobile.live

/** One check of a run as the desktop's `rem.task` sends it: pass, fail or unknown, with the reviewer's reason. */
data class RemCheck(val id: String, val description: String, val result: String, val evidence: String)

/**
 * A run's checks by outcome for the task screen, as the desktop's task pane groups them.
 *
 * An unknown check with a reason was reviewed but not confirmed (unresolved); one without
 * a reason hasn't been checked. A check without a description is numbered instead.
 */
object RemTaskChecks {
    enum class Outcome(val heading: String) { FAILED("Failed"), UNRESOLVED("Unresolved"), UNCHECKED("Not checked"), PASSED("Passed") }
    data class Item(val key: String, val title: String, val reason: String, val outcome: Outcome)
    data class Groups(val failed: List<Item>, val unresolved: List<Item>, val unchecked: List<Item>, val passed: List<Item>) {
        val total get() = failed.size + unresolved.size + unchecked.size + passed.size
        /** Whether a review looked at any check. Before that, only "not run yet" shows. */
        val reviewed get() = total > unchecked.size
        fun of(outcome: Outcome) = when (outcome) {
            Outcome.FAILED -> failed
            Outcome.UNRESOLVED -> unresolved
            Outcome.UNCHECKED -> unchecked
            Outcome.PASSED -> passed
        }
    }

    /** Reasons longer than this, or over three lines, start folded. */
    const val LONG_REASON = 240

    fun groups(checks: List<RemCheck>): Groups {
        val items = checks.mapIndexed { index, check ->
            val reason = check.evidence.trim()
            val outcome = when {
                check.result == "pass" -> Outcome.PASSED
                check.result == "fail" -> Outcome.FAILED
                reason.isNotEmpty() -> Outcome.UNRESOLVED
                else -> Outcome.UNCHECKED
            }
            Item(check.id.ifBlank { "check-$index" }, check.description.trim().ifBlank { "Check ${index + 1}" }, reason, outcome)
        }
        fun by(outcome: Outcome) = items.filter { it.outcome == outcome }
        return Groups(by(Outcome.FAILED), by(Outcome.UNRESOLVED), by(Outcome.UNCHECKED), by(Outcome.PASSED))
    }

    /** The count line above the list, worded as on the desktop. Null when the run has no checks. */
    fun summary(groups: Groups): String? {
        val total = groups.total
        if (total == 0) return null
        if (groups.unchecked.size == total) return if (total == 1) "Not checked yet" else "$total checks not run yet"
        if (groups.passed.size == total) return "$total of $total checks passed"
        return listOfNotNull(
            "${groups.passed.size} of $total passed",
            groups.failed.size.takeIf { it > 0 }?.let { "$it failed" },
            groups.unresolved.size.takeIf { it > 0 }?.let { "$it unresolved" },
            groups.unchecked.size.takeIf { it > 0 }?.let { "$it not checked" },
        ).joinToString(", ")
    }

    fun longReason(reason: String) = reason.length > LONG_REASON || reason.lines().size > 3

    /**
     * Which run's checks the task screen lists: a run being fixed after review shows what the
     * review found; otherwise the last finished run's, while nothing else is running.
     */
    fun runToShow(task: RemTask): RemRun? {
        val active = task.activeRun?.takeIf { it.active }
        return if (active != null) active.takeIf { it.state == "repairing" } else task.lastRun
    }
}
