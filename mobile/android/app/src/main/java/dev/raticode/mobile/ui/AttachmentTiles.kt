package dev.raticode.mobile.ui

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import dev.raticode.mobile.live.FileKind
import dev.raticode.mobile.live.FileKinds
import dev.raticode.mobile.live.FileTransfers
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.live.MediaPreview
import dev.raticode.mobile.live.PreviewMode
import dev.raticode.mobile.live.RemAttachments
import dev.raticode.mobile.live.RemFile
import dev.raticode.mobile.live.TextPreview
import dev.raticode.mobile.live.UploadQueue

/** Lets attachment tiles in Rem's chat and threads read and open this phone's copies of files. */
internal class AttachmentHost(
    val files: () -> List<LiveClient.LiveFile>,
    val previews: () -> Map<String, MediaPreview>,
    val requestPreview: (String) -> Unit,
    val openImage: (String, (android.graphics.Bitmap?) -> Unit) -> Unit,
    val readMedia: (String, (ByteArray?) -> Unit) -> Unit,
    val readText: (String, (TextPreview?) -> Unit) -> Unit,
    val open: (String, String) -> Unit,
    /** Whether the phone is connected, so an upload can say it is waiting rather than look stuck. */
    val connected: () -> Boolean = { true },
    /** Downloads holding Rem chat attachments fetched from the desktop, by attachment ID. */
    val copies: () -> Map<String, String> = { emptyMap() },
    val fetches: () -> Map<String, RemAttachments.Fetch> = { emptyMap() },
    /** Whether the desktop offers Rem chat attachments on request. */
    val canFetch: () -> Boolean = { false },
    /** Fetches an attachment of a Rem chat message: (message ID, attachment, without a tap). */
    val fetch: (String, RemFile, Boolean) -> Unit = { _, _, _ -> },
) {
    companion object {
        @androidx.annotation.RequiresApi(29)
        fun of(client: LiveClient, open: (String, String) -> Unit) = AttachmentHost({ client.files }, { client.mediaPreviews },
            client::requestMediaPreview, client::openImage, client::readMedia, client::readText, open, { client.connected },
            { client.attachmentCopies }, { client.attachmentFetches }, { client.attachmentFiles },
            { message, file, auto -> if (auto) client.autoFetchAttachment(message, file) else client.fetchAttachment(message, file) })
    }
}
internal val LocalAttachments = staticCompositionLocalOf<AttachmentHost?> { null }

/**
 * One attached file: a thumbnail for a picture or video this phone holds, otherwise a file-type tile, which
 * previews the file when tapped. [onRemove] makes it a composer chip. The phone holds the files it sent itself
 * until they expire. Given [messageId], the attachment is on that Rem chat message, so a file this phone doesn't
 * hold, such as one attached on the computer, is fetched from the desktop: pictures and videos at once, other files
 * when tapped.
 */
@Composable
internal fun AttachmentTile(id: String, name: String, size: Long, onRemove: (() -> Unit)? = null, messageId: String? = null) {
    val host = LocalAttachments.current
    val files = host?.files?.invoke().orEmpty()
    val own = files.firstOrNull { it.id == id }
    // A copy fetched from the desktop stands in once the phone's own upload is gone or never was.
    val copy = if (messageId == null || onRemove != null) null else host?.copies?.invoke()?.get(id)?.let { copy -> files.firstOrNull { it.id == copy } }
    val ownUsable = own != null && (FileTransfers.holds(own.direction, own.state) || (own.direction == "upload" && own.state == "receiving"))
    val local = if (ownUsable || copy == null) own else copy
    val fileId = local?.id ?: id
    val held = local != null && FileTransfers.holds(local.direction, local.state)
    val mode = FileKinds.previewMode(name)
    val visual = mode == PreviewMode.IMAGE || mode == PreviewMode.VIDEO
    val preview = if (visual && held) host?.previews?.invoke()?.get(fileId) else null
    LaunchedEffect(fileId, held, preview == null) { if (visual && held && preview == null) host?.requestPreview?.invoke(fileId) }
    val poster = (preview as? MediaPreview.Ready)?.poster
    val bitmap = remember(poster) { poster?.asImageBitmap() }
    var open by remember(id) { mutableStateOf(false) }
    var openWhenReady by remember(id) { mutableStateOf(false) }
    // This phone's own upload, or a copy coming from the desktop: how much has arrived, or that it stopped.
    val upload = local?.takeIf { it.direction == "upload" }
    val download = local?.takeIf { it === copy && it.direction == "download" && it.state in setOf("offered", "receiving") }
    val sending = upload?.state in setOf("offered", "receiving")
    val connected = host?.connected?.invoke() != false
    val fetch = host?.fetches?.invoke()?.get(id)
    val remote = if (messageId != null && onRemove == null && !held && !sending && download == null)
        RemAttachments.remote(name, size, fetch, host?.canFetch?.invoke() == true, connected) else null
    val file = RemFile(id, name, size)
    LaunchedEffect(id, remote?.fetchable, fetch) {
        if (visual && remote?.fetchable == true && fetch == null) host?.fetch?.invoke(messageId!!, file, true)
    }
    LaunchedEffect(held, openWhenReady) { if (held && openWhenReady) { openWhenReady = false; open = true } }
    val moving = upload?.takeIf { sending } ?: download
    AttachmentTileView(name, size, bitmap, mode, sending = sending || download != null || fetch == RemAttachments.Fetch.Asking && remote != null ||
        (onRemove != null && local == null),
        missing = onRemove == null && !held && !sending && download == null && remote == null, onOpen = {
            if (remote?.fetchable == true) { openWhenReady = true; host?.fetch?.invoke(messageId!!, file, false) }
            else if (remote == null || remote.note.isNotEmpty()) open = true
        }, onRemove = onRemove,
        progress = moving?.let { UploadQueue.progress(it.state, it.bytes, it.size) },
        failed = onRemove != null && upload?.state in setOf("failed", "cancelled"), offline = (sending || download != null) && !connected,
        meta = remote?.meta, downloading = download != null)
    if (open) AttachmentPreview(host, fileId, name, local, held, mode, bitmap, remote?.note, onClose = { open = false })
}

/** What a tile says when tapped on a file this phone doesn't have. */
internal fun missingNote(name: String, state: String?): String = when (state) {
    "failed", "cancelled" -> "$name didn't finish sending, so this phone no longer has it. Attach it again to see it here."
    else -> "This phone doesn't have a copy of $name. Files attached on your computer stay there, and the phone keeps " +
        "the files it sends for about a day. Open it on your computer."
}

/** What a tile's second line says about an upload. */
internal fun uploadMeta(progress: Float?, failed: Boolean, offline: Boolean, downloading: Boolean = false): String? = when {
    failed -> "Didn't upload"
    progress == null -> null
    offline -> "Not connected · ${percent(progress)}"
    downloading -> "Downloading · ${percent(progress)}"
    else -> "Uploading · ${percent(progress)}"
}

internal fun percent(progress: Float) = "${(progress * 100).toInt().coerceIn(0, 100)}%"

/**
 * The tile itself. Stateless so it can be tested alone. While [sending], a ring over the thumbnail fills with
 * [progress], the share of the file the desktop has confirmed (or, [downloading], the phone has received), or spins
 * when that isn't known yet. [failed] marks an upload that stopped; [offline] one waiting for the connection. [meta]
 * replaces the second line for a file that is only on the computer.
 */
@Composable
internal fun AttachmentTileView(name: String, size: Long, thumbnail: ImageBitmap?, mode: PreviewMode, sending: Boolean, missing: Boolean,
                                onOpen: () -> Unit, onRemove: (() -> Unit)? = null, progress: Float? = null, failed: Boolean = false,
                                offline: Boolean = false, meta: String? = null, downloading: Boolean = false) {
    val ink = LocalContentColor.current
    val upload = uploadMeta(progress.takeIf { sending }, failed, offline, downloading)
    val meta = when {
        upload != null -> upload
        meta != null -> meta
        missing -> "Not on this phone"
        upload != null -> upload
        else -> "${FileKinds.label(name)} · ${fileBytes(size)}"
    }
    val large = onRemove == null && thumbnail != null && !sending && !failed
    Surface(shape = RoundedCornerShape(10.dp), color = ink.copy(alpha = .07f), border = BorderStroke(1.dp, ink.copy(alpha = .22f)),
        modifier = Modifier.testTag("attachmentTile")) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            val tap = Modifier.clickable(onClickLabel = "Preview", role = Role.Button, onClick = onOpen)
                .semantics { contentDescription = "$name, $meta" }
            if (large) Column(tap.width(160.dp)) {
                Thumbnail(thumbnail, mode, Modifier.size(160.dp, 112.dp))
                Column(Modifier.padding(horizontal = 8.dp, vertical = 4.dp)) { Caption(name, meta) }
            } else Row(tap.padding(4.dp).padding(end = 6.dp), verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Box(Modifier.size(44.dp).clip(RoundedCornerShape(7.dp))) {
                    Thumbnail(thumbnail, mode, Modifier.fillMaxSize(), name)
                    if (failed) Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha = .55f)), contentAlignment = Alignment.Center) {
                        Icon(ThreadIcons.Failed, null, Modifier.size(20.dp), tint = Color.White)
                    }
                    else if (sending) Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha = .45f)), contentAlignment = Alignment.Center) {
                        if (progress == null) CircularProgressIndicator(Modifier.size(18.dp), color = Color.White, strokeWidth = 2.dp)
                        else {
                            // Fills smoothly between the desktop's confirmations, which come every 16 KiB.
                            val shown by animateFloatAsState(progress, tween(500, easing = LinearEasing), label = "upload")
                            CircularProgressIndicator({ shown }, Modifier.size(30.dp).testTag("uploadRing"), color = Color.White,
                                strokeWidth = 3.dp, trackColor = Color.White.copy(alpha = .3f), gapSize = 0.dp)
                        }
                    }
                }
                Column(Modifier.widthIn(max = 150.dp)) { Caption(name, meta, if (failed) MaterialTheme.colorScheme.error else null) }
            }
            if (onRemove != null) IconButton(onClick = onRemove, modifier = Modifier.size(36.dp)) {
                Icon(ThreadIcons.Close, "Remove $name", Modifier.size(14.dp))
            }
        }
    }
}

@Composable
private fun Caption(name: String, meta: String, metaColor: Color? = null) {
    Text(name, style = MaterialTheme.typography.labelMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
    Text(meta, style = MaterialTheme.typography.labelSmall, color = metaColor ?: LocalContentColor.current.copy(alpha = .72f), maxLines = 1)
}

@Composable
private fun Thumbnail(bitmap: ImageBitmap?, mode: PreviewMode, modifier: Modifier, name: String = "") {
    if (bitmap == null) { KindBadge(name, modifier); return }
    Box(modifier.clip(RoundedCornerShape(topStart = 9.dp, topEnd = 9.dp)), contentAlignment = Alignment.Center) {
        Image(bitmap, null, Modifier.fillMaxSize(), contentScale = ContentScale.Crop)
        if (mode == PreviewMode.VIDEO) Box(Modifier.size(28.dp).background(Color.Black.copy(alpha = .6f), CircleShape), contentAlignment = Alignment.Center) {
            Icon(ThreadIcons.Play, null, Modifier.size(14.dp).padding(start = 1.dp), tint = Color.White)
        }
    }
}

private fun kindColor(kind: FileKind) = Color(when (kind) {
    FileKind.IMAGE -> 0xFF0EA5E9; FileKind.VIDEO -> 0xFF8B5CF6; FileKind.AUDIO -> 0xFFDB4F96; FileKind.PDF -> 0xFFDC4444
    FileKind.SHEET -> 0xFF2E9D63; FileKind.CODE -> 0xFF6366F1; FileKind.ARCHIVE -> 0xFFC9851B; FileKind.DOCUMENT -> 0xFF3B82F6
    FileKind.TEXT, FileKind.FILE -> 0xFF64748B
})

/** A file-type tile: the kind's icon and short type on its color, like the desktop's. */
@Composable
internal fun KindBadge(name: String, modifier: Modifier = Modifier, large: Boolean = false) {
    val kind = FileKinds.kind(name)
    val color = kindColor(kind)
    Box(modifier.background(color.copy(alpha = .18f)).clearAndSetSemantics {}, contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Icon(when (kind) { FileKind.IMAGE -> ThreadIcons.Image; FileKind.VIDEO -> ThreadIcons.Video; else -> ThreadIcons.FileText },
                null, Modifier.size(if (large) 30.dp else 16.dp), tint = color)
            Text(FileKinds.label(name), color = color, fontSize = if (large) 13.sp else 8.sp, style = MaterialTheme.typography.labelSmall)
        }
    }
}

/**
 * What tapping a tile opens: a picture full screen, the video player, the text, or another app for other files.
 * A file this phone doesn't hold says why instead.
 */
@Composable
private fun AttachmentPreview(host: AttachmentHost?, id: String, name: String, local: LiveClient.LiveFile?, held: Boolean, mode: PreviewMode,
                              poster: ImageBitmap?, note: String?, onClose: () -> Unit) {
    if (host == null || !held) {
        AlertDialog(onDismissRequest = onClose, title = { Text(name) }, text = { Text(note?.ifBlank { null } ?: missingNote(name, local?.state)) },
            confirmButton = { TextButton(onClick = onClose) { Text("Close") } })
        return
    }
    when (mode) {
        PreviewMode.IMAGE -> {
            var full by remember(id) { mutableStateOf<ImageBitmap?>(null) }
            var failed by remember(id) { mutableStateOf(false) }
            LaunchedEffect(id) { host.openImage(id) { bitmap -> if (bitmap == null) failed = true else full = bitmap.asImageBitmap() } }
            MediaImageViewer(full ?: poster, name, loading = full == null && !failed,
                note = if (failed && poster == null) "This picture couldn't be shown on this phone. Open it in another app instead." else null,
                onClose = onClose) {
                TextButton(onClick = { onClose(); host.open(id, name) }, colors = ViewerButton) { Text("Open in another app") }
            }
        }
        PreviewMode.VIDEO -> VideoPlayerDialog(name, poster, load = { done -> host.readMedia(id, done) }, onClose = onClose,
            onOpenElsewhere = { onClose(); host.open(id, name) })
        PreviewMode.TEXT -> TextPreviewDialog(name, load = { done -> host.readText(id, done) }, onClose = onClose,
            onOpenElsewhere = { onClose(); host.open(id, name) })
        // Other files open straight in the app the phone uses for them; with none, Open offers Save instead.
        PreviewMode.OTHER -> LaunchedEffect(id) { onClose(); host.open(id, name) }
    }
}

/** A text or code file shown full screen in a fixed-width font. Only the first 256 KB is read. */
@Composable
internal fun TextPreviewDialog(name: String, load: ((TextPreview?) -> Unit) -> Unit, onClose: () -> Unit, onOpenElsewhere: () -> Unit) {
    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        var loaded by remember { mutableStateOf(false) }
        var text by remember { mutableStateOf<TextPreview?>(null) }
        LaunchedEffect(Unit) { load { text = it; loaded = true } }
        Box(Modifier.fillMaxSize().background(Color(0xFF141416)).testTag("textPreview")) {
            val shown = text
            when {
                !loaded -> CircularProgressIndicator(Modifier.align(Alignment.Center).size(28.dp), color = Color.White, strokeWidth = 2.dp)
                shown == null -> Column(Modifier.align(Alignment.Center).padding(24.dp), horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text("$name doesn't look like text, so it can't be shown here.", color = Color.White, textAlign = TextAlign.Center)
                    OutlinedButton(onClick = onOpenElsewhere) { Text("Open in another app", color = Color.White) }
                }
                else -> Column(Modifier.fillMaxSize().statusBarsPadding().padding(top = 56.dp).navigationBarsPadding()) {
                    SelectionContainer(Modifier.weight(1f).verticalScroll(rememberScrollState()).horizontalScroll(rememberScrollState())) {
                        Text(shown.text, Modifier.padding(16.dp), color = Color(0xFFE9E9EE), fontFamily = FontFamily.Monospace, fontSize = 13.sp,
                            lineHeight = 19.sp)
                    }
                    if (shown.truncated) Text("Showing the first 256 KB. Open it in another app to see the rest.", Modifier.padding(12.dp),
                        color = Color.White.copy(alpha = .75f), style = MaterialTheme.typography.labelSmall)
                }
            }
            ViewerBar(name, onClose) { TextButton(onClick = onOpenElsewhere, colors = ViewerButton) { Text("Open in another app") } }
        }
    }
}
