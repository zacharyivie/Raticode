package dev.raticode.mobile.ui

import android.graphics.SurfaceTexture
import android.media.MediaPlayer
import android.view.Surface
import android.view.TextureView
import androidx.annotation.RequiresApi
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import dev.raticode.mobile.live.BytesSource
import dev.raticode.mobile.live.ChatMedia
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.live.MediaKind
import dev.raticode.mobile.live.MediaPreview
import dev.raticode.mobile.live.RemFile
import kotlinx.coroutines.delay

/**
 * A picture or video Rem sent: it downloads over the paired link by itself, shows inline, and opens full
 * screen when tapped. [caption] is the file's usual row with Download, Save and Open.
 */
@RequiresApi(29)
@Composable
internal fun RemMediaFile(client: LiveClient, file: RemFile, kind: MediaKind, onOpen: (String, String) -> Unit, caption: @Composable () -> Unit) {
    val local = client.files.firstOrNull { it.id == file.id }
    val state = local?.state
    val preview = client.mediaPreviews[file.id]
    LaunchedEffect(file.id, state, client.connected) { if (state == "offered" && client.connected) client.autoDownload(file.id) }
    LaunchedEffect(file.id, state, preview == null) { if (state == "available" && preview == null) client.requestMediaPreview(file.id) }
    var viewing by remember(file.id) { mutableStateOf(false) }
    val poster = (preview as? MediaPreview.Ready)?.poster
    MediaTile(file.name, kind, state, if (local != null && local.size > 0) (local.bytes * 100 / local.size).toInt() else 0,
        client.connected, preview, onView = { viewing = true }, caption = caption)
    if (!viewing) return
    when (kind) {
        MediaKind.IMAGE -> {
            var full by remember(file.id) { mutableStateOf<ImageBitmap?>(null) }
            var failed by remember(file.id) { mutableStateOf(false) }
            LaunchedEffect(file.id) { client.openImage(file.id) { bitmap -> if (bitmap == null) failed = true else full = bitmap.asImageBitmap() } }
            val shown = full ?: remember(poster) { poster?.asImageBitmap() }
            MediaImageViewer(shown, file.name, loading = full == null && !failed,
                note = if (failed && shown == null) "This picture couldn't be read on this phone." else null, onClose = { viewing = false })
        }
        MediaKind.VIDEO -> VideoPlayerDialog(file.name, remember(poster) { poster?.asImageBitmap() }, load = { done -> client.readMedia(file.id, done) },
            onClose = { viewing = false }, onOpenElsewhere = { viewing = false; onOpen(file.id, file.name) })
    }
}

/** The inline preview. Stateless so it can be tested alone; [state] is the download's state on this phone. */
@Composable
internal fun MediaTile(name: String, kind: MediaKind, state: String?, percent: Int, connected: Boolean, preview: MediaPreview?,
                       onView: () -> Unit, caption: @Composable () -> Unit = {}) {
    val ready = preview as? MediaPreview.Ready
    Column(Modifier.widthIn(max = 360.dp).fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        val shape = RoundedCornerShape(10.dp)
        val poster = remember(ready?.poster) { ready?.poster?.asImageBitmap() }
        val aspect = poster?.let { (it.width.toFloat() / it.height).coerceIn(.75f, 1.9f) } ?: (16f / 10f)
        val label = when {
            ready != null && kind == MediaKind.VIDEO -> "Video: $name" + if (ready.durationMs > 0) ", ${ChatMedia.duration(ready.durationMs)}" else ""
            ready != null -> "Picture: $name"
            else -> "$name, ${tileNote(kind, state, percent, connected, preview)}"
        }
        val busy = preview == MediaPreview.Loading || state == "receiving" || (state == "offered" && connected) ||
            (state == "available" && preview == null)
        // A preview keeps its own shape; a note about a missing or unreadable file stays short.
        val size = when { ready != null -> Modifier.aspectRatio(aspect); busy -> Modifier.height(150.dp); else -> Modifier.heightIn(min = 72.dp) }
        Box(Modifier.fillMaxWidth().then(size).clip(shape).background(MaterialTheme.colorScheme.surfaceVariant)
            .then(if (ready != null) Modifier.clickable(onClickLabel = if (kind == MediaKind.VIDEO) "Play video" else "View full size",
                role = Role.Button, onClick = onView) else Modifier)
            .semantics { contentDescription = label }.testTag("remMedia"), contentAlignment = Alignment.Center) {
            when {
                ready != null -> {
                    if (poster != null) Image(poster, null, Modifier.fillMaxSize(), contentScale = ContentScale.Fit)
                    else Box(Modifier.fillMaxSize().background(Color.Black))
                    if (kind == MediaKind.VIDEO) {
                        Box(Modifier.size(52.dp).background(Color.Black.copy(alpha = .62f), CircleShape).testTag("remMediaPlay"), contentAlignment = Alignment.Center) {
                            Icon(ThreadIcons.Play, null, Modifier.size(24.dp).padding(start = 2.dp), tint = Color.White)
                        }
                        if (ready.durationMs > 0) Text(ChatMedia.duration(ready.durationMs), Modifier.align(Alignment.BottomEnd).padding(8.dp)
                            .background(Color.Black.copy(alpha = .62f), RoundedCornerShape(4.dp)).padding(horizontal = 6.dp, vertical = 2.dp),
                            style = MaterialTheme.typography.labelSmall, color = Color.White)
                    }
                }
                else -> Column(Modifier.padding(16.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (busy) CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp)
                    else Icon(if (kind == MediaKind.VIDEO) ThreadIcons.Video else ThreadIcons.Image, null, Modifier.size(22.dp),
                        tint = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(tileNote(kind, state, percent, connected, preview), style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center)
                }
            }
        }
        caption()
    }
}

/** What the tile says before its preview is ready, worded for each download state. */
internal fun tileNote(kind: MediaKind, state: String?, percent: Int, connected: Boolean, preview: MediaPreview?): String {
    val noun = if (kind == MediaKind.VIDEO) "video" else "picture"
    return when {
        preview == MediaPreview.Failed -> "This phone can't preview this $noun. Open it in another app instead."
        state == "receiving" -> "Downloading $percent%"
        state == "offered" && connected -> "Downloading"
        state == "offered" -> "Connect to your desktop to load this $noun"
        state == "available" -> "Loading $noun"
        else -> "This $noun is no longer available. Ask Rem to send it again."
    }
}

/** Full-screen picture with pinch and double-tap zoom. Back or Close returns to the chat. */
@Composable
internal fun MediaImageViewer(bitmap: ImageBitmap?, label: String, loading: Boolean = false, note: String? = null, onClose: () -> Unit,
                              actions: @Composable RowScope.() -> Unit = {}) {
    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        var scale by remember { mutableFloatStateOf(1f) }
        var offset by remember { mutableStateOf(Offset.Zero) }
        Box(Modifier.fillMaxSize().background(Color.Black).testTag("mediaViewer")) {
            if (bitmap != null) Image(bitmap, label, Modifier.fillMaxSize()
                .pointerInput(Unit) {
                    detectTransformGestures { _, pan, zoom, _ ->
                        scale = (scale * zoom).coerceIn(1f, 5f)
                        offset = if (scale == 1f) Offset.Zero else offset + pan
                    }
                }
                .pointerInput(Unit) { detectTapGestures(onDoubleTap = { if (scale > 1f) { scale = 1f; offset = Offset.Zero } else scale = 2.5f }) }
                .graphicsLayer { scaleX = scale; scaleY = scale; translationX = offset.x; translationY = offset.y },
                contentScale = ContentScale.Fit)
            if (loading) CircularProgressIndicator(Modifier.align(Alignment.Center).size(28.dp), color = Color.White, strokeWidth = 2.dp)
            note?.let { Text(it, Modifier.align(Alignment.Center).padding(24.dp), color = Color.White, textAlign = TextAlign.Center) }
            ViewerBar(label, onClose, actions)
        }
    }
}

/** Buttons on the full-screen viewers' dark bar, readable on black. */
internal val ViewerButton @Composable get() = ButtonDefaults.textButtonColors(contentColor = Color.White)

@Composable
internal fun BoxScope.ViewerBar(label: String, onClose: () -> Unit, actions: @Composable RowScope.() -> Unit) {
    Row(Modifier.align(Alignment.TopStart).fillMaxWidth().background(Color.Black.copy(alpha = .5f)).statusBarsPadding()
        .padding(horizontal = 4.dp, vertical = 2.dp), verticalAlignment = Alignment.CenterVertically) {
        IconButton(onClick = onClose) { Icon(ThreadIcons.Close, "Close", tint = Color.White) }
        Text(label, Modifier.weight(1f), color = Color.White, style = MaterialTheme.typography.titleSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
        CompositionLocalProvider(LocalContentColor provides Color.White) { actions() }
    }
}

/**
 * A full-screen player for a downloaded video. It plays the verified bytes from memory and zeroes them when
 * closed. When this phone can't play the format, it offers another app instead.
 */
@Composable
internal fun VideoPlayerDialog(label: String, poster: ImageBitmap?, load: ((ByteArray?) -> Unit) -> Unit, onClose: () -> Unit,
                               onOpenElsewhere: (() -> Unit)?, audio: Boolean = false) {
    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        var data by remember { mutableStateOf<ByteArray?>(null) }
        var problem by remember { mutableStateOf<String?>(null) }
        DisposableEffect(Unit) {
            var open = true
            load { bytes -> if (!open) bytes?.fill(0) else if (bytes == null) problem = "This video couldn't be read on this phone." else data = bytes }
            onDispose { open = false }
        }
        Box(Modifier.fillMaxSize().background(Color.Black).testTag("videoPlayer")) {
            val bytes = data
            if (bytes != null && problem == null) VideoSurface(bytes, label, if (audio) "sound file" else "video") { problem = it }
            // Sound has no picture: show what is playing instead of a black frame.
            if (audio && problem == null) Column(Modifier.align(Alignment.Center).padding(24.dp), horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Icon(ThreadIcons.Music, null, Modifier.size(72.dp), tint = Color.White.copy(alpha = .8f))
                Text(label, color = Color.White, textAlign = TextAlign.Center, style = MaterialTheme.typography.titleMedium)
            }
            else if (problem == null) {
                if (poster != null) Image(poster, null, Modifier.fillMaxSize(), contentScale = ContentScale.Fit)
                CircularProgressIndicator(Modifier.align(Alignment.Center).size(28.dp), color = Color.White, strokeWidth = 2.dp)
            }
            problem?.let { message ->
                Column(Modifier.align(Alignment.Center).padding(24.dp), horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text(message, color = Color.White, textAlign = TextAlign.Center)
                    if (onOpenElsewhere != null) OutlinedButton(onClick = onOpenElsewhere) { Text("Open in another app", color = Color.White) }
                }
            }
            ViewerBar(label, onClose) {}
        }
    }
}

@Composable
private fun BoxScope.VideoSurface(data: ByteArray, label: String, noun: String, onProblem: (String) -> Unit) {
    val player = remember(data) { MediaPlayer() }
    var playing by remember(data) { mutableStateOf(false) }
    var prepared by remember(data) { mutableStateOf(false) }
    var duration by remember(data) { mutableIntStateOf(0) }
    var position by remember(data) { mutableIntStateOf(0) }
    var seeking by remember(data) { mutableStateOf<Float?>(null) }
    var aspect by remember(data) { mutableFloatStateOf(16f / 9f) }
    val failed by rememberUpdatedState(onProblem)
    DisposableEffect(data) {
        player.setOnPreparedListener { duration = it.duration.coerceAtLeast(0); prepared = true; it.start(); playing = true }
        player.setOnVideoSizeChangedListener { _, w, h -> if (w > 0 && h > 0) aspect = w.toFloat() / h }
        player.setOnCompletionListener { playing = false; position = duration }
        player.setOnErrorListener { _, _, _ -> playing = false; failed("This phone can't play this $noun."); true }
        runCatching { player.setDataSource(BytesSource(data)); player.prepareAsync() }.onFailure { failed("This phone can't play this $noun.") }
        onDispose { player.release(); data.fill(0) }
    }
    // Leaving the app pauses playback, as other players do.
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    DisposableEffect(lifecycle, player) {
        val observer = LifecycleEventObserver { _, event -> if (event == Lifecycle.Event.ON_PAUSE && playing) { runCatching { player.pause() }; playing = false } }
        lifecycle.addObserver(observer)
        onDispose { lifecycle.removeObserver(observer) }
    }
    LaunchedEffect(playing) { while (playing) { runCatching { position = player.currentPosition }; delay(250) } }
    fun toggle() {
        if (!prepared) return
        runCatching { if (playing) player.pause() else { if (position >= duration && duration > 0) player.seekTo(0); player.start() } }
        playing = !playing
    }
    AndroidView(factory = { context -> playerView(context, player) }, Modifier.align(Alignment.Center).fillMaxWidth().aspectRatio(aspect)
        .clickable(onClickLabel = if (playing) "Pause" else "Play") { toggle() }.semantics { contentDescription = label })
    Column(Modifier.align(Alignment.BottomCenter).fillMaxWidth().background(Color.Black.copy(alpha = .5f)).navigationBarsPadding()
        .padding(horizontal = 8.dp, vertical = 4.dp)) {
        Slider(value = seeking ?: position.toFloat(), onValueChange = { seeking = it }, enabled = prepared && duration > 0,
            onValueChangeFinished = { seeking?.let { runCatching { player.seekTo(it.toInt()) }; position = it.toInt() }; seeking = null },
            valueRange = 0f..duration.coerceAtLeast(1).toFloat(), modifier = Modifier.testTag("videoSeek")
                .semantics { stateDescription = "${ChatMedia.duration(position.toLong())} of ${ChatMedia.duration(duration.toLong())}" },
            colors = SliderDefaults.colors(thumbColor = Color.White, activeTrackColor = Color.White, inactiveTrackColor = Color.White.copy(alpha = .3f)))
        Row(verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = { toggle() }, enabled = prepared, modifier = Modifier.testTag("videoToggle")) {
                Icon(if (playing) ThreadIcons.Pause else ThreadIcons.Play, if (playing) "Pause" else "Play", tint = Color.White)
            }
            Text("${ChatMedia.duration((seeking?.toLong() ?: position.toLong()))} / ${ChatMedia.duration(duration.toLong())}",
                color = Color.White, style = MaterialTheme.typography.labelMedium)
        }
    }
}

/** The player's picture. Its Surface is released when the view's texture goes away. */
@android.annotation.SuppressLint("Recycle")
private fun playerView(context: android.content.Context, player: MediaPlayer) = TextureView(context).apply {
    surfaceTextureListener = object : TextureView.SurfaceTextureListener {
        private var surface: Surface? = null
        override fun onSurfaceTextureAvailable(texture: SurfaceTexture, width: Int, height: Int) {
            surface = Surface(texture).also { runCatching { player.setSurface(it) } }
        }
        override fun onSurfaceTextureSizeChanged(texture: SurfaceTexture, width: Int, height: Int) {}
        override fun onSurfaceTextureDestroyed(texture: SurfaceTexture): Boolean {
            runCatching { player.setSurface(null) }; surface?.release(); surface = null; return true
        }
        override fun onSurfaceTextureUpdated(texture: SurfaceTexture) {}
    }
}
