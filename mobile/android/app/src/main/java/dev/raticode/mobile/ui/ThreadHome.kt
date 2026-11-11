package dev.raticode.mobile.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.annotation.RequiresApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.live.ThreadActivity
import dev.raticode.mobile.live.ThreadEntry
import dev.raticode.mobile.live.ThreadSelection
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle

internal fun permissionLabel(mode: String) = when (mode) {
    "danger-full-access" -> "Full access"
    "workspace-write" -> "Workspace write"
    "read-only" -> "Read only"
    "cli-managed" -> "CLI managed"
    "bypassPermissions" -> "Bypass permissions"
    else -> mode.replaceFirstChar { it.titlecase() }
}

@Composable
internal fun ChoiceMenu(label: String, description: String, choices: List<Pair<String, String>>, selected: String,
                        modifier: Modifier = Modifier, enabled: Boolean = true, onChoose: (String) -> Unit) {
    var expanded by remember { mutableStateOf(false) }
    Box(modifier) {
        TextButton(onClick = { expanded = true }, enabled = enabled && choices.isNotEmpty(),
            colors = ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.onSurface),
            contentPadding = PaddingValues(horizontal = 8.dp), modifier = Modifier.fillMaxWidth()
                .semantics { contentDescription = description }) {
            Text(label, Modifier.weight(1f, fill = false), maxLines = 1, overflow = TextOverflow.Ellipsis,
                style = MaterialTheme.typography.labelMedium)
            Icon(ThreadIcons.ExpandMore, null, Modifier.size(16.dp))
        }
        DropdownMenu(expanded, { expanded = false }) {
            choices.forEach { (value, text) -> DropdownMenuItem(text = { Text(text) }, onClick = {
                expanded = false; onChoose(value)
            }, trailingIcon = { if (selected == value) Icon(Icons.Default.Check, "Selected") }) }
        }
    }
}

@RequiresApi(29)
@Composable
internal fun ModelControls(client: LiveClient, provider: String, model: String, effort: String,
                           enabled: Boolean = true, onChange: (String, String, String) -> Unit) {
    Surface(color = MaterialTheme.colorScheme.surface, shape = RoundedCornerShape(16.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            ChoiceMenu(provider.replaceFirstChar { it.titlecase() }.ifBlank { "Provider" }, "Provider",
                client.providers.keys.map { it to it.replaceFirstChar { c -> c.titlecase() } }, provider,
                Modifier.weight(0.85f), enabled) { p -> onChange(p, client.providers[p]?.firstOrNull().orEmpty(), "") }
            VerticalDivider(Modifier.height(20.dp))
            ChoiceMenu(model.ifBlank { "Model" }, "Model", (client.providers[provider].orEmpty() + listOf(model).filter { it.isNotBlank() }).distinct().map { it to it }, model,
                Modifier.weight(1.35f), enabled) { onChange(provider, it, "") }
            VerticalDivider(Modifier.height(20.dp))
            ChoiceMenu(effort.replaceFirstChar { it.titlecase() }.ifBlank { "Default" }, "Effort",
                listOf("" to "Default") + client.efforts[provider]?.get(model).orEmpty().map { it to it.replaceFirstChar { c -> c.titlecase() } }, effort,
                Modifier.weight(0.8f), enabled) { onChange(provider, model, it) }
        }
    }
}

@RequiresApi(29)
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun ThreadHome(client: LiveClient, onOpen: (String, String?) -> Unit, onDesktop: () -> Unit,
                        onAttachAfterCreation: () -> Unit, onApprovals: () -> Unit = {}) {
    var project by rememberSaveable { mutableStateOf("") }
    var provider by rememberSaveable { mutableStateOf("") }
    var model by rememberSaveable { mutableStateOf("") }
    var effort by rememberSaveable { mutableStateOf("") }
    var permission by rememberSaveable { mutableStateOf("") }
    var runningOpen by rememberSaveable { mutableStateOf(false) }
    var archiveOpen by rememberSaveable { mutableStateOf(false) }
    var archiveCount by rememberSaveable { mutableIntStateOf(10) }
    var search by rememberSaveable { mutableStateOf(false) }
    var query by rememberSaveable { mutableStateOf("") }
    var draft by rememberSaveable { mutableStateOf("") }
    var edited by remember { mutableStateOf(false) }
    val creating = client.creatingThread
    var deleteId by remember { mutableStateOf<String?>(null) }
    // Selected threads, by ID. A long press starts selecting; Back or the close button ends it.
    var selected by rememberSaveable { mutableStateOf(listOf<String>()) }
    var deleteSelection by remember { mutableStateOf<List<String>?>(null) }
    var selectionMenu by remember { mutableStateOf(false) }
    var toolbarMenu by remember { mutableStateOf(false) }
    var speechError by remember { mutableStateOf<String?>(null) }
    var listening by remember { mutableStateOf(false) }
    var dictation by remember { mutableStateOf<OnDeviceDictation?>(null) }
    val context = LocalContext.current
    val latestDraft by rememberUpdatedState(draft)
    fun edit(value: String) { draft = value.take(4000); edited = true; client.draft("new-thread", draft) }
    val microphone = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) {
            dictation?.close()
            dictation = OnDeviceDictation(context, result = { words -> edit(listOf(latestDraft.trimEnd(), words).filter { it.isNotBlank() }.joinToString(" ")) },
                ended = { error -> listening = false; speechError = error })
            listening = dictation?.start() == true
            if (!listening) speechError = "Install an offline speech language in Android settings, or use your keyboard microphone."
        } else speechError = "Microphone access is off. You can still type a message."
    }
    val lifecycle = androidx.lifecycle.compose.LocalLifecycleOwner.current.lifecycle
    DisposableEffect(lifecycle) {
        val observer = androidx.lifecycle.LifecycleEventObserver { _, event ->
            if (event == androidx.lifecycle.Lifecycle.Event.ON_STOP) { dictation?.close(); listening = false }
        }
        lifecycle.addObserver(observer)
        onDispose { lifecycle.removeObserver(observer); dictation?.close() }
    }
    LaunchedEffect(client.drafts, edited) { if (!edited) draft = client.drafts["new-thread"].orEmpty() }
    val template = client.creationTemplates.firstOrNull { client.threadContexts[it.first]?.project == project.takeIf(String::isNotBlank) }
        ?: client.creationTemplates.firstOrNull()
    LaunchedEffect(client.providers, template) {
        if (provider.isBlank()) {
            val source = client.threadContexts[template?.first]
            provider = source?.provider?.takeIf { it in client.providers || client.providers.isEmpty() } ?: client.providers.keys.firstOrNull().orEmpty()
            model = source?.model?.takeIf { it in client.providers[provider].orEmpty() || client.providers.isEmpty() } ?: client.providers[provider]?.firstOrNull().orEmpty()
            effort = source?.effort.orEmpty()
        }
        if (permission.isBlank()) permission = client.threadContexts[template?.first]?.permission
            ?.takeIf { it in client.permissionModes[provider].orEmpty() } ?: client.defaultPermissions[provider].orEmpty()
    }
    LaunchedEffect(query, client.connected) { kotlinx.coroutines.delay(250); client.searchThreads(query) }
    LaunchedEffect(client.requestError) { if (client.requestError != null) { edited = false } }
    val canCreate = template != null && provider.isNotBlank() && model.isNotBlank() && !creating
    fun create(send: Boolean, attach: Boolean = false) {
        if (!canCreate) return
        if (attach) onAttachAfterCreation()
        client.createThread(template.first, project = project.takeIf(String::isNotBlank), provider = provider,
            model = model, effort = effort, permission = permission, firstMessage = if (send) draft else "", draftText = if (attach) draft else "") {
            if (send || attach) { draft = ""; edited = false }
            // Creation remains disabled until its authenticated response navigates to chat.
        }
    }
    // Desktop sections and rules (ThreadSections in desktop App.jsx). Classify every
    // thread first so the scope filter cannot change which group a thread is in.
    val entries = client.threads.map { (id, title) ->
        val info = client.threadContexts[id]
        ThreadEntry(id, title, info?.group ?: "active", info?.running == true, info?.unread == true, info?.updatedAt.orEmpty(), info?.readAt.orEmpty())
    }
    val selection = selected.filter { id -> entries.any { it.id == id } }.toSet()
    val selecting = selection.isNotEmpty() && query.isBlank()
    androidx.activity.compose.BackHandler(enabled = selecting) { selected = emptyList() }
    fun runSelection(action: String) {
        selectionMenu = false
        if (action == "delete") deleteSelection = selection.toList()
        else { client.manageThreads(selection, action); selected = emptyList() }
    }
    BoxWithConstraints(Modifier.fillMaxSize()) {
    val compactTyping = maxHeight < 220.dp
    Column(Modifier.fillMaxSize()) {
        if (selecting) {
            val actions = ThreadSelection.actions(entries.filter { it.id in selection }, client.threadReadSync)
            Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp).testTag("threadSelectionBar"), verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = { selected = emptyList() }) { Icon(ThreadIcons.Close, "Clear selection") }
                Text("${selection.size} selected", Modifier.weight(1f).semantics { liveRegion = LiveRegionMode.Polite },
                    style = MaterialTheme.typography.titleSmall)
                if (actions.any { it.action == "read" }) TextButton(onClick = { runSelection("read") }) {
                    Icon(ThreadIcons.CheckCheck, null, Modifier.size(16.dp)); Spacer(Modifier.width(4.dp)); Text("Mark read")
                }
                if (actions.any { it.action == "archive" }) IconButton(onClick = { runSelection("archive") }) { Icon(ThreadIcons.Archive, "Archive selected threads") }
                Box {
                    IconButton(onClick = { selectionMenu = true }) { Icon(Icons.Default.MoreVert, "More actions for selected threads") }
                    DropdownMenu(selectionMenu, { selectionMenu = false }) {
                        actions.forEach { item -> DropdownMenuItem(onClick = { runSelection(item.action) }, text = {
                            Text(item.label, color = if (item.danger) MaterialTheme.colorScheme.error else Color.Unspecified)
                        }) }
                    }
                }
            }
        } else if (!compactTyping) Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            ChoiceMenu((if (LocalConfiguration.current.screenWidthDp >= 380) "Scoped to " else "") + (client.projects[project] ?: "Global"), "Project scope",
                listOf("" to "Global") + client.projects.map { it.key to it.value }, project, Modifier.weight(1f)) { project = it }
            IconButton(onClick = { create(false) }, enabled = canCreate) { Icon(Icons.Default.Add, "New thread") }
            IconButton(onClick = { search = !search; if (!search) query = "" }) { Icon(Icons.Default.Search, "Search in threads") }
            Box {
                IconButton(onClick = { toolbarMenu = true }) { Icon(Icons.Default.MoreVert, "Thread list options") }
                DropdownMenu(toolbarMenu, { toolbarMenu = false }) {
                    DropdownMenuItem(text = { Text("Refresh") }, enabled = client.connected, onClick = { toolbarMenu = false; client.refresh() })
                    DropdownMenuItem(text = { Text(if (client.approvals.isEmpty()) "Approvals" else "Approvals (${client.approvals.size})") }, onClick = { toolbarMenu = false; onApprovals() })
                    DropdownMenuItem(text = { Text(if (client.connected) "Desktop · Encrypted ${client.route}" else "Desktop · Offline") }, onClick = { toolbarMenu = false; onDesktop() })
                }
            }
        }
        HorizontalDivider()
        if (search) OutlinedTextField(query, { query = it.take(300) }, placeholder = { Text("Search in threads") }, singleLine = true,
            modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp).testTag("threadSearch"))
        LazyColumn(Modifier.weight(1f).fillMaxWidth().testTag("threadList"), contentPadding = PaddingValues(horizontal = 16.dp, vertical = 4.dp)) {
            if (query.isNotBlank()) {
                item { Text(if (client.searching) "Searching saved messages…" else client.searchError.ifBlank { if (client.searchResults.isEmpty()) "No threads match your search." else "${client.searchResults.size} matching threads" },
                    style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(vertical = 8.dp)) }
                items(client.searchResults.filter { project.isBlank() || client.threadContexts[it.thread]?.project == project }, key = { it.thread }) { result ->
                    ThreadRow(client, result.thread, result.title, result.snippet, onOpen = {
                        client.openSearchResult(result); onOpen(result.thread, result.message)
                    }, onDelete = { deleteId = result.thread })
                }
                if (client.searchHasMore) item { TextButton(onClick = { client.searchThreads(query, more = true) }, enabled = !client.searching) { Text("Show more matches") } }
            } else {
                val sections = ThreadActivity.organize(entries)
                fun scoped(list: List<ThreadEntry>) = list.filter { project.isBlank() || client.threadContexts[it.id]?.project == project }
                val pinned = scoped(sections.pinned); val unread = scoped(sections.unread); val read = scoped(sections.read)
                val running = scoped(sections.running); val archivedThreads = scoped(sections.archived)
                fun rows(list: List<ThreadEntry>, archived: Boolean = false) = items(list, key = { it.id }) { entry ->
                    ThreadRow(client, entry.id, entry.title, archived = archived, selected = entry.id in selection, selecting = selecting,
                        onSelect = { selected = ThreadSelection.toggle(selection, entry.id).toList() },
                        onOpen = { onOpen(entry.id, null) }, onDelete = { deleteId = entry.id })
                }
                // Tapping a section's name selects all of its threads, as on desktop.
                fun selectAll(list: List<ThreadEntry>) = { selected = ThreadSelection.section(selection, list.map { it.id }).toList() }
                fun allSelected(list: List<ThreadEntry>) = list.isNotEmpty() && list.all { it.id in selection }
                fun empty(text: String) = item { Text(text, Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 12.dp),
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
                if (pinned.isNotEmpty()) {
                    item(key = "pinned") { ThreadGroupHeader("Pinned", pinned.size, { Icon(ThreadIcons.Pin, null, Modifier.size(12.dp)) },
                        onSelectAll = selectAll(pinned), allSelected = allSelected(pinned)) }
                    rows(pinned)
                }
                if (unread.isNotEmpty()) {
                    item(key = "unread") { ThreadGroupHeader("Unread", unread.size, { UnreadDot() }, onSelectAll = selectAll(unread), allSelected = allSelected(unread)) {
                        TextButton(onClick = { client.manageThreads(unread.map { it.id }, "read") },
                            modifier = Modifier.semantics { contentDescription = "Mark all ${unread.size} unread ${if (unread.size == 1) "thread" else "threads"} as read" }) {
                            Icon(ThreadIcons.CheckCheck, null, Modifier.size(14.dp)); Spacer(Modifier.width(4.dp)); Text("Mark all read", style = MaterialTheme.typography.labelMedium)
                        }
                    } }
                    rows(unread)
                }
                if (read.isNotEmpty() || pinned.size + unread.size == 0) {
                    item(key = "read") { ThreadGroupHeader("Read", read.size, { Icon(ThreadIcons.Check, null, Modifier.size(12.dp)) },
                        description = "Your ${ThreadActivity.READ_LIMIT} most recently read threads. Older ones move to the archive.",
                        onSelectAll = selectAll(read).takeIf { read.isNotEmpty() }, allSelected = allSelected(read)) }
                    rows(read)
                    if (read.isEmpty()) empty(when {
                        client.threads.isEmpty() -> "Desktop threads will appear after sync. Write a message below to start a thread."
                        running.isNotEmpty() || archivedThreads.isNotEmpty() -> "No read threads."
                        else -> "No thread history yet."
                    })
                }
                if (running.isNotEmpty()) {
                    item(key = "running") { ThreadGroupHeader("Running", running.size, {
                        if (client.connected) CircularProgressIndicator(Modifier.size(11.dp), strokeWidth = 1.5.dp)
                        else Icon(ThreadIcons.History, null, Modifier.size(12.dp))
                    }, open = runningOpen, onToggle = { runningOpen = !runningOpen },
                        onSelectAll = { runningOpen = true; selectAll(running)() }, allSelected = allSelected(running)) }
                    if (runningOpen) rows(running)
                }
                item(key = "archived") { ThreadGroupHeader("Archived", archivedThreads.size, { Icon(ThreadIcons.Archive, null, Modifier.size(12.dp)) },
                    open = archiveOpen, onToggle = { archiveOpen = !archiveOpen; archiveCount = 10 },
                    onSelectAll = { archiveOpen = true; selectAll(archivedThreads)() }.takeIf { archivedThreads.isNotEmpty() }, allSelected = allSelected(archivedThreads)) }
                if (archiveOpen) {
                    rows(archivedThreads.take(archiveCount), archived = true)
                    if (archivedThreads.isEmpty()) empty("The archive is empty.")
                    if (archivedThreads.size > archiveCount) item(key = "archive-older") {
                        TextButton(onClick = { archiveCount += 10 }) { Text("Show older threads (${archivedThreads.size - archiveCount})") }
                    }
                    if (archiveCount > 10) item(key = "archive-collapse") { TextButton(onClick = { archiveCount = 10 }) { Text("Collapse older threads") } }
                }
            }
        }
        HorizontalDivider()
        Column(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            if (!compactTyping) ModelControls(client, provider, model, effort, !creating) { p, m, e ->
                if (p != provider) permission = client.defaultPermissions[p].orEmpty()
                provider = p; model = m; effort = e
            }
            if (speechError != null) Text(speechError!!, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
            if (listening) Text("Listening on this phone…", style = MaterialTheme.typography.bodySmall)
            if (template == null) Text(if (client.connected) "Enable thread sync in desktop Paired devices to start threads." else "Connect to desktop to load projects and models.", style = MaterialTheme.typography.bodySmall)
            if (creating) Text(if (client.connected) "Creating thread…" else "Thread queued. Reconnect to desktop to continue.", style = MaterialTheme.typography.bodySmall)
            Surface(shape = RoundedCornerShape(16.dp), color = MaterialTheme.colorScheme.surface,
                border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant)) {
                Column {
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        OutlinedTextField(draft, ::edit, enabled = !creating, placeholder = { Text("Message Rem") },
                            singleLine = compactTyping, minLines = 1, maxLines = if (compactTyping) 1 else 3,
                            modifier = Modifier.weight(1f).testTag("newThreadComposer").semantics { contentDescription = "Message Rem" },
                            colors = OutlinedTextFieldDefaults.colors(focusedBorderColor = androidx.compose.ui.graphics.Color.Transparent, unfocusedBorderColor = androidx.compose.ui.graphics.Color.Transparent))
                        if (compactTyping) FilledIconButton(onClick = { create(true) }, enabled = canCreate && draft.isNotBlank(), shape = RoundedCornerShape(12.dp)) {
                            Icon(ThreadIcons.Send, if (client.connected) "Send message" else "Queue message", Modifier.size(20.dp))
                        }
                    }
                    if (!compactTyping) Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                        IconButton(onClick = { if (listening) { dictation?.close(); listening = false } else microphone.launch(android.Manifest.permission.RECORD_AUDIO) }) { Icon(ThreadIcons.Mic, if (listening) "Stop dictation" else "Dictate") }
                        IconButton(onClick = { create(false, true) }, enabled = canCreate) { Icon(ThreadIcons.AttachFile, "Attach file") }
                        ChoiceMenu(permissionLabel(permission).ifBlank { "Permissions" }, "Permissions", client.permissionModes[provider].orEmpty().map { it to permissionLabel(it) }, permission,
                            Modifier.weight(1f), !creating) { permission = it }
                        FilledIconButton(onClick = { create(true) }, enabled = canCreate && draft.isNotBlank(), shape = RoundedCornerShape(12.dp)) {
                            Icon(ThreadIcons.Send, if (client.connected) "Send message" else "Queue message", Modifier.size(20.dp))
                        }
                    }
                }
            }
        }
    }
    }
    deleteSelection?.let { ids ->
        AlertDialog(onDismissRequest = { deleteSelection = null }, title = { Text("Delete ${ids.size} ${if (ids.size == 1) "thread" else "threads"}?") },
            text = { Text("Delete ${ids.size} ${if (ids.size == 1) "thread" else "threads"} from desktop and this phone? This cannot be undone. Desktop saves any configured archive before finishing deletion.") },
            confirmButton = { TextButton(onClick = { client.manageThreads(ids, "delete"); deleteSelection = null; selected = emptyList() }) { Text("Delete") } },
            dismissButton = { TextButton(onClick = { deleteSelection = null }) { Text("Cancel") } })
    }
    deleteId?.let { id ->
        val info = client.threadContexts[id]
        AlertDialog(onDismissRequest = { deleteId = null }, title = { Text("Delete thread?") },
            text = { Text("Delete \"${client.threads.firstOrNull { it.first == id }?.second.orEmpty()}\" from desktop and this phone? This cannot be undone. Desktop saves any configured archive before finishing deletion.") },
            confirmButton = { TextButton(onClick = { if (info != null) client.manageThread(id, "delete", info.revision); deleteId = null }) { Text("Delete") } },
            dismissButton = { TextButton(onClick = { deleteId = null }) { Text("Cancel") } })
    }
}

// Collapsible groups keep their header visible so their count and status stay readable.
// Tapping the name selects every thread in the group; the chevron opens and closes it.
@Composable
private fun ThreadGroupHeader(label: String, count: Int, icon: @Composable () -> Unit, description: String? = null,
                              open: Boolean? = null, onToggle: (() -> Unit)? = null, onSelectAll: (() -> Unit)? = null,
                              allSelected: Boolean = false, action: (@Composable () -> Unit)? = null) {
    val countText = "$count ${if (count == 1) "thread" else "threads"}"
    val tag = Modifier.testTag("group-${label.lowercase()}")
    Row(Modifier.fillMaxWidth().padding(top = 10.dp, bottom = 2.dp).then(if (onToggle == null) tag else Modifier),
        verticalAlignment = Alignment.CenterVertically) {
        val name = Modifier.weight(1f).heightIn(min = if (onSelectAll != null || onToggle != null) 48.dp else 32.dp)
        val title = if (onSelectAll != null) name.clickable(role = Role.Button, onClickLabel = if (allSelected) "Clear selection" else "Select all $label threads", onClick = onSelectAll)
            .semantics(mergeDescendants = true) { heading(); selected = allSelected; contentDescription = listOfNotNull("$label, $countText", description).joinToString(". ") }
            else name.semantics(mergeDescendants = true) { heading(); contentDescription = listOfNotNull("$label, $countText", description).joinToString(". ") }
        Row(title.padding(horizontal = 8.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Box(Modifier.size(14.dp), contentAlignment = Alignment.Center) { icon() }
            Text(label, style = MaterialTheme.typography.labelLarge, color = if (allSelected) MaterialTheme.colorScheme.primary else Color.Unspecified)
            Surface(shape = RoundedCornerShape(4.dp), color = MaterialTheme.colorScheme.surfaceVariant) {
                Text("$count", Modifier.padding(horizontal = 5.dp), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        action?.invoke()
        if (onToggle != null) IconButton(onClick = onToggle, modifier = tag.semantics { stateDescription = if (open == true) "Expanded" else "Collapsed" }) {
            Icon(ThreadIcons.ChevronRight, if (open == true) "Hide $label threads" else "Show $label threads",
                Modifier.size(14.dp).rotate(if (open == true) 90f else 0f), tint = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

// Desktop's unread marker is a blue dot; the dark theme uses a lighter blue for contrast.
@Composable
private fun UnreadDot() {
    val blue = if (MaterialTheme.colorScheme.background.luminance() < 0.5f) Color(0xFF60A5FA) else Color(0xFF3B82F6)
    Box(Modifier.size(8.dp).background(blue, CircleShape))
}

internal fun threadDate(value: String): String = runCatching {
    DateTimeFormatter.ofLocalizedDateTime(FormatStyle.MEDIUM, FormatStyle.SHORT).withZone(ZoneId.systemDefault()).format(OffsetDateTime.parse(value))
}.getOrDefault("No messages yet")

@RequiresApi(29)
@OptIn(androidx.compose.foundation.ExperimentalFoundationApi::class)
@Composable
private fun ThreadRow(client: LiveClient, id: String, title: String, snippet: String = "", archived: Boolean? = null,
                      selected: Boolean = false, selecting: Boolean = false, onSelect: (() -> Unit)? = null,
                      onOpen: () -> Unit, onDelete: () -> Unit) {
    val info = client.threadContexts[id]
    var menu by remember { mutableStateOf(false) }
    val enabled = info?.canManage == true && !info.deletePending
    val inArchive = archived ?: (info?.group == "archived")
    val date = remember(info?.updatedAt) { threadDate(info?.updatedAt.orEmpty()) }
    val scope = info?.project?.let { client.projects[it] } ?: if (info != null && info.project == null) "Global" else null
    val running = info?.running == true
    val unread = info?.unread == true
    val status = when {
        info?.deletePending == true -> "Deleting on desktop. Waiting for archive."
        running && !client.connected -> listOfNotNull(date, scope, "Running at last sync").joinToString(" · ")
        else -> listOfNotNull(date, scope).joinToString(" · ")
    }
    // A long press selects; while selecting, a tap adds or removes the thread instead of opening it.
    val select = onSelect?.takeIf { enabled }
    Row(Modifier.fillMaxWidth().testTag("thread-$id"), verticalAlignment = Alignment.CenterVertically) {
        Surface(color = if (selected) MaterialTheme.colorScheme.secondaryContainer else MaterialTheme.colorScheme.background,
            modifier = Modifier.weight(1f), shape = RoundedCornerShape(8.dp)) {
            Row(Modifier.combinedClickable(enabled = info?.deletePending != true,
                    onClickLabel = if (selecting) (if (selected) "Deselect thread" else "Select thread") else "Open thread",
                    onLongClickLabel = if (select != null && !selecting) "Select thread" else null,
                    onLongClick = if (select != null && !selecting) select else null,
                    onClick = { if (selecting) select?.invoke() else onOpen() })
                .then(if (selecting) Modifier.semantics { this.selected = selected } else Modifier)
                .padding(horizontal = 8.dp, vertical = 7.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (selecting) Checkbox(checked = selected, onCheckedChange = null, enabled = select != null)
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Text(title, style = MaterialTheme.typography.bodyMedium, fontWeight = if (unread) FontWeight.SemiBold else FontWeight.Normal,
                        maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text(status, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    if (snippet.isNotBlank()) Text(snippet, style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
                }
                // Same indicator as desktop ThreadActivityIndicator: running wins over unread.
                if (running && client.connected) CircularProgressIndicator(Modifier.size(14.dp).semantics { contentDescription = "Running" }, strokeWidth = 1.5.dp)
                else if (unread && !running) Box(Modifier.semantics { contentDescription = "Rem response complete" }) { UnreadDot() }
            }
        }
        // While selecting, actions come from the selection bar instead.
        if (selecting) return@Row
        if (unread && !running) IconButton(onClick = { client.manageThreads(listOf(id), "read") }, enabled = enabled) {
            Icon(ThreadIcons.CheckCheck, "Mark $title as read", Modifier.size(18.dp))
        }
        if (inArchive) IconButton(onClick = onDelete, enabled = enabled) {
            Icon(ThreadIcons.Trash, "Delete thread $title", Modifier.size(18.dp))
        } else IconButton(onClick = { info?.let { client.manageThread(id, "archive", it.revision) } }, enabled = enabled) {
            Icon(ThreadIcons.Archive, "Archive thread $title", Modifier.size(18.dp))
        }
        Box {
            IconButton(onClick = { menu = true }, enabled = enabled) { Icon(Icons.Default.MoreVert, "Thread options for $title", Modifier.size(18.dp)) }
            DropdownMenu(menu, { menu = false }) {
                if (select != null) DropdownMenuItem(text = { Text("Select") }, onClick = { menu = false; select() })
                if (unread) DropdownMenuItem(text = { Text("Mark as read") }, onClick = { menu = false; client.manageThreads(listOf(id), "read") })
                else if (client.threadReadSync) DropdownMenuItem(text = { Text("Mark as unread") }, onClick = { menu = false; client.manageThreads(listOf(id), "unread") })
                DropdownMenuItem(text = { Text(if (info?.group == "pinned") "Unpin thread" else "Pin thread") }, onClick = {
                    menu = false; info?.let { client.manageThread(id, if (it.group == "pinned") "unpin" else "pin", it.revision) }
                })
                // Read-limit overflow is still active on desktop, so there is nothing to restore.
                if (info?.group == "archived") DropdownMenuItem(text = { Text("Restore thread") }, onClick = {
                    menu = false; info?.let { client.manageThread(id, "restore", it.revision) }
                })
                DropdownMenuItem(text = { Text("Delete thread", color = MaterialTheme.colorScheme.error) }, onClick = { menu = false; onDelete() })
            }
        }
    }
}
