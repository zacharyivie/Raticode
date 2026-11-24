package dev.raticode.mobile.live

/**
 * The decisions behind Rem's chat alerts, in plain Kotlin so unit tests cover them. [RemNotifications],
 * [RemReplyReceiver] and the settings screen only carry them out with Android's APIs.
 */
object RemAlertRules {
    /** The longest reply an alert sends; Rem's chat takes the same. */
    const val REPLY_LIMIT = 4000
    /** All the lock screen ever shows of a Rem alert, whatever the alert holds and whichever switches are on. */
    const val LOCK_SCREEN_TITLE = "Rem"
    const val LOCK_SCREEN_TEXT = "New message from Rem"

    /** The alert switches, by the names `rem.notices` uses for them. The desktop still decides what counts under each. */
    const val NEEDS_YOU = "needs_you"
    const val FINISHED = "finished"
    const val PROPOSALS = "proposals"
    /** Messages workflows send with a notification step set to `channel: phone`. Only desktops advertising `rem_notices_workflow` take it. */
    const val WORKFLOW = "workflow"
    val FILTERS = listOf(NEEDS_YOU, FINISHED, PROPOSALS, WORKFLOW)
    /** The lock screen's whole view of a workflow message's alert. */
    const val WORKFLOW_LOCK_SCREEN_TITLE = "Raticode"
    const val WORKFLOW_GENERIC = "A workflow sent a message"
    /** Where a tapped workflow message opens, as the notification's destination: `workflow/<workflow ID>/<run ID>`. */
    private const val WORKFLOW_TARGET = "workflow/"
    /** Notification groups for workflow messages, one per workflow, and the key prefix of a run's "And N more" alert. */
    private const val WORKFLOW_GROUP = "rem-workflow:"
    private const val WORKFLOW_MORE = "more:"
    /** A question of Rem's that waits for the user while no task stops for it; it counts under [NEEDS_YOU]. */
    const val DECISION = "decision"
    const val QUESTION_GENERIC = "Rem has a question"
    /** Where a tapped question opens, as the notification's destination: `decision/<decision ID>`. */
    private const val DECISION_TARGET = "decision/"
    private val DECISION_ID = Regex("D-[0-9]{1,9}")
    /** Self-work rounds remembered, so one round alerts once however many answers name it. */
    const val ROUNDS_KEPT = 16

    /** Local wall-clock hours, in minutes after midnight. [end] may be earlier than [start], which spans midnight. */
    data class QuietHours(val on: Boolean = false, val start: Int = 22 * 60, val end: Int = 7 * 60) {
        /** The same start and end leave no hours to be quiet in. */
        val empty get() = start == end
    }

    /** Whether alerts are silent at [minute] minutes after midnight, local time. */
    fun quiet(hours: QuietHours, minute: Int): Boolean = when {
        !hours.on || hours.empty -> false
        hours.start < hours.end -> minute >= hours.start && minute < hours.end
        else -> minute >= hours.start || minute < hours.end
    }

    /** What one answer leaves to alert about, the self-work rounds now remembered, and the workflow messages the desktop deleted unread. */
    data class Incoming(val notices: List<RemNotice>, val skipped: Int, val rounds: List<String>, val dropped: Int = 0)

    /** The [FILTERS] names a desktop takes in `include`: the workflow switch only goes to one that advertises it. */
    fun sendable(include: List<String>, workflowSupported: Boolean): List<String> = include.filter { it != WORKFLOW || workflowSupported }

    /**
     * Applies the switches to an answer: a read already on its way when a switch went off can still carry that category, and a
     * self-work round alerts once. [chatOpen] holds back only what Rem's chat is showing; proposals and organization tasks aren't in it.
     * [settling] names switches just turned on: what they find from before that stays quiet, and a round found then doesn't alert later.
     */
    fun incoming(read: RemNotices, filters: Collection<String>, seen: List<String>, chatOpen: Boolean, settling: Collection<String> = emptyList()): Incoming {
        val rounds = seen.toMutableList()
        val kept = read.notices.filter { it.reason.isEmpty() || it.reason in filters }.filter {
            if (it.kind != "proposals") return@filter true
            if (it.id in rounds) return@filter false
            rounds += it.id
            true
        }.filter { it.reason !in settling }
        return Incoming(if (chatOpen) kept.filter { it.kind != "reply" && it.kind != "task_update" } else kept,
            if (chatOpen || settling.isNotEmpty()) 0 else read.skipped, rounds.takeLast(ROUNDS_KEPT),
            if (WORKFLOW in filters && WORKFLOW !in settling) read.dropped else 0)
    }

    /** What a reply typed into Rem's alert does. */
    sealed interface Reply {
        /** Alerts were turned off after this one showed: remove it and send nothing. */
        data object Withdraw : Reply
        /** Not sent; the alert shows [note], which never repeats the reply. */
        data class Refuse(val note: String) : Reply
        data class Send(val text: String) : Reply
    }

    /** [locked] is Android's `isDeviceLocked`; the reply action already asks for unlock on Android 12 and newer. */
    fun reply(enabled: Boolean, sdk: Int, locked: Boolean, typed: CharSequence?, childLocked: Boolean = false): Reply {
        val text = typed?.toString()?.trim().orEmpty()
        return when {
            !enabled -> Reply.Withdraw
            childLocked -> Reply.Refuse("Not sent. Raticode's child lock is on.")
            sdk < 29 -> Reply.Refuse("Open Raticode to reply.")
            locked -> Reply.Refuse("Not sent. Unlock your phone, then reply again.")
            text.isEmpty() -> Reply.Refuse("Nothing was sent.")
            text.length > REPLY_LIMIT -> Reply.Refuse("Not sent. Replies here can be up to 4,000 characters; open Rem's chat for longer messages.")
            else -> Reply.Send(text)
        }
    }

    /** Where a settings button sends the user to fix a blocked state. */
    enum class Fix { NONE, ASK_PERMISSION, APP_SETTINGS, CHANNEL_SETTINGS }

    /** What the Rem chat notification settings say once alerts are on, most basic problem first. */
    enum class State(val problem: String?, val fix: Fix) {
        NO_PERMISSION("Raticode doesn't have permission to show notifications.", Fix.ASK_PERMISSION),
        APP_BLOCKED("Android has blocked notifications for Raticode.", Fix.APP_SETTINGS),
        CHANNEL_BLOCKED("The Rem notification category is turned off in Android settings.", Fix.CHANNEL_SETTINGS),
        DESKTOP_OFF("Turn on Use Raticode from this phone in desktop Paired devices to get Rem's alerts.", Fix.NONE),
        DESKTOP_OLD("Update Raticode on your computer to get Rem's alerts.", Fix.NONE),
        READY(null, Fix.NONE);

        /** Android lets the app post the alert; the desktop may still be off or too old. */
        val androidAllows get() = this !in setOf(NO_PERMISSION, APP_BLOCKED, CHANNEL_BLOCKED)
    }

    /**
     * [permission] is the POST_NOTIFICATIONS grant (always true before Android 13), [appEnabled] Android's switch
     * for the whole app, [channelBlocked] the Rem category's switch, and [supported] the desktop's `rem_notices`,
     * null until its overview arrives.
     */
    fun state(permission: Boolean, appEnabled: Boolean, channelBlocked: Boolean, desktopControl: Boolean, supported: Boolean?): State = when {
        !permission -> State.NO_PERMISSION
        !appEnabled -> State.APP_BLOCKED
        channelBlocked -> State.CHANNEL_BLOCKED
        !desktopControl -> State.DESKTOP_OFF
        supported == false -> State.DESKTOP_OLD
        else -> State.READY
    }

    /** Stored switches: [get] reads one, [put] saves several at once. */
    interface Flags {
        fun get(key: String): Boolean
        fun put(values: Map<String, Boolean>)
        fun number(key: String, default: Int): Int
        fun putNumber(key: String, value: Int)
    }

    /** What changing a switch does to the alert on screen and to Android's records of it. */
    interface Surface {
        fun clear()
        fun forgetConversation()
        fun createChannel()
    }

    /**
     * The opt-ins, all off until the user turns them on. Turning alerts off removes the alert and Rem's
     * conversation shortcut; hiding Rem's words removes an alert that may show them. Turning alerts on asks the
     * client, once, to start from the desktop's current message so older ones never alert. The [FILTERS] switches
     * and quiet hours only change later alerts.
     */
    class Switches(private val flags: Flags, private val surface: Surface) {
        val enabled get() = flags.get(ENABLED)
        val showText get() = flags.get(SHOW_TEXT)
        /** The [FILTERS] names that are on, in the order the desktop is told them. */
        val include get() = FILTERS.filter { flags.get(it) }
        val quietHours get() = QuietHours(flags.get(QUIET_ON), flags.number(QUIET_START, QuietHours().start).coerceIn(0, DAY_END),
            flags.number(QUIET_END, QuietHours().end).coerceIn(0, DAY_END))

        /** The switches turned on whose first complete read hasn't come back, so what it finds is from before. */
        val settling get() = FILTERS.filter { flags.get(SETTLING + it) }

        fun setFilter(name: String, on: Boolean) {
            require(name in FILTERS)
            flags.put(mapOf(name to on, SETTLING + name to on))
        }

        fun settled(names: Collection<String>) = flags.put(names.associate { SETTLING + it to false })

        fun setQuietHours(hours: QuietHours) {
            flags.put(mapOf(QUIET_ON to hours.on))
            flags.putNumber(QUIET_START, hours.start.coerceIn(0, DAY_END))
            flags.putNumber(QUIET_END, hours.end.coerceIn(0, DAY_END))
        }

        fun setEnabled(on: Boolean) {
            flags.put(mapOf(ENABLED to on, BASELINE to on))
            if (on) surface.createChannel() else { surface.clear(); surface.forgetConversation() }
        }

        fun setShowText(on: Boolean) {
            flags.put(mapOf(SHOW_TEXT to on))
            if (!on) surface.clear()
        }

        /** True once after alerts are turned on. */
        fun takeBaseline(): Boolean {
            if (!flags.get(BASELINE)) return false
            flags.put(mapOf(BASELINE to false))
            return true
        }

        private companion object {
            const val ENABLED = "enabled"
            const val SHOW_TEXT = "show_text"
            const val BASELINE = "baseline"
            const val SETTLING = "settling_"
            const val QUIET_ON = "quiet_on"
            const val QUIET_START = "quiet_start"
            const val QUIET_END = "quiet_end"
            const val DAY_END = 24 * 60 - 1
        }
    }

    /** Who a line in the alert is from. */
    enum class Sender { REM, RATICODE, YOU }
    data class Line(val sender: Sender, val text: String, val at: Long)

    /** The lines Rem's new messages add to the alert. Without the words opt-in they carry no reply text or task name. */
    fun lines(notices: List<RemNotice>, skipped: Int, showText: Boolean, now: Long): List<Line> = buildList {
        RemNoticeText.earlier(skipped)?.let { add(Line(Sender.RATICODE, it, now)) }
        notices.forEach { add(Line(Sender.REM, RemNoticeText.line(it, showText), RemNoticeText.millis(it.createdAt) ?: now)) }
    }

    /** Workflow messages get an alert each, grouped by workflow, which opens their run; everything else joins Rem's conversation. */
    fun split(notices: List<RemNotice>): Pair<List<RemNotice>, List<RemNotice>> = notices.partition { it.kind != WORKFLOW }

    /** Rem's questions get an alert each, which opens Decisions on them; the rest goes on to [split]. */
    fun questions(notices: List<RemNotice>): Pair<List<RemNotice>, List<RemNotice>> = notices.partition { it.kind == DECISION }

    /** A question's line: "Rem has a question: <question>" with the words opt-in, else only "Rem has a question". */
    fun decisionText(notice: RemNotice, showText: Boolean): String {
        val question = if (showText) RemNoticeText.plain(notice.question.ifBlank { notice.title }) else ""
        return if (question.isBlank()) QUESTION_GENERIC else "$QUESTION_GENERIC: $question"
    }

    /** One question's alert. [key] is one per decision, so the same question never shows twice; [target] opens it in Decisions. */
    data class DecisionAlert(val key: String, val title: String, val text: String, val target: String, val at: Long)

    /** With the words opt-in, the question, its project and Rem's recommended answer; without it, only that Rem has a question. */
    fun decisionAlert(notice: RemNotice, showText: Boolean, now: Long): DecisionAlert {
        val details = listOf(notice.projectName, notice.recommended.takeIf { it.isNotBlank() }?.let { "Rem recommends: $it" }.orEmpty())
            .map { RemNoticeText.plain(it) }.filter { it.isNotBlank() }.joinToString(" · ")
        val text = if (showText && details.isNotBlank()) details else "Tap to answer it in Decisions."
        return DecisionAlert(DECISION + ":" + notice.decisionId, decisionText(notice, showText), text, decisionTarget(notice.decisionId),
            RemNoticeText.millis(notice.createdAt) ?: now)
    }

    /** The destination for one decision on the Decisions screen. */
    fun decisionTarget(decisionId: String): String = DECISION_TARGET + decisionId

    /** The decision ID in a [decisionTarget], or null for any other destination. */
    fun parseDecisionTarget(target: String?): String? =
        target?.takeIf { it.startsWith(DECISION_TARGET) }?.removePrefix(DECISION_TARGET)?.takeIf { DECISION_ID.matches(it) }

    /**
     * One workflow message's alert. [target] is where a tap opens; [key] tells one message's alert from another's. A run's
     * "And N more" notice ([more] = N) keeps one key per run, so a later count replaces the earlier one.
     */
    data class WorkflowAlert(val key: String, val title: String, val text: String, val target: String, val at: Long, val more: Int = 0)

    /** Without the words opt-in, the alert says only that a workflow sent a message; with it, the message's title and text. */
    fun workflowAlert(notice: RemNotice, showText: Boolean, now: Long): WorkflowAlert {
        val title = if (showText) RemNoticeText.plain(notice.title).ifBlank { RemNoticeText.plain(notice.workflowName) }.ifBlank { WORKFLOW_GENERIC } else WORKFLOW_GENERIC
        val folded = notice.moreFromRun > 0
        val text = when {
            showText && !folded -> RemNoticeText.plain(notice.body)
            folded -> "And ${notice.moreFromRun} more from this run. Tap to open it."
            // Raticode's own notices about a workflow, such as a trigger waiting for approval, name no run.
            notice.runId.isBlank() -> "Tap to open the workflow."
            else -> "Tap to open the run."
        }
        val key = if (folded) WORKFLOW_MORE + notice.workflowId + "/" + notice.runId else notice.id
        return WorkflowAlert(key, title, text, workflowTarget(notice.workflowId, notice.runId), RemNoticeText.millis(notice.createdAt) ?: now, notice.moreFromRun)
    }

    /** One workflow's alerts from one read, under the notification group [key]. [name] is its newest non-blank name. */
    data class WorkflowGroup(val key: String, val name: String, val alerts: List<WorkflowAlert>)

    /** The notification group a workflow message goes in: one per workflow. */
    fun workflowGroup(notice: RemNotice): String = WORKFLOW_GROUP + notice.workflowId.ifBlank { notice.workflowName }

    /** A read's workflow messages by workflow, in the order each workflow first appears. */
    fun workflowGroups(notices: List<RemNotice>, showText: Boolean, now: Long): List<WorkflowGroup> =
        notices.groupBy { workflowGroup(it) }.map { (key, items) ->
            WorkflowGroup(key, items.lastOrNull { it.workflowName.isNotBlank() }?.workflowName?.let { RemNoticeText.plain(it) }.orEmpty(),
                items.map { workflowAlert(it, showText, now) })
        }

    /** A child alert already showing in a group: its [key] and the messages past the cap it stands for. */
    data class Shown(val key: String, val more: Int = 0)

    /**
     * The group's summary alert, such as "4 messages from Nightly backup". [count] adds up the alerts showing and the new ones,
     * a new alert replacing a shown one with the same key, and an "And N more" alert counting as N + 1. Its tap opens the
     * newest run. Without the words opt-in it doesn't name the workflow.
     */
    data class WorkflowSummary(val key: String, val title: String, val text: String, val target: String, val at: Long, val count: Int)

    fun workflowSummary(group: WorkflowGroup, shown: List<Shown>, showText: Boolean): WorkflowSummary {
        val children = shown.associate { it.key to it.more } + group.alerts.associate { it.key to it.more }
        val count = children.values.sumOf { 1 + it }
        val messages = if (count == 1) "1 message" else "$count messages"
        val title = if (showText && group.name.isNotBlank()) "$messages from ${group.name}" else if (count == 1) "1 workflow message" else "$count workflow messages"
        val newest = group.alerts.last()
        return WorkflowSummary(group.key, title, "Tap to open the newest run.", newest.target, newest.at, count)
    }

    /** The alert for workflow messages the desktop deleted before this phone read them, or null when none were. */
    fun missed(dropped: Int): String? = when {
        dropped <= 0 -> null
        dropped == 1 -> "1 workflow message was deleted on your computer before this phone got it."
        else -> "$dropped workflow messages were deleted on your computer before this phone got them."
    }

    /** The destination for a workflow's run. A blank workflow ID opens the workflow list. */
    fun workflowTarget(workflowId: String, runId: String): String =
        WORKFLOW_TARGET + java.net.URLEncoder.encode(workflowId, "UTF-8") + "/" + java.net.URLEncoder.encode(runId, "UTF-8")

    /** The workflow and run IDs in a [workflowTarget], or null for any other destination. */
    fun parseWorkflowTarget(target: String?): Pair<String, String>? {
        if (target == null || !target.startsWith(WORKFLOW_TARGET)) return null
        val parts = target.removePrefix(WORKFLOW_TARGET).split('/')
        if (parts.size != 2) return null
        return runCatching { java.net.URLDecoder.decode(parts[0], "UTF-8") to java.net.URLDecoder.decode(parts[1], "UTF-8") }.getOrNull()
    }

    /** The line the user's own reply adds to the alert. */
    fun replyLine(text: String, showText: Boolean, now: Long) = Line(Sender.YOU, RemNoticeText.reply(text, showText), now)
}
