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
    val FILTERS = listOf(NEEDS_YOU, FINISHED, PROPOSALS)
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

    /** What one answer leaves to alert about, and the self-work rounds now remembered. */
    data class Incoming(val notices: List<RemNotice>, val skipped: Int, val rounds: List<String>)

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
            if (chatOpen || settling.isNotEmpty()) 0 else read.skipped, rounds.takeLast(ROUNDS_KEPT))
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

    /** The line the user's own reply adds to the alert. */
    fun replyLine(text: String, showText: Boolean, now: Long) = Line(Sender.YOU, RemNoticeText.reply(text, showText), now)
}
