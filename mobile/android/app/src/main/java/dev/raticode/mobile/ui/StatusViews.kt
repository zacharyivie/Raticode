package dev.raticode.mobile.ui

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.raticode.mobile.ui.RemTaskStatus.Tone

/** The desktop's status colors (RemHome.css --rem-success, --rem-attention, --rem-danger), for light and dark. */
@Composable
internal fun toneColor(tone: Tone): Color {
    val dark = MaterialTheme.colorScheme.surface.luminance() < 0.5f
    return when (tone) {
        Tone.ACTIVE -> MaterialTheme.colorScheme.primary
        Tone.SUCCESS -> if (dark) Color(0xFF6EE7B7) else Color(0xFF047857)
        Tone.ATTENTION -> if (dark) Color(0xFFFCD34D) else Color(0xFF92400E)
        Tone.DANGER -> if (dark) Color(0xFFFCA5A5) else Color(0xFFB91C1C)
        Tone.NEUTRAL -> MaterialTheme.colorScheme.onSurfaceVariant
    }
}

/** A task's leading glyph, as the desktop's TaskGlyph: a turning ring while work runs, else its outcome or type. */
internal fun taskGlyph(activity: RemTaskStatus.Activity, type: String): ImageVector = when {
    activity.spinning -> ThreadIcons.Running
    activity.state == RemTaskStatus.SLEEPING -> ThreadIcons.Moon
    activity.kind == RemTaskStatus.Kind.QUEUED -> ThreadIcons.Queued
    activity.kind == RemTaskStatus.Kind.PAUSED -> ThreadIcons.PauseCircle
    activity.kind == RemTaskStatus.Kind.WAITING -> ThreadIcons.Queued
    activity.kind == RemTaskStatus.Kind.ATTENTION -> if (activity.tone == Tone.DANGER) ThreadIcons.Failed else ThreadIcons.Waiting
    activity.kind == RemTaskStatus.Kind.DONE -> runGlyph(activity.state)
    else -> typeGlyph(type)
}

internal fun runGlyph(state: String): ImageVector = when (state) {
    "succeeded" -> ThreadIcons.Succeeded
    "failed" -> ThreadIcons.Failed
    "cancelled", "cancelling" -> ThreadIcons.Stopped
    "blocked", "outcome_unknown" -> ThreadIcons.Waiting
    "queued" -> ThreadIcons.Queued
    "executing", "verifying", "repairing" -> ThreadIcons.Running
    "paused" -> ThreadIcons.PauseCircle
    else -> ThreadIcons.NotStarted
}

internal fun typeGlyph(type: String): ImageVector = when (type) {
    "research" -> ThreadIcons.Research
    "develop" -> ThreadIcons.Develop
    "operations" -> ThreadIcons.Operations
    "reminder" -> ThreadIcons.Reminder
    else -> ThreadIcons.Maintain
}

/** An icon that turns while [spinning], unless the phone has animations off. */
@Composable
internal fun StatusIcon(icon: ImageVector, color: Color, modifier: Modifier = Modifier, spinning: Boolean = false, size: Dp = 18.dp) {
    val reduced = animationsOff()
    if (spinning && !reduced) {
        val angle = rememberInfiniteTransition(label = "status").animateFloat(0f, 360f,
            infiniteRepeatable(tween(1000, easing = LinearEasing), RepeatMode.Restart), label = "spin")
        // Read while drawing, so a turning icon redraws each frame without recomposing its screen.
        Icon(icon, null, modifier.size(size).graphicsLayer { rotationZ = angle.value }, tint = color)
    } else Icon(icon, null, modifier.size(size), tint = color)
}

/** A small tinted pill with a glyph and the status in words, like the desktop's status chips. */
@Composable
internal fun StatusPill(text: String, tone: Tone, modifier: Modifier = Modifier, icon: ImageVector? = null, spinning: Boolean = false) {
    val color = toneColor(tone)
    Surface(modifier, shape = RoundedCornerShape(999.dp), color = color.copy(alpha = .12f), contentColor = color) {
        Row(Modifier.padding(start = if (icon != null) 6.dp else 9.dp, end = 9.dp, top = 3.dp, bottom = 3.dp),
            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            if (icon != null) StatusIcon(icon, color, spinning = spinning, size = 13.dp)
            Text(text, style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    }
}

/** The desktop's group heading: small capitals, a count, and a chevron that folds the group. */
@Composable
internal fun StatusSectionHeader(label: String, count: Int, open: Boolean, onToggle: () -> Unit, modifier: Modifier = Modifier) {
    val muted = MaterialTheme.colorScheme.onSurfaceVariant
    Row(modifier.fillMaxWidth().heightIn(min = 40.dp).clickable(role = Role.Button, onClickLabel = if (open) "Fold $label" else "Show $label", onClick = onToggle)
        .semantics { heading(); stateDescription = if (open) "Expanded" else "Collapsed" }.padding(horizontal = 4.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        Icon(if (open) ThreadIcons.ChevronDown else ThreadIcons.ChevronRight, null, Modifier.size(14.dp), tint = muted)
        Text(label.uppercase(), Modifier.weight(1f), style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.SemiBold,
            letterSpacing = 1.2.sp, color = muted)
        Text("$count", style = MaterialTheme.typography.labelSmall, color = muted)
    }
}

@Composable
internal fun animationsOff(): Boolean {
    val resolver = LocalContext.current.contentResolver
    return runCatching { android.provider.Settings.Global.getFloat(resolver, android.provider.Settings.Global.ANIMATOR_DURATION_SCALE, 1f) == 0f }.getOrDefault(false)
}

/** The desktop's dot status (SwarmWorkspace.css .swarm-state): a small colored dot and the state in words. */
@Composable
internal fun DotStatus(text: String, tone: Tone, modifier: Modifier = Modifier) {
    val color = toneColor(tone)
    Row(modifier, verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        Box(Modifier.size(7.dp).background(color, CircleShape))
        Text(text, style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.Medium, color = color, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

/** Swarm and agent states in the desktop's swarm colors: running and done green, paused or waiting amber, failed red. */
internal fun swarmTone(state: String): Tone = when (state) {
    "running", "working", "completed" -> Tone.SUCCESS
    "paused", "blocked", "stopping", "awaiting_approval", "retry_wait" -> Tone.ATTENTION
    "failed", "error" -> Tone.DANGER
    else -> Tone.NEUTRAL
}
