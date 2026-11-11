package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import java.time.Duration
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.util.Locale

/**
 * Phone actions Rem may suggest under a reply: a timer, an alarm, a calendar draft, a place on the map,
 * a number in the dialer, or a text or email draft. The phone accepts only these types and fields, checks
 * every value itself, and opens them only when the user taps the card on an unlocked phone. Each one opens
 * another app's own screen, where the user sets, saves, calls or sends; nothing here runs on its own.
 *
 * The desktop's src/gofer/ui/rem_phone_actions.py has the same rules, so both sides refuse the same things.
 */
sealed interface PhoneAction {
    data class Timer(val seconds: Int, val label: String) : PhoneAction
    /** [days] are `mon`..`sun`; empty means a one-time alarm. */
    data class Alarm(val hour: Int, val minute: Int, val label: String, val days: List<String>) : PhoneAction
    /** [start] and [end] are epoch milliseconds; an all-day event spans whole days in the phone's time zone. */
    data class CalendarEvent(val title: String, val start: Long, val end: Long, val allDay: Boolean, val location: String,
                             val notes: String) : PhoneAction
    data class Place(val query: String, val directions: Boolean) : PhoneAction
    /**
     * [recipient] is the user's words for a person ("my brother") instead of a [number]; the phone finds the number among
     * people the user saved here, or the user picks a contact. Exactly one of the two is set.
     */
    data class Dial(val number: String, val recipient: String = "") : PhoneAction
    /** [to] is a number the user gave, or empty; [recipient] names a saved person instead, as for [Dial]. */
    data class TextDraft(val to: String, val body: String, val recipient: String = "") : PhoneAction
    data class EmailDraft(val to: String, val subject: String, val body: String) : PhoneAction
}

/**
 * What a card opens: an implicit intent described without Android types, so it can be checked in plain unit tests.
 * [fallbackUri], when set, is tried with the same action if no app handles [uri], such as a phone without a maps app.
 * [type] is set where the receiving apps match on it, so opening doesn't depend on Android looking it up.
 */
data class PhoneIntentSpec(val action: String, val uri: String?, val extras: Map<String, Any>, val fallbackUri: String? = null,
                           val type: String? = null, val pkg: String? = null)

/** The actions on one Rem message; [refused] counts suggestions the phone wouldn't show. */
data class PhoneActionList(val actions: List<PhoneAction>, val refused: Int) {
    companion object { val EMPTY = PhoneActionList(emptyList(), 0) }
}

object PhoneActions {
    const val MAX = 3
    const val MAX_TIMER_SECONDS = 86_400
    const val MAX_RECIPIENT = 60
    /** A tapped card stays disabled this long, so a double tap can't set two timers or alarms. */
    const val REOPEN_MS = 2_000L
    private const val MAX_EVENT_DAYS = 14L

    private val FIELDS = mapOf(
        "timer" to setOf("seconds", "label"),
        "alarm" to setOf("hour", "minute", "label", "days"),
        "calendar_event" to setOf("title", "start", "end", "all_day", "location", "notes"),
        "map" to setOf("query", "directions"),
        "dial" to setOf("number", "recipient"),
        "sms_draft" to setOf("to", "body", "recipient"),
        "email_draft" to setOf("to", "subject", "body"),
    )
    val TYPES: Set<String> = FIELDS.keys
    val DAYS = listOf("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    // java.util.Calendar.MONDAY..SUNDAY, which AlarmClock.EXTRA_DAYS expects.
    private val CALENDAR_DAYS = mapOf("sun" to 1, "mon" to 2, "tue" to 3, "wed" to 4, "thu" to 5, "fri" to 6, "sat" to 7)

    // Digits with an optional leading +, and spaces, dots, dashes or brackets between them. No * or #, so a
    // suggestion can't fill the dialer with a service code, and no , ; or letters, so no pauses or extensions.
    private val NUMBER = Regex("^\\+?[0-9][0-9 ().-]{1,30}$")
    // One plain address. No commas, semicolons or a second @, so a draft never gains extra recipients.
    private val EMAIL = Regex("^[^@\\s,;:<>()\"']{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?(?:\\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?)+$")
    // Control and invisible formatting characters, which could hide or reorder what the card shows.
    private val HIDDEN = Regex("[\\p{Cc}\\p{Cf}&&[^\\n\\t]]")

    /** The valid actions in [raw], at most [MAX]. Anything malformed or unknown is counted, not shown. */
    fun parse(raw: JSONArray?, zone: ZoneId = ZoneId.systemDefault()): PhoneActionList {
        raw ?: return PhoneActionList.EMPTY
        val kept = mutableListOf<PhoneAction>()
        var refused = 0
        for (index in 0 until raw.length()) {
            val action = (raw.opt(index) as? JSONObject)?.let { parse(it, zone) }
            if (action != null && kept.size < MAX) kept += action else refused++
        }
        return PhoneActionList(kept, refused)
    }

    /** One action, or null when its type, fields or values aren't allowed. */
    fun parse(json: JSONObject, zone: ZoneId = ZoneId.systemDefault()): PhoneAction? = runCatching {
        val type = json.opt("type") as? String ?: return null
        val allowed = FIELDS[type] ?: return null
        if (!json.keys().asSequence().all { it == "type" || it in allowed }) return null
        when (type) {
            "timer" -> PhoneAction.Timer(json.int("seconds", 1..MAX_TIMER_SECONDS), json.text("label", 60))
            "alarm" -> {
                val days = json.optJSONArray("days")?.let { a -> (0 until a.length()).map { a.get(it) as String } }.orEmpty()
                require(json.isNull("days") || json.opt("days") is JSONArray || !json.has("days"))
                require(days.all { it in DAYS } && days.toSet().size == days.size)
                PhoneAction.Alarm(json.int("hour", 0..23), json.int("minute", 0..59), json.text("label", 60), DAYS.filter { it in days })
            }
            "calendar_event" -> calendar(json, zone)
            "map" -> PhoneAction.Place(json.text("query", 200, required = true), json.flag("directions"))
            "dial" -> {
                val recipient = recipient(json)
                val number = json.text("number", 32)
                // Exactly one: a number the user gave, or their words for a saved person.
                require(recipient.isEmpty() != number.isEmpty())
                PhoneAction.Dial(if (number.isEmpty()) "" else number(number), recipient)
            }
            "sms_draft" -> {
                val to = json.text("to", 32).let { if (it.isEmpty()) it else number(it) }
                val body = json.text("body", 1000, lines = true)
                val recipient = recipient(json)
                require((to.isNotEmpty() || body.isNotEmpty() || recipient.isNotEmpty()) && (to.isEmpty() || recipient.isEmpty()))
                PhoneAction.TextDraft(to, body, recipient)
            }
            "email_draft" -> {
                val to = json.text("to", 254).also { require(it.isEmpty() || EMAIL.matches(it)) }
                val subject = json.text("subject", 200)
                val body = json.text("body", 2000, lines = true)
                require(to.isNotEmpty() || subject.isNotEmpty() || body.isNotEmpty())
                PhoneAction.EmailDraft(to, subject, body)
            }
            else -> null
        }
    }.getOrNull()

    private fun calendar(json: JSONObject, zone: ZoneId): PhoneAction.CalendarEvent {
        val title = json.text("title", 200, required = true)
        val startText = json.text("start", 40, required = true)
        val endText = json.text("end", 40)
        val dateOnly = startText.length == 10
        val allDay = json.flag("all_day") || dateOnly
        val (start, end) = if (allDay) {
            val first = LocalDate.parse(startText.take(10))
            val last = if (endText.isEmpty()) first else LocalDate.parse(endText.take(10))
            require(!last.isBefore(first))
            first.atStartOfDay(zone).toInstant() to last.plusDays(1).atStartOfDay(zone).toInstant()
        } else {
            val begin = instant(startText, zone)
            begin to (if (endText.isEmpty()) begin.plus(Duration.ofHours(1)) else instant(endText, zone))
        }
        require(start.isAfter(Instant.parse("2000-01-01T00:00:00Z")) && start.isBefore(Instant.parse("2100-01-01T00:00:00Z")))
        require(end.isAfter(start) && Duration.between(start, end) <= Duration.ofDays(MAX_EVENT_DAYS))
        return PhoneAction.CalendarEvent(title, start.toEpochMilli(), end.toEpochMilli(), allDay,
            json.text("location", 200), json.text("notes", 1000, lines = true))
    }

    /** A time with its offset, or without one in the phone's time zone; the card shows the result either way. */
    private fun instant(text: String, zone: ZoneId): Instant =
        runCatching { OffsetDateTime.parse(text).toInstant() }.getOrElse { LocalDateTime.parse(text).atZone(zone).toInstant() }

    private fun number(value: String): String {
        require(validNumber(value))
        return value
    }

    /** A number a card may open: digits and plain separators only, 3 to 15 digits. */
    fun validNumber(value: String): Boolean = NUMBER.matches(value) && value.count { it.isDigit() } in 3..15

    /** A person as the user named them. Three or more digits look like a number, which never travels as a name. */
    private fun recipient(json: JSONObject): String = json.text("recipient", MAX_RECIPIENT).also { require(it.count(Char::isDigit) < 3) }

    /** The card with the number of the person the user saved or picked; it then opens like a card with a number. */
    fun withNumber(action: PhoneAction, number: String): PhoneAction = when (action) {
        is PhoneAction.Dial -> PhoneAction.Dial(number)
        is PhoneAction.TextDraft -> PhoneAction.TextDraft(number, action.body)
        else -> action
    }

    /** The user's words for a person on a call or text card, or "" when the card has a number or none. */
    fun recipientOf(action: PhoneAction): String = when (action) {
        is PhoneAction.Dial -> action.recipient
        is PhoneAction.TextDraft -> action.recipient
        else -> ""
    }

    private fun JSONObject.int(key: String, range: IntRange): Int {
        val value = opt(key)
        require(value is Int || value is Long)
        val number = (value as Number).toLong()
        require(number in range.first..range.last)
        return number.toInt()
    }

    private fun JSONObject.flag(key: String): Boolean = if (isNull(key)) false else opt(key) as? Boolean ?: throw IllegalArgumentException(key)

    private fun JSONObject.text(key: String, limit: Int, required: Boolean = false, lines: Boolean = false): String {
        val value = if (isNull(key)) "" else (opt(key) as? String)?.trim() ?: throw IllegalArgumentException(key)
        require(value.length <= limit && !HIDDEN.containsMatchIn(value))
        require(lines || '\n' !in value)
        require(!required || value.isNotEmpty())
        return value
    }

    /**
     * The intent a tapped card opens. None of these place a call, send anything or skip the other app's screen. Clock apps
     * act on the tap itself: Google Clock starts the timer and saves the alarm as it opens, so their cards say so.
     */
    fun intent(action: PhoneAction): PhoneIntentSpec = when (action) {
        is PhoneAction.Timer -> PhoneIntentSpec("android.intent.action.SET_TIMER", null, buildMap {
            put("android.intent.extra.alarm.LENGTH", action.seconds)
            put("android.intent.extra.alarm.SKIP_UI", false)
            if (action.label.isNotEmpty()) put("android.intent.extra.alarm.MESSAGE", action.label)
        })
        is PhoneAction.Alarm -> PhoneIntentSpec("android.intent.action.SET_ALARM", null, buildMap {
            put("android.intent.extra.alarm.HOUR", action.hour)
            put("android.intent.extra.alarm.MINUTES", action.minute)
            put("android.intent.extra.alarm.SKIP_UI", false)
            if (action.label.isNotEmpty()) put("android.intent.extra.alarm.MESSAGE", action.label)
            if (action.days.isNotEmpty()) put("android.intent.extra.alarm.DAYS", ArrayList(action.days.map { CALENDAR_DAYS.getValue(it) }))
        })
        // Google Calendar's insert screen matches the event type only, as Android's common intents list it.
        is PhoneAction.CalendarEvent -> PhoneIntentSpec("android.intent.action.INSERT", "content://com.android.calendar/events", type = "vnd.android.cursor.dir/event", extras = buildMap {
            put("title", action.title)
            put("beginTime", action.start)
            put("endTime", action.end)
            put("allDay", action.allDay)
            if (action.location.isNotEmpty()) put("eventLocation", action.location)
            if (action.notes.isNotEmpty()) put("description", action.notes)
        })
        // Any maps app handles geo:; without one, the web map opens in the browser instead.
        is PhoneAction.Place -> if (action.directions)
            PhoneIntentSpec("android.intent.action.VIEW", "https://www.google.com/maps/dir/?api=1&destination=" + encode(action.query), emptyMap())
            else PhoneIntentSpec("android.intent.action.VIEW", "geo:0,0?q=" + encode(action.query), emptyMap(),
                fallbackUri = "https://www.google.com/maps/search/?api=1&query=" + encode(action.query))
        is PhoneAction.Dial -> PhoneIntentSpec("android.intent.action.DIAL", "tel:" + encode(action.number), emptyMap())
        is PhoneAction.TextDraft -> PhoneIntentSpec("android.intent.action.SENDTO", "smsto:" + encode(action.to),
            if (action.body.isEmpty()) emptyMap() else mapOf("sms_body" to action.body))
        is PhoneAction.EmailDraft -> PhoneIntentSpec("android.intent.action.SENDTO", "mailto:", buildMap {
            if (action.to.isNotEmpty()) put("android.intent.extra.EMAIL", arrayOf(action.to))
            if (action.subject.isNotEmpty()) put("android.intent.extra.SUBJECT", action.subject)
            if (action.body.isNotEmpty()) put("android.intent.extra.TEXT", action.body)
        })
    }

    /** Percent-encodes everything except unreserved characters, so text can't add URI parts or parameters. */
    fun encode(text: String): String = buildString {
        for (byte in text.toByteArray(Charsets.UTF_8)) {
            val c = (byte.toInt() and 0xff).toChar()
            if (c in 'A'..'Z' || c in 'a'..'z' || c in '0'..'9' || c in "-._~") append(c) else append("%%%02X".format(c.code))
        }
    }

    /** A card's heading. An alarm's time is written the way the phone writes times, so 7:00 and 19:00 can't be mixed up. */
    fun title(action: PhoneAction, locale: Locale = Locale.getDefault()): String = when (action) {
        is PhoneAction.Timer -> "Timer · " + duration(action.seconds)
        is PhoneAction.Alarm -> "Alarm · " + LocalTime.of(action.hour, action.minute).format(DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).withLocale(locale))
        is PhoneAction.CalendarEvent -> "Calendar event · " + action.title
        is PhoneAction.Place -> (if (action.directions) "Directions to " else "Map · ") + action.query
        is PhoneAction.Dial -> "Call " + action.number.ifEmpty { action.recipient }
        is PhoneAction.TextDraft -> "Text " + action.to.ifEmpty { action.recipient.ifEmpty { "someone" } }
        is PhoneAction.EmailDraft -> "Email " + action.to.ifEmpty { "someone" }
    }

    /** Everything the other app receives, so the user sees exactly what they're opening. An event already over says so. */
    fun details(action: PhoneAction, zone: ZoneId = ZoneId.systemDefault(), locale: Locale = Locale.getDefault(),
                now: Instant = Instant.now()): List<String> = when (action) {
        is PhoneAction.Timer -> listOfNotNull(action.label.ifEmpty { null }, "Your clock app starts it when you tap.")
        is PhoneAction.Alarm -> listOfNotNull(action.label.ifEmpty { null },
            if (action.days.isEmpty()) "Once" else "Repeats " + action.days.joinToString(", ") { it.replaceFirstChar(Char::titlecase) },
            "Your clock app saves it when you tap. Change or delete it there.")
        is PhoneAction.CalendarEvent -> listOfNotNull(when {
            action.allDay -> {
                val format = DateTimeFormatter.ofLocalizedDate(FormatStyle.MEDIUM).withLocale(locale)
                val first = Instant.ofEpochMilli(action.start).atZone(zone).toLocalDate()
                val last = Instant.ofEpochMilli(action.end).atZone(zone).toLocalDate().minusDays(1)
                if (last.isAfter(first)) "All day, ${first.format(format)} – ${last.format(format)}" else "All day, ${first.format(format)}"
            }
            else -> {
                val format = DateTimeFormatter.ofLocalizedDateTime(FormatStyle.MEDIUM, FormatStyle.SHORT).withLocale(locale)
                val begin = Instant.ofEpochMilli(action.start).atZone(zone)
                val end = Instant.ofEpochMilli(action.end).atZone(zone)
                val endText = if (end.toLocalDate() == begin.toLocalDate()) end.format(DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).withLocale(locale))
                    else end.format(format)
                begin.format(format) + " – " + endText
            }
        }, if (action.end <= now.toEpochMilli()) PAST else null, action.location.ifEmpty { null }, action.notes.ifEmpty { null })
        is PhoneAction.Place -> emptyList()
        is PhoneAction.Dial -> listOf("Opens the dialer. You place the call.")
        is PhoneAction.TextDraft -> listOfNotNull(action.body.ifEmpty { null }, "Opens a draft. You choose to send it.")
        is PhoneAction.EmailDraft -> listOfNotNull(action.subject.ifEmpty { null }, action.body.ifEmpty { null }, "Opens a draft. You choose to send it.")
    }

    /** The card's button, naming where the action continues. */
    fun button(action: PhoneAction): String = when (action) {
        is PhoneAction.Timer -> "Start timer"
        is PhoneAction.Alarm -> "Set alarm"
        is PhoneAction.CalendarEvent -> "Open in Calendar"
        is PhoneAction.Place -> "Open in Maps"
        is PhoneAction.Dial -> "Open dialer"
        is PhoneAction.TextDraft -> "Open in Messages"
        is PhoneAction.EmailDraft -> "Open in Email"
    }

    /** What TalkBack hears for the card's button: where it goes and what it opens, since several cards can share a label. */
    fun spoken(action: PhoneAction, locale: Locale = Locale.getDefault()): String = button(action) + ": " + title(action, locale)

    const val PAST = "This time has already passed."

    fun duration(seconds: Int): String {
        val h = seconds / 3600; val m = seconds % 3600 / 60; val s = seconds % 60
        return listOfNotNull(if (h > 0) "$h h" else null, if (m > 0) "$m min" else null, if (s > 0) "$s s" else null).joinToString(" ")
    }
}
