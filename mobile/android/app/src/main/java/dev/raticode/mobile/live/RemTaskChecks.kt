package dev.raticode.mobile.live

/** One check of a run as the desktop's `rem.task` sends it: pass, fail or unknown, with the reviewer's reason. */
data class RemCheck(val id: String, val description: String, val result: String, val evidence: String)

/**
 * A review finding kept on the run as a note: [severity] is note, or should_fix after its one return; [axis] is spec
 * (what the task asked) or standards (how the change is written); [file] may be empty.
 */
data class RemReviewNote(val severity: String, val axis: String, val file: String, val explanation: String)

/**
 * A run's checks by outcome for the task screen, as the desktop's task pane groups them.
 *
 * An unknown check with a reason was reviewed but not confirmed (unresolved); one without
 * a reason hasn't been checked. A check without a description is numbered instead.
 */
object RemTaskChecks {
    /** [NOTES] is never a check's outcome: it marks the review's notes, listed after the checks. */
    enum class Outcome(val heading: String) { FAILED("Failed"), UNRESOLVED("Unresolved"), UNCHECKED("Not checked"), PASSED("Passed"), NOTES("Notes") }
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
            Outcome.NOTES -> emptyList()
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

    private val NOTE_SEVERITY = mapOf("note" to "Note", "should_fix" to "Should fix", "blocker" to "Blocker")
    private val NOTE_AXIS = mapOf("spec" to "task", "standards" to "standards")

    /**
     * The review's notes as list items, as the desktop's task pane shows them: the explanation, then a reason
     * line such as "Note · standards · src/a.py". Notes without an explanation are left out.
     */
    fun notes(notes: List<RemReviewNote>): List<Item> = notes.filter { it.explanation.isNotBlank() }.mapIndexed { index, note ->
        val reason = listOfNotNull(NOTE_SEVERITY[note.severity] ?: "Note", NOTE_AXIS[note.axis], note.file.trim().ifBlank { null })
        Item("note-$index", note.explanation.trim(), reason.joinToString(" · "), Outcome.NOTES)
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
