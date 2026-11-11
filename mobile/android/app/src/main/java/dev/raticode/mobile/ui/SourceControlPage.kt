package dev.raticode.mobile.ui

import androidx.activity.compose.BackHandler
import androidx.annotation.RequiresApi
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.Orientation
import androidx.compose.foundation.gestures.draggable
import androidx.compose.foundation.gestures.rememberDraggableState
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Clear
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.onClick
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.raticode.mobile.live.*
import org.json.JSONArray
import org.json.JSONObject

/**
 * The desktop's Source control side pane on the phone: open projects, then one project's Changes, History (with the
 * branch graph) and Branches, and diffs of changed files and commits. It only reads: staging, committing, pushing,
 * pulling and branch or worktree changes stay on the computer. [onAskRem] takes the words for Rem's message box when the
 * user asks Rem about a commit, a file's change or one hunk of it; nothing is sent from here.
 */
@RequiresApi(29)
@Composable
internal fun SourceControlPage(client: LiveClient, onBack: (() -> Unit)?, onAskRem: ((String) -> Unit)? = null) {
    var open by rememberSaveable { mutableStateOf<String?>(null) }
    // Each project opens to History, as Source control does on the desktop; leaving the page starts it over.
    var tab by rememberSaveable { mutableStateOf(SourceControl.openingTab(client.sourceControlHistory)) }
    var commit by rememberSaveable { mutableStateOf<String?>(null) }
    var diffSource by rememberSaveable { mutableStateOf<String?>(null) }
    var diffPath by rememberSaveable { mutableStateOf("") }
    var diffHash by rememberSaveable { mutableStateOf("") }
    // null shows every branch and tag; otherwise the ref IDs or categories the desktop's filter accepts.
    var refs by rememberSaveable { mutableStateOf<List<String>?>(null) }
    var limit by rememberSaveable { mutableIntStateOf(SourceControl.HISTORY_STEP) }
    var query by rememberSaveable { mutableStateOf("") }
    val history = client.sourceControlHistory == true
    fun openProject(id: String?) { open = id; tab = SourceControl.openingTab(client.sourceControlHistory); commit = null; diffSource = null; refs = null; limit = SourceControl.HISTORY_STEP; query = "" }

    val project = open
    val list = client.desktopViews[SourceControl.LIST] as? SourceProjectList
    val status = project?.let { id -> client.desktopViews[SourceControl.key(id)] as? SourceProject ?: list?.projects?.firstOrNull { it.id == id } }
    // A folder that isn't a working repository has no history: read its status, which says why.
    val usable = status == null || status.state == "ok"
    val (op, params) = when {
        project == null -> SourceControl.LIST to JSONObject()
        diffSource != null -> SourceControl.DIFF to JSONObject().put("project_id", project).put("source", diffSource).put("path", diffPath)
            .apply { if (diffHash.isNotBlank()) put("hash", diffHash) }
        commit != null -> SourceControl.COMMIT to JSONObject().put("project_id", project).put("hash", commit)
        tab == "history" && history && usable -> SourceControl.HISTORY to JSONObject().put("project_id", project).put("limit", limit)
            .apply { refs?.let { put("refs", JSONArray(it)) } }
        else -> SourceControl.READ to JSONObject().put("project_id", project)
    }
    val key = if (project == null) SourceControl.LIST else SourceControl.viewKey(op, params)
    DisposableEffect(op, params.toString()) { client.openDesktop(op, params); onDispose { client.openDesktop(null) } }
    BackHandler(enabled = project != null) {
        when { diffSource != null -> diffSource = null; commit != null -> commit = null; else -> openProject(null) }
    }
    Column(Modifier.fillMaxSize()) {
        val (title, subtitle) = when {
            project == null -> "Source control" to if (client.connected) "Open projects on your computer" else "Offline"
            diffSource != null -> SourceControl.splitPath(diffPath).first to when (diffSource) {
                "staged" -> "Staged changes"; "unstaged" -> "Changes not staged"; else -> "In commit ${diffHash.take(7)}" }
            commit != null -> "Commit ${commit!!.take(7)}" to (status?.name ?: "")
            else -> (status?.name?.ifBlank { null } ?: "Project") to (status?.let(SourceControl::branchText) ?: "")
        }
        Header(title, subtitle, if (project != null) ({ when { diffSource != null -> diffSource = null; commit != null -> commit = null; else -> openProject(null) } }) else onBack) {
            IconButton(onClick = client::refreshDesktop, enabled = client.connected && client.sourceControl == true) {
                Icon(Icons.Filled.Refresh, "Refresh")
            }
        }
        Box(Modifier.weight(1f)) {
            when {
                project == null -> SourceGate(client, key, "Reading your projects…") {
                    Column(Modifier.fillMaxSize()) {
                        Banners(client, key)
                        (client.desktopViews[key] as? SourceProjectList)?.let { ProjectList(it) { id -> openProject(id) } }
                    }
                }
                diffSource != null -> SourceGate(client, key, "Reading this change…") {
                    Column(Modifier.fillMaxSize()) { Banners(client, key); (client.desktopViews[key] as? SourceDiff)?.let { DiffView(it, status?.name.orEmpty(), diffHash, onAskRem) } }
                }
                commit != null -> SourceGate(client, key, "Reading this commit…") {
                    Column(Modifier.fillMaxSize()) {
                        Banners(client, key)
                        (client.desktopViews[key] as? SourceCommitDetail)?.let { detail ->
                            CommitView(detail, status?.name.orEmpty(), onAskRem) { path -> diffSource = "commit"; diffPath = path; diffHash = detail.commit.hash }
                        }
                    }
                }
                status != null && status.state != "ok" -> NotRepository(status)
                else -> Column(Modifier.fillMaxSize()) {
                    status?.let { Summary(it) }
                    val tabs = listOf("changes" to "Changes${status?.takeIf { it.state == "ok" }?.let { " · ${it.changes.total}" }.orEmpty()}",
                        "history" to "History", "branches" to "Branches")
                    TabRow(tabs.indexOfFirst { it.first == tab }.coerceAtLeast(0), Modifier.testTag("sourceTabs"), containerColor = MaterialTheme.colorScheme.background) {
                        tabs.forEach { (id, label) -> Tab(tab == id, { tab = id }, text = { Text(label, maxLines = 1) }) }
                    }
                    Box(Modifier.weight(1f)) {
                        val readKey = SourceControl.key(project)
                        when (tab) {
                            "history" -> if (client.sourceControlHistory == null && client.connected) Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                LinearProgressIndicator(Modifier.fillMaxWidth()); Note("Checking what your desktop supports…")
                            } else if (!history) UpdateForHistory(client) else SourceGate(client, key, "Reading history…") {
                                Column(Modifier.fillMaxSize()) {
                                    Banners(client, key)
                                    (client.desktopViews[key] as? SourceHistory)?.let { graph ->
                                        HistoryTab(graph, refs, { refs = it; limit = SourceControl.HISTORY_STEP }, query, { query = it }, limit,
                                            { limit = (limit + SourceControl.HISTORY_STEP).coerceAtMost(SourceControl.HISTORY_MAX) }) { commit = it }
                                    }
                                }
                            }
                            else -> SourceGate(client, readKey, "Reading this project…") {
                                Column(Modifier.fillMaxSize()) {
                                    Banners(client, readKey)
                                    (client.desktopViews[readKey] as? SourceProject)?.takeIf { it.detailed }?.let { detail ->
                                        if (tab == "branches") BranchesTab(detail, history)
                                        else ChangesTab(detail, history) { source, path -> diffSource = source; diffPath = path; diffHash = "" }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

/** Like [DesktopGate], and says when the desktop is too old to answer Git status. */
@RequiresApi(29)
@Composable
private fun SourceGate(client: LiveClient, key: String, loading: String, content: @Composable () -> Unit) {
    val loaded = client.desktopViews.containsKey(key)
    when {
        !loaded && client.connected && client.desktopControl && client.sourceControl == false ->
            Column(Modifier.padding(20.dp).testTag("sourceUpdateDesktop"), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Note(SourceControl.UPDATE_DESKTOP); TextButton(onClick = client::checkDesktopFeatures) { Text("Check again") }
            }
        !loaded && client.connected && client.desktopControl && client.sourceControl == null ->
            Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                LinearProgressIndicator(Modifier.fillMaxWidth()); Note("Checking what your desktop supports…")
            }
        else -> DesktopGate(client, key, loading, content)
    }
}

@RequiresApi(29)
@Composable
private fun UpdateForHistory(client: LiveClient) {
    Column(Modifier.padding(20.dp).testTag("sourceUpdateHistory"), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Note(SourceControl.UPDATE_FOR_HISTORY); TextButton(onClick = client::checkDesktopFeatures, enabled = client.connected) { Text("Check again") }
    }
}

/** Offline and refused states above a loaded view, so old data is never mistaken for current. */
@RequiresApi(29)
@Composable
private fun Banners(client: LiveClient, key: String) {
    val error = client.desktopErrors[key]
    if (!client.connected) Banner("Offline. Showing what your desktop last sent.", MaterialTheme.colorScheme.surfaceVariant)
    else if (error != null) Banner(error, MaterialTheme.colorScheme.errorContainer, "Try again", client::refreshDesktop)
}

// Pills ----------------------------------------------------------------------------------------------------------

private enum class Tone { PLAIN, GOOD, WARN, BAD, LOCAL, REMOTE, TAG }

@Composable
private fun toneColors(tone: Tone): Pair<Color, Color> {
    val dark = MaterialTheme.colorScheme.surface.luminance() < 0.5f
    val scheme = MaterialTheme.colorScheme
    // The desktop panel's ref colors (index.css .scm-history-ref-*), and matching tints for status.
    return when (tone) {
        Tone.PLAIN -> scheme.surfaceVariant to scheme.onSurfaceVariant
        Tone.GOOD -> if (dark) Color(0xFF173E32) to Color(0xFF8ADDBE) else Color(0xFFDCF3EB) to Color(0xFF196C54)
        Tone.WARN -> if (dark) Color(0xFF3F3214) to Color(0xFFF0C75E) else Color(0xFFFFF1D6) to Color(0xFF7A4F00)
        Tone.BAD -> scheme.errorContainer to scheme.onErrorContainer
        Tone.LOCAL -> if (dark) Color(0xFF193A57) to Color(0xFF9BCFFF) else Color(0xFFE0EFFF) to Color(0xFF165E9C)
        Tone.REMOTE -> if (dark) Color(0xFF3C2856) to Color(0xFFD0B1F3) else Color(0xFFEEE3FF) to Color(0xFF7444A9)
        Tone.TAG -> if (dark) Color(0xFF173E32) to Color(0xFF8ADDBE) else Color(0xFFDCF3EB) to Color(0xFF196C54)
    }
}

@Composable
private fun Pill(text: String, tone: Tone = Tone.PLAIN, small: Boolean = false, mark: String = "") {
    val (background, ink) = toneColors(tone)
    Surface(color = background, contentColor = ink, shape = RoundedCornerShape(6.dp)) {
        Text((if (mark.isNotBlank()) "$mark " else "") + text, Modifier.padding(horizontal = if (small) 5.dp else 8.dp, vertical = if (small) 1.dp else 3.dp),
            style = if (small) MaterialTheme.typography.labelSmall else MaterialTheme.typography.labelMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

/** Push, pull and change counts as pills. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun StatusPills(p: SourceProject) {
    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Pill(SourceControl.branchText(p), Tone.LOCAL, mark = "⎇")
        if (p.upstream.isNotBlank()) {
            val ahead = p.ahead ?: 0; val behind = p.behind ?: 0
            if (ahead > 0) Pill("$ahead to push", Tone.WARN, mark = "↑")
            if (behind > 0) Pill("$behind to pull", Tone.WARN, mark = "↓")
            if (ahead == 0 && behind == 0) Pill("Up to date", Tone.GOOD, mark = "✓")
        } else if (!p.detached) Pill("No upstream")
        val c = p.changes
        when {
            c.conflicts > 0 -> Pill("${c.conflicts} in conflict", Tone.BAD, mark = "!")
            c.total > 0 -> Pill("${c.total} ${if (c.total == 1) "change" else "changes"}", Tone.WARN, mark = "●")
            else -> Pill("Clean", Tone.GOOD, mark = "✓")
        }
        if (p.operation.isNotBlank()) Pill("${p.operation.replaceFirstChar { it.uppercase() }} paused", Tone.BAD)
    }
}

// Project list ---------------------------------------------------------------------------------------------------

@Composable
private fun ProjectList(list: SourceProjectList, onOpen: (String) -> Unit) {
    LazyColumn(Modifier.fillMaxSize().testTag("sourceProjects"), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        if (list.projects.isEmpty()) item { Note("No projects are open in Raticode on your computer. Open one there to see its status here.") }
        items(list.projects, key = { it.id }) { project ->
            Surface(onClick = { onOpen(project.id) }, shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.surface, modifier = Modifier.fillMaxWidth()) {
                Row(Modifier.padding(start = 16.dp, top = 14.dp, bottom = 14.dp, end = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text(project.name, style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        if (project.state == "ok") {
                            StatusPills(project)
                            project.lastCommit?.let { c ->
                                Text("${c.subject} · ${whenText(c.at, past = true)}", style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            }
                        } else Pill(SourceControl.headline(project), if (project.state == "not_repository") Tone.PLAIN else Tone.BAD)
                    }
                    Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
        if (list.more > 0) item { Note("${list.more} more open projects aren't shown.") }
        item { ReadOnlyNote() }
    }
}

@Composable
private fun ReadOnlyNote() = Note("Read-only here. Stage, commit, push, pull and switch branches on your computer.", Modifier.padding(top = 8.dp))

@Composable
private fun NotRepository(project: SourceProject) {
    Column(Modifier.fillMaxSize().padding(16.dp).testTag("sourceProject"), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        SettingsGroup(SourceControl.headline(project)) {
            Text(project.detail.ifBlank { "Your computer couldn't read this repository's status." }, style = MaterialTheme.typography.bodyMedium)
            Note(when (project.state) {
                "not_repository" -> "There are no changes to track here. To start, run git init in this project on your computer."
                "missing" -> "The folder was moved or deleted. Reopen the project in Raticode on your computer."
                else -> "Check that Git works in this project on your computer, then refresh."
            })
        }
    }
}

@Composable
private fun Summary(p: SourceProject) {
    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp).testTag("sourceSummary"), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        StatusPills(p)
        val notes = listOfNotNull(
            p.upstream.takeIf { it.isNotBlank() }?.let { if (p.fetchedAt.isBlank()) "Not fetched yet on your computer" else "Fetched ${whenText(p.fetchedAt, past = true)}" },
            p.stashes.takeIf { it > 0 }?.let { "$it stashed" },
            p.subfolder.takeIf { it.isNotBlank() }?.let { "Folder $it of a larger repository" })
        if (notes.isNotEmpty()) Text(notes.joinToString(" · "), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

// Changes --------------------------------------------------------------------------------------------------------

@Composable
private fun ChangesTab(project: SourceProject, diffs: Boolean, onFile: (String, String) -> Unit) {
    var collapsed by rememberSaveable { mutableStateOf(listOf<String>()) }
    LazyColumn(Modifier.fillMaxSize().testTag("sourceChanges"), contentPadding = PaddingValues(bottom = 16.dp)) {
        if (project.operation.isNotBlank() || project.changes.conflicts > 0) item {
            Surface(color = MaterialTheme.colorScheme.errorContainer, contentColor = MaterialTheme.colorScheme.onErrorContainer, shape = MaterialTheme.shapes.small,
                modifier = Modifier.fillMaxWidth().padding(16.dp)) {
                Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(if (project.operation.isNotBlank()) "${project.operation.replaceFirstChar { it.uppercase() }} paused" else "Unresolved conflicts",
                        style = MaterialTheme.typography.titleSmall)
                    Text("Resolve the files marked ! and continue on your computer, or ask Rem to resolve them.", style = MaterialTheme.typography.bodySmall)
                }
            }
        }
        if (project.files.isEmpty()) item {
            Column(Modifier.fillMaxWidth().padding(vertical = 40.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Icon(Icons.Filled.Check, null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
                Text("Working tree clean", style = MaterialTheme.typography.titleSmall)
                Note("No uncommitted changes.")
            }
        }
        // The desktop panel lists conflicts with staged files; here they get their own group first.
        val groups = listOf(
            Triple("conflicts", "Conflicts", project.files.filter { it.status == "conflict" }),
            Triple("staged", "Staged", project.files.filter { it.staged && it.status != "conflict" }),
            Triple("unstaged", "Changes", project.files.filter { it.unstaged && it.status != "conflict" }))
        groups.filter { it.third.isNotEmpty() }.forEach { (id, title, files) ->
            val open = id !in collapsed
            item(key = "group:$id") {
                Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).clickable { collapsed = if (open) collapsed + id else collapsed - id }
                    .semantics { heading(); stateDescription = if (open) "Expanded" else "Collapsed" }
                    .padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text("$title · ${files.size}", Modifier.weight(1f), style = MaterialTheme.typography.titleSmall)
                    Icon(if (open) androidx.compose.material.icons.Icons.Filled.ArrowDropDown else Icons.AutoMirrored.Filled.KeyboardArrowRight, null)
                }
            }
            if (open) items(files, key = { "$id:${it.path}" }) { file ->
                val source = if (id == "staged") "staged" else "unstaged"
                FileRow(file.path, file.status, diffs) { onFile(source, file.path) }
            }
        }
        if (project.moreFiles > 0) item { Note("${project.moreFiles} more changed files. See them on your computer.", Modifier.padding(16.dp)) }
        if (project.files.isNotEmpty() && !diffs) item { Note(SourceControl.UPDATE_FOR_HISTORY, Modifier.padding(16.dp)) }
        item { Box(Modifier.padding(horizontal = 16.dp)) { ReadOnlyNote() } }
    }
}

@Composable
private fun statusTone(status: String) = when (status) {
    "added", "untracked" -> Tone.GOOD; "deleted", "conflict" -> Tone.BAD; "renamed" -> Tone.REMOTE; else -> Tone.WARN
}

/** One changed file: a status badge, its name, and the folder it is in. */
@Composable
private fun FileRow(path: String, status: String, openable: Boolean, trailing: String = "", onOpen: () -> Unit) {
    val (name, folder) = SourceControl.splitPath(path)
    val (background, ink) = toneColors(statusTone(status))
    Row(Modifier.fillMaxWidth().heightIn(min = 56.dp)
        .then(if (openable) Modifier.clickable(onClickLabel = "Show changes", role = Role.Button, onClick = onOpen) else Modifier)
        .semantics(mergeDescendants = true) { contentDescription = "${SourceControl.statusLabel(status)}: $path" + if (trailing.isNotBlank()) ", $trailing" else "" }
        .padding(horizontal = 16.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Box(Modifier.size(28.dp).background(background, RoundedCornerShape(6.dp)), contentAlignment = Alignment.Center) {
            Text(SourceControl.statusLetter(status), color = ink, style = MaterialTheme.typography.labelLarge, fontWeight = FontWeight.Bold)
        }
        Column(Modifier.weight(1f)) {
            Text(name, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium, maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (folder.isNotBlank()) Text(folder, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1, overflow = TextOverflow.MiddleEllipsis)
        }
        if (trailing.isNotBlank()) Text(trailing, style = MaterialTheme.typography.labelMedium, fontFamily = FontFamily.Monospace,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        if (openable) Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

// History --------------------------------------------------------------------------------------------------------

private val LANE_SPACING = 16.dp
private val LANE_INSET = 8.dp
private val DOT_Y = 22.dp
private val LIGHT_LANES = listOf(Color(0xFF0067A5), Color(0xFF956000), Color(0xFF9F367C), Color(0xFF007565))
private val DARK_LANES = listOf(Color(0xFF56B4E9), Color(0xFFF0C75E), Color(0xFFE7A1D0), Color(0xFF59D9B4))
/** The desktop's dash patterns, one per lane color, so lanes differ by more than color. */
private val LANE_DASHES = listOf(null, floatArrayOf(7f, 3f), floatArrayOf(2f, 3f), floatArrayOf(9f, 3f, 2f, 3f))

private fun refSummary(refs: List<String>?, all: List<SourceRef>): String = when {
    refs == null -> "All branches and tags"
    refs.isEmpty() -> "Nothing selected"
    refs.size > 1 -> "${refs.size} selections"
    else -> when (val only = refs.single()) {
        "HEAD" -> "Current branch"; "local" -> "Local branches"; "remote" -> "Remote branches"; "tag" -> "Tags"
        else -> all.firstOrNull { it.id == only }?.let { if (it.current) "${it.name} (current)" else it.name } ?: only
    }
}

@Composable
private fun HistoryTab(graph: SourceHistory, refs: List<String>?, onRefs: (List<String>?) -> Unit, query: String, onQuery: (String) -> Unit,
                       limit: Int, onMore: () -> Unit, onCommit: (String) -> Unit) {
    val refsById = remember(graph.refs) { graph.refs.associateBy { it.id } }
    val shown = remember(graph.commits, query, refsById) { HistoryGraph.filter(graph.commits, query) { c -> c.labels.mapNotNull { refsById[it]?.name } } }
    val layout = remember(shown) { HistoryGraph.layout(shown) }
    val density = LocalDensity.current
    var pan by remember { mutableFloatStateOf(0f) }
    BoxWithConstraints(Modifier.fillMaxSize()) {
        // As on desktop: the text keeps at least two thirds, and a wider graph pans inside the last third.
        val graphWidth: Dp = LANE_INSET * 2 + LANE_SPACING * (layout.lanes - 1).coerceAtLeast(0)
        val column = minOf(graphWidth, maxWidth / 3)
        val maxPan = with(density) { (graphWidth - column).toPx() }.coerceAtLeast(0f)
        LaunchedEffect(maxPan) { pan = pan.coerceIn(0f, maxPan) }
        LazyColumn(Modifier.fillMaxSize().testTag("sourceHistory"), contentPadding = PaddingValues(bottom = 16.dp)) {
            item(key = "controls") {
                Column(Modifier.padding(horizontal = 16.dp, vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    BranchFilter(graph.refs, refs, onRefs)
                    val keyboard = androidx.compose.ui.platform.LocalSoftwareKeyboardController.current
                    OutlinedTextField(query, { onQuery(it.take(200)) }, Modifier.fillMaxWidth().testTag("historySearch"), singleLine = true,
                        keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(imeAction = androidx.compose.ui.text.input.ImeAction.Search),
                        keyboardActions = androidx.compose.foundation.text.KeyboardActions(onSearch = { keyboard?.hide() }),
                        placeholder = { Text("Filter message, author, branch or ID") }, leadingIcon = { Icon(Icons.Filled.Search, null) },
                        trailingIcon = { if (query.isNotEmpty()) IconButton(onClick = { onQuery("") }) { Icon(Icons.Filled.Clear, "Clear filter") } })
                    Note(if (maxPan > 0f) "Newer commits are above their ancestors. Drag the graph sideways to see every lane."
                        else "Newer commits are above their ancestors. Lines meet at shared commits.")
                }
            }
            when {
                graph.commits.isEmpty() -> item { Note(if (refs != null) "No commits in this selection." else "No commits yet.", Modifier.padding(16.dp)) }
                layout.rows.isEmpty() -> item { Note("No loaded commits match. Clear the filter or load more history.", Modifier.padding(16.dp)) }
            }
            items(layout.rows, key = { it.commit.hash }) { row ->
                HistoryRow(row, refsById, column, pan, { delta -> pan = (pan - delta).coerceIn(0f, maxPan) }) { onCommit(row.commit.hash) }
            }
            if (graph.hasMore && limit < SourceControl.HISTORY_MAX) item {
                OutlinedButton(onClick = onMore, modifier = Modifier.fillMaxWidth().padding(16.dp)) { Text("Load more history") }
            } else if (graph.hasMore) item { Note("Showing ${SourceControl.HISTORY_MAX} commits. Choose a branch to narrow history.", Modifier.padding(16.dp)) }
        }
    }
}

@Composable
private fun BranchFilter(all: List<SourceRef>, refs: List<String>?, onRefs: (List<String>?) -> Unit) {
    var menu by remember { mutableStateOf(false) }
    val current = all.firstOrNull { it.current }
    Box {
        OutlinedButton(onClick = { menu = true }, modifier = Modifier.fillMaxWidth().semantics { contentDescription = "History branches: ${refSummary(refs, all)}" }) {
            Text(refSummary(refs, all), Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
            Icon(Icons.Filled.ArrowDropDown, null)
        }
        DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
            @Composable fun choice(label: String, value: List<String>?) =
                DropdownMenuItem(text = { Text(label, maxLines = 1, overflow = TextOverflow.Ellipsis) }, onClick = { menu = false; onRefs(value) },
                    trailingIcon = { if (value == refs) Icon(Icons.Filled.Check, "Selected") })
            choice("All branches and tags", null)
            choice("Current branch", listOf(current?.id ?: "HEAD"))
            choice("Local branches", listOf("local"))
            if (all.any { it.type == "remote" }) choice("Remote branches", listOf("remote"))
            if (all.any { it.type == "tag" }) choice("Tags", listOf("tag"))
            HorizontalDivider()
            all.filter { it.type == "local" }.take(60).forEach { choice(it.name + if (it.current) " (current)" else "", listOf(it.id)) }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun HistoryRow(row: HistoryGraph.Row, refs: Map<String, SourceRef>, graphColumn: Dp, pan: Float, onPan: (Float) -> Unit, onOpen: () -> Unit) {
    val c = row.commit
    val labels = c.labels.mapNotNull(refs::get)
    val detached = c.isHead && labels.none { it.current }
    val spoken = buildString {
        append(c.subject); append(", by ${c.author}, ${whenText(c.at, past = true)}, commit ${c.short}")
        SourceControl.commitStats(c)?.let { append(", ${it.label}") }
        if (labels.isNotEmpty()) append(", " + labels.joinToString { (if (it.type == "tag") "tag " else "branch ") + it.name + if (it.current) " (current)" else "" })
        if (c.isHead) append(", checked out")
    }
    Row(Modifier.fillMaxWidth().height(IntrinsicSize.Min).heightIn(min = 52.dp).testTag("historyRow")) {
        Column(Modifier.weight(1f).clickable(onClickLabel = "Show commit", role = Role.Button, onClick = onOpen)
            .semantics(mergeDescendants = true) { contentDescription = spoken }.padding(start = 16.dp, end = 8.dp, top = 8.dp, bottom = 8.dp),
            verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Text(c.subject.ifBlank { "(no message)" }, style = MaterialTheme.typography.bodyMedium, fontWeight = if (c.isHead) FontWeight.SemiBold else FontWeight.Normal,
                maxLines = 2, overflow = TextOverflow.Ellipsis)
            if (labels.isNotEmpty() || detached) FlowRow(horizontalArrangement = Arrangement.spacedBy(4.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                if (detached) Pill("HEAD", Tone.LOCAL, small = true, mark = "◉")
                labels.forEach { ref ->
                    val tone = when (ref.type) { "remote" -> Tone.REMOTE; "tag" -> Tone.TAG; else -> Tone.LOCAL }
                    Pill(ref.name, tone, small = true, mark = when { ref.type == "tag" -> "⌂"; ref.type == "remote" -> "☁"; ref.current -> "◉"; else -> "⎇" })
                    if (ref.upstream.isNotBlank() && (ref.ahead > 0 || ref.behind > 0 || ref.upstreamGone))
                        Pill(if (ref.upstreamGone) "upstream missing" else "↑${ref.ahead} ↓${ref.behind}", Tone.LOCAL, small = true)
                }
            }
            CommitMeta(c)
        }
        GraphCell(row, graphColumn, pan, onPan)
    }
}

/**
 * Author and time, then the commit ID with its line counts right after it, as the desktop's history row shows them. A long
 * author shortens first; when even that leaves too little room, the ID and its counts move to the next line together.
 */
@Composable
private fun CommitMeta(c: SourceHistoryCommit) {
    val muted = MaterialTheme.colorScheme.onSurfaceVariant
    val style = MaterialTheme.typography.labelSmall
    val stats = SourceControl.commitStats(c)
    val dark = MaterialTheme.colorScheme.surface.luminance() < 0.5f
    // The desktop's emerald and red, darkened or lightened to stay readable on this surface.
    val added = if (dark) Color(0xFF6EE7A8) else Color(0xFF13734B)
    val removed = if (dark) Color(0xFFFF9A9A) else Color(0xFFB3261E)
    androidx.compose.ui.layout.Layout(content = {
        Text(listOf(c.author, whenText(c.at, past = true)).filter { it.isNotBlank() }.joinToString(" · "),
            style = style, color = muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.testTag("commitId")) {
            Text(c.short, style = style, color = muted, fontFamily = FontFamily.Monospace, maxLines = 1)
            if (stats != null) Row(Modifier.testTag("commitStats"), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                if (stats.lines) {
                    Text("+${stats.insertions}", style = style, color = added, fontFamily = FontFamily.Monospace, fontWeight = FontWeight.SemiBold, maxLines = 1)
                    Text("−${stats.deletions}", style = style, color = removed, fontFamily = FontFamily.Monospace, fontWeight = FontWeight.SemiBold, maxLines = 1)
                }
                if (stats.binaryFiles > 0) Text("${stats.binaryFiles} binary", style = style, color = muted, fontFamily = FontFamily.Monospace, maxLines = 1)
            }
        }
    }) { measurables, constraints ->
        val gap = 8.dp.roundToPx()
        val minAuthor = 56.dp.roundToPx()
        val loose = constraints.copy(minWidth = 0, minHeight = 0)
        val id = measurables[1].measure(loose)
        val oneLine = constraints.maxWidth - id.width - gap >= minAuthor
        val author = measurables[0].measure(loose.copy(maxWidth = if (oneLine) constraints.maxWidth - id.width - gap else constraints.maxWidth))
        val width = constraints.maxWidth.coerceAtLeast(0)
        if (oneLine) {
            val height = maxOf(author.height, id.height)
            layout(width, height) {
                author.placeRelative(0, (height - author.height) / 2)
                id.placeRelative(author.width + gap, (height - id.height) / 2)
            }
        } else layout(width, author.height + 2.dp.roundToPx() + id.height) {
            author.placeRelative(0, 0); id.placeRelative(0, author.height + 2.dp.roundToPx())
        }
    }
}

/** One row of the branch graph, drawn like the desktop's GitHistoryLane: lines in from above, the dot, and lines out below. */
@Composable
private fun GraphCell(row: HistoryGraph.Row, width: Dp, pan: Float, onPan: (Float) -> Unit) {
    val lanes = if (MaterialTheme.colorScheme.surface.luminance() < 0.5f) DARK_LANES else LIGHT_LANES
    val surface = MaterialTheme.colorScheme.surface
    Canvas(Modifier.width(width).fillMaxHeight().clipToBounds()
        .draggable(rememberDraggableState(onPan), Orientation.Horizontal)) {
        val spacing = LANE_SPACING.toPx(); val inset = LANE_INSET.toPx(); val dotY = DOT_Y.toPx(); val bottom = size.height
        fun x(lane: Int) = inset + lane * spacing - pan
        fun stroke(color: Int) = Stroke(2.5.dp.toPx(), cap = StrokeCap.Round,
            pathEffect = LANE_DASHES[color % 4]?.let { d -> PathEffect.dashPathEffect(FloatArray(d.size) { d[it] * density }) })
        row.segments.filter { it.full || it.top }.forEach { s ->
            drawLine(lanes[s.color % 4], Offset(x(s.from), 0f), Offset(x(s.from), dotY), strokeWidth = 2.5.dp.toPx(),
                pathEffect = stroke(s.color).pathEffect)
        }
        row.segments.filter { !it.top }.forEach { s ->
            val path = Path().apply {
                moveTo(x(s.from), dotY)
                if (s.full) lineTo(x(s.to), bottom)
                else { val span = bottom - dotY; cubicTo(x(s.from), dotY + span * 10 / 18, x(s.to), dotY + span * 8 / 18, x(s.to), bottom) }
            }
            drawPath(path, lanes[s.color % 4], style = stroke(s.color))
        }
        val color = lanes[row.color % 4]
        val center = Offset(x(row.lane), dotY)
        if (row.commit.isHead) {
            drawCircle(surface, 5.dp.toPx(), center); drawCircle(color, 5.dp.toPx(), center, style = Stroke(2.dp.toPx()))
        } else drawCircle(color, 4.dp.toPx(), center)
    }
}

// Commit and diff ------------------------------------------------------------------------------------------------

@Composable
internal fun CommitView(detail: SourceCommitDetail, project: String, onAskRem: ((String) -> Unit)?, onFile: (String) -> Unit) {
    val c = detail.commit
    val clipboard = LocalClipboardManager.current
    var copied by remember(c.hash) { mutableStateOf(false) }
    LazyColumn(Modifier.fillMaxSize().testTag("sourceCommit"), contentPadding = PaddingValues(bottom = 16.dp)) {
        item {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(c.subject.ifBlank { "(no message)" }, style = MaterialTheme.typography.titleMedium)
                if (c.body.isNotBlank()) Text(c.body, style = MaterialTheme.typography.bodyMedium)
                Text("${c.author} · ${whenText(c.at, past = true)}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    SourceControl.commitStats(c)?.let { stats ->
                        if (stats.lines) { Pill("+${stats.insertions}", Tone.GOOD); Pill("−${stats.deletions}", Tone.BAD) }
                        if (stats.binaryFiles > 0) Pill("${stats.binaryFiles} binary")
                    }
                    Spacer(Modifier.weight(1f))
                    TextButton(onClick = { clipboard.setText(AnnotatedString(c.hash)); copied = true },
                        modifier = Modifier.semantics { contentDescription = if (copied) "Copied commit ID ${c.short}" else "Copy commit ID ${c.short}" }) {
                        Text(if (copied) "Copied" else c.short, fontFamily = FontFamily.Monospace)
                    }
                }
                if (c.parents.size > 1) Note("A merge: changes are shown against its first parent, as on desktop.")
                if (onAskRem != null) AskRemButton("Ask Rem about this commit") { onAskRem(AskRem.commit(project, c.hash)) }
            }
        }
        item { Text("Files · ${detail.files.size + detail.moreFiles}", Modifier.padding(horizontal = 16.dp, vertical = 8.dp).semantics { heading() },
            style = MaterialTheme.typography.titleSmall) }
        if (detail.files.isEmpty()) item { Note("No file changes.", Modifier.padding(horizontal = 16.dp)) }
        items(detail.files, key = { it.path }) { f ->
            FileRow(f.path, f.status, true, if (f.binary) "binary" else "+${f.insertions} −${f.deletions}") { onFile(f.path) }
        }
        if (detail.moreFiles > 0) item { Note("${detail.moreFiles} more files. See them on your computer.", Modifier.padding(16.dp)) }
    }
}

/** Puts the words in Rem's message box and goes there; only the user can send them. */
@Composable
private fun AskRemButton(label: String, modifier: Modifier = Modifier, spoken: String = label, onClick: () -> Unit) {
    TextButton(onClick, modifier.semantics { contentDescription = spoken }, contentPadding = PaddingValues(horizontal = 8.dp)) {
        Icon(ThreadIcons.Rem, null, Modifier.size(16.dp))
        Spacer(Modifier.width(6.dp))
        Text(label, maxLines = 1)
    }
}

@Composable
internal fun DiffView(diff: SourceDiff, project: String, hash: String, onAskRem: ((String) -> Unit)?) {
    val lines = remember(diff.text) { SourceControl.diffLines(diff.text) }
    val hunks = remember(lines) { SourceControl.hunks(lines) }
    // A secret-looking file offers no Ask Rem at all; the desktop doesn't send its changes either.
    val ask = onAskRem?.takeIf { !AskRem.refused(diff.path) }
    val dark = MaterialTheme.colorScheme.surface.luminance() < 0.5f
    val added = if (dark) Color(0xFF173E2A) else Color(0xFFE6F6EA)
    val removed = if (dark) Color(0xFF4A1F24) else Color(0xFFFDE8E8)
    LazyColumn(Modifier.fillMaxSize().testTag("sourceDiff"), contentPadding = PaddingValues(vertical = 8.dp)) {
        if (ask != null) item(key = "askFile") {
            AskRemButton("Ask Rem about this file", Modifier.padding(horizontal = 8.dp)) {
                AskRem.diffFile(project, diff.path, diff.source, hash)?.let(ask)
            }
        }
        when {
            diff.binary -> item { Note("This is a binary file, so there are no lines to show. Open it on your computer.", Modifier.padding(16.dp)) }
            lines.isEmpty() -> item { Note("No text changes, such as a file mode change or an empty file.", Modifier.padding(16.dp)) }
        }
        itemsIndexed(lines) { index, line ->
            if (line.kind == '@') {
                Row(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceVariant).padding(horizontal = 12.dp, vertical = 2.dp),
                    verticalAlignment = Alignment.CenterVertically) {
                    Text(line.text, Modifier.weight(1f).padding(vertical = 4.dp), style = MaterialTheme.typography.labelSmall,
                        fontFamily = FontFamily.Monospace, color = MaterialTheme.colorScheme.primary)
                    val hunk = hunks[index]
                    if (ask != null && hunk != null) AskRemButton("Ask Rem", Modifier.testTag("askRemHunk"),
                        spoken = "Ask Rem about this change, ${AskRem.hunkLines(hunk.header)?.lowercase() ?: "this hunk"}") {
                        AskRem.hunk(project, diff.path, diff.source, hash, hunk)?.let(ask)
                    }
                }
                return@itemsIndexed
            }
            val background = when (line.kind) { '+' -> added; '-' -> removed; else -> Color.Transparent }
            Row(Modifier.fillMaxWidth().background(background).semantics(mergeDescendants = true) {
                contentDescription = when (line.kind) { '+' -> "Added: "; '-' -> "Removed: "; '\\' -> ""; else -> "" } + line.text
            }) {
                Text(line.number?.toString().orEmpty(), Modifier.width(40.dp).padding(end = 6.dp), style = MaterialTheme.typography.labelSmall,
                    fontFamily = FontFamily.Monospace, color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = androidx.compose.ui.text.style.TextAlign.End)
                Text(when (line.kind) { '+' -> "+"; '-' -> "−"; else -> " " }, Modifier.width(14.dp), fontFamily = FontFamily.Monospace, fontSize = 12.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text(line.text, Modifier.weight(1f).padding(end = 8.dp), fontFamily = FontFamily.Monospace, fontSize = 12.sp, lineHeight = 17.sp,
                    fontStyle = if (line.kind == '\\') androidx.compose.ui.text.font.FontStyle.Italic else null)
            }
        }
        if (diff.truncated) item { Note("This change is long, so only its start is shown. See the rest on your computer.", Modifier.padding(16.dp)) }
    }
}

// Branches -------------------------------------------------------------------------------------------------------

@Composable
private fun BranchesTab(project: SourceProject, history: Boolean) {
    LazyColumn(Modifier.fillMaxSize().testTag("sourceBranches"), contentPadding = PaddingValues(bottom = 16.dp)) {
        sectionTitle("Branches${if (project.branches.isNotEmpty()) " · ${project.branches.size}" else ""}")
        if (project.branches.isEmpty()) item { Note(if (history) "No local branches yet." else SourceControl.UPDATE_FOR_HISTORY, Modifier.padding(horizontal = 16.dp)) }
        // The current branch first, then ones checked out in a worktree, then the rest as the desktop lists them.
        items(project.branches.sortedBy { if (it.current) 0 else if (it.worktree) 1 else 2 }, key = { "branch:${it.name}" }) { b ->
            ListRow(b.name, SourceControl.trackingText(b), when { b.current -> "Current"; b.worktree -> "Worktree"; else -> "" }, b.current)
        }
        if (project.remotes == 0) item { Note("No remote configured.", Modifier.padding(horizontal = 16.dp, vertical = 4.dp)) }
        sectionTitle("Worktrees · ${project.worktrees.size + project.moreWorktrees}")
        items(project.worktrees, key = { "tree:${it.name}" }) { t ->
            ListRow(t.name, listOfNotNull(if (t.detached) "Detached" else t.branch.ifBlank { null },
                when { t.missing -> "folder missing"; t.changes == null -> null; t.changes == 0 -> "no changes"; else -> "${t.changes} changed" },
                "locked".takeIf { t.locked }).joinToString(" · "), if (t.current) "Current" else "", t.current)
        }
        if (project.moreWorktrees > 0) item { Note("${project.moreWorktrees} more worktrees.", Modifier.padding(horizontal = 16.dp)) }
        item { Box(Modifier.padding(horizontal = 16.dp)) {
            Note("Read-only here. Switch, create, delete, merge and rebase branches, and add or remove worktrees, on your computer.", Modifier.padding(top = 12.dp))
        } }
    }
}

private fun LazyListScope.sectionTitle(title: String) = item(key = "title:$title") {
    Text(title, Modifier.padding(start = 16.dp, end = 16.dp, top = 16.dp, bottom = 4.dp).semantics { heading() }, style = MaterialTheme.typography.titleSmall)
}

@Composable
private fun ListRow(title: String, detail: String, badge: String, current: Boolean) {
    Row(Modifier.fillMaxWidth().heightIn(min = 56.dp).semantics(mergeDescendants = true) {}.padding(horizontal = 16.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Box(Modifier.size(10.dp).background(if (current) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outlineVariant, CircleShape))
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.bodyLarge, fontWeight = if (current) FontWeight.SemiBold else FontWeight.Normal, maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (detail.isNotBlank()) Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2, overflow = TextOverflow.Ellipsis)
        }
        if (badge.isNotBlank()) Pill(badge, if (current) Tone.LOCAL else Tone.PLAIN, small = true)
    }
}
