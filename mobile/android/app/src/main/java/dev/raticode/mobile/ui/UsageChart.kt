package dev.raticode.mobile.ui

import android.content.Context
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.clipRect
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.core.content.edit
import dev.raticode.mobile.live.UsageBucket
import dev.raticode.mobile.live.UsageHistory
import dev.raticode.mobile.live.UsageRangeChoice
import dev.raticode.mobile.live.UsageSeries
import dev.raticode.mobile.live.UsageSeriesProvider
import dev.raticode.mobile.live.UsageSplit
import java.time.Instant
import java.time.LocalDate
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

/** The Usage page's period and the chart's By provider / By account split, saved on this phone. */
internal object UsageRangePrefs {
    private const val KEY = "token_range"
    private const val SPLIT = "token_split"
    fun loadSplit(context: Context): UsageSplit = UsageSplit.parse(prefs(context).getString(SPLIT, null))
    fun saveSplit(context: Context, split: UsageSplit) = prefs(context).edit { putString(SPLIT, split.key) }
    private fun prefs(context: Context) = context.getSharedPreferences("usage_history", Context.MODE_PRIVATE)
    fun load(context: Context): UsageRangeChoice {
        val saved = prefs(context).getString(KEY, null)
        val choice = UsageHistory.parse(saved)
        // Rewrite older values, such as 90 days, in the current form.
        if (saved != null && saved != UsageHistory.encode(choice)) save(context, choice)
        return choice
    }
    fun save(context: Context, choice: UsageRangeChoice) = prefs(context).edit { putString(KEY, UsageHistory.encode(choice)) }
}

/** How one provider is drawn: its brand color for this theme and its pattern. */
internal data class SeriesLook(val fill: Color, val pattern: String)

/** Patterns, by position, for a provider the desktop sent no style for. Those are drawn gray. */
private val FALLBACK_PATTERNS = listOf("stripes", "dots", "horizontal", "vertical", "cross", "grid", "back-stripes", "checks", "rings", "solid")

/** A provider's look from the desktop's palette (gofer/core/provider_colors.py), or gray with a pattern. */
internal fun seriesLook(provider: UsageSeriesProvider, index: Int, dark: Boolean): SeriesLook =
    provider.style?.let { SeriesLook(Color(if (dark) it.dark else it.light), it.pattern) }
        ?: SeriesLook(Color(if (dark) 0xFF8C8C8C else 0xFF6E6E6E), FALLBACK_PATTERNS[index % FALLBACK_PATTERNS.size])

/** Fills a piece with its color and pattern. Light colors get dark pattern lines so they still show. */
private fun DrawScope.drawLook(look: SeriesLook, rect: Rect) {
    drawRect(look.fill, rect.topLeft, rect.size)
    val ink = if (look.fill.luminance() > 0.5f) Color.Black.copy(alpha = 0.35f) else Color.White.copy(alpha = 0.45f)
    val gap = 5.dp.toPx()
    val stroke = 1.5.dp.toPx()
    fun rising() { var x = rect.left - rect.height; while (x < rect.right) { drawLine(ink, Offset(x, rect.bottom), Offset(x + rect.height, rect.top), stroke); x += gap } }
    fun falling() { var x = rect.left - rect.height; while (x < rect.right) { drawLine(ink, Offset(x, rect.top), Offset(x + rect.height, rect.bottom), stroke); x += gap } }
    fun rows(step: Float, width: Float) { var y = rect.bottom; while (y > rect.top) { drawLine(ink, Offset(rect.left, y), Offset(rect.right, y), width); y -= step } }
    fun columns(step: Float, width: Float) { var x = rect.left + step / 2; while (x < rect.right) { drawLine(ink, Offset(x, rect.top), Offset(x, rect.bottom), width); x += step } }
    clipRect(rect.left, rect.top, rect.right, rect.bottom) {
        when (look.pattern) {
            "stripes" -> rising()
            "back-stripes" -> falling()
            "horizontal" -> rows(gap, stroke)
            "vertical" -> columns(gap, stroke)
            "grid" -> { rows(4.dp.toPx(), 1.dp.toPx()); columns(4.dp.toPx(), 1.dp.toPx()) }
            "cross" -> { rising(); falling() }
            "checks" -> {
                val step = 3.dp.toPx()
                var row = 0; var y = rect.top
                while (y < rect.bottom) { var x = rect.left + (row % 2) * step; while (x < rect.right) { drawRect(ink, Offset(x, y), Size(step, step)); x += 2 * step }; y += step; row++ }
            }
            "rings" -> {
                val step = 6.dp.toPx(); val ring = Stroke(width = 0.8.dp.toPx())
                var y = rect.top + step / 2
                while (y < rect.bottom) { var x = rect.left + step / 2; while (x < rect.right) { drawCircle(ink, 1.9.dp.toPx(), Offset(x, y), style = ring); x += step }; y += step }
            }
            "dots" -> {
                val step = 4.dp.toPx(); val radius = 1.dp.toPx()
                var y = rect.top + step / 2
                while (y < rect.bottom) { var x = rect.left + step / 2; while (x < rect.right) { drawCircle(ink, radius, Offset(x, y)); x += step }; y += step }
            }
        }
    }
}

@Composable
private fun Swatch(look: SeriesLook) {
    Canvas(Modifier.size(12.dp).clip(RoundedCornerShape(2.dp))) { drawLook(look, Rect(Offset.Zero, size)) }
}

/** A bucket's name in words, short for the scale or long for the selected bar. */
internal fun bucketLabel(bucket: UsageBucket, kind: String, long: Boolean): String {
    val start = runCatching { OffsetDateTime.parse(bucket.start) }.getOrNull() ?: return bucket.start
    val pattern = when (kind) {
        "hour" -> if (long) "MMM d, h a" else "h a"
        "week" -> if (long) "'Week of' MMM d, yyyy" else "MMM d"
        "month" -> if (long) "MMMM yyyy" else "MMM yy"
        "year" -> "yyyy"
        else -> if (long) "EEE, MMM d, yyyy" else "MMM d"
    }
    return start.format(DateTimeFormatter.ofPattern(pattern))
}

/** The period chips: 24h to All, plus Custom with a date range picker. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun UsageRangeRow(choice: UsageRangeChoice, onChoose: (UsageRangeChoice) -> Unit) {
    var picking by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        UsageHistory.RANGES.forEach { option ->
            FilterChip(selected = choice.key == option.key, onClick = { if (option.key == "custom") picking = true else onChoose(UsageRangeChoice(option.key)) },
                label = { Text(option.short) }, modifier = Modifier.semantics { contentDescription = option.label })
        }
    }
    error?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) }
    if (picking) {
        val today = LocalDate.now()
        val todayMillis = today.atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli()
        val state = rememberDateRangePickerState(
            initialSelectedStartDateMillis = choice.start.takeIf { choice.key == "custom" }?.let { LocalDate.parse(it).atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli() },
            initialSelectedEndDateMillis = choice.end.takeIf { choice.key == "custom" }?.let { LocalDate.parse(it).atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli() },
            yearRange = 2000..today.year,
            selectableDates = object : SelectableDates { override fun isSelectableDate(utcTimeMillis: Long) = utcTimeMillis <= todayMillis })
        fun day(millis: Long?) = millis?.let { Instant.ofEpochMilli(it).atZone(ZoneOffset.UTC).toLocalDate().toString() }.orEmpty()
        DatePickerDialog(onDismissRequest = { picking = false },
            confirmButton = {
                TextButton(enabled = state.selectedStartDateMillis != null && state.selectedEndDateMillis != null, onClick = {
                    val start = day(state.selectedStartDateMillis); val end = day(state.selectedEndDateMillis)
                    error = UsageHistory.customError(start, end)
                    if (error == null) { picking = false; onChoose(UsageRangeChoice("custom", start, end)) }
                }) { Text("Show range") }
            },
            dismissButton = { TextButton(onClick = { picking = false }) { Text("Cancel") } }) {
            DateRangePicker(state, Modifier.weight(1f), title = { Text("Choose a start and an end date", Modifier.padding(start = 24.dp, top = 16.dp)) })
        }
    }
}

/** Reported tokens per time bucket, one stacked piece per provider or per account. Tap a bar to read it. */
@Composable
internal fun TokenChart(all: UsageSeries) {
    val context = LocalContext.current
    var split by remember { mutableStateOf(UsageRangePrefs.loadSplit(context)) }
    val series = remember(all, split) { UsageHistory.view(all, split) }
    val buckets = series.buckets
    if (buckets.isEmpty()) return
    val byAccount = split == UsageSplit.ACCOUNT && all.accounts.isNotEmpty()
    val who = if (byAccount) "account" else "provider"
    val order = series.providers.map { it.key }
    val names = series.providers.associate { it.key to UsageHistory.pieceName(it) }
    val dark = MaterialTheme.colorScheme.surface.luminance() < 0.5f
    val looks = series.providers.withIndex().associate { (n, provider) -> provider.key to seriesLook(provider, n, dark) }
    var selected by remember(series) { mutableStateOf(buckets.indexOfLast { it.totalTokens > 0 }.takeIf { it >= 0 } ?: buckets.lastIndex) }
    val max = buckets.maxOf { it.totalTokens }.coerceAtLeast(1)
    val grand = series.providers.sumOf { it.totalTokens ?: 0 }
    val highlight = MaterialTheme.colorScheme.primary.copy(alpha = 0.12f)
    val line = MaterialTheme.colorScheme.outlineVariant
    val word = series.bucket.ifBlank { "day" }
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text("Reported tokens per $word", style = MaterialTheme.typography.labelLarge)
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            UsageSplit.entries.forEach { option ->
                FilterChip(selected = split == option, onClick = { split = option; UsageRangePrefs.saveSplit(context, option) },
                    label = { Text(option.label) })
            }
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(UsageHistory.compact(max), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Canvas(Modifier.fillMaxWidth().height(150.dp)
            .pointerInput(series) { detectTapGestures { offset -> selected = (offset.x / size.width * buckets.size).toInt().coerceIn(0, buckets.lastIndex) } }
            .semantics { contentDescription = "Chart of tokens per $word by $who, ${buckets.size} bars. Use Earlier and Later below to read each $word." }) {
            val slot = size.width / buckets.size
            val width = (slot * 0.72f).coerceAtMost(28.dp.toPx()).coerceAtLeast(1f)
            drawLine(line, Offset(0f, size.height), Offset(size.width, size.height), 1.dp.toPx())
            buckets.forEachIndexed { i, bucket ->
                val left = i * slot + (slot - width) / 2
                if (i == selected) drawRect(highlight, Offset(i * slot, 0f), Size(slot, size.height))
                var bottom = size.height
                order.forEach { provider ->
                    val tokens = bucket.providers[provider] ?: 0
                    if (tokens > 0) {
                        val height = size.height * tokens / max
                        val rect = Rect(Offset(left, bottom - height), Size(width, height))
                        drawLook(looks.getValue(provider), rect)
                        bottom -= height
                    }
                }
            }
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(bucketLabel(buckets.first(), word, false), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(bucketLabel(buckets.last(), word, false), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        val bucket = buckets[selected]
        Surface(color = MaterialTheme.colorScheme.surfaceVariant, shape = RoundedCornerShape(8.dp)) {
            Column(Modifier.fillMaxWidth().padding(10.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(bucketLabel(bucket, word, true), style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold)
                        Text(if (bucket.totalTokens > 0) "%,d tokens".format(bucket.totalTokens) else "No reported tokens", style = MaterialTheme.typography.bodySmall)
                    }
                    TextButton(enabled = selected > 0, onClick = { selected -= 1 }) { Text("Earlier") }
                    TextButton(enabled = selected < buckets.lastIndex, onClick = { selected += 1 }) { Text("Later") }
                }
                order.forEach { provider ->
                    val tokens = bucket.providers[provider] ?: 0
                    if (tokens > 0) Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Swatch(looks.getValue(provider))
                        Text(names.getValue(provider), Modifier.weight(1f), style = MaterialTheme.typography.bodySmall)
                        Text("%,d".format(tokens), style = MaterialTheme.typography.bodySmall)
                        Text(UsageHistory.share(tokens, bucket.totalTokens), Modifier.widthIn(min = 36.dp), style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
                if (bucket.unknownCalls > 0) Note("${bucket.unknownCalls} ${if (bucket.unknownCalls == 1) "call" else "calls"} without token data")
            }
        }
        series.providers.forEach { provider ->
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Swatch(looks.getValue(provider.key))
                Text(names.getValue(provider.key), Modifier.weight(1f), style = MaterialTheme.typography.bodySmall)
                Text(provider.totalTokens?.takeIf { it > 0 }?.let { "%,d · %s".format(it, UsageHistory.share(it, grand)) } ?: "Tokens not reported",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}
