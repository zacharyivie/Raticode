package dev.raticode.mobile.ui

import androidx.compose.runtime.saveable.Saver

/**
 * Where the Rem tab is. The chat is the base; [tasks] is the task list opened from Rem's status, and a task, a work
 * thread, memories or decisions open on top of whichever of the two you were on. Back closes the top one, so a task opened from
 * the list returns to the list and one opened from a chat card returns to the chat. A task opened from a waiting question in
 * decisions returns to the decisions.
 */
internal data class RemNav(val tasks: Boolean = false, val taskId: String? = null, val threadId: String? = null, val memories: Boolean = false,
                          val decisions: Boolean = false) {
    /** True when the chat and its message box are on screen. */
    val chatShown get() = !tasks && taskId == null && threadId == null && !memories && !decisions

    fun back(): RemNav = when {
        // A task opened from a question's asked line, and its work thread, sit on top of the decisions.
        decisions && threadId != null -> copy(threadId = null)
        decisions && taskId != null -> copy(taskId = null)
        decisions -> copy(decisions = false)
        memories -> copy(memories = false)
        threadId != null -> copy(threadId = null)
        taskId != null -> copy(taskId = null)
        else -> copy(tasks = false)
    }

    companion object {
        val Saver = Saver<RemNav, List<Any?>>(
            save = { listOf(it.tasks, it.taskId, it.threadId, it.memories, it.decisions) },
            restore = { RemNav(it[0] as Boolean, it[1] as String?, it[2] as String?, it[3] as Boolean, it.getOrNull(4) as? Boolean ?: false) },
        )

        /** How many tasks the list shows, for the task screen's subtitle and Rem's status button. Empty until the desktop answers. */
        fun tasksCount(listed: Int?) = when (listed) { null -> ""; 1 -> "1 task"; else -> "$listed tasks" }
    }
}
