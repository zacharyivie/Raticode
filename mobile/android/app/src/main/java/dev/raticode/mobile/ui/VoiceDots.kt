package dev.raticode.mobile.ui

import android.provider.Settings
import androidx.compose.animation.core.withInfiniteAnimationFrameMillis
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Icon
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.scale
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.ceil
import kotlin.math.exp
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.sin

/** What the dotted line is doing. */
internal enum class DotMood { LISTENING, PAUSED, THINKING, SPEAKING }

/**
 * The motion behind voice mode's dotted line, kept apart from drawing so it can be checked on its own.
 * Each column rises in dots above the line with the voice and falls back gently; its top dot hangs for a moment
 * before dropping, like the peak lights on an old hardware equalizer.
 */
internal class DotMeter(columns: Int = 0, val rows: Int = ROWS) {
    var heights = FloatArray(columns); private set
    var peaks = FloatArray(columns); private set
    private var held = FloatArray(columns)
    val columns get() = heights.size

    fun resize(columns: Int) {
        if (columns == heights.size) return
        heights = FloatArray(columns); peaks = FloatArray(columns); held = FloatArray(columns)
    }

    /** Moves every column [dtMs] forward toward [level] (0 to 1). [calm] drops the sway when animations are off. */
    fun step(dtMs: Float, level: Float, timeMs: Float, calm: Boolean = false) {
        val loud = level.coerceIn(0f, 1f)
        for (i in heights.indices) {
            val target = loud * rows * shape(i) * (if (calm) 0.8f else sway(i, timeMs))
            val h = heights[i]
            heights[i] = (h + (target - h) * min(1f, dtMs / (if (target > h) RISE_MS else FALL_MS))).coerceIn(0f, rows.toFloat())
            if (heights[i] >= peaks[i]) { peaks[i] = heights[i]; held[i] = 0f }
            else {
                held[i] += dtMs
                if (held[i] > HOLD_MS) peaks[i] = max(heights[i], peaks[i] - dtMs / PEAK_FALL_MS_PER_DOT)
            }
        }
    }

    /** Speech is fullest in the middle: a hump across the line, roughened per column so it never looks drawn. */
    internal fun shape(i: Int): Float {
        val x = if (columns <= 1) 0.5f else i / (columns - 1f)
        return (0.3f + 0.7f * sin(PI * x).toFloat().pow(1.4f)) * (0.72f + 0.28f * noise(i))
    }

    /** Each column bobs on its own beat, between about half and full height. */
    private fun sway(i: Int, timeMs: Float): Float {
        val beat = 1.1f + 1.6f * noise(i * 7 + 3)
        return 0.5f + 0.5f * (0.5f + 0.5f * sin(2 * PI.toFloat() * beat * timeMs / 1000f + 6.3f * noise(i * 13 + 5)))
    }

    companion object {
        const val ROWS = 6
        const val RISE_MS = 45f
        const val FALL_MS = 170f
        const val HOLD_MS = 420f
        const val PEAK_FALL_MS_PER_DOT = 110f

        /** A fixed scatter in 0..1 for column [n], the same on every run. */
        internal fun noise(n: Int): Float {
            val s = sin(n * 12.9898f + 78.233f) * 43758.547f
            return s - kotlin.math.floor(s)
        }
    }
}

/** Synthwave across the line: cyan at the left, Raticode's indigo-lilac in the middle, magenta at the right. */
internal object DotColors {
    val Cyan = Color(0xFF3EE0FF)
    val Lilac = Color(0xFFA5A6FF)
    val Magenta = Color(0xFFFF5AD6)
    val Resting = Color(0xFF5E5878)

    fun at(x: Float): Color = if (x < 0.5f) lerp(Cyan, Lilac, x * 2) else lerp(Lilac, Magenta, (x - 0.5f) * 2)
}

/**
 * Voice mode's dotted line, after Perplexity's assistant: a row of dots along the bottom that rises into dot bars
 * while you talk. Paused, it dims and makes room for a microphone; while Rem works, a bright dot sweeps back and forth;
 * while Rem speaks, it dances to a beat of its own. It is decoration: callers give it a role and a label.
 */
@Composable
internal fun VoiceDots(mood: DotMood, level: () -> Float, modifier: Modifier = Modifier, height: Dp = 64.dp) {
    val context = LocalContext.current
    // Android's "Remove animations" setting: keep the line meaningful but still.
    val calm = remember { runCatching { Settings.Global.getFloat(context.contentResolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) == 0f }.getOrDefault(false) }
    val meter = remember { DotMeter() }
    val clock = remember { mutableLongStateOf(0L) }
    val currentMood = rememberUpdatedState(mood)
    val currentLevel = rememberUpdatedState(level)
    // An endless animation, so tests and other idle checks don't wait on it.
    LaunchedEffect(Unit) {
        var last = -1L
        while (true) withInfiniteAnimationFrameMillis { now ->
            val dt = if (last < 0) 16f else (now - last).coerceIn(0, 100).toFloat()
            last = now
            val input = when (currentMood.value) {
                DotMood.LISTENING -> currentLevel.value()
                DotMood.SPEAKING -> if (calm) 0.5f else speakingLevel(now.toFloat())
                DotMood.PAUSED, DotMood.THINKING -> 0f
            }
            meter.step(dt, input, now.toFloat(), calm)
            clock.longValue = now
        }
    }
    Box(modifier.fillMaxWidth().height(height), contentAlignment = Alignment.Center) {
        Canvas(Modifier.matchParentSize().clearAndSetSemantics { }) {
            val gap = 7.dp.toPx(); val dot = 1.6.dp.toPx()
            meter.resize(max(1, (size.width / gap).toInt()))
            val t = clock.longValue.toFloat()
            val n = meter.columns
            val left = (size.width - (n - 1) * gap) / 2
            val base = size.height - 2.5f * gap
            val m = currentMood.value
            val micGap = if (m == DotMood.PAUSED) 22.dp.toPx() else 0f
            // Until the first frame (and in tests, which hold endless animations still) draw the voice as heard now.
            val still = clock.longValue == 0L && m == DotMood.LISTENING
            val heard = if (still) currentLevel.value().coerceIn(0f, 1f) * meter.rows * 0.8f else 0f
            // A low glow on the horizon that swells with the voice: a flat oval that fades out inside the line's box.
            val swell = if (still) heard / meter.rows else meter.heights.average().toFloat() / meter.rows
            if (m != DotMood.PAUSED) {
                val horizon = Offset(size.width / 2, base); val reach = size.width * 0.48f
                scale(1f, (size.height - base) * 0.95f / reach, horizon) {
                    drawCircle(Brush.radialGradient(listOf(DotColors.Lilac.copy(alpha = 0.12f + 0.25f * swell), Color.Transparent), horizon, reach), reach, horizon)
                }
            }
            val sweep = if (calm) 0.5f else (t / 1500f % 2f).let { if (it > 1f) 2f - it else it }
            for (i in 0 until n) {
                val x = left + i * gap
                if (micGap > 0 && abs(x - size.width / 2) < micGap) continue
                val across = if (n <= 1) 0.5f else i / (n - 1f)
                val tint = if (m == DotMood.PAUSED) DotColors.Resting else DotColors.at(across)
                // The resting line: dim, with a slow glint travelling along it so it never looks dead.
                var glow = when (m) {
                    DotMood.PAUSED -> 0.55f
                    DotMood.THINKING -> 0.3f + 0.7f * exp(-((across - sweep) * n / 3f).pow(2))
                    else -> 0.42f + if (calm) 0f else 0.4f * max(0f, sin(across * 9f - t / 260f)).pow(6)
                }
                val h = if (still) heard * meter.shape(i) else meter.heights[i]
                if (h > 0.05f) glow = 1f
                drawCircle(tint.copy(alpha = glow), dot, Offset(x, base))
                for (r in 1..ceil(h).toInt()) {
                    val a = (h - (r - 1)).coerceIn(0f, 1f) * (0.65f + 0.35f * r / meter.rows)
                    drawCircle(tint.copy(alpha = a), dot, Offset(x, base - r * gap))
                }
                // Reflection under the line, like a neon sign on a wet street.
                for (r in 1..2) if (h >= r) drawCircle(tint.copy(alpha = 0.22f / r), dot * 0.9f, Offset(x, base + r * gap))
                val peak = meter.peaks[i]
                if (peak >= 1f && peak - h > 0.6f) drawCircle(lerp(tint, Color.White, 0.55f), dot * 1.1f, Offset(x, base - ceil(peak - 0.5f) * gap))
            }
        }
        // Centred on the line, in the gap the dots leave for it.
        if (mood == DotMood.PAUSED) Icon(VoiceIcons.Mic, null, Modifier.align(Alignment.BottomCenter).padding(bottom = 6.5.dp).size(22.dp),
            tint = Color(0xFFE6E2FF))
    }
}

/** Rem's voice has no level to follow, so it gets a gentle beat with a little swing. */
private fun speakingLevel(t: Float): Float = 0.35f + 0.3f * abs(sin(t / 210f)) * (0.6f + 0.4f * sin(t / 830f))
