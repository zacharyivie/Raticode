package dev.raticode.mobile.live

/**
 * Holds back new requests while a received event is being applied.
 *
 * Applying an event works on a copy of the journal and saves that copy at the end. A request queued
 * part-way through (a link preview sent as an earlier one finishes, for example) saves and sends from the
 * live journal, which the copy then overwrites. That put the request counter back, so the phone's next
 * request reused a number the desktop already had and was refused, and the queued request was dropped.
 *
 * Requests queued inside [during] wait until the copy is saved and then run in order, even if applying
 * failed. Everything runs on the device-sync thread; this class is not thread-safe.
 */
class CommitGate {
    private var open = false
    private val waiting = ArrayDeque<() -> Unit>()

    /** True while [during] runs. */
    val holding: Boolean get() = open

    /**
     * Runs [block], then everything it held back. Each held action runs even if [block] or an earlier action
     * failed; the first failure is rethrown afterwards with later ones attached.
     */
    fun <T> during(block: () -> T): T {
        check(!open) { "Events are applied one at a time" }
        open = true
        val result = runCatching(block)
        open = false
        var failure = result.exceptionOrNull()
        while (true) {
            // An action may queue another one; with nothing held it runs at once.
            val action = waiting.removeFirstOrNull() ?: break
            try { action() }
            catch (problem: Throwable) { if (failure == null) failure = problem else failure.addSuppressed(problem) }
        }
        failure?.let { throw it }
        return result.getOrThrow()
    }

    /** Runs [action] now, or after the event being applied is saved. */
    fun afterCommit(action: () -> Unit) {
        if (open) waiting.addLast(action) else action()
    }
}
