package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.abs
import kotlin.math.ceil
import kotlin.math.floor
import kotlin.math.log10
import kotlin.math.pow

/**
 * A ```rem-chart block: a small bar or line chart in one of Rem's replies. The JSON is data only;
 * anything invalid shows as the code block instead. The desktop reads the same format (remChart.js).
 */
data class RemChartSpec(val type: String, val title: String, val labels: List<String>, val series: List<Series>) {
    data class Series(val name: String, val values: List<Double?>)
    data class Scale(val low: Double, val high: Double, val ticks: List<Double>)

    /** The value axis: always includes zero, in readable steps. */
    fun scale(): Scale {
        val values = series.flatMap { it.values }.filterNotNull()
        var low = minOf(0.0, values.minOrNull() ?: 0.0)
        var high = maxOf(0.0, values.maxOrNull() ?: 0.0)
        if (low == high) high = low + 1
        val rough = (high - low) / 4
        val magnitude = 10.0.pow(floor(log10(rough)))
        val step = listOf(1.0, 2.0, 2.5, 5.0, 10.0).map { it * magnitude }.firstOrNull { it >= rough } ?: rough
        low = floor(low / step) * step
        high = ceil(high / step) * step
        val ticks = generateSequence(low) { it + step }.takeWhile { it <= high + step / 2 }.map { (it * 1e9).let(Math::round) / 1e9 }.toList()
        return Scale(low, high, ticks)
    }

    /** What TalkBack reads for the chart; the data table follows it. */
    val summary get() = "${if (type == "line") "Line" else "Bar"} chart${if (title.isNotBlank()) ": $title" else ""}. " +
        "${labels.size} points for ${series.joinToString(", ") { it.name }}. The data table follows."

    companion object {
        const val POINTS = 50
        const val SERIES = 4

        fun parse(source: String): RemChartSpec? {
            val raw = runCatching { JSONObject(source.trim()) }.getOrNull() ?: return null
            val type = raw.optString("type").takeIf { it == "bar" || it == "line" } ?: return null
            val labels = raw.optJSONArray("labels")?.let { a -> (0 until a.length()).map { a.opt(it)?.toString().orEmpty().take(40) } } ?: return null
            if (labels.isEmpty() || labels.size > POINTS) return null
            val series = (raw.optJSONArray("series") ?: JSONArray()).let { a -> (0 until minOf(a.length(), SERIES)).mapNotNull { a.optJSONObject(it) } }
                .mapIndexed { index, item ->
                    val values = item.optJSONArray("values")?.let { v -> (0 until minOf(v.length(), labels.size)).map { i ->
                        (v.opt(i) as? Number)?.toDouble()?.takeIf { it.isFinite() } } }.orEmpty()
                    Series(item.optString("name", "Series ${index + 1}").take(40), values)
                }.filter { s -> s.values.any { it != null } }
            if (series.isEmpty()) return null
            return RemChartSpec(type, raw.optString("title").take(120), labels, series)
        }

        fun format(value: Double?): String = when {
            value == null -> "—"
            abs(value) >= 1000 -> String.format(java.util.Locale.US, "%,.1f", value).removeSuffix(".0")
            else -> (Math.round(value * 100) / 100.0).let { if (it == Math.floor(it)) it.toLong().toString() else it.toString() }
        }
    }
}
