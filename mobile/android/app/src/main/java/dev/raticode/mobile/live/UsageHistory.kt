package dev.raticode.mobile.live

import org.json.JSONObject
import java.time.LocalDate
import java.time.ZoneId

/** One period choice on the Usage page. Mirrors USAGE_RANGES in gofer/core/usage_history.py. */
data class UsageRangeOption(val key: String, val short: String, val label: String)

/** The period the phone asks for: a preset key, or "custom" with its own dates (YYYY-MM-DD). */
data class UsageRangeChoice(val key: String = UsageHistory.DEFAULT, val start: String = "", val end: String = "")

/** The period the desktop actually used for its answer. */
data class UsageRangeInfo(val key: String, val label: String, val startDate: String, val endDate: String)
/**
 * A provider's chart colors (#RRGGBB for dark and light themes) and pattern. The desktop sends them
 * from its one palette, gofer/core/provider_colors.py; null when it sent none or a malformed one.
 */
data class UsageSeriesStyle(val dark: Long, val light: Long, val pattern: String)
/**
 * One stacked piece of the chart: a provider, or one of its accounts. [key] names its tokens in each
 * bucket (the provider, or "provider:profile"); [name] is the account's name, blank for a provider.
 */
data class UsageSeriesProvider(val provider: String, val totalTokens: Long?, val calls: Int, val style: UsageSeriesStyle? = null,
    val key: String = provider, val name: String = "")
data class UsageBucket(val start: String, val end: String, val calls: Int, val unknownCalls: Int, val totalTokens: Long, val providers: Map<String, Long>,
    val accounts: Map<String, Long> = emptyMap())
/** [accounts] is empty from a desktop older than the By account switch. */
data class UsageSeries(val bucket: String, val providers: List<UsageSeriesProvider>, val buckets: List<UsageBucket>,
    val accounts: List<UsageSeriesProvider> = emptyList())

/** How the chart splits its bars, saved on this phone like the period. */
enum class UsageSplit(val key: String, val label: String) {
    PROVIDER("provider", "By provider"), ACCOUNT("account", "By account");
    companion object { fun parse(saved: String?): UsageSplit = entries.firstOrNull { it.key == saved } ?: PROVIDER }
}

object UsageHistory {
    const val DEFAULT = "30d"
    val RANGES = listOf(
        UsageRangeOption("24h", "24h", "Last 24 hours"),
        UsageRangeOption("7d", "7d", "Last 7 days"),
        UsageRangeOption("30d", "30d", "Last 30 days"),
        UsageRangeOption("3mo", "3mo", "Last 3 months"),
        UsageRangeOption("6mo", "6mo", "Last 6 months"),
        UsageRangeOption("1y", "1Y", "Last year"),
        UsageRangeOption("5y", "5Y", "Last 5 years"),
        UsageRangeOption("all", "All", "All time"),
        UsageRangeOption("custom", "Custom", "Custom range"),
    )
    private val KEYS = RANGES.map { it.key }.toSet()
    // Older saved values were day counts; 90 ("Last 90 days") is now 3mo.
    private val LEGACY_DAYS = mapOf("1" to "24h", "7" to "7d", "30" to "30d", "90" to "3mo", "90d" to "3mo",
        "180" to "6mo", "365" to "1y", "1825" to "5y")

    private fun date(text: String): LocalDate? = runCatching { LocalDate.parse(text) }.getOrNull()

    /** A saved choice ("3mo" or "custom:2026-10-01:2026-10-31"); anything unreadable is the default. */
    fun parse(saved: String?): UsageRangeChoice {
        val text = saved?.trim().orEmpty()
        LEGACY_DAYS[text]?.let { return UsageRangeChoice(it) }
        if (text.startsWith("custom:")) {
            val parts = text.split(":")
            val start = parts.getOrNull(1).orEmpty()
            val end = parts.getOrNull(2).orEmpty()
            return if (parts.size == 3 && customError(start, end, LocalDate.MAX) == null) UsageRangeChoice("custom", start, end) else UsageRangeChoice()
        }
        return if (text in KEYS && text != "custom") UsageRangeChoice(text) else UsageRangeChoice()
    }

    fun encode(choice: UsageRangeChoice): String =
        if (choice.key == "custom") "custom:${choice.start}:${choice.end}" else choice.key

    /** Why a custom range can't be shown, or null when it can. */
    fun customError(start: String, end: String, today: LocalDate = LocalDate.now()): String? {
        val first = date(start) ?: return "Choose a start date."
        val last = date(end) ?: return "Choose an end date."
        return when {
            first.year < 2000 -> "The start date must be in 2000 or later."
            first.isAfter(last) -> "The start date must be on or before the end date."
            first.isAfter(today) -> "The start date can't be in the future."
            else -> null
        }
    }

    fun params(choice: UsageRangeChoice, zone: String = ZoneId.systemDefault().id): JSONObject = JSONObject().apply {
        put("range", choice.key)
        if (choice.key == "custom") { put("start", choice.start); put("end", choice.end) }
        if (zone.isNotBlank()) put("tz", zone)
    }

    fun label(choice: UsageRangeChoice): String =
        if (choice.key == "custom") "${choice.start} to ${choice.end}" else RANGES.first { it.key == choice.key }.label

    fun parseRange(json: JSONObject?): UsageRangeInfo? = json?.takeIf { it.has("key") }?.let {
        UsageRangeInfo(it.optString("key"), it.optString("label"), it.optString("start_date").takeUnless { s -> s == "null" }.orEmpty(),
            it.optString("end_date").takeUnless { s -> s == "null" }.orEmpty())
    }

    fun parseSeries(json: JSONObject?): UsageSeries? = json?.takeIf { it.has("buckets") }?.let { series ->
        val providers = series.optJSONArray("providers")?.let { a -> (0 until a.length()).mapNotNull { a.optJSONObject(it) } }.orEmpty()
        val buckets = series.optJSONArray("buckets")?.let { a -> (0 until a.length()).mapNotNull { a.optJSONObject(it) } }.orEmpty()
        UsageSeries(series.optString("bucket", "day"),
            providers.map { UsageSeriesProvider(it.optString("provider"), if (it.isNull("total_tokens")) null else it.optLong("total_tokens"), it.optInt("calls"),
                parseStyle(it.optJSONObject("style"))) },
            buckets.map { b ->
                val amounts = b.optJSONObject("providers")
                val held = b.optJSONObject("accounts")
                UsageBucket(b.optString("start"), b.optString("end"), b.optInt("calls"), b.optInt("unknown_calls"), b.optLong("total_tokens"),
                    amounts?.keys()?.asSequence()?.associateWith { amounts.optLong(it) }.orEmpty(),
                    held?.keys()?.asSequence()?.associateWith { held.optLong(it) }.orEmpty())
            },
            series.optJSONArray("accounts")?.let { a -> (0 until a.length()).mapNotNull { a.optJSONObject(it) } }.orEmpty().map {
                val provider = it.optString("provider")
                val profile = it.optString("profile").takeUnless { p -> it.isNull("profile") || p.isBlank() }
                UsageSeriesProvider(provider, if (it.isNull("total_tokens")) null else it.optLong("total_tokens"), it.optInt("calls"),
                    parseStyle(it.optJSONObject("style")), it.optString("key").ifBlank { "$provider:${profile.orEmpty()}" },
                    it.optString("name").takeUnless { n -> it.isNull("name") }.orEmpty().ifBlank { profile ?: "Default account" })
            })
    }

    /**
     * The series as the chart draws it: per provider, or per account with each bucket's tokens per account.
     * A desktop that sent no accounts stays split by provider.
     */
    fun view(series: UsageSeries, split: UsageSplit): UsageSeries =
        if (split != UsageSplit.ACCOUNT || series.accounts.isEmpty()) series
        else series.copy(providers = series.accounts, buckets = series.buckets.map { it.copy(providers = it.accounts) })

    /** A piece's name in the legend, the selected bar and the read-aloud text: "Claude Code · work". */
    fun pieceName(piece: UsageSeriesProvider): String =
        if (piece.name.isBlank()) providerName(piece.provider) else "${providerName(piece.provider)} · ${piece.name}"

    /** Patterns the chart can draw, the same names the desktop uses. */
    val PATTERNS = setOf("solid", "stripes", "back-stripes", "horizontal", "vertical", "dots", "grid", "cross", "checks", "rings")
    private val HEX = Regex("#[0-9A-Fa-f]{6}")

    fun parseStyle(json: JSONObject?): UsageSeriesStyle? {
        json ?: return null
        val dark = json.optString("dark"); val light = json.optString("light"); val pattern = json.optString("pattern")
        if (!HEX.matches(dark) || !HEX.matches(light) || pattern !in PATTERNS) return null
        return UsageSeriesStyle(0xFF000000 or dark.substring(1).toLong(16), 0xFF000000 or light.substring(1).toLong(16), pattern)
    }

    /** Short token counts for the chart's scale: 950, 12K, 3.4M, 1.2B. */
    fun compact(value: Long): String {
        for ((size, suffix) in listOf(1_000_000_000L to "B", 1_000_000L to "M", 1_000L to "K")) {
            if (value >= size) {
                val scaled = value.toDouble() / size
                return if (scaled >= 100) "${scaled.toLong()}$suffix" else "${"%.1f".format(java.util.Locale.ROOT, scaled).removeSuffix(".0")}$suffix"
            }
        }
        return value.toString()
    }

    fun share(part: Long, whole: Long): String = when {
        whole <= 0 -> "0%"
        part > 0 && part * 100 < whole -> "<1%"
        else -> "${Math.round(100.0 * part / whole)}%"
    }

    private val NAMES = mapOf("codex" to "Codex", "claude_code" to "Claude Code", "claude" to "Claude Code", "cursor" to "Cursor",
        "copilot" to "GitHub Copilot", "opencode" to "OpenCode", "antigravity" to "Antigravity", "agy" to "Antigravity", "grok" to "Grok",
        "openai_api" to "OpenAI API", "anthropic_api" to "Anthropic API", "jev" to "Jev (TypeSafe)")

    fun providerName(provider: String): String = NAMES[provider] ?: provider.ifBlank { "Unknown provider" }
}
