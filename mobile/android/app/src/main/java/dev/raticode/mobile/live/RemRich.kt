package dev.raticode.mobile.live

import org.json.JSONObject
import java.time.Duration
import java.time.Instant
import java.time.OffsetDateTime
import kotlin.math.roundToInt

/**
 * Rules for the rich parts of Rem's chat: task progress, check counts, result cards, follow-ups and
 * reply details. The desktop has the same rules in frontend/src/lib/remRich.js, so both apps say the same thing.
 */
object RemRich {
    enum class StepState { DONE, CURRENT, STOPPED, TODO }
    data class Step(val key: String, val label: String, val state: StepState) {
        /** Said in words as well as shown, so the step never depends on color. */
        val spoken get() = "$label " + when (state) { StepState.DONE -> "done"; StepState.CURRENT -> "in progress"; StepState.STOPPED -> "stopped"; StepState.TODO -> "not started" }
        val symbol get() = when (state) { StepState.DONE -> "✓"; StepState.CURRENT -> "▶"; StepState.STOPPED -> "■"; StepState.TODO -> "○" }
    }

    private val LABELS = listOf("plan" to "Plan", "work" to "Work", "check" to "Check", "report" to "Report")
    private val AT = mapOf("queued" to 0, "executing" to 1, "cancelling" to 1, "verifying" to 2, "repairing" to 2)
    private val ENDED = setOf("failed", "blocked", "cancelled", "outcome_unknown")

    private val STEP_AT = mapOf("plan" to 0, "work" to 1, "check" to 2)

    /**
     * Plan · Work · Check · Report for a run in [state]; empty for a state with no steps. [step] is the desktop's word for
     * where the run is: a queued run whose work is done but waits to be checked again (a retried review, a restart's
     * recovery check) is at Check, not Plan. An older desktop sends none, and [state] decides as before.
     */
    fun steps(state: String, reachedCheck: Boolean = false, step: String = ""): List<Step> {
        val at = when {
            state == "succeeded" -> 4
            state !in AT && state !in ENDED -> return emptyList()
            step in STEP_AT -> STEP_AT.getValue(step)
            state in AT -> AT.getValue(state)
            else -> if (reachedCheck) 2 else 1
        }
        val stopped = state in ENDED
        return LABELS.mapIndexed { index, (key, label) ->
            Step(key, label, when {
                index < at -> StepState.DONE
                index == at -> if (stopped) StepState.STOPPED else StepState.CURRENT
                else -> StepState.TODO
            })
        }
    }

    fun elapsed(from: String, until: Instant = Instant.now()): String {
        val start = instant(from) ?: return ""
        if (until.isBefore(start)) return ""
        val seconds = Duration.between(start, until).seconds
        if (seconds < 60) return "${maxOf(seconds, 1)} s"
        val minutes = Math.round(seconds / 60.0)
        if (minutes < 60) return "$minutes min"
        val hours = minutes / 60
        return if (minutes % 60 == 0L) "$hours h" else "$hours h ${minutes % 60} min"
    }

    fun instant(value: String): Instant? = if (value.isBlank()) null else runCatching { OffsetDateTime.parse(value).toInstant() }.getOrNull()

    enum class Tone { SUCCESS, DANGER, NEUTRAL }
    data class Checks(val tone: Tone, val symbol: String, val text: String)

    /** A check count with a symbol and words; null when there are no checks. */
    fun checks(checks: RemChecks): Checks? {
        val (passed, failed, total) = checks
        if (total <= 0) return null
        return when {
            failed > 0 -> Checks(Tone.DANGER, "✕", "$failed of $total checks failed")
            passed == total -> Checks(Tone.SUCCESS, "✓", "$passed of $total checks passed")
            passed == 0 -> Checks(Tone.NEUTRAL, "○", if (total == 1) "Check not run yet" else "$total checks not run yet")
            else -> Checks(Tone.NEUTRAL, "◐", "$passed of $total checks passed so far")
        }
    }

    /** The text without paragraphs that are only one of the card's report links; the card shows those as buttons. */
    fun bodyWithoutCardLinks(body: String, result: RemResult?): String {
        val hrefs = result?.artifacts?.map { it.href }?.filter { it.isNotBlank() }.orEmpty()
        if (hrefs.isEmpty()) return body
        val link = Regex("""^\[[^\]\n]*]\(<([^>\n]+)>\)$""")
        return body.split(Regex("\n{2,}")).filterNot { paragraph ->
            link.matchEntire(paragraph.trim())?.groupValues?.get(1)?.let { it in hrefs } == true
        }.joinToString("\n\n").trim()
    }

    fun fileSize(bytes: Long): String = when {
        bytes < 1024 -> "$bytes B"
        bytes < 1024 * 1024 -> "${Math.round(bytes / 1024.0)} KB"
        else -> String.format(java.util.Locale.US, "%.1f MB", bytes / (1024.0 * 1024))
    }

    fun artifactLine(artifact: RemArtifact) = "${artifact.kind.ifBlank { "file" }.uppercase()} · ${fileSize(artifact.size)}"

    /** "claude-sonnet-5-5 · 4.2 s" for a reply, when the desktop recorded it. */
    fun replyMeta(meta: RemMeta?): String {
        if (meta == null) return ""
        val seconds = meta.seconds?.let { String.format(java.util.Locale.US, "%.1f s", it) }.orEmpty()
        val model = meta.model.takeIf { it.isNotBlank() && it != "cli-default" } ?: meta.provider
        return listOf(model, seconds).filter { it.isNotBlank() }.joinToString(" · ")
    }

    /** Reply links a new message to this one. The desktop stores the ID and builds the same preview (reference_preview). */
    fun replyTarget(message: RemMessage, limit: Int = 280): RemReference {
        val plain = plainText(message.body).replace(Regex("\\s+"), " ").trim()
        return RemReference(message.id, message.role.ifBlank { "rem" }, if (plain.length > limit) plain.take(limit - 1).trimEnd() + "…" else plain,
            message.createdAt, if (message.kind == "task_update") "task_update" else "", message.work?.title.orEmpty(),
            message.attachments.size + message.files.size)
    }

    /** Who wrote the message a reply refers to, and the line shown from it. */
    fun referenceLines(reference: RemReference): Pair<String, String> {
        if (reference.unavailable) return "an earlier message" to "No longer in this chat"
        val author = when {
            reference.role == "user" -> "You"
            reference.kind == "task_update" -> "Rem · Task update"
            else -> "Rem"
        }
        val files = when (reference.files) { 0 -> ""; 1 -> "1 file"; else -> "${reference.files} files" }
        return author to (reference.body.ifBlank { null } ?: reference.title.takeIf { it.isNotBlank() }?.let { "Task: $it" } ?: files.ifBlank { "Message" })
    }

    /**
     * A `rem.message` request's params. Screen text and the reply link go only to a desktop that says it takes them;
     * an older desktop would drop the link without a word, so the phone doesn't offer Reply there.
     */
    fun messageParams(text: String, home: RemHome?, screen: Pair<String, String>? = null, replyTo: String? = null): JSONObject {
        val params = JSONObject().put("body", text.trim())
        if (screen != null && home?.screenContext == true) params.put("screen", JSONObject().put("app", screen.first).put("text", screen.second))
        if (!replyTo.isNullOrBlank() && replyTo.length <= 128 && home?.replies == true) params.put("refers_to", replyTo)
        return params
    }

    /** Copy puts the reply and its details on the clipboard as Markdown. */
    fun copyText(message: RemMessage) = listOf(message.body, message.details).map { it.trim() }.filter { it.isNotEmpty() }.joinToString("\n\n")

    /** Markdown without link targets, code ticks, emphasis or heading marks. */
    fun plainText(text: String): String = text
        .replace(Regex("""!?\[([^\]]*)]\((?:<[^>\n]*>|[^()\s]*)\)"""), "$1")
        .replace(Regex("`([^`\n]+)`"), "$1")
        .replace(Regex("""(\*\*|__)(\S[^\n]*?\S|\S)\1"""), "$2")
        .replace(Regex("""(^|[^\w*])\*(\S[^*\n]*?\S|\S)\*(?![\w*])"""), "$1$2")
        .replace(Regex("(?m)^ {0,3}#{1,6}[ \t]+"), "")

    enum class Presence { OFFLINE, THINKING, WORKING, ATTENTION, IDLE }

    /** Rem's presence in the header: a state for the face and the words beside it. */
    fun presence(connected: Boolean, home: RemHome?, status: String): Pair<Presence, String> = when {
        !connected -> Presence.OFFLINE to "Not connected"
        home?.turnState == "replying" -> Presence.THINKING to (home.turnLabel.takeIf { it.isNotBlank() }?.let { "$it…" } ?: "Thinking…")
        Regex("needs? you").containsMatchIn(status) -> Presence.ATTENTION to status
        home?.tasks?.any { it.activeRun?.active == true } == true -> Presence.WORKING to status
        else -> Presence.IDLE to status
    }

    /** An action chip under a finished task's update; [artifact] is set for Open report. */
    data class Action(val id: String, val label: String, val taskId: String = "", val runId: String = "", val artifact: RemArtifact? = null)

    /**
     * Open report, Retry review and Run again under the newest update about a finished task. They come only from the
     * task record, never from what Rem wrote, and only when the control would work: a saved report, a task that isn't
     * running, and for Retry review the run the desktop marks `review_retryable` (and a desktop that has that action).
     */
    fun resultActions(message: RemMessage, tasks: List<RemTask>, live: Boolean, reviewRetry: Boolean): List<Action> {
        val work = message.work
        if (!live || message.role == "user" || message.kind != "task_update" || work == null) return emptyList()
        val actions = mutableListOf<Action>()
        message.result?.artifacts?.firstOrNull()?.let { actions.add(Action("open", "Open report", artifact = it)) }
        val task = tasks.firstOrNull { it.id == work.taskId } ?: return actions
        if (task.state == "deleted" || task.activeRun?.active == true) return actions
        // A task waiting for others: retry each prerequisite that didn't succeed, or start it now.
        task.dependency?.let { dependency ->
            dependency.prerequisites.filter { it.retryable }.forEach { actions.add(Action("retry-after:${it.taskId}", "Retry “${it.title}”", it.taskId)) }
            actions.add(Action("run", "Start now", task.id))
            return actions
        }
        val run = task.lastRun
        if (reviewRetry && run != null && run.id == work.runId && run.reviewRetryable) actions.add(Action("retry", "Retry review", task.id, run.id))
        actions.add(Action("run", "Run again", task.id))
        return actions
    }

    /**
     * The messages whose task card is the live one: the newest card of each run. A card from before its task had a run
     * (a task on hold) is keyed by the task and gives way to the first card of that task's run.
     */
    fun liveWork(messages: List<RemMessage>): Set<String> {
        val latest = linkedMapOf<String, String>()
        for (message in messages) {
            val work = message.work ?: continue
            if (work.runId.isNotBlank()) { latest[work.runId] = message.id; latest.remove("task:" + work.taskId) }
            else if (work.taskId.isNotBlank()) latest["task:" + work.taskId] = message.id
        }
        return latest.values.toSet()
    }

    /** The note under a request the user queued behind other tasks, as the desktop words it. */
    fun queuedNote(titles: List<String>): String =
        "Queued until ${titles.joinToString(", ") { "“$it”" }.ifBlank { "earlier tasks" }} finished"

    /** Follow-ups show under the newest Rem message only, until you answer it. */
    fun followUpsFor(messages: List<RemMessage>): String? = messages.lastOrNull()?.takeIf { it.role != "user" && it.suggestions.isNotEmpty() }?.id

    /** A long message folds to [preview] dp; only one taller than [threshold] dp folds, so folding hides a useful amount. */
    data class Fold(val preview: Int, val threshold: Int)

    /** About a third of the screen, the same numbers as the desktop's collapseLimits in px. */
    fun collapseLimits(screenHeight: Int): Fold {
        val height = if (screenHeight > 0) screenHeight else 800
        val preview = (height * .3).coerceIn(168.0, 360.0).roundToInt()
        return Fold(preview, (preview * 1.5).roundToInt())
    }

    /** Rem's newest message opens in full, since it is the one being read; older long messages start folded. */
    fun latestRemMessageId(messages: List<RemMessage>): String? = messages.lastOrNull { it.role != "user" }?.id

    /**
     * A message sent while Rem was still replying. The desktop holds it and Rem answers it next, so it shows as
     * waiting instead of the phone refusing it with "try again". An older desktop refused such messages instead.
     */
    const val WAITING = "waiting"
    const val WAITING_NOTE = "Sends when Rem finishes replying"
    fun waiting(message: RemMessage): Boolean = message.role == "user" && message.state == WAITING

    /** The phone's copy of Rem's reply as it is written, and the attempt (model call) it came from. */
    data class Draft(val text: String = "", val attempt: Long? = null)

    /** Applies a `rem.draft` delta. A gap starts over: the next read asks for the whole draft. */
    fun applyDraft(draft: Draft, delta: RemDraftDelta): Draft = when {
        !delta.replying -> Draft()
        delta.reset -> Draft(delta.text.take(4000), delta.attempt)
        delta.start == draft.text.length -> Draft((draft.text + delta.text).take(4000), delta.attempt ?: draft.attempt)
        else -> Draft()
    }

    /**
     * The next `rem.draft` read. It names the attempt the phone's text came from, so a desktop whose answer to an
     * earlier read was lost still sends the whole new text when Rem started over, instead of adding to old text.
     */
    fun draftRead(draft: Draft): JSONObject = JSONObject().put("since", draft.text.length).apply { draft.attempt?.let { put("attempt", it) } }
}
