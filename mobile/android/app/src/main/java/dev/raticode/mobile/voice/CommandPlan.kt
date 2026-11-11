package dev.raticode.mobile.voice

import dev.raticode.mobile.live.PersonMatch
import dev.raticode.mobile.live.PhoneAction
import dev.raticode.mobile.live.PhoneActions
import dev.raticode.mobile.live.PhoneIntentSpec
import dev.raticode.mobile.live.PhonePeople
import dev.raticode.mobile.live.SavedPerson
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.util.Locale

/** Where a phone command stands in voice mode. Every step is on the phone; none reaches the desktop. */
sealed interface CommandStep {
    /** Calling or texting [who] in a moment, unless the user cancels. [endsAt] is on the controller's clock. */
    data class Countdown(val call: Boolean, val who: String, val number: String, val body: String, val endsAt: Long, val total: Long) : CommandStep
    /** Several saved people answer to the words; the user taps one. */
    data class Choose(val call: Boolean, val words: String, val body: String, val people: List<SavedPerson>) : CommandStep
    /** Nobody saved answers to [words]; the user picks a contact, or sends the words to Rem instead. [card] keys the pick. */
    data class Pick(val call: Boolean, val words: String, val body: String, val card: PhoneAction) : CommandStep
    /** Raticode needs Android's calling or texting permission first. */
    data class NeedsPermission(val call: Boolean, val who: String, val number: String, val body: String) : CommandStep
    /** Another app opens to finish: the clock, calendar, maps, email, an app, or the dialer and Messages as a fallback. */
    data class Launch(val spec: PhoneIntentSpec, val doing: String) : CommandStep
    data class Working(val text: String) : CommandStep
    /**
     * Finished: what happened, said in one sentence. [speak] reads it aloud (answers to questions). [askRem] offers to
     * send the words to Rem after all, for a device command or question that wasn't meant as one.
     */
    data class Done(val text: String, val speak: Boolean = false, val askRem: Boolean = false) : CommandStep
    /** It couldn't be done; [askRem] offers to send the words to Rem instead. [fallback] opens the draft or dialer. */
    data class Problem(val text: String, val askRem: Boolean = false, val fallback: PhoneIntentSpec? = null) : CommandStep
}

/** A person a call or text goes to: who the user hears about, and the number, which stays on the phone. */
data class Recipient(val who: String, val number: String)

/** The person or people a call or text means, or none. Pure, so it is unit tested. */
sealed interface Resolved {
    data class One(val recipient: Recipient) : Resolved
    data class Several(val people: List<SavedPerson>) : Resolved
    data object None : Resolved
}

object CommandPlan {
    const val CALL_MS = 2_000L
    const val TEXT_MS = 3_000L

    /** The card a contact pick for this command is recorded against, so the same words pick once. */
    fun card(call: Boolean, words: String, body: String): PhoneAction =
        if (call) PhoneAction.Dial("", words) else PhoneAction.TextDraft("", body, words)

    /**
     * Who [words] means: a number the user said, a person the user just picked for this command ([chosen]), one saved
     * person, or several. [label] narrows several to the number type the user named ("at work").
     */
    fun resolve(words: String, people: List<SavedPerson>, chosen: SavedPerson? = null, label: String = ""): Resolved {
        if (PhoneActions.validNumber(words)) return Resolved.One(Recipient(words, words))
        return when (val match = PhonePeople.resolve(people, words, chosen)) {
            is PersonMatch.One -> Resolved.One(Recipient(PhonePeople.describe(match.person), match.person.number))
            is PersonMatch.Several -> {
                val labelled = if (label.isEmpty()) emptyList() else match.people.filter { it.label.lowercase(Locale.ROOT).contains(label) }
                if (labelled.size == 1) Resolved.One(Recipient(PhonePeople.describe(labelled.single()), labelled.single().number))
                else Resolved.Several(labelled.ifEmpty { match.people })
            }
            PersonMatch.None -> Resolved.None
        }
    }

    /** The draft or dialer to open when calling or texting directly isn't allowed. */
    fun fallback(call: Boolean, number: String, body: String): PhoneIntentSpec =
        PhoneActions.intent(if (call) PhoneAction.Dial(number) else PhoneAction.TextDraft(number, body))

    /** What the voice panel says while another app opens for [action]. */
    fun doing(action: PhoneAction, locale: Locale = Locale.getDefault()): String = when (action) {
        is PhoneAction.Timer -> "Starting a ${PhoneActions.duration(action.seconds)} timer"
        is PhoneAction.Alarm -> PhoneActions.title(action, locale).replace("Alarm · ", "Setting an alarm for ")
        is PhoneAction.CalendarEvent -> "Opening a calendar event: ${action.title}"
        is PhoneAction.Place -> if (action.directions) "Getting directions to ${action.query}" else "Finding ${action.query} on the map"
        is PhoneAction.EmailDraft -> "Opening an email draft"
        is PhoneAction.Dial -> "Opening the dialer"
        is PhoneAction.TextDraft -> "Opening a text draft"
    }

    /** The answer to a time, date or battery question. [battery] is the percentage and whether it is charging. */
    fun answer(what: Question, now: ZonedDateTime, battery: Pair<Int, Boolean>?, locale: Locale = Locale.getDefault()): String = when (what) {
        Question.TIME -> "It's " + now.format(DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).withLocale(locale)).replace(' ', ' ') + "."
        Question.DATE -> "It's " + now.format(DateTimeFormatter.ofLocalizedDate(FormatStyle.FULL).withLocale(locale)) + "."
        Question.BATTERY -> battery?.let { (level, charging) -> "Your battery is at $level%" + if (charging) " and charging." else "." }
            ?: "Couldn't read the battery level."
    }

    /** Whether an app label matches what the user called it ("open spotify" → "Spotify"). */
    fun appMatches(label: String, said: String): Boolean {
        fun key(s: String) = s.lowercase(Locale.ROOT).replace(Regex("[^\\p{L}\\p{N}]+"), " ").trim()
        return key(label).isNotEmpty() && key(label) == key(said)
    }

    const val NO_BODY = "Say what the text should say, like “text Emily I'm on my way”."
    const val EMERGENCY = "Raticode doesn't call emergency numbers itself. The dialer is open; press Call if you need to."
}
