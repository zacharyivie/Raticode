package dev.raticode.mobile.open

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.OpenableColumns
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.annotation.RequiresApi
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconToggleButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.data.OpenedFiles
import dev.raticode.mobile.live.FileExplorer
import dev.raticode.mobile.live.FileKinds
import dev.raticode.mobile.live.FileViewer
import dev.raticode.mobile.ui.CodeLines
import dev.raticode.mobile.ui.Header
import dev.raticode.mobile.ui.PdfView
import dev.raticode.mobile.ui.PictureView
import dev.raticode.mobile.ui.ProblemCard
import dev.raticode.mobile.ui.RaticodeTheme
import dev.raticode.mobile.ui.TextContent
import dev.raticode.mobile.ui.ThreadIcons
import dev.raticode.mobile.ui.VideoPlayerDialog
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Shows a file another app opened with Raticode: a web page, PDF, picture, SVG, video, sound, Markdown, text or code
 * ([OpenTypes.kinds]). It runs in that app's task, where Android's one-time read grant for the file's `content://` address
 * holds, and reads only that file. Raticode keeps no copy, beyond the temporary one a PDF needs while it is shown.
 * When Raticode is the phone's default app for a type, Open on a file Rem or the computer sent lands here too, with the
 * copy Raticode just handed out ([OpenedFiles.handedOut]); every other file of Raticode's own is refused. Such a copy is
 * Raticode's own data: the app lock counts this screen while it shows one ([ownFile]), and the copy is refused, and the
 * bytes dropped, whenever the app lock or child lock is on, checked on reading and every time the screen comes back.
 *
 * What it shows is untrusted. A web page or SVG is drawn with JavaScript, network loads and file access off (WebPreview),
 * the same as a web page from your computer's files. The screen has no Ask Rem and nothing here reaches Rem, a
 * thread, the computer or the browser's tabs.
 */
class OpenFileActivity : ComponentActivity() {
    /** Showing a copy Raticode handed out, not another app's file. Set before the screen starts, so the app lock counts it. */
    var ownFile = false
        private set
    private var locked by mutableStateOf(false)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val uri = intent?.takeIf { it.action == Intent.ACTION_VIEW }?.data
        if (Build.VERSION.SDK_INT < 29 || uri == null) { finish(); return }
        ownFile = OpenedFiles.handedOut.contains(uri.toString())
        locked = raticodeLocked()
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
        )
        val type = intent.type
        setContent { RaticodeTheme { OpenedFileScreen(uri, type, ownFile, locked, onClose = ::finish) } }
    }

    override fun onResume() {
        super.onResume()
        locked = raticodeLocked()
    }

    private fun raticodeLocked() =
        dev.raticode.mobile.applock.AppLockRuntime.locked(this) || dev.raticode.mobile.childlock.ChildLockRuntime.locked(this)
}

/** What the screen has read: the file, or why it can't show it. */
internal sealed interface OpenedFile {
    data object Loading : OpenedFile
    data class Problem(val name: String, val message: String) : OpenedFile
    data class Ready(val name: String, val viewer: FileViewer, val bytes: ByteArray) : OpenedFile
}

@RequiresApi(29)
private suspend fun readOpened(context: android.content.Context, uri: Uri, type: String?, ownFile: Boolean, locked: Boolean): OpenedFile = withContext(Dispatchers.IO) {
    if (!OpenTypes.readableFrom(uri.scheme, uri.authority, context.packageName, handedOut = ownFile, locked = locked)) {
        return@withContext OpenedFile.Problem("File",
            if (ownFile) LOCKED_NOTE else "Raticode only opens files shared with it by another app. Open this file from a file manager.")
    }
    val resolver = context.contentResolver
    var name = uri.lastPathSegment?.substringAfterLast('/')?.take(200).orEmpty()
    var size = -1L
    runCatching {
        resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use { cursor ->
            if (cursor.moveToFirst()) {
                cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME).takeIf { it >= 0 && !cursor.isNull(it) }?.let { name = cursor.getString(it).take(200) }
                cursor.getColumnIndex(OpenableColumns.SIZE).takeIf { it >= 0 && !cursor.isNull(it) }?.let { size = cursor.getLong(it) }
            }
        }
    }
    val shown = name.ifBlank { "File" }
    val mime = type ?: runCatching { resolver.getType(uri) }.getOrNull()
    val viewer = OpenTypes.viewer(mime, name) ?: return@withContext OpenedFile.Problem(shown, "Raticode can't show this kind of file.")
    if (size > OpenTypes.MAX_BYTES) return@withContext OpenedFile.Problem(shown, tooLarge())
    val bytes = try {
        resolver.openInputStream(uri)?.use { input -> readAtMost(input, OpenTypes.MAX_BYTES) }
    } catch (_: SecurityException) {
        return@withContext OpenedFile.Problem(shown, "Raticode wasn't allowed to read this file. Open it again from the app that has it.")
    } catch (_: Exception) {
        null
    } ?: return@withContext OpenedFile.Problem(shown, "This file couldn't be read.")
    if (bytes.size.toLong() > OpenTypes.MAX_BYTES) { bytes.fill(0); return@withContext OpenedFile.Problem(shown, tooLarge()) }
    OpenedFile.Ready(shown, viewer, bytes)
}

private const val LOCKED_NOTE = "Raticode is locked. Unlock it, then open this file again."

private fun tooLarge() = "This file is larger than the ${OpenTypes.MAX_BYTES / (1024 * 1024)} MB Raticode opens. Open it in another app."

/** Up to [limit] bytes, plus one more when the stream goes on, so a caller can tell it was too long. */
internal fun readAtMost(input: java.io.InputStream, limit: Long): ByteArray {
    val out = java.io.ByteArrayOutputStream()
    val buffer = ByteArray(64 * 1024)
    var total = 0L
    while (true) {
        val count = input.read(buffer)
        if (count < 0) break
        out.write(buffer, 0, count)
        total += count
        if (total > limit) break
    }
    return out.toByteArray()
}

@RequiresApi(29)
@Composable
private fun OpenedFileScreen(uri: Uri, type: String?, ownFile: Boolean, locked: Boolean, onClose: () -> Unit) {
    val context = androidx.compose.ui.platform.LocalContext.current
    var opened by remember { mutableStateOf<OpenedFile>(OpenedFile.Loading) }
    // Raticode's own copy is read again once unlocked, and dropped the moment a lock is found on.
    LaunchedEffect(uri, locked) {
        (opened as? OpenedFile.Ready)?.takeIf { ownFile && locked }?.bytes?.fill(0)
        opened = if (ownFile && locked) OpenedFile.Problem("File", LOCKED_NOTE) else readOpened(context, uri, type, ownFile, locked)
    }
    val current = opened
    // The viewers clear the bytes they're given once used, so each gets its own copy.
    val load: ((ByteArray?) -> Unit) -> Unit = { done -> done((current as? OpenedFile.Ready)?.bytes?.copyOf()) }
    if (current is OpenedFile.Ready && (current.viewer == FileViewer.VIDEO || current.viewer == FileViewer.AUDIO)) {
        VideoPlayerDialog(current.name, null, load = load, onClose = onClose, onOpenElsewhere = null, audio = current.viewer == FileViewer.AUDIO)
        return
    }
    var preview by remember { mutableStateOf(true) }
    var wrap by remember { mutableStateOf(true) }
    Surface(Modifier.fillMaxSize().testTag("openedFile")) {
        Column(Modifier.fillMaxSize().statusBarsPadding()) {
            val name = when (current) { is OpenedFile.Ready -> current.name; is OpenedFile.Problem -> current.name; else -> "" }
            val text = current is OpenedFile.Ready && FileExplorer.readsText(current.viewer)
            Header(name, if (ownFile) "Opened from Raticode. Stays on this phone" else "Opened from another app. Stays on this phone", onClose, backLabel = "Close") {
                if (text && (current as OpenedFile.Ready).viewer != FileViewer.TEXT) IconButton(onClick = { preview = !preview }) {
                    Icon(if (preview) ThreadIcons.Code else ThreadIcons.Eye, if (preview) "Show source" else "Show preview")
                }
                if (text && ((current as OpenedFile.Ready).viewer == FileViewer.TEXT || !preview)) IconToggleButton(wrap, { wrap = it }) {
                    Icon(ThreadIcons.WrapText, "Wrap long lines", tint = if (wrap) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            // Drawn edge to edge: keep the file's last lines above the navigation bar.
            Box(Modifier.weight(1f).fillMaxWidth().navigationBarsPadding()) {
                when (current) {
                    OpenedFile.Loading -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator(Modifier.size(28.dp), strokeWidth = 2.dp) }
                    is OpenedFile.Problem -> Box(Modifier.fillMaxSize().padding(24.dp).testTag("openedFileProblem"), contentAlignment = Alignment.Center) {
                        ProblemCard(current.message, null)
                    }
                    is OpenedFile.Ready -> when (current.viewer) {
                        FileViewer.IMAGE -> PictureView(load, current.name, onClose)
                        FileViewer.PDF -> PdfView(load, current.name, onClose)
                        else -> OpenedText(current, preview, wrap)
                    }
                }
            }
        }
    }
}

/** Text, code, Markdown, HTML and SVG. A file that turns out not to be text says so. No Ask Rem: the text stays here. */
@Composable
private fun OpenedText(file: OpenedFile.Ready, preview: Boolean, wrap: Boolean) {
    val shown = remember(file) { FileKinds.previewText(file.bytes, OpenTypes.MAX_TEXT_BYTES) }
    val lines = remember(shown) { shown?.text?.let(CodeLines::split).orEmpty() }
    if (shown == null) {
        Box(Modifier.fillMaxSize().padding(24.dp), contentAlignment = Alignment.Center) { ProblemCard("${file.name} isn't text, so Raticode can't show it.", null) }
        return
    }
    Column(Modifier.fillMaxSize()) {
        if (shown.truncated) Text("Showing the first ${OpenTypes.MAX_TEXT_BYTES / (1024 * 1024)} MB.", Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
            style = MaterialTheme.typography.bodySmall)
        Box(Modifier.weight(1f)) { TextContent(shown.text, lines, file.name, file.viewer, preview, wrap, "", null, onAskRem = null) }
    }
}
