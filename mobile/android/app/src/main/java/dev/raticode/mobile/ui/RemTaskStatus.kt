package dev.raticode.mobile.ui

import dev.raticode.mobile.live.RemRun
import dev.raticode.mobile.live.RemTask

/**
 * How a Rem task reads at a glance, worded and grouped as the desktop's lib/remHome.js does
 * (RUN_STATES, taskActivity and groupTasks), so a task looks the same on both.
 */
internal object RemTaskStatus {
    /** The color family of a status. The glyph and the words always say it too, never color alone. */
    enum class Tone { ACTIVE, ATTENTION, DANGER, SUCCESS, NEUTRAL }

    enum class Kind { RUNNING, QUEUED, ATTENTION, PAUSED, WAITING, SCHEDULED, IDLE, DONE }

    data class RunInfo(val label: String, val tone: Tone, val active: Boolean, val spinning: Boolean)

    /** [state] is the run state behind it, when there is one, for the glyph. */
    data class Activity(val kind: Kind, val label: String, val tone: Tone, val spinning: Boolean, val state: String = "")

    data class Group(val id: String, val label: String, val tasks: List<Pair<RemTask, Activity>>)

    private val RUN_STATES = mapOf(
        "queued" to RunInfo("Waiting to start", Tone.NEUTRAL, active = true, spinning = false),
        "executing" to RunInfo("Working", Tone.ACTIVE, active = true, spinning = true),
        "verifying" to RunInfo("Checking the work", Tone.ACTIVE, active = true, spinning = true),
        "repairing" to RunInfo("Fixing what the check found", Tone.ACTIVE, active = true, spinning = true),
        "cancelling" to RunInfo("Stopping", Tone.NEUTRAL, active = true, spinning = true),
        "blocked" to RunInfo("Needs you", Tone.ATTENTION, active = false, spinning = false),
        "outcome_unknown" to RunInfo("Result unclear", Tone.ATTENTION, active = false, spinning = false),
        "failed" to RunInfo("Didn't finish", Tone.DANGER, active = false, spinning = false),
        "cancelled" to RunInfo("Stopped", Tone.NEUTRAL, active = false, spinning = false),
        "succeeded" to RunInfo("Done", Tone.SUCCESS, active = false, spinning = false),
        // Not run states: the card of a task that waits for other tasks.
        "waiting" to RunInfo("Waiting for another task", Tone.NEUTRAL, active = false, spinning = false),
        "held" to RunInfo("On hold", Tone.ATTENTION, active = false, spinning = false),
    )

    /** A blocked run that isn't waiting on the user couldn't finish, as on the desktop. */
    fun run(state: String, needsUser: Boolean = true): RunInfo =
        if (state == "blocked" && !needsUser) RunInfo("Couldn't finish", Tone.DANGER, active = false, spinning = false)
        else RUN_STATES[state] ?: RunInfo("Status unavailable", Tone.NEUTRAL, active = false, spinning = false)

    fun run(run: RemRun): RunInfo = run(run.state, run.needsUser)

    /** One summary of a task: queued work says why it waits, running work names its step, then attention, paused, next, done. */
    fun activity(task: RemTask, formatWhen: (String, Boolean) -> String = { value, past -> whenText(value, past) }): Activity {
        task.activeRun?.let { run ->
            val info = run(run.state)
            if (info.active) {
                val phase = if (run.state == "queued") run.waitingReason else run.phaseLabel
                return Activity(if (run.state == "queued") Kind.QUEUED else Kind.RUNNING, phase.ifBlank { info.label }, info.tone, info.spinning, run.state)
            }
        }
        // A task that starts after other tasks: waiting for them, or held because one didn't succeed.
        task.dependency?.takeIf { task.state != "paused" }?.let { dependency ->
            return if (dependency.held) Activity(Kind.ATTENTION, dependency.reason.ifBlank { "On hold" }, Tone.ATTENTION, false, "held")
            else Activity(Kind.WAITING, if (dependency.state == "met") "Starting soon" else dependency.reason.ifBlank { "Waiting for another task" },
                Tone.NEUTRAL, false, "waiting")
        }
        val last = task.lastRun
        if (last != null && last.attention) {
            val info = run(last)
            return Activity(Kind.ATTENTION, plain(last.summary).ifBlank { info.label }, info.tone, false, last.state)
        }
        if (task.state == "paused") return Activity(Kind.PAUSED, "Paused", Tone.NEUTRAL, false)
        if (task.nextAt.isNotBlank()) return Activity(Kind.SCHEDULED, "Next ${formatWhen(task.nextAt, false)}", Tone.NEUTRAL, false)
        if (last?.state == "succeeded") return Activity(Kind.DONE, "Done ${formatWhen(last.finishedAt, true)}", Tone.SUCCESS, false, last.state)
        if (last != null) { val info = run(last); return Activity(Kind.DONE, info.label, info.tone, false, last.state) }
        return Activity(Kind.IDLE, "Not scheduled", Tone.NEUTRAL, false)
    }

    private val GROUPS = listOf(
        Triple("now", "Working on", setOf(Kind.RUNNING, Kind.QUEUED)),
        Triple("attention", "Needs you", setOf(Kind.ATTENTION)),
        Triple("scheduled", "Coming up", setOf(Kind.WAITING, Kind.SCHEDULED, Kind.IDLE)),
        Triple("paused", "Paused", setOf(Kind.PAUSED)),
        Triple("done", "Done", setOf(Kind.DONE)),
    )

    /**
     * The desktop's sidebar sections, in its order, keeping the order of [tasks] inside each (the phone's
     * newest-first list). Attention that needs nothing from the user is headed Updates, as on the desktop.
     */
    fun groups(tasks: List<RemTask>, formatWhen: (String, Boolean) -> String = { value, past -> whenText(value, past) }): List<Group> {
        val items = tasks.map { it to activity(it, formatWhen) }
        return GROUPS.mapNotNull { (id, label, kinds) ->
            val members = items.filter { it.second.kind in kinds }
            if (members.isEmpty()) null
            else Group(id, if (id == "attention" && members.all { it.first.lastRun?.needsUser == false }) "Updates" else label, members)
        }
    }

    /** The header's one phrase about what Rem is doing, as the desktop's statusLine says it. */
    fun summary(tasks: List<RemTask>): String {
        val kinds = tasks.map { activity(it) { _, _ -> "" } }
        val working = kinds.count { it.kind == Kind.RUNNING || it.kind == Kind.QUEUED }
        val attention = tasks.zip(kinds).count { (task, activity) -> activity.kind == Kind.ATTENTION && task.lastRun?.needsUser != false }
        return when {
            attention > 0 -> "$attention ${if (attention == 1) "thing needs" else "things need"} you"
            working > 0 -> "Working on $working ${if (working == 1) "thing" else "things"}"
            kinds.any { it.kind == Kind.SCHEDULED } -> "All caught up"
            else -> "Here when you need me"
        }
    }

    /** What each prerequisite of a waiting task shows next to its title, as the desktop's prerequisites(). */
    fun prerequisite(item: dev.raticode.mobile.live.RemPrerequisite): Pair<String, Tone> = when {
        item.state == "met" -> "Done" to Tone.SUCCESS
        item.state == "held" -> (if (item.runState.isNotBlank()) run(item.runState).label else item.reason.ifBlank { "On hold" }) to Tone.ATTENTION
        run(item.runState).active -> "Running" to Tone.NEUTRAL
        else -> "Not started" to Tone.NEUTRAL
    }

    /** Desktop plainMarkdown, enough for a one-line summary: no emphasis marks, link targets or line breaks. */
    fun plain(text: String): String = text
        .replace(Regex("\\[([^\\]]*)\\]\\([^)]*\\)"), "$1")
        .replace(Regex("[*_`#>]+"), "")
        .replace(Regex("\\s+"), " ")
        .trim()
}
