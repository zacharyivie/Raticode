package dev.raticode.mobile.ui

import android.content.ClipData
import android.content.ClipboardManager
import androidx.annotation.RequiresApi
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.live.*
import org.json.JSONObject

/*
 * What Rem remembers, on the phone: the same list, modes and changes as the desktop's
 * memory panel (RemMemoryPanel.jsx). The desktop stores the memories and applies every change.
 */

private val MODES = listOf(
    Triple("on", "On", "Rem uses what it remembers and saves new facts and preferences you state."),
    Triple("paused", "Paused", "Rem uses what it remembers but saves nothing new. Asking it to correct or forget something still works."),
    Triple("off", "Off", "Rem neither uses nor saves memories. Nothing here is deleted."),
)

/** The lines a reply's memory note shows, as on the desktop. */
internal fun memoryNoteLines(note: RemMemoryNote): List<String> {
    fun quoted(entry: RemMemoryEntry) = if (entry.removed) "a memory you later forgot" else "“${entry.text}”"
    return note.saved.map { "Saved to memory: ${quoted(it)}" } +
        note.updated.map { "Updated memory: ${quoted(it)}" } +
        note.forgotten.map { "Forgot: ${quoted(it)}" } +
        listOfNotNull(when {
            note.refused == 1 -> "Didn’t save 1 item that looked like a password, key or token."
            note.refused > 1 -> "Didn’t save ${note.refused} items that looked like passwords, keys or tokens."
            else -> null
        })
}

/** Under a reply: what it saved, changed or forgot, with Undo. */
@RequiresApi(29)
@Composable
internal fun RemMemoryNoteView(client: LiveClient, message: RemMessage) {
    val note = message.memory ?: return
    val busy = "rem.memories" in client.desktopBusy || !client.connected || !client.desktopControl
    Row(Modifier.fillMaxWidth().testTag("remMemoryNote")
        .border(1.dp, MaterialTheme.colorScheme.outlineVariant, RoundedCornerShape(8.dp))
        .padding(horizontal = 10.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Column(Modifier.weight(1f)) {
            memoryNoteLines(note).forEach {
                Text(it, style = MaterialTheme.typography.bodySmall, color = LocalRemColors.current.muted)
            }
        }
        if (note.undone) Text("Undone", style = MaterialTheme.typography.labelSmall, color = LocalRemColors.current.muted)
        else if (note.undoable) TextButton(onClick = { client.desktopAction("rem.memory.undo", JSONObject().put("message_id", message.id)) },
            enabled = !busy, modifier = Modifier.semantics { contentDescription = "Undo this memory change" }) { Text("Undo") }
    }
}

@RequiresApi(29)
@Composable
internal fun RemMemoryScreen(client: LiveClient, onBack: () -> Unit) {
    val view = client.desktopViews["rem.memories"] as? RemMemories
    val busy = "rem.memories" in client.desktopBusy || !client.connected || !client.desktopControl
    val clipboard = LocalContext.current.getSystemService(ClipboardManager::class.java)
    var editing by rememberSaveable { mutableStateOf<String?>(null) }
    var editText by rememberSaveable { mutableStateOf("") }
    var draft by rememberSaveable { mutableStateOf("") }
    val error = client.desktopErrors["rem.memories"]
    Header("What Rem remembers", view?.let { "${it.memories.size} saved" }.orEmpty(), onBack) {
        TextButton(onClick = {
            val text = view?.memories.orEmpty().joinToString("\n") { "- ${it.text}" }
            clipboard?.setPrimaryClip(ClipData.newPlainText("What Rem remembers", text))
        }, enabled = !view?.memories.isNullOrEmpty()) { Text("Copy all") }
    }
    DesktopGate(client, "rem.memories", "Loading what Rem remembers…") {
        val shown = view ?: return@DesktopGate
        LazyColumn(Modifier.fillMaxSize().testTag("remMemories"), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            item {
                Note("Rem saves facts and preferences you state so it doesn’t have to ask again, and you can ask it in chat to change or forget one. " +
                    "Each message sends these to the chat model’s provider, so Rem never keeps passwords, keys or tokens.")
            }
            item {
                Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Row(Modifier.semantics { contentDescription = "Memory" }, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        MODES.forEach { (value, label, _) ->
                            FilterChip(shown.mode == value, onClick = { if (shown.mode != value) client.desktopAction("rem.memory.mode", JSONObject().put("mode", value)) },
                                label = { Text(label) }, enabled = !busy)
                        }
                    }
                    MODES.firstOrNull { it.first == shown.mode }?.let {
                        Text(it.third, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
            if (error != null) item {
                Text(error, Modifier.semantics { liveRegion = LiveRegionMode.Polite }, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
            }
            if (shown.memories.isEmpty()) item { Note("Rem hasn’t saved anything yet.") }
            items(shown.memories, key = { it.id }) { memory ->
                Surface(shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.surface, modifier = Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        if (editing == memory.id) {
                            OutlinedTextField(editText, { if (it.length <= 2000) editText = it }, Modifier.fillMaxWidth(), label = { Text("Memory text") })
                            Row {
                                TextButton(onClick = {
                                    client.desktopAction("rem.memory.edit", JSONObject().put("memory_id", memory.id).put("text", editText.trim()))
                                    editing = null
                                }, enabled = !busy && editText.isNotBlank()) { Text("Save") }
                                TextButton(onClick = { editing = null }) { Text("Cancel") }
                            }
                        } else {
                            Text(memory.text, style = MaterialTheme.typography.bodyMedium)
                            Text(listOf(whenText(memory.createdAt, past = true), if (memory.addedByUser) "added by you" else "",
                                if (memory.editedAt.isNotBlank()) "edited" else "").filter { it.isNotBlank() }.joinToString(" · "),
                                style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            Row {
                                TextButton(onClick = { editing = memory.id; editText = memory.text }, enabled = !busy) { Text("Edit") }
                                TextButton(onClick = { client.desktopAction("rem.memory.forget", JSONObject().put("memory_id", memory.id)) }, enabled = !busy,
                                    colors = ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.error)) { Text("Forget") }
                            }
                        }
                    }
                }
            }
            item {
                Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    OutlinedTextField(draft, { if (it.length <= 2000) draft = it }, Modifier.fillMaxWidth().testTag("remMemoryDraft"),
                        label = { Text("Add something for Rem to remember") })
                    Button(onClick = { client.desktopAction("rem.memory.add", JSONObject().put("text", draft.trim())); draft = "" },
                        enabled = !busy && draft.isNotBlank()) { Text("Add") }
                }
            }
        }
    }
}
