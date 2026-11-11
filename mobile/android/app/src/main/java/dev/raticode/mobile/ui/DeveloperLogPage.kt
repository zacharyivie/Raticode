package dev.raticode.mobile.ui

import androidx.activity.compose.BackHandler
import androidx.annotation.RequiresApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectDragGesturesAfterLongPress
import androidx.compose.foundation.gestures.scrollBy
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.*
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.raticode.mobile.data.HandoffFiles
import dev.raticode.mobile.live.DESKTOP_OFF
import dev.raticode.mobile.live.DeveloperLog
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.live.LogEntry
import dev.raticode.mobile.live.LogGroup
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.io.File

/** Level colors with a text label beside each, so color is never the only cue. */
@Composable
internal fun levelColor(level: String): Color {
    val dark = MaterialTheme.colorScheme.background.luminance() < 0.5f
    return when (level) {
        "error" -> if (dark) Color(0xFFFF8A80) else Color(0xFFB42332)
        "warn" -> if (dark) Color(0xFFFFC46B) else Color(0xFF8A4B04)
        "debug" -> MaterialTheme.colorScheme.outline
        else -> if (dark) Color(0xFF8AB4F8) else Color(0xFF2F5FB3)
    }
}

/** A row of the list: a single entry or a repeated message, or one entry of a repeated message that is open. */
private data class LogRowItem(val key: String, val entries: List<LogEntry>, val group: LogGroup? = null, val member: Boolean = false) {
    val entry get() = entries.first()
}

/**
 * The desktop's application log, live while this page is open, with this phone's own connection events.
 * The same message repeated in a row shows once with a count, and a tap opens it to show each entry.
 * Long-press a row to select it; keep holding and drag to select a range. Copy puts the selection on the clipboard and
 * Ask Rem attaches it to Rem's message box without sending anything. Selecting a repeated message takes in every entry.
 */
@RequiresApi(29)
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun DeveloperLogPage(client: LiveClient, onBack: (() -> Unit)?, onAskRem: (File, String) -> Unit) {
    DisposableEffect(Unit) { client.openDesktop(DeveloperLog.OP); onDispose { client.openDesktop(null) } }
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var levels by rememberSaveable { mutableStateOf(client.developerLogLevels.toList()) }
    var source by rememberSaveable { mutableStateOf<String?>(null) }
    var query by rememberSaveable { mutableStateOf("") }
    var searching by rememberSaveable { mutableStateOf(false) }
    var paused by rememberSaveable { mutableStateOf(false) }
    // The newest entry when following paused, so new ones are counted instead of moving the list.
    var pausedAt by rememberSaveable { mutableStateOf<String?>(null) }
    var selected by rememberSaveable { mutableStateOf(listOf<String>()) }
    var expanded by rememberSaveable { mutableStateOf(listOf<String>()) }
    var about by rememberSaveable { mutableStateOf(false) }
    var notice by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(notice) { if (notice != null) { delay(2500); notice = null } }

    val desktop = client.developerLog
    val phone = client.phoneLogEntries
    val all = remember(desktop, phone) { (desktop + phone).sortedWith(compareBy({ it.time }, { it.id })) }
    val levelSet = levels.toSet()
    val frozen = paused && pausedAt != null
    val shownBase = remember(all, frozen, pausedAt) {
        if (!frozen) all else all.filter { it.time <= pausedAt!! }
    }
    // Newest first: on a phone the latest entry belongs at the top, under the filters.
    val shown = remember(shownBase, levelSet, source, query) {
        shownBase.filter { DeveloperLog.matches(it, levelSet, source, query) }.asReversed()
    }
    val groups = remember(shown) { DeveloperLog.groupRepeats(shown) }
    // Each group, then its entries while it is open. Selection is kept by row; a selected group stands for all its entries.
    val rows = remember(groups, expanded) {
        groups.flatMap { group ->
            if (!group.repeated) listOf(LogRowItem(group.id, group.entries))
            else listOf(LogRowItem(group.id, group.entries, group)) +
                if (group.id in expanded) group.entries.map { LogRowItem(it.id, listOf(it), member = true) } else emptyList()
        }
    }
    val waiting = if (frozen) all.count { it.time > pausedAt!! && DeveloperLog.matches(it, levelSet, source, query) } else 0
    val counts = remember(all) { DeveloperLog.counts(all) }
    val chosen = remember(rows, selected) { val keys = selected.toSet(); rows.filter { it.key in keys }.flatMap { it.entries }.distinctBy { it.id } }
    val selecting = selected.isNotEmpty()
    val list = rememberLazyListState()

    fun pause() { if (!paused) { paused = true; pausedAt = all.lastOrNull()?.time ?: "" } }
    fun follow() { paused = false; pausedAt = null; scope.launch { list.scrollToItem(0) } }
    fun clearSelection() { selected = emptyList() }
    fun select(ids: List<String>) {
        // Selecting holds the list still, as on the desktop, so rows don't move under a finger.
        if (ids.isNotEmpty()) pause()
        selected = ids
    }
    fun copy(entries: List<LogEntry>) {
        if (entries.isEmpty()) return
        context.getSystemService(android.content.ClipboardManager::class.java)
            ?.setPrimaryClip(android.content.ClipData.newPlainText("Raticode log", DeveloperLog.selectionText(entries)))
        notice = "Copied ${count(entries.size)}"
    }
    fun askRem(entries: List<LogEntry>) {
        if (entries.isEmpty()) return
        val info = client.developerLogInfo
        val text = DeveloperLog.remAttachment(entries, info?.desktop.orEmpty(), info?.logFile.orEmpty())
        val file = runCatching { HandoffFiles.write(context.cacheDir, text) }.getOrNull()
        if (file == null) { notice = "Couldn't prepare the entries for Rem. Free some phone storage and try again."; return }
        clearSelection()
        onAskRem(file, DeveloperLog.attachmentName(entries.size))
    }

    // Following keeps the newest entry in view; scrolling away from it pauses.
    LaunchedEffect(all.size, frozen) { if (!frozen && list.firstVisibleItemIndex <= 1) list.scrollToItem(0) }
    LaunchedEffect(list.isScrollInProgress) { if (list.isScrollInProgress && list.firstVisibleItemIndex > 2 && !selecting) pause() }
    BackHandler(enabled = selecting) { clearSelection() }

    Column(Modifier.fillMaxSize()) {
        if (selecting) Row(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.secondaryContainer).padding(horizontal = 4.dp, vertical = 4.dp)
            .testTag("logSelectionBar"), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = ::clearSelection) { androidx.compose.material3.Icon(ThreadIcons.Close, "Clear selection") }
            Text("${count(chosen.size)} selected", Modifier.weight(1f).semantics { liveRegion = LiveRegionMode.Polite }, style = MaterialTheme.typography.titleMedium,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
            TextButton(onClick = { select(rows.map { it.key }) }, enabled = rows.any { it.key !in selected },
                modifier = Modifier.semantics { contentDescription = "Select all shown" }) { Text("All") }
            IconButton(onClick = { copy(chosen) }, modifier = Modifier.testTag("logCopy")) { androidx.compose.material3.Icon(ThreadIcons.Copy, "Copy") }
            Button(onClick = { askRem(chosen) }, enabled = client.desktopControl, modifier = Modifier.testTag("logAskRem")) { Text("Ask Rem") }
        } else Header("Developer log", when {
            !client.connected -> "Offline · showing what this phone has"
            frozen -> if (waiting > 0) "Paused · $waiting new" else "Paused"
            else -> "Live from your computer"
        }, onBack) {
            IconButton(onClick = { searching = !searching; if (!searching) query = "" }) {
                androidx.compose.material3.Icon(ThreadIcons.Search, if (searching) "Close search" else "Search the log")
            }
            TextButton(onClick = { if (frozen) follow() else pause() }, modifier = Modifier.testTag("logFollow")) { Text(if (frozen) "Follow" else "Pause") }
        }
        Row(Modifier.fillMaxWidth().horizontalScroll(androidx.compose.foundation.rememberScrollState()).padding(horizontal = 12.dp),
            horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
            DeveloperLog.LEVELS.forEach { (id, label) ->
                val on = id in levelSet
                FilterChip(on, {
                    val next = if (on) levels - id else levels + id
                    if (next.isNotEmpty()) { levels = next; clearSelection(); client.setLogLevels(next.toSet()) }
                }, label = { Text(if (on) "$label ${counts[id] ?: 0}" else label) },
                    leadingIcon = { Box(Modifier.size(8.dp).clip(CircleShape).background(levelColor(id))) },
                    modifier = Modifier.testTag("logLevel-$id").semantics { stateDescription = if (on) "Shown" else "Hidden" })
            }
            val sources = remember(all) { all.map { it.source }.distinct().sortedBy(DeveloperLog::sourceName) }
            ChoiceMenu(source?.let(DeveloperLog::sourceName) ?: "All sources", "Source",
                listOf("" to "All sources") + sources.map { it to DeveloperLog.sourceName(it) }, source.orEmpty(), MENU) { choice ->
                source = choice.ifBlank { null }; clearSelection()
            }
        }
        if (searching) OutlinedTextField(query, { query = it.take(200) }, Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp)
            .testTag("logSearch").semantics { contentDescription = "Search the log" }, placeholder = { Text("Search messages") }, singleLine = true,
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search), shape = MaterialTheme.shapes.large)
        val error = client.desktopErrors[DeveloperLog.OP]
        val problem = when {
            !client.desktopControl && client.connected -> DESKTOP_OFF
            client.desktopControl && client.developerLogSupported == false -> DeveloperLog.UPDATE_DESKTOP
            error != null -> error
            client.developerLogSkipped -> "Some entries were skipped while this phone was away. The full log is on your computer."
            else -> null
        }
        problem?.let { text ->
            Surface(color = MaterialTheme.colorScheme.surfaceVariant, shape = MaterialTheme.shapes.medium,
                modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp)) {
                Row(Modifier.padding(start = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(text, Modifier.weight(1f).padding(vertical = 8.dp).testTag("logProblem"), style = MaterialTheme.typography.bodySmall)
                    if (error != null) TextButton(onClick = client::reloadLog) { Text("Try again") }
                }
            }
        }
        Box(Modifier.weight(1f)) {
            if (shown.isEmpty()) {
                Column(Modifier.fillMaxWidth().padding(24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    if (client.connected && client.desktopControl && client.developerLogSupported != false && error == null && desktop.isEmpty())
                        LinearProgressIndicator(Modifier.fillMaxWidth())
                    Note(if (all.isEmpty()) "No log entries yet." else "No entries match these filters.")
                }
            } else {
                val density = LocalDensity.current
                val edge = with(density) { 56.dp.toPx() }
                var anchor by remember { mutableStateOf<Int?>(null) }
                // Read through these inside the gesture, which must not restart when selecting pauses the list.
                val currentRows by rememberUpdatedState(rows)
                val currentSelected by rememberUpdatedState(selected)
                val selectNow by rememberUpdatedState<(List<String>) -> Unit>({ ids -> select(ids) })
                // Lifting the finger after a long-press also reaches the row as a tap; that tap must not undo the selection.
                val releasing = remember { booleanArrayOf(false) }
                // The row under a finger, by its entry, whatever else the list shows above it.
                fun rowAt(y: Float): Int? {
                    val id = list.layoutInfo.visibleItemsInfo.firstOrNull { y >= it.offset && y < it.offset + it.size }?.key as? String ?: return null
                    return currentRows.indexOfFirst { it.key == id }.takeIf { it >= 0 }
                }
                LazyColumn(Modifier.fillMaxSize().testTag("logList")
                    .semantics { collectionInfo = CollectionInfo(rows.size, 1) }
                    // Long-press selects a row; keep holding and drag to take in the rows between.
                    .pointerInput(Unit) {
                        detectDragGesturesAfterLongPress(
                            onDragStart = { offset -> releasing[0] = true; rowAt(offset.y)?.let { i -> anchor = i; val id = currentRows[i].key
                                selectNow(if (id in currentSelected) currentSelected else currentSelected + id) } },
                            onDragEnd = { anchor = null; releasing[0] = false }, onDragCancel = { anchor = null; releasing[0] = false },
                            onDrag = { change, _ ->
                                change.consume()
                                val y = change.position.y
                                if (y < edge) scope.launch { list.scrollBy(-24f) } else if (y > size.height - edge) scope.launch { list.scrollBy(24f) }
                                val start = anchor ?: return@detectDragGesturesAfterLongPress
                                rowAt(y)?.let { i -> selectNow(currentRows.subList(minOf(start, i), maxOf(start, i) + 1).map { it.key }) }
                            })
                    }, state = list, contentPadding = PaddingValues(bottom = 24.dp)) {
                    if (frozen && waiting > 0) item(key = "waiting") {
                        TextButton(onClick = ::follow, modifier = Modifier.fillMaxWidth()) { Text("Show $waiting new ${if (waiting == 1) "entry" else "entries"}") }
                    }
                    items(rows, key = { it.key }) { row ->
                        val entry = row.entry
                        val isSelected = row.key in selected
                        val open = row.key in expanded
                        val repeats = row.group?.entries?.size ?: 0
                        val expandable = repeats > 0 || entry.extraLines > 0 || entry.truncated
                        val openLabel = if (repeats > 0) (if (open) "Hide each entry" else "Show each entry") else if (open) "Hide lines" else "Show all lines"
                        // Taps toggle while selecting and open repeats and long entries otherwise; the list handles long-press and drag.
                        LogRow(entry, isSelected, open, repeats, row.group?.oldest, row.member, Modifier
                            .clickable(enabled = selecting || expandable,
                                onClickLabel = if (selecting) (if (isSelected) "Deselect" else "Select") else openLabel) {
                                if (releasing[0]) { releasing[0] = false; return@clickable }
                                if (selecting) {
                                    val next = if (isSelected) selected - row.key else selected + row.key
                                    if (next.isEmpty()) clearSelection() else select(next)
                                } else expanded = if (open) expanded - row.key else expanded + row.key
                            }
                            .semantics { if (!selecting) onLongClick("Select") { select(selected + row.key); true } })
                    }
                    item(key = "about") { LogAbout(about) { about = !about } }
                }
            }
            notice?.let { text ->
                Surface(color = MaterialTheme.colorScheme.inverseSurface, shape = MaterialTheme.shapes.medium,
                    modifier = Modifier.align(Alignment.BottomCenter).padding(16.dp).semantics { liveRegion = LiveRegionMode.Polite }) {
                    Text(text, Modifier.padding(horizontal = 16.dp, vertical = 10.dp), color = MaterialTheme.colorScheme.inverseOnSurface)
                }
            }
        }
        if (!selecting && shown.isNotEmpty()) Text("Long-press an entry to select it. Hold and drag to select more. Tap a count to see each repeat.",
            Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp), style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

private fun count(n: Int) = "$n ${if (n == 1) "entry" else "entries"}"

@Composable
private fun LogRow(entry: LogEntry, selected: Boolean, open: Boolean, repeats: Int, oldest: LogEntry?, member: Boolean, modifier: Modifier) {
    val color = levelColor(entry.level)
    val (day, time) = remember(entry.time) { DeveloperLog.timeParts(entry.time) }
    val first = remember(oldest?.time) { oldest?.let { DeveloperLog.timeParts(it.time).second } }
    val background = when {
        selected -> MaterialTheme.colorScheme.primaryContainer
        member -> MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f)
        else -> Color.Transparent
    }
    Row(modifier.fillMaxWidth().background(background).heightIn(min = 48.dp).padding(end = 12.dp)
        .semantics(mergeDescendants = true) {
            this.selected = selected
            val repeated = if (repeats > 0) "Repeated $repeats times from $first to $time. " else ""
            contentDescription = "$repeated${DeveloperLog.levelName(entry.level)} from ${DeveloperLog.sourceName(entry.source)} at $time${if (day.isNotBlank()) " on $day" else ""}: ${entry.firstLine}"
            if (repeats > 0) stateDescription = if (open) "Showing each entry" else "Collapsed"
        }.testTag(if (member) "logRepeatEntry" else if (repeats > 0) "logRepeat" else "logRow"), verticalAlignment = Alignment.Top) {
        Box(Modifier.width(3.dp).heightIn(min = 48.dp).fillMaxHeight().background(color))
        Column(Modifier.padding(start = if (member) 21.dp else 9.dp, top = 8.dp, bottom = 8.dp).width(84.dp)) {
            if (day.isNotBlank()) Text(day, style = MaterialTheme.typography.labelSmall.copy(fontSize = 10.sp), color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1)
            Text(time, style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace, fontSize = 11.sp),
                color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Clip)
        }
        Column(Modifier.weight(1f).padding(start = 6.dp, top = 8.dp, bottom = 8.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                // The count, like a browser console's; tapping the row opens each entry.
                if (repeats > 0) Surface(shape = CircleShape, color = color.copy(alpha = 0.18f), modifier = Modifier.testTag("logRepeatCount")) {
                    Text("${if (open) "▾" else "▸"} $repeats", Modifier.padding(horizontal = 7.dp),
                        style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold, fontSize = 11.sp), maxLines = 1)
                }
                Text(DeveloperLog.levelName(entry.level).uppercase(), style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold, fontSize = 10.sp, letterSpacing = 0.6.sp),
                    color = color)
                Text(DeveloperLog.sourceName(entry.source) + if (entry.component.isNotBlank() && entry.component != entry.source) " · ${entry.component}" else "",
                    style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            val showAll = open && repeats == 0
            Text(if (showAll) entry.message else entry.firstLine, style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace),
                maxLines = if (showAll) Int.MAX_VALUE else 3, overflow = TextOverflow.Ellipsis)
            if (!showAll && repeats == 0 && entry.extraLines > 0) Text("+${entry.extraLines} ${if (entry.extraLines == 1) "line" else "lines"}",
                style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.primary)
            if (showAll && entry.truncated) Text("Shortened on the phone. The full entry is in the log on your computer.",
                style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        if (selected) androidx.compose.material3.Icon(ThreadIcons.Check, null, Modifier.padding(top = 12.dp).size(18.dp), tint = MaterialTheme.colorScheme.primary)
    }
    HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.6f))
}

@Composable
private fun LogAbout(open: Boolean, onToggle: () -> Unit) {
    Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        TextButton(onClick = onToggle, contentPadding = PaddingValues(0.dp)) { Text(if (open) "Hide what this log includes" else "What this log includes") }
        if (open) listOf(
            "The same log as Settings > Developer on your computer: desktop warnings and errors, backend messages, phone connections, window errors and browser problems. Debug is hidden until you turn it on.",
            "This phone's own connection events appear as “This phone”. They help when the phone can't reach the computer at all, and they stay on this phone until the app closes.",
            "Left out on purpose: message and conversation content, pairing codes, keys and addresses. Tokens and passwords read [redacted]. An error can still quote a file path, so read entries before sharing them.",
            "The same message repeated in a row shows once with a count. Tap it to see each entry with its own time. Selecting it takes in every entry it counts.",
            "Ask Rem attaches the selected entries to Rem's message box and puts the cursor there for your question. Anything you already typed stays, and nothing is sent until you send it.",
            "The phone keeps the newest ${DeveloperLog.MAX_ENTRIES} entries while the app is open, and long entries are shortened. Nothing is saved on the phone.",
        ).forEach { Text("• $it", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
    }
}
