package dev.raticode.mobile.ui

import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.annotation.RequiresApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.live.*

/**
 * Browse the files of the projects open on the computer and open them on the phone, like the desktop's file
 * explorer: projects, then folders one level at a time with a breadcrumb trail, then a full-screen viewer for the
 * file. It only reads: creating, renaming, moving and deleting files stay on the computer.
 */
@RequiresApi(29)
@Composable
internal fun FileExplorerPage(client: LiveClient, onBack: (() -> Unit)?, onAskRem: ((String) -> Unit)? = null) {
    var project by rememberSaveable { mutableStateOf<String?>(null) }
    var projectName by rememberSaveable { mutableStateOf("") }
    var path by rememberSaveable { mutableStateOf("") }
    var open by rememberSaveable { mutableStateOf<String?>(null) }
    var openSize by rememberSaveable { mutableLongStateOf(-1L) }
    var openModified by rememberSaveable { mutableStateOf("") }
    var openType by rememberSaveable { mutableStateOf("file") }
    var openLocked by rememberSaveable { mutableStateOf(false) }
    var filter by rememberSaveable { mutableStateOf<String?>(null) }

    fun goTo(folder: String) { path = folder; filter = null }
    fun up() {
        when {
            open != null -> open = null
            filter != null -> filter = null
            path.isNotEmpty() -> goTo(FileExplorer.parent(path))
            project != null -> { project = null; path = "" }
        }
    }
    BackHandler(enabled = open != null || project != null || filter != null) { up() }

    val current = open
    val id = project
    if (current != null && id != null) {
        FileViewerPage(client, id, projectName, current, openSize.takeIf { it >= 0 }, openModified, openType, openLocked, onAskRem, onClose = { open = null })
        return
    }
    val key = FileExplorer.listKey(id, path)
    val params = FileExplorer.listParams(id, path)
    DisposableEffect(key) { client.openDesktop(FileExplorer.LIST, params); onDispose { client.openDesktop(null) } }
    val folder = client.desktopViews[key] as? ExplorerFolder
    Column(Modifier.fillMaxSize().testTag("fileExplorer")) {
        val title = when { id == null -> "Files"; path.isEmpty() -> projectName.ifBlank { folder?.project ?: "Project" }; else -> FileExplorer.name(path) }
        val subtitle = when {
            id == null -> if (client.connected) "Projects open on your computer" else "Offline"
            folder != null -> listOf(projectName.ifBlank { folder.project }, items(folder.entries.size + folder.more)).filter { it.isNotBlank() }.joinToString(" · ")
            else -> projectName
        }
        Header(title, subtitle, if (id != null) ({ up() }) else onBack) {
            // While filtering, the field's own close button ends it.
            if (id != null && filter == null) IconButton(onClick = { filter = "" }) { Icon(ThreadIcons.Search, "Filter this folder") }
            IconButton(onClick = client::refreshDesktop, enabled = client.connected && client.fileExplorer == true) { Icon(Icons.Filled.Refresh, "Refresh") }
        }
        if (id != null) Breadcrumbs(FileExplorer.crumbs(path, projectName.ifBlank { folder?.project ?: "Project" }), ::goTo)
        filter?.let { query -> FilterField(query, { filter = it }, onClose = { filter = null }) }
        Box(Modifier.weight(1f)) {
            ExplorerGate(client, key, if (id == null) "Reading your projects…" else "Reading this folder…") {
                Column(Modifier.fillMaxSize()) {
                    ExplorerBanners(client, key)
                    if (id == null) (client.desktopViews[key] as? ExplorerProjects)?.let { list ->
                        ProjectRows(list) { chosen -> project = chosen.id; projectName = chosen.name; goTo("") }
                    } else folder?.let { shown ->
                        FolderRows(shown, filter.orEmpty()) { entry ->
                            if (entry.folder) goTo(FileExplorer.child(path, entry.name))
                            else {
                                open = FileExplorer.child(path, entry.name); openSize = entry.size ?: -1; openModified = entry.modified
                                openType = entry.type; openLocked = entry.locked
                            }
                        }
                    }
                }
            }
        }
    }
}

private fun items(count: Int) = when (count) { 0 -> "Empty"; 1 -> "1 item"; else -> "$count items" }

/** Explains an older desktop or a missing setting before the usual offline, error and loading states. */
@RequiresApi(29)
@Composable
private fun ExplorerGate(client: LiveClient, key: String, loading: String, content: @Composable () -> Unit) {
    val loaded = client.desktopViews.containsKey(key)
    when {
        !loaded && client.connected && client.desktopControl && client.fileExplorer == false ->
            Column(Modifier.padding(20.dp).testTag("explorerUpdateDesktop"), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Note(FileExplorer.UPDATE_DESKTOP); TextButton(onClick = client::checkDesktopFeatures) { Text("Check again") }
            }
        !loaded && client.connected && client.desktopControl && client.fileExplorer == null ->
            Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                LinearProgressIndicator(Modifier.fillMaxWidth()); Note("Checking what your desktop supports…")
            }
        else -> DesktopGate(client, key, loading, content)
    }
}

/** Offline and refused states above a loaded view, so an old listing is never mistaken for the current one. */
@RequiresApi(29)
@Composable
private fun ExplorerBanners(client: LiveClient, key: String) {
    val error = client.desktopErrors[key]
    if (!client.connected) Banner("Offline. Showing what your computer last sent.", MaterialTheme.colorScheme.surfaceVariant)
    else if (error != null) Banner(error, MaterialTheme.colorScheme.errorContainer, "Try again", client::refreshDesktop)
}

/** Where this folder is: the project, then each folder; tapping one goes there. Scrolls to show the deepest. */
@Composable
private fun Breadcrumbs(crumbs: List<Pair<String, String>>, onGo: (String) -> Unit) {
    val scroll = rememberScrollState()
    LaunchedEffect(crumbs.size) { scroll.animateScrollTo(scroll.maxValue) }
    Row(Modifier.fillMaxWidth().horizontalScroll(scroll).padding(horizontal = 12.dp).testTag("explorerCrumbs"),
        verticalAlignment = Alignment.CenterVertically) {
        crumbs.forEachIndexed { index, (target, label) ->
            val last = index == crumbs.lastIndex
            if (index > 0) Icon(ThreadIcons.ChevronRight, null, Modifier.size(16.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
            if (last) Text(label, Modifier.padding(horizontal = 8.dp, vertical = 12.dp), style = MaterialTheme.typography.labelLarge,
                fontWeight = FontWeight.SemiBold, maxLines = 1)
            else TextButton(onClick = { onGo(target) }, contentPadding = PaddingValues(horizontal = 8.dp)) {
                Text(label, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.widthIn(max = 180.dp))
            }
        }
    }
}

@Composable
private fun FilterField(query: String, onChange: (String) -> Unit, onClose: () -> Unit) {
    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) { focus.requestFocus() }
    OutlinedTextField(query, onChange, Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp).focusRequester(focus).testTag("explorerFilter"),
        singleLine = true, placeholder = { Text("Filter this folder by name") },
        leadingIcon = { Icon(ThreadIcons.Search, null) },
        trailingIcon = { IconButton(onClick = onClose) { Icon(ThreadIcons.Close, "Stop filtering") } })
}

@Composable
private fun ProjectRows(list: ExplorerProjects, onOpen: (ExplorerProject) -> Unit) {
    if (list.projects.isEmpty()) {
        Note("No projects are open on your computer. Open one in Raticode to browse it here.", Modifier.padding(20.dp))
        return
    }
    LazyColumn(Modifier.fillMaxSize().testTag("explorerProjects"), contentPadding = PaddingValues(bottom = 16.dp)) {
        items(list.projects, key = { it.id }) { project ->
            Row(Modifier.fillMaxWidth().clickable(role = Role.Button, onClickLabel = "Browse") { onOpen(project) }
                .heightIn(min = 64.dp).padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                Tile(ThreadIcons.Folder, MaterialTheme.colorScheme.primary)
                Text(project.name, Modifier.weight(1f).padding(horizontal = 14.dp), style = MaterialTheme.typography.titleMedium,
                    maxLines = 2, overflow = TextOverflow.Ellipsis)
                Icon(ThreadIcons.ChevronRight, null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        if (list.more > 0) item { Note("${list.more} more open projects aren't listed.", Modifier.padding(20.dp)) }
    }
}

@Composable
private fun Tile(icon: androidx.compose.ui.graphics.vector.ImageVector, tint: androidx.compose.ui.graphics.Color) {
    Box(Modifier.size(44.dp).clip(RoundedCornerShape(10.dp)).background(tint.copy(alpha = .14f)), contentAlignment = Alignment.Center) {
        Icon(icon, null, Modifier.size(24.dp), tint = tint)
    }
}

/** The second line of a row: what it is, how big, and when it changed. */
internal fun entryDetail(entry: ExplorerEntry, now: Long = System.currentTimeMillis()): String {
    val changed = FileExplorer.modified(entry.modified, now)
    fun join(vararg parts: String) = parts.filter { it.isNotBlank() }.joinToString(" · ")
    return when {
        entry.folder -> join("Folder", changed)
        entry.type == "link" -> "Link · the phone doesn't follow links"
        entry.type != "file" -> "Not a regular file"
        entry.locked -> "May hold passwords or keys · stays on your computer"
        else -> {
            val size = entry.size?.let(FileExplorer::size).orEmpty()
            val tooLarge = (entry.size ?: 0) > FileTransfers.MAX_FILE && !FileExplorer.readsText(FileExplorer.viewer(entry.name))
            join(size, if (tooLarge) "too large for the phone" else changed)
        }
    }
}

@Composable
private fun FolderRows(folder: ExplorerFolder, query: String, onOpen: (ExplorerEntry) -> Unit) {
    val shown = remember(folder, query) { FileExplorer.filter(folder.entries, query) }
    if (folder.entries.isEmpty()) { Note("This folder is empty.", Modifier.padding(20.dp)); return }
    if (shown.isEmpty()) { Note("Nothing in this folder matches “${query.trim()}”.", Modifier.padding(20.dp)); return }
    LazyColumn(Modifier.fillMaxSize().testTag("explorerEntries"), contentPadding = PaddingValues(bottom = 16.dp)) {
        items(shown, key = { it.name }) { entry ->
            val detail = entryDetail(entry)
            Row(Modifier.fillMaxWidth().clickable(role = Role.Button, onClickLabel = if (entry.folder) "Open folder" else "Open file") { onOpen(entry) }
                .heightIn(min = 64.dp).padding(horizontal = 16.dp, vertical = 8.dp).semantics(mergeDescendants = true) {}
                .testTag("entry:${entry.name}"), verticalAlignment = Alignment.CenterVertically) {
                when {
                    entry.folder -> Tile(ThreadIcons.Folder, MaterialTheme.colorScheme.primary)
                    entry.type == "link" -> Tile(ThreadIcons.Link, MaterialTheme.colorScheme.onSurfaceVariant)
                    // Locked isn't a failure; it reads as a quiet, attention-colored lock, not an error.
                    entry.locked -> Tile(ThreadIcons.Lock, toneColor(RemTaskStatus.Tone.ATTENTION))
                    else -> KindBadge(entry.name, Modifier.size(44.dp).clip(RoundedCornerShape(10.dp)))
                }
                Column(Modifier.weight(1f).padding(horizontal = 14.dp)) {
                    Text(entry.name, style = MaterialTheme.typography.bodyLarge, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1,
                        overflow = TextOverflow.Ellipsis)
                }
                if (entry.folder) Icon(ThreadIcons.ChevronRight, null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        if (folder.more > 0) item { Note("${folder.more} more items in this folder aren't listed. Open the folder on your computer to see them all.", Modifier.padding(20.dp)) }
        if (folder.hidden > 0) item { Note("${folder.hidden} items with unusual characters in their names aren't shown.", Modifier.padding(horizontal = 20.dp)) }
    }
}

// The viewer ------------------------------------------------------------------------------------------------------

/**
 * One file, full screen. Text, code, Markdown, HTML and SVG come from the desktop as text (the first 256 KB, or the
 * whole file on request); everything else downloads through the normal end-to-end file transfer, up to 10 MB, and
 * is shown here or opened in another app.
 */
@RequiresApi(29)
@Composable
private fun FileViewerPage(client: LiveClient, projectId: String, projectName: String, path: String, listedSize: Long?, listedModified: String,
                           type: String, locked: Boolean, onAskRem: ((String) -> Unit)?, onClose: () -> Unit) {
    val name = FileExplorer.name(path)
    val viewer = FileExplorer.viewer(name)
    val readable = type == "file" && !locked
    var preview by rememberSaveable(path) { mutableStateOf(true) }
    var wrap by rememberSaveable(path) { mutableStateOf(true) }
    var find by rememberSaveable(path) { mutableStateOf<String?>(null) }
    var match by rememberSaveable(path) { mutableIntStateOf(0) }
    var whole by rememberSaveable(path) { mutableStateOf(false) }
    // Open in another app or Save, waiting for the download.
    var pending by rememberSaveable(path) { mutableStateOf<String?>(null) }
    var menu by remember { mutableStateOf(false) }
    val clipboard = LocalClipboardManager.current

    val textKey = FileExplorer.textKey(projectId, path)
    val readsText = readable && FileExplorer.readsText(viewer) && !whole
    DisposableEffect(readsText, textKey) {
        if (readsText) client.openDesktop(FileExplorer.TEXT, JSONObjectOf(projectId, path)) else client.openDesktop(null)
        onDispose { client.openDesktop(null) }
    }
    val text = client.desktopViews[textKey] as? ExplorerText
    val size = text?.size ?: listedSize
    val modified = text?.modified?.ifBlank { null } ?: listedModified
    val downloads = readable && (!FileExplorer.readsText(viewer) || whole || pending != null)
    LaunchedEffect(downloads, path) { if (downloads) client.openExplorerFile(projectId, path, size, modified) }
    DisposableEffect(path) { onDispose { client.dismissExplorerFetch() } }
    val fetch = client.explorerFetch?.takeIf { it.projectId == projectId && it.path == path }
    val local = fetch?.fileId?.let { id -> client.files.firstOrNull { it.id == id } }
    val ready = local?.state == "available"

    var exportId by rememberSaveable { mutableStateOf<String?>(null) }
    val exporter = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/octet-stream")) { uri ->
        exportId?.let { id -> if (uri != null) client.exportFile(id, uri) }
        exportId = null
    }
    val save = { id: String, saveName: String -> exportId = id; exporter.launch(saveName) }
    val opener = rememberFileOpener(client, save)
    LaunchedEffect(ready, pending) {
        val id = local?.id
        if (ready && id != null) when (pending) { "open" -> { pending = null; opener(id, name) }; "save" -> { pending = null; save(id, name) } }
    }
    val tooLarge = (size ?: 0) > FileTransfers.MAX_FILE
    fun elsewhere() { if (local != null && ready) opener(local.id, name) else pending = "open" }

    val isText = readsText || (whole && FileExplorer.readsText(viewer))
    val codeShown = isText && (viewer == FileViewer.TEXT || !preview)
    val wholeText = wholeTextOf(client, local?.takeIf { whole && FileExplorer.readsText(viewer) })
    val shownText = if (readsText) text?.takeIf { !it.binary }?.text else wholeText
    val lines = remember(shownText) { shownText?.let(CodeLines::split).orEmpty() }
    val hits = remember(lines, find) { CodeLines.matches(lines, find.orEmpty()) }
    val hit = hits.getOrNull(match.coerceIn(0, (hits.size - 1).coerceAtLeast(0)))
    Column(Modifier.fillMaxSize().testTag("fileViewer")) {
        val folder = FileExplorer.parent(path)
        val details = listOfNotNull(size?.let(FileExplorer::size), FileExplorer.modified(modified).ifBlank { null },
            if (folder.isEmpty()) projectName else folder).joinToString(" · ")
        Header(name, details, onClose) {
            // While finding, the find bar's own close button ends it.
            if (codeShown && find == null) IconButton(onClick = { find = ""; match = 0 }) { Icon(ThreadIcons.Search, "Find in file") }
            if (isText && viewer != FileViewer.TEXT) IconButton(onClick = { preview = !preview; find = null }) {
                Icon(if (preview) ThreadIcons.Code else ThreadIcons.Eye, if (preview) "Show source" else "Show preview")
            }
            if (codeShown) IconToggleButton(wrap, { wrap = it }, Modifier.semantics { stateDescription = if (wrap) "On" else "Off" }) {
                Icon(ThreadIcons.WrapText, "Wrap long lines", tint = if (wrap) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Box {
                IconButton(onClick = { menu = true }) { Icon(Icons.Filled.MoreVert, "More options") }
                DropdownMenu(menu, { menu = false }) {
                    if (readable && !tooLarge) {
                        DropdownMenuItem({ Text("Open in another app") }, { menu = false; elsewhere() })
                        DropdownMenuItem({ Text("Save to phone") }, { menu = false; if (local != null && ready) save(local.id, name) else pending = "save" })
                    }
                    DropdownMenuItem({ Text("Copy path") }, { menu = false; clipboard.setText(AnnotatedString(path)) })
                    if (readsText) DropdownMenuItem({ Text("Refresh") }, { menu = false; client.refreshDesktop() })
                }
            }
        }
        if (find != null && codeShown) {
            FindBar(find.orEmpty(), { find = it; match = 0 }, if (hits.isEmpty()) 0 else match.coerceIn(0, hits.lastIndex) + 1, hits.size,
                onStep = { step -> if (hits.isNotEmpty()) match = (match + step).mod(hits.size) }, onClose = { find = null })
        }
        Box(Modifier.weight(1f).fillMaxWidth()) {
            // Ask Rem is offered only on a file the phone may show, and never on a secret-looking one.
            val ask: ((String, IntRange?) -> Unit)? = onAskRem?.takeIf { readable && !AskRem.refused(path) }?.let { send ->
                { selected, range -> AskRem.file(projectName, path, range, selected)?.let(send) }
            }
            val content = @Composable { TextContent(shownText.orEmpty(), lines, name, viewer, preview, wrap, find.orEmpty(), hit, ask) }
            when {
                type == "link" -> Centered { ProblemCard("This is a link to somewhere else on your computer, so the phone doesn't follow it.", null) }
                type != "file" -> Centered { ProblemCard("This isn't a regular file, so it can't be opened from the phone.", null) }
                locked -> Centered { ProblemCard("This file may hold passwords, keys or sign-ins, so it stays on your computer.", null) }
                readsText -> ExplorerGate(client, textKey, "Reading $name…") {
                    val shown = text
                    Column(Modifier.fillMaxSize()) {
                        ExplorerBanners(client, textKey)
                        when {
                            shown == null -> {}
                            shown.binary -> Centered {
                                ProblemCard(if (tooLarge) "This file isn't text, and it's larger than the 10 MB a phone can download. Open it on your computer."
                                    else "This file isn't text, so the phone can't show it here.", if (tooLarge) null else ({ elsewhere() }))
                            }
                            else -> {
                                Box(Modifier.weight(1f)) { content() }
                                if (shown.truncated) TruncatedBar(shown.size, tooLarge) { whole = true }
                            }
                        }
                    }
                }
                whole && FileExplorer.readsText(viewer) && ready ->
                    if (wholeText == null) Centered { CircularProgressIndicator(Modifier.size(28.dp), strokeWidth = 2.dp) } else content()
                tooLarge && !ready -> Centered { ProblemCard("This file is ${FileExplorer.size(size ?: 0)}, larger than the 10 MB a phone can download. Open it on your computer.", null) }
                !ready -> DownloadState(fetch, local, client.connected) { client.dismissExplorerFetch(); client.openExplorerFile(projectId, path, size, modified) }
                else -> DownloadedContent(client, local!!.id, name, viewer, onClose, ::elsewhere) { id -> save(id, name) }
            }
        }
    }
}

@Suppress("FunctionName")
private fun JSONObjectOf(projectId: String, path: String) = org.json.JSONObject().put("project_id", projectId).put("path", path)

@Composable
private fun Centered(content: @Composable () -> Unit) = Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { content() }

/** The whole downloaded text file, decoded once the download is verified. */
@RequiresApi(29)
@Composable
private fun wholeTextOf(client: LiveClient, local: LiveClient.LiveFile?): String? {
    var text by remember(local?.id) { mutableStateOf<String?>(null) }
    LaunchedEffect(local?.id, local?.state) {
        val id = local?.id ?: return@LaunchedEffect
        if (local.state != "available") return@LaunchedEffect
        client.readMedia(id) { bytes ->
            text = try { bytes?.let { FileKinds.previewText(it, it.size)?.text } ?: "" } finally { bytes?.fill(0) }
        }
    }
    return text
}

@Composable
internal fun TextContent(text: String, lines: List<String>, name: String, viewer: FileViewer, preview: Boolean, wrap: Boolean, query: String, hit: Int?,
                         onAskRem: ((String, IntRange?) -> Unit)? = null) {
    when {
        // Rendered text has no line numbers of its own, so only its words go to Rem.
        preview && viewer == FileViewer.MARKDOWN -> MarkdownFileView(text, onAskRem = onAskRem?.let { ask -> { selected -> ask(selected, null) } })
        preview && viewer == FileViewer.HTML -> WebPreview(text, name)
        preview && viewer == FileViewer.SVG -> WebPreview(svgPage(text), name)
        else -> CodeView(lines, codeLanguage(name), wrap, query = query, current = hit, onAskRem = onAskRem)
    }
}

@Composable
private fun TruncatedBar(size: Long, tooLarge: Boolean, onWhole: () -> Unit) {
    Surface(color = MaterialTheme.colorScheme.surfaceVariant, modifier = Modifier.fillMaxWidth().testTag("truncatedBar")) {
        Row(Modifier.padding(start = 16.dp, end = 8.dp, top = 4.dp, bottom = 4.dp).navigationBarsPadding(), verticalAlignment = Alignment.CenterVertically) {
            Text(if (tooLarge) "Showing the first 256 KB of ${FileExplorer.size(size)}. The rest is on your computer."
                else "Showing the first 256 KB of ${FileExplorer.size(size)}.", Modifier.weight(1f).padding(vertical = 8.dp),
                style = MaterialTheme.typography.bodySmall)
            if (!tooLarge) TextButton(onClick = onWhole) { Text("Show all") }
        }
    }
}

@Composable
private fun FindBar(query: String, onChange: (String) -> Unit, position: Int, count: Int, onStep: (Int) -> Unit, onClose: () -> Unit) {
    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) { focus.requestFocus() }
    Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        OutlinedTextField(query, onChange, Modifier.weight(1f).focusRequester(focus).testTag("findInFile"), singleLine = true,
            placeholder = { Text("Find in file") },
            suffix = { if (query.isNotBlank()) Text(if (count == 0) "None" else "$position of $count", style = MaterialTheme.typography.labelMedium) })
        IconButton(onClick = { onStep(-1) }, enabled = count > 0) { Icon(ThreadIcons.ChevronUp, "Previous match") }
        IconButton(onClick = { onStep(1) }, enabled = count > 0) { Icon(ThreadIcons.ChevronDown, "Next match") }
        IconButton(onClick = onClose) { Icon(ThreadIcons.Close, "Stop finding") }
    }
}

/** Asking the desktop, downloading with progress, or why the download didn't work. */
@Composable
private fun DownloadState(fetch: FileExplorer.Fetch?, local: LiveClient.LiveFile?, connected: Boolean, onRetry: () -> Unit) {
    Column(Modifier.fillMaxSize().padding(24.dp).testTag("fileDownload"), verticalArrangement = Arrangement.spacedBy(12.dp, Alignment.CenterVertically),
        horizontalAlignment = Alignment.CenterHorizontally) {
        val error = fetch?.error
        when {
            error != null -> {
                Text(error, style = MaterialTheme.typography.bodyLarge, textAlign = androidx.compose.ui.text.style.TextAlign.Center)
                if (connected) OutlinedButton(onClick = onRetry) { Text("Try again") }
            }
            local == null || local.state == "offered" -> {
                Text(when {
                    // Offline, a copy opened before is looked up on the phone first.
                    local == null -> if (connected) "Asking your computer for this file…" else "Opening…"
                    connected -> "Starting the download…"
                    else -> "Connect to your computer to download this file."
                })
                LinearProgressIndicator(Modifier.fillMaxWidth(.7f))
            }
            local.state == "receiving" -> {
                val progress = if (local.size > 0) local.bytes.toFloat() / local.size else 0f
                Text("Downloading ${FileExplorer.size(local.bytes)} of ${FileExplorer.size(local.size)}")
                LinearProgressIndicator({ progress }, Modifier.fillMaxWidth(.7f).semantics { contentDescription = "${(progress * 100).toInt()}% downloaded" })
            }
            else -> {
                Text("The download didn't finish.", style = MaterialTheme.typography.bodyLarge)
                if (connected) OutlinedButton(onClick = onRetry) { Text("Try again") }
            }
        }
    }
}

/** A verified download, shown with the viewer for its type. */
@RequiresApi(29)
@Composable
private fun DownloadedContent(client: LiveClient, id: String, name: String, viewer: FileViewer, onClose: () -> Unit, onElsewhere: () -> Unit,
                              onSave: (String) -> Unit) {
    when (viewer) {
        FileViewer.IMAGE -> PictureView({ done -> client.readMedia(id, done) }, name, onElsewhere)
        FileViewer.PDF -> PdfView({ done -> client.readMedia(id, done) }, name, onElsewhere)
        FileViewer.VIDEO, FileViewer.AUDIO -> {
            Centered { ProblemCard("Playing $name.", onElsewhere) }
            VideoPlayerDialog(name, null, load = { done -> client.readMedia(id, done) }, onClose = onClose,
                onOpenElsewhere = { onClose(); onElsewhere() }, audio = viewer == FileViewer.AUDIO)
        }
        else -> Centered {
            Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(12.dp),
                modifier = Modifier.padding(24.dp).testTag("fileOther")) {
                KindBadge(name, Modifier.size(72.dp).clip(RoundedCornerShape(14.dp)), large = true)
                val kind = FileKinds.extension(name).takeIf { it.isNotBlank() }?.let { ".$it files" } ?: "this kind of file"
                Text("The phone can't show $kind itself. Open it in another app, or save it to the phone.",
                    style = MaterialTheme.typography.bodyLarge, textAlign = androidx.compose.ui.text.style.TextAlign.Center)
                Button(onClick = onElsewhere) { Text("Open in another app") }
                OutlinedButton(onClick = { onSave(id) }) { Text("Save to phone") }
            }
        }
    }
}
