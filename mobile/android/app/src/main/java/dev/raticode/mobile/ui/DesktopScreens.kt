package dev.raticode.mobile.ui

import dev.raticode.mobile.voice.VoiceSetup
import dev.raticode.mobile.voice.AssistantSupport
import androidx.activity.compose.BackHandler
import androidx.annotation.RequiresApi
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.saveable.rememberSaveableStateHolder
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.layout.layout
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.live.*
import org.json.JSONArray
import org.json.JSONObject
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle

/** Top-level pages for desktop features. The desktop carries out every action shown here. */

@Composable
internal fun Header(title: String, subtitle: String = "", onBack: (() -> Unit)? = null, backLabel: String = "Back",
                    actions: @Composable RowScope.() -> Unit = {}) {
    Row(Modifier.fillMaxWidth().padding(start = if (onBack == null) 20.dp else 8.dp, end = 8.dp, top = 8.dp, bottom = 4.dp),
        verticalAlignment = Alignment.CenterVertically) {
        if (onBack != null) IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, backLabel) }
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.titleLarge, maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.semantics { heading() })
            if (subtitle.isNotBlank()) Text(subtitle, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        actions()
    }
}

@Composable
internal fun Note(text: String, modifier: Modifier = Modifier) =
    Text(text, modifier, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)

/** Shows why a desktop screen is empty: offline, not allowed, failed or loading. */
@RequiresApi(29)
@Composable
internal fun DesktopGate(client: LiveClient, key: String, loading: String, content: @Composable () -> Unit) {
    val error = client.desktopErrors[key]
    val loaded = client.desktopViews.containsKey(key)
    when {
        !client.connected && !loaded -> EmptyState("Not connected", "Connect to your desktop to continue.")
        !client.desktopControl -> EmptyState("Desktop control is off", DESKTOP_OFF)
        !loaded && error != null -> Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
            EmptyState("Couldn't load this", error); OutlinedButton(onClick = client::refreshDesktop) { Text("Try again") }
        }
        !loaded -> Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            LinearProgressIndicator(Modifier.fillMaxWidth()); Note(loading)
        }
        else -> content()
    }
}

internal fun whenText(value: String, past: Boolean = false): String {
    val time = runCatching { OffsetDateTime.parse(value) }.getOrNull() ?: return value
    val minutes = java.time.Duration.between(java.time.Instant.now(), time.toInstant()).toMinutes()
    return when {
        past && minutes > -1 -> "just now"
        past && minutes > -60 -> "${-minutes} min ago"
        !past && minutes in 0..59 -> "in $minutes min"
        else -> time.atZoneSameInstant(ZoneId.systemDefault()).format(DateTimeFormatter.ofLocalizedDateTime(FormatStyle.MEDIUM, FormatStyle.SHORT))
    }
}

/** A run state in the desktop's words ("Working", "Didn't finish", "Stopped"). */
internal fun runLabel(state: String) = RemTaskStatus.run(state).label

/** Matches the desktop's task status line. */
internal fun remActivity(task: RemTask): String = RemTaskStatus.activity(task).label

/** Why a finished one-time task is missing from the task list, and when the desktop removes it if it says. */
internal fun cleanupText(cleanupAt: String): String {
    val base = "This one-time task finished, so it's no longer in the task list. The chat keeps its link."
    if (cleanupAt.isBlank()) return base
    val time = whenText(cleanupAt)
    return "$base Your desktop removes it ${if (time.startsWith("in ")) time else "on $time"}."
}

private val WEEKDAYS =listOf("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

internal fun scheduleText(schedule: RemSchedule): String {
    val at = if (schedule.localTime.isNotBlank()) " at ${schedule.localTime}" else ""
    return when (schedule.kind) {
        "daily" -> "Every day$at"
        "weekly" -> {
            val days = WEEKDAYS.filter { it in schedule.weekdays }
            when {
                days.size == 7 -> "Every day$at"
                days == WEEKDAYS.take(5) -> "Weekdays$at"
                days.isEmpty() -> "Weekly$at"
                else -> days.joinToString(", ") { it.take(3).replaceFirstChar { c -> c.uppercase() } } + at
            }
        }
        "interval" -> if (schedule.everyMinutes % 60 == 0) "Every ${schedule.everyMinutes / 60} h" else "Every ${schedule.everyMinutes} min"
        "cron" -> "Custom schedule"
        else -> if (schedule.at.isNotBlank()) "Once, ${whenText(schedule.at)}" else "Once, right away"
    }
}

@Composable
internal fun ThreadComposer(value: String, onValue: (String) -> Unit, placeholder: String, enabled: Boolean, tag: String, onSend: () -> Unit) {
    Surface(color = MaterialTheme.colorScheme.surface, shadowElevation = 2.dp) {
        Row(Modifier.fillMaxWidth().padding(8.dp), verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            OutlinedTextField(value, { onValue(it.take(4000)) }, Modifier.weight(1f).testTag(tag).semantics { contentDescription = placeholder },
                placeholder = { Text(placeholder) }, shape = MaterialTheme.shapes.large, maxLines = 4)
            FilledIconButton(onClick = onSend, enabled = enabled && value.isNotBlank(), shape = MaterialTheme.shapes.medium,
                modifier = Modifier.size(48.dp).semantics { contentDescription = "Send" }) { Icon(ThreadIcons.Send, null, Modifier.size(20.dp)) }
        }
    }
}

// Rem -----------------------------------------------------------------------

@RequiresApi(29)
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun RemPage(client: LiveClient, handoff: RemHandoff? = null, onHandoffTaken: () -> Unit = {}, onSelfWork: () -> Unit = {},
                     onTicket: ((RemTicket) -> Unit)? = null) {
    // The Rem tab opens to the chat. Tapping Rem's status opens the task list as its own screen, with a back arrow.
    var nav by rememberSaveable(stateSaver = RemNav.Saver) { mutableStateOf(RemNav()) }
    var creating by rememberSaveable { mutableStateOf(false) }
    // The chat keeps its draft, reply, scroll position and attached files while tasks, a task, a thread or memories are open.
    val saved = rememberSaveableStateHolder()
    // Files handed over for the message box open the chat, where the message box is.
    LaunchedEffect(handoff?.id) { if (handoff != null) nav = RemNav() }
    val (op, params) = when {
        nav.memories -> "rem.memories" to JSONObject()
        nav.threadId != null -> "rem.thread" to JSONObject().put("thread_id", nav.threadId)
        nav.taskId != null -> "rem.task" to JSONObject().put("task_id", nav.taskId)
        else -> "rem.home" to JSONObject()
    }
    val key = DesktopViews.key(op, params)
    DisposableEffect(key) { client.openDesktop(op, params); onDispose { client.openDesktop(null) } }
    val chatShown = nav.chatShown
    DisposableEffect(chatShown) { client.showingRemChat(chatShown); onDispose { client.showingRemChat(false) } }
    BackHandler(enabled = !chatShown) { nav = nav.back() }
    val home = client.desktopViews["rem.home"] as? RemHome
    val activities = remember(home?.tasks) { home?.tasks.orEmpty().map { RemTaskStatus.activity(it) { _, _ -> "" } } }
    // As the desktop's status line: a run that ended badly without asking anything is an update, not a request.
    val attention = home?.tasks.orEmpty().zip(activities).count { (task, it) -> it.kind == RemTaskStatus.Kind.ATTENTION && task.lastRun?.needsUser != false }
    val working = activities.count { it.kind == RemTaskStatus.Kind.RUNNING || it.kind == RemTaskStatus.Kind.QUEUED }
    val listed = home?.let { RemTaskList.shown(it.tasks).size }
    // Ticket keys in the chat, on task cards and in a task's details show as pills that open the global board.
    val taskTickets = nav.taskId?.let { (client.desktopViews["rem.task:${it}"] as? RemTask)?.ticketIndex }.orEmpty()
    CompositionLocalProvider(LocalRemTickets provides (client.remEarlierTickets + home?.tickets.orEmpty() + taskTickets), LocalTicketOpener provides onTicket) {
    Column(Modifier.fillMaxSize()) {
        when {
            nav.memories -> RemMemoryScreen(client) { nav = nav.back() }
            nav.threadId != null -> RemThreadView(client, nav.threadId!!) { nav = nav.back() }
            nav.taskId != null -> RemTaskView(client, nav.taskId!!, onBack = { nav = nav.back() }, onThread = { nav = nav.copy(threadId = it) })
            nav.tasks -> {
                // The groups below carry their own counts, so the subtitle only says how many tasks there are.
                Header("Tasks", RemNav.tasksCount(listed), onBack = { nav = nav.back() }, backLabel = "Back to chat") {
                    TextButton(onClick = { creating = true }, enabled = client.desktopControl && client.connected) { Text("New task") }
                }
                if (client.kanban == true) SelfWorkBanner(client, onSelfWork)
                Box(Modifier.weight(1f)) {
                    DesktopGate(client, "rem.home", "Loading Rem's tasks from your desktop…") {
                        if (home != null) RemTasks(home) { nav = nav.copy(taskId = it) }
                    }
                }
            }
            else -> saved.SaveableStateProvider("chat") {
                var draft by rememberSaveable { mutableStateOf("") }
                // The message the next one replies to, kept with the draft.
                var replyTo by rememberSaveable(stateSaver = ReplyTargetSaver) { mutableStateOf<RemReference?>(null) }
                // Files of a waiting message taken back with Edit, on their way to the message box.
                var restored by remember { mutableStateOf(emptyList<String>()) }
                // Tapping the message box's reply bar scrolls the chat to that message; the count makes a repeat tap count.
                var showMessage by remember { mutableStateOf<Pair<String, Int>?>(null) }
                // Search covers the chat without replacing it, so closing it finds the chat, draft and files as they were.
                var searching by rememberSaveable { mutableStateOf(false) }
                var query by rememberSaveable { mutableStateOf("") }
                var foundMessage by remember { mutableStateOf<Pair<String, Int>?>(null) }
                var foundWords by remember { mutableStateOf(emptyList<String>()) }
                // A result whose earlier messages are still loading; search stays open until they arrive.
                var opening by remember { mutableStateOf<String?>(null) }
                val closeSearch = { searching = false; opening = null; client.clearRemHistoryError() }
                BackHandler(enabled = searching) { closeSearch() }
                // Typing searches after a pause; going offline or reconnecting searches the same words again.
                LaunchedEffect(searching, query, client.connected, home?.search) {
                    if (!searching) return@LaunchedEffect
                    if (query.isNotBlank()) kotlinx.coroutines.delay(RemSearch.DELAY_MS)
                    client.searchRem(query)
                }
                val jump: (String) -> Unit = { id ->
                    foundWords = RemSearch.terms(query)
                    foundMessage = id to (foundMessage?.second ?: 0) + 1
                    searching = false; opening = null
                }
                LaunchedEffect(client.remJump) { client.remJump?.let { (id, _) -> if (id == opening) jump(id) } }
                LaunchedEffect(client.remHistoryBusy) { if (!client.remHistoryBusy && client.remHistoryError.isNotBlank()) opening = null }
                val status = when {
                    !client.connected -> "Not connected"
                    home == null -> "On your desktop"
                    home.turnState == "replying" -> "Thinking…"
                    attention > 0 -> "$attention ${if (attention == 1) "thing needs" else "things need"} you"
                    working > 0 -> "Working on $working ${if (working == 1) "thing" else "things"}"
                    else -> "Here when you need me"
                }
                val (presence, label) = RemRich.presence(client.connected, home, status)
                if (searching) RemSearchBar(query, { query = it }, onSearch = { client.searchRem(query) }, onClose = closeSearch)
                else RemHeader(if (home == null && client.connected) status else label, presence, onOpenTasks = { nav = nav.copy(tasks = true) },
                    tasksLabel = RemNav.tasksCount(listed)) {
                    if (home != null) IconButton(onClick = { searching = true }, modifier = Modifier.testTag("remSearch")) { Icon(ThreadIcons.Search, "Search chat") }
                    // An older desktop doesn't know the memory requests, so the button waits for its flag.
                    if (home?.memories == true && client.desktopControl) TextButton(onClick = { nav = nav.copy(memories = true) }) { Text("Memory") }
                }
                if (client.kanban == true && !searching) SelfWorkBanner(client, onSelfWork)
                Box(Modifier.weight(1f)) {
                    DesktopGate(client, "rem.home", "Loading Rem from your desktop…") {
                        if (home != null) {
                            // A suggested reply sends at once, under the same rules as the Send button, and leaves the draft alone.
                            // The busy check runs at tap time too, so a second tap before the chips redraw doesn't send twice.
                            val canSend = client.connected && client.desktopControl && home.turnState != "replying" && "rem.home" !in client.desktopBusy && client.remHeld == null
                            val sendFollowUp: (String) -> Unit = { text -> if ("rem.home" !in client.desktopBusy) client.sendRemMessage(text, emptyList()) }
                            RemConversation(client, home, onTask = { nav = nav.copy(taskId = it) }, onThread = { nav = nav.copy(threadId = it) }, onSuggest = { draft = it },
                                onFollowUp = sendFollowUp.takeIf { canSend }, onReply = { target: RemReference -> replyTo = target }.takeIf { client.desktopControl && home.replies }, showRequest = showMessage,
                                foundRequest = foundMessage, foundWords = foundWords,
                                onEditHeld = { held ->
                                    // Words typed meanwhile stay, after the returned message.
                                    draft = listOf(held.text, draft).filter { it.isNotBlank() }.joinToString("\n").take(4000)
                                    held.replyTo?.let { replyTo = it }
                                    restored = held.fileIds
                                })
                        }
                    }
                    if (searching && home != null) RemSearchResults(client.remSearch, opening, if (opening != null || client.remHistoryBusy) "" else client.remHistoryError,
                        onPick = { result ->
                            val loaded = result.id in client.remEarlier.map { it.id } || home.messages.any { it.id == result.id }
                            if (loaded) jump(result.id) else { opening = result.id; client.loadEarlierRem(through = result.id) }
                        },
                        onMore = { client.searchRem(query, more = true) }, onRetry = { client.searchRem(query) })
                }
                // Hidden but kept while searching, so its draft, reply and attached files stay as they were.
                if (client.desktopControl) Box(if (searching) Modifier.hiddenKeepingState() else Modifier) { RemComposer(client, home, draft, replyTo.takeIf { home?.replies == true },
                    onReplyTo = { replyTo = it }, onShowReply = { id -> showMessage = id to (showMessage?.second ?: 0) + 1 },
                    restored = restored, onRestored = { restored = emptyList() }, handoff = handoff, onHandoffTaken = onHandoffTaken) { draft = it } }
            }
        }
    }
    }
    if (creating) NewRemTask(client) { creating = false }
}

/**
 * Rem's tasks grouped as the desktop's sidebar groups them: Working on, Needs you, Coming up, Paused and Done,
 * each task with a glyph that turns while it works and its status in the desktop's colors and words.
 */
@Composable
private fun RemTasks(home: RemHome, onTask: (String) -> Unit) {
    val shown = RemTaskList.shown(home.tasks)
    val groups = RemTaskStatus.groups(shown)
    // Done starts folded, as on the desktop.
    var closed by rememberSaveable { mutableStateOf(listOf("done")) }
    LazyColumn(Modifier.fillMaxSize().testTag("remTasks"), contentPadding = PaddingValues(horizontal = 12.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp)) {
        if (home.tasks.isEmpty()) item { EmptyState("Nothing on Rem's plate yet.", "Ask in the chat, or add a task that runs on a schedule.") }
        else if (shown.isEmpty()) item { EmptyState("Nothing to show.", "Finished one-time tasks stay linked in the chat.") }
        groups.forEach { group ->
            val open = group.id !in closed
            item(key = "group-${group.id}") {
                StatusSectionHeader(group.label, group.tasks.size, open, { closed = if (open) closed + group.id else closed - group.id },
                    Modifier.padding(top = 8.dp).testTag("remTaskGroup-${group.id}"))
            }
            if (open) items(group.tasks, key = { it.first.id }) { (task, activity) -> RemTaskRow(task, activity) { onTask(task.id) } }
        }
    }
}

@Composable
private fun RemTaskRow(task: RemTask, activity: RemTaskStatus.Activity, onClick: () -> Unit) {
    val tone = toneColor(activity.tone)
    val running = activity.kind == RemTaskStatus.Kind.RUNNING || activity.kind == RemTaskStatus.Kind.QUEUED
    Surface(onClick = onClick, shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.surface,
        border = if (running || activity.kind == RemTaskStatus.Kind.ATTENTION) androidx.compose.foundation.BorderStroke(1.dp, tone.copy(alpha = .35f)) else null,
        modifier = Modifier.fillMaxWidth().testTag("remTaskRow").semantics(mergeDescendants = true) {}) {
        Row(Modifier.padding(start = 12.dp, end = 8.dp, top = 10.dp, bottom = 10.dp), verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Box(Modifier.size(34.dp).background(tone.copy(alpha = .12f), androidx.compose.foundation.shape.CircleShape), contentAlignment = Alignment.Center) {
                StatusIcon(taskGlyph(activity, task.type), tone, spinning = activity.spinning, size = 18.dp)
            }
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(task.title, style = MaterialTheme.typography.titleSmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
                Text(activity.label, style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis,
                    color = if (activity.tone == RemTaskStatus.Tone.NEUTRAL) MaterialTheme.colorScheme.onSurfaceVariant else tone,
                    fontWeight = if (running) androidx.compose.ui.text.font.FontWeight.Medium else null)
                Text(listOf(task.type.replaceFirstChar { it.uppercase() }, scheduleText(task.schedule), task.project).filter { it.isNotBlank() }.joinToString(" · "),
                    style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            Icon(ThreadIcons.ChevronRight, null, Modifier.size(16.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/** A quiet, centered empty state: what is missing and what to do about it. */
@Composable
internal fun EmptyState(title: String, hint: String, modifier: Modifier = Modifier) {
    Column(modifier.fillMaxWidth().padding(horizontal = 24.dp, vertical = 32.dp).testTag("emptyState"), horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(title, style = MaterialTheme.typography.titleSmall, textAlign = androidx.compose.ui.text.style.TextAlign.Center)
        if (hint.isNotBlank()) Text(hint, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = androidx.compose.ui.text.style.TextAlign.Center)
    }
}

@RequiresApi(29)
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun RemTaskView(client: LiveClient, id: String, onBack: () -> Unit, onThread: (String) -> Unit) {
    val key = "rem.task:$id"
    val home = client.desktopViews["rem.home"] as? RemHome
    val task = client.desktopViews[key] as? RemTask ?: home?.tasks?.firstOrNull { it.id == id }
    var confirmDelete by remember { mutableStateOf(false) }
    Header(task?.title ?: "Task", task?.let { listOf(it.type.replaceFirstChar { c -> c.uppercase() }, scheduleText(it.schedule)).joinToString(" · ") }.orEmpty(), onBack)
    Box(Modifier.fillMaxSize()) {
        DesktopGate(client, key, "Loading this task…") {
            if (task == null) EmptyState("This task no longer exists.", "Your desktop removed it.")
            else Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                val busy = "rem.home" in client.desktopBusy || !client.connected
                RemTaskStatusCard(task)
                val refs = TicketMentions.taskTickets(task)
                if (refs.isNotEmpty()) SettingsGroup("Tickets") {
                    TicketPillRow(refs, LocalRemTickets.current, "Tickets for this task", limit = 12)
                }
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    val p = JSONObject().put("task_id", task.id)
                    val active = task.activeRun?.takeIf { it.active }
                    // A held task offers to run each prerequisite that didn't succeed again; Start now drops what it waits for.
                    if (active == null) task.dependency?.prerequisites?.filter { it.retryable }?.forEach { item ->
                        OutlinedButton(onClick = { client.desktopAction("rem.task.run", JSONObject().put("task_id", item.taskId)) }, enabled = !busy) { Text("Retry “${item.title}”") }
                    }
                    if (active == null) Button(onClick = { client.desktopAction("rem.task.run", p) }, enabled = !busy) { Text(if (task.dependency != null) "Start now" else "Run now") }
                    else OutlinedButton(onClick = { client.desktopAction("rem.run.cancel", JSONObject().put("run_id", active.id)) }, enabled = !busy) { Text("Stop run") }
                    if (task.state == "paused") OutlinedButton(onClick = { client.desktopAction("rem.task.resume", p.put("expected_revision", task.revision)) }, enabled = !busy) { Text("Resume") }
                    else if (task.schedule.kind != "once") OutlinedButton(onClick = { client.desktopAction("rem.task.pause", p) }, enabled = !busy) { Text("Pause") }
                    task.lastRun?.takeIf { it.attention && task.activeRun == null }?.let { run ->
                        TextButton(onClick = { client.desktopAction("rem.run.acknowledge", JSONObject().put("run_id", run.id)) }, enabled = !busy) { Text("Dismiss") }
                    }
                    TextButton(onClick = { confirmDelete = true }, enabled = !busy, colors = ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.error)) { Text("Delete") }
                }
                task.dependency?.takeIf { it.prerequisites.isNotEmpty() }?.let { dependency ->
                    SettingsGroup("Starts after") {
                        dependency.prerequisites.forEach { item ->
                            val (label, tone) = RemTaskStatus.prerequisite(item)
                            Row(Modifier.fillMaxWidth().testTag("remPrerequisite"), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                Text(item.title, Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium, maxLines = 2, overflow = TextOverflow.Ellipsis)
                                Text(label, style = MaterialTheme.typography.labelMedium,
                                    color = if (tone == RemTaskStatus.Tone.NEUTRAL) MaterialTheme.colorScheme.onSurfaceVariant else toneColor(tone))
                            }
                        }
                        Note("It starts by itself once each of these finishes successfully.")
                    }
                }
                // Which checks failed or stayed unresolved, right under the controls, as on the desktop.
                RemTaskChecks.runToShow(task)?.let { RemTaskChecksGroup(it) }
                SettingsGroup("Runs") {
                    val runs = task.runs.ifEmpty { listOfNotNull(task.activeRun, task.lastRun).distinctBy { it.id } }
                    if (runs.isEmpty()) Note("No runs yet.")
                    runs.forEachIndexed { index, run ->
                        if (index > 0) HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                        RemRunRow(run, onThread)
                    }
                }
                SettingsGroup("Details") {
                    if (task.project.isNotBlank()) Note("Project: ${task.project}")
                    if (task.model.isNotBlank()) Note("Model: ${listOf(task.provider, task.model).filter { it.isNotBlank() }.joinToString(" · ")}")
                    // A task's prompt can run to pages; it folds like a long message.
                    if (task.prompt.isNotBlank()) FoldableText { ChatMarkdown(task.prompt, compact = true) }
                    if (RemTaskList.finishedOnce(task)) Note(cleanupText(task.cleanupAt))
                }
                if (task.criteria.isNotEmpty()) SettingsGroup("Done when") { task.criteria.forEach { Text("• $it", style = MaterialTheme.typography.bodySmall) } }
            }
        }
    }
    if (confirmDelete && task != null) AlertDialog(onDismissRequest = { confirmDelete = false }, title = { Text("Delete ${task.title}?") },
        text = { Text("The desktop stops this task's running work and removes its schedule. Project files and saved reports stay.") },
        confirmButton = { TextButton(onClick = { client.desktopAction("rem.task.delete", JSONObject().put("task_id", task.id)); confirmDelete = false; onBack() }) { Text("Delete task") } },
        dismissButton = { TextButton(onClick = { confirmDelete = false }) { Text("Keep") } })
}

/** The task's state at the top of its page: the same glyph, words and colors as its row, and its run's progress. */
@Composable
private fun RemTaskStatusCard(task: RemTask) {
    val activity = RemTaskStatus.activity(task)
    val tone = toneColor(activity.tone)
    Surface(shape = MaterialTheme.shapes.medium, color = tone.copy(alpha = .08f), border = androidx.compose.foundation.BorderStroke(1.dp, tone.copy(alpha = .3f)),
        modifier = Modifier.fillMaxWidth().testTag("remTaskStatus")) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                StatusIcon(taskGlyph(activity, task.type), tone, spinning = activity.spinning, size = 22.dp)
                Text(activity.label, Modifier.weight(1f), style = MaterialTheme.typography.titleSmall, maxLines = 3, overflow = TextOverflow.Ellipsis,
                    color = if (activity.tone == RemTaskStatus.Tone.NEUTRAL) MaterialTheme.colorScheme.onSurface else tone)
            }
            task.activeRun?.takeIf { it.active }?.let { RemProgress(it) }
        }
    }
}

/** One run: its outcome as a glyph and words, when, a short summary that opens on tap, and its threads. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun RemRunRow(run: RemRun, onThread: (String) -> Unit) {
    val info = RemTaskStatus.run(run)
    val tone = toneColor(info.tone)
    Column(Modifier.testTag("remRunRow"), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            StatusIcon(runGlyph(run.state), tone, spinning = info.spinning, size = 16.dp)
            Text(info.label, style = MaterialTheme.typography.labelLarge, color = if (info.tone == RemTaskStatus.Tone.NEUTRAL) MaterialTheme.colorScheme.onSurface else tone)
            Text(whenText(run.finishedAt.ifBlank { run.createdAt }, past = true), Modifier.weight(1f), style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        if (run.summary.isNotBlank()) ClampedText(run.summary, 3, Modifier.padding(start = 24.dp))
        FlowRow(Modifier.padding(start = 12.dp), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            if (run.threadId.isNotBlank()) TextButton(onClick = { onThread(run.threadId) }) { Text("Open work thread") }
            if (run.verificationThreadId.isNotBlank() && run.verificationThreadId != run.threadId)
                TextButton(onClick = { onThread(run.verificationThreadId) }) { Text("Open review") }
        }
    }
}

/** Plain text kept to [lines] lines, with Show more when it is longer. TalkBack reads all of it either way. */
@Composable
internal fun ClampedText(text: String, lines: Int, modifier: Modifier = Modifier, style: androidx.compose.ui.text.TextStyle = MaterialTheme.typography.bodySmall,
                         color: androidx.compose.ui.graphics.Color = androidx.compose.ui.graphics.Color.Unspecified) {
    var open by rememberSaveable(text) { mutableStateOf(false) }
    var clipped by remember(text) { mutableStateOf(false) }
    Column(modifier) {
        Text(text, Modifier.semantics { contentDescription = text }, style = style, color = color, maxLines = if (open) Int.MAX_VALUE else lines,
            overflow = TextOverflow.Ellipsis, onTextLayout = { if (!open) clipped = it.hasVisualOverflow })
        if (clipped || open) TextButton(onClick = { open = !open }, contentPadding = PaddingValues(horizontal = 0.dp), modifier = Modifier.testTag("clampToggle")) {
            Text(if (open) "Show less" else "Show more", style = MaterialTheme.typography.labelMedium)
        }
    }
}

@RequiresApi(29)
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun NewRemTask(client: LiveClient, onDone: () -> Unit) {
    var title by rememberSaveable { mutableStateOf("") }
    var prompt by rememberSaveable { mutableStateOf("") }
    var type by rememberSaveable { mutableStateOf("errand") }
    var project by rememberSaveable { mutableStateOf<String?>(null) }
    var kind by rememberSaveable { mutableStateOf("once") }
    var time by rememberSaveable { mutableStateOf("09:00") }
    var days by rememberSaveable { mutableStateOf(listOf("monday")) }
    var hours by rememberSaveable { mutableStateOf("24") }
    val validTime = time.matches(Regex("([01]?\\d|2[0-3]):[0-5]\\d"))
    val validHours = hours.toIntOrNull()?.let { it in 1..8760 } == true
    val valid = title.isNotBlank() && prompt.isNotBlank() && (kind !in setOf("daily", "weekly") || validTime) &&
        (kind != "weekly" || days.isNotEmpty()) && (kind != "interval" || validHours)
    AlertDialog(onDismissRequest = onDone, title = { Text("New Rem task") }, text = {
        Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(title, { title = it.take(160) }, Modifier.fillMaxWidth(), label = { Text("Title") }, singleLine = true)
            OutlinedTextField(prompt, { prompt = it.take(8000) }, Modifier.fillMaxWidth(), label = { Text("What should Rem do?") }, minLines = 3, maxLines = 6)
            Text("Type", style = MaterialTheme.typography.titleSmall)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                listOf("errand" to "Errand", "research" to "Research", "develop" to "Develop", "maintain" to "Maintain", "reminder" to "Reminder").forEach { (value, label) ->
                    FilterChip(type == value, { type = value }, label = { Text(label) })
                }
            }
            Text("Project", style = MaterialTheme.typography.titleSmall)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                FilterChip(project == null, { project = null }, label = { Text("Rem chooses") })
                client.projects.forEach { (id, name) -> FilterChip(project == id, { project = id }, label = { Text(name) }) }
            }
            Text("When", style = MaterialTheme.typography.titleSmall)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                listOf("once" to "Now", "daily" to "Daily", "weekly" to "Weekly", "interval" to "Repeat").forEach { (value, label) ->
                    FilterChip(kind == value, { kind = value }, label = { Text(label) })
                }
            }
            if (kind in setOf("daily", "weekly")) OutlinedTextField(time, { time = it.take(5) }, Modifier.fillMaxWidth(), singleLine = true,
                label = { Text("Time on your desktop (HH:MM)") }, isError = !validTime)
            if (kind == "weekly") FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                WEEKDAYS.forEach { day -> FilterChip(day in days, { days = if (day in days) days - day else days + day },
                    label = { Text(day.take(3).replaceFirstChar { it.uppercase() }) }) }
            }
            if (kind == "interval") OutlinedTextField(hours, { hours = it.filter(Char::isDigit).take(4) }, Modifier.fillMaxWidth(), singleLine = true,
                label = { Text("Every how many hours?") }, isError = !validHours)
            Note("Rem runs this on your desktop with its normal approvals.")
        }
    }, confirmButton = { TextButton(enabled = valid && client.connected, onClick = {
        val schedule = JSONObject().put("kind", kind)
        if (kind in setOf("daily", "weekly")) schedule.put("local_time", time).put("timezone", ZoneId.systemDefault().id)
        if (kind == "weekly") schedule.put("weekdays", JSONArray(WEEKDAYS.filter { it in days }))
        if (kind == "interval") schedule.put("every_minutes", (hours.toIntOrNull() ?: 24) * 60)
        client.desktopAction("rem.task.create", JSONObject().put("title", title.trim()).put("type", type).put("prompt", prompt.trim())
            .put("schedule", schedule).apply { project?.let { put("project_id", it) } })
        onDone()
    }) { Text("Add task") } }, dismissButton = { TextButton(onClick = onDone) { Text("Cancel") } })
}

// Organizations -------------------------------------------------------------

private fun statusLabel(status: String) = when (status) {
    "in_progress" -> "In progress"; "in_review" -> "In review"; "todo" -> "To do"
    else -> status.replace('_', ' ').replaceFirstChar { it.uppercase() }
}

/** The task list an older desktop supports, shown under the organization's header until the desktop is updated. */
@RequiresApi(29)
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun ColumnScope.LegacyOrganizationView(client: LiveClient, id: String) {
    val key = "organizations.read:$id"
    val org = client.desktopViews[key] as? Organization
    var filter by rememberSaveable { mutableStateOf("attention") }
    var expanded by rememberSaveable { mutableStateOf<String?>(null) }
    var dialog by remember { mutableStateOf<Pair<String, OrganizationTask?>?>(null) }
    var wake by remember { mutableStateOf<OrganizationEmployee?>(null) }
    val busy = key in client.desktopBusy || !client.connected
    fun task(action: String, task: OrganizationTask?, extra: JSONObject.() -> Unit = {}) = client.desktopAction("organizations.task",
        JSONObject().put("organization_id", id).put("action", action).apply {
            task?.let { put("task_id", it.id).put("expected_revision", it.revision) }; extra()
        })
    Box(Modifier.weight(1f)) {
        DesktopGate(client, key, "Loading this organization…") {
            if (org != null) {
                val needsYou = { t: OrganizationTask -> t.needsApproval || t.needsReview || t.attention || t.status == "blocked" }
                val shown = org.tasks.filter { t -> when (filter) {
                    "attention" -> needsYou(t)
                    "open" -> t.status !in setOf("done", "cancelled")
                    else -> t.status in setOf("done", "cancelled")
                } }.reversed()
                LazyColumn(Modifier.fillMaxSize().testTag("organizationTasks"), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    item {
                        if (org.description.isNotBlank()) Note(org.description)
                        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            FilterChip(filter == "attention", { filter = "attention" }, label = { Text("Needs you (${org.tasks.count(needsYou)})") })
                            FilterChip(filter == "open", { filter = "open" }, label = { Text("Open") })
                            FilterChip(filter == "done", { filter = "done" }, label = { Text("Done") })
                            AssistChip(onClick = { dialog = "create" to null }, enabled = !busy, label = { Text("New task") })
                        }
                    }
                    if (shown.isEmpty()) item { Note(if (filter == "attention") "Nothing needs you right now." else "No tasks here.") }
                    items(shown, key = { it.id }) { t ->
                        val open = expanded == t.id
                        Surface(onClick = { expanded = if (open) null else t.id }, shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.surface, modifier = Modifier.fillMaxWidth()) {
                            Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                                Text(t.title, style = MaterialTheme.typography.titleSmall)
                                // Status as the organization pills say it, never a run-on line of words.
                                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                                    Pill(statusLabel(t.status), when (t.status) {
                                        "blocked" -> OrgTone.BAD; "done" -> OrgTone.DONE; "in_progress" -> OrgTone.RUN; "in_review" -> OrgTone.WAIT; else -> OrgTone.IDLE })
                                    if (t.running) Pill("Running", OrgTone.RUN)
                                    if (t.needsApproval) Pill("Needs approval", OrgTone.WAIT)
                                    if (t.needsReview) Pill("Needs review", OrgTone.WAIT)
                                    if (t.priority in setOf("critical", "high")) Pill(t.priority.replaceFirstChar { it.uppercase() }, if (t.priority == "critical") OrgTone.BAD else OrgTone.WAIT)
                                }
                                if (t.assignee.isNotBlank()) Text(t.assignee, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                if (t.blockReason.isNotBlank()) Text(t.blockReason, style = MaterialTheme.typography.bodySmall, color = orgColors(OrgTone.BAD).first)
                                if (open) {
                                    if (t.description.isNotBlank()) FoldableText { ChatMarkdown(t.description, compact = true) }
                                    if (t.evidence.isNotBlank()) { Text("Evidence", style = MaterialTheme.typography.labelMedium); ClampedText(t.evidence, 6) }
                                    t.comments.takeLast(12).forEach { c ->
                                        Text("${c.actor}${if (c.decision.isNotBlank()) " · ${c.decision}" else ""}", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                        ClampedText(c.body, 5)
                                    }
                                    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                                        if (t.needsApproval) Button(onClick = { task("approve", t) }, enabled = !busy) { Text("Approve") }
                                        if (t.needsReview && !t.running) Button(onClick = { dialog = "review" to t }, enabled = !busy) { Text("Review") }
                                        OutlinedButton(onClick = { dialog = "comment" to t }, enabled = !busy) { Text("Comment") }
                                        if (!t.running && t.status in setOf("done", "cancelled", "blocked")) TextButton(onClick = { task("reopen", t) }, enabled = !busy) { Text("Reopen") }
                                        if (t.status !in setOf("done", "cancelled")) TextButton(onClick = { dialog = "cancel" to t }, enabled = !busy,
                                            colors = ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.error)) { Text("Cancel") }
                                    }
                                }
                            }
                        }
                    }
                    if (org.employees.isNotEmpty()) item {
                        SettingsGroup("Employees") {
                            org.employees.forEach { e ->
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Column(Modifier.weight(1f)) {
                                        Text(e.name, style = MaterialTheme.typography.bodyMedium)
                                        Text(listOf(e.title, e.model, "Paused".takeIf { e.paused }.orEmpty()).filter { it.isNotBlank() }.joinToString(" · "),
                                            style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                    }
                                    TextButton(onClick = { wake = e }, enabled = !busy && !e.paused) { Text("Ask") }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    dialog?.let { (kind, t) -> OrganizationDialog(kind, t, org?.employees.orEmpty(), onDismiss = { dialog = null }) { fields ->
        when (kind) {
            "create" -> task("create", null) { put("title", fields["title"]).put("description", fields["body"]).put("priority", fields["priority"])
                fields["assignee"]?.takeIf { it.isNotBlank() }?.let { put("assignee", it) } }
            "comment" -> task("comment", t) { put("body", fields["body"]) }
            "review" -> task("review", t) { put("decision", fields["decision"]).put("body", fields["body"]) }
            "cancel" -> task("cancel", t)
        }
        dialog = null
    } }
    wake?.let { employee ->
        var message by rememberSaveable { mutableStateOf("") }
        AlertDialog(onDismissRequest = { wake = null }, title = { Text("Ask ${employee.name}") },
            text = { OutlinedTextField(message, { message = it.take(4000) }, Modifier.fillMaxWidth(), label = { Text("What should they look at?") }, minLines = 2, maxLines = 5) },
            confirmButton = { TextButton(enabled = message.isNotBlank(), onClick = {
                client.desktopAction("organizations.task", JSONObject().put("organization_id", id).put("action", "wake")
                    .put("employee_id", employee.id).put("body", message.trim())); wake = null
            }) { Text("Send") } },
            dismissButton = { TextButton(onClick = { wake = null }) { Text("Cancel") } })
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun OrganizationDialog(kind: String, task: OrganizationTask?, employees: List<OrganizationEmployee>, onDismiss: () -> Unit, onConfirm: (Map<String, String>) -> Unit) {
    var title by rememberSaveable { mutableStateOf("") }
    var body by rememberSaveable { mutableStateOf("") }
    var decision by rememberSaveable { mutableStateOf("accept") }
    var priority by rememberSaveable { mutableStateOf("medium") }
    var assignee by rememberSaveable { mutableStateOf("") }
    val valid = when (kind) { "create" -> title.isNotBlank(); "comment", "review" -> body.isNotBlank(); else -> true }
    AlertDialog(onDismissRequest = onDismiss, title = { Text(when (kind) {
        "create" -> "New task"; "comment" -> "Comment"; "review" -> "Review ${task?.title.orEmpty()}"; else -> "Cancel ${task?.title.orEmpty()}?"
    }, maxLines = 2, overflow = TextOverflow.Ellipsis) }, text = {
        Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            if (kind == "cancel") Text("The desktop stops this task and any work started for it.")
            if (kind == "create") OutlinedTextField(title, { title = it.take(500) }, Modifier.fillMaxWidth(), label = { Text("Title") }, singleLine = true)
            if (kind == "review") FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                listOf("accept" to "Accept", "revise" to "Revise", "reject" to "Reject").forEach { (value, label) -> FilterChip(decision == value, { decision = value }, label = { Text(label) }) }
            }
            if (kind != "cancel") OutlinedTextField(body, { body = it.take(8000) }, Modifier.fillMaxWidth(), minLines = 2, maxLines = 6,
                label = { Text(when (kind) { "create" -> "Description (optional)"; "review" -> "Reason"; else -> "Comment" }) })
            if (kind == "create") {
                Text("Priority", style = MaterialTheme.typography.titleSmall)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    listOf("critical", "high", "medium", "low").forEach { value -> FilterChip(priority == value, { priority = value }, label = { Text(value.replaceFirstChar { it.uppercase() }) }) }
                }
                if (employees.isNotEmpty()) {
                    Text("Assign to", style = MaterialTheme.typography.titleSmall)
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        FilterChip(assignee.isBlank(), { assignee = "" }, label = { Text("Unassigned") })
                        employees.forEach { e -> FilterChip(assignee == e.id, { assignee = e.id }, label = { Text(e.name) }) }
                    }
                }
            }
        }
    }, confirmButton = { TextButton(enabled = valid, onClick = {
        onConfirm(mapOf("title" to title.trim(), "body" to body.trim(), "decision" to decision, "priority" to priority, "assignee" to assignee))
    }) { Text(when (kind) { "create" -> "Add task"; "cancel" -> "Cancel task"; else -> "Send" }) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(if (kind == "cancel") "Keep" else "Close") } })
}

// Settings, usage and the More hub -----------------------------------------

private fun count(n: Int, one: String, many: String = one + "s") = "$n ${if (n == 1) one else many}"

private fun numberText(value: Any?): String = (value as? Number)?.toDouble()?.let { if (it % 1.0 == 0.0) it.toLong().toString() else it.toString() } ?: "—"

@RequiresApi(29)
@Composable
internal fun DesktopSettingsPage(client: LiveClient, onBack: (() -> Unit)?, onSelfWork: () -> Unit = {}) {
    DisposableEffect(Unit) { client.openDesktop("settings.get"); onDispose { client.openDesktop(null) } }
    val view = client.desktopViews["settings.get"] as? DesktopSettings
    val busy = "settings.get" in client.desktopBusy || !client.connected
    fun update(changes: JSONObject) = client.desktopAction("settings.update", JSONObject().put("changes", changes))
    Column(Modifier.fillMaxSize()) {
        Header("Desktop settings", if (busy && client.connected) "Saving…" else "Raticode on your computer", onBack)
        Box(Modifier.weight(1f)) {
            DesktopGate(client, "settings.get", "Loading desktop settings…") {
                if (view != null) Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(20.dp)) {
                    if (!view.available) Note("Open Raticode on your computer once to load its settings.")
                    if (view.pending > 0) Note("${view.pending} ${if (view.pending == 1) "change is" else "changes are"} waiting for the Raticode window on your computer.")
                    val groups = view.items.groupBy { it.group }
                    // Self-work's settings live in Rem's backend on the computer; they sit with Rem's other settings.
                    if ("Rem" !in groups) SelfWorkEntry(client, onSelfWork)
                    groups.forEach { (group, items) ->
                        SettingsGroup(group) {
                            items.forEach { item ->
                                val value = view.values[item.key]
                                when (item.kind) {
                                    "bool" -> Row(Modifier.fillMaxWidth().heightIn(min = 48.dp), verticalAlignment = Alignment.CenterVertically) {
                                        Text(item.label, Modifier.weight(1f))
                                        Switch(value == true, { update(JSONObject().put(item.key, it)) }, enabled = view.available && !busy,
                                            modifier = Modifier.semantics { contentDescription = item.label })
                                    }
                                    "number" -> Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                                        Text(item.label, Modifier.weight(1f))
                                        val current = (value as? Number)?.toDouble() ?: item.min
                                        fun step(delta: Double) {
                                            val next = (current + delta).coerceIn(item.min, item.max)
                                            update(JSONObject().put(item.key, if (item.step % 1.0 == 0.0) next.toLong() else next))
                                        }
                                        TextButton(onClick = { step(-item.step) }, enabled = view.available && !busy && current > item.min,
                                            modifier = Modifier.semantics { contentDescription = "Decrease ${item.label}" }) { Text("−") }
                                        Text(numberText(value), style = MaterialTheme.typography.bodyMedium)
                                        TextButton(onClick = { step(item.step) }, enabled = view.available && !busy && current < item.max,
                                            modifier = Modifier.semantics { contentDescription = "Increase ${item.label}" }) { Text("+") }
                                    }
                                    else -> Row(Modifier.fillMaxWidth().heightIn(min = 48.dp), verticalAlignment = Alignment.CenterVertically) {
                                        Text(item.label, Modifier.weight(1f))
                                        ChoiceMenu(item.options.firstOrNull { it.first == value }?.second ?: value?.toString().orEmpty().ifBlank { "Default" }, item.label,
                                            item.options, value?.toString().orEmpty(), Modifier.widthIn(max = 200.dp).width(IntrinsicSize.Max), view.available && !busy) { choice ->
                                            // A new provider starts from its own default model and effort.
                                            update(JSONObject().put(item.key, choice).apply {
                                                if (item.key == "assistant.provider") put("assistant.model", "").put("assistant.effort", "")
                                                if (item.key == "assistant.model") put("assistant.effort", "")
                                            })
                                        }
                                    }
                                }
                            }
                        }
                        if (group == "Rem") SelfWorkEntry(client, onSelfWork)
                    }
                    Note("Agent tools and permissions, browser access, folders and keybindings stay in Settings on your computer.")
                }
            }
        }
    }
}

/** Desktop settings › Rem self-work: the row that opens self-work's own page. */
@RequiresApi(29)
@Composable
private fun SelfWorkEntry(client: LiveClient, onOpen: () -> Unit) {
    val unsupported = client.desktopControl && client.kanban == false
    SettingsGroup("Rem self-work") {
        Surface(onClick = onOpen, enabled = !unsupported, color = androidx.compose.ui.graphics.Color.Transparent, modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp).testTag("selfWorkEntry")) {
            Column(Modifier.padding(vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text("Self-work", style = MaterialTheme.typography.bodyLarge)
                Text(if (unsupported) Kanban.UPDATE_DESKTOP else "What Rem does after 15 quiet minutes: on or off, threads, backlog pulling and its projects",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

@RequiresApi(29)
@Composable
internal fun UsagePage(client: LiveClient, onBack: (() -> Unit)?) {
    DisposableEffect(Unit) { client.openDesktop("usage.overview"); onDispose { client.openDesktop(null) } }
    val usage = client.desktopViews["usage.overview"] as? DesktopUsage
    Column(Modifier.fillMaxSize()) {
        Header("Usage", usage?.let { "Last ${it.days} days" }.orEmpty(), onBack)
        Box(Modifier.weight(1f)) {
            DesktopGate(client, "usage.overview", "Loading provider usage…") {
                if (usage != null) LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                    item {
                        SettingsGroup("Activity") {
                            Text("${usage.calls} calls", style = MaterialTheme.typography.titleMedium)
                            if (usage.inputTokens != null || usage.outputTokens != null)
                                Note("${usage.inputTokens ?: 0} input and ${usage.outputTokens ?: 0} output tokens reported")
                            usage.providers.forEach { Text("${it.provider}: ${it.calls} calls", style = MaterialTheme.typography.bodySmall) }
                        }
                    }
                    items(usage.accounts) { account ->
                        SettingsGroup(account.label) {
                            Text(account.status.replace('_', ' ').replaceFirstChar { it.uppercase() }, style = MaterialTheme.typography.labelMedium)
                            if (account.reason.isNotBlank()) Note(account.reason)
                            account.windows.forEach { window ->
                                Text(window.label, style = MaterialTheme.typography.bodySmall)
                                window.usedPercent?.let { used ->
                                    LinearProgressIndicator({ (used / 100).toFloat().coerceIn(0f, 1f) }, Modifier.fillMaxWidth().semantics { contentDescription = "${used.toInt()} percent used" })
                                }
                                Note(listOfNotNull(window.usedPercent?.let { "${it.toInt()}% used" }, window.resetsAt.takeIf { it.isNotBlank() }?.let { "resets ${whenText(it)}" }).joinToString(" · "))
                            }
                        }
                    }
                }
            }
        }
    }
}

@RequiresApi(29)
@Composable
internal fun MorePage(client: LiveClient, bar: List<String> = LiveNavigation.DEFAULT, onOpen: (String) -> Unit) {
    val context = LocalContext.current
    Column(Modifier.fillMaxSize()) {
        Header("More", if (client.connected) "Encrypted ${client.route}" else "Offline")
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = 20.dp).testTag("moreList")) {
            ConversationRow("Child lock", "", "Stops every touch in Raticode until your phone's screen lock is entered") { dev.raticode.mobile.childlock.ChildLockRuntime.engage(context) }
            val appLock = remember { dev.raticode.mobile.applock.AppLockRuntime.get(context) }
            ConversationRow("App lock", "", if (appLock.enabled) "On. Asks ${appLock.delay.label.replaceFirstChar { it.lowercase() }}" else "Off. Ask for your fingerprint, face or screen lock when Raticode opens") { onOpen(LiveNavigation.APP_LOCK) }
            // Every destination left off the bottom bar stays one tap away here.
            LiveNavigation.omitted(bar).forEach { destination ->
                when (destination.page) {
                    "Workflows" -> WorkflowsRow(client) { onOpen("Workflows") }
                    else -> ConversationRow(destination.title, "", moreRowText(client, destination.page, context)) { onOpen(destination.page) }
                }
            }
            ConversationRow("Connection", "", if (client.connected) "Connected over ${client.route}" else "Reconnect or change the desktop address") { onOpen("Desktop") }
            ConversationRow("Bottom bar", "", "Choose which destinations the bar shows") { onOpen(LiveNavigation.NAV_BAR) }
            ConversationRow("People Rem can reach", "", "Who “brother” or “Mom” means on call and text cards. Kept on this phone") { onOpen(LiveNavigation.PEOPLE) }
            Spacer(Modifier.height(16.dp))
            Note(if (client.desktopControl) "This phone can use Raticode on your computer. Work runs there under its approval policies."
                else DESKTOP_OFF)
        }
    }
}

@RequiresApi(29)
private fun moreRowText(client: LiveClient, page: String, context: android.content.Context) = when (page) {
    "Rem" -> "Chat with Rem and follow its tasks"
    "Threads" -> "Your desktop conversations"
    "Organizations" -> "AI teams that run on your desktop"
    "Kanban" -> if (client.desktopControl && client.kanban == false) Kanban.UPDATE_DESKTOP else "Every ticket, Rem's proposals and its self-work"
    "Swarms" -> "Agent teams in your open projects"
    "SourceControl" -> if (client.desktopControl && client.sourceControl == false) "Update Raticode on your computer to use source control here"
        else "Branches, changes and recent commits"
    "Explorer" -> if (client.desktopControl && client.fileExplorer == false) "Update Raticode on your computer to browse its files here"
        else "Browse and open files in your open projects"
    "Approvals" -> if (client.approvals.isEmpty()) "Workflow and swarm decisions" else "${client.approvals.size} waiting for you"
    "Settings" -> "Appearance, Rem defaults and self-work, editor and terminal"
    "Usage" -> "Provider limits and activity"
    "Developer" -> if (client.desktopControl && client.developerLogSupported == false) DeveloperLog.UPDATE_DESKTOP
        else "Live application log from your computer, with this phone's connection events"
    "Voice" -> voiceRowText(context)
    "Browser" -> "Browse the web on this phone and open your computer's tabs"
    else -> ""
}

private fun voiceRowText(context: android.content.Context) = when (VoiceSetup.support(context)) {
    AssistantSupport.SELECTED -> "Rem is your assistant. Hold power or use the assist gesture"
    AssistantSupport.UNSUPPORTED -> "Needs Android 12 or newer"
    else -> "Talk to Rem with the power button. Set up"
}

/** Voice mode setup on its own page, reached from More and from the launch offer. */
@RequiresApi(29)
@Composable
internal fun VoicePage(client: LiveClient, onBack: (() -> Unit)?) {
    Column(Modifier.fillMaxSize()) {
        Header("Voice assistant", "Talk to Rem from anywhere on this phone", onBack)
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 8.dp).testTag("voicePage"),
            verticalArrangement = Arrangement.spacedBy(12.dp)) { VoiceAssistantSettings(client) }
    }
}

/** Takes no space and draws nothing, while its content keeps its state. */
private fun Modifier.hiddenKeepingState(): Modifier = clearAndSetSemantics {}.layout { measurable, constraints ->
    measurable.measure(constraints)
    layout(0, 0) {}
}
