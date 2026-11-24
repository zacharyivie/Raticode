package dev.raticode.mobile.live

import java.time.Duration
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.util.Locale
import org.json.JSONObject

/** One meter on a card. [window] is null when the desktop has no reading for it yet. */
data class UsageMeter(val label: String, val window: UsageWindow?) {
    /** Percent left, 0 to 100, or null when not reported. */
    val leftPercent: Double? get() = window?.usedPercent?.let { (100 - it).coerceIn(0.0, 100.0) }
}

data class UsageCard(val providerName: String, val accountName: String, val claude: Boolean, val signIn: String?,
                     val needsSignIn: Boolean, val remNext: String?, val observedAt: String, val reason: String,
                     val status: String, val meters: List<UsageMeter>, val profile: String = "",
                     val weekly: UsageBurndown? = null, val provider: String = "", val phoneSignIn: Boolean = false)

/** One saved allowance reading: the provider's percent used at [observedAt]. */
data class UsageReading(val observedAt: String, val usedPercent: Double)

/**
 * A card's weekly window as the desktop's history recorded it (gofer.core.usage_allowance_history):
 * the provider's own readings since the window started, and the pace they show.
 */
data class UsageBurndown(val label: String, val startsAt: String, val resetsAt: String, val readingCount: Int,
                         val readings: List<UsageReading>, val paceState: String, val runsOutAt: String)

/**
 * Usage › Accounts at a glance, the same cards as the desktop's Settings › Usage
 * (frontend/src/lib/usage.js usageCards). One card per provider account, each with its
 * own readings: accounts can have different limits, so nothing is added together.
 */
object UsageCards {
    private val CLAUDE_WINDOWS = listOf("five_hour" to "5-hour", "seven_day" to "Weekly")
    private val NO_CARD = setOf("not_installed", "disabled", "unsupported")
    val SIGN_IN_LABELS = mapOf("signed_in" to "Signed in", "needs_sign_in" to "Needs sign-in",
        "unavailable" to "Claude Code not found", "unknown" to "Sign-in not checked")

    /** A card's sign-in pill. "Not found" names the card's own app (Claude Code, Codex). */
    fun signInLabel(state: String, providerName: String = "Claude Code"): String =
        if (state == "unavailable") "${providerName.ifBlank { "The app" }} not found"
        else SIGN_IN_LABELS[state] ?: "Sign-in not checked"

    fun cards(accounts: List<UsageAccount>): List<UsageCard> = accounts.filter { a ->
        a.status !in NO_CARD && !(a.status == "unavailable" && a.windows.isEmpty() && a.profile.isBlank() && a.provider != "claude_code")
    }.map { a ->
        val claude = a.provider == "claude_code"
        // An older desktop sends windows without ids; show what it sent rather than empty pairs.
        val named = a.windows.isEmpty() || a.windows.any { it.id.isNotBlank() }
        val fixed = if (claude && named) CLAUDE_WINDOWS.map { (id, label) -> UsageMeter(label, a.windows.firstOrNull { it.id == id }) } else emptyList()
        val rest = a.windows.filter { w -> fixed.none { it.window === w } }
            .map { UsageMeter(it.label.ifBlank { it.id }.replace(Regex(" allowance$", RegexOption.IGNORE_CASE), ""), it) }
        val signedOut = a.status == "needs sign-in" || a.status == "auth_required"
        // Every provider account (Claude Code, Codex) reads its sign-in from its own app.
        val ownState = if (a.profile.isNotBlank() && a.signInState.isNotBlank()) a.signInState else null
        val signIn = if (claude) a.signInState.ifBlank { if (signedOut) "needs_sign_in" else "unknown" } else ownState
        // An account's own status (`claude auth status`, Codex's account/read) is newer than its last usage reading.
        val needsSignIn = if (ownState != null) signIn == "needs_sign_in"
            else signIn == "needs_sign_in" || signedOut
        UsageCard(
            providerName = if (a.provider.isBlank()) a.label.substringBefore(" · ") else UsageHistory.providerName(a.provider),
            accountName = a.profile.ifBlank { a.label.substringAfter(" · ", "").ifBlank { if (claude) "Default sign-in" else "Default account" } },
            claude = claude, signIn = signIn, needsSignIn = needsSignIn, remNext = a.remNext,
            observedAt = a.observedAt, reason = a.reason, status = a.status, meters = fixed + rest, profile = a.profile,
            weekly = a.weekly, provider = a.provider, phoneSignIn = a.phoneSignIn && a.profile.isNotBlank(),
        )
    }

    /**
     * A time from the computer. Years outside 1970 to 9999 are refused: java.time reads years up to ±999,999,999, and
     * the millisecond spans the burndown chart takes of those overflow and would end the app.
     */
    private fun instant(value: String): Instant? =
        runCatching { OffsetDateTime.parse(value).takeIf { it.year in 1970..9999 }?.toInstant() }.getOrNull()

    /** "resets in 2h 14m" for a window's reset time, counted from [now]. Blank when unknown. */
    fun countdown(resetsAt: String, now: Instant = Instant.now()): String {
        val at = instant(resetsAt) ?: return ""
        val seconds = Duration.between(now, at).seconds
        if (seconds <= 0) return "reset time passed"
        if (seconds < 60) return "resets in under a minute"
        val minutes = seconds / 60
        val days = minutes / 1440
        val hours = (minutes % 1440) / 60
        val rest = minutes % 60
        return when {
            days > 0 -> "resets in ${days}d ${hours}h"
            hours > 0 -> "resets in ${hours}h ${rest}m"
            else -> "resets in ${rest}m"
        }
    }

    /** When the desktop last checked an account, short and relative while it's recent. */
    fun checked(observedAt: String, now: Instant = Instant.now(), zone: ZoneId = ZoneId.systemDefault()): String {
        val at = instant(observedAt) ?: return "Not checked yet"
        val minutes = Duration.between(at, now).toMinutes()
        return when {
            minutes < 1 -> "Checked just now"
            minutes < 60 -> "Checked $minutes min ago"
            minutes < 24 * 60 -> "Checked ${minutes / 60}h ago"
            else -> "Checked " + at.atZone(zone).format(DateTimeFormatter.ofLocalizedDateTime(FormatStyle.MEDIUM, FormatStyle.SHORT))
        }
    }

    private val PACE_TEXT = mapOf("not_enough" to "Not enough readings yet", "on_pace" to "On pace to last until reset",
        "used_up" to "Used up until reset")
    private const val MAX_READINGS = 120

    fun parseBurndown(json: JSONObject?): UsageBurndown? {
        if (json == null) return null
        val readings = json.optJSONArray("readings")?.let { array ->
            (0 until minOf(array.length(), MAX_READINGS)).mapNotNull { index ->
                array.optJSONObject(index)?.let { r ->
                    r.optDouble("used_percent").takeIf { it.isFinite() }?.let { UsageReading(r.optString("observed_at").take(80), it) }
                }
            }
        }.orEmpty()
        val pace = json.optJSONObject("pace")
        return UsageBurndown(json.optString("label").take(160), json.optString("starts_at").take(80), json.optString("resets_at").take(80),
            json.optInt("reading_count", readings.size), readings, pace?.optString("state").orEmpty().take(20), pace?.optString("runs_out_at").orEmpty().take(80))
    }

    /** The card's one line about the weekly window's pace, with the run-out time in this phone's zone. */
    fun paceText(weekly: UsageBurndown, zone: ZoneId = ZoneId.systemDefault()): String {
        if (weekly.paceState == "runs_out") {
            val at = instant(weekly.runsOutAt) ?: return PACE_TEXT.getValue("not_enough")
            return "At this pace, runs out around " + at.atZone(zone).format(DateTimeFormatter.ofPattern("EEE HH:mm", Locale.US))
        }
        return PACE_TEXT[weekly.paceState] ?: PACE_TEXT.getValue("not_enough")
    }

    /**
     * Points for the burndown, 0..1 across the window and 0..1 down (100% left at the top). Readings are saved
     * only when they change, so each one holds until the next, and the last until [checkedAt].
     */
    fun burndownPoints(weekly: UsageBurndown, checkedAt: String): List<Pair<Float, Float>> {
        val start = instant(weekly.startsAt) ?: return emptyList()
        val reset = instant(weekly.resetsAt) ?: return emptyList()
        val span = Duration.between(start, reset).toMillis().toDouble()
        if (span <= 0) return emptyList()
        fun x(at: Instant) = (Duration.between(start, at).toMillis() / span).coerceIn(0.0, 1.0).toFloat()
        fun y(used: Double) = (used / 100).coerceIn(0.0, 1.0).toFloat()
        val readings = weekly.readings.mapNotNull { r -> instant(r.observedAt)?.let { it to r.usedPercent } }.sortedBy { it.first }
        val points = mutableListOf<Pair<Float, Float>>()
        readings.forEachIndexed { index, (at, used) ->
            if (index > 0) points += x(at) to points.last().second
            points += x(at) to y(used)
        }
        val checked = instant(checkedAt)
        readings.lastOrNull()?.let { (at, used) -> if (checked != null && checked > at) points += x(checked) to y(used) }
        return points
    }
}
