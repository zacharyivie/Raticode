package dev.raticode.mobile.ui

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.live.*

/**
 * Try another model on a workflow's tests from the phone, as in the desktop's Tests panel: pick a step, a provider and a
 * model, see the expected cost of both models, run both, then compare each case side by side and use the model if it wins.
 * The desktop runs everything and never changes the workflow until Use this model.
 */

/** The candidate being chosen: a step, a provider and a model name. */
internal data class TryDraft(val node: String, val provider: String, val model: String) {
    fun models(): List<TriedModel>? = model.trim().takeIf { node.isNotBlank() && provider.isNotBlank() && Workflows.validModel(it) }
        ?.let { listOf(TriedModel(node, provider, it)) }
}

internal fun sideText(side: CaseSide): String =
    listOf(Workflows.caseLabel(side.status), side.cost, side.time).filter { it.isNotBlank() }.joinToString(" · ")

internal fun totalText(total: SideTotal): String =
    listOf("${total.passed}/${total.cases} passed", total.cost, total.time).filter { it.isNotBlank() }.joinToString(" · ")

@Composable
internal fun WorkflowTestsView(tests: WorkflowTests, state: WorkflowScreenState, estimate: TestEstimate?, checking: Boolean,
                               onRetry: () -> Unit, onEstimate: (List<TriedModel>) -> Unit, onChange: () -> Unit,
                               onTry: (List<TriedModel>) -> Unit, onUse: (TestResult) -> Unit) {
    val first = tests.modelNodes.firstOrNull()
    var draft by remember(tests.workflowId, first?.node) { mutableStateOf(TryDraft(first?.node.orEmpty(), "", "")) }
    var confirmUse by remember { mutableStateOf<TestResult?>(null) }
    val enabled = state.online && !state.busy
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(bottom = 24.dp)) {
        WorkflowBanners(state, onRetry)
        if (tests.message.isNotBlank()) Banner(tests.message, MaterialTheme.colorScheme.secondaryContainer)
        Column(Modifier.padding(horizontal = 16.dp, vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            when {
                tests.error.isNotBlank() -> Note(tests.error)
                !tests.exists || tests.cases == 0 -> Note("This workflow has no test cases yet. Add them in the Tests panel on your computer.")
                else -> Text("${tests.cases} ${if (tests.cases == 1) "case" else "cases"}", style = MaterialTheme.typography.titleSmall)
            }
            tests.result?.let { result -> ResultCard(result, enabled) { confirmUse = it } }
            val canTry = tests.exists && tests.cases > 0 && tests.modelNodes.isNotEmpty() && tests.result?.running != true && !tests.running
            if (tests.modelNodes.isEmpty() && tests.exists) Note("No step in this workflow calls a model.")
            if (canTry) {
                if (estimate != null || checking) EstimateCard(estimate, draft, enabled, onChange) { draft.models()?.let(onTry) }
                else TryForm(tests, draft, enabled, onDraft = { draft = it }) { draft.models()?.let(onEstimate) }
            }
        }
    }
    confirmUse?.let { result ->
        AlertDialog(onDismissRequest = { confirmUse = null }, title = { Text("Use this model?") },
            text = { Text("Writes ${result.tried} into the workflow on your computer, like an edit in the graph. Triggers on this workflow ask for approval again before they run it.") },
            confirmButton = { TextButton(onClick = { onUse(result); confirmUse = null }, modifier = Modifier.testTag("useModelConfirm")) { Text("Use this model") } },
            dismissButton = { TextButton(onClick = { confirmUse = null }) { Text("Cancel") } })
    }
}

@Composable
private fun TryForm(tests: WorkflowTests, draft: TryDraft, enabled: Boolean, onDraft: (TryDraft) -> Unit, onCheck: () -> Unit) {
    val now = tests.modelNodes.firstOrNull { it.node == draft.node }
    val provider = draft.provider.ifBlank { now?.provider.orEmpty() }
    val suggestions = tests.providers.firstOrNull { it.id == provider }?.models.orEmpty().filter { it != now?.model || provider != now.provider }
    val models = draft.copy(provider = provider).models()
    val unchanged = models != null && now != null && now.provider == provider && now.model == models.first().model
    Card(Modifier.fillMaxWidth().testTag("tryModelForm")) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Try another model", style = MaterialTheme.typography.titleSmall, modifier = Modifier.semantics { heading() })
            Text("Runs each case with the current model, then the candidate. The workflow, its triggers and approvals don't change; judge checks keep their own model.",
                style = MaterialTheme.typography.bodySmall)
            Text("Step", style = MaterialTheme.typography.labelMedium)
            Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                tests.modelNodes.forEach { item ->
                    FilterChip(item.node == draft.node, { onDraft(draft.copy(node = item.node)) }, label = { Text(item.node) })
                }
            }
            now?.let { Text("Now ${it.provider} ${it.model}", style = MaterialTheme.typography.bodySmall) }
            Text("Provider", style = MaterialTheme.typography.labelMedium)
            Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                val ids = (tests.providers.map { it.id } + listOfNotNull(now?.provider)).distinct()
                ids.forEach { id -> FilterChip(id == provider, { onDraft(draft.copy(provider = id, model = "")) }, label = { Text(id) }) }
            }
            OutlinedTextField(draft.model, { onDraft(draft.copy(provider = provider, model = it)) }, label = { Text("Model") }, singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Ascii), modifier = Modifier.fillMaxWidth().testTag("tryModelName"))
            if (suggestions.isNotEmpty()) Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                suggestions.take(12).forEach { model ->
                    SuggestionChip({ onDraft(draft.copy(provider = provider, model = model)) }, label = { Text(model) })
                }
            }
            if (unchanged) Text("That step already uses this model.", style = MaterialTheme.typography.bodySmall)
            Button(onClick = onCheck, enabled = enabled && models != null && !unchanged, modifier = Modifier.testTag("tryModelCheck")) {
                Text("Check cost")
            }
        }
    }
}

@Composable
private fun EstimateCard(estimate: TestEstimate?, draft: TryDraft, enabled: Boolean, onChange: () -> Unit, onRun: () -> Unit) {
    Card(Modifier.fillMaxWidth().testTag("tryModelEstimate")) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Expected cost", style = MaterialTheme.typography.titleSmall, modifier = Modifier.semantics { heading() })
            if (estimate == null) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp); Text("Checking the cost on your computer…")
                }
            } else {
                Text("Current model", style = MaterialTheme.typography.labelMedium)
                Text(estimate.currentSummary, style = MaterialTheme.typography.bodySmall)
                Text("Candidate: ${estimate.candidateLabel.ifBlank { "${draft.node}: ${draft.provider} ${draft.model}" }}", style = MaterialTheme.typography.labelMedium)
                Text(estimate.candidateSummary, style = MaterialTheme.typography.bodySmall)
                Text("Total: ${estimate.total}", style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold)
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = onRun, enabled = enabled && estimate != null, modifier = Modifier.testTag("tryModelRun")) { Text("Run both") }
                TextButton(onClick = onChange) { Text("Change") }
            }
        }
    }
}

@Composable
private fun ResultCard(result: TestResult, enabled: Boolean, onUse: (TestResult) -> Unit) {
    Card(Modifier.fillMaxWidth().testTag("testResult")) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            val title = when {
                result.tried.isNotBlank() -> "Current model against ${result.tried}"
                else -> "Newest result: ${result.passed}/${result.total} passed"
            }
            Text(title, style = MaterialTheme.typography.titleSmall, modifier = Modifier.semantics { heading() })
            if (result.running) Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp); Text("Running on your computer…", style = MaterialTheme.typography.bodySmall)
            }
            if (result.stale) Text("The workflow changed since this result.", style = MaterialTheme.typography.bodySmall)
            result.comparison?.let { ComparisonTable(it, result.running) }
            if (result.tried.isNotBlank()) {
                Button(onClick = { onUse(result) }, enabled = enabled && result.useAllowed, modifier = Modifier.testTag("useModel")) { Text("Use this model") }
                if (!result.useAllowed && result.useReason.isNotBlank()) Text(result.useReason, style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

/** Case by case: pass/fail, cost and time for the current model against the candidate, then the totals. */
@Composable
internal fun ComparisonTable(comparison: ModelComparison, running: Boolean) {
    Column(Modifier.fillMaxWidth().testTag("modelComparison"), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(Modifier.fillMaxWidth()) {
            Text("Case", Modifier.weight(1f), style = MaterialTheme.typography.labelMedium)
            Text("Current", Modifier.weight(1.3f), style = MaterialTheme.typography.labelMedium)
            Text("Candidate", Modifier.weight(1.3f), style = MaterialTheme.typography.labelMedium)
        }
        HorizontalDivider()
        comparison.rows.forEach { row ->
            val change = Workflows.changeLabel(row.change)
            Row(Modifier.fillMaxWidth().semantics(mergeDescendants = true) {
                contentDescription = "${row.name}: current ${sideText(row.current)}; candidate ${sideText(row.candidate)}" +
                    if (change.isNotBlank()) "; $change" else ""
            }, verticalAlignment = Alignment.Top) {
                Column(Modifier.weight(1f)) {
                    Text(row.name, style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    if (change.isNotBlank()) Text(change, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.primary)
                }
                SideCell(row.current, Modifier.weight(1.3f))
                SideCell(row.candidate, Modifier.weight(1.3f))
            }
        }
        if (!running) {
            HorizontalDivider()
            Row(Modifier.fillMaxWidth()) {
                Text("Total", Modifier.weight(1f), style = MaterialTheme.typography.labelMedium)
                Text(totalText(comparison.current), Modifier.weight(1.3f), style = MaterialTheme.typography.bodySmall)
                Text(totalText(comparison.candidate), Modifier.weight(1.3f), style = MaterialTheme.typography.bodySmall)
            }
            if (comparison.sentence.isNotBlank()) Text(comparison.sentence, style = MaterialTheme.typography.bodySmall)
        }
        if (comparison.baselineReused) Text("The current model's side reuses an earlier result of this version.", style = MaterialTheme.typography.bodySmall)
        Text("Cost is the workflow's own model calls; judge checks use their own model on both sides.", style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
private fun SideCell(side: CaseSide, modifier: Modifier) {
    Column(modifier) {
        Text(Workflows.caseLabel(side.status), style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.SemiBold)
        Text(listOf(side.cost, side.time).filter { it.isNotBlank() }.joinToString(" · "), style = MaterialTheme.typography.bodySmall)
    }
}
