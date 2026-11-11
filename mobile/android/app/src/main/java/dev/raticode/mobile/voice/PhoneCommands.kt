package dev.raticode.mobile.voice

import dev.raticode.mobile.live.PhoneAction
import dev.raticode.mobile.live.PhoneActions
import java.time.DayOfWeek
import java.time.Duration
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZonedDateTime
import java.util.Locale

/**
 * Phone commands voice mode carries out on the phone itself, without the desktop or any model: calls, texts, timers,
 * alarms, calendar drafts, directions and maps, opening an app, the flashlight, media keys, volume, and the time,
 * date and battery. Pure, so it is unit tested.
 *
 * The patterns are anchored and narrow on purpose. A miss costs only a normal message to Rem; a wrong match could
 * call or text someone, so anything with an extra clause ("call Emily and tell her…") is not a command.
 */
sealed interface PhoneCommand {
    /** [who] is the user's words for a person, or a number they said. */
    /** [label] is a number type the user named ("call mom at work"), or empty. */
    data class Call(val who: String, val label: String = "") : PhoneCommand
    /** [body] is the message as said; it is tidied on the phone before it goes. */
    data class Text(val who: String, val body: String) : PhoneCommand
    /** Opens another app's own screen: a timer, alarm, calendar draft, map, directions or email draft. */
    data class Open(val action: PhoneAction) : PhoneCommand
    data object ShowAlarms : PhoneCommand
    /** [name] must be the label of an installed app; otherwise the words go to Rem. */
    data class OpenApp(val name: String) : PhoneCommand
    data class Flashlight(val on: Boolean) : PhoneCommand
    data class Media(val key: MediaKey) : PhoneCommand
    data class Volume(val change: VolumeChange, val percent: Int = 0) : PhoneCommand
    data class Ask(val what: Question) : PhoneCommand
}

enum class MediaKey { PLAY, PAUSE, NEXT, PREVIOUS }
enum class VolumeChange { UP, DOWN, MUTE, UNMUTE, SET }
enum class Question { TIME, DATE, BATTERY }

object PhoneCommands {
    /** Words recognizers and people add around a command, dropped before matching. */
    private val LEAD = Regex("^(?:(?:a|uh|um|uhm|er|erm|ok|okay|hey|hi|yo|so|please|rem|raticode|can you|could you|would you|will you|go ahead and|i want to|i wanna|i'd like to|i need to|let's)\\b[,\\s]*)+")
    private val TAIL = Regex("(?:[,\\s]+(?:please|thanks|thank you|for me|now|right now))+$")

    /** Spoken lightly: the comparable form of what was said, lowercase, outer punctuation and filler words gone. */
    fun clean(text: String): String {
        var s = text.trim().replace(Regex("\\s+"), " ").lowercase(Locale.ROOT)
        s = s.trimEnd('.', '!', '?', ',', ' ')
        repeat(3) { s = s.replace(LEAD, "").trim() }
        s = s.replace(TAIL, "").trim().trimEnd('.', '!', '?', ',', ' ')
        return s
    }

    /**
     * The command in [text], or null when it isn't one. [knownName] says whether some words are the name of a person
     * saved on this phone, so a text to a two-word name ("text baby bro I'm late") splits in the right place.
     */
    fun parse(text: String, knownName: (String) -> Boolean = { false }, now: ZonedDateTime = ZonedDateTime.now()): PhoneCommand? {
        if (text.length > 1200 || '\n' in text.trim()) return null
        val original = text.trim().replace(Regex("\\s+"), " ")
        val s = clean(original)
        if (s.isEmpty()) return null
        return call(s) ?: text(s, original, knownName) ?: timer(s) ?: alarm(s, now) ?: showAlarms(s) ?: place(s, original)
            ?: calendar(s, original, now) ?: email(s, original) ?: flashlight(s) ?: media(s) ?: volume(s) ?: ask(s) ?: openApp(s)
    }

    /** Whether what has been said so far starts like a command, so voice mode keeps it off the desktop's tidy model. */
    fun mightBeCommand(text: String): Boolean {
        val s = clean(text)
        return Regex("^(?:call|ring|phone|dial|give (?:a )?call to|make a call to|place a call to|text|message|send (?:a )?(?:text|message|an? sms)|(?:send|shoot) .+? (?:a )?(?:text|message)|sms|set (?:a |an )?(?:timer|alarm)|start a timer|timer|alarm|wake me|navigate|directions|get directions|take me|show (?:me )?(?:my )?alarms|add .+ to my calendar|put .+ on my calendar|add an event|create an event|email|send (?:an )?email|open|launch|turn (?:on|off)|flashlight|torch|(?:pause|stop|play|resume|unpause|continue|skip) (?:the |this |my )?(?:music|song|track|playback|audio|podcast|video)|(?:next|previous|last) (?:song|track)|volume|turn (?:the )?volume|(?:mute|unmute) (?:the )?(?:volume|sound|music|phone)|what time|what's the time|what is the time|what day|what's the date|what is the date|what's today|battery|how much battery|what's my battery)\\b")
            .containsMatchIn(s)
    }

    // ---- calls ----

    private val CALL = Regex("^(?:call|ring|phone|dial|give (?:a )?call to|make a call to|place a call to) (.+?)(?: (?:on|at) (?:(?:their|his|her) )?(mobile|cell|work|home))?$")

    private fun call(s: String): PhoneCommand? {
        val m = CALL.find(s) ?: return null
        val who = m.groupValues[1].trim()
        spokenDigits(who)?.takeIf { PhoneActions.validNumber(it) }?.let { return PhoneCommand.Call(it, m.groupValues[2].replace("cell", "mobile")) }
        // "call Emily and tell her…", "call it a day", "call me back", "call the API twice": not a plain call.
        if (who.isEmpty() || who.split(' ').size > 3 || NOT_A_PERSON.containsMatchIn(who) || Regex("\\b(?:and|then|to tell|about|later|tomorrow|back)\\b").containsMatchIn(who)) return null
        if (who in setOf("me", "it", "it a day", "it off", "this", "that", "him", "her", "them", "us", "rem", "a meeting", "an uber", "a cab", "a taxi")) return null
        return PhoneCommand.Call(numberOrName(who), m.groupValues[2].replace("cell", "mobile"))
    }

    /** Words that start something other than a person: "call the API", "call some function", "call this method". */
    private val NOT_A_PERSON = Regex("^(?:the|a|an|this|that|these|those|some|any|your|our|its|it|every|each|all)\\b")

    /** A spoken number becomes digits; anything else stays as the user's words for a person. */
    private fun numberOrName(who: String): String {
        val digits = spokenDigits(who)
        return if (digits != null && PhoneActions.validNumber(digits)) digits else who.removePrefix("my ").trim()
    }

    private val DIGIT_WORDS = mapOf("zero" to '0', "oh" to '0', "one" to '1', "two" to '2', "three" to '3', "four" to '4',
        "five" to '5', "six" to '6', "seven" to '7', "eight" to '8', "nine" to '9')

    /** "555 123 4567", "+1 555-123-4567" or "five five five one two three four" as digits, or null. */
    internal fun spokenDigits(words: String): String? {
        val parts = words.replace(Regex("[().-]"), " ").split(' ').filter { it.isNotBlank() }
        val out = StringBuilder()
        for ((i, p) in parts.withIndex()) {
            when {
                p.all { it.isDigit() } -> out.append(p)
                i == 0 && p.startsWith("+") && p.drop(1).all { it.isDigit() } -> out.append(p)
                p in DIGIT_WORDS -> out.append(DIGIT_WORDS.getValue(p))
                p == "plus" && i == 0 -> out.append('+')
                else -> return null
            }
        }
        return out.toString().takeIf { it.count(Char::isDigit) >= 3 }
    }

    // ---- texts ----

    private val TEXT_LEADS = listOf(
        Regex("^(?:send (?:a )?(?:text|text message|message|sms) to|send (?:a )?(?:text|message) to|text message|text|message|sms) (.+)$"),
        Regex("^(?:send|shoot) (.+?) (?:a )?(?:text|message|text message)(?: (?:saying|that says|that|to say))? (.+)$"),
    )
    // Not a bare "that": "text emily i think that we're late" must not make "emily i think" the person.
    private val SAYING = Regex("^(.+?)(?:,)? (?:saying|that says|and say|and tell (?:him|her|them)|to say|telling (?:him|her|them))[,:]? (.+)$")

    private fun text(s: String, original: String, knownName: (String) -> Boolean): PhoneCommand? {
        val m = TEXT_LEADS[0].find(s) ?: return TEXT_LEADS[1].find(s)?.let { send ->
            // "send mom a text that says…"
            val who = send.groupValues[1].trim()
            if (who.split(' ').size > 4 || who == "me" || NOT_A_PERSON.containsMatchIn(who)) null
            else PhoneCommand.Text(numberOrName(who), bodyFrom(original, send.groupValues[2]))
        }
        val rest = m.groupValues[1].trim()
        // "message Rem…" or "text me…" isn't a text to someone.
        if (rest.startsWith("me ") || rest == "me" || rest.startsWith("rem ")) return null
        // A known name first, longest first, so "text baby bro running late" splits after "baby bro".
        val words = rest.split(' ')
        for (n in minOf(4, words.size - 1) downTo 1) {
            val who = words.take(n).joinToString(" ")
            if (knownName(who.removePrefix("my "))) {
                val body = words.drop(n).joinToString(" ").replace(Regex("^(?:saying|that says|that|to say)[,:]? "), "")
                return PhoneCommand.Text(who.removePrefix("my "), bodyFrom(original, body))
            }
        }
        SAYING.find(rest)?.let { said ->
            val who = said.groupValues[1].trim()
            if (who.split(' ').size <= 4) return PhoneCommand.Text(numberOrName(who), bodyFrom(original, said.groupValues[2]))
        }
        // A number then the message: "text 555 123 4567 on my way".
        for (n in minOf(10, words.size - 1) downTo 3) {
            val digits = spokenDigits(words.take(n).joinToString(" ")) ?: continue
            if (PhoneActions.validNumber(digits)) return PhoneCommand.Text(digits, bodyFrom(original, words.drop(n).joinToString(" ")))
        }
        // Otherwise the first word is the person ("text emily running late"), or the whole thing with no message.
        val who = if (words.first() == "my" && words.size > 1) words.take(2).joinToString(" ") else words.first()
        if (NOT_A_PERSON.containsMatchIn(who)) return null
        return PhoneCommand.Text(who.removePrefix("my "), bodyFrom(original, words.drop(who.split(' ').size).joinToString(" ")))
    }

    /** The message in the user's own capitalization and punctuation, found in [original] by its lowercase form. */
    private fun bodyFrom(original: String, lower: String): String {
        val body = lower.trim()
        if (body.isEmpty()) return ""
        val at = original.lowercase(Locale.ROOT).lastIndexOf(body)
        if (at < 0) return body
        // The message keeps its own closing mark ("you alive?"), which matching dropped.
        var end = at + body.length
        while (end < original.length && original[end] in ".!?…") end++
        return original.substring(at, end).trim()
    }

    // ---- timers and alarms ----

    private fun timer(s: String): PhoneCommand? {
        val m = Regex("^(?:set (?:a |an |the )?timer (?:for )?|start (?:a |an )?timer (?:for )?|timer (?:for )?|(?:set |start )?(?:a |an )?)(.+?)(?: timer)?$").find(s) ?: return null
        val said = m.groupValues[1]
        if (!s.contains("timer")) return null
        val seconds = duration(said) ?: return null
        return PhoneCommand.Open(PhoneAction.Timer(seconds.coerceAtMost(PhoneActions.MAX_TIMER_SECONDS), ""))
    }

    private val NUMBER_WORDS = mapOf("a" to 1, "an" to 1, "one" to 1, "two" to 2, "three" to 3, "four" to 4, "five" to 5, "six" to 6,
        "seven" to 7, "eight" to 8, "nine" to 9, "ten" to 10, "eleven" to 11, "twelve" to 12, "thirteen" to 13, "fourteen" to 14,
        "fifteen" to 15, "sixteen" to 16, "seventeen" to 17, "eighteen" to 18, "nineteen" to 19, "twenty" to 20, "thirty" to 30,
        "forty" to 40, "forty five" to 45, "fifty" to 50, "sixty" to 60, "ninety" to 90, "twenty five" to 25)

    /** "20 minutes", "an hour and a half", "1 hour 30 minutes", "half an hour", "90 seconds" in seconds, or null. */
    internal fun duration(said: String): Int? {
        var s = said.trim().replace("-", " ")
        if (s.isEmpty()) return null
        s = s.replace("half an hour", "30 minutes").replace("a half hour", "30 minutes").replace("quarter of an hour", "15 minutes")
            .replace(Regex("\\b(\\d+|an?|one|two) hours? and a half\\b")) { "${number(it.groupValues[1])!!}.5 hours" }
            .replace(Regex("\\b(\\d+|an?|one|two|three|four|five|ten|fifteen|twenty|thirty) minutes? and a half\\b")) { "${number(it.groupValues[1])!!}.5 minutes" }
        s = s.replace(Regex("\\b(hours?|minutes?|mins?|hrs?) and\\b"), "$1")
        val unit = Regex("(\\d+(?:\\.\\d+)?|[a-z]+(?: [a-z]+)?) (hours?|hrs?|minutes?|mins?|seconds?|secs?)\\b")
        var total = 0.0
        var found = false
        var rest = s
        for (m in unit.findAll(s)) {
            val n = m.groupValues[1].toDoubleOrNull() ?: number(m.groupValues[1])?.toDouble() ?: return null
            total += n * when (m.groupValues[2].first()) { 'h' -> 3600; 'm' -> 60; else -> 1 }
            found = true
            rest = rest.replace(m.value, " ")
        }
        if (!found || rest.replace(Regex("\\b(?:and|for|a)\\b"), " ").isNotBlank()) return null
        return total.toInt().takeIf { it in 1..PhoneActions.MAX_TIMER_SECONDS }
    }

    private fun number(word: String): Int? = word.toIntOrNull() ?: NUMBER_WORDS[word.trim()]

    private fun alarm(s: String, now: ZonedDateTime): PhoneCommand? {
        val m = Regex("^(?:set (?:an |the |my )?alarm (?:for |at )?|alarm (?:for |at )?|wake me (?:up )?(?:at |by )?|set (?:a )?wake up (?:alarm )?(?:for |at )?)(.+?)(?: (?:tomorrow(?: morning)?|in the morning|tonight))?$").find(s) ?: return null
        val time = clockTime(m.groupValues[1], now.toLocalTime(), morning = s.contains("morning")) ?: return null
        return PhoneCommand.Open(PhoneAction.Alarm(time.hour, time.minute, "", emptyList()))
    }

    /**
     * "7", "7 am", "7:30 pm", "7 30", "six forty five", "noon", "midnight" as a time. Without am or pm, the next
     * time it comes round after [now] (or the morning one when [morning] is said), as phone assistants do.
     */
    internal fun clockTime(said: String, now: LocalTime, morning: Boolean = false): LocalTime? {
        var s = said.trim().replace(".", "").replace(Regex("\\bo'?clock\\b"), "").trim()
        if (s == "noon" || s == "midday") return LocalTime.NOON
        if (s == "midnight") return LocalTime.MIDNIGHT
        val half = Regex("^(am|pm|a m|p m)$")
        var meridiem: String? = null
        Regex(" ?(am|pm|a m|p m|in the morning|in the evening|at night|in the afternoon)$").find(s)?.let {
            meridiem = when (it.groupValues[1]) { "am", "a m", "in the morning" -> "am"; else -> "pm" }
            s = s.removeSuffix(it.value).trim()
        }
        if (half.matches(s)) return null
        val parts = s.split(Regex("[: ]")).filter { it.isNotBlank() }
        if (parts.isEmpty() || parts.size > 3) return null
        val hour = number(parts[0]) ?: return null
        val minute = when (parts.size) {
            1 -> 0
            2 -> number(parts[1]) ?: return null
            else -> (number(parts[1] + " " + parts[2]) ?: (number(parts[1])?.plus(number(parts[2]) ?: return null))) ?: return null
        }
        if (hour !in 0..23 || minute !in 0..59) return null
        if (hour > 12 || meridiem == null && hour == 0) return LocalTime.of(hour, minute)
        val am = LocalTime.of(hour % 12, minute)
        val pm = LocalTime.of(hour % 12 + 12, minute)
        return when (meridiem ?: if (morning) "am" else null) {
            "am" -> am
            "pm" -> pm
            else -> listOf(am, pm).minByOrNull { Duration.between(now, it).let { d -> if (d.isNegative || d.isZero) d.plusDays(1) else d } }
        }
    }

    private fun showAlarms(s: String): PhoneCommand? =
        if (Regex("^(?:show|open|see|list) (?:me )?(?:my |all )?(?:alarms|timers|alarms and timers)$").matches(s)) PhoneCommand.ShowAlarms else null

    // ---- maps ----

    private fun place(s: String, original: String): PhoneCommand? {
        Regex("^(?:get |give me )?(?:directions|driving directions|navigation) to (.+)$|^(?:navigate|take me|drive me|route me) to (.+)$|^how do i get to (.+)$").find(s)?.let { m ->
            val where = m.groupValues.drop(1).first { it.isNotEmpty() }
            return PhoneCommand.Open(PhoneAction.Place(bodyFrom(original, where).take(200), directions = true))
        }
        Regex("^(?:show (?:me )?|find |where is |where's )(.+?) on (?:the |a )?map$|^(?:map of|show me a map of) (.+)$|^find (?:a |an |the )?(.+ near(?: me|by| here))$").find(s)?.let { m ->
            val where = m.groupValues.drop(1).first { it.isNotEmpty() }
            return PhoneCommand.Open(PhoneAction.Place(bodyFrom(original, where).take(200), directions = false))
        }
        return null
    }

    // ---- calendar ----

    private fun calendar(s: String, original: String, now: ZonedDateTime): PhoneCommand? {
        val m = Regex("^(?:add|put) (.+?) (?:to|on|in) my calendar(?: (.+))?$|^(?:add an event|add event|create an event|create event|make an event)(?: called| for)? (.+?)(?: ((?:today|tomorrow|on |next |this ).+|at .+))?$").find(s) ?: return null
        val title = (m.groupValues[1].ifEmpty { m.groupValues[3] }).trim()
        val whenSaid = (m.groupValues[2].ifEmpty { m.groupValues[4] }).trim()
        if (title.isEmpty() || title.length > 200) return null
        val (day, time) = dayAndTime(whenSaid, now) ?: return null
        val zone = now.zone
        val event = if (time == null) {
            val start = day.atStartOfDay(zone).toInstant().toEpochMilli()
            PhoneAction.CalendarEvent(bodyFrom(original, title), start, day.plusDays(1).atStartOfDay(zone).toInstant().toEpochMilli(), true, "", "")
        } else {
            val start = day.atTime(time).atZone(zone).toInstant().toEpochMilli()
            PhoneAction.CalendarEvent(bodyFrom(original, title), start, start + 3_600_000, false, "", "")
        }
        return PhoneCommand.Open(event)
    }

    /** "tomorrow at 7 pm", "on friday", "at noon", "" (today, all day) as a day and optional time, or null. */
    private fun dayAndTime(said: String, now: ZonedDateTime): Pair<LocalDate, LocalTime?>? {
        var s = said.trim()
        var day = now.toLocalDate()
        var time: LocalTime? = null
        Regex("(?:^| )at (.+)$").find(s)?.let { m ->
            time = clockTime(m.groupValues[1], LocalTime.of(6, 0)) ?: return null
            s = s.removeSuffix(m.value).trim()
        }
        s = s.removePrefix("on ").removePrefix("this ").trim()
        when {
            s.isEmpty() || s == "today" -> Unit
            s == "tomorrow" -> day = day.plusDays(1)
            else -> {
                val name = s.removePrefix("next ").trim()
                val dow = DayOfWeek.entries.firstOrNull { it.name.lowercase(Locale.ROOT) == name } ?: return null
                var d = day.plusDays(1)
                while (d.dayOfWeek != dow) d = d.plusDays(1)
                day = d
            }
        }
        return day to time
    }

    // ---- email ----

    private fun email(s: String, original: String): PhoneCommand? {
        val m = Regex("^(?:send (?:an )?email to|email) (\\S+@\\S+|[a-z]+(?: [a-z]+)?)(?:,)?(?: (?:saying|that says|about|that|with) (.+)| (.+))?$").find(s) ?: return null
        val to = m.groupValues[1].takeIf { it.contains('@') }.orEmpty()
        val body = bodyFrom(original, m.groupValues[2].ifEmpty { m.groupValues[3] })
        // An email address only if the user said one; a name opens the draft for them to choose who.
        return PhoneCommand.Open(PhoneAction.EmailDraft(to.take(254), "", body.take(2000)))
    }

    // ---- device ----

    private fun flashlight(s: String): PhoneCommand? {
        Regex("^turn (on|off) (?:the |my )?(?:flashlight|flash light|torch)$|^(?:turn )?(?:the |my )?(?:flashlight|flash light|torch) (on|off)$").find(s)?.let { m ->
            return PhoneCommand.Flashlight((m.groupValues[1].ifEmpty { m.groupValues[2] }) == "on")
        }
        return null
    }

    // Media keys need something to play: "stop", "continue", "next" or "go back" on their own are things people say to
    // Rem, so they go to Rem.
    private val PLAYING = "(?:the |this |my )?(?:music|song|track|playback|audio|podcast|video)"
    private fun media(s: String): PhoneCommand? = when {
        Regex("^(?:pause|stop) $PLAYING$").matches(s) -> PhoneCommand.Media(MediaKey.PAUSE)
        Regex("^(?:play|resume|unpause|continue) $PLAYING$").matches(s) -> PhoneCommand.Media(MediaKey.PLAY)
        Regex("^(?:skip|next) (?:this |the )?(?:song|track)$|^(?:play )?the next (?:song|track)$").matches(s) -> PhoneCommand.Media(MediaKey.NEXT)
        Regex("^(?:previous|last) (?:song|track)$|^(?:play )?the (?:previous|last) (?:song|track)$|^go back (?:a|one) (?:song|track)$").matches(s) -> PhoneCommand.Media(MediaKey.PREVIOUS)
        else -> null
    }

    private fun volume(s: String): PhoneCommand? {
        Regex("^(?:set (?:the )?volume to|volume to|volume) (\\d{1,3}) ?(?:%|percent)$").find(s)?.let {
            val p = it.groupValues[1].toInt()
            return if (p in 0..100) PhoneCommand.Volume(VolumeChange.SET, p) else null
        }
        return when {
            // Always about the volume itself; a bare "mute" or "louder" goes to Rem.
            Regex("^(?:turn (?:the )?volume up|volume up|raise (?:the )?volume|increase (?:the )?volume)$").matches(s) -> PhoneCommand.Volume(VolumeChange.UP)
            Regex("^(?:turn (?:the )?volume down|volume down|lower (?:the )?volume|decrease (?:the )?volume)$").matches(s) -> PhoneCommand.Volume(VolumeChange.DOWN)
            Regex("^mute (?:the )?(?:volume|sound|music|phone)$").matches(s) -> PhoneCommand.Volume(VolumeChange.MUTE)
            Regex("^unmute (?:the )?(?:volume|sound|music|phone)$").matches(s) -> PhoneCommand.Volume(VolumeChange.UNMUTE)
            else -> null
        }
    }

    private fun ask(s: String): PhoneCommand? = when {
        Regex("^(?:what time is it|what's the time|what is the time|time)(?: now| right now)?$").matches(s) -> PhoneCommand.Ask(Question.TIME)
        Regex("^(?:what's the date|what is the date|what's today's date|what is today's date|what day is it|what's today|what is today|today's date)(?: today)?$").matches(s) -> PhoneCommand.Ask(Question.DATE)
        Regex("^(?:what's my battery|what is my battery|battery|battery level|how much battery do i have(?: left)?|what's my battery (?:level|at)|how's my battery)$").matches(s) -> PhoneCommand.Ask(Question.BATTERY)
        else -> null
    }

    private fun openApp(s: String): PhoneCommand? {
        val m = Regex("^(?:open|launch|start) (?:the |my )?(.+?)(?: app)?$").find(s) ?: return null
        val name = m.groupValues[1].trim()
        return if (name.isEmpty() || name.split(' ').size > 4) null else PhoneCommand.OpenApp(name)
    }
}
