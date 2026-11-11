package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.time.LocalDate
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

/**
 * One block of an organization's work schedule, in the desktop's phone fields. [whenText] is the desktop's own
 * description; it is blank for a block the phone made or changed, which [WorkSchedule.describe] words instead.
 */
data class OrgScheduleBlock(val id: String, val label: String, val focus: String = "", val cadence: String = "daily", val start: String = "",
                            val end: String = "", val days: List<Int> = emptyList(), val monthDay: Int? = null, val nthWeek: Int? = null,
                            val weekday: Int? = null, val quarterMonth: Int = 1, val keywords: List<String> = emptyList(), val whenText: String = "")
/** The block in effect now and the occurrence it belongs to. */
data class OrgScheduleNow(val block: OrgScheduleBlock, val startedAt: String, val endsAt: String)
data class OrgScheduleIssue(val kind: String, val text: String)
/** Work started under a block: a ticket's run, or a check-in. */
data class OrgUnderway(val employeeId: String, val name: String, val taskId: String, val title: String, val checkIn: Boolean, val blockLabel: String)
/** An organization's work schedule. [next] wins at [changesAt]; null there means no block from then. */
data class OrgSchedule(val timezone: String, val configured: Boolean, val blocks: List<OrgScheduleBlock>, val template: List<OrgScheduleBlock>,
                       val current: OrgScheduleNow?, val next: OrgScheduleBlock?, val changesAt: String, val issues: List<OrgScheduleIssue>,
                       val underway: List<OrgUnderway>)

/**
 * The phone's side of the desktop's work schedule (gofer/ui/organization_schedule.py). The desktop checks every save
 * again and its refusal is the one that counts; these rules only give quick feedback while editing.
 */
object WorkSchedule {
    const val OP = "organizations.schedule"
    const val UPDATE_DESKTOP = "Update Raticode on your computer to see and change the work schedule here."
    const val MAX_BLOCKS = 40
    const val MAX_LABEL = 80
    const val MAX_FOCUS = 600
    const val MAX_KEYWORDS = 12
    const val MAX_KEYWORD = 40
    val CADENCES = listOf("daily" to "Daily", "weekly" to "Weekly", "monthly" to "Monthly", "quarterly" to "Quarterly")
    private val RANK = mapOf("daily" to 0, "weekly" to 1, "monthly" to 2, "quarterly" to 3)
    val DAY_NAMES = listOf("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    val LONG_DAYS = listOf("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
    private val DAY_PLURALS = LONG_DAYS.map { it + "s" }
    val ORDINALS = listOf(1 to "1st", 2 to "2nd", 3 to "3rd", 4 to "4th", -1 to "last")
    private val CLOCK = Regex("^([01]\\d|2[0-3]):[0-5]\\d$")
    private val ID = Regex("^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$")
    private const val DAY_MINUTES = 24 * 60

    // Reading ---------------------------------------------------------------------------------------------------------

    fun parse(json: JSONObject?): OrgSchedule? {
        json ?: return null
        val current = json.optJSONObject("current")
        return OrgSchedule(json.optString("timezone", "UTC").ifBlank { "UTC" }, json.optBoolean("configured"), json.objects("blocks").map(::parseBlock),
            json.objects("template").map(::parseBlock),
            current?.let { OrgScheduleNow(parseBlock(it), it.optString("started_at"), it.optString("ends_at")) },
            json.optJSONObject("next")?.let(::parseBlock), json.optString("changes_at").takeUnless { json.isNull("changes_at") }.orEmpty(),
            json.objects("issues").map { OrgScheduleIssue(it.optString("kind"), it.optString("text")) }.filter { it.text.isNotBlank() },
            json.objects("underway").map { OrgUnderway(it.optString("employee_id"), it.optString("name"), it.optString("task_id"), it.optString("title"),
                it.optBoolean("check_in"), it.optString("block_label")) })
    }

    fun parseBlock(b: JSONObject) = OrgScheduleBlock(b.optString("block_id"), b.optString("label"), b.optString("focus"),
        b.optString("cadence", "daily").ifBlank { "daily" }, b.optString("start"), b.optString("end"),
        b.optJSONArray("days")?.let { a -> (0 until a.length()).mapNotNull { a.opt(it) as? Int } }.orEmpty(),
        b.int("month_day"), b.int("nth_week"), b.int("weekday"), b.int("quarter_month") ?: 1,
        b.optJSONArray("keywords")?.let { a -> (0 until a.length()).map { a.optString(it) } }.orEmpty(), b.optString("when"))

    /** A block of the saved list by ID, so the current and next blocks can show their times. */
    fun saved(schedule: OrgSchedule, block: OrgScheduleBlock) = schedule.blocks.firstOrNull { it.id == block.id } ?: block

    // Saving ----------------------------------------------------------------------------------------------------------

    /** One block as `organizations.schedule` takes it: only the fields its cadence uses, and never `when`. */
    fun blockJson(block: OrgScheduleBlock): JSONObject = JSONObject().put("block_id", block.id).put("label", block.label.trim())
        .put("focus", block.focus.trim()).put("cadence", block.cadence).put("start", block.start).put("end", block.end)
        .put("keywords", JSONArray(block.keywords)).apply {
            when (block.cadence) {
                "weekly" -> put("days", JSONArray(block.days.distinct().sorted()))
                "monthly", "quarterly" -> {
                    if (block.monthDay != null) put("month_day", block.monthDay) else { put("nth_week", block.nthWeek); put("weekday", block.weekday) }
                    if (block.cadence == "quarterly") put("quarter_month", block.quarterMonth)
                }
            }
        }

    /** The whole schedule, sent against the organization's [revision]. The time zone goes only when it changes. */
    fun params(organizationId: String, revision: Long, blocks: List<OrgScheduleBlock>, timezone: String, savedTimezone: String): JSONObject =
        JSONObject().put("organization_id", organizationId).put("expected_revision", revision)
            .put("blocks", JSONArray().apply { blocks.forEach { put(blockJson(it)) } })
            .apply { timezone.trim().takeIf { it.isNotEmpty() && it != savedTimezone }?.let { put("timezone", it) } }

    /** A new block's ID from its name: letters, digits, - and _, starting with a letter or digit, unused in [taken]. */
    fun newId(label: String, taken: Collection<String>): String {
        val base = label.lowercase(Locale.ROOT).replace(Regex("[^a-z0-9]+"), "-").trim('-').take(80).ifEmpty { "block" }
        if (base !in taken) return base
        return generateSequence(2) { it + 1 }.map { "$base-$it" }.first { it !in taken }
    }

    /** What a save would send for these blocks, to tell whether anything changed. */
    fun sendable(blocks: List<OrgScheduleBlock>): String = JSONArray().apply { blocks.forEach { put(blockJson(it)) } }.toString()

    /** Blocks exactly as they are, mid-edit too, for keeping on screen across a configuration change. */
    fun encode(blocks: List<OrgScheduleBlock>): String = JSONArray().apply {
        blocks.forEach { b ->
            put(JSONObject().put("block_id", b.id).put("label", b.label).put("focus", b.focus).put("cadence", b.cadence).put("start", b.start)
                .put("end", b.end).put("days", JSONArray(b.days)).put("month_day", b.monthDay ?: JSONObject.NULL)
                .put("nth_week", b.nthWeek ?: JSONObject.NULL).put("weekday", b.weekday ?: JSONObject.NULL).put("quarter_month", b.quarterMonth)
                .put("keywords", JSONArray(b.keywords)).put("when", b.whenText))
        }
    }.toString()

    fun decode(text: String): List<OrgScheduleBlock> = runCatching {
        val array = JSONArray(text)
        (0 until array.length()).mapNotNull { array.optJSONObject(it)?.let(::parseBlock) }
    }.getOrDefault(emptyList())

    /** A new block before it has a name: every day, nine to five. */
    fun blank() = OrgScheduleBlock("", "", start = "09:00", end = "17:00")

    /** Switching how often a block repeats, with a usable default for the new rule. */
    fun withCadence(block: OrgScheduleBlock, cadence: String): OrgScheduleBlock = when (cadence) {
        "weekly" -> block.copy(cadence = cadence, days = block.days.ifEmpty { listOf(0, 1, 2, 3, 4) })
        "monthly", "quarterly" -> block.copy(cadence = cadence,
            monthDay = if (block.monthDay == null && block.nthWeek == null) 1 else block.monthDay)
        else -> block.copy(cadence = cadence)
    }

    /**
     * The edited block as the list keeps it: trimmed, with only its cadence's fields, a new ID for a new block, and
     * the desktop's description kept only while nothing changed.
     */
    fun finish(form: OrgScheduleBlock, keywords: List<String>, original: OrgScheduleBlock, isNew: Boolean, otherIds: Collection<String>): OrgScheduleBlock {
        val monthly = form.cadence in setOf("monthly", "quarterly")
        val byDay = form.monthDay != null || form.nthWeek == null
        var block = form.copy(label = form.label.trim(), focus = form.focus.trim(), keywords = keywords,
            days = if (form.cadence == "weekly") form.days.filter { it in 0..6 }.distinct().sorted() else emptyList(),
            monthDay = if (monthly && byDay) form.monthDay ?: 1 else null,
            nthWeek = if (monthly && !byDay) form.nthWeek else null, weekday = if (monthly && !byDay) form.weekday else null,
            quarterMonth = if (form.cadence == "quarterly") form.quarterMonth else 1, whenText = "")
        if (isNew || block.id.isBlank()) block = block.copy(id = newId(block.label, otherIds))
        return if (!isNew && blockJson(block).toString() == blockJson(original).toString()) block.copy(whenText = original.whenText) else block
    }

    /** Keywords typed with commas: trimmed and without repeats in any case. [problem] says when there are too many. */
    fun keywords(text: String): List<String> {
        val words = mutableListOf<String>()
        text.split(',').map { it.trim() }.filter { it.isNotEmpty() }.forEach { word ->
            if (words.none { it.equals(word, ignoreCase = true) }) words += word
        }
        return words
    }

    // Checking --------------------------------------------------------------------------------------------------------

    fun validClock(value: String) = CLOCK.matches(value)

    fun validZone(value: String) = value.isNotBlank() && runCatching { ZoneId.of(value.trim()) }.isSuccess

    private fun minutes(clock: String) = clock.substring(0, 2).toInt() * 60 + clock.substring(3, 5).toInt()

    /** Minutes the block lasts; the same start and end is a whole day. */
    fun length(block: OrgScheduleBlock): Int {
        val span = Math.floorMod(minutes(block.end) - minutes(block.start), DAY_MINUTES)
        return if (span == 0) DAY_MINUTES else span
    }

    /** How the end relates to the start, for the editor: "All day", "Ends the next day" or nothing. */
    fun span(start: String, end: String): String = when {
        !validClock(start) || !validClock(end) -> ""
        start == end -> "All day: runs 24 hours from its start"
        minutes(end) < minutes(start) -> "Ends the next day"
        else -> ""
    }

    /** What's wrong with one block, in the desktop's words, or null. */
    fun problem(block: OrgScheduleBlock): String? {
        val name = block.label.trim()
        return when {
            name.isEmpty() -> "Give each block a name"
            name.length > MAX_LABEL -> "$name: keep the name to $MAX_LABEL characters"
            block.focus.trim().length > MAX_FOCUS -> "$name: keep the focus to $MAX_FOCUS characters"
            !ID.matches(block.id) -> "$name: this block's ID can't be saved"
            !validClock(block.start) || !validClock(block.end) -> "$name: use 24-hour times such as 08:00 and 17:30"
            block.keywords.size > MAX_KEYWORDS -> "$name: use at most $MAX_KEYWORDS keywords"
            block.keywords.any { it.trim().length !in 1..MAX_KEYWORD } -> "$name: keep each keyword between 1 and $MAX_KEYWORD characters"
            block.cadence !in RANK -> "$name: choose how often it repeats"
            block.cadence == "weekly" && block.days.isEmpty() -> "$name: choose the weekdays it runs on"
            block.days.any { it !in 0..6 } -> "Days are Monday to Sunday"
            block.cadence in setOf("monthly", "quarterly") -> monthProblem(block, name)
            else -> null
        }
    }

    private fun monthProblem(block: OrgScheduleBlock, name: String): String? {
        val byDay = block.monthDay != null
        val byWeekday = block.nthWeek != null || block.weekday != null
        return when {
            byDay == byWeekday -> "$name: choose either a day of the month or a weekday such as the first Monday"
            byDay && block.monthDay !in 1..31 -> "$name: choose a day of the month from 1 to 31"
            byWeekday && (block.nthWeek !in setOf(1, 2, 3, 4, -1) || block.weekday !in 0..6) -> "$name: choose which weekday and which week"
            block.cadence == "quarterly" && block.quarterMonth !in 1..3 -> "$name: choose the 1st, 2nd or 3rd month of the quarter"
            else -> null
        }
    }

    /** The first problem with the whole schedule that would stop it saving, or null. */
    fun problem(blocks: List<OrgScheduleBlock>): String? {
        if (blocks.size > MAX_BLOCKS) return "A work schedule has at most $MAX_BLOCKS blocks."
        blocks.firstNotNullOfOrNull(::problem)?.let { return it }
        if (blocks.map { it.id }.toSet().size != blocks.size) return "Each block needs its own ID"
        return null
    }

    /**
     * Two blocks that overlap with the same cadence and the same length, which the desktop refuses because neither
     * takes precedence. Checked on clock times, as the desktop does, ignoring daylight saving.
     */
    fun clash(blocks: List<OrgScheduleBlock>): Pair<String, String>? {
        val usable = blocks.filter { problem(it) == null }
        for ((i, first) in usable.withIndex()) for (second in usable.drop(i + 1)) {
            if (first.cadence != second.cadence || length(first) != length(second)) continue
            val (begin, end) = if (first.cadence in setOf("daily", "weekly")) LocalDate.of(2027, 1, 4) to LocalDate.of(2027, 1, 18)
                // Eight years cover every weekday a date can fall on, and two leap years.
                else LocalDate.of(2027, 1, 1) to LocalDate.of(2034, 12, 31)
            if (intersect(intervals(first, begin, end), intervals(second, begin, end))) return first.label.trim() to second.label.trim()
        }
        return null
    }

    fun clashText(clash: Pair<String, String>) = "${clash.first} and ${clash.second} overlap and neither takes precedence: they repeat on the same " +
        "cadence and last equally long. Change one's times, or its cadence."

    /** Whether one of the block's occurrences starts on this date. */
    fun occursOn(block: OrgScheduleBlock, day: LocalDate): Boolean = when (block.cadence) {
        "daily" -> true
        "weekly" -> day.dayOfWeek.value - 1 in block.days
        else -> (block.cadence != "quarterly" || (day.monthValue - 1) % 3 + 1 == block.quarterMonth) && monthRule(block, day)
    }

    private fun monthRule(block: OrgScheduleBlock, day: LocalDate): Boolean {
        val last = day.lengthOfMonth()
        block.monthDay?.let { return day.dayOfMonth == minOf(it, last) }
        if (day.dayOfWeek.value - 1 != block.weekday) return false
        return if (block.nthWeek == -1) day.dayOfMonth + 7 > last else (day.dayOfMonth - 1) / 7 + 1 == block.nthWeek
    }

    private fun intervals(block: OrgScheduleBlock, first: LocalDate, last: LocalDate): List<LongRange> {
        val found = mutableListOf<LongRange>()
        var day = first
        var offset = 0L
        while (!day.isAfter(last)) {
            if (occursOn(block, day)) { val start = offset * DAY_MINUTES + minutes(block.start); found += start until start + length(block) }
            day = day.plusDays(1); offset++
        }
        return found
    }

    private fun intersect(left: List<LongRange>, right: List<LongRange>): Boolean {
        var i = 0; var j = 0
        while (i < left.size && j < right.size) {
            if (left[i].first <= right[j].last && right[j].first <= left[i].last) return true
            if (left[i].last <= right[j].last) i++ else j++
        }
        return false
    }

    // Words -----------------------------------------------------------------------------------------------------------

    /** When a block repeats, as the desktop's describe() says it: "Every day 08:00–17:00", "Monthly, 1st Monday, 09:00–12:00". */
    fun describe(block: OrgScheduleBlock): String {
        if (block.whenText.isNotBlank()) return block.whenText
        if (!validClock(block.start) || !validClock(block.end)) return ""
        val times = if (block.start == block.end) (if (block.start == "00:00") "all day" else "${block.start} for 24 hours") else "${block.start}–${block.end}"
        when (block.cadence) {
            "daily" -> return "Every day $times"
            "weekly" -> {
                val days = block.days.filter { it in 0..6 }.distinct().sorted()
                val named = when {
                    days.isEmpty() -> "Weekly"
                    days.size == 1 -> DAY_PLURALS[days[0]]
                    days == listOf(0, 1, 2, 3, 4) -> "Weekdays"
                    days == listOf(5, 6) -> "Weekends"
                    else -> days.joinToString(", ") { DAY_NAMES[it] }
                }
                return "$named $times"
            }
        }
        val which = block.monthDay?.let { if (it == 31) "last day" else "day $it" }
            ?: "${ordinal(block.nthWeek)} ${block.weekday?.let { LONG_DAYS.getOrNull(it) }.orEmpty()}".trim()
        return if (block.cadence == "monthly") "Monthly, $which, $times"
            else "Quarterly, $which of the ${ordinal(block.quarterMonth)} month, $times"
    }

    fun ordinal(n: Int?) = ORDINALS.firstOrNull { it.first == n }?.second.orEmpty()

    /**
     * A time in the organization's [zone] as people read it next to now: "17:00", "17:00 tomorrow", "Tue 09:00" or
     * "Dec 7, 09:00". Blank when it can't be read.
     */
    fun clock(iso: String, zone: String, now: Instant = Instant.now()): String {
        val id = runCatching { ZoneId.of(zone) }.getOrDefault(ZoneId.of("UTC"))
        val at = runCatching { OffsetDateTime.parse(iso).atZoneSameInstant(id) }.getOrNull() ?: return ""
        val today = now.atZone(id).toLocalDate()
        val time = at.format(DateTimeFormatter.ofPattern("HH:mm", Locale.US))
        val days = java.time.temporal.ChronoUnit.DAYS.between(today, at.toLocalDate())
        return when {
            days == 0L -> time
            days == 1L -> "$time tomorrow"
            days in 2..6 -> at.format(DateTimeFormatter.ofPattern("EEE", Locale.US)) + " $time"
            else -> at.format(DateTimeFormatter.ofPattern("MMM d", Locale.US)) + ", $time"
        }
    }

    private fun JSONObject.objects(key: String): List<JSONObject> = optJSONArray(key)?.let { a -> (0 until a.length()).mapNotNull { a.optJSONObject(it) } }.orEmpty()
    private fun JSONObject.int(key: String): Int? = if (isNull(key)) null else opt(key) as? Int
}
