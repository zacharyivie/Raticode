package dev.raticode.mobile.ui

import androidx.activity.compose.BackHandler
import androidx.annotation.RequiresApi
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.live.ChatMedia
import dev.raticode.mobile.live.MediaKind
import dev.raticode.mobile.share.ShareIntake
import dev.raticode.mobile.share.ShareSession
import dev.raticode.mobile.share.ShareSession.Phase

/** The sheet another app's share opens: the shared text, editable, and the files, each removable, then Send. */
@RequiresApi(29)
@Composable
fun SharePanel(session: ShareSession, onOpenRem: () -> Unit, onClose: () -> Unit) {
    val phase = session.phase
    // Back leaves a review, but never abandons a send part-way; Stop does that.
    BackHandler(enabled = phase == Phase.SENDING) {}
    Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha = .45f)).clearAndSetSemantics {})
    Box(Modifier.fillMaxSize().safeDrawingPadding().imePadding(), contentAlignment = Alignment.BottomCenter) {
        Surface(Modifier.fillMaxWidth().widthIn(max = 560.dp).fillMaxHeight(.92f).wrapContentHeight(Alignment.Bottom).testTag("sharePanel"),
            shape = RoundedCornerShape(24.dp), color = MaterialTheme.colorScheme.surface, shadowElevation = 8.dp) {
            Column(Modifier.padding(start = 20.dp, end = 12.dp, top = 16.dp, bottom = 12.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    RemFace(36.dp, 22.dp)
                    Column(Modifier.weight(1f)) {
                        Text("Send to Rem", style = MaterialTheme.typography.titleLarge, modifier = Modifier.semantics { heading() })
                        Text(if (phase == Phase.SENT) "In Rem's chat on your desktop" else "Nothing goes until you tap Send",
                            style = MaterialTheme.typography.bodySmall, color = LocalRemColors.current.muted)
                    }
                    if (phase != Phase.SENDING) IconButton(onClick = onClose) { Icon(ThreadIcons.Close, "Close") }
                }
                Spacer(Modifier.height(12.dp))
                ShareBody(session, onOpenRem, onClose)
            }
        }
    }
}

@RequiresApi(29)
@Composable
private fun ColumnScope.ShareBody(session: ShareSession, onOpenRem: () -> Unit, onClose: () -> Unit) {
    val phase = session.phase
    val editing = phase == Phase.REVIEW
    Column(Modifier.weight(1f, fill = false).verticalScroll(rememberScrollState()).padding(end = 8.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        if (phase == Phase.LOADING) {
            LinearProgressIndicator(Modifier.fillMaxWidth())
            Text("Opening what you shared…", style = MaterialTheme.typography.bodyMedium)
            return@Column
        }
        OutlinedTextField(session.text, { session.text = it.take(ShareIntake.MAX_TEXT) }, Modifier.fillMaxWidth().testTag("shareText"),
            enabled = editing, label = { Text("Message to Rem") }, placeholder = { Text("Add a note, like “summarize this”") },
            minLines = 2, maxLines = 8, textStyle = MaterialTheme.typography.bodyLarge)
        if (session.shortened) Hint("Shortened to ${ShareIntake.MAX_TEXT} characters, the most Rem takes in one message.")
        session.items.forEach { item -> ShareFileRow(session, item, editing) }
        session.notices.forEach { Hint(it, warn = true) }
        if (editing) Hint("Rem reads all of this as your request and may pass it to the AI model your desktop uses. " +
            "Check shared text for instructions you didn't write.")
    }
    Spacer(Modifier.height(10.dp))
    val status = session.error ?: session.blocker?.takeIf { phase == Phase.REVIEW && (session.text.isNotBlank() || session.items.isNotEmpty()) }
    status?.let {
        Text(it, Modifier.padding(end = 8.dp).semantics { liveRegion = LiveRegionMode.Polite }.testTag("shareError"),
            style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.error)
    }
    if (phase == Phase.SENDING || phase == Phase.QUEUED) Row(Modifier.padding(top = 6.dp, end = 8.dp), verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        if (phase == Phase.SENDING || session.watching) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
        Text(session.step, Modifier.weight(1f).semantics { liveRegion = LiveRegionMode.Polite }.testTag("shareStep"), style = MaterialTheme.typography.bodyMedium)
    }
    if (phase == Phase.SENT) Text("Sent. Rem's reply will be in Rem's chat.", Modifier.semantics { liveRegion = LiveRegionMode.Polite }.testTag("shareSent"),
        style = MaterialTheme.typography.bodyMedium)
    Row(Modifier.fillMaxWidth().padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.End), verticalAlignment = Alignment.CenterVertically) {
        when (phase) {
            Phase.LOADING -> TextButton(onClick = onClose) { Text("Cancel") }
            Phase.REVIEW -> {
                if (session.client.loaded && !session.client.paired) TextButton(onClick = onOpenRem) { Text("Open Raticode") }
                TextButton(onClick = onClose) { Text("Cancel") }
                Button(onClick = session::send, enabled = session.blocker == null, modifier = Modifier.testTag("shareSend")) {
                    Icon(ThreadIcons.Send, null, Modifier.size(18.dp)); Spacer(Modifier.width(8.dp)); Text("Send")
                }
            }
            Phase.SENDING -> OutlinedButton(onClick = session::cancel) { Text("Stop") }
            Phase.QUEUED, Phase.SENT -> {
                TextButton(onClick = onOpenRem) { Text("Open Rem chat") }
                Button(onClick = onClose) { Text("Done") }
            }
        }
    }
}

@RequiresApi(29)
@Composable
private fun ShareFileRow(session: ShareSession, item: ShareSession.Item, editing: Boolean) {
    val progress = session.progress(item)
    Surface(shape = RoundedCornerShape(10.dp), color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = .5f), modifier = Modifier.fillMaxWidth()) {
        Row(Modifier.padding(start = 12.dp, top = 6.dp, bottom = 6.dp, end = 4.dp), verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            val icon = when (ChatMedia.kind(item.name)) { MediaKind.IMAGE -> ThreadIcons.Image; MediaKind.VIDEO -> ThreadIcons.Video; null -> ThreadIcons.FileText }
            Icon(icon, null, Modifier.size(18.dp))
            Column(Modifier.weight(1f).padding(vertical = 4.dp)) {
                Text(item.name, style = MaterialTheme.typography.labelLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(item.size?.let(::fileBytes) ?: "Size unknown", style = MaterialTheme.typography.labelSmall, color = LocalRemColors.current.muted)
                item.problem?.let { Text(it, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.error) }
                if (progress != null && session.phase != Phase.REVIEW) LinearProgressIndicator({ progress / 100f }, Modifier.fillMaxWidth().padding(top = 4.dp)
                    .semantics { contentDescription = "Uploaded $progress%" })
            }
            if (editing) IconButton(onClick = { session.remove(item.key) }) { Icon(ThreadIcons.Close, "Remove ${item.name}", Modifier.size(18.dp)) }
        }
    }
}

@Composable
private fun Hint(text: String, warn: Boolean = false) {
    Text(text, style = MaterialTheme.typography.bodySmall,
        color = if (warn) MaterialTheme.colorScheme.error else LocalRemColors.current.muted)
}
