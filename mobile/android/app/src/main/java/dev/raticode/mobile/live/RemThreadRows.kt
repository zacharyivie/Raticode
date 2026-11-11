package dev.raticode.mobile.live

/**
 * A work thread laid out like the desktop's thread pane: instructions, replies,
 * and each run of thoughts folded into one group.
 */
object RemThreadRows {
    sealed interface Row { val id: String }
    /** [author] is `rem` for Rem's prompt to the worker, `user` for the user's own, empty when the desktop doesn't say. */
    data class Instruction(override val id: String, val body: String, val author: String = "") : Row
    data class Reply(override val id: String, val body: String) : Row
    data class Failure(override val id: String, val body: String) : Row
    /**
     * [replied] means a reply follows before the next instruction, so the group starts
     * folded, as the desktop hides finished thoughts. [running] marks the group still being written.
     */
    data class Thoughts(override val id: String, val count: Int, val steps: List<RemThreadStep>,
                        val replied: Boolean, val running: Boolean) : Row

    fun build(thread: RemThread): List<Row> {
        val rows = mutableListOf<Row>()
        val legacy = mutableListOf<RemThreadMessage>()
        // An older desktop sends each thought on its own; fold those here instead.
        fun close() {
            if (legacy.isEmpty()) return
            val steps = legacy.filter { it.body.isNotBlank() }.map { RemThreadStep("summary", it.body.take(120), "", "", "", "") }
            if (steps.isNotEmpty()) rows += Thoughts(legacy.first().id, steps.size, steps, replied = false, running = false)
            legacy.clear()
        }
        for (message in thread.messages) {
            if (message.kind == "thought") { legacy += message; continue }
            close()
            rows += when {
                message.kind == "thoughts" -> Thoughts(message.id, maxOf(message.count, message.steps.size), message.steps, replied = false, running = false)
                message.role == "user" -> Instruction(message.id, message.body,
                    message.sender.takeIf { it == "rem" || it == "user" }.orEmpty())
                message.kind == "error" -> Failure(message.id, message.body)
                else -> Reply(message.id, message.body)
            }
        }
        close()
        var replied = false
        for (index in rows.indices.reversed()) {
            when (val row = rows[index]) {
                is Instruction -> replied = false
                is Reply -> if (row.body.isNotBlank()) replied = true
                is Thoughts -> rows[index] = row.copy(replied = replied,
                    running = thread.running && index == rows.lastIndex)
                is Failure -> {}
            }
        }
        return rows.filterNot { it is Reply && it.body.isBlank() }
    }

    /** The step's second line: what it did, or its command when it has no description. */
    fun stepDetail(step: RemThreadStep): String = step.detail.ifBlank { step.command }

    /** A long instruction starts folded so the work below it stays in reach on a phone. */
    fun longInstruction(body: String): Boolean = body.length > 600 || body.count { it == '\n' } >= 8
}
