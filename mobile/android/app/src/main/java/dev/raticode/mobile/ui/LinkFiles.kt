package dev.raticode.mobile.ui

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import androidx.core.net.toUri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.annotation.RequiresApi
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.live.ChatLink
import dev.raticode.mobile.live.LiveClient

/**
 * Opens a web address in the phone's default browser. The browser selector keeps an app that
 * claims the site from taking it; without a default browser any app that views web pages is used.
 */
internal fun openInBrowser(context: Context, url: String): Boolean {
    val view = Intent(Intent.ACTION_VIEW, url.toUri()).addCategory(Intent.CATEGORY_BROWSABLE)
    if (context !is android.app.Activity) view.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    val browser = Intent(view).apply { selector = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_APP_BROWSER) }
    for (intent in listOf(browser, view)) {
        try { context.startActivity(intent); return true } catch (_: ActivityNotFoundException) {} catch (_: SecurityException) {}
    }
    return false
}

/**
 * Routes taps on links in messages and shows the sheet for a tapped file link. Web links go to
 * [openWeb] (Raticode's browser, when the user keeps that on) and otherwise to the phone's browser. File links ask the desktop for the file; it downloads and then opens on its own,
 * because the tap already asked for it. Opening checks the file and offers Save when no app can open it.
 */
@RequiresApi(29)
@Composable
internal fun rememberChatLinks(client: LiveClient, openWeb: (String) -> Boolean = { false }): ChatLinkHandler {
    val inApp by androidx.compose.runtime.rememberUpdatedState(openWeb)
    val context = LocalContext.current
    var exportId by rememberSaveable { mutableStateOf<String?>(null) }
    val exporter = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/octet-stream")) { uri ->
        exportId?.let { id -> if (uri != null) client.exportFile(id, uri) }
        exportId = null
    }
    val save = { id: String, name: String -> exportId = id; exporter.launch(name) }
    val open = rememberFileOpener(client, save)
    var unopened by remember { mutableStateOf<String?>(null) }
    unopened?.let { url ->
        AlertDialog(onDismissRequest = { unopened = null }, title = { Text("No browser") },
            text = { Text("This phone has no app that opens web pages. The address is $url") },
            confirmButton = { TextButton(onClick = { unopened = null }) { Text("Close") } })
    }
    client.linkFetch?.let { fetch ->
        val file = fetch.fileId?.let { id -> client.files.firstOrNull { it.id == id } }
        val ready = fetch.error == null && file?.state == "available"
        // Closing the sheet first cancels the open; a later tap opens the downloaded copy.
        LaunchedEffect(fetch.key, file?.id, ready) {
            if (ready && file != null) { client.dismissLinkFetch(); open(file.id, file.name) }
        }
        if (!ready) LinkFileDialog(fetch, file, client.connected, onDownload = { file?.let { client.requestFile(it.id) } },
            onDismiss = client::dismissLinkFetch)
    }
    return remember(client, context) {
        ChatLinkHandler { link, source ->
            when (link) {
                is ChatLink.Web -> if (!inApp(link.url) && !openInBrowser(context, link.url)) unopened = link.url
                is ChatLink.File -> if (source != null) client.fetchLink(link, source)
            }
        }
    }
}

/** What a tapped file link is doing. Stateless so it can be tested alone. */
@Composable
internal fun LinkFileDialog(fetch: LiveClient.LinkFetch, file: LiveClient.LiveFile?, connected: Boolean,
                            onDownload: () -> Unit, onDismiss: () -> Unit) {
    val name = file?.name ?: fetch.name
    val state = when {
        fetch.error != null -> "error"
        fetch.fileId == null || file == null -> "asking"
        else -> file.state
    }
    AlertDialog(onDismissRequest = onDismiss, modifier = Modifier.testTag("linkFileDialog"), title = { Text(name) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                when (state) {
                    "error" -> Text(fetch.error.orEmpty())
                    "asking" -> { Text("Asking your desktop for this file…"); LinearProgressIndicator(Modifier.fillMaxWidth()) }
                    "offered" -> Text(if (connected) "Starting the download…" else "Connect to your desktop to download this file.")
                    "receiving" -> {
                        val progress = if (file!!.size > 0) file.bytes.toFloat() / file.size else 0f
                        Text("Downloading ${fileBytes(file.bytes)} of ${fileBytes(file.size)}")
                        LinearProgressIndicator({ progress }, Modifier.fillMaxWidth().semantics { contentDescription = "${(progress * 100).toInt()}% downloaded" })
                    }
                    "available" -> Text("Opening…")
                    else -> Text("The download didn't finish. Tap the link again to ask your desktop for it.")
                }
            }
        },
        confirmButton = { if (state == "offered") TextButton(onClick = onDownload, enabled = connected) { Text("Download") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Close", style = MaterialTheme.typography.labelLarge) } })
}
