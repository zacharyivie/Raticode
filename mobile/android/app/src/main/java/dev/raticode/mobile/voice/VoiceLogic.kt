package dev.raticode.mobile.voice

import dev.raticode.mobile.data.LongText
import dev.raticode.mobile.live.PhoneActionList
import dev.raticode.mobile.live.PhoneActions
import dev.raticode.mobile.live.RemHome
import dev.raticode.mobile.live.RemMessage

/** Whether a voice request can go to desktop Rem now. */
enum class DeskReadiness {
    /** The phone's protected storage is still opening. */
    LOADING,
    NOT_PAIRED,
    /** Paired, but this phone hasn't heard from the desktop since it started. */
    CONNECTING,
    /** The desktop answered with Use Raticode from this phone turned off. */
    CONTROL_OFF,
    /** Messages go out now, or wait in the phone's encrypted queue until the desktop is reachable. */
    READY,
}

/** Decisions for voice mode that don't need Android. */
internal object VoiceLogic {
    /** Rem's message limit from the phone, the same as the Rem chat box. Longer words go as a text file ([LongText]). */
    const val MAX_CHARS = LongText.LIMIT

    /**
     * [synced] means a desktop sync snapshot arrived since the app started; until then the phone doesn't know whether
     * the desktop allows Rem from this phone. After one, the desktop control setting is known even while offline.
     */
    fun readiness(loaded: Boolean, paired: Boolean, synced: Boolean, desktopControl: Boolean): DeskReadiness = when {
        !loaded -> DeskReadiness.LOADING
        !paired -> DeskReadiness.NOT_PAIRED
        !synced -> DeskReadiness.CONNECTING
        !desktopControl -> DeskReadiness.CONTROL_OFF
        else -> DeskReadiness.READY
    }

    /** The desktop stored the message; its ID in Rem's chat is the phone's request ID. */
    fun accepted(home: RemHome?, request: String): Boolean = home?.messages?.any { it.id == request && it.role == "user" } == true

    /**
     * Rem's answer to [request]: the first message from Rem after the user's own, once Rem has stopped replying.
     * A started task's notice counts, since that is Rem's reply for now.
     */
    fun reply(home: RemHome?, request: String): RemMessage? {
        home ?: return null
        if (home.turnState == "replying") return null
        val at = home.messages.indexOfFirst { it.id == request && it.role == "user" }
        if (at < 0) return null
        return home.messages.drop(at + 1).firstOrNull { it.role != "user" && (it.body.isNotBlank() || it.details.isNotBlank()) }
    }

    /**
     * The phone action cards answering [request]: those on every Rem message after it, up to the next message from the
     * user. When the reply also started a task, the desktop puts the cards on a message of their own after the notice.
     */
    fun actions(home: RemHome?, request: String): PhoneActionList {
        val at = home?.messages?.indexOfFirst { it.id == request && it.role == "user" } ?: -1
        if (at < 0) return PhoneActionList.EMPTY
        val lists = home!!.messages.drop(at + 1).takeWhile { it.role != "user" }.map { it.phoneActions }
        val all = lists.flatMap { it.actions }
        return PhoneActionList(all.take(PhoneActions.MAX), lists.sumOf { it.refused } + (all.size - PhoneActions.MAX).coerceAtLeast(0))
    }

    /**
     * Whether words may run as a phone command on this phone. Only the user's own words do: a suggestion Rem wrote, or
     * a message with files attached, always goes to Rem, so nothing Rem chose can call or text anyone.
     */
    fun mayRunOnPhone(fromRem: Boolean, attachments: Int): Boolean = !fromRem && attachments == 0

    /** What Rem said, for screen and speech. Some task notices carry their words only in the details. */
    fun replyText(message: RemMessage): String = message.body.ifBlank { message.details }.trim()

    /** Voice mode opened over Rem's chat closes once its message is queued; the chat behind shows the rest. */
    fun closeOnceQueued(overRemChat: Boolean): Boolean = overRemChat

    /** A voice message needs words or at least one attached file. */
    fun sendable(text: String, files: List<String>): Boolean = text.isNotBlank() || files.isNotEmpty()

    /**
     * Send while listening: only once words have been heard, never while a tidy round is out (it would send words
     * halfway through tidying), and not while picked files are still being read. Attachments alone don't count:
     * to send files without speaking, use the keyboard.
     */
    fun canSendSpoken(heard: String, tidyPending: Boolean, filesRead: Boolean): Boolean =
        ProgressiveTidy.hasVoice(heard) && !tidyPending && filesRead

    /** Where a voice message's attached files are. */
    enum class Files { READY, UPLOADING, STOPPED }

    /**
     * [states] are the phone's upload states for the attached files, null for one the phone no longer has. A message
     * goes once all have arrived; one that stopped must be removed or attached again first.
     */
    fun files(states: List<String?>): Files = when {
        states.any { it == null || it !in setOf("offered", "receiving", "available") } -> Files.STOPPED
        states.all { it == "available" } -> Files.READY
        else -> Files.UPLOADING
    }

    const val FILES_STOPPED = "An attached file stopped uploading. Remove it or attach it again."

    /**
     * The screen text that goes with a message: only when the user chose to share it for this message, screen
     * sharing is still allowed in settings, and the text is recent. Otherwise none, however much was read.
     */
    fun screenToSend(share: ScreenShare, chosen: Boolean, allowed: Boolean, now: Long): ScreenContext? =
        (share as? ScreenShare.Ready)?.screen?.takeIf { chosen && allowed && ScreenText.fresh(it, now) }

    /** On a locked phone, a reply stays hidden and silent unless the user chose to hear replies while locked. */
    fun showReply(locked: Boolean, whileLocked: Boolean): Boolean = !locked || whileLocked

    const val VOICE_CHECKING = "Checking for an offline voice…"
    const val VOICE_READY = "An offline voice is ready."
    const val VOICE_MISSING = "No offline voice for your language is installed, so replies are shown but not read aloud."
    const val NOT_READ = "Not read aloud: no offline voice installed."

    /**
     * What to say about reading replies aloud. With talking back off there is nothing to say, whatever voices are
     * installed. [ready] is the speaker's state: null while it starts, false with no usable offline voice.
     */
    fun voiceStatus(talkBack: Boolean, ready: Boolean?): String? = when {
        !talkBack -> null
        ready == null -> VOICE_CHECKING
        ready -> VOICE_READY
        else -> VOICE_MISSING
    }

    /** The one line under a reply that wasn't read aloud; none when talking back is off or the voice works. */
    fun replyNote(talkBack: Boolean, ready: Boolean?): String? = if (talkBack && ready == false) NOT_READ else null

    /**
     * The words to send: trimmed, or null when nothing was said. Nothing is cut at Rem's message limit: words that
     * long go as a text file ([asFile]). Only past [LongText.MAX_CHARS], far beyond any dictation, are they cut.
     */
    fun outgoing(text: String): String? = text.trim().take(LongText.MAX_CHARS).trim().takeIf { it.isNotEmpty() }

    /** Words long enough to go to Rem as a text file instead of in the message. */
    fun asFile(words: String): Boolean = LongText.asFile(words)

    /**
     * Why long words can't go now, or null when they can: as a file when there is room for one more and the desktop
     * takes files in Rem's chat, otherwise as a message when they fit in one.
     */
    fun longProblem(words: String, room: Int, filesThread: Boolean): String? = when {
        !asFile(words) || (room > 0 && filesThread) -> null
        words.length <= MAX_CHARS -> null
        !filesThread -> TOO_LONG_OLD_DESKTOP
        else -> TOO_LONG_NO_ROOM
    }

    /** What the voice screen says under long words in the box: their length, and that they go as a file. */
    fun lengthNote(length: Int): String? = when {
        length >= LongText.FILE_AT -> "${LongText.characters(length)}. Goes to Rem as an attached text file."
        length > MAX_CHARS - 200 -> "$length / $MAX_CHARS"
        else -> null
    }

    const val TOO_LONG_OLD_DESKTOP = "This is longer than the 4,000 characters Rem takes in one message. Update Raticode on your computer to " +
        "send long text as a file, or shorten it."
    const val TOO_LONG_NO_ROOM = "This is longer than the 4,000 characters Rem takes in one message, and the message already carries " +
        "8 files. Remove one so the words can go as a file, or shorten them."
}
