package dev.raticode.mobile.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedCard
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.live.UsageAccount
import dev.raticode.mobile.live.UsageBurndown
import dev.raticode.mobile.live.UsageCard
import dev.raticode.mobile.live.UsageCards
import dev.raticode.mobile.live.UsageMeter
import kotlinx.coroutines.delay
import java.time.Instant

/** Usage › Accounts at a glance: one card per provider account, never a combined figure. */
@Composable
internal fun UsageAccountCards(accounts: List<UsageAccount>, signIns: PhoneSignIn? = null) {
    val cards = UsageCards.cards(accounts)
    if (cards.isEmpty()) return
    // Countdowns move on while the page stays open.
    var now by remember { mutableStateOf(Instant.now()) }
    LaunchedEffect(Unit) { while (true) { delay(30_000); now = Instant.now() } }
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("Accounts at a glance", style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(start = 4.dp).semantics { heading() })
        Note("Each account has its own limits.", Modifier.padding(start = 4.dp))
        cards.forEach { UsageAccountCard(it, now, signIns) }
    }
}

@Composable
private fun UsageAccountCard(card: UsageCard, now: Instant, signIns: PhoneSignIn?) {
    val scheme = MaterialTheme.colorScheme
    val border = when {
        card.remNext != null -> BorderStroke(2.dp, scheme.primary)
        card.needsSignIn -> BorderStroke(1.dp, scheme.error)
        else -> CardDefaults.outlinedCardBorder()
    }
    OutlinedCard(Modifier.fillMaxWidth().testTag("usageCard"), border = border) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Column(Modifier.weight(1f)) {
                    Text(card.providerName, style = MaterialTheme.typography.labelMedium, color = scheme.onSurfaceVariant)
                    Text(card.accountName, style = MaterialTheme.typography.titleMedium)
                }
                StatePill(card)
            }
            card.remNext?.let { why ->
                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Surface(shape = RoundedCornerShape(50), color = scheme.primary, contentColor = scheme.onPrimary) {
                        Text("Rem uses next", style = MaterialTheme.typography.labelMedium, modifier = Modifier.padding(horizontal = 10.dp, vertical = 3.dp))
                    }
                    if (why.isNotBlank()) Text(why, style = MaterialTheme.typography.bodySmall, color = scheme.onSurfaceVariant)
                }
            }
            if (card.meters.isNotEmpty()) card.meters.chunked(2).forEach { pair ->
                // The 5-hour and weekly windows sit side by side, never folded into "+1 more".
                Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                    pair.forEach { CardMeter(it, now, Modifier.weight(1f)) }
                    if (pair.size == 1) Column(Modifier.weight(1f)) {}
                }
            } else if (!card.needsSignIn) Note(card.reason.ifBlank { "No allowance reading yet." })
            if (!card.needsSignIn) card.weekly?.let { WeeklyBurndown(it, card.observedAt) }
            // A Codex or Grok account signs in from here; Claude Code accounts and default sign-ins sign in on the computer.
            val actions = signIns?.takeIf { it.available && card.phoneSignIn }
            val signing = actions?.states?.get(card.profile)
            if (actions != null && signing != null) AccountSignInPanel(card.provider, card.profile, signing, actions)
            else if (card.needsSignIn && actions != null) {
                Note("Rem skips this account until it's signed in.")
                Button(onClick = { actions.start(card.provider, card.profile) }, modifier = Modifier.testTag("usageCardSignIn")) { Text("Sign in") }
            } else if (card.needsSignIn) Note(if (card.profile.isNotBlank() && card.signIn != null)
                "Sign in on your computer: Settings › Usage › Sign in on this account's card. Rem skips it until then."
                else "Sign in to ${card.providerName} on your computer, then refresh Settings › Usage there.")
            HorizontalDivider()
            Text(UsageCards.checked(card.observedAt, now), style = MaterialTheme.typography.labelSmall, color = scheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun StatePill(card: UsageCard) {
    val scheme = MaterialTheme.colorScheme
    val label = when {
        card.needsSignIn -> "Needs sign-in"
        card.signIn != null -> UsageCards.signInLabel(card.signIn, card.providerName)
        else -> card.status.replace('_', ' ').replaceFirstChar { it.uppercase() }
    }
    Surface(shape = RoundedCornerShape(50), color = if (card.needsSignIn) scheme.errorContainer else scheme.surfaceVariant,
        contentColor = if (card.needsSignIn) scheme.onErrorContainer else scheme.onSurfaceVariant) {
        Text(label, style = MaterialTheme.typography.labelSmall, modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp))
    }
}

@Composable
private fun CardMeter(meter: UsageMeter, now: Instant, modifier: Modifier) {
    val left = meter.leftPercent
    val countdown = meter.window?.let { UsageCards.countdown(it.resetsAt, now) }.orEmpty()
    Column(modifier, verticalArrangement = Arrangement.spacedBy(3.dp)) {
        Text(meter.label, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(left?.let { "${it.toInt()}% left" } ?: "Not reported", style = MaterialTheme.typography.titleSmall)
        LinearProgressIndicator({ ((left ?: 0.0) / 100).toFloat() }, Modifier.fillMaxWidth().semantics {
            contentDescription = (left?.let { "${meter.label}: ${it.toInt()} percent left" } ?: "${meter.label}: not reported") +
                countdown.takeIf { it.isNotBlank() }?.let { ", $it" }.orEmpty()
        }, color = if (left != null && left <= 10) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary)
        Text(countdown.ifBlank { if (meter.window == null) "No reading yet" else "Reset time not reported" },
            style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/**
 * The current weekly window as a small burndown of the provider's own readings (percent left), with a dashed
 * even-pace line from the window's start to its reset, and one line about the pace.
 */
@Composable
private fun WeeklyBurndown(weekly: UsageBurndown, checkedAt: String) {
    val scheme = MaterialTheme.colorScheme
    val points = UsageCards.burndownPoints(weekly, checkedAt)
    val text = UsageCards.paceText(weekly)
    val short = weekly.paceState == "runs_out" || weekly.paceState == "used_up"
    val color = if (short) scheme.error else scheme.primary
    val count = weekly.readingCount
    val readings = if (count == 1) "1 reading" else "$count readings"
    val label = weekly.label.ifBlank { "Weekly" }.replace(Regex(" allowance$", RegexOption.IGNORE_CASE), "")
    Column(Modifier.testTag("usageBurndown"), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row {
            Text("$label this window", Modifier.weight(1f), style = MaterialTheme.typography.labelSmall, color = scheme.onSurfaceVariant)
            Text(readings, style = MaterialTheme.typography.labelSmall, color = scheme.onSurfaceVariant)
        }
        Canvas(Modifier.fillMaxWidth().height(48.dp).semantics {
            contentDescription = "$label window this week, from $readings. The dashed line is an even pace to the reset."
        }) {
            val w = size.width
            val h = size.height
            drawLine(scheme.outlineVariant, Offset(0f, h), Offset(w, h), strokeWidth = 1.dp.toPx())
            drawLine(scheme.onSurfaceVariant.copy(alpha = .7f), Offset(0f, 0f), Offset(w, h), strokeWidth = 1.dp.toPx(),
                pathEffect = PathEffect.dashPathEffect(floatArrayOf(4.dp.toPx(), 4.dp.toPx())))
            if (points.isNotEmpty()) {
                val line = Path().apply { points.forEachIndexed { i, (x, y) -> if (i == 0) moveTo(x * w, y * h) else lineTo(x * w, y * h) } }
                val area = Path().apply {
                    addPath(line)
                    lineTo(points.last().first * w, h)
                    lineTo(points.first().first * w, h)
                    close()
                }
                drawPath(area, color.copy(alpha = .14f))
                drawPath(line, color, style = Stroke(width = 1.6.dp.toPx()))
            }
        }
        Text(text, style = MaterialTheme.typography.bodySmall, fontWeight = if (short) FontWeight.SemiBold else null,
            color = when (weekly.paceState) { "on_pace" -> scheme.primary; "runs_out", "used_up" -> scheme.error; else -> scheme.onSurfaceVariant })
    }
}
