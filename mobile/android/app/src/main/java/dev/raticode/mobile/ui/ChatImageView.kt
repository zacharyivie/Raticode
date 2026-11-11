package dev.raticode.mobile.ui

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.live.ChatImages
import dev.raticode.mobile.live.ChatLink
import dev.raticode.mobile.live.ChatImage

/** Loads message images through the paired desktop. Null where no desktop link is available. */
internal class ChatImageSource(val enabled: Boolean, val state: (String) -> ChatImage?, val request: (String) -> Unit)
internal val LocalChatImages = compositionLocalOf<ChatImageSource?> { null }

/**
 * A web image from Rem's reply, fetched by the desktop. Without a desktop that can fetch it,
 * or when the fetch fails, the phone shows a card that opens the address only when tapped.
 */
@Composable
internal fun ChatImageView(url: String, alt: String) {
    val source = LocalChatImages.current
    val enabled = source?.enabled == true && ChatImages.fetchable(url)
    LaunchedEffect(url, enabled) { if (enabled) source?.request?.invoke(url) }
    val state = if (enabled) source?.state?.invoke(url) else null
    var zoomed by remember(url) { mutableStateOf(false) }
    val label = alt.ifBlank { "Image" }
    when (state) {
        is ChatImage.Ready -> {
            val bitmap = remember(state.bitmap) { state.bitmap.asImageBitmap() }
            Image(bitmap, label, Modifier.widthIn(max = 480.dp).heightIn(max = 360.dp).clip(RoundedCornerShape(8.dp))
                .clickable(onClickLabel = "View full size", role = Role.Image) { zoomed = true }, contentScale = ContentScale.Fit)
            if (zoomed) {
                val opener = LocalUriHandler.current
                val links = LocalChatLinks.current
                MediaImageViewer(bitmap, label, onClose = { zoomed = false }) {
                    TextButton(onClick = { links?.open(ChatLink.Web(url), null) ?: runCatching { opener.openUri(url) } }, colors = ViewerButton) { Text("Open in browser") }
                }
            }
        }
        ChatImage.Loading -> Box(Modifier.widthIn(max = 480.dp).fillMaxWidth().height(160.dp).clip(RoundedCornerShape(8.dp))
            .background(MaterialTheme.colorScheme.surfaceVariant).semantics { contentDescription = "Loading $label" }, contentAlignment = Alignment.Center) {
            CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp)
        }
        else -> ImageLink(url, label, if (state == ChatImage.Failed) "Your desktop couldn't load this image" else null)
    }
}

@Composable
private fun ImageLink(url: String, label: String, note: String?) {
    val opener = LocalUriHandler.current
    val links = LocalChatLinks.current
    val host = remember(url) { runCatching { java.net.URI(url).host }.getOrNull().orEmpty() }
    Surface(shape = RoundedCornerShape(8.dp), color = MaterialTheme.colorScheme.surfaceVariant,
        onClick = { if (ChatImages.fetchable(url)) links?.open(ChatLink.Web(url), null) ?: runCatching { opener.openUri(url) } }) {
        Row(Modifier.padding(horizontal = 12.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Icon(ThreadIcons.Image, null, Modifier.size(18.dp))
            Column(Modifier.weight(1f, fill = false)) {
                Text(label, style = MaterialTheme.typography.labelLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(note ?: "Open $host in your browser", style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
    }
}
