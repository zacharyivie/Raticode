package dev.raticode.mobile.ui

import android.annotation.SuppressLint
import android.graphics.Bitmap
import android.graphics.ImageDecoder
import android.graphics.drawable.AnimatedImageDrawable
import android.graphics.drawable.Drawable
import android.graphics.pdf.PdfRenderer
import android.os.Build
import android.os.ParcelFileDescriptor
import android.webkit.RenderProcessGoneDetail
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.ImageView
import androidx.annotation.RequiresApi
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.DisableSelection
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import dev.raticode.mobile.live.CodeHighlight
import dev.raticode.mobile.live.FileExplorer
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Viewers for files opened from the file explorer, matching what the desktop opens each type with: code and text with
 * line numbers and highlighting, Markdown rendered, HTML and SVG drawn in a locked-down web view, PDF pages, pictures
 * (animated ones move) and the in-app player for video and audio. Everything works from bytes or text already on the
 * phone; nothing here contacts the network.
 */

/** Lines of a text file and where the search matches are. Plain so it can be tested alone. */
internal object CodeLines {
    /** Lines highlighted together, so a block comment that spans lines keeps its color within a block. */
    const val BLOCK = 60

    fun split(text: String): List<String> = text.removeSuffix("\n").split('\n').map { it.removeSuffix("\r") }

    /** Line indexes holding [query], ignoring case. */
    fun matches(lines: List<String>, query: String): List<Int> {
        if (query.isBlank()) return emptyList()
        return lines.indices.filter { lines[it].contains(query, ignoreCase = true) }
    }

    /**
     * The 1-based lines [selected] came from, or null when the phone can't tell which they are. Selected lines arrive
     * joined by line breaks, with blank lines left out. [near] is the line that was long-pressed to start the selection,
     * which settles a word that appears on several lines; without it such a selection names no lines at all.
     */
    fun selectedLines(lines: List<String>, selected: String, near: Int? = null): IntRange? {
        val parts = selected.split('\n').map { it.removeSuffix("\r") }.filter { it.isNotEmpty() }
        if (parts.isEmpty()) return null
        val found = lines.indices.mapNotNull { start -> lastLineOf(lines, start, parts)?.let { start + 1..it + 1 } }
        return found.singleOrNull() ?: near?.let { line -> found.firstOrNull { line in it } }
    }

    /** The index of the last line of [parts] if they start at line [start], or null. */
    private fun lastLineOf(lines: List<String>, start: Int, parts: List<String>): Int? {
        if (parts.size == 1) return start.takeIf { lines[start].contains(parts[0]) }
        if (!lines[start].endsWith(parts[0])) return null
        var at = start
        for (index in 1..parts.lastIndex) {
            at++
            while (at < lines.size && lines[at].isEmpty()) at++
            if (at == lines.size) return null
            if (if (index == parts.lastIndex) !lines[at].startsWith(parts[index]) else lines[at] != parts[index]) return null
        }
        return at
    }

    /** Token kinds for each line of block [block], from the shared chat tokenizer. */
    fun highlight(lines: List<String>, block: Int, language: String?): List<List<CodeHighlight.Token>> {
        val from = block * BLOCK
        val chunk = lines.subList(from, minOf(lines.size, from + BLOCK))
        if (language == null || !CodeHighlight.supports(language)) return chunk.map { listOf(CodeHighlight.Token(CodeHighlight.Kind.TEXT, it)) }
        val tokens = CodeHighlight.tokenize(chunk.joinToString("\n"), language)
        val result = MutableList(chunk.size) { mutableListOf<CodeHighlight.Token>() }
        var line = 0
        for (token in tokens) {
            val pieces = token.text.split('\n')
            pieces.forEachIndexed { index, piece ->
                if (index > 0) line++
                if (piece.isNotEmpty() && line < result.size) result[line] += CodeHighlight.Token(token.kind, piece)
            }
        }
        return result
    }
}

/**
 * A text or code file with line numbers. Long lines wrap by default, which reads best on a phone; with wrapping off
 * the whole file scrolls sideways. [query] marks matches and [current] is the match line to scroll to. [onAskRem] adds
 * Ask Rem to the selection menu, and takes the selected text and its 1-based lines (null when they can't be told).
 */
@Composable
internal fun CodeView(lines: List<String>, language: String?, wrap: Boolean, modifier: Modifier = Modifier, query: String = "", current: Int? = null,
                      onAskRem: ((String, IntRange?) -> Unit)? = null) {
    val styles = codeTokenStyles()
    val mark = MaterialTheme.colorScheme.tertiaryContainer
    val state = rememberLazyListState()
    LaunchedEffect(current) { current?.let { state.animateScrollToItem(it.coerceAtLeast(0)) } }
    val blocks = remember(lines, language) { mutableMapOf<Int, List<List<CodeHighlight.Token>>>() }
    val digits = lines.size.toString().length
    // The line a selection was started on, 1-based; 0 before any.
    var pressed by remember(lines) { mutableIntStateOf(0) }
    val style = TextStyle(fontFamily = FontFamily.Monospace, fontSize = 12.5.sp, lineHeight = 18.sp, color = MaterialTheme.colorScheme.onSurface)
    val measurer = rememberTextMeasurer()
    val density = LocalDensity.current
    val charWidth = remember(measurer) { measurer.measure("0000000000", style).size.width / 10f }
    val longest = remember(lines) { lines.maxOfOrNull { it.length } ?: 0 }
    val gutter = with(density) { (charWidth * digits).toDp() } + 20.dp
    val body = @Composable { inner: Modifier ->
        LazyColumn(inner.then(if (onAskRem != null) Modifier.pointerInput(state) { watchLongPress(state) { pressed = it + 1 } } else Modifier).testTag("codeView"), state = state) {
            itemsIndexed(lines) { index, line ->
                val tokens = blocks.getOrPut(index / CodeLines.BLOCK) { CodeLines.highlight(lines, index / CodeLines.BLOCK, language) }[index % CodeLines.BLOCK]
                Row(Modifier.fillMaxWidth().background(if (index == current) mark.copy(alpha = .55f) else Color.Transparent)) {
                    // Line numbers aren't part of the file, so a selection leaves them out.
                    DisableSelection {
                        Text((index + 1).toString().padStart(digits), Modifier.width(gutter).padding(start = 8.dp, end = 12.dp),
                            style = style.copy(color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = .7f)), softWrap = false)
                    }
                    Text(codeLine(tokens, styles, query, mark), Modifier.padding(end = 12.dp), style = style, softWrap = wrap)
                }
            }
        }
    }
    AskRemSelection(modifier, onAskRem?.let { ask -> { text -> ask(text, CodeLines.selectedLines(lines, text, pressed.takeIf { it > 0 })) } }) {
        if (wrap) body(Modifier.fillMaxSize())
        else {
            // Monospace makes the widest line's width known without measuring every line.
            val width = with(density) { (charWidth * (longest.coerceAtMost(2000) + 2)).toDp() } + gutter + 12.dp
            BoxWithConstraints(Modifier.fillMaxSize().horizontalScroll(rememberScrollState())) {
                body(Modifier.width(maxOf(width, maxWidth)).fillMaxHeight())
            }
        }
    }
}

private fun codeLine(tokens: List<CodeHighlight.Token>, styles: Map<CodeHighlight.Kind, SpanStyle?>, query: String, mark: Color): AnnotatedString {
    val text = buildAnnotatedString {
        for (token in tokens) {
            val span = styles[token.kind]
            if (span == null) append(token.text) else withStyle(span) { append(token.text) }
        }
    }
    if (query.isBlank()) return text
    return buildAnnotatedString {
        append(text)
        var at = text.text.indexOf(query, ignoreCase = true)
        while (at >= 0) {
            addStyle(SpanStyle(background = mark), at, at + query.length)
            at = text.text.indexOf(query, at + query.length, ignoreCase = true)
        }
    }
}

/** Markdown rendered the way Rem's chat renders it, in a scrolling page. */
@Composable
internal fun MarkdownFileView(text: String, modifier: Modifier = Modifier, onAskRem: ((String) -> Unit)? = null) {
    AskRemSelection(modifier.fillMaxSize(), onAskRem) {
        Column(Modifier.verticalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 12.dp).testTag("markdownView")) {
            ChatMarkdown(text)
        }
    }
}

/**
 * An SVG fitted to the screen. The markup is placed in the page itself: the preview runs no JavaScript, so a script
 * or event handler in the file never runs, and anything it links to is blocked like any other request.
 */
internal fun svgPage(svg: String): String {
    val markup = svg.replace(Regex("""<\?xml[^>]*\?>"""), "").replace(Regex("""<!DOCTYPE[^>\[]*(\[[^]]*])?\s*>""", RegexOption.IGNORE_CASE), "")
    return "<!doctype html><html><head><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><style>" +
        "body{margin:0;padding:16px;background:#fff}svg{display:block;margin:0 auto;max-width:100%;height:auto}" +
        "</style></head><body>$markup</body></html>"
}

/**
 * HTML drawn in a web view with JavaScript, file and content access off and network loads blocked, so a page can't
 * run code, reach the internet or read anything on the phone. Links don't navigate. Pages that need their scripts,
 * styles or pictures from other files show without them.
 */
@SuppressLint("SetJavaScriptEnabled")
@Composable
internal fun WebPreview(html: String, label: String, modifier: Modifier = Modifier) {
    // A preview whose page process ended, such as a file built to crash it, shows a message instead of ending Raticode.
    var ended by remember { mutableStateOf(false) }
    if (ended) {
        Box(modifier.fillMaxSize().testTag("webPreviewEnded"), contentAlignment = Alignment.Center) {
            ProblemCard("This preview stopped. Open the file again to try once more.", null)
        }
        return
    }
    AndroidView(factory = { context ->
        WebView(context).apply {
            settings.javaScriptEnabled = false
            settings.allowFileAccess = false
            settings.allowContentAccess = false
            // Network loads are blocked here and every request the page makes is answered empty below.
            settings.blockNetworkLoads = true
            settings.setGeolocationEnabled(false)
            settings.builtInZoomControls = true
            settings.displayZoomControls = false
            settings.loadWithOverviewMode = true
            settings.useWideViewPort = true
            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest) = true
                // Anything the page asks for besides itself gets nothing back.
                override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? =
                    if (request.url.scheme == "data") null else WebResourceResponse("text/plain", "utf-8", java.io.ByteArrayInputStream(ByteArray(0)))
                // Without this, Android ends the whole app when the preview's page process crashes or is reclaimed.
                override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
                    ended = true
                    return true
                }
            }
            contentDescription = label
        }
    // Base64, so characters such as # and % in the page reach the web view unchanged.
    }, update = { it.loadData(java.util.Base64.getEncoder().encodeToString(html.toByteArray()), "text/html; charset=utf-8", "base64") },
        onRelease = { it.stopLoading(); it.destroy() }, modifier = modifier.fillMaxSize().testTag("webPreview"))
}

/** A picture with pinch and double-tap zoom. Animated GIF and WebP pictures play. */
@RequiresApi(29)
@Composable
internal fun PictureView(load: ((ByteArray?) -> Unit) -> Unit, label: String, onProblem: () -> Unit, modifier: Modifier = Modifier) {
    var drawable by remember { mutableStateOf<Drawable?>(null) }
    var failed by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) {
        load { bytes ->
            if (bytes == null) { failed = true; return@load }
            // Decoding big pictures takes a moment; keep the screen responsive.
            Thread({
                val decoded = try { runCatching { decodePicture(bytes) }.getOrNull() } finally { bytes.fill(0) }
                android.os.Handler(android.os.Looper.getMainLooper()).post { if (decoded == null) failed = true else drawable = decoded }
            }, "file-picture").apply { isDaemon = true; start() }
        }
    }
    DisposableEffect(drawable) { val d = drawable; (d as? AnimatedImageDrawable)?.start(); onDispose { (d as? AnimatedImageDrawable)?.stop() } }
    var scale by remember { mutableFloatStateOf(1f) }
    var offset by remember { mutableStateOf(Offset.Zero) }
    Box(modifier.fillMaxSize().background(Color.Black).testTag("pictureView"), contentAlignment = Alignment.Center) {
        val shown = drawable
        when {
            failed -> ProblemCard("This phone can't show this picture.", onProblem, dark = true)
            shown == null -> CircularProgressIndicator(Modifier.size(28.dp), color = Color.White, strokeWidth = 2.dp)
            else -> AndroidView(factory = { ImageView(it).apply { scaleType = ImageView.ScaleType.FIT_CENTER; adjustViewBounds = true } },
                update = { it.setImageDrawable(shown); it.contentDescription = label },
                modifier = Modifier.fillMaxSize()
                    .pointerInput(Unit) {
                        detectTransformGestures { _, pan, zoom, _ ->
                            scale = (scale * zoom).coerceIn(1f, 6f)
                            offset = if (scale == 1f) Offset.Zero else offset + pan
                        }
                    }
                    .pointerInput(Unit) { detectTapGestures(onDoubleTap = { if (scale > 1f) { scale = 1f; offset = Offset.Zero } else scale = 2.5f }) }
                    .graphicsLayer { scaleX = scale; scaleY = scale; translationX = offset.x; translationY = offset.y }
                    .semantics { contentDescription = label })
        }
    }
}

/** Decodes with Android's own decoder, at most about 2,048 pixels on the longer side. HEIC and AVIF need a phone that reads them. */
@RequiresApi(29)
private fun decodePicture(bytes: ByteArray): Drawable {
    val source = ImageDecoder.createSource(java.nio.ByteBuffer.wrap(bytes.copyOf()))
    return ImageDecoder.decodeDrawable(source) { decoder, info, _ ->
        val side = maxOf(info.size.width, info.size.height)
        var sample = 1
        while (side / sample > 2048) sample *= 2
        decoder.setTargetSampleSize(sample)
        decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
    }
}

/** What a PDF view is showing. */
private sealed interface PdfState {
    data object Loading : PdfState
    data class Ready(val renderer: PdfRenderer, val descriptor: ParcelFileDescriptor) : PdfState
    data class Failed(val message: String) : PdfState
}

/**
 * PDF pages drawn with Android's PDF renderer, one after another; tapping a page shows it full screen to zoom. The
 * renderer needs a file, so the bytes go into memory-backed storage on Android 11 and newer, or a private file that is
 * deleted as soon as it is open on Android 10.
 */
@RequiresApi(29)
@Composable
internal fun PdfView(load: ((ByteArray?) -> Unit) -> Unit, label: String, onProblem: () -> Unit, modifier: Modifier = Modifier) {
    val context = androidx.compose.ui.platform.LocalContext.current
    var state by remember { mutableStateOf<PdfState>(PdfState.Loading) }
    LaunchedEffect(Unit) {
        load { bytes ->
            if (bytes == null) { state = PdfState.Failed("This PDF couldn't be read on this phone."); return@load }
            state = try { openPdf(context.cacheDir, bytes) } catch (_: SecurityException) {
                PdfState.Failed("This PDF is protected with a password. Open it in another app.")
            } catch (_: Exception) { PdfState.Failed("This phone can't show this PDF.") } finally { bytes.fill(0) }
        }
    }
    DisposableEffect(state) {
        val open = state as? PdfState.Ready
        onDispose { open?.let { synchronized(it.renderer) { it.renderer.close() }; it.descriptor.close() } }
    }
    var zoomed by remember { mutableStateOf<Bitmap?>(null) }
    zoomed?.let { page -> MediaImageViewer(page.asImageBitmap(), label, onClose = { zoomed = null }) }
    when (val current = state) {
        PdfState.Loading -> Box(modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator(Modifier.size(28.dp), strokeWidth = 2.dp) }
        is PdfState.Failed -> Box(modifier.fillMaxSize(), contentAlignment = Alignment.Center) { ProblemCard(current.message, onProblem) }
        is PdfState.Ready -> BoxWithConstraints(modifier.fillMaxSize().background(MaterialTheme.colorScheme.surfaceVariant)) {
            val width = with(LocalDensity.current) { maxWidth.toPx() }.toInt().coerceIn(320, 1600)
            val count = remember(current) { current.renderer.pageCount }
            LazyColumn(Modifier.fillMaxSize().testTag("pdfView"), contentPadding = PaddingValues(12.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                items(count) { index ->
                    var page by remember(index) { mutableStateOf<Bitmap?>(null) }
                    LaunchedEffect(index, width) { page = withContext(Dispatchers.Default) { renderPage(current.renderer, index, width * 2) } }
                    val shown = page
                    Surface(shadowElevation = 2.dp, color = Color.White) {
                        if (shown == null) Box(Modifier.fillMaxWidth().aspectRatio(.77f))
                        else Image(shown.asImageBitmap(), "Page ${index + 1} of $count", Modifier.fillMaxWidth().clickable(onClickLabel = "Zoom") { zoomed = shown },
                            contentScale = ContentScale.FillWidth)
                    }
                }
            }
        }
    }
}

@RequiresApi(29)
private fun openPdf(cache: java.io.File, bytes: ByteArray): PdfState.Ready {
    val descriptor = if (Build.VERSION.SDK_INT >= 30) {
        val fd = android.system.Os.memfd_create("raticode-pdf", 0)
        java.io.FileOutputStream(fd).use { it.write(bytes) }
        android.system.Os.lseek(fd, 0, android.system.OsConstants.SEEK_SET)
        ParcelFileDescriptor.dup(fd).also { android.system.Os.close(fd) }
    } else {
        val folder = java.io.File(cache, "pdf-view").apply { mkdirs() }
        val file = java.io.File(folder, java.util.UUID.randomUUID().toString())
        try { file.writeBytes(bytes); ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY) } finally { file.delete() }
    }
    return try { PdfState.Ready(PdfRenderer(descriptor), descriptor) } catch (error: Exception) { descriptor.close(); throw error }
}

private fun renderPage(renderer: PdfRenderer, index: Int, width: Int): Bitmap? = synchronized(renderer) {
    runCatching {
        renderer.openPage(index).use { page ->
            val target = width.coerceAtMost(2400)
            val height = (target.toFloat() * page.height / page.width.coerceAtLeast(1)).toInt().coerceIn(1, 6000)
            androidx.core.graphics.createBitmap(target, height).apply {
                eraseColor(android.graphics.Color.WHITE)
                page.render(this, null, null, PdfRenderer.Page.RENDER_MODE_FOR_DISPLAY)
            }
        }
    }.getOrNull()
}

/** Why a file can't be shown, with a way to open it elsewhere. */
@Composable
internal fun ProblemCard(message: String, onOpenElsewhere: (() -> Unit)?, dark: Boolean = false, action: String = "Open in another app") {
    Column(Modifier.padding(24.dp).testTag("fileProblem"), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(message, color = if (dark) Color.White else MaterialTheme.colorScheme.onSurface, style = MaterialTheme.typography.bodyLarge,
            textAlign = androidx.compose.ui.text.style.TextAlign.Center)
        if (onOpenElsewhere != null) OutlinedButton(onClick = onOpenElsewhere) { Text(action, color = if (dark) Color.White else Color.Unspecified) }
    }
}

/** Whether a name is shown as code (with highlighting) rather than plain text. */
internal fun codeLanguage(name: String) = FileExplorer.language(name)

/**
 * A file the phone already holds, shown in the app because no other app on the phone opens it, such as code from a
 * tapped link. Text, Markdown, HTML, SVG, pictures, PDFs, video and sound are shown here; Save keeps a copy.
 */
@RequiresApi(29)
@Composable
internal fun InAppFileDialog(client: dev.raticode.mobile.live.LiveClient, id: String, name: String, onClose: () -> Unit, onSave: () -> Unit) {
    val viewer = FileExplorer.viewer(name)
    if (viewer == dev.raticode.mobile.live.FileViewer.VIDEO || viewer == dev.raticode.mobile.live.FileViewer.AUDIO) {
        VideoPlayerDialog(name, null, load = { done -> client.readMedia(id, done) }, onClose = onClose, onOpenElsewhere = null,
            audio = viewer == dev.raticode.mobile.live.FileViewer.AUDIO)
        return
    }
    androidx.compose.ui.window.Dialog(onDismissRequest = onClose, properties = androidx.compose.ui.window.DialogProperties(usePlatformDefaultWidth = false)) {
        var preview by remember { mutableStateOf(true) }
        var wrap by remember { mutableStateOf(true) }
        Surface(Modifier.fillMaxSize().testTag("inAppFile")) {
            Column(Modifier.fillMaxSize().statusBarsPadding()) {
                val text = FileExplorer.readsText(viewer)
                Header(name, "On this phone", onClose) {
                    if (text && viewer != dev.raticode.mobile.live.FileViewer.TEXT) IconButton(onClick = { preview = !preview }) {
                        Icon(if (preview) ThreadIcons.Code else ThreadIcons.Eye, if (preview) "Show source" else "Show preview")
                    }
                    if (text && (viewer == dev.raticode.mobile.live.FileViewer.TEXT || !preview)) IconToggleButton(wrap, { wrap = it }) {
                        Icon(ThreadIcons.WrapText, "Wrap long lines", tint = if (wrap) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    TextButton(onClick = onSave) { Text("Save") }
                }
                Box(Modifier.weight(1f).fillMaxWidth()) {
                    when (viewer) {
                        dev.raticode.mobile.live.FileViewer.IMAGE -> PictureView({ done -> client.readMedia(id, done) }, name, onSave)
                        dev.raticode.mobile.live.FileViewer.PDF -> PdfView({ done -> client.readMedia(id, done) }, name, onSave)
                        else -> {
                            var loaded by remember { mutableStateOf(false) }
                            var content by remember { mutableStateOf<String?>(null) }
                            LaunchedEffect(id) {
                                client.readMedia(id) { bytes ->
                                    content = try { bytes?.let { dev.raticode.mobile.live.FileKinds.previewText(it, it.size)?.text } } finally { bytes?.fill(0) }
                                    loaded = true
                                }
                            }
                            val shown = content
                            val lines = remember(shown) { shown?.let(CodeLines::split).orEmpty() }
                            when {
                                !loaded -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator(Modifier.size(28.dp), strokeWidth = 2.dp) }
                                shown == null -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                                    ProblemCard("$name isn't text, and no app on this phone opens it. Save it instead.", onSave, action = "Save")
                                }
                                else -> TextContent(shown, lines, name, viewer, preview, wrap, "", null)
                            }
                        }
                    }
                }
            }
        }
    }
}
