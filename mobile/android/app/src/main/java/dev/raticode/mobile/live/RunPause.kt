package dev.raticode.mobile.live

/**
 * Pausing one Rem run, as the desktop's task controls do. A paused run keeps its thread,
 * provider session and worktree and holds no worker; resuming continues that same run in its
 * thread. Pausing a repeating task's schedule (`rem.task.pause`) is separate.
 *
 * Only desktops reporting `run_pause` take these actions or send the `paused` state; an
 * older desktop shows a running run's Stop only.
 */
object RunPause {
    const val PAUSE = "rem.run.pause"
    const val RESUME = "rem.run.resume"
    const val PAUSED = "paused"
    const val FOR_YOU = "Paused for your work"
    const val UPDATE_DESKTOP = "Update Raticode on your computer to pause and resume tasks from this phone."

    /** "priority" when self-work made way for the user's own work; otherwise the user paused it. */
    fun by(value: String): String = when (value) {
        "priority" -> "priority"
        "user" -> "user"
        else -> ""
    }

    fun label(pausedBy: String): String = if (pausedBy == "priority") FOR_YOU else "Paused"

    /** What a task's run controls can do now: pause a run that is going, or resume a paused one. */
    sealed interface Control {
        data class Pause(val runId: String) : Control
        data class Resume(val runId: String) : Control
        data object None : Control
    }

    fun control(task: RemTask, supported: Boolean): Control {
        if (!supported) return Control.None
        val active = task.activeRun
        if (active != null && active.active && active.state != "cancelling") return Control.Pause(active.id)
        val last = task.lastRun
        if (last != null && last.paused) return Control.Resume(last.id)
        return Control.None
    }

    /** Why a paused run waits, in one sentence, for its task screen. */
    fun note(pausedBy: String): String = if (pausedBy == "priority")
        "Rem paused this self-work so your own work could run first. It picks up in the same thread once your work is done and you've been away 15 minutes."
    else "Its thread and any worktree are kept. Resume continues in the same thread, where the worker checks what it already did."
}
