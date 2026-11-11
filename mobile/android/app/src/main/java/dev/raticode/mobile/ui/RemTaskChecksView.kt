package dev.raticode.mobile.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.width
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.live.RemRun
import dev.raticode.mobile.live.RemTaskChecks
import dev.raticode.mobile.live.RemTaskChecks.Outcome

private val Passed = Color(0xFF15803D)
private val PassedDark = Color(0xFF6EE7B7)
private val Unresolved = Color(0xFF92400E)
private val UnresolvedDark = Color(0xFFFCD34D)

@Composable
private fun tone(outcome: Outcome): Color {
    val dark = MaterialTheme.colorScheme.surface.luminance() < .5f
    return when (outcome) {
        Outcome.FAILED -> MaterialTheme.colorScheme.error
        Outcome.UNRESOLVED -> if (dark) UnresolvedDark else Unresolved
        Outcome.PASSED -> if (dark) PassedDark else Passed
        Outcome.UNCHECKED -> MaterialTheme.colorScheme.onSurfaceVariant
    }
}

private fun symbol(outcome: Outcome) = when (outcome) {
    Outcome.FAILED -> "✕"
    Outcome.UNRESOLVED -> "?"
    Outcome.PASSED -> "✓"
    Outcome.UNCHECKED -> "○"
}

/**
 * Which checks of a task's newest run failed or stayed unresolved, each with the reviewer's
 * reason, apart from the run's summary. Passed checks fold away below. Before a review has
 * looked at any check only the count line shows, and a desktop that sends counts only gets
 * the count line it always had.
 */
@Composable
internal fun RemTaskChecksGroup(run: RemRun) {
    val title = if (run.state == "repairing") "What the review found" else "Checks"
    val details = run.details
    if (details == null) {
        if (run.checks.total > 0) SettingsGroup(title) { ChecksLine(run.checks) }
        return
    }
    val groups = RemTaskChecks.groups(details)
    val summary = RemTaskChecks.summary(groups) ?: return
    SettingsGroup(title) {
        Text(summary, Modifier.testTag("remChecksSummary"), style = MaterialTheme.typography.labelLarge,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        if (groups.reviewed) {
            listOf(Outcome.FAILED, Outcome.UNRESOLVED, Outcome.UNCHECKED).forEach { outcome ->
                val items = groups.of(outcome)
                if (items.isNotEmpty()) Column(Modifier.testTag("remChecks-${outcome.name.lowercase()}"), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("${outcome.heading} · ${items.size}", Modifier.semantics { heading() },
                        style = MaterialTheme.typography.labelLarge, color = tone(outcome))
                    items.forEach { CheckRow(it, run.id) }
                }
            }
            if (groups.passed.isNotEmpty()) PassedChecks(groups.passed, run.id)
        }
    }
}

@Composable
private fun PassedChecks(items: List<RemTaskChecks.Item>, runId: String) {
    var open by rememberSaveable(runId) { mutableStateOf(false) }
    Column(Modifier.testTag("remChecks-passed"), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        TextButton(onClick = { open = !open }, contentPadding = PaddingValues(horizontal = 0.dp),
            modifier = Modifier.semantics { stateDescription = if (open) "Expanded" else "Collapsed" }) {
            Text("${if (open) "▾" else "▸"} ${Outcome.PASSED.heading} · ${items.size}", style = MaterialTheme.typography.labelLarge,
                color = tone(Outcome.PASSED))
        }
        if (open) items.forEach { CheckRow(it, runId) }
    }
}

@Composable
private fun CheckRow(item: RemTaskChecks.Item, runId: String) {
    val reason = item.reason.ifBlank { if (item.outcome == Outcome.FAILED) "The review gave no reason." else "" }
    val long = RemTaskChecks.longReason(reason)
    var open by rememberSaveable(runId, item.key) { mutableStateOf(false) }
    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(symbol(item.outcome), Modifier.width(14.dp).clearAndSetSemantics {}, style = MaterialTheme.typography.labelLarge,
            color = tone(item.outcome))
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(item.title, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
            if (reason.isNotEmpty()) Text(reason, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = if (long && !open) 3 else Int.MAX_VALUE, overflow = TextOverflow.Ellipsis)
            if (long) TextButton(onClick = { open = !open }, contentPadding = PaddingValues(0.dp)) {
                Text(if (open) "Show less" else "Show more", style = MaterialTheme.typography.labelMedium)
            }
        }
    }
}
