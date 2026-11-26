package dev.raticode.mobile.ui

import androidx.activity.compose.BackHandler
import androidx.annotation.RequiresApi
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.*
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.live.*
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

internal object KanbanTabs {
    const val BOARD = "board"
    const val PROPOSED = "proposed"
    const val PEOPLE = "people"
    const val SELF = "self"
}

private val SECTIONS = listOf(KanbanTabs.BOARD to "Board", KanbanTabs.PROPOSED to "Proposed", KanbanTabs.PEOPLE to "People", KanbanTabs.SELF to "Self-work")
private const val NEW = "new"
private const val TICKET = "ticket:"
private const val PERSON = "person:"

private fun columnTone(column: String?) = when (column) {
    "running" -> OrgTone.RUN; "review" -> OrgTone.WAIT; "blocked" -> OrgTone.BAD; "done" -> OrgTone.DONE; else -> OrgTone.IDLE
}

private fun entryTone(tone: String) = when (tone) {
    "bad" -> OrgTone.BAD; "done" -> OrgTone.DONE; "review" -> OrgTone.WAIT; "run" -> OrgTone.RUN; else -> OrgTone.IDLE
}

/**
 * Every organization's tickets and Rem's in one place, what Rem proposes, and Rem's self-work. Each section shows what the
 * desktop last said, and the desktop decides every change.
 */
@RequiresApi(29)
@Composable
internal fun KanbanPage(client: LiveClient, tab: String, onTab: (String) -> Unit, onBack: (() -> Unit)? = null,
                        openTicket: String? = null, onTicketOpened: () -> Unit = {}, onSelfWorkSettings: () -> Unit = {},
                        onOrganization: (String) -> Unit = {}) {
    // An open ticket or the new-ticket form, kept while the user switches apps.
    var top by rememberSaveable { mutableStateOf<String?>(null) }
    // A ticket pill in Rem's chat opens its ticket here; one no longer on the board says so on its page.
    LaunchedEffect(openTicket) { if (openTicket != null) { top = TICKET + openTicket; onTicketOpened() } }
    // The board's filters and column, saved on this phone so they come back after leaving the board or closing the app.
    val context = LocalContext.current
    var saved by remember { mutableStateOf(BoardFilterPrefs.global(context)) }
    fun setFilters(next: GlobalBoardFilters) { if (next != saved) { saved = next; BoardFilterPrefs.save(context, next) } }
    // An open ticket reads its history, which carries the ticket too; older desktops keep reading the board.
    val history = top?.takeIf { it.startsWith(TICKET) && client.kanbanComments == true }?.removePrefix(TICKET)?.let(Kanban::timelineParams)
    val op = if (history != null) Kanban.TIMELINE else if (top != null || tab != KanbanTabs.SELF) Kanban.BOARD else Kanban.SELF_WORK
    val screenKey = history?.let(Kanban::timelineKey) ?: op
    DisposableEffect(screenKey) { client.openDesktop(op, history ?: org.json.JSONObject()); onDispose { client.openDesktop(null) } }
    BackHandler(enabled = top != null) { top = null }
    val busy = op in client.desktopBusy || Kanban.BOARD in client.desktopBusy || !client.connected
    val board = client.desktopViews[Kanban.BOARD] as? KanbanBoard
    // A saved organization, person or project that is gone shows everything for that filter; the others stay.
    val filters = board?.let { BoardFilters.live(saved, it) } ?: saved
    val open = top
    // Moves from the board and a ticket's page say what they did once the board shows them, with Undo where it applies.
    val moves = remember(client) { KanbanMoves { client.desktopAction(Kanban.TICKET, it) } }
    val opened = history?.let { (client.desktopViews[Kanban.timelineKey(it)] as? TicketTimeline)?.ticket }
    Box(Modifier.fillMaxSize()) {
        Column(Modifier.fillMaxSize().testTag("kanbanPage")) {
            when {
                open == NEW -> KanbanNewTicket(client, board, busy) { top = null }
                open.orEmpty().startsWith(PERSON) -> KanbanPersonPage(client, board, open!!.removePrefix(PERSON), busy, onTicket = { top = TICKET + it },
                    onBoard = { p -> val (s, a) = Kanban.boardFilterFor(p); setFilters(filters.copy(scope = s, assignee = a, project = Kanban.ALL, column = "todo")); onTab(KanbanTabs.BOARD); top = null }) { top = null }
                open != null -> KanbanTicketPage(client, board, open.removePrefix(TICKET), busy, moves, onOrganization, onPerson = { top = PERSON + it }, onOpenTicket = { top = TICKET + it }) { top = null }
                else -> {
                    val status = when { !client.connected -> "Offline"; board == null -> "On your computer"
                        else -> "${board.tickets.count(Kanban::onBoard)} tickets, every organization and Rem" }
                    Header("Global board", status, onBack) {
                        TextButton(onClick = { top = NEW }, enabled = board != null && !busy && client.kanban == true, modifier = Modifier.testTag("kanbanNew")) { Text("New") }
                    }
                    val proposed = board?.let { Kanban.proposed(it).size } ?: 0
                    PrimaryScrollableTabRow(SECTIONS.indexOfFirst { it.first == tab }.coerceAtLeast(0), edgePadding = 8.dp, containerColor = MaterialTheme.colorScheme.background, modifier = Modifier.testTag("kanbanTabs")) {
                        SECTIONS.forEach { (value, label) ->
                            Tab(tab == value, { onTab(value) }, modifier = Modifier.testTag("kanbanTab-$value"), text = {
                                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                                    Text(label, maxLines = 1)
                                    if (value == KanbanTabs.PROPOSED && proposed > 0) Badge { Text("$proposed", Modifier.semantics { contentDescription = "$proposed waiting" }) }
                                }
                            })
                        }
                    }
                    KanbanStateStrip(client, op, client.desktopViews.containsKey(op))
                    Box(Modifier.weight(1f)) {
                        if (client.desktopControl && client.kanban == false) KanbanUnsupported(client)
                        else DesktopGate(client, op, if (tab == KanbanTabs.SELF) "Loading Rem's self-work…" else "Loading every ticket…") {
                            when (tab) {
                                KanbanTabs.PROPOSED -> if (board != null) ProposedTab(client, board, busy) { top = TICKET + it }
                                KanbanTabs.PEOPLE -> if (board != null) PeopleTab(client, board) { top = PERSON + it }
                                KanbanTabs.SELF -> SelfWorkContent(client, busy, showSettings = false, onSettings = onSelfWorkSettings,
                                    projectsEnabled = board?.let { Kanban.person(it, Kanban.REM) } != null, onProjects = { top = PERSON + Kanban.REM }) { top = TICKET + it }
                                else -> if (board != null) BoardTab(board, busy, filters, moves, onFilters = ::setFilters, onOpen = { top = TICKET + it }) { top = NEW }
                            }
                        }
                    }
                }
            }
        }
        // One notice for the board and a ticket's page, kept above the page's pinned actions.
        KanbanMoveNotice(moves, listOfNotNull(opened) + board?.tickets.orEmpty(), client.requestError != null,
            Modifier.align(Alignment.BottomCenter).padding(bottom = if (open.orEmpty().startsWith(TICKET)) 72.dp else 8.dp))
    }
}

/** Moves sent from the board or a ticket's page, each kept until the desktop's board shows it went through. */
@Stable
internal class KanbanMoves(private val action: (org.json.JSONObject) -> Unit) {
    var pending by mutableStateOf<KanbanMove?>(null); private set
    fun send(move: KanbanMove) { action(move.params()); pending = move }
    fun move(ticket: KanbanTicket, column: String) = send(KanbanMove(ticket, column))
    fun done(move: KanbanMove) { if (pending == move) pending = null }
    fun clear() { pending = null }
}

/** How long a move may take to show on the board before the phone stops waiting to say what it did. */
private const val MOVE_WAIT_MS = 30_000L

/**
 * Says what a move did once [tickets] show it went through, in the desktop's words, with Undo where the desktop offers it.
 * As on the desktop, a notice with Undo stays about 10 seconds and one without about 4; Android keeps either longer when
 * accessibility settings ask for it. A refused move ([failed]) shows the desktop's reason in its own dialog instead.
 */
@Composable
internal fun KanbanMoveNotice(moves: KanbanMoves, tickets: List<KanbanTicket>, failed: Boolean, modifier: Modifier = Modifier) {
    val host = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    val pending = moves.pending
    LaunchedEffect(failed) { if (failed) moves.clear() }
    // A move the board never shows, such as one made while the phone went offline, is let go quietly.
    LaunchedEffect(pending) { if (pending != null) { delay(MOVE_WAIT_MS); moves.done(pending) } }
    LaunchedEffect(pending, tickets) {
        val move = pending ?: return@LaunchedEffect
        val moved = Kanban.landed(move, tickets) ?: return@LaunchedEffect
        val undo = Kanban.undo(move, moved)
        val text = Kanban.notice(move)
        host.currentSnackbarData?.dismiss()
        scope.launch {
            val result = host.showSnackbar(text, actionLabel = undo?.let { "Undo" }, withDismissAction = true,
                duration = if (undo != null) SnackbarDuration.Long else SnackbarDuration.Short)
            if (result == SnackbarResult.ActionPerformed && undo != null) moves.send(undo)
        }
        moves.done(move)
    }
    SnackbarHost(host, modifier.testTag("kanbanNotice"))
}

/** Says when the page shows older information, because the phone is offline or the last read failed. */
@RequiresApi(29)
@Composable
private fun KanbanStateStrip(client: LiveClient, key: String, loaded: Boolean) {
    val error = client.desktopErrors[key]
    val text = when {
        !client.connected && loaded -> "Offline. This is the last you saw from your computer. Reconnect to make changes."
        client.connected && loaded && error != null -> "Couldn't refresh: $error"
        else -> return
    }
    Surface(color = MaterialTheme.colorScheme.surfaceVariant, modifier = Modifier.fillMaxWidth().testTag("kanbanStale")) {
        Text(text, Modifier.padding(horizontal = 16.dp, vertical = 8.dp), style = MaterialTheme.typography.bodySmall)
    }
}

@RequiresApi(29)
@Composable
private fun KanbanUnsupported(client: LiveClient) {
    Column(Modifier.padding(20.dp).testTag("kanbanUnsupported"), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Note(Kanban.UPDATE_DESKTOP)
        TextButton(onClick = client::checkDesktopFeatures) { Text("Check again") }
    }
}

// Board --------------------------------------------------------------------------------------------------------------

@OptIn(ExperimentalFoundationApi::class)
@RequiresApi(29)
@Composable
private fun BoardTab(board: KanbanBoard, busy: Boolean, filters: GlobalBoardFilters, moves: KanbanMoves,
                     onFilters: (GlobalBoardFilters) -> Unit, onOpen: (String) -> Unit, onNew: () -> Unit) {
    val (scope, assignee, project) = filters
    val pager = rememberPagerState(initialPage = Kanban.COLUMNS.indexOf(filters.column).coerceAtLeast(0)) { Kanban.COLUMNS.size }
    val current by rememberUpdatedState(filters)
    LaunchedEffect(pager.currentPage) { Kanban.COLUMNS[pager.currentPage].let { if (it != current.column) onFilters(current.copy(column = it)) } }
    val tasks = rememberCoroutineScope()
    var moving by remember { mutableStateOf<KanbanTicket?>(null) }
    val shown = remember(board, scope, assignee, project) { Kanban.shown(board, scope, assignee, project) }
    val projects = Kanban.projectFilters(board)
    val scopes = Kanban.scopes(board)
    val people = Kanban.assigneeFilters(board, scope)
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 12.dp, vertical = 4.dp),
            horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
            ChoiceMenu(scopes.firstOrNull { it.first == scope }?.second ?: "Everything", "Whose tickets", scopes, scope, MENU.testTag("kanbanScopeFilter"), active = scope != Kanban.ALL) {
                onFilters(filters.copy(scope = it, assignee = Kanban.ANY)) }
            ChoiceMenu(people.firstOrNull { it.first == assignee }?.second ?: "Anyone", "Assigned to", people, assignee, MENU.testTag("kanbanAssigneeFilter"), active = assignee != Kanban.ANY) {
                onFilters(filters.copy(assignee = it)) }
            if (projects.isNotEmpty()) ChoiceMenu(projects.firstOrNull { it.first == project }?.second ?: "Any project", "Project", projects, project, MENU.testTag("kanbanProjectFilter"),
                active = project != Kanban.ALL) { onFilters(filters.copy(project = it)) }
            if (filters.active > 0) TextButton(onClick = { onFilters(filters.cleared()) }, modifier = Modifier.testTag("kanbanClearFilters")
                .semantics { contentDescription = "Clear filters, ${filters.active} on" }) { Text("Clear filters") }
        }
        // One column at a time; swipe or tap a column's name to move between them.
        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 12.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Kanban.COLUMNS.forEachIndexed { index, name ->
                val n = shown.count { Kanban.column(it) == name }
                FilterChip(pager.currentPage == index, { tasks.launch { pager.animateScrollToPage(index) } },
                    label = { Text("${Kanban.COLUMN_LABELS[name]} $n") }, modifier = Modifier.testTag("kanbanColumn-$name"),
                    leadingIcon = { Box(Modifier.size(8.dp).clip(CircleShape).background(orgColors(columnTone(name)).first)) })
            }
        }
        HorizontalPager(pager, Modifier.weight(1f).testTag("kanbanBoard"), beyondViewportPageCount = 1) { page ->
            val name = Kanban.COLUMNS[page]
            val cards = remember(shown, name) { Kanban.ordered(shown.filter { Kanban.column(it) == name }) }
            LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(start = 12.dp, end = 12.dp, top = 8.dp, bottom = 24.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (cards.isEmpty()) item { Note(when (name) {
                    "running" -> "Nothing is running."
                    "review" -> "Nothing to review."
                    "todo" -> "Nothing to do. Add a ticket or ask Rem to."
                    "backlog" -> "Backlog is empty."
                    else -> "No tickets here."
                }, Modifier.padding(8.dp)) }
                items(cards, key = { it.uid }) { t -> KanbanCard(t, onOpen = { onOpen(t.uid) }, onMove = { moving = t }) }
                if (board.earlier > 0) item { Note("${board.earlier} older tickets are on your computer.", Modifier.padding(8.dp)) }
            }
        }
        Text("Long-press a ticket to move it.", Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp), style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
    moving?.let { t -> KanbanMoveSheet(t, busy, { moves.move(t, it) }) { moving = null } }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun KanbanCard(ticket: KanbanTicket, onOpen: () -> Unit, onMove: (() -> Unit)? = null) {
    val block = Kanban.blockText(ticket)
    val paused = ticket.paused.isNotBlank()
    val stripe = when { paused -> orgColors(OrgTone.WAIT).first; block.isNotBlank() -> orgColors(OrgTone.BAD).first; ticket.running -> orgColors(OrgTone.RUN).first; Kanban.column(ticket) == "review" -> orgColors(OrgTone.WAIT).first; else -> null }
    val shape = RoundedCornerShape(10.dp)
    Surface(shape = shape, color = MaterialTheme.colorScheme.surface, modifier = Modifier.fillMaxWidth().heightIn(min = 72.dp)
        .border(1.dp, MaterialTheme.colorScheme.outlineVariant, shape).clip(shape)
        .combinedClickable(onClickLabel = "Open", onLongClickLabel = onMove?.let { "Move" }, onLongClick = onMove, onClick = onOpen)
        .semantics { onMove?.let { customActions = listOf(CustomAccessibilityAction("Move to another column") { it(); true }) } }.testTag("kanbanTicket")) {
        Row(Modifier.height(IntrinsicSize.Min)) {
            if (stripe != null) Box(Modifier.width(3.dp).fillMaxHeight().background(stripe))
            Column(Modifier.weight(1f).padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    if (ticket.key.isNotBlank()) Text(ticket.key, style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace), color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Pill(ticket.owner, if (ticket.isRem) OrgTone.ACCENT else OrgTone.IDLE, Modifier.weight(1f, fill = false))
                    Spacer(Modifier.weight(1f))
                    if (ticket.priority in setOf("critical", "high")) Pill(ticket.priority.replaceFirstChar { it.uppercase() }, if (ticket.priority == "critical") OrgTone.BAD else OrgTone.WAIT)
                }
                Text(ticket.title, style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Medium), maxLines = 3, overflow = TextOverflow.Ellipsis)
                // Paused isn't stuck: a calm pill in words, and its reason without the red.
                if (paused) Pill(RunPause.label(ticket.paused), OrgTone.WAIT, Modifier.testTag("kanbanPaused"))
                if (ticket.autoApproved) Pill("Auto-approved", OrgTone.IDLE, Modifier.testTag("kanbanCardAutoApproved"))
                if (ticket.proposedOn.isNotBlank()) Pill("From ${ticket.proposedOn}", OrgTone.IDLE, Modifier.testTag("kanbanCardProposedOn"))
                if (ticket.guardrail) Pill(Kanban.GUARDRAIL, OrgTone.IDLE, Modifier.testTag("kanbanCardGuardrail"))
                Kanban.violatesLine(ticket).takeIf { it.isNotBlank() }?.let { Pill(it, OrgTone.BAD, Modifier.testTag("kanbanCardViolates")) }
                if (block.isNotBlank()) Text(block, style = MaterialTheme.typography.bodySmall, color = if (paused) MaterialTheme.colorScheme.onSurfaceVariant else orgColors(OrgTone.BAD).first, maxLines = 2, overflow = TextOverflow.Ellipsis)
                // A cancelled ticket it waits for needs the user; otherwise a quiet line says what comes first.
                if (ticket.waitNotice.isNotBlank()) Text(ticket.waitNotice, Modifier.testTag("kanbanCardWaitNotice"), style = MaterialTheme.typography.bodySmall, color = orgColors(OrgTone.WAIT).first, maxLines = 3, overflow = TextOverflow.Ellipsis)
                else Kanban.waitsLine(ticket).takeIf { it.isNotBlank() }?.let { Text(it, Modifier.testTag("kanbanCardWaits"), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2, overflow = TextOverflow.Ellipsis) }
                // The self-work objective it advances, or that self-work leaves it; the page says more.
                Kanban.objectiveLine(ticket)?.let { line ->
                    Row(Modifier.testTag("kanbanCardObjective"), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                        Icon(ThreadIcons.Target, null, Modifier.size(12.dp), tint = if (line.off) MaterialTheme.colorScheme.onSurfaceVariant else orgColors(OrgTone.ACCENT).first)
                        Text(line.short, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                }
                if (ticket.running) LinearProgressIndicator(Modifier.fillMaxWidth().height(2.dp).semantics { contentDescription = "Running" }, color = orgColors(OrgTone.RUN).first)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.weight(1f)) { AssigneeLine(ticket) }
                    if (ticket.project.isNotBlank()) Text(ticket.project, Modifier.widthIn(max = 140.dp), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
        }
    }
}

@Composable
private fun AssigneeLine(ticket: KanbanTicket) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        if (ticket.assigneeId.isBlank()) Text("Unassigned", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        else {
            val name = ticket.assignee.ifBlank { ticket.assigneeId }
            Avatar(name, ticket.assigneeId, 22.dp)
            Text(name, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@RequiresApi(29)
@Composable
private fun KanbanMoveSheet(ticket: KanbanTicket, busy: Boolean, onMove: (String) -> Unit, onDone: () -> Unit) {
    ModalBottomSheet(onDismissRequest = onDone) {
        Column(Modifier.fillMaxWidth().padding(horizontal = 20.dp).padding(bottom = 24.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text("Move ${ticket.key.ifBlank { "ticket" }}", style = MaterialTheme.typography.titleMedium, modifier = Modifier.semantics { heading() })
            Text(ticket.title, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2, overflow = TextOverflow.Ellipsis)
            Spacer(Modifier.height(8.dp))
            Kanban.COLUMNS.forEach { column ->
                val rule = Kanban.move(ticket, column)
                val label = Kanban.COLUMN_LABELS[column].orEmpty()
                val enabled = !busy && rule is OrganizationModel.Move.Allowed
                Surface(onClick = { onMove(column); onDone() }, enabled = enabled,
                    color = Color.Transparent, modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp).testTag("kanbanMove-$column")) {
                    Row(Modifier.padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        Box(Modifier.size(10.dp).clip(CircleShape).background(orgColors(columnTone(column)).first))
                        Column(Modifier.weight(1f)) {
                            Text(label + if (rule == null) " (here now)" else "", style = MaterialTheme.typography.bodyLarge,
                                color = if (enabled || rule == null) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant)
                            val detail = when (rule) {
                                is OrganizationModel.Move.Refused -> rule.reason
                                is OrganizationModel.Move.Review -> "Accept it in this organization's Issues."
                                is OrganizationModel.Move.Allowed -> when {
                                    rule.reopen -> "Reopens it straight into $label."
                                    column == "backlog" -> "It won't run from Backlog."
                                    column == "running" -> if (ticket.isRem) "Rem starts it now, within its thread limit." else "Next for ${ticket.assignee.ifBlank { "its worker" }}."
                                    column == "done" -> "Marks it done without a run."
                                    else -> "Ready for ${ticket.assignee.ifBlank { "its worker" }}."
                                }
                                null -> ""
                            }
                            if (detail.isNotBlank()) Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }
        }
    }
}

// Proposed -----------------------------------------------------------------------------------------------------------

@RequiresApi(29)
@Composable
private fun ProposedTab(client: LiveClient, board: KanbanBoard, busy: Boolean, onOpen: (String) -> Unit) {
    val proposals = remember(board) { Kanban.proposed(board) }
    var declining by remember { mutableStateOf<KanbanTicket?>(null) }
    LazyColumn(Modifier.fillMaxSize().testTag("kanbanProposed"), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        item { Note("Rem writes these while it works on its own. Nothing here runs until you approve it.") }
        if (proposals.isEmpty()) item { Note("Nothing to review right now.") }
        items(proposals, key = { it.uid }) { t -> ProposalCard(t, busy, onOpen = { onOpen(t.uid) }, onApprove = { client.desktopAction(Kanban.TICKET, Kanban.decideParams(t, true)) }) { declining = t } }
    }
    declining?.let { t -> DeclineDialog(client.connected, t, { p -> client.desktopAction(Kanban.TICKET, p) }) { declining = null } }
}

@Composable
private fun ProposalCard(ticket: KanbanTicket, busy: Boolean, onOpen: (() -> Unit)?, onApprove: () -> Unit, onDecline: () -> Unit) {
    val shape = RoundedCornerShape(10.dp)
    Surface(shape = shape, color = MaterialTheme.colorScheme.surface, modifier = Modifier.fillMaxWidth().border(1.dp, MaterialTheme.colorScheme.outlineVariant, shape).testTag("kanbanProposal")) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Pill(ticket.owner, if (ticket.isRem) OrgTone.ACCENT else OrgTone.IDLE, Modifier.weight(1f, fill = false))
                if (ticket.createdAt.isNotBlank()) Text(whenText(ticket.createdAt, past = true), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            val title: @Composable () -> Unit = { Text(ticket.title, style = MaterialTheme.typography.titleSmall) }
            if (onOpen != null) Surface(onClick = onOpen, color = Color.Transparent, modifier = Modifier.fillMaxWidth().heightIn(min = 32.dp)) { title() } else title()
            if (ticket.proposalReason.isNotBlank()) Text(ticket.proposalReason, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 4, overflow = TextOverflow.Ellipsis)
            if (ticket.duplicateOf.isNotBlank()) Pill("Might duplicate ${ticket.duplicateOf}", OrgTone.WAIT)
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = onDecline, enabled = !busy, modifier = Modifier.weight(1f).heightIn(min = 48.dp).testTag("kanbanDecline")) { Text("Decline") }
                Button(onClick = onApprove, enabled = !busy, modifier = Modifier.weight(1f).heightIn(min = 48.dp).testTag("kanbanApprove")) { Text("Approve") }
            }
        }
    }
}

@RequiresApi(29)
@Composable
private fun DeclineDialog(connected: Boolean, ticket: KanbanTicket, onAction: (org.json.JSONObject) -> Unit, onDone: () -> Unit) {
    var reason by rememberSaveable { mutableStateOf("") }
    AlertDialog(onDismissRequest = onDone, title = { Text("Decline this proposal?") },
        text = { OutlinedTextField(reason, { reason = it.take(1000) }, Modifier.fillMaxWidth().testTag("kanbanDeclineReason"), label = { Text("Why, for Rem (optional)") }, minLines = 2, maxLines = 4) },
        confirmButton = { TextButton(modifier = Modifier.testTag("kanbanDeclineConfirm"), enabled = connected, onClick = { onAction(Kanban.decideParams(ticket, false, reason)); onDone() }) { Text("Decline") } },
        dismissButton = { TextButton(onClick = onDone) { Text("Cancel") } })
}

// One ticket ---------------------------------------------------------------------------------------------------------

@RequiresApi(29)
@Composable
private fun ColumnScope.KanbanTicketPage(client: LiveClient, board: KanbanBoard?, uid: String, busy: Boolean, moves: KanbanMoves, onOrganization: (String) -> Unit,
                                         onPerson: (String) -> Unit, onOpenTicket: (String) -> Unit, onBack: () -> Unit) {
    val timelineKey = Kanban.timelineKey(Kanban.timelineParams(uid))
    val timeline = client.desktopViews[timelineKey] as? TicketTimeline
    // The history read has the newest copy of the ticket; the board may be a few seconds older.
    val ticket = timeline?.ticket?.takeIf { it.uid == uid } ?: board?.tickets?.firstOrNull { it.uid == uid }
    // A change made here answers with the board; read the history again so it shows at once.
    LaunchedEffect(board) { if (client.kanbanComments == true) client.refreshDesktop() }
    Row(Modifier.fillMaxWidth().padding(start = 4.dp, end = 12.dp, top = 4.dp), verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") }
        Text(ticket?.key?.ifBlank { null } ?: "Ticket", Modifier.weight(1f), style = MaterialTheme.typography.labelLarge.copy(fontFamily = FontFamily.Monospace),
            color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1)
        if (ticket != null) Pill(ticket.owner, if (ticket.isRem) OrgTone.ACCENT else OrgTone.IDLE)
    }
    val gate = if (client.kanbanComments == true) timelineKey else Kanban.BOARD
    KanbanStateStrip(client, gate, client.desktopViews.containsKey(gate))
    Box(Modifier.weight(1f)) {
        DesktopGate(client, if (board != null) Kanban.BOARD else gate, "Loading this ticket…") {
            if (ticket == null) Note("This ticket is no longer on the board.", Modifier.padding(20.dp))
            else TicketDetailView(ticket, board, timeline, client.kanbanComments, client.connected, busy,
                onAction = { client.desktopAction(Kanban.TICKET, it) }, onMove = { moves.move(ticket, it) }, onOrganization = onOrganization, onPerson = onPerson,
                onOpenTicket = onOpenTicket)
        }
    }
}

/**
 * One ticket, laid out to be read in seconds: its title and status chips, where it is on the board, what needs attention,
 * who has it, its facts as tiles, the long texts folded to a first line, what it waits for and what waits for it, and its
 * history. The main actions stay pinned at the bottom. [onAction] sends a ticket action's params to the desktop.
 */
@RequiresApi(29)
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun TicketDetailView(ticket: KanbanTicket, board: KanbanBoard?, timeline: TicketTimeline?, historySupported: Boolean?, connected: Boolean, busy: Boolean,
                              onAction: (org.json.JSONObject) -> Unit, onMove: (String) -> Unit = { onAction(Kanban.moveParams(ticket, it)) },
                              onOrganization: (String) -> Unit = {}, onPerson: (String) -> Unit = {}, onOpenTicket: (String) -> Unit = {}) {
    var moving by remember { mutableStateOf(false) }
    var declining by remember { mutableStateOf(false) }
    var handOver by remember { mutableStateOf<Pair<String, String>?>(null) }
    val column = Kanban.column(ticket)
    Column(Modifier.fillMaxSize()) {
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 8.dp).testTag("kanbanTicketPage"),
            verticalArrangement = Arrangement.spacedBy(12.dp)) {
            // Title and the states that matter most, in color.
            Text(ticket.title, style = MaterialTheme.typography.headlineSmall, modifier = Modifier.semantics { heading() }.testTag("kanbanTicketTitle"))
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                when {
                    ticket.proposed -> StatusChip("Proposed", OrgTone.WAIT, Modifier.testTag("kanbanStatusChip"), icon = ThreadIcons.Lightbulb)
                    column != null -> StatusChip(Kanban.COLUMN_LABELS[column].orEmpty(), columnTone(column), Modifier.testTag("kanbanStatusChip"))
                    else -> StatusChip(Kanban.STATUS_LABELS[ticket.status] ?: ticket.status, OrgTone.IDLE, Modifier.testTag("kanbanStatusChip"))
                }
                if (ticket.running) StatusChip("Running", OrgTone.RUN, icon = ThreadIcons.Running)
                if (ticket.paused.isNotBlank()) StatusChip(RunPause.label(ticket.paused), OrgTone.WAIT, icon = ThreadIcons.Stopped)
                if (ticket.priority in setOf("critical", "high")) StatusChip(Kanban.priorityLabel(ticket.priority),
                    if (ticket.priority == "critical") OrgTone.BAD else OrgTone.WAIT, icon = ThreadIcons.Flag)
                if (ticket.autoApproved) InfoChip("Auto-approved", AUTO_APPROVED_NOTE, icon = ThreadIcons.Sparkles, tag = "kanbanAutoApproved")
                if (ticket.proposedOn.isNotBlank()) InfoChip("From ${ticket.proposedOn}", Kanban.proposedOnNote(ticket), icon = ThreadIcons.Computer, tag = "kanbanProposedOn")
                if (ticket.violatesDecisions.isNotEmpty()) StatusChip("Violates decisions", OrgTone.BAD, Modifier.testTag("kanbanViolatesChip"), icon = ThreadIcons.Failed)
            }
            if (!ticket.proposed && column != null) StageStrip(column, Kanban.COLUMN_LABELS)
            if (ticket.running) LinearProgressIndicator(Modifier.fillMaxWidth().height(3.dp).clip(RoundedCornerShape(2.dp)).semantics { contentDescription = "Running" },
                color = orgColors(OrgTone.RUN).first)
            // What needs attention, short and tinted.
            Kanban.blockText(ticket).takeIf { it.isNotBlank() }?.let {
                val paused = ticket.paused.isNotBlank()
                Callout(it, if (paused) OrgTone.WAIT else OrgTone.BAD, Modifier.testTag("kanbanBlock"), icon = if (paused) ThreadIcons.Stopped else ThreadIcons.Failed,
                    title = if (paused) "Paused" else if (ticket.violatesDecisions.isNotEmpty()) "Violates decisions" else "Blocked")
            }
            Kanban.outsideProject(ticket).takeIf { it.isNotBlank() }?.let {
                Callout(it, OrgTone.WAIT, Modifier.testTag("kanbanOutsideProject"), icon = ThreadIcons.Folder, title = "Self-work won't pick this up")
            }
            // Who has it.
            AssigneeCard(ticket, board, busy, onPerson) { choice, label -> if (Kanban.isHandOver(choice)) handOver = choice to label else onAction(Kanban.assignParams(ticket, choice)) }
            // The facts, as tiles.
            FactGrid(ticketFacts(ticket, column, onColumn = if (ticket.proposed) null else ({ moving = true })))
            // Its finish line, one review check per line; shown on proposals too, to judge them before approving.
            ticket.doneWhenLines.takeIf { it.isNotEmpty() }?.let { lines ->
                DetailCard("Done when", Modifier.testTag("kanbanDoneWhen"), icon = ThreadIcons.Succeeded, tone = OrgTone.DONE) {
                    lines.forEach { line ->
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.Top) {
                            Icon(ThreadIcons.Check, null, Modifier.padding(top = 2.dp).size(14.dp), tint = orgColors(OrgTone.DONE).first)
                            Text(line, Modifier.weight(1f), style = MaterialTheme.typography.bodySmall)
                        }
                    }
                }
            }
            // The self-work objective it advances, or why self-work leaves it and how to start it anyway.
            Kanban.objectiveLine(ticket)?.let { line ->
                DetailCard("Objective", Modifier.testTag("kanbanObjective"), icon = ThreadIcons.Target, tone = if (line.off) OrgTone.WAIT else OrgTone.ACCENT) {
                    Text(Kanban.objectivePageText(line), Modifier.testTag("kanbanObjectiveText"), style = MaterialTheme.typography.bodySmall)
                }
            }
            // Why Rem wrote it, folded to its first sentence.
            if ((ticket.autoApproved || ticket.proposed) && ticket.proposalReason.isNotBlank()) {
                DetailCard(if (ticket.proposed) "Why Rem proposes it" else "Why Rem suggests it", Modifier.testTag("kanbanWhy"), icon = ThreadIcons.Lightbulb, tone = OrgTone.ACCENT) {
                    SummaryText(ticket.proposalReason, tag = "kanbanWhyText") { ChatMarkdown(ticket.proposalReason, compact = true) }
                }
            }
            if (ticket.proposed && ticket.duplicateOf.isNotBlank()) Callout("Might duplicate ${ticket.duplicateOf}", OrgTone.WAIT, icon = ThreadIcons.Copy)
            // Both can run long; they fold so everything below stays close.
            if (ticket.summary.isNotBlank()) DetailCard("Last result", Modifier.testTag("kanbanResult"), icon = ThreadIcons.Succeeded, tone = OrgTone.DONE) {
                SummaryText(ticket.summary, tag = "kanbanResultText") { ChatMarkdown(ticket.summary, compact = true) }
            }
            if (ticket.description.isNotBlank()) DetailCard("Details", Modifier.testTag("kanbanDetails"), icon = ThreadIcons.FileText) {
                FoldableText(toggleColor = MaterialTheme.colorScheme.primary) { ChatMarkdown(ticket.description, compact = true) }
            }
            WaitsSection(board, ticket, busy, onAction, onOpenTicket)
            WaitedOnBySection(board, ticket, onOpenTicket)
            DetailCard("History and comments", icon = ThreadIcons.History) {
                TicketHistoryView(ticket.uid, timeline, historySupported, connected, busy, title = false) { onAction(Kanban.commentParams(ticket, it)) }
            }
        }
        // The main actions, always in reach.
        ActionBar {
            if (ticket.proposed) {
                OutlinedButton(onClick = { declining = true }, enabled = !busy, modifier = Modifier.weight(1f).heightIn(min = 48.dp).testTag("kanbanTicketDecline")) { Text("Decline") }
                Button(onClick = { onAction(Kanban.decideParams(ticket, true)) }, enabled = !busy, modifier = Modifier.weight(1f).heightIn(min = 48.dp).testTag("kanbanTicketApprove")) { Text("Approve") }
            } else if (ticket.isRem && ticket.status == "cancelled") {
                // A cancelled ticket has one way back, as on the desktop.
                Button(onClick = { onMove("backlog") }, enabled = !busy, modifier = Modifier.weight(1f).heightIn(min = 48.dp).testTag("kanbanRestore")) {
                    Text("Restore to Backlog", maxLines = 1)
                }
            } else {
                Button(onClick = { moving = true }, enabled = !busy, modifier = Modifier.weight(1f).heightIn(min = 48.dp).testTag("kanbanMoveOpen")
                    .semantics { contentDescription = "Move to another column" }) {
                    Icon(ThreadIcons.Columns, null, Modifier.size(18.dp)); Spacer(Modifier.width(8.dp)); Text("Move", maxLines = 1)
                }
                // Undo on the notice brings it back, so it doesn't ask first.
                if (Kanban.canCancel(ticket)) OutlinedButton(onClick = { onMove("cancelled") }, enabled = !busy,
                    modifier = Modifier.weight(1f).heightIn(min = 48.dp).testTag("kanbanCancelTicket")) {
                    Text("Cancel ticket", maxLines = 1, color = MaterialTheme.colorScheme.error)
                }
                if (!ticket.isRem) OutlinedButton(onClick = { onOrganization(ticket.organizationId) }, modifier = Modifier.weight(1f).heightIn(min = 48.dp).testTag("kanbanOpenOrg")) {
                    Icon(ThreadIcons.Organizations, null, Modifier.size(18.dp)); Spacer(Modifier.width(8.dp))
                    Text("Open ${ticket.organizationName.ifBlank { "organization" }}", maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
        }
    }
    if (moving) KanbanMoveSheet(ticket, busy, onMove) { moving = false }
    if (declining) DeclineDialog(connected, ticket, onAction) { declining = false }
    handOver?.let { (choice, label) ->
        AlertDialog(onDismissRequest = { handOver = null }, title = { Text("Hand this to $label?") },
            text = { Text("The ticket is created there and Rem's copy closes.") },
            confirmButton = { TextButton(modifier = Modifier.testTag("kanbanHandOverConfirm"), enabled = connected, onClick = { onAction(Kanban.assignParams(ticket, choice)); handOver = null }) { Text("Hand over") } },
            dismissButton = { TextButton(onClick = { handOver = null }) { Text("Cancel") } })
    }
}

internal const val AUTO_APPROVED_NOTE = "Approved by self-work, not by you, because auto-approval is on in Settings › Rem self-work. Edit, move or cancel it like any ticket."
internal const val HAND_OVER_LATER = "Rem's ticket can go to an organization while it isn't running or finished."

/** The ticket's facts for [FactGrid]. Tapping the column opens the move sheet through [onColumn]. */
private fun ticketFacts(ticket: KanbanTicket, column: String?, onColumn: (() -> Unit)?): List<Fact> = buildList {
    val label = if (ticket.proposed) "Proposed" else column?.let { Kanban.COLUMN_LABELS[it] } ?: (Kanban.STATUS_LABELS[ticket.status] ?: ticket.status)
    add(Fact(ThreadIcons.Columns, "Column", label, if (ticket.proposed) OrgTone.WAIT else column?.let(::columnTone), "kanbanFactColumn", "Move to another column", onColumn))
    add(Fact(ThreadIcons.Flag, "Priority", Kanban.priorityLabel(ticket.priority.ifBlank { "medium" }),
        when (ticket.priority) { "critical" -> OrgTone.BAD; "high" -> OrgTone.WAIT; else -> null }, "kanbanFactPriority"))
    if (ticket.project.isNotBlank()) add(Fact(ThreadIcons.Folder, "Project", ticket.project, tag = "kanbanFactProject"))
    if (ticket.isRem && ticket.kind.isNotBlank()) add(Fact(ThreadIcons.Tag, "Kind", Kanban.kindLabel(ticket.kind), tag = "kanbanFactKind"))
    if (!ticket.isRem) add(Fact(ThreadIcons.Organizations, "Organization", ticket.organizationName.ifBlank { "Organization" }, tag = "kanbanFactOrg"))
    if (ticket.waitsFor.isNotEmpty()) {
        val (done, all) = Kanban.waitsDone(ticket)
        add(Fact(ThreadIcons.Hourglass, "Waits for", if (done == all) "All $all done" else "$done of $all done", if (done == all) OrgTone.DONE else OrgTone.WAIT, "kanbanFactWaits"))
    }
    val added = listOfNotNull(ticket.createdBy.takeIf { it.isNotBlank() }, ticket.createdAt.takeIf { it.isNotBlank() }?.let { whenText(it, past = true) }).joinToString(" · ")
    if (added.isNotBlank()) add(Fact(ThreadIcons.Recent, "Added", added, tag = "kanbanFactAdded"))
}

/** Who has the ticket, with a way to their projects and, unless it's a proposal, a menu to give it to someone else. */
@Composable
private fun AssigneeCard(ticket: KanbanTicket, board: KanbanBoard?, busy: Boolean, onPerson: (String) -> Unit, onChoose: (String, String) -> Unit) {
    val person = board?.let { Kanban.assignedPerson(it, ticket) }
    DetailCard("Assigned to", Modifier.testTag("kanbanAssignee"), icon = ThreadIcons.User, trailing = {
        if (person != null) TextButton(onClick = { onPerson(person.id) }, modifier = Modifier.heightIn(min = 48.dp).testTag("kanbanOpenPerson")) { Text("Their projects") }
    }) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            val name = ticket.assignee.ifBlank { ticket.assigneeId }
            if (ticket.assigneeId.isBlank()) IconBadge(ThreadIcons.User, OrgTone.IDLE, 36.dp) else Avatar(name, ticket.assigneeId, 36.dp)
            Column(Modifier.weight(1f)) {
                Text(if (ticket.assigneeId.isBlank()) "Unassigned" else name, style = MaterialTheme.typography.bodyLarge.copy(fontWeight = FontWeight.SemiBold), maxLines = 1, overflow = TextOverflow.Ellipsis)
                person?.title?.takeIf { it.isNotBlank() }?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis) }
            }
            if (!ticket.proposed) {
                val choices = board?.let { Kanban.assignChoices(it, ticket) }.orEmpty()
                ChoiceMenu("Change", "Assigned to: ${choices.firstOrNull { it.first == ticket.assigneeId }?.second ?: ticket.assignee.ifBlank { "Unassigned" }}", choices, ticket.assigneeId,
                    MENU.heightIn(min = 48.dp).testTag("kanbanAssign"), enabled = !busy && (!ticket.isRem || Kanban.canHandOver(ticket))) { choice ->
                    if (choice != ticket.assigneeId) onChoose(choice, choices.firstOrNull { it.first == choice }?.second ?: "that organization")
                }
            }
        }
        if (!ticket.proposed && ticket.isRem && !Kanban.canHandOver(ticket)) InfoChip("Why can't I change this?", HAND_OVER_LATER, tag = "kanbanHandOverLater")
    }
}

/** What a ticket waits for as tappable rows with status dots, its notice when one of them was cancelled, and the controls to change the list. */
@RequiresApi(29)
@Composable
private fun WaitsSection(board: KanbanBoard?, ticket: KanbanTicket, busy: Boolean, onAction: (org.json.JSONObject) -> Unit, onOpenTicket: (String) -> Unit) {
    val editable = Kanban.canEditWaits(ticket)
    val choices = if (editable && board != null) Kanban.waitChoices(board, ticket) else emptyList()
    if (ticket.waitsFor.isEmpty() && choices.isEmpty()) return
    val ids = ticket.waitsFor.map { it.id }
    val (done, all) = Kanban.waitsDone(ticket)
    DetailCard("Waits for", Modifier.testTag("kanbanWaits"), icon = ThreadIcons.Hourglass, tone = if (all > 0 && done < all) OrgTone.WAIT else null, trailing = {
        if (all > 0) Pill("$done/$all done", if (done == all) OrgTone.DONE else OrgTone.WAIT)
    }) {
        if (ticket.waitNotice.isNotBlank()) Callout(ticket.waitNotice, OrgTone.WAIT, Modifier.testTag("kanbanWaitNotice"))
        if (ticket.waitsFor.isEmpty()) Note("Nothing yet. Choose a ticket that has to be Done first.")
        ticket.waitsFor.forEach { wait ->
            val open = board?.tickets?.any { it.id == wait.id && it.organizationId == ticket.organizationId } == true
            LinkRow(wait.title, Kanban.STATUS_LABELS[wait.status] ?: if (wait.status == "removed") "Removed" else wait.status, waitTone(wait.status),
                Modifier.testTag("kanbanWait"), key = wait.key, muted = wait.status == "done",
                onClick = if (open) ({ onOpenTicket("${ticket.organizationId}/${wait.id}") }) else null) {
                if (editable) IconButton(onClick = { onAction(Kanban.waitsParams(ticket, ids - wait.id)) }, enabled = !busy,
                    modifier = Modifier.testTag("kanbanWaitRemove")) { Icon(Icons.Filled.Close, "Stop waiting for ${wait.key.ifBlank { wait.title }}", Modifier.size(18.dp)) }
            }
        }
        if (choices.isNotEmpty()) ChoiceMenu(if (ticket.waitsFor.isEmpty()) "Wait for a ticket…" else "Also wait for…", "Wait for another ticket",
            choices.map { it.id to "${it.key} ${it.title}" }, "", Modifier.heightIn(min = 48.dp).testTag("kanbanWaitAdd"), enabled = !busy) { id ->
            onAction(Kanban.waitsParams(ticket, ids + id))
        }
        if (ticket.waitsFor.isNotEmpty() && ticket.isRem) InfoChip("How waiting works", "Self-work starts this only after every ticket here is Done. You can still start it yourself.",
            tag = "kanbanWaitsInfo")
    }
}

/** Tickets on the board that wait for this one, so the user sees what finishing it unblocks. */
@Composable
private fun WaitedOnBySection(board: KanbanBoard?, ticket: KanbanTicket, onOpenTicket: (String) -> Unit) {
    val waiting = board?.let { Kanban.waitedOnBy(it, ticket) }.orEmpty()
    if (waiting.isEmpty()) return
    DetailCard("Waited on by", Modifier.testTag("kanbanWaitedOnBy"), icon = ThreadIcons.Link, trailing = { Pill("${waiting.size}", OrgTone.IDLE) }) {
        waiting.forEach { t ->
            val col = Kanban.column(t)
            LinkRow(t.title, col?.let { Kanban.COLUMN_LABELS[it] } ?: (Kanban.STATUS_LABELS[t.status] ?: t.status), columnTone(col), Modifier.testTag("kanbanWaitedOn"),
                key = t.key, muted = t.status == "done", onClick = { onOpenTicket(t.uid) })
        }
    }
}

private fun waitTone(status: String) = when (status) { "done" -> OrgTone.DONE; "cancelled", "removed" -> OrgTone.WAIT; "blocked" -> OrgTone.BAD
    "in_progress" -> OrgTone.RUN; "in_review" -> OrgTone.WAIT; else -> OrgTone.ACCENT }

/**
 * The ticket's history under its details: status changes with their reasons, then comments, oldest first, and a box to add one.
 * [supported] is null until the desktop says whether it keeps ticket history, and false on an older desktop. */
@RequiresApi(29)
@Composable
internal fun TicketHistoryView(uid: String, timeline: TicketTimeline?, supported: Boolean?, connected: Boolean, busy: Boolean, title: Boolean = true, onComment: (String) -> Unit) {
    var draft by rememberSaveable(uid) { mutableStateOf("") }
    var showAll by rememberSaveable(uid) { mutableStateOf(false) }
    Column(Modifier.fillMaxWidth().testTag("kanbanHistory"), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        if (title) SectionTitle("History and comments")
        when {
            supported == false -> Note(Kanban.UPDATE_COMMENTS, Modifier.testTag("kanbanHistoryUnsupported"))
            timeline == null -> Text(if (connected) "Loading its history…" else "Connect to your computer to see its history.",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            timeline.comments.isEmpty() -> Text("Nothing yet. Status changes and comments appear here.", style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            else -> {
                // The newest entries matter most; earlier ones fold away until asked for.
                val hidden = if (showAll) 0 else (timeline.comments.size - 8).coerceAtLeast(0)
                val older = timeline.earlier + hidden
                if (older > 0) TextButton(onClick = { showAll = true }, enabled = hidden > 0, modifier = Modifier.heightIn(min = 48.dp).testTag("kanbanHistoryEarlier")) {
                    Text(if (hidden > 0) "Show $hidden earlier" else "$older older not shown")
                }
                timeline.comments.drop(hidden).forEach { TicketEntry(it) }
            }
        }
        if (supported == true && timeline?.canComment != false) {
            OutlinedTextField(draft, { draft = it }, Modifier.fillMaxWidth().testTag("kanbanCommentField"), label = { Text("Add a comment") }, minLines = 2, maxLines = 6)
            Button(onClick = { onComment(draft.trim()); draft = "" },
                enabled = !busy && draft.isNotBlank() && connected, modifier = Modifier.heightIn(min = 48.dp).testTag("kanbanCommentSend")) { Text("Comment") }
        }
    }
}

@RequiresApi(29)
@Composable
private fun TicketEntry(entry: TicketComment) {
    val tone = entryTone(Kanban.tone(entry))
    val headline = Kanban.headline(entry)
    val move = Kanban.move(entry)
    val who = entry.authorName.ifBlank { "Raticode" }
    val at = entry.at.takeIf { it.isNotBlank() }?.let { whenText(it, past = true) }.orEmpty()
    Row(Modifier.fillMaxWidth().testTag("kanbanEntry"), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        Box(Modifier.padding(top = 6.dp).size(10.dp).clip(CircleShape).background(if (entry.kind == "status") orgColors(tone).first else MaterialTheme.colorScheme.outlineVariant))
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(listOf(who, at).filter { it.isNotBlank() }.joinToString(" · "), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (headline.isNotBlank()) Text(headline, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold,
                color = if (tone == OrgTone.BAD) orgColors(OrgTone.BAD).first else MaterialTheme.colorScheme.onSurface)
            if (move.isNotBlank()) Text(move, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (entry.body.isNotBlank()) Text(entry.body, style = MaterialTheme.typography.bodyMedium)
            if (entry.checks.isNotEmpty()) {
                Text(Kanban.checksLine(entry.checks), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                entry.checks.forEach { check ->
                    val mark = when (check.result) { "pass" -> "✓"; "fail" -> "✗"; else -> "·" }
                    val word = when (check.result) { "pass" -> "Passed"; "fail" -> "Failed"; else -> "Not checked" }
                    Text("$mark ${check.name}", Modifier.semantics { contentDescription = "$word: ${check.name}" }, style = MaterialTheme.typography.bodySmall,
                        color = when (check.result) { "pass" -> orgColors(OrgTone.DONE).first; "fail" -> orgColors(OrgTone.BAD).first; else -> MaterialTheme.colorScheme.onSurfaceVariant })
                }
            }
            if (entry.details.isNotBlank()) {
                // The whole result stays folded away under its summary until asked for.
                var open by rememberSaveable(entry.id) { mutableStateOf(false) }
                TextButton(onClick = { open = !open }, contentPadding = PaddingValues(horizontal = 0.dp), modifier = Modifier.heightIn(min = 48.dp).testTag("kanbanEntryDetails")) {
                    Text(if (open) "Hide the full text" else "Show the full text")
                }
                if (open) Text(entry.details, style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

@RequiresApi(29)
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ColumnScope.KanbanNewTicket(client: LiveClient, board: KanbanBoard?, busy: Boolean, onDone: () -> Unit) {
    var place by rememberSaveable { mutableStateOf(Kanban.SCOPE_REM) }
    var title by rememberSaveable { mutableStateOf("") }
    var body by rememberSaveable { mutableStateOf("") }
    var priority by rememberSaveable { mutableStateOf("medium") }
    var column by rememberSaveable { mutableStateOf("backlog") }
    var assignee by rememberSaveable { mutableStateOf(Kanban.REM) }
    var kind by rememberSaveable { mutableStateOf("develop") }
    var project by rememberSaveable { mutableStateOf("") }
    Row(Modifier.fillMaxWidth().padding(start = 4.dp, end = 8.dp, top = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        IconButton(onClick = onDone) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") }
        Text("New ticket", Modifier.weight(1f).semantics { heading() }, style = MaterialTheme.typography.titleMedium)
        Button(enabled = title.isNotBlank() && !busy && board != null, modifier = Modifier.testTag("kanbanCreate"), onClick = {
            client.desktopAction(Kanban.TICKET, Kanban.createParams(place, title, body, priority, column, assignee, kind, project)); onDone()
        }) { Text("Add") }
    }
    Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        if (!client.connected) Note("Offline. Connect to your desktop to add this ticket.")
        val places = board?.let(Kanban::places).orEmpty()
        OutlinedTextField(title, { title = it.take(200) }, Modifier.fillMaxWidth().testTag("kanbanTitle"), label = { Text("Title") }, singleLine = true)
        OutlinedTextField(body, { body = it.take(4000) }, Modifier.fillMaxWidth().testTag("kanbanBody"), label = { Text("Description (optional)") }, minLines = 3, maxLines = 8)
        Text("For", style = MaterialTheme.typography.titleSmall)
        ChoiceMenu(places.firstOrNull { it.first == place }?.second ?: "Rem", "Whose ticket", places, place, Modifier.heightIn(min = 48.dp).testTag("kanbanPlace")) { chosen ->
            place = chosen
            assignee = if (chosen == Kanban.SCOPE_REM) Kanban.REM else Kanban.UNASSIGNED
        }
        val assignees = board?.let { Kanban.assignees(it, if (place == Kanban.SCOPE_REM) Kanban.SCOPE_REM else Kanban.SCOPE_ORG, place) }.orEmpty()
        if (place == Kanban.SCOPE_REM) {
            Text("Kind of work", style = MaterialTheme.typography.titleSmall)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Kanban.KINDS.forEach { (value, label) ->
                    FilterChip(kind == value, { kind = value }, label = { Text(label) }, modifier = Modifier.testTag("kanbanKind-$value"))
                }
            }
            if (client.projects.isNotEmpty()) {
                Text("Project", style = MaterialTheme.typography.titleSmall)
                val projects = listOf("" to "No project") + client.projects.map { it.key to it.value }.sortedBy { it.second.lowercase() }
                ChoiceMenu(projects.firstOrNull { it.first == project }?.second ?: "No project", "Project", projects, project, Modifier.heightIn(min = 48.dp).testTag("kanbanProject")) { project = it }
                board?.takeIf { it.participantsKnown }?.let { Kanban.outsideProject(it, project) }?.takeIf { it.isNotBlank() }?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = orgColors(OrgTone.WAIT).first) }
            }
        } else {
            Text("Assign to", style = MaterialTheme.typography.titleSmall)
            ChoiceMenu(assignees.firstOrNull { it.first == assignee }?.second ?: "Unassigned", "Assign to", assignees, assignee, Modifier.heightIn(min = 48.dp).testTag("kanbanAssignee")) { assignee = it }
            if (assignees.size == 1) Note("This organization has no workers yet. Add them on your computer.")
        }
        Text("Starts in", style = MaterialTheme.typography.titleSmall)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            FilterChip(column == "backlog", { column = "backlog" }, label = { Text("Backlog") }, modifier = Modifier.testTag("kanbanStartBacklog"))
            FilterChip(column == "todo", { column = "todo" }, label = { Text("To do") }, modifier = Modifier.testTag("kanbanStartTodo"))
        }
        Note(if (column == "todo") "To do tickets are picked up by whoever they are assigned to." else "Backlog tickets wait until they are moved to To do or pulled in.")
        Text("Priority", style = MaterialTheme.typography.titleSmall)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Kanban.PRIORITIES.forEach { (value, label) -> FilterChip(priority == value, { priority = value }, label = { Text(label) }) }
        }
    }
}

// Self-work ----------------------------------------------------------------------------------------------------------

/**
 * Rem's self-work settings, in Desktop settings › Rem self-work. They live on the computer, so they work whether or not the board
 * is on there; [onProjects] opens the board's People to choose Rem's projects.
 */
@RequiresApi(29)
@Composable
internal fun SelfWorkPage(client: LiveClient, onBack: (() -> Unit)?, onProjects: () -> Unit, onTicket: (String) -> Unit) {
    DisposableEffect(Unit) { client.openDesktop(Kanban.SELF_WORK); onDispose { client.openDesktop(null) } }
    val busy = Kanban.SELF_WORK in client.desktopBusy || !client.connected
    val view = client.desktopViews[Kanban.SELF_WORK] as? SelfWorkView
    Column(Modifier.fillMaxSize().testTag("selfWorkPage")) {
        Header("Rem self-work", when { !client.connected -> "Offline"; view == null -> "On your computer"; else -> Kanban.status(view) }, onBack)
        KanbanStateStrip(client, Kanban.SELF_WORK, view != null)
        Box(Modifier.weight(1f)) {
            if (client.desktopControl && client.kanban == false) KanbanUnsupported(client)
            else DesktopGate(client, Kanban.SELF_WORK, "Loading Rem's self-work…") {
                SelfWorkContent(client, busy, showSettings = true, onSettings = {}, projectsEnabled = client.kanbanParticipants != false,
                    onProjects = onProjects, onTicket = onTicket)
            }
        }
    }
}

/** Self-work's state, Rem's projects and the running report; its settings on the settings page, or a link to them on the board. */
@RequiresApi(29)
@Composable
private fun SelfWorkContent(client: LiveClient, busy: Boolean, showSettings: Boolean, onSettings: () -> Unit, projectsEnabled: Boolean,
                            onProjects: () -> Unit, onTicket: (String) -> Unit) {
    val view = client.desktopViews[Kanban.SELF_WORK] as? SelfWorkView ?: return
    SelfWorkBody(view, client.desktopViews[Kanban.BOARD] as? KanbanBoard, busy, client.connected, client.desktopErrors[Kanban.SELF_WORK],
        showSettings, onSettings, projectsEnabled, onProjects, onTicket,
        onStop = { client.desktopAction(Kanban.STOP, it) },
        onSeen = { client.desktopAction(Kanban.SEEN, Kanban.seenParams(it)) }) { client.desktopAction(Kanban.SETTINGS, it) }
}

/** One provider's self-work account: its name and the chosen account, which opens the list of choices. */
@Composable
private fun SelfWorkAccountRow(row: SelfWorkAccount, enabled: Boolean, onChoose: (String) -> Unit) {
    var expanded by remember { mutableStateOf(false) }
    val shown = Kanban.accountLabel(row)
    Box(Modifier.fillMaxWidth()) {
        Surface(onClick = { expanded = true }, enabled = enabled, color = MaterialTheme.colorScheme.surface,
            modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp).testTag("selfWorkAccount:${row.provider}")
                .semantics { contentDescription = "${row.label} account for self-work: $shown" }) {
            Row(Modifier.padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(row.label, style = MaterialTheme.typography.bodyLarge)
                    Text(shown, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Icon(Icons.Default.ArrowDropDown, null)
            }
        }
        DropdownMenu(expanded, { expanded = false }) {
            Kanban.accountChoices(row).forEach { (value, text) ->
                DropdownMenuItem(text = { Text(text) }, modifier = Modifier.testTag("selfWorkAccountChoice:${row.provider}:$value"),
                    onClick = { expanded = false; if (value != row.choice) onChoose(value) },
                    trailingIcon = { if (value == row.choice) Icon(Icons.Default.Check, "Selected") })
            }
        }
    }
}

/** What [SelfWorkContent] shows, from the desktop's answer alone; [onChange] sends `selfwork.settings` params. */
@RequiresApi(29)
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun SelfWorkBody(view: SelfWorkView, board: KanbanBoard?, busy: Boolean, connected: Boolean, error: String?, showSettings: Boolean,
                          onSettings: () -> Unit, projectsEnabled: Boolean, onProjects: () -> Unit, onTicket: (String) -> Unit,
                          onStop: (org.json.JSONObject) -> Unit = {},
                          onSeen: (SelfWorkReport) -> Unit, onChange: (org.json.JSONObject) -> Unit) {
    val settings = view.settings
    // Opening this page reads the report, on every device.
    val report = view.report
    LaunchedEffect(report?.updatedAt, report?.unseen, connected) {
        if (report != null && report.unseen && connected) onSeen(report)
    }
    // Several quick taps become one change: the number on screen moves at once and goes to the desktop when the taps stop.
    var pendingMax by remember { mutableStateOf<Int?>(null) }
    LaunchedEffect(settings.revision, error) { pendingMax = null }
    LaunchedEffect(pendingMax) {
        val chosen = pendingMax ?: return@LaunchedEffect
        delay(600)
        if (chosen != settings.maxThreads) onChange(Kanban.settingsParams(settings, maxThreads = chosen))
    }
    val shownMax = pendingMax ?: settings.maxThreads
    // The daily cap steps the same way: taps move the number, and it saves when they stop.
    var pendingCap by remember { mutableStateOf<Int?>(null) }
    LaunchedEffect(settings.revision, error) { pendingCap = null }
    LaunchedEffect(pendingCap) {
        val chosen = pendingCap ?: return@LaunchedEffect
        delay(600)
        if (chosen != settings.dailyCap) onChange(Kanban.settingsParams(settings, dailyCap = chosen))
    }
    // Pace to reset's aim and finish step the same way.
    var pendingAim by remember { mutableStateOf<Int?>(null) }
    var pendingLead by remember { mutableStateOf<Int?>(null) }
    LaunchedEffect(settings.revision, error) { pendingAim = null; pendingLead = null }
    LaunchedEffect(pendingAim) {
        val chosen = pendingAim ?: return@LaunchedEffect
        delay(600)
        if (chosen != settings.paceAim) onChange(Kanban.settingsParams(settings, paceAim = chosen))
    }
    LaunchedEffect(pendingLead) {
        val chosen = pendingLead ?: return@LaunchedEffect
        delay(600)
        if (chosen != settings.paceLead) onChange(Kanban.settingsParams(settings, paceLead = chosen))
    }
    var confirmingAutoApprove by remember { mutableStateOf(false) }
    if (confirmingAutoApprove) AlertDialog(onDismissRequest = { confirmingAutoApprove = false }, modifier = Modifier.testTag("selfWorkAutoApproveConfirm"),
        title = { Text("Let Rem approve tickets it proposes?") },
        text = { Column(verticalArrangement = Arrangement.spacedBy(8.dp)) { Kanban.autoApproveWarning(view).forEach { Text(it) } } },
        confirmButton = { TextButton(onClick = { confirmingAutoApprove = false; onChange(Kanban.settingsParams(settings, autoApprove = true)) },
            modifier = Modifier.testTag("selfWorkAutoApproveTurnOn")) { Text("Turn on") } },
        dismissButton = { TextButton(onClick = { confirmingAutoApprove = false }) { Text("Keep asking me") } })
    LazyColumn(Modifier.fillMaxSize().testTag("selfWork"), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item {
            val state = view.state
            val working = state.mode == "working"
            val (ink, fill) = orgColors(if (working) OrgTone.RUN else if (state.mode == "paused") OrgTone.WAIT else OrgTone.ACCENT)
            Surface(color = fill, shape = RoundedCornerShape(12.dp), modifier = Modifier.fillMaxWidth().testTag("selfWorkState")) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        Icon(if (working) ThreadIcons.Running else if (state.mode == "paused") ThreadIcons.Stopped else ThreadIcons.Rem, null, Modifier.size(22.dp), tint = ink)
                        Text(Kanban.status(view), Modifier.weight(1f).semantics { liveRegion = LiveRegionMode.Polite }, style = MaterialTheme.typography.titleMedium, color = ink)
                    }
                    // Each fact a small chip of its own, rather than one line joined with dots.
                    val facts = listOfNotNull(state.proposalsWaiting.takeIf { it > 0 }?.let { ThreadIcons.Approvals to "$it to approve" },
                        state.startsAt.takeIf { it.isNotBlank() && state.mode == "waiting" }?.let { ThreadIcons.Recent to "Starts ${whenText(it)}" },
                        state.lastPromptAt.takeIf { it.isNotBlank() }?.let { ThreadIcons.Threads to "Last message ${whenText(it, past = true)}" })
                    if (facts.isNotEmpty()) FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        facts.forEach { (icon, text) ->
                            Row(Modifier.clip(RoundedCornerShape(50)).background(MaterialTheme.colorScheme.surface.copy(alpha = 0.55f)).padding(horizontal = 10.dp, vertical = 4.dp)
                                .testTag("selfWorkFact"), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                                Icon(icon, null, Modifier.size(14.dp), tint = ink)
                                Text(text, style = MaterialTheme.typography.labelMedium, color = ink, maxLines = 1)
                            }
                        }
                    }
                    Kanban.windowNote(view)?.let { Text(it, Modifier.testTag("selfWorkWindowNote"), style = MaterialTheme.typography.bodySmall, color = ink) }
                }
            }
        }
        // Timed windows from a desktop that has them; set or cancel them in Rem's chat or on the computer.
        view.schedule?.takeIf { !it.isEmpty }?.let { schedule ->
            item {
                SettingsGroup("Self-work windows") {
                    schedule.active?.let { window ->
                        WindowRow("Now", window, settings.idleMinutes, "selfWorkActiveWindow")
                    }
                    schedule.upcoming.forEachIndexed { index, window ->
                        if (index > 0 || schedule.active != null) HorizontalDivider()
                        WindowRow(if (index == 0) "Next" else "Later", window, settings.idleMinutes, "selfWorkUpcomingWindow")
                    }
                    schedule.lastPeriod?.let { period ->
                        if (schedule.active != null || schedule.upcoming.isNotEmpty()) HorizontalDivider()
                        Column(Modifier.fillMaxWidth().heightIn(min = 48.dp).padding(vertical = 6.dp).testTag("selfWorkLastPeriod"), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                            Text("Last time", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            if (period.summary.isNotBlank()) Text(period.summary, style = MaterialTheme.typography.bodyMedium)
                            Text(listOfNotNull(Kanban.periodEnd(period), period.endedAt.takeIf { it.isNotBlank() }?.let { whenText(it, past = true) }).joinToString(" · "),
                                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }
        }
        // When self-work stops, from a desktop that has stop conditions; set or change it here, in chat or on the computer.
        view.stop?.let { stop ->
            item { StopGroup(stop, selfWorkOn = settings.enabled, enabled = connected && !busy, onStop = onStop) }
        }
        Kanban.boardOffNote(view)?.let { note ->
            item {
                Surface(color = MaterialTheme.colorScheme.surfaceVariant, shape = RoundedCornerShape(10.dp), modifier = Modifier.fillMaxWidth().testTag("selfWorkBoardOff")) {
                    Text(note, Modifier.padding(12.dp), style = MaterialTheme.typography.bodyMedium)
                }
            }
        }
        selfWorkBoundary(view, board)?.let { (line, warn) ->
            item {
                Surface(onClick = onProjects, enabled = projectsEnabled, color = MaterialTheme.colorScheme.surface, shape = RoundedCornerShape(10.dp),
                    modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp).testTag("selfWorkBoundary")) {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                        Text("Where Rem can work", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Text(line, style = MaterialTheme.typography.bodyMedium, color = if (warn) orgColors(OrgTone.WAIT).first else MaterialTheme.colorScheme.onSurface)
                    }
                }
            }
        }
        if (!showSettings) item {
            Surface(onClick = onSettings, color = MaterialTheme.colorScheme.surface, shape = RoundedCornerShape(10.dp),
                modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp).testTag("selfWorkSettingsLink")) {
                Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Text("Self-work settings", style = MaterialTheme.typography.bodyLarge)
                    Text("On or off, threads, backlog pulling, allowance use and accounts are in Settings › Rem self-work.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
        if (showSettings) item {
            SettingsGroup("Settings") {
                SwitchRow("Self-work", "After ${settings.idleMinutes} minutes without a message from you, Rem works by itself. Work you ask for goes first: self-work pauses to make room. You can also tell Rem \"turn on self-work\", \"self-work until 7 PM\" or \"stop self-work\".", settings.enabled, !busy, "selfWorkEnabled") {
                    onChange(Kanban.settingsParams(settings, enabled = it))
                }
                HorizontalDivider()
                Row(Modifier.fillMaxWidth().heightIn(min = 56.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("Maximum threads", style = MaterialTheme.typography.bodyLarge)
                        Text("How many Rem keeps going at once while there's work.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    FilledTonalIconButton(onClick = { pendingMax = (shownMax - 1).coerceAtLeast(view.limits.min) }, enabled = !busy && shownMax > view.limits.min,
                        modifier = Modifier.size(48.dp).testTag("selfWorkFewer").semantics { contentDescription = "Fewer threads" }) { Text("−", style = MaterialTheme.typography.titleLarge) }
                    Text("$shownMax", Modifier.widthIn(min = 40.dp).testTag("selfWorkMax").semantics { contentDescription = "$shownMax maximum threads" },
                        style = MaterialTheme.typography.titleLarge.copy(fontFamily = FontFamily.Monospace), textAlign = TextAlign.Center)
                    FilledTonalIconButton(onClick = { pendingMax = (shownMax + 1).coerceAtMost(view.limits.max) }, enabled = !busy && shownMax < view.limits.max,
                        modifier = Modifier.size(48.dp).testTag("selfWorkMore").semantics { contentDescription = "More threads" }) { Text("+", style = MaterialTheme.typography.titleLarge) }
                }
                HorizontalDivider()
                SwitchRow("Pull work from Backlog", Kanban.autoPullDetail(view), settings.autoPull, !busy && view.state.boardEnabled, "selfWorkAutoPull") {
                    onChange(Kanban.settingsParams(settings, autoPull = it))
                }
                // Only from a desktop that has it; turning it on asks first, turning it off doesn't.
                settings.autoApprove?.let { on ->
                    HorizontalDivider()
                    SwitchRow("Approve its own backlog tickets", Kanban.autoApproveDetail(view), on, !busy && view.state.boardEnabled, "selfWorkAutoApprove") {
                        if (it) confirmingAutoApprove = true else onChange(Kanban.settingsParams(settings, autoApprove = false))
                    }
                }
                // Off, Daily cap or Pace to reset, from a desktop that has the choice.
                settings.allowanceMode?.let { mode ->
                    HorizontalDivider()
                    Column(Modifier.fillMaxWidth().padding(vertical = 8.dp).testTag("selfWorkAllowanceUse"), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text("Allowance use", style = MaterialTheme.typography.bodyLarge)
                        SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
                            Kanban.ALLOWANCE_MODES.forEachIndexed { index, (choice, label) ->
                                SegmentedButton(mode == choice, { if (choice != mode) onChange(Kanban.settingsParams(settings, allowanceMode = choice)) },
                                    SegmentedButtonDefaults.itemShape(index, Kanban.ALLOWANCE_MODES.size), Modifier.testTag("selfWorkAllowance-$choice"),
                                    enabled = !busy) { Text(label, maxLines = 1) }
                            }
                        }
                        Text(Kanban.allowanceDetail(mode), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    if (mode == "pace") {
                        val aim = pendingAim ?: settings.paceAim
                        val lead = pendingLead ?: settings.paceLead
                        HorizontalDivider()
                        StepRow("Aim by reset", "How much of the allowance to have used when it resets. The rest is left for you.", "$aim%", "selfWorkPaceAim",
                            "Lower the aim", "Raise the aim", !busy && aim > view.limits.paceAimMin, !busy && aim < view.limits.paceAimMax,
                            { pendingAim = (aim - 1).coerceAtLeast(view.limits.paceAimMin) }, { pendingAim = (aim + 1).coerceAtMost(view.limits.paceAimMax) })
                        HorizontalDivider()
                        StepRow("Finish before reset", "Reach the aim this many hours before the reset, so work started late spends the new week.", "$lead h", "selfWorkPaceLead",
                            "Finish closer to the reset", "Finish earlier", !busy && lead > 0, !busy && lead < view.limits.paceLeadMax,
                            { pendingLead = (lead - 1).coerceAtLeast(0) }, { pendingLead = (lead + 1).coerceAtMost(view.limits.paceLeadMax) })
                        view.state.allowancePace.takeIf { it.isNotBlank() }?.let { line ->
                            Text(line, Modifier.padding(bottom = 4.dp).testTag("selfWorkAllowancePace").semantics { liveRegion = LiveRegionMode.Polite },
                                style = MaterialTheme.typography.bodySmall,
                                color = if (view.state.paceHolding) orgColors(OrgTone.WAIT).first else MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        Kanban.paceRateWarning(settings, settings.rateLimitPercent)?.let {
                            Text(it, Modifier.padding(bottom = 8.dp).testTag("selfWorkPaceRateWarning"), style = MaterialTheme.typography.bodySmall,
                                color = orgColors(OrgTone.WAIT).first)
                        }
                    }
                }
                // Only from a desktop that has it, and with Daily cap chosen where there is a choice.
                settings.dailyCap?.takeIf { settings.allowanceMode == null || settings.allowanceMode == "daily" }?.let { saved ->
                    val shownCap = pendingCap ?: saved
                    HorizontalDivider()
                    Row(Modifier.fillMaxWidth().heightIn(min = 56.dp).testTag("selfWorkDailyCap"), verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text("Weekly allowance per day", style = MaterialTheme.typography.bodyLarge)
                            Text(Kanban.dailyCapDetail(view), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        FilledTonalIconButton(onClick = { pendingCap = (shownCap - 1).coerceAtLeast(0) }, enabled = !busy && shownCap > 0,
                            modifier = Modifier.size(48.dp).testTag("selfWorkDailyCapLess").semantics { contentDescription = "Lower the daily cap" }) { Text("−", style = MaterialTheme.typography.titleLarge) }
                        Text(Kanban.dailyCapLabel(shownCap), Modifier.widthIn(min = 48.dp).testTag("selfWorkDailyCapValue").semantics { contentDescription = "Daily cap ${Kanban.dailyCapLabel(shownCap)}" },
                            style = MaterialTheme.typography.titleLarge.copy(fontFamily = FontFamily.Monospace), textAlign = TextAlign.Center)
                        FilledTonalIconButton(onClick = { pendingCap = (shownCap + 1).coerceAtMost(view.limits.dailyCapMax) }, enabled = !busy && shownCap < view.limits.dailyCapMax,
                            modifier = Modifier.size(48.dp).testTag("selfWorkDailyCapMore").semantics { contentDescription = "Raise the daily cap" }) { Text("+", style = MaterialTheme.typography.titleLarge) }
                    }
                    view.state.dailyAllowance.takeIf { it.isNotBlank() }?.let { line ->
                        Text(line, Modifier.padding(bottom = 8.dp).testTag("selfWorkDailyAllowance").semantics { liveRegion = LiveRegionMode.Polite },
                            style = MaterialTheme.typography.bodySmall,
                            color = if (view.state.dailyAllowanceUnknown) orgColors(OrgTone.WAIT).first else MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
        }
        // Only from a desktop that has it, and only for providers with accounts.
        if (showSettings && view.accounts.isNotEmpty()) item {
            SettingsGroup("Accounts") {
                Text("The account self-work runs on. A task with its own account keeps it. Chat, threads and tasks you create use Rem’s setting.",
                    Modifier.padding(vertical = 6.dp), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                view.accounts.forEach { row ->
                    HorizontalDivider()
                    SelfWorkAccountRow(row, !busy) { onChange(Kanban.accountParams(settings, row.provider, it)) }
                }
            }
        }
        // Only from a desktop that sends them; Rem's chat changes them.
        view.objectives?.let { objectives -> item { ObjectivesCard(objectives) } }
        item { SectionTitle("Running report") }
        if (report == null || report.isEmpty) item { Note("Nothing yet. When Rem works on its own, what it did shows here, on your computer and on this phone.") }
        else {
            report.updatedAt.takeIf { it.isNotBlank() }?.let { at -> item { Text("Updated ${whenText(at, past = true)}", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant) } }
            // The counts first, as tiles, then the summary folded to its first sentence.
            val counts = listOfNotNull(
                report.ticketsAdded.size.takeIf { it > 0 }?.let { Fact(ThreadIcons.Plus, "Tickets added", "$it", OrgTone.ACCENT) },
                report.workDone.size.takeIf { it > 0 }?.let { Fact(ThreadIcons.Succeeded, "Work done", "$it", OrgTone.DONE) },
                report.duplicatesMerged.size.takeIf { it > 0 }?.let { Fact(ThreadIcons.Copy, "Merged", "$it", OrgTone.IDLE) },
                report.notes.size.takeIf { it > 0 }?.let { Fact(ThreadIcons.FileText, "Notes", "$it", OrgTone.IDLE) })
            if (counts.isNotEmpty()) item { FactGrid(counts, Modifier.testTag("selfWorkReportCounts")) }
            if (report.summary.isNotBlank()) item {
                DetailCard("Summary", icon = ThreadIcons.Log) { SummaryText(report.summary, tag = "selfWorkSummary") { ChatMarkdown(report.summary, compact = true) } }
            }
            if (report.ticketsAdded.isNotEmpty()) {
                item { SectionTitle("Tickets added · ${report.ticketsAdded.size}") }
                items(report.ticketsAdded, key = { "added:${it.organizationId}/${it.id}" }) { t ->
                    val found = board?.tickets?.firstOrNull { it.id == t.id && (t.organizationId.isBlank() || it.organizationId == t.organizationId) }
                    ReportRow(t.title, listOfNotNull(t.organizationName.ifBlank { "Rem" }, if (t.proposed) (if (t.computerName.isNotBlank()) "To approve, from ${t.computerName}" else "To approve") else if (t.autoApproved) "Approved by self-work" else null, t.at.takeIf { it.isNotBlank() }?.let { whenText(it, past = true) }).joinToString(" · "),
                        found?.let { { onTicket(it.uid) } })
                }
            }
            if (report.duplicatesMerged.isNotEmpty()) {
                item { SectionTitle("Duplicates merged · ${report.duplicatesMerged.size}") }
                items(report.duplicatesMerged, key = { "merged:${it.ticketId}/${it.at}" }) { m ->
                    ReportRow(m.title, listOfNotNull(m.keptTitle.takeIf { it.isNotBlank() }?.let { "Kept $it" }, m.reason.takeIf { it.isNotBlank() }).joinToString(" · "), null)
                }
            }
            if (report.notes.isNotEmpty()) {
                item { SectionTitle("Notes") }
                items(report.notes, key = { "note:${it.at}/${it.text.hashCode()}" }) { n ->
                    ReportRow(n.text, n.at.takeIf { it.isNotBlank() }?.let { whenText(it, past = true) }.orEmpty(), null)
                }
            }
            if (report.workDone.isNotEmpty()) {
                item { SectionTitle("Work done · ${report.workDone.size}") }
                items(report.workDone, key = { "work:${it.ticketId}/${it.at}/${it.state}" }) { w ->
                    // The run's outcome as a glyph and colored words, its summary on its own lines.
                    ReportRow(w.title, w.at.takeIf { it.isNotBlank() }?.let { whenText(it, past = true) }.orEmpty(),
                        board?.tickets?.firstOrNull { it.id == w.ticketId }?.let { { onTicket(it.uid) } }, state = w.state, summary = w.summary)
                }
            }
        }
    }
}

/**
 * Self-work's objectives, as Rem's side pane on the computer lists them: open ones first, newest first, each opening to its
 * finish line and who checked it off; what earlier sessions finished folds away. Read-only: Rem's chat changes them.
 */
@Composable
private fun ObjectivesCard(objectives: SelfWorkObjectives) {
    var staleOpen by rememberSaveable { mutableStateOf(false) }
    // One row opens at a time, as in the side pane on the computer.
    var openId by rememberSaveable { mutableStateOf("") }
    val row: @Composable (SelfWorkObjective) -> Unit = { item -> ObjectiveRow(item, item.id == openId) { openId = if (openId == item.id) "" else item.id } }
    val current = objectives.current
    DetailCard("Objectives", Modifier.testTag("selfWorkObjectives"), icon = ThreadIcons.Target, tone = OrgTone.ACCENT, trailing = {
        Kanban.objectivesCount(objectives).takeIf { it.isNotBlank() }?.let {
            Text(it, Modifier.testTag("selfWorkObjectivesCount"), style = MaterialTheme.typography.labelLarge.copy(fontFamily = FontFamily.Monospace),
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }) {
        Text("Goals that guide Rem's self-work for the whole session. To change them, tell Rem in chat.", style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        if (current.isEmpty()) Text(Kanban.objectivesEmpty(objectives), Modifier.testTag("selfWorkObjectivesEmpty"), style = MaterialTheme.typography.bodyMedium)
        current.forEach { row(it) }
        if (objectives.stale > 0) {
            HorizontalDivider()
            Surface(onClick = { staleOpen = !staleOpen }, color = Color.Transparent, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp).testTag("selfWorkObjectivesStale")
                .semantics { stateDescription = if (staleOpen) "Shown" else "Folded" }) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Icon(if (staleOpen) ThreadIcons.ChevronDown else ThreadIcons.ChevronRight, null, Modifier.size(14.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text("Stale · ${objectives.stale}", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            if (staleOpen) {
                Text("Done in earlier self-work sessions. They don't count toward this session's objectives.", style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                objectives.staleItems.forEach { row(it) }
            }
        }
        (objectives.total - objectives.items.size).takeIf { it > 0 }?.let {
            Text("$it more on your computer.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/** One objective: its text and whether it's done; while [open], its finish line, who checked it off and the evidence. Tapping calls [onToggle]. */
@Composable
private fun ObjectiveRow(item: SelfWorkObjective, open: Boolean, onToggle: () -> Unit) {
    val state = if (item.done) "Done" else "Open"
    val done = orgColors(OrgTone.DONE).first
    Surface(onClick = onToggle, color = Color.Transparent, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp).testTag("selfWorkObjective")
        .semantics { stateDescription = state }) {
        Column(Modifier.padding(vertical = 6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Row(verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Icon(if (item.done) ThreadIcons.Succeeded else ThreadIcons.CircleDashed, null, Modifier.padding(top = 1.dp).size(18.dp),
                    tint = if (item.done && !item.stale) done else MaterialTheme.colorScheme.onSurfaceVariant)
                Text(item.text, Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium,
                    color = if (item.done) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface)
                Text(state, Modifier.padding(top = 2.dp), style = MaterialTheme.typography.labelSmall,
                    color = if (item.done && !item.stale) done else MaterialTheme.colorScheme.onSurfaceVariant)
                Icon(if (open) ThreadIcons.ChevronUp else ThreadIcons.ChevronDown, null, Modifier.padding(top = 3.dp).size(14.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            if (open) Text(Kanban.objectiveDetails(item), Modifier.padding(start = 28.dp).testTag("selfWorkObjectiveDetails"), style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/** The line about Rem's projects and whether it needs attention. The count is the desktop's; names come from the board if it's loaded. Null on an older desktop. */
private fun selfWorkBoundary(view: SelfWorkView, board: KanbanBoard?): Pair<String, Boolean>? {
    val count = view.state.assignedProjects ?: return null
    if (count == 0) return "No projects yet, so Rem won't work by itself. Tap to choose some." to true
    val rem = board?.let { Kanban.person(it, Kanban.REM) }
    val names = rem?.let { Kanban.projectNames(board, it.copy(projects = it.projects - it.unavailable.toSet())) }.orEmpty()
    val where = if (names.size == count) names.joinToString(", ") else "$count ${if (count == 1) "project" else "projects"}"
    val outside = view.state.outsideProjects.takeIf { it > 0 }?.let { " $it ${if (it == 1) "ticket waits" else "tickets wait"} outside them." }.orEmpty()
    return where + outside to false
}

/** When self-work stops: the condition and latest time, Rem's last check, and Edit or Clear; or how the last stop went. */
@Composable
private fun StopGroup(stop: SelfWorkStop, selfWorkOn: Boolean, enabled: Boolean, onStop: (org.json.JSONObject) -> Unit) {
    var editing by remember { mutableStateOf(false) }
    if (editing) StopEditor(stop, onDismiss = { editing = false }) { editing = false; onStop(it) }
    if (!stop.active && !selfWorkOn && stop.last == null) return
    SettingsGroup("When to stop") {
        if (stop.active) {
            Column(Modifier.fillMaxWidth().testTag("selfWorkStop"), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(stop.label, style = MaterialTheme.typography.bodyMedium)
                Kanban.stopJudgmentLine(stop)?.let {
                    Text(it, Modifier.testTag("selfWorkStopJudgment").semantics { liveRegion = LiveRegionMode.Polite },
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = { editing = true }, enabled = enabled, modifier = Modifier.testTag("selfWorkStopEdit")) { Text("Edit") }
                TextButton(onClick = { onStop(Kanban.clearStopParams(stop)) }, enabled = enabled, modifier = Modifier.testTag("selfWorkStopClear")) { Text("Clear") }
            }
        } else {
            stop.last?.let {
                Text(Kanban.lastStopLine(it), Modifier.testTag("selfWorkLastStop"), style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            if (selfWorkOn) {
                Text("Stop self-work when something is done, or by a time at the latest.", style = MaterialTheme.typography.bodySmall)
                OutlinedButton(onClick = { editing = true }, enabled = enabled, modifier = Modifier.testTag("selfWorkStopAdd")) { Text("Set when to stop") }
            }
        }
    }
}

/** Edits the stop condition and the latest time; Save sends `selfwork.stop` params. */
@Composable
private fun StopEditor(stop: SelfWorkStop, onDismiss: () -> Unit, onSave: (org.json.JSONObject) -> Unit) {
    var condition by remember { mutableStateOf(if (stop.active) stop.condition else "") }
    var time by remember { mutableStateOf("") }
    var day by remember { mutableStateOf("") }
    val badTime = time.isNotBlank() && !Kanban.validClock(time)
    // An unchanged latest time is kept as it is: the field holds a new time only.
    val keepsTime = stop.active && stop.noLaterThan.isNotBlank() && time.isBlank()
    AlertDialog(onDismissRequest = onDismiss, modifier = Modifier.testTag("selfWorkStopEditor"),
        title = { Text("When to stop self-work") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedTextField(condition, { condition = it.take(Kanban.MAX_STOP_CONDITION) }, Modifier.fillMaxWidth().testTag("selfWorkStopCondition"),
                    label = { Text("Stop when") }, placeholder = { Text("REM-205 to REM-212 are done") }, minLines = 2,
                    supportingText = { Text("In your words. Rem checks it after each self-work task finishes.") })
                OutlinedTextField(time, { time = it.take(5) }, Modifier.fillMaxWidth().testTag("selfWorkStopTime"),
                    label = { Text("No later than (24-hour HH:MM)") }, singleLine = true, isError = badTime,
                    placeholder = { Text("00:00") },
                    supportingText = { Text(when {
                        badTime -> "Use a 24-hour time such as 23:30."
                        keepsTime -> "Now ${stop.noLaterThanLabel}. Leave empty to keep it."
                        else -> "Optional. In your computer's time zone."
                    }) })
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    listOf("" to "Next", "today" to "Today", "tomorrow" to "Tomorrow").forEach { (value, text) ->
                        FilterChip(selected = day == value, onClick = { day = value }, label = { Text(text) },
                            modifier = Modifier.testTag("selfWorkStopDay:${value.ifBlank { "next" }}"))
                    }
                }
            }
        },
        confirmButton = {
            TextButton(onClick = {
                val params = Kanban.stopParams(stop.takeIf { it.revision > 0 }, condition, time, day)
                if (keepsTime) params.remove("no_later_than")
                onSave(params)
            }, enabled = !badTime && (condition.isNotBlank() || time.isNotBlank() || keepsTime), modifier = Modifier.testTag("selfWorkStopSave")) { Text("Save") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } })
}

/** One timed window: its label as the desktop formats it, then when it ends and what the switch does after. */
@Composable
private fun WindowRow(heading: String, window: SelfWorkWindow, idleMinutes: Int, tag: String) {
    Column(Modifier.fillMaxWidth().heightIn(min = 48.dp).padding(vertical = 6.dp).testTag(tag), verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text(heading, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(window.label.ifBlank { "Self-work window" }, style = MaterialTheme.typography.bodyMedium)
        Kanban.windowDetail(window, idleMinutes).takeIf { it.isNotBlank() }?.let {
            Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun SwitchRow(title: String, detail: String, checked: Boolean, enabled: Boolean, tag: String, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth().heightIn(min = 56.dp).toggleable(value = checked, enabled = enabled, role = Role.Switch, onValueChange = onChange).testTag(tag), verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.bodyLarge)
            Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Switch(checked, null, enabled = enabled)
    }
}

/** A report entry's state as a run glyph and words: what happened to the ticket, not a run that is still going. */
internal fun reportStatus(state: String): Pair<String, RemTaskStatus.RunInfo> = when (state) {
    "done" -> "succeeded" to RemTaskStatus.RunInfo("Done", RemTaskStatus.Tone.SUCCESS, active = false, spinning = false)
    "researched" -> "succeeded" to RemTaskStatus.RunInfo("Researched", RemTaskStatus.Tone.SUCCESS, active = false, spinning = false)
    "ranked" -> "succeeded" to RemTaskStatus.RunInfo("Ranked", RemTaskStatus.Tone.SUCCESS, active = false, spinning = false)
    "unblocked" -> "queued" to RemTaskStatus.RunInfo("Picked back up", RemTaskStatus.Tone.ACTIVE, active = false, spinning = false)
    "still_blocked" -> "blocked" to RemTaskStatus.RunInfo("Still needs you", RemTaskStatus.Tone.ATTENTION, active = false, spinning = false)
    else -> state to RemTaskStatus.run(state)
}

@Composable
private fun ReportRow(title: String, detail: String, onClick: (() -> Unit)?, state: String = "", summary: String = "") {
    val content: @Composable () -> Unit = {
        Column(Modifier.fillMaxWidth().heightIn(min = 48.dp).padding(vertical = 6.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(title, style = MaterialTheme.typography.bodyMedium, maxLines = 2, overflow = TextOverflow.Ellipsis)
            if (state.isNotBlank() || detail.isNotBlank()) Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                if (state.isNotBlank()) {
                    val (glyph, info) = reportStatus(state)
                    val color = toneColor(info.tone)
                    StatusIcon(runGlyph(glyph), color, spinning = info.spinning, size = 14.dp)
                    Text(info.label, style = MaterialTheme.typography.labelMedium, color = if (info.tone == RemTaskStatus.Tone.NEUTRAL) MaterialTheme.colorScheme.onSurface else color)
                }
                if (detail.isNotBlank()) Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 4, overflow = TextOverflow.Ellipsis)
            }
            if (summary.isNotBlank()) Text(summary, style = MaterialTheme.typography.bodySmall, maxLines = 3, overflow = TextOverflow.Ellipsis)
        }
    }
    if (onClick != null) Surface(onClick = onClick, color = Color.Transparent, modifier = Modifier.fillMaxWidth().testTag("selfWorkReportRow")) { content() } else content()
}

/** On Rem's chat: what self-work did while you were away, until you open it or dismiss it. */
@RequiresApi(29)
@Composable
internal fun SelfWorkBanner(client: LiveClient, onOpen: () -> Unit) {
    val view = client.desktopViews[Kanban.SELF_WORK] as? SelfWorkView ?: return
    val headline = Kanban.reportHeadline(view.report)
    val working = view.state.mode == "working"
    if (headline.isBlank() && !working) return
    val (ink, fill) = orgColors(if (headline.isNotBlank()) OrgTone.ACCENT else OrgTone.RUN)
    Surface(onClick = onOpen, color = fill, modifier = Modifier.fillMaxWidth().testTag("selfWorkBanner")) {
        Row(Modifier.padding(start = 16.dp, end = 8.dp, top = 6.dp, bottom = 6.dp).heightIn(min = 40.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(if (headline.isNotBlank()) "While you were away" else "Rem's self-work", style = MaterialTheme.typography.labelMedium, color = ink)
                Text(headline.ifBlank { Kanban.status(view) }, style = MaterialTheme.typography.bodySmall, color = ink, maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
            view.report?.takeIf { headline.isNotBlank() }?.let { report ->
                TextButton(onClick = { client.desktopAction(Kanban.SEEN, Kanban.seenParams(report)) }, enabled = client.connected,
                    colors = ButtonDefaults.textButtonColors(contentColor = ink), modifier = Modifier.testTag("selfWorkDismiss")) { Text("Dismiss") }
            }
        }
    }
}

// People -------------------------------------------------------------------------------------------------------------

/** Who's on the board, like the members list of a ticket tool: a picture, a name, what they do and the projects they're on. */
@RequiresApi(29)
@Composable
private fun PeopleTab(client: LiveClient, board: KanbanBoard, onPerson: (String) -> Unit) {
    if (client.kanbanParticipants == false || !board.participantsKnown) {
        Column(Modifier.padding(20.dp).testTag("kanbanPeopleUnsupported"), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Note(Kanban.UPDATE_PARTICIPANTS)
            TextButton(onClick = client::checkDesktopFeatures, modifier = Modifier.heightIn(min = 48.dp)) { Text("Check again") }
        }
        return
    }
    var query by rememberSaveable { mutableStateOf("") }
    val people = remember(board) { Kanban.people(board) }
    val shown = remember(people, query) { people.filter { Kanban.matchesPerson(it, query) } }
    LazyColumn(Modifier.fillMaxSize().testTag("kanbanPeople"), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        item { Note("Everyone works only in their own projects when they work by themselves. Tap someone to change theirs.") }
        if (people.size > 8) item {
            OutlinedTextField(query, { query = it.take(80) }, Modifier.fillMaxWidth().testTag("kanbanPeopleSearch"), label = { Text("Find someone") }, singleLine = true)
        }
        if (people.isEmpty()) item { Note("Nobody's on the board yet.") }
        else if (shown.isEmpty()) item { Note("Nobody matches that.") }
        Kanban.peopleSections(shown).forEach { (section, members) ->
            item(key = "section:$section") { SectionTitle("$section · ${members.size}") }
            items(members, key = { "person:" + it.id }) { p -> PersonRow(board, p) { onPerson(p.id) } }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun PersonRow(board: KanbanBoard, person: KanbanParticipant, onOpen: () -> Unit) {
    val projects = remember(board, person) { Kanban.projectNames(board, person) }
    val open = remember(board, person) { Kanban.openTicketsOf(board, person).size }
    val shape = RoundedCornerShape(10.dp)
    Surface(onClick = onOpen, shape = shape, color = MaterialTheme.colorScheme.surface, modifier = Modifier.fillMaxWidth().heightIn(min = 72.dp)
        .border(1.dp, MaterialTheme.colorScheme.outlineVariant, shape).testTag("kanbanPerson")) {
        Row(Modifier.padding(12.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Avatar(person.name, person.id, 40.dp)
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text(person.name.ifBlank { "Unnamed" }, Modifier.weight(1f, fill = false), style = MaterialTheme.typography.titleSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    if (person.paused) Pill("Held", OrgTone.WAIT)
                }
                if (person.title.isNotBlank()) Text(person.title, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
                if (projects.isNotEmpty()) FlowRow(horizontalArrangement = Arrangement.spacedBy(4.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    projects.take(3).forEach { Pill(it, OrgTone.ACCENT, Modifier.widthIn(max = 140.dp)) }
                    if (projects.size > 3) Pill("+${projects.size - 3}", OrgTone.IDLE)
                }
                Kanban.boundaryText(person).takeIf { it.isNotBlank() }?.let {
                    Text(it, style = MaterialTheme.typography.bodySmall, color = if (projects.isEmpty() && person.hiddenProjects == 0) orgColors(OrgTone.WAIT).first else MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            if (open > 0) Text("$open", Modifier.semantics { contentDescription = "$open open tickets" }, style = MaterialTheme.typography.labelLarge.copy(fontFamily = FontFamily.Monospace), color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/** One person: who they are, the projects they can work in, and the tickets they hold. Changes go when Save is tapped. */
@RequiresApi(29)
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ColumnScope.KanbanPersonPage(client: LiveClient, board: KanbanBoard?, id: String, busy: Boolean, onTicket: (String) -> Unit, onBoard: (KanbanParticipant) -> Unit, onBack: () -> Unit) {
    val person = board?.let { Kanban.person(it, id) }
    // What the user has ticked, and the revision that editing began from. Nothing is kept while they match the desktop.
    var edit by rememberSaveable(id) { mutableStateOf<List<String>?>(null) }
    var base by rememberSaveable(id) { mutableStateOf<Long?>(null) }
    LaunchedEffect(person?.revision, person?.projects) { if (person != null && edit != null && Kanban.sameProjects(edit!!, person.projects)) { edit = null; base = null } }
    val selected = edit ?: person?.projects.orEmpty()
    val canSave = person != null && edit != null && !busy && client.kanbanParticipants == true
    Row(Modifier.fillMaxWidth().padding(start = 4.dp, end = 8.dp, top = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") }
        Text(person?.name ?: "Person", Modifier.weight(1f).semantics { heading() }, style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
        Button(onClick = { if (person != null) client.desktopAction(Kanban.PARTICIPANT, Kanban.participantParams(person, selected, base ?: person.revision)) }, enabled = canSave,
            modifier = Modifier.testTag("kanbanPersonSave")) { Text("Save") }
    }
    KanbanStateStrip(client, Kanban.BOARD, board != null)
    Box(Modifier.weight(1f)) {
        DesktopGate(client, Kanban.BOARD, "Loading this person…") {
            if (board == null || person == null) Note("This person is no longer on the board.", Modifier.padding(20.dp))
            else Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp).testTag("kanbanPersonPage"), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Avatar(person.name, person.id, 56.dp)
                    Column(Modifier.weight(1f)) {
                        Text(person.title.ifBlank { if (person.isRem) "Coordinator" else "Agent" }, style = MaterialTheme.typography.bodyMedium)
                        Text(if (person.isRem) "Rem" else person.organizationName, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    if (person.paused) Pill("Held", OrgTone.WAIT)
                }
                val openCount = Kanban.openTicketsOf(board, person).size
                FactGrid(listOf(
                    Fact(ThreadIcons.Folder, "Projects", "${person.projects.size + person.hiddenProjects}", if (person.projects.isEmpty() && person.hiddenProjects == 0) OrgTone.WAIT else OrgTone.ACCENT),
                    Fact(ThreadIcons.Kanban, "Open tickets", "$openCount", if (openCount > 0) OrgTone.RUN else null),
                ), Modifier.testTag("kanbanPersonFacts"))
                SectionTitle("Projects")
                InfoChip("How projects limit work", if (person.isRem) "During self-work, Rem takes tickets and researches only in these projects. Tickets you start yourself still run."
                    else "On its own check-ins, ${person.name} works only in these projects. Tickets you assign still run.", tag = "kanbanPersonProjectsInfo")
                if (base != null && base != person.revision) Surface(color = MaterialTheme.colorScheme.surfaceVariant, shape = RoundedCornerShape(10.dp), modifier = Modifier.fillMaxWidth().testTag("kanbanPersonStale")) {
                    Column(Modifier.padding(12.dp)) {
                        Text("These projects changed on your computer while you were editing.", style = MaterialTheme.typography.bodySmall)
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            TextButton(onClick = { edit = null; base = null }, modifier = Modifier.heightIn(min = 48.dp).testTag("kanbanPersonReload")) { Text("Use theirs") }
                            TextButton(onClick = { base = person.revision }, modifier = Modifier.heightIn(min = 48.dp).testTag("kanbanPersonKeep")) { Text("Keep mine") }
                        }
                    }
                }
                val choices = Kanban.projectChoices(board, person)
                if (choices.isEmpty()) Note(if (person.isRem) "No projects are open in Raticode on your computer." else "This organization has no projects yet. Add them on your computer.")
                choices.forEach { project ->
                    val checked = project.id in selected
                    val available = Kanban.canAdd(person, project)
                    Row(Modifier.fillMaxWidth().heightIn(min = 56.dp).toggleable(value = checked, enabled = !busy && (available || checked), role = Role.Checkbox, onValueChange = {
                        val next = Kanban.toggled(selected, project.id)
                        if (Kanban.sameProjects(next, person.projects)) { edit = null; base = null }
                        else { if (edit == null) base = person.revision; edit = next }
                    }).testTag("kanbanPersonProject-${project.id}"), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        Checkbox(checked, null, enabled = !busy && (available || checked))
                        Column(Modifier.weight(1f)) {
                            Text(project.name, style = MaterialTheme.typography.bodyLarge, maxLines = 2, overflow = TextOverflow.Ellipsis)
                            val detail = when { !available -> if (project.available) "Can only be removed" else "Not open on your computer"; else -> project.organization }
                            if (detail.isNotBlank()) Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        }
                    }
                }
                if (person.hiddenProjects > 0) Callout("Also assigned to ${person.hiddenProjects} ${if (person.hiddenProjects == 1) "project" else "projects"} this phone can't see. Saving here leaves ${if (person.hiddenProjects == 1) "it" else "them"} as they are.", OrgTone.IDLE, icon = ThreadIcons.Eye)
                if (edit != null) Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    TextButton(onClick = { edit = null; base = null }, modifier = Modifier.heightIn(min = 48.dp).testTag("kanbanPersonDiscard")) { Text("Discard changes") }
                    if (selected.isEmpty() && person.hiddenProjects == 0) Text("With no projects, this person does no self-work.", Modifier.weight(1f).align(Alignment.CenterVertically), style = MaterialTheme.typography.bodySmall, color = orgColors(OrgTone.WAIT).first)
                }
                val tickets = Kanban.openTicketsOf(board, person)
                SectionTitle("Open tickets · ${tickets.size}") {
                    if (tickets.isNotEmpty()) TextButton(onClick = { onBoard(person) }, modifier = Modifier.heightIn(min = 48.dp).testTag("kanbanPersonBoard")) { Text("On the board") }
                }
                if (tickets.isEmpty()) Note("Nothing open right now.")
                tickets.take(20).forEach { t -> KanbanCard(t, onOpen = { onTicket(t.uid) }) }
                if (tickets.size > 20) Note("And ${tickets.size - 20} more on the board.")
            }
        }
    }
}

/** A setting stepped with − and + buttons, its value between them, as the thread limit and daily cap rows are. */
@Composable
private fun StepRow(title: String, detail: String, value: String, tag: String, lessLabel: String, moreLabel: String,
                    canLess: Boolean, canMore: Boolean, onLess: () -> Unit, onMore: () -> Unit) {
    Row(Modifier.fillMaxWidth().heightIn(min = 56.dp).testTag(tag), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.bodyLarge)
            Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        FilledTonalIconButton(onClick = onLess, enabled = canLess,
            modifier = Modifier.size(48.dp).testTag("${tag}Less").semantics { contentDescription = lessLabel }) { Text("−", style = MaterialTheme.typography.titleLarge) }
        Text(value, Modifier.widthIn(min = 56.dp).testTag("${tag}Value").semantics { contentDescription = "$title $value" },
            style = MaterialTheme.typography.titleLarge.copy(fontFamily = FontFamily.Monospace), textAlign = TextAlign.Center)
        FilledTonalIconButton(onClick = onMore, enabled = canMore,
            modifier = Modifier.size(48.dp).testTag("${tag}More").semantics { contentDescription = moreLabel }) { Text("+", style = MaterialTheme.typography.titleLarge) }
    }
}
