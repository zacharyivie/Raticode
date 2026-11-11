package dev.raticode.mobile.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.live.RemChartSpec

private val LIGHT_SERIES = listOf(Color(0xFF4F46E5), Color(0xFF0F766E), Color(0xFFC2410C), Color(0xFFBE185D))
private val DARK_SERIES = listOf(Color(0xFFA5B4FC), Color(0xFF5EEAD4), Color(0xFFFDBA74), Color(0xFFF9A8D4))
// Series differ by dash and marker shape as well as color, as on the desktop.
private val DASHES = listOf(null, floatArrayOf(18f, 12f), floatArrayOf(6f, 9f), floatArrayOf(30f, 9f, 6f, 9f))

/** A ```rem-chart chart, drawn natively. Its data table is one tap away and TalkBack reads a summary. */
@Composable
internal fun RemChartView(chart: RemChartSpec) {
    val scale = remember(chart) { chart.scale() }
    val dark = MaterialTheme.colorScheme.surface.luminance() < .5f
    val palette = if (dark) DARK_SERIES else LIGHT_SERIES
    val grid = MaterialTheme.colorScheme.outlineVariant
    val axis = MaterialTheme.colorScheme.onSurfaceVariant
    val measurer = rememberTextMeasurer()
    val labelStyle = MaterialTheme.typography.labelSmall.copy(color = axis)
    var showData by rememberSaveable(chart) { mutableStateOf(false) }
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        if (chart.title.isNotBlank()) Text(chart.title, style = MaterialTheme.typography.titleSmall)
        Box(Modifier.horizontalScroll(rememberScrollState())) {
            Canvas(Modifier.width(maxOf(320, chart.labels.size * 40).dp).height(200.dp).semantics { contentDescription = chart.summary }) {
                val left = 44.dp.toPx(); val bottom = 26.dp.toPx(); val top = 8.dp.toPx(); val right = 8.dp.toPx()
                val plotWidth = size.width - left - right
                val plotHeight = size.height - top - bottom
                fun y(value: Double) = (top + plotHeight - ((value - scale.low) / (scale.high - scale.low)) * plotHeight).toFloat()
                val band = plotWidth / chart.labels.size
                fun x(index: Int) = left + band * index + band / 2
                for (tick in scale.ticks) {
                    drawLine(grid, Offset(left, y(tick)), Offset(size.width - right, y(tick)), strokeWidth = 1f)
                    val text = measurer.measure(RemChartSpec.format(tick), labelStyle)
                    drawText(text, topLeft = Offset(left - text.size.width - 6f, y(tick) - text.size.height / 2))
                }
                val every = (chart.labels.size + 9) / 10
                chart.labels.forEachIndexed { index, label ->
                    if (index % every != 0) return@forEachIndexed
                    val text = measurer.measure(if (label.length > 12) label.take(11) + "…" else label, labelStyle)
                    drawText(text, topLeft = Offset(x(index) - text.size.width / 2, size.height - bottom + 6f))
                }
                val barWidth = maxOf(2f, band * .72f / chart.series.size)
                chart.series.forEachIndexed { seriesIndex, series ->
                    val color = palette[seriesIndex % palette.size]
                    if (chart.type == "bar") series.values.forEachIndexed { index, value ->
                        if (value == null) return@forEachIndexed
                        val start = x(index) - barWidth * chart.series.size / 2 + barWidth * seriesIndex
                        val top0 = minOf(y(value), y(0.0)); val height = maxOf(1f, kotlin.math.abs(y(value) - y(0.0)))
                        drawRect(color, Offset(start, top0), Size(barWidth - 1f, height))
                    } else {
                        val path = Path()
                        var open = false
                        series.values.forEachIndexed { index, value ->
                            if (value == null) { open = false; return@forEachIndexed }
                            if (open) path.lineTo(x(index), y(value)) else path.moveTo(x(index), y(value))
                            open = true
                        }
                        drawPath(path, color, style = Stroke(width = 2.dp.toPx(), pathEffect = DASHES[seriesIndex % DASHES.size]?.let { PathEffect.dashPathEffect(it) }))
                        series.values.forEachIndexed { index, value -> if (value != null) marker(seriesIndex, color, Offset(x(index), y(value))) }
                    }
                }
                drawLine(axis, Offset(left, y(0.0)), Offset(size.width - right, y(0.0)), strokeWidth = 1f)
            }
        }
        if (chart.series.size > 1) Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            chart.series.forEachIndexed { index, series ->
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Box(Modifier.padding(top = 3.dp).size(12.dp).background(palette[index % palette.size], RoundedCornerShape(2.dp)))
                    Text(series.name, style = MaterialTheme.typography.labelMedium)
                }
            }
        }
        TextButton(onClick = { showData = !showData }, contentPadding = PaddingValues(horizontal = 0.dp)) {
            Text(if (showData) "Hide data table" else "Show data table", style = MaterialTheme.typography.labelLarge)
        }
        if (showData) ChartTable(chart)
    }
}

private fun DrawScope.marker(index: Int, color: Color, at: Offset) {
    val r = 4.dp.toPx()
    when (index % 4) {
        1 -> drawRect(color, Offset(at.x - r, at.y - r), Size(r * 2, r * 2))
        2 -> drawPath(Path().apply { moveTo(at.x, at.y - r); lineTo(at.x + r, at.y + r); lineTo(at.x - r, at.y + r); close() }, color)
        3 -> drawPath(Path().apply { moveTo(at.x, at.y - r); lineTo(at.x + r, at.y); lineTo(at.x, at.y + r); lineTo(at.x - r, at.y); close() }, color)
        else -> drawCircle(color, r, at)
    }
}

@Composable
private fun ChartTable(chart: RemChartSpec) {
    val line = MaterialTheme.colorScheme.outlineVariant
    Column(Modifier.horizontalScroll(rememberScrollState()).border(1.dp, line, RoundedCornerShape(6.dp))) {
        Row(Modifier.background(MaterialTheme.colorScheme.surfaceVariant)) {
            Text("Label", Modifier.width(110.dp).padding(6.dp), fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.bodySmall)
            chart.series.forEach { Text(it.name, Modifier.width(90.dp).padding(6.dp), fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.bodySmall) }
        }
        chart.labels.forEachIndexed { index, label ->
            HorizontalDivider(color = line)
            Row {
                Text(label, Modifier.width(110.dp).padding(6.dp), style = MaterialTheme.typography.bodySmall)
                chart.series.forEach { Text(RemChartSpec.format(it.values.getOrNull(index)), Modifier.width(90.dp).padding(6.dp), style = MaterialTheme.typography.bodySmall) }
            }
        }
    }
}
