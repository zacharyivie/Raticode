package dev.raticode.mobile.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.live.WorkflowList
import dev.raticode.mobile.live.WorkflowTextInput
import dev.raticode.mobile.share.WorkflowShare

/**
 * Run a workflow, the share target: pick a workflow that has a text input, then the input the shared text goes in, or,
 * for one shared file, a workflow with a file input the file fits. The workflow's own Run sheet opens with the text or the
 * file filled in, and nothing starts until Run is tapped there.
 */

/** Shared text waiting for the open workflow, the text inputs it can go in, and the one chosen. */
internal class SharedRun(val text: String, val notices: List<String>, val choices: List<WorkflowTextInput>, val input: String?,
                         val onChoose: (String) -> Unit, val onDiscard: () -> Unit, val file: WorkflowShare.SharedFile? = null,
                         val status: String? = null) {
    /** What the Run sheet shows in the chosen input: the text, or the shared file's name. */
    val prefill get() = file?.name ?: text
}

internal const val SHARE_UPDATE_DESKTOP = "Update Raticode on your computer to run workflows with text you share."
internal const val SHARE_NO_TEXT_INPUTS = "None of the workflows in your open projects has a text input for shared text. " +
    "Add one to a workflow on your computer, then share again."
internal const val SHARE_UPDATE_DESKTOP_FILES = "Update Raticode on your computer to run workflows with a file you share."
internal const val SHARE_NO_FILE_INPUTS = "None of the workflows in your open projects has a file input this file fits. " +
    "Add one to a workflow on your computer, then share again."

/** The shared text, what was left out of it, and a reminder that it may hold instructions the user didn't write. */
@Composable
private fun SharedTextCard(text: String, notices: List<String>, modifier: Modifier = Modifier, file: WorkflowShare.SharedFile? = null,
                           actions: @Composable RowScope.() -> Unit) {
    Surface(color = MaterialTheme.colorScheme.surface, shape = MaterialTheme.shapes.medium, modifier = modifier.fillMaxWidth()) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("Shared from another app", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary)
            if (text.isNotEmpty()) Text(text, style = MaterialTheme.typography.bodyMedium, maxLines = 4, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.testTag("workflowShareText"))
            if (file != null) Text(file.name + (file.size?.let { " · " + WorkflowShare.size(it) } ?: ""), style = MaterialTheme.typography.bodyMedium,
                maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.testTag("workflowShareFile"))
            notices.forEach { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
            if (text.isNotEmpty()) Text(WorkflowShare.CAUTION, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.tertiary)
            if (file != null) Text(WorkflowShare.FILE_CAUTION, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.tertiary)
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.End), verticalAlignment = Alignment.CenterVertically, content = actions)
        }
    }
}

/** The workflows shared text can go in: those whose list entry names a text input. */
@Composable
internal fun ShareWorkflowPicker(share: WorkflowShare.Pending, list: WorkflowList, state: WorkflowScreenState, onRetry: () -> Unit,
                                 onCancel: () -> Unit, onPick: (String) -> Unit) {
    val file = share.file
    val eligible = if (file != null) list.items.filter { WorkflowShare.fileInputs(it.fileInputs, file).isNotEmpty() }
        else list.items.filter { !it.textInputs.isNullOrEmpty() }
    Column(Modifier.fillMaxSize()) {
        WorkflowBanners(state, onRetry)
        LazyColumn(Modifier.fillMaxSize().testTag("workflowSharePicker"), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            item { SharedTextCard(share.text, share.notices, file = file) { TextButton(onClick = onCancel, modifier = Modifier.testTag("workflowShareCancel")) { Text("Cancel") } } }
            if (share.text.isEmpty() && file == null) return@LazyColumn
            when {
                file != null && list.items.isNotEmpty() && list.items.all { it.fileInputs == null } -> item { Note(SHARE_UPDATE_DESKTOP_FILES) }
                file != null && eligible.isEmpty() -> item { Note(SHARE_NO_FILE_INPUTS) }
                file == null && list.items.isNotEmpty() && list.items.all { it.textInputs == null } -> item { Note(SHARE_UPDATE_DESKTOP) }
                eligible.isEmpty() -> item { Note(SHARE_NO_TEXT_INPUTS) }
                else -> item { Text("Choose a workflow", style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = 8.dp)) }
            }
            items(eligible, key = { it.id }) { workflow ->
                Surface(onClick = { onPick(workflow.id) }, shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.surface,
                    modifier = Modifier.fillMaxWidth().testTag("shareWorkflow:${workflow.id}")) {
                    Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        if (workflow.project.isNotBlank()) Text(workflow.project, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.primary,
                            maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Text(workflow.name.ifBlank { "Untitled workflow" }, style = MaterialTheme.typography.titleMedium, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        Text(if (file != null) fileInputsLine(WorkflowShare.fileInputs(workflow.fileInputs, file)) else textInputsLine(workflow.textInputs.orEmpty()),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    }
                }
            }
            if (list.earlier > 0 && eligible.isNotEmpty()) item {
                Note("The phone lists your 100 most recent workflows. Older ones are on your computer.")
            }
        }
    }
}

/** "File input: Document" or "File inputs: Scan, Photo". */
internal fun fileInputsLine(inputs: List<WorkflowTextInput>): String =
    (if (inputs.size == 1) "File input: " else "File inputs: ") + inputs.joinToString(", ") { it.label.ifBlank { it.name } }

/** "Text input: Target" or "Text inputs: Topic, Notes". */
internal fun textInputsLine(inputs: List<WorkflowTextInput>): String =
    (if (inputs.size == 1) "Text input: " else "Text inputs: ") + inputs.joinToString(", ") { it.label.ifBlank { it.name } }

/** Above the run card of the chosen workflow: where the text goes, and buttons to run, change the input or discard it. */
@Composable
internal fun SharedRunCard(shared: SharedRun, enabled: Boolean, onRun: () -> Unit, onChooseInput: () -> Unit) {
    val label = shared.choices.firstOrNull { it.name == shared.input }?.label
    SharedTextCard(shared.text, buildList {
        addAll(shared.notices)
        add(when {
            shared.choices.isEmpty() && shared.file != null -> "This workflow has no file input this file fits. Go back and choose another."
            shared.choices.isEmpty() -> "This workflow has no text input for shared text. Go back and choose another."
            label == null -> "Choose the input the shared ${if (shared.file != null) "file" else "text"} goes in."
            else -> "It goes in $label."
        })
        shared.status?.let(::add)
    }, Modifier.padding(horizontal = 16.dp, vertical = 4.dp).testTag("workflowShareCard"), file = shared.file) {
        TextButton(onClick = shared.onDiscard, modifier = Modifier.testTag("workflowShareDiscard")) { Text("Discard") }
        if (shared.choices.size > 1) TextButton(onClick = onChooseInput, modifier = Modifier.testTag("workflowShareInput")) {
            Text(if (shared.input == null) "Choose input" else "Change input")
        }
        if (shared.choices.isNotEmpty()) Button(onClick = onRun, enabled = enabled, modifier = Modifier.testTag("workflowShareRun")) { Text("Fill in and run") }
    }
}

/** Which text input gets the shared text. The phone remembers the answer for this workflow. */
@Composable
internal fun SharedInputChooser(shared: SharedRun, onDismiss: () -> Unit, onChoose: (String) -> Unit) {
    var chosen by remember(shared.input) { mutableStateOf(shared.input ?: shared.choices.firstOrNull()?.name) }
    AlertDialog(onDismissRequest = onDismiss, title = { Text("Which input gets the shared ${if (shared.file != null) "file" else "text"}?") },
        text = {
            Column(Modifier.selectableGroup().verticalScroll(rememberScrollState()).testTag("workflowShareInputs")) {
                Text("The phone remembers your choice for this workflow.", style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(bottom = 8.dp))
                shared.choices.forEach { input ->
                    Row(Modifier.fillMaxWidth().selectable(chosen == input.name, role = Role.RadioButton) { chosen = input.name }
                        .padding(vertical = 8.dp).testTag("shareInput:${input.name}"), verticalAlignment = Alignment.CenterVertically) {
                        RadioButton(selected = chosen == input.name, onClick = null)
                        Spacer(Modifier.width(12.dp))
                        Text(input.label.ifBlank { input.name }, style = MaterialTheme.typography.bodyLarge)
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = { chosen?.let(onChoose) }, enabled = chosen != null, modifier = Modifier.testTag("workflowShareInputConfirm")) { Text("Continue") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } })
}
