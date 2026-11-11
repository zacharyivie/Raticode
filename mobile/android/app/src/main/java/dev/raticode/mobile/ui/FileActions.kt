package dev.raticode.mobile.ui

import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.data.OpenedFiles
import dev.raticode.mobile.live.FileExplorer
import dev.raticode.mobile.live.FileViewer
import dev.raticode.mobile.live.LiveClient

/** Download, then Save or Open, for one file the desktop sent. Stateless so it can be tested alone. */
@Composable
internal fun ReceivedFileActions(name: String, state: String?, percent: Int, connected: Boolean,
                                 onDownload: () -> Unit, onSave: () -> Unit, onOpen: () -> Unit) {
    val padding = PaddingValues(horizontal = 8.dp)
    when (state) {
        "offered" -> TextButton(onClick = onDownload, enabled = connected, contentPadding = padding,
            modifier = Modifier.semantics { contentDescription = "Download $name" }) {
            Icon(ThreadIcons.Download, null, Modifier.size(14.dp)); Spacer(Modifier.width(4.dp)); Text("Download")
        }
        "receiving" -> Text("$percent%", Modifier.padding(horizontal = 8.dp), style = MaterialTheme.typography.labelSmall)
        "available" -> Row {
            TextButton(onClick = onSave, contentPadding = padding, modifier = Modifier.semantics { contentDescription = "Save $name" }) { Text("Save") }
            TextButton(onClick = onOpen, contentPadding = padding, modifier = Modifier.semantics { contentDescription = "Open $name" }) { Text("Open") }
        }
        else -> Text("Ask Rem to send it again", Modifier.padding(horizontal = 8.dp), style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

private data class OpenProblem(val id: String, val name: String, val message: String)

/**
 * Opens a downloaded file in another app. When that isn't possible it says why and offers
 * Save instead. Opening only ever follows a tap; nothing opens on its own.
 */
@androidx.annotation.RequiresApi(29)
@Composable
internal fun rememberFileOpener(client: LiveClient, onSave: (String, String) -> Unit): (String, String) -> Unit {
    val context = LocalContext.current
    var problem by remember { mutableStateOf<OpenProblem?>(null) }
    var inApp by remember { mutableStateOf<Pair<String, String>?>(null) }
    inApp?.let { (id, name) -> InAppFileDialog(client, id, name, onClose = { inApp = null }, onSave = { inApp = null; onSave(id, name) }) }
    problem?.let { current ->
        AlertDialog(onDismissRequest = { problem = null }, title = { Text("Can't open ${current.name}") }, text = { Text(current.message) },
            confirmButton = { TextButton(onClick = { problem = null; onSave(current.id, current.name) }) { Text("Save instead") } },
            dismissButton = { TextButton(onClick = { problem = null }) { Text("Close") } })
    }
    return remember(client, context) {
        { id, name ->
            if (OpenedFiles.isInstaller(name))
                problem = OpenProblem(id, name, "Raticode can't install apps. Save the file, then open it from your Files app to install it.")
            else if (FileExplorer.inAppFirst(name, OpenedFiles.mimeType(name))) inApp = id to name
            else client.openFile(id) { result ->
                val file = result.getOrNull()
                problem = when {
                    // The download finished after a lock came on: nothing opens over the lock.
                    dev.raticode.mobile.applock.AppLockRuntime.locked(context) || dev.raticode.mobile.childlock.ChildLockRuntime.locked(context) -> { file?.parentFile?.deleteRecursively(); null }
                    file == null -> OpenProblem(id, name, "This file couldn't be prepared. Download it again, or save it.")
                    OpenedFiles.view(context, file, name) -> null
                    else -> {
                        file.parentFile?.deleteRecursively()
                        // Code, Markdown and other files the app can show itself open here instead.
                        if (FileExplorer.viewer(name) != FileViewer.OTHER) { inApp = id to name; return@openFile }
                        val kind = OpenedFiles.extension(name)?.let { ".$it files" } ?: "this kind of file"
                        OpenProblem(id, name, "No app on this phone can open $kind. Save it instead, or install an app that opens it.")
                    }
                }
            }
        }
    }
}
