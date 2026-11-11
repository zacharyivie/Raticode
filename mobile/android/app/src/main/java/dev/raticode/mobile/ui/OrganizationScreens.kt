package dev.raticode.mobile.ui

import androidx.activity.compose.BackHandler
import androidx.annotation.RequiresApi
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.*
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.graphics.toColorInt
import dev.raticode.mobile.live.*
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.time.LocalDate
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sin

// Tones from the desktop's OrganizationHome.css status pills, with dark-theme partners. Each pill also says its state in words.
internal enum class OrgTone { RUN, WAIT, BAD, DONE, IDLE, ACCENT }

@Composable
internal fun orgColors(tone: OrgTone): Pair<Color, Color> {
    val dark = MaterialTheme.colorScheme.background.luminance() < 0.5f
    return when (tone) {
        OrgTone.RUN -> if (dark) Color(0xFF7FD3CF) to Color(0xFF173436) else Color(0xFF17767B) to Color(0xFFDEF1F1)
        OrgTone.WAIT -> if (dark) Color(0xFFF0C27A) to Color(0xFF3A2C14) else Color(0xFF8F5D16) to Color(0xFFFBEFD9)
        OrgTone.BAD -> if (dark) Color(0xFFF4A39C) to Color(0xFF3D1D1B) else Color(0xFFA13C36) to Color(0xFFF8E3E1)
        OrgTone.DONE -> if (dark) Color(0xFF8FD9B6) to Color(0xFF17332A) else Color(0xFF23765A) to Color(0xFFE0F1E8)
        OrgTone.IDLE -> if (dark) Color(0xFFB8C4CE) to Color(0xFF263038) else Color(0xFF4D5D6A) to Color(0xFFE8EDF1)
        OrgTone.ACCENT -> if (dark) Color(0xFF8AC9E2) to Color(0xFF16313F) else Color(0xFF215879) to Color(0xFFDDEBF3)
    }
}

private fun columnTone(column: String) = when (column) {
    "running" -> OrgTone.RUN; "review" -> OrgTone.WAIT; "done" -> OrgTone.DONE; else -> OrgTone.IDLE
}

@Composable
internal fun Pill(text: String, tone: OrgTone, modifier: Modifier = Modifier) {
    val (ink, fill) = orgColors(tone)
    Text(text, modifier.clip(RoundedCornerShape(50)).background(fill).padding(horizontal = 8.dp, vertical = 2.dp),
        color = ink, style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.SemiBold), maxLines = 1)
}

/** A start hint in the desktop's tone: amber when it waits on you, the run color when it will start, muted otherwise. */
@Composable
private fun startHintColor(task: OrganizationTask, org: Organization): Color = when (OrganizationModel.startTone(task, org)) {
    "you" -> orgColors(OrgTone.WAIT).first
    "run" -> orgColors(OrgTone.RUN).first
    else -> MaterialTheme.colorScheme.onSurfaceVariant
}

internal fun hex(value: String, fallback: Color = Color(0xFF4D5D6A)) =
    runCatching { Color(value.toColorInt()) }.getOrDefault(fallback)

@Composable
internal fun Avatar(name: String, id: String, size: Dp = 32.dp) {
    Box(Modifier.size(size).clip(CircleShape).background(hex(OrganizationModel.hashColor(id))), contentAlignment = Alignment.Center) {
        Text(OrganizationModel.initials(name), color = Color.White, style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold,
            fontSize = (size.value * 0.36f).sp), maxLines = 1)
    }
}

@Composable
private fun Progress(fraction: Double, color: Color, label: String, modifier: Modifier = Modifier) {
    LinearProgressIndicator({ fraction.toFloat().coerceIn(0f, 1f) }, modifier.fillMaxWidth().height(6.dp).clip(RoundedCornerShape(3.dp))
        .semantics { contentDescription = "$label ${(fraction * 100).roundToInt()} percent done" }, color = color,
        trackColor = MaterialTheme.colorScheme.surfaceVariant, drawStopIndicator = {})
}

@Composable
private fun Card(onClick: (() -> Unit)?, modifier: Modifier = Modifier, stripe: Color? = null, content: @Composable ColumnScope.() -> Unit) {
    val shape = RoundedCornerShape(10.dp)
    val inner: @Composable () -> Unit = {
        Row(Modifier.height(IntrinsicSize.Min)) {
            if (stripe != null) Box(Modifier.width(3.dp).fillMaxHeight().background(stripe))
            Column(Modifier.weight(1f).padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp), content = content)
        }
    }
    val border = Modifier.border(1.dp, MaterialTheme.colorScheme.outlineVariant, shape)
    if (onClick != null) Surface(onClick = onClick, shape = shape, color = MaterialTheme.colorScheme.surface, modifier = modifier.fillMaxWidth().then(border)) { inner() }
    else Surface(shape = shape, color = MaterialTheme.colorScheme.surface, modifier = modifier.fillMaxWidth().then(border)) { inner() }
}

/** The organization screens' card, for their pages in other files. */
@Composable
internal fun OrgCard(onClick: (() -> Unit)?, modifier: Modifier = Modifier, stripe: Color? = null, content: @Composable ColumnScope.() -> Unit) =
    Card(onClick, modifier, stripe, content)

@Composable
internal fun SectionTitle(text: String, trailing: @Composable RowScope.() -> Unit = {}) {
    Row(Modifier.fillMaxWidth().padding(top = 12.dp, bottom = 2.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(text.uppercase(), Modifier.weight(1f).semantics { heading() }, style = MaterialTheme.typography.labelMedium.copy(letterSpacing = 0.8.sp,
            fontWeight = FontWeight.SemiBold), color = MaterialTheme.colorScheme.onSurfaceVariant)
        trailing()
    }
}

private fun dueText(value: String): String {
    val date = runCatching { LocalDate.parse(value.take(10)) }.getOrNull() ?: return value
    return date.format(DateTimeFormatter.ofPattern("MMM d", Locale.US)) + if (date.year != LocalDate.now().year) ", ${date.year}" else ""
}

/** A choice menu in a row of filters: as wide as its label, up to a limit. */
internal val MENU = Modifier.width(IntrinsicSize.Max).widthIn(max = 200.dp)

private fun percent(value: Double) = "${(value * 100).roundToInt()}%"
private fun plural(n: Int, one: String, many: String = one + "s") = "$n ${if (n == 1) one else many}"

// The list ---------------------------------------------------------------------------------------------------------

@RequiresApi(29)
@Composable
internal fun OrganizationsPage(client: LiveClient, onBack: (() -> Unit)? = null, openFirst: String? = null, onOpened: () -> Unit = {}) {
    var open by rememberSaveable { mutableStateOf<String?>(null) }
    LaunchedEffect(openFirst) { if (openFirst != null) { open = openFirst; onOpened() } }
    if (open != null) { OrganizationHome(client, open!!) { open = null }; return }
    DisposableEffect(Unit) { client.openDesktop("organizations.list"); onDispose { client.openDesktop(null) } }
    Column(Modifier.fillMaxSize()) {
        Header("Organizations", "AI teams that run on your desktop", onBack)
        Box(Modifier.weight(1f)) {
            DesktopGate(client, "organizations.list", "Loading organizations…") {
                @Suppress("UNCHECKED_CAST") val list = client.desktopViews["organizations.list"] as? List<OrganizationSummary> ?: emptyList()
                LazyColumn(Modifier.fillMaxSize().testTag("organizationList"), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    if (list.isEmpty()) item { Note("No organizations yet. Create one in Raticode on your computer.") }
                    items(list, key = { it.id }) { org ->
                        Card({ open = org.id }) {
                            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                                Avatar(org.name, org.id, 40.dp)
                                Column(Modifier.weight(1f)) {
                                    Text(org.name, style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                                    Text(listOf(plural(org.employees, "worker"), plural(org.openTasks, "open ticket")).joinToString(" · "),
                                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                    if (org.projects.isNotEmpty()) Text(org.projects.joinToString(", "), style = MaterialTheme.typography.labelSmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
                                }
                                StatePill(org.state)
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun StatePill(state: String, archived: Boolean = false) = when {
    archived -> Pill("Archived", OrgTone.IDLE)
    state == "running" -> Pill("Running", OrgTone.RUN)
    else -> Pill("Paused", OrgTone.WAIT)
}

// One organization -------------------------------------------------------------------------------------------------

private val TABS = listOf("org" to "Organization", "projects" to "Projects", "issues" to "Issues", "workers" to "Workers", "knowledge" to "Knowledge")

/** The desktop's five sections. Items open as full pages under the tabs; Back returns to where you were. */
@RequiresApi(29)
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun OrganizationHome(client: LiveClient, id: String, onBack: () -> Unit) {
    val key = "organizations.read:$id"
    val org = client.desktopViews[key] as? Organization
    var tab by rememberSaveable { mutableStateOf("org") }
    var stack by rememberSaveable { mutableStateOf(listOf<String>()) }
    // Board filters, kept while you look at a ticket and come back.
    var boardProject by rememberSaveable { mutableStateOf("") }
    var boardWorker by rememberSaveable { mutableStateOf("") }
    var needsMe by rememberSaveable { mutableStateOf(false) }
    // The board column on screen, kept while a ticket or a new-ticket page is open.
    var boardColumn by rememberSaveable { mutableStateOf("todo") }
    var confirm by remember { mutableStateOf<String?>(null) }
    val top = stack.lastOrNull()
    val noteId = top?.takeIf { it.startsWith("note:") }?.removePrefix("note:")
    val v2 = org?.plan != null && client.organizationsV2 != false
    val readsNote = noteId != null && v2
    DisposableEffect(readsNote, noteId) {
        if (readsNote) client.openDesktop(OrganizationModel.NOTE, JSONObject().put("organization_id", id).put("note_id", noteId))
        else client.openDesktop("organizations.read", JSONObject().put("organization_id", id))
        onDispose { client.openDesktop(null) }
    }
    BackHandler(enabled = stack.isNotEmpty()) { stack = stack.dropLast(1) }
    val busy = key in client.desktopBusy || !client.connected
    val nav = remember { OrgNav({ stack = stack + it }, { stack = stack.dropLast(1) }) { t -> tab = t; stack = emptyList() } }
    nav.push = { stack = stack + it }; nav.pop = { stack = stack.dropLast(1) }
    nav.tab = { t -> tab = t; stack = emptyList() }
    nav.board = { project, worker -> boardProject = project; boardWorker = worker; needsMe = false; tab = "issues"; stack = emptyList() }

    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(start = 4.dp, end = 8.dp, top = 6.dp, bottom = 2.dp), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onBack) { androidx.compose.material3.Icon(Icons.AutoMirrored.Filled.ArrowBack, "Organizations") }
            if (org != null) Avatar(org.name, org.id, 34.dp)
            Column(Modifier.weight(1f).padding(start = 10.dp)) {
                Text(org?.name ?: "Organization", style = MaterialTheme.typography.titleLarge, maxLines = 1, overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.semantics { heading() })
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                    if (org != null) StatePill(org.state, org.plan?.archived == true)
                    org?.plan?.hours?.takeIf { it.mode == "scheduled" }?.let { h -> Pill(if (h.open) "Working hours" else "Off hours", if (h.open) OrgTone.RUN else OrgTone.IDLE) }
                    Text(when { !client.connected -> "Offline"; key in client.desktopBusy -> "Saving…"; else -> "" }, style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            if (org != null && org.plan?.archived != true) TextButton(enabled = !busy, onClick = { confirm = if (org.state == "running") "pause" else "start" },
                modifier = Modifier.testTag("orgControl")) { Text(if (org.state == "running") "Pause" else "Start") }
        }
        Box(Modifier.weight(1f)) {
            DesktopGate(client, key, "Loading this organization…") {
                when {
                    org == null -> Note("This organization is no longer on your computer.", Modifier.padding(20.dp))
                    !v2 -> Column {
                        Surface(color = MaterialTheme.colorScheme.surfaceVariant, modifier = Modifier.fillMaxWidth()) {
                            Text("Update Raticode on your computer for projects, the board, workers and knowledge here.",
                                Modifier.padding(horizontal = 16.dp, vertical = 8.dp).testTag("orgUpdateDesktop"), style = MaterialTheme.typography.bodySmall)
                        }
                        Column(Modifier.weight(1f)) { LegacyOrganizationView(client, id) }
                    }
                    else -> {
                        val plan = org.plan!!
                        val attention = remember(org) { OrganizationModel.needsYou(org, plan).size }
                        Column(Modifier.fillMaxSize()) {
                            PrimaryScrollableTabRow(TABS.indexOfFirst { it.first == tab }.coerceAtLeast(0), edgePadding = 8.dp,
                                containerColor = MaterialTheme.colorScheme.background, modifier = Modifier.testTag("orgTabs")) {
                                TABS.forEach { (value, label) ->
                                    Tab(tab == value, { tab = value; stack = emptyList() }, modifier = Modifier.testTag("orgTab-$value"), text = {
                                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                                            Text(label, maxLines = 1)
                                            if (value == "org" && attention > 0) Badge { Text("$attention", modifier = Modifier.semantics { contentDescription = "$attention need you" }) }
                                        }
                                    })
                                }
                            }
                            Box(Modifier.weight(1f)) {
                                when {
                                    top == null -> when (tab) {
                                        "org" -> OverviewTab(client, org, plan, nav, busy)
                                        "projects" -> ProjectsTab(org, plan, nav)
                                        "issues" -> IssuesTab(client, org, plan, nav, busy, boardProject, boardWorker, needsMe, boardColumn,
                                            onColumn = { boardColumn = it }, onFilters = { p, w, n -> boardProject = p; boardWorker = w; needsMe = n })
                                        "workers" -> WorkersTab(org, plan, nav)
                                        else -> KnowledgeTab(org, plan, nav, busy)
                                    }
                                    top.startsWith("ticket:") -> TicketPage(client, org, plan, top.removePrefix("ticket:"), nav, busy)
                                    top.startsWith("project:") -> ProjectPage(org, plan, top.removePrefix("project:"), nav)
                                    top.startsWith("epic:") -> EpicPage(org, plan, top.removePrefix("epic:"), nav)
                                    top.startsWith("worker:") -> WorkerPage(client, org, plan, top.removePrefix("worker:"), nav, busy)
                                    top.startsWith("note:") -> NotePage(client, org, plan, top.removePrefix("note:"), nav)
                                    top.startsWith("new:ticket") -> NewTicketPage(client, org, plan, top.removePrefix("new:ticket").removePrefix(":"), nav, busy)
                                    top == "new:note" -> NewNotePage(client, org, plan, nav, busy)
                                    top == "schedule" || top == "schedule:template" -> SchedulePage(client, org, plan, top == "schedule:template", nav, busy)
                                    else -> Note("This page isn't available.", Modifier.padding(20.dp))
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    confirm?.let { kind ->
        val name = org?.name ?: "this organization"
        AlertDialog(onDismissRequest = { confirm = null }, title = { Text(if (kind == "start") "Start $name?" else "Pause $name?") },
            text = { Text(if (kind == "start") "Workers pick up To do tickets that are ready, within your limits and working hours. An organization with full-access workers starts only from your computer, where you can review them."
                else "Running tickets stop and go back to To do. Nothing new starts until you start it again.") },
            confirmButton = { TextButton(modifier = Modifier.testTag("orgConfirm"), onClick = {
                client.desktopAction("organizations.control", JSONObject().put("organization_id", id).put("state", if (kind == "start") "running" else "paused")); confirm = null
            }) { Text(if (kind == "start") "Start" else "Pause") } },
            dismissButton = { TextButton(onClick = { confirm = null }) { Text("Cancel") } })
    }
}

/** Moves between an organization's pages. [board] opens Issues filtered to a project or a worker. */
internal class OrgNav(var push: (String) -> Unit, var pop: () -> Unit, var tab: (String) -> Unit) {
    var board: (String, String) -> Unit = { _, _ -> }
}

@Composable
internal fun DetailHeader(title: String, nav: OrgNav, kicker: String = "", onBack: () -> Unit = nav.pop, actions: @Composable RowScope.() -> Unit = {}) {
    Row(Modifier.fillMaxWidth().padding(start = 4.dp, end = 8.dp, top = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        IconButton(onClick = onBack) { androidx.compose.material3.Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") }
        Column(Modifier.weight(1f)) {
            if (kicker.isNotBlank()) Text(kicker, style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1)
            Text(title, style = MaterialTheme.typography.titleMedium, maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.semantics { heading() })
        }
        actions()
    }
}

// Organization ------------------------------------------------------------------------------------------------------

@RequiresApi(29)
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun OverviewTab(client: LiveClient, org: Organization, plan: OrgPlan, nav: OrgNav, busy: Boolean) {
    val needs = remember(org) { OrganizationModel.needsYou(org, plan) }
    LazyColumn(Modifier.fillMaxSize().testTag("orgOverview"), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        item {
            val (ink, fill) = orgColors(OrgTone.ACCENT)
            Surface(color = fill, shape = RoundedCornerShape(12.dp), modifier = Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text("MISSION", style = MaterialTheme.typography.labelSmall.copy(letterSpacing = 1.sp), color = ink)
                    Text(plan.mission.ifBlank { org.description.ifBlank { "No mission yet. Write one in Organizations on your computer." } },
                        style = MaterialTheme.typography.titleMedium, color = if (plan.mission.isBlank()) MaterialTheme.colorScheme.onSurfaceVariant else ink)
                }
            }
        }
        item {
            val active = org.employees.count { !it.paused }
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Fact("${plan.projects.size}", "Projects") { nav.tab("projects") }
                Fact("${OrganizationModel.openTickets(org).size}", "Open tickets") { nav.tab("issues") }
                Fact("$active/${org.employees.size}", "Workers active") { nav.tab("workers") }
                Fact("${plan.usage.runs}", "Runs this month", null)
                Fact("${plan.notes.size}", "Notes") { nav.tab("knowledge") }
            }
        }
        if (org.pauseReason.isNotBlank() && org.state != "running") item { Note(org.pauseReason.let { if (it.startsWith("Paused")) it else "Paused: $it" }) }
        item { SectionTitle("Needs you${if (needs.isNotEmpty()) " · ${needs.size}" else ""}") }
        if (needs.isEmpty()) item { Note("Nothing needs you right now.") }
        items(needs.take(12)) { need -> NeedRow(org, need, nav) }
        item { SectionTitle("Work schedule") }
        item { ScheduleCard(client, org, plan, nav, busy) }
        val orgMilestones = plan.milestones.filter { it.projectId.isBlank() }.sortedBy { it.dueAt.ifBlank { "9999" } }
        if (orgMilestones.isNotEmpty()) {
            item { SectionTitle("Milestones") }
            items(orgMilestones, key = { "ms:" + it.id }) { m ->
                val progress = OrganizationModel.orgMilestoneProgress(org, plan, m.id)
                val serving = plan.milestones.filter { it.parentId == m.id && it.projectId.isNotBlank() }
                Card(null) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(m.title, Modifier.weight(1f), style = MaterialTheme.typography.titleSmall)
                        if (m.dueAt.isNotBlank()) Text(dueText(m.dueAt), style = MaterialTheme.typography.labelMedium.copy(fontFamily = FontFamily.Monospace),
                            color = if (runCatching { LocalDate.parse(m.dueAt.take(10)).isBefore(LocalDate.now()) }.getOrDefault(false) && progress < 1) orgColors(OrgTone.BAD).first
                                else MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    Progress(progress, orgColors(OrgTone.ACCENT).first, m.title)
                    Text(percent(progress) + " done", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    if (serving.isNotEmpty()) FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        serving.forEach { s -> AssistChip(onClick = { nav.push("project:${s.projectId}") }, label = { Text(s.title, maxLines = 1) },
                            leadingIcon = { Box(Modifier.size(8.dp).clip(CircleShape).background(hex(OrganizationModel.projectColor(plan, s.projectId)))) }) }
                    }
                }
            }
        }
        if (plan.projects.isNotEmpty()) {
            item { SectionTitle("Projects") }
            items(plan.projects, key = { "p:" + it.id }) { ProjectCard(org, plan, it, nav) }
        }
        plan.hours.takeIf { it.mode == "scheduled" }?.let { hours ->
            item { SectionTitle("Working hours") }
            item { Note(if (hours.open) "Open now${hours.closesAt.takeIf { it.isNotBlank() }?.let { ", closes ${whenText(it)}" }.orEmpty()}"
                else "Closed${hours.opensAt.takeIf { it.isNotBlank() }?.let { ", opens ${whenText(it)}" }.orEmpty()}. Work waits for working hours.") }
        }
        plan.repository?.let { repo ->
            item { SectionTitle("Shared repository") }
            item {
                Card(null) {
                    if (!repo.exists) Note("Turned on, but not created yet. It's created the first time a worker needs it.")
                    else {
                        Text("${plural(repo.files, "file")} · ${plural(repo.branches, "open branch", "open branches")}", style = MaterialTheme.typography.bodyMedium)
                        repo.commits.take(4).forEach { c ->
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                Text(c.sha.take(7), style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace), color = MaterialTheme.colorScheme.onSurfaceVariant)
                                Text(c.subject, Modifier.weight(1f), style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            }
                        }
                    }
                }
            }
        }
        item { Note("Mission, milestones, workers' settings and folders are edited in Organizations on your computer.", Modifier.padding(top = 12.dp)) }
    }
}

@Composable
private fun Fact(value: String, label: String, onClick: (() -> Unit)?) {
    val content: @Composable () -> Unit = {
        Column(Modifier.padding(horizontal = 14.dp, vertical = 10.dp)) {
            Text(value, style = MaterialTheme.typography.titleLarge.copy(fontFamily = FontFamily.Monospace), color = orgColors(OrgTone.ACCENT).first)
            Text(label, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
    val modifier = Modifier.widthIn(min = 96.dp).border(1.dp, MaterialTheme.colorScheme.outlineVariant, RoundedCornerShape(10.dp))
    if (onClick != null) Surface(onClick = onClick, shape = RoundedCornerShape(10.dp), modifier = modifier) { content() }
    else Surface(shape = RoundedCornerShape(10.dp), modifier = modifier) { content() }
}

@Composable
private fun NeedRow(org: Organization, need: OrganizationModel.NeedsYou, nav: OrgNav) {
    when (need) {
        is OrganizationModel.NeedsYou.Request -> Card(null, stripe = orgColors(OrgTone.WAIT).first) {
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) { Pill("Rem asks", OrgTone.WAIT); Text(whenText(need.request.at, past = true), style = MaterialTheme.typography.labelSmall) }
            Text(need.request.summary, style = MaterialTheme.typography.bodyMedium)
            Note("Approve or decline it in Organizations on your computer, where you can review what it changes.")
        }
        is OrganizationModel.NeedsYou.Review -> Card({ nav.push("ticket:${need.task.id}") }, stripe = orgColors(OrgTone.WAIT).first) {
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                Pill("To review", OrgTone.WAIT); Text(OrganizationModel.key(need.task), style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace))
            }
            Text(need.task.title, style = MaterialTheme.typography.bodyMedium)
        }
        is OrganizationModel.NeedsYou.Flag -> Card({ nav.push("ticket:${need.task.id}") }, stripe = orgColors(OrgTone.BAD).first) {
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                Pill(if (need.task.status == "blocked") "Blocked" else "Approve plan", OrgTone.BAD)
                Text(OrganizationModel.key(need.task), style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace))
            }
            Text(need.task.title, style = MaterialTheme.typography.bodyMedium)
            Text(need.text, style = MaterialTheme.typography.bodySmall, color = orgColors(OrgTone.BAD).first, maxLines = 3, overflow = TextOverflow.Ellipsis)
        }
        is OrganizationModel.NeedsYou.Stalled -> Card({ nav.push("worker:${need.employee.id}") }, stripe = orgColors(OrgTone.BAD).first) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                Avatar(need.employee.name, need.employee.id, 24.dp); Text("${need.employee.name}'s check-ins stopped", style = MaterialTheme.typography.bodyMedium)
            }
            Text(need.reason, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Note("Resume check-ins from the worker's page on your computer.")
        }
    }
}

// Projects ----------------------------------------------------------------------------------------------------------

@Composable
private fun ProjectsTab(org: Organization, plan: OrgPlan, nav: OrgNav) {
    LazyColumn(Modifier.fillMaxSize().testTag("orgProjects"), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        if (plan.projects.isEmpty()) item { Note("No projects yet. Add one in Organizations on your computer; tickets can still go on the board.") }
        items(plan.projects, key = { it.id }) { ProjectCard(org, plan, it, nav) }
        val loose = org.tasks.count { it.extras?.projectId.isNullOrBlank() && OrganizationModel.columnOf(it.status).let { c -> c != null && c != "done" } }
        if (loose > 0) item { TextButton(onClick = { nav.board("", "") }) { Text("${plural(loose, "open ticket")} without a project") } }
        if (org.projects.isNotEmpty()) item {
            SectionTitle("Folders")
            Note("This organization works in ${org.projects.joinToString(", ")}. Folders are added and removed on your computer.")
        }
    }
}

@Composable
private fun ProjectCard(org: Organization, plan: OrgPlan, project: OrgProject, nav: OrgNav) {
    val color = hex(OrganizationModel.projectColor(plan, project.id))
    val progress = OrganizationModel.projectProgress(org, plan, project.id)
    val open = OrganizationModel.openTickets(org, project.id).size
    val milestones = plan.milestones.count { it.projectId == project.id }
    Card({ nav.push("project:${project.id}") }, stripe = color, modifier = Modifier.testTag("orgProject")) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(project.name, Modifier.weight(1f), style = MaterialTheme.typography.titleSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(percent(progress), style = MaterialTheme.typography.labelMedium.copy(fontFamily = FontFamily.Monospace), color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Progress(progress, color, project.name)
        val lead = org.employees.firstOrNull { it.id == project.owner }
        Text(listOfNotNull(plural(milestones, "milestone"), plural(open, "open ticket"), lead?.let { "Lead: ${it.name}" }).joinToString(" · "),
            style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ProjectPage(org: Organization, plan: OrgPlan, projectId: String, nav: OrgNav) {
    val project = plan.projects.firstOrNull { it.id == projectId }
    Column(Modifier.fillMaxSize()) {
        DetailHeader(project?.name ?: "Project", nav, "PROJECT") {
            if (project != null) TextButton(onClick = { nav.board(project.id, "") }) { Text("On board") }
        }
        if (project == null) { Note("This project is no longer in the organization.", Modifier.padding(20.dp)); return@Column }
        val color = hex(OrganizationModel.projectColor(plan, project.id))
        LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            item {
                Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Progress(OrganizationModel.projectProgress(org, plan, project.id), color, project.name)
                    val lead = org.employees.firstOrNull { it.id == project.owner }
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        if (project.folder.isNotBlank()) Pill("Folder: ${project.folder}", OrgTone.IDLE)
                        if (lead != null) Pill("Lead: ${lead.name}", OrgTone.ACCENT)
                        if (project.checks > 0) Pill(plural(project.checks, "check"), OrgTone.DONE)
                    }
                    if (project.description.isNotBlank()) ChatMarkdown(project.description, compact = true)
                }
            }
            val milestones = plan.milestones.filter { it.projectId == project.id }.sortedBy { it.dueAt.ifBlank { "9999" } }
            milestones.forEach { m ->
                item(key = "m:" + m.id) {
                    val progress = OrganizationModel.milestoneProgress(org, plan, m.id)
                    val serves = plan.milestones.firstOrNull { it.id == m.parentId }
                    SectionTitle(m.title) { Text(listOfNotNull(m.dueAt.takeIf { it.isNotBlank() }?.let { "Due ${dueText(it)}" }, percent(progress)).joinToString(" · "),
                        style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
                    if (serves != null) Text("Serves ${serves.title}", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                val epics = plan.epics.filter { it.milestoneId == m.id }
                if (epics.isEmpty()) item(key = "m-empty:" + m.id) { Note("No epics yet.") }
                items(epics, key = { "e:" + it.id }) { EpicRow(org, plan, it, nav) }
            }
            val unscheduled = plan.epics.filter { e -> e.projectId == project.id && plan.milestones.none { it.id == e.milestoneId } }
            if (unscheduled.isNotEmpty()) {
                item { SectionTitle("Not scheduled") }
                items(unscheduled, key = { "u:" + it.id }) { EpicRow(org, plan, it, nav) }
            }
            if (milestones.isEmpty() && unscheduled.isEmpty()) item { Note("No milestones or epics yet. Plan them in Organizations on your computer.") }
        }
    }
}

@Composable
private fun EpicRow(org: Organization, plan: OrgPlan, epic: OrgEpic, nav: OrgNav) {
    val tickets = OrganizationModel.ticketsOfEpic(org, epic.id)
    val done = tickets.count { it.status == "done" }
    val state = OrganizationModel.epicState(org, epic)
    Card({ nav.push("epic:${epic.id}") }, modifier = Modifier.testTag("orgEpic")) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(epic.title, Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium, maxLines = 2, overflow = TextOverflow.Ellipsis)
            Pill(when (state) { "done" -> "Done"; "active" -> "In progress"; else -> "Planned" }, when (state) { "done" -> OrgTone.DONE; "active" -> OrgTone.RUN; else -> OrgTone.IDLE })
        }
        Progress(OrganizationModel.epicProgress(org, epic.id), hex(OrganizationModel.projectColor(plan, epic.projectId)), epic.title)
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(3.dp)) {
            Text("$done/${tickets.size}", style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace), color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.width(4.dp))
            // One small square per ticket, colored by its column, as on the desktop.
            tickets.take(24).forEach { t -> Box(Modifier.size(8.dp).clip(RoundedCornerShape(2.dp)).background(orgColors(columnTone(OrganizationModel.columnOf(t.status) ?: "todo")).first)) }
            Spacer(Modifier.weight(1f))
            org.employees.firstOrNull { it.id == epic.owner }?.let { Avatar(it.name, it.id, 20.dp) }
        }
    }
}

@Composable
private fun EpicPage(org: Organization, plan: OrgPlan, epicId: String, nav: OrgNav) {
    val epic = plan.epics.firstOrNull { it.id == epicId }
    Column(Modifier.fillMaxSize()) {
        DetailHeader(epic?.title ?: "Epic", nav, "EPIC") {
            if (epic != null) TextButton(onClick = { nav.push("new:ticket:${epic.id}") }) { Text("Add ticket") }
        }
        if (epic == null) { Note("This epic is no longer in the plan.", Modifier.padding(20.dp)); return@Column }
        val tickets = OrganizationModel.ticketsOfEpic(org, epic.id)
        LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            item {
                Progress(OrganizationModel.epicProgress(org, epic.id), hex(OrganizationModel.projectColor(plan, epic.projectId)), epic.title)
                val milestone = plan.milestones.firstOrNull { it.id == epic.milestoneId }
                val project = plan.projects.firstOrNull { it.id == epic.projectId }
                Text(listOfNotNull(project?.name, milestone?.title?.let { "Milestone: $it" }).joinToString(" · "), style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 6.dp))
                if (epic.description.isNotBlank()) ChatMarkdown(epic.description, compact = true)
            }
            OrganizationModel.COLUMNS.forEach { column ->
                val inColumn = tickets.filter { OrganizationModel.columnOf(it.status) == column }
                if (inColumn.isNotEmpty()) {
                    item(key = "c:$column") { SectionTitle("${OrganizationModel.COLUMN_LABELS[column]} · ${inColumn.size}") }
                    items(inColumn, key = { it.id }) { TicketCard(org, plan, it, { nav.push("ticket:${it.id}") }) }
                }
            }
            if (tickets.isEmpty()) item { Note("No tickets yet.") }
        }
    }
}

// Issues ------------------------------------------------------------------------------------------------------------

@RequiresApi(29)
@OptIn(ExperimentalFoundationApi::class, ExperimentalLayoutApi::class)
@Composable
private fun IssuesTab(client: LiveClient, org: Organization, plan: OrgPlan, nav: OrgNav, busy: Boolean, project: String, worker: String, needsMe: Boolean,
                      column: String, onColumn: (String) -> Unit, onFilters: (String, String, Boolean) -> Unit) {
    val pager = rememberPagerState(initialPage = OrganizationModel.COLUMNS.indexOf(column).coerceAtLeast(0)) { OrganizationModel.COLUMNS.size }
    LaunchedEffect(pager.currentPage) { onColumn(OrganizationModel.COLUMNS[pager.currentPage]) }
    val scope = rememberCoroutineScope()
    var moving by remember { mutableStateOf<OrganizationTask?>(null) }
    val filtered = org.tasks.filter { t ->
        OrganizationModel.columnOf(t.status) != null && (project.isBlank() || t.extras?.projectId == project) &&
            (worker.isBlank() || t.extras?.assigneeId == worker) &&
            (!needsMe || OrganizationModel.columnOf(t.status) == "review" || OrganizationModel.flag(t).isNotBlank())
    }
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 12.dp, vertical = 4.dp),
            horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
            ChoiceMenu(plan.projects.firstOrNull { it.id == project }?.name ?: "All projects", "Project",
                listOf("" to "All projects") + plan.projects.map { it.id to it.name }, project, MENU) { onFilters(it, worker, needsMe) }
            ChoiceMenu(org.employees.firstOrNull { it.id == worker }?.name ?: "All workers", "Worker",
                listOf("" to "All workers") + org.employees.map { it.id to it.name }, worker, MENU) { onFilters(project, it, needsMe) }
            FilterChip(needsMe, { onFilters(project, worker, !needsMe) }, label = { Text("Needs me") })
        }
        // One column at a time; swipe or tap a column's name to move between them.
        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 12.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            OrganizationModel.COLUMNS.forEachIndexed { index, column ->
                val n = filtered.count { OrganizationModel.columnOf(it.status) == column }
                FilterChip(pager.currentPage == index, { scope.launch { pager.animateScrollToPage(index) } },
                    label = { Text("${OrganizationModel.COLUMN_LABELS[column]} $n") }, modifier = Modifier.testTag("orgColumn-$column"),
                    leadingIcon = { Box(Modifier.size(8.dp).clip(CircleShape).background(orgColors(columnTone(column)).first)) })
            }
        }
        HorizontalPager(pager, Modifier.weight(1f).testTag("orgBoard"), beyondViewportPageCount = 1) { page ->
            val column = OrganizationModel.COLUMNS[page]
            val cards = filtered.filter { OrganizationModel.columnOf(it.status) == column }.sortedWith(
                compareByDescending<OrganizationTask> { it.running }.thenBy { OrganizationModel.flag(it).isBlank() }.thenByDescending { it.extras?.createdAt.orEmpty() })
            LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(start = 12.dp, end = 12.dp, top = 8.dp, bottom = 24.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (column in setOf("backlog", "todo")) item {
                    OutlinedButton(onClick = { nav.push("new:ticket" + if (column == "backlog") "" else ":todo") }, enabled = !busy && client.organizationsV2 != false,
                        modifier = Modifier.fillMaxWidth().testTag("orgNewTicket-$column")) {
                        androidx.compose.material3.Icon(ThreadIcons.Plus, null, Modifier.size(16.dp)); Spacer(Modifier.width(6.dp))
                        Text(if (column == "backlog") "New ticket in Backlog" else "New ticket in To do")
                    }
                }
                if (cards.isEmpty()) item { Note(when (column) {
                    "running" -> "Nothing is running. Workers pick up To do tickets while the organization runs."
                    "review" -> "Nothing to review."
                    else -> "No tickets here."
                }, Modifier.padding(8.dp)) }
                items(cards, key = { it.id }) { t -> TicketCard(org, plan, t, onOpen = { nav.push("ticket:${t.id}") }, onMove = { moving = t }) }
            }
        }
        Text("Long-press a ticket to move it.", Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp), style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
    moving?.let { t -> MoveSheet(client, org, t, busy) { moving = null } }
}

@OptIn(ExperimentalFoundationApi::class, ExperimentalLayoutApi::class)
@Composable
private fun TicketCard(org: Organization, plan: OrgPlan, task: OrganizationTask, onOpen: () -> Unit, onMove: (() -> Unit)? = null) {
    val column = OrganizationModel.columnOf(task.status) ?: "todo"
    val flag = OrganizationModel.flag(task)
    val epic = plan.epics.firstOrNull { it.id == task.extras?.epicId }
    val project = plan.projects.firstOrNull { it.id == task.extras?.projectId }
    val stripe = when { flag.isNotBlank() -> orgColors(OrgTone.BAD).first; task.running -> orgColors(OrgTone.RUN).first; column == "review" -> orgColors(OrgTone.WAIT).first; else -> null }
    val shape = RoundedCornerShape(10.dp)
    Surface(shape = shape, color = MaterialTheme.colorScheme.surface, modifier = Modifier.fillMaxWidth().border(1.dp, MaterialTheme.colorScheme.outlineVariant, shape)
        .clip(shape).combinedClickable(onClickLabel = "Open", onLongClickLabel = onMove?.let { "Move" }, onLongClick = onMove, onClick = onOpen)
        .semantics { onMove?.let { customActions = listOf(CustomAccessibilityAction("Move to another column") { it(); true }) } }.testTag("orgTicket")) {
        Row(Modifier.height(IntrinsicSize.Min)) {
            if (stripe != null) Box(Modifier.width(3.dp).fillMaxHeight().background(stripe))
            Column(Modifier.weight(1f).padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text(OrganizationModel.key(task), style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace), color = MaterialTheme.colorScheme.onSurfaceVariant)
                    (epic?.title ?: project?.name)?.let { name ->
                        Box(Modifier.size(7.dp).clip(CircleShape).background(hex(OrganizationModel.projectColor(plan, epic?.projectId ?: project?.id.orEmpty()))))
                        Text(name, Modifier.weight(1f), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    } ?: Spacer(Modifier.weight(1f))
                    if (task.priority in setOf("critical", "high")) Pill(task.priority.replaceFirstChar { it.uppercase() }, if (task.priority == "critical") OrgTone.BAD else OrgTone.WAIT)
                }
                Text(task.title, style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Medium), maxLines = 3, overflow = TextOverflow.Ellipsis)
                if (flag.isNotBlank()) Text(flag, style = MaterialTheme.typography.bodySmall, color = orgColors(OrgTone.BAD).first, maxLines = 2, overflow = TextOverflow.Ellipsis)
                else if (column == "todo") OrganizationModel.startHint(task, org).takeIf { it.isNotBlank() }?.let {
                    Text(it, style = MaterialTheme.typography.labelSmall, color = startHintColor(task, org), maxLines = 2, overflow = TextOverflow.Ellipsis)
                }
                if (task.running) LinearProgressIndicator(Modifier.fillMaxWidth().height(2.dp).semantics { contentDescription = "Running" }, color = orgColors(OrgTone.RUN).first)
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    task.extras?.evidence?.let { e -> EvidenceBadges(e) }
                    val notes = task.extras?.let { (it.notes + it.readNotes).distinct().size } ?: 0
                    if (notes > 0) Text(plural(notes, "note"), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Spacer(Modifier.weight(1f))
                    org.employees.firstOrNull { it.id == task.extras?.assigneeId }?.let { Avatar(it.name, it.id, 22.dp) }
                }
            }
        }
    }
}

@Composable
private fun EvidenceBadges(evidence: Map<String, Int>) {
    listOf("ok" to OrgTone.DONE, "warn" to OrgTone.WAIT, "bad" to OrgTone.BAD, "pending" to OrgTone.ACCENT, "none" to OrgTone.IDLE).forEach { (state, tone) ->
        val n = evidence[state] ?: 0
        if (n > 0) Pill("$n ${when (state) { "ok" -> "verified"; "warn" -> "changed"; "bad" -> "failed"; "pending" -> "checking"; else -> "unproven" }}", tone)
    }
}

/** Touch in place of the desktop's drag: the columns a ticket may move to, and why the others are closed. */
@RequiresApi(29)
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun MoveSheet(client: LiveClient, org: Organization, task: OrganizationTask, busy: Boolean, onDone: () -> Unit) {
    var accept by remember { mutableStateOf(false) }
    ModalBottomSheet(onDismissRequest = onDone) {
        Column(Modifier.fillMaxWidth().padding(horizontal = 20.dp).padding(bottom = 24.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text("Move ${OrganizationModel.key(task)}", style = MaterialTheme.typography.titleMedium, modifier = Modifier.semantics { heading() })
            Text(task.title, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2, overflow = TextOverflow.Ellipsis)
            Spacer(Modifier.height(8.dp))
            OrganizationModel.COLUMNS.forEach { column ->
                val rule = OrganizationModel.move(task, column)
                val label = OrganizationModel.COLUMN_LABELS[column].orEmpty()
                val enabled = !busy && (rule is OrganizationModel.Move.Allowed || rule is OrganizationModel.Move.Review)
                Surface(onClick = {
                    when (rule) {
                        is OrganizationModel.Move.Allowed -> { moveTicket(client, org, task, rule.column); onDone() }
                        is OrganizationModel.Move.Review -> accept = true
                        else -> Unit
                    }
                }, enabled = enabled, color = Color.Transparent, modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp).testTag("orgMove-$column")) {
                    Row(Modifier.padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        Box(Modifier.size(10.dp).clip(CircleShape).background(orgColors(columnTone(column)).first))
                        Column(Modifier.weight(1f)) {
                            Text(label + if (rule == null) " (here now)" else "", style = MaterialTheme.typography.bodyLarge,
                                color = if (enabled || rule == null) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant)
                            val detail = when (rule) {
                                is OrganizationModel.Move.Refused -> rule.reason
                                is OrganizationModel.Move.Review -> "Accepts the work after review."
                                is OrganizationModel.Move.Allowed -> when {
                                    rule.pull -> "Next for its worker. It runs when they're free."
                                    rule.reopen -> "Reopens it straight into ${label}."
                                    column == "backlog" -> "It won't run from Backlog."
                                    else -> "Ready for its worker."
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
    if (accept) AcceptDialog(client, org, task, { accept = false }) { accept = false; onDone() }
}

@RequiresApi(29)
private fun moveTicket(client: LiveClient, org: Organization, task: OrganizationTask, column: String) =
    client.desktopAction("organizations.task", JSONObject().put("organization_id", org.id).put("action", "move").put("task_id", task.id)
        .put("expected_revision", task.revision).put("column", column))

/** Accepting work, with the desktop's warning when its evidence isn't verified. */
@RequiresApi(29)
@Composable
private fun AcceptDialog(client: LiveClient, org: Organization, task: OrganizationTask, onDismiss: () -> Unit, onDone: () -> Unit) {
    val caution = OrganizationModel.reviewCaution(task)
    AlertDialog(onDismissRequest = onDismiss, title = { Text("Accept ${OrganizationModel.key(task)}?") },
        text = { Text(if (caution.isBlank()) "It moves to Done, and its epic and milestones count it." else "Its evidence: $caution. Read the ticket before accepting it.") },
        confirmButton = { TextButton(onClick = {
            client.desktopAction("organizations.task", JSONObject().put("organization_id", org.id).put("action", "review").put("task_id", task.id)
                .put("expected_revision", task.revision).put("decision", "accept").put("body", "Accepted from the phone")); onDone()
        }) { Text(if (caution.isBlank()) "Accept" else "Accept anyway") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } })
}

@RequiresApi(29)
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun TicketPage(client: LiveClient, org: Organization, plan: OrgPlan, taskId: String, nav: OrgNav, busy: Boolean) {
    val task = org.tasks.firstOrNull { it.id == taskId }
    var dialog by remember { mutableStateOf<String?>(null) }
    var moving by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxSize()) {
        DetailHeader(task?.title ?: "Ticket", nav, task?.let { OrganizationModel.key(it) }.orEmpty())
        if (task == null) { Note("This ticket is no longer on the board.", Modifier.padding(20.dp)); return@Column }
        val column = OrganizationModel.columnOf(task.status)
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp).testTag("orgTicketPage"), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Pill(if (task.status == "cancelled") "Cancelled" else if (task.status == "blocked") "Blocked" else OrganizationModel.COLUMN_LABELS[column].orEmpty(),
                    if (task.status == "blocked") OrgTone.BAD else columnTone(column ?: "todo"))
                if (task.running) Pill("Running", OrgTone.RUN)
                Pill(task.priority.replaceFirstChar { it.uppercase() }, if (task.priority == "critical") OrgTone.BAD else OrgTone.IDLE)
                if (task.dueAt.isNotBlank()) Pill("Due ${whenText(task.dueAt)}", OrgTone.IDLE)
            }
            OrganizationModel.flag(task).takeIf { it.isNotBlank() }?.let { Text(it, color = orgColors(OrgTone.BAD).first, style = MaterialTheme.typography.bodyMedium) }
            if (column == "todo") OrganizationModel.startHint(task, org).takeIf { it.isNotBlank() }?.let {
                Text(it, style = MaterialTheme.typography.bodyMedium, color = startHintColor(task, org))
            }
            // The actions this ticket allows now.
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                fun act(action: String, extra: JSONObject.() -> Unit = {}) = client.desktopAction("organizations.task", JSONObject().put("organization_id", org.id)
                    .put("action", action).put("task_id", task.id).put("expected_revision", task.revision).apply(extra))
                if (task.needsApproval && task.status != "done") Button(onClick = { act("approve") }, enabled = !busy) { Text("Approve plan") }
                if (task.needsReview && !task.running) Button(onClick = { dialog = "accept" }, enabled = !busy, modifier = Modifier.testTag("orgAccept")) { Text("Accept") }
                if (task.needsReview && !task.running) OutlinedButton(onClick = { dialog = "revise" }, enabled = !busy) { Text("Request changes") }
                if (column in setOf("backlog", "todo") && task.status != "blocked" && !task.running)
                    OutlinedButton(onClick = { moveTicket(client, org, task, "running") }, enabled = !busy) { Text("Start next") }
                if (task.status != "cancelled") OutlinedButton(onClick = { moving = true }, enabled = !busy, modifier = Modifier.testTag("orgMoveTicket")) { Text("Move") }
                if (!task.running && task.status in setOf("done", "cancelled", "blocked")) OutlinedButton(onClick = { act("reopen") }, enabled = !busy) { Text("Reopen") }
                OutlinedButton(onClick = { dialog = "comment" }, enabled = !busy) { Text("Comment") }
                if (task.status !in setOf("done", "cancelled")) TextButton(onClick = { dialog = "cancel" }, enabled = !busy,
                    colors = ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.error)) { Text("Cancel ticket") }
            }
            val extras = task.extras
            if (!extras?.doneWhen.isNullOrBlank() || task.description.isNotBlank()) SettingsGroup("Why") {
                extras?.doneWhen?.takeIf { it.isNotBlank() }?.let { Text("Done when", style = MaterialTheme.typography.labelMedium); Text(it, style = MaterialTheme.typography.bodyMedium) }
                // A long description folds like a long message, as the desktop keeps Details folded.
                if (task.description.isNotBlank()) FoldableText(toggleColor = MaterialTheme.colorScheme.primary) { ChatMarkdown(task.description, compact = true) }
            }
            SettingsGroup("Part of") {
                val project = plan.projects.firstOrNull { it.id == extras?.projectId }
                val epic = plan.epics.firstOrNull { it.id == extras?.epicId }
                val milestone = plan.milestones.firstOrNull { it.id == epic?.milestoneId }
                val worker = org.employees.firstOrNull { it.id == extras?.assigneeId }
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    project?.let { p -> AssistChip(onClick = { nav.push("project:${p.id}") }, label = { Text(p.name) },
                        leadingIcon = { Box(Modifier.size(8.dp).clip(CircleShape).background(hex(OrganizationModel.projectColor(plan, p.id)))) }) }
                    milestone?.let { m -> AssistChip(onClick = { nav.push("project:${m.projectId}") }, label = { Text("Milestone: ${m.title}") }) }
                    epic?.let { e -> AssistChip(onClick = { nav.push("epic:${e.id}") }, label = { Text("Epic: ${e.title}") }) }
                    worker?.let { w -> AssistChip(onClick = { nav.push("worker:${w.id}") }, label = { Text(w.name) }, leadingIcon = { Avatar(w.name, w.id, 18.dp) }) }
                }
                if (project == null && epic == null && worker == null) Note("Not linked to a project or epic, and nobody is assigned.")
            }
            if (extras != null && extras.claims.isNotEmpty()) SettingsGroup("Evidence") {
                extras.claims.forEach { claim ->
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.Top) {
                        Pill(OrganizationModel.EVIDENCE_LABELS[claim.state] ?: claim.state, when (claim.state) {
                            "ok" -> OrgTone.DONE; "warn" -> OrgTone.WAIT; "bad" -> OrgTone.BAD; "queued", "running" -> OrgTone.ACCENT; else -> OrgTone.IDLE })
                        Text(claim.text, Modifier.weight(1f), style = MaterialTheme.typography.bodySmall)
                    }
                }
                Note("Checks run from your computer.")
            } else if (task.evidence.isNotBlank()) SettingsGroup("Evidence") { ClampedText(task.evidence, 6) }
            val used = extras?.let { (it.notes + it.readNotes).distinct() }.orEmpty().mapNotNull { id -> plan.notes.firstOrNull { it.id == id } }
            if (used.isNotEmpty()) SettingsGroup("Notes it uses") {
                used.forEach { n -> NoteLine(n, if (n.id in extras!!.readNotes) "Read by a run" else "Planned") { nav.push("note:${n.id}") } }
            }
            SettingsGroup("Conversation") {
                if (task.comments.isEmpty()) Note("No comments yet.")
                // The desktop's last 12, each with its author's name and avatar; a long comment keeps to a few lines until opened.
                task.comments.takeLast(12).forEach { c ->
                    val name = OrganizationModel.actorName(c.actor, org)
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                        Avatar(name, c.actor.removePrefix("employee:"), 20.dp)
                        Text(name, style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.SemiBold))
                        if (c.decision.isNotBlank()) Pill(c.decision.replaceFirstChar { it.uppercase() }, if (c.decision == "accept") OrgTone.DONE else OrgTone.WAIT)
                        if (c.at.isNotBlank()) Text(whenText(c.at, past = true), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    ClampedText(c.body, 5, Modifier.padding(start = 26.dp))
                }
                Note("The newest comments; the whole conversation and run history are on your computer.")
            }
        }
    }
    if (moving && task != null) MoveSheet(client, org, task, busy) { moving = false }
    if (dialog == "accept" && task != null) AcceptDialog(client, org, task, { dialog = null }) { dialog = null }
    dialog?.takeIf { it in setOf("revise", "comment", "cancel") }?.let { kind ->
        var body by rememberSaveable(kind) { mutableStateOf("") }
        AlertDialog(onDismissRequest = { dialog = null }, title = { Text(when (kind) { "revise" -> "Request changes"; "comment" -> "Comment"; else -> "Cancel this ticket?" }) },
            text = {
                if (kind == "cancel") Text("The desktop stops this ticket and any run started for it. You can reopen it later.")
                else OutlinedTextField(body, { body = it.take(8000) }, Modifier.fillMaxWidth(), minLines = 3, maxLines = 8,
                    label = { Text(if (kind == "revise") "What should change?" else "Comment") })
            },
            confirmButton = { TextButton(enabled = kind == "cancel" || body.isNotBlank(), onClick = {
                val p = JSONObject().put("organization_id", org.id).put("task_id", task!!.id).put("expected_revision", task.revision)
                when (kind) {
                    "revise" -> p.put("action", "review").put("decision", "revise").put("body", body.trim())
                    "comment" -> p.put("action", "comment").put("body", body.trim())
                    else -> p.put("action", "cancel")
                }
                client.desktopAction("organizations.task", p); dialog = null
            }) { Text(when (kind) { "cancel" -> "Cancel ticket"; "revise" -> "Send back"; else -> "Send" }) } },
            dismissButton = { TextButton(onClick = { dialog = null }) { Text(if (kind == "cancel") "Keep" else "Close") } })
    }
}

/** A new ticket. It starts in Backlog, or To do when opened from that column, so nothing runs by surprise. */
@RequiresApi(29)
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun NewTicketPage(client: LiveClient, org: Organization, plan: OrgPlan, context: String, nav: OrgNav, busy: Boolean) {
    val presetEpic = plan.epics.firstOrNull { it.id == context }
    var title by rememberSaveable { mutableStateOf("") }
    var body by rememberSaveable { mutableStateOf("") }
    var priority by rememberSaveable { mutableStateOf("medium") }
    var assignee by rememberSaveable { mutableStateOf("") }
    var epic by rememberSaveable { mutableStateOf(presetEpic?.id.orEmpty()) }
    var status by rememberSaveable { mutableStateOf(if (context == "todo") "todo" else "backlog") }
    Column(Modifier.fillMaxSize()) {
        DetailHeader("New ticket", nav) {
            Button(enabled = title.isNotBlank() && !busy, modifier = Modifier.testTag("orgCreateTicket"), onClick = {
                val chosen = plan.epics.firstOrNull { it.id == epic }
                client.desktopAction("organizations.task", JSONObject().put("organization_id", org.id).put("action", "create").put("title", title.trim())
                    .put("description", body.trim()).put("priority", priority).put("status", status).apply {
                        if (assignee.isNotBlank()) put("assignee", assignee)
                        if (chosen != null) { put("epic_id", chosen.id); if (chosen.projectId.isNotBlank()) put("project_id", chosen.projectId) }
                    })
                nav.pop()
            }) { Text("Add") }
        }
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            OutlinedTextField(title, { title = it.take(500) }, Modifier.fillMaxWidth().testTag("orgTicketTitle"), label = { Text("Title") }, singleLine = true)
            OutlinedTextField(body, { body = it.take(8000) }, Modifier.fillMaxWidth(), label = { Text("Description (optional)") }, minLines = 3, maxLines = 8)
            Text("Starts in", style = MaterialTheme.typography.titleSmall)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                FilterChip(status == "backlog", { status = "backlog" }, label = { Text("Backlog") })
                FilterChip(status == "todo", { status = "todo" }, label = { Text("To do") })
            }
            Note(if (status == "todo") "To do tickets run once their worker is free while the organization runs." else "Backlog tickets wait until you move them to To do.")
            if (plan.epics.isNotEmpty()) {
                Text("Epic", style = MaterialTheme.typography.titleSmall)
                ChoiceMenu(plan.epics.firstOrNull { it.id == epic }?.title ?: "No epic", "Epic", listOf("" to "No epic") + plan.epics.map { it.id to it.title }, epic) { epic = it }
            }
            Text("Priority", style = MaterialTheme.typography.titleSmall)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                listOf("critical", "high", "medium", "low").forEach { value -> FilterChip(priority == value, { priority = value }, label = { Text(value.replaceFirstChar { it.uppercase() }) }) }
            }
            if (org.employees.isNotEmpty()) {
                Text("Assign to", style = MaterialTheme.typography.titleSmall)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    FilterChip(assignee.isBlank(), { assignee = "" }, label = { Text("Unassigned") })
                    org.employees.forEach { e -> FilterChip(assignee == e.id, { assignee = e.id }, label = { Text(e.name) }, leadingIcon = { Avatar(e.name, e.id, 18.dp) }) }
                }
            }
        }
    }
}

// Workers -----------------------------------------------------------------------------------------------------------

@Composable
private fun WorkersTab(org: Organization, plan: OrgPlan, nav: OrgNav) {
    val rows = remember(org.employees) { OrganizationModel.reportingTree(org.employees) }
    val line = MaterialTheme.colorScheme.outlineVariant
    LazyColumn(Modifier.fillMaxSize().testTag("orgWorkers"), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        item {
            Card(null) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Box(Modifier.size(36.dp).clip(CircleShape).background(orgColors(OrgTone.ACCENT).second), contentAlignment = Alignment.Center) {
                        Text("You", style = MaterialTheme.typography.labelSmall, color = orgColors(OrgTone.ACCENT).first)
                    }
                    Column { Text("You", style = MaterialTheme.typography.titleSmall); Text("Owner, with Rem coordinating", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
                }
            }
        }
        if (rows.isEmpty()) item { Note("No workers yet. Add them in Organizations on your computer.") }
        items(rows, key = { it.employee.id }) { row ->
            val e = row.employee
            val (state, label) = OrganizationModel.workerStatus(org, e)
            val worker = plan.workers[e.id]
            val current = OrganizationModel.currentTicket(org, e.id)
            // The reporting line: one indent and connector per level under you.
            Row(Modifier.fillMaxWidth().height(IntrinsicSize.Min)) {
                repeat(row.depth + 1) { level ->
                    Box(Modifier.width(18.dp).fillMaxHeight()) {
                        Canvas(Modifier.fillMaxSize()) {
                            val x = size.width / 2
                            drawLine(line, Offset(x, 0f), Offset(x, if (level == row.depth) size.height / 2 else size.height), strokeWidth = 2f)
                            if (level == row.depth) drawLine(line, Offset(x, size.height / 2), Offset(size.width, size.height / 2), strokeWidth = 2f)
                        }
                    }
                }
                Card({ nav.push("worker:${e.id}") }, modifier = Modifier.weight(1f).testTag("orgWorker")) {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        Avatar(e.name, e.id, 34.dp)
                        Column(Modifier.weight(1f)) {
                            Text(e.name, style = MaterialTheme.typography.titleSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(e.title.ifBlank { worker?.role.orEmpty() }, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                                maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(current?.let { "On ${OrganizationModel.key(it)}" } ?: worker?.responsibilities?.size?.takeIf { it > 0 }?.let { plural(it, "responsibility", "responsibilities") }.orEmpty(),
                                style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace), color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        Pill(label, when (state) { "working" -> OrgTone.RUN; "needs" -> OrgTone.WAIT; "paused" -> OrgTone.IDLE; else -> OrgTone.IDLE })
                    }
                }
            }
        }
    }
}

@RequiresApi(29)
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun WorkerPage(client: LiveClient, org: Organization, plan: OrgPlan, employeeId: String, nav: OrgNav, busy: Boolean) {
    val e = org.employees.firstOrNull { it.id == employeeId }
    var ask by remember { mutableStateOf(false) }
    var hold by remember { mutableStateOf<Boolean?>(null) }
    Column(Modifier.fillMaxSize()) {
        DetailHeader(e?.name ?: "Worker", nav, "WORKER")
        if (e == null) { Note("This worker is no longer in the organization.", Modifier.padding(20.dp)); return@Column }
        val worker = plan.workers[e.id]
        val (state, label) = OrganizationModel.workerStatus(org, e)
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp).testTag("orgWorkerPage"), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Avatar(e.name, e.id, 52.dp)
                Column(Modifier.weight(1f)) {
                    Text(e.title.ifBlank { worker?.role.orEmpty() }.ifBlank { "Worker" }, style = MaterialTheme.typography.bodyMedium)
                    Pill(label, when (state) { "working" -> OrgTone.RUN; "needs" -> OrgTone.WAIT; else -> OrgTone.IDLE })
                }
            }
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Button(onClick = { ask = true }, enabled = !busy && !e.paused) { Text("Ask") }
                if (e.paused) OutlinedButton(onClick = { hold = false }, enabled = !busy && client.organizationsV2 != false && worker?.fullAccess != true,
                    modifier = Modifier.testTag("orgRelease")) { Text("Release hold") }
                else OutlinedButton(onClick = { hold = true }, enabled = !busy && client.organizationsV2 != false, modifier = Modifier.testTag("orgHold")) { Text("Hold") }
            }
            if (e.paused && worker?.fullAccess == true) Note("This worker runs without a sandbox, so its hold is released on your computer, where you can review its access.")
            worker?.persona?.takeIf { it.isNotBlank() }?.let { SettingsGroup("Persona") { ClampedText(it, 6, style = MaterialTheme.typography.bodyMedium) } }
            if (!worker?.responsibilities.isNullOrEmpty()) SettingsGroup("Responsibilities") {
                worker!!.responsibilities.forEach { Text("• $it", style = MaterialTheme.typography.bodySmall) }
            }
            SettingsGroup("Reporting") {
                val manager = org.employees.firstOrNull { it.id == e.reportsTo }
                Text("Reports to ${manager?.name ?: "you"}", style = MaterialTheme.typography.bodyMedium)
                val reports = org.employees.filter { it.reportsTo == e.id }
                if (reports.isNotEmpty()) FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    reports.forEach { r -> AssistChip(onClick = { nav.push("worker:${r.id}") }, label = { Text(r.name) }, leadingIcon = { Avatar(r.name, r.id, 18.dp) }) }
                }
            }
            SettingsGroup("Model and access") {
                Text(listOf(e.provider, e.model, worker?.effort.orEmpty()).filter { it.isNotBlank() }.joinToString(" · ").ifBlank { "Organization default" },
                    style = MaterialTheme.typography.bodyMedium)
                worker?.let { w ->
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) { Pill(w.permission, if (w.fullAccess) OrgTone.BAD else OrgTone.IDLE) }
                    Text(when {
                        w.ticketAccess == null -> "Tickets: as its tools allow, in every project"
                        w.ticketAccess.isEmpty() -> "Tickets: can't create or change tickets"
                        else -> "Tickets: " + w.ticketAccess.joinToString("; ") { (project, allowed) ->
                            "${if (project == "*") "every project" else plan.projects.firstOrNull { it.id == project }?.name ?: project}: ${allowed.joinToString(", ").ifBlank { "nothing" }}"
                        }
                    }, style = MaterialTheme.typography.bodySmall)
                    if (w.heartbeatSeconds > 0) Text("Checks in every ${w.heartbeatSeconds / 60} min", style = MaterialTheme.typography.bodySmall)
                    if (w.stalled.isNotBlank()) Text("Check-ins stopped: ${w.stalled}", style = MaterialTheme.typography.bodySmall, color = orgColors(OrgTone.BAD).first)
                }
                Note("Change a worker's model, access and persona in Organizations on your computer.")
            }
            val mine = org.tasks.filter { it.extras?.assigneeId == e.id && OrganizationModel.columnOf(it.status).let { c -> c != null && c != "done" } }
            SettingsGroup("Open tickets · ${mine.size}") {
                if (mine.isEmpty()) Note("Nothing assigned.")
                mine.forEach { t -> TicketCard(org, plan, t, { nav.push("ticket:${t.id}") }) }
                if (mine.isNotEmpty()) TextButton(onClick = { nav.board("", e.id) }) { Text("Show on the board") }
            }
        }
    }
    if (ask && e != null) {
        var message by rememberSaveable { mutableStateOf("") }
        AlertDialog(onDismissRequest = { ask = false }, title = { Text("Ask ${e.name}") },
            text = { OutlinedTextField(message, { message = it.take(4000) }, Modifier.fillMaxWidth(), label = { Text("What should they look at?") }, minLines = 2, maxLines = 5) },
            confirmButton = { TextButton(enabled = message.isNotBlank(), onClick = {
                client.desktopAction("organizations.task", JSONObject().put("organization_id", org.id).put("action", "wake").put("employee_id", e.id).put("body", message.trim()))
                ask = false
            }) { Text("Send") } }, dismissButton = { TextButton(onClick = { ask = false }) { Text("Cancel") } })
    }
    hold?.let { paused ->
        AlertDialog(onDismissRequest = { hold = null }, title = { Text(if (paused) "Hold ${e?.name}?" else "Release ${e?.name}'s hold?") },
            text = { Text(if (paused) "They stop picking up tickets. A run already going finishes or stops as the organization's settings say."
                else "They pick up their To do tickets again while the organization runs.") },
            confirmButton = { TextButton(modifier = Modifier.testTag("orgConfirm"), onClick = {
                client.desktopAction(OrganizationModel.WORKER, JSONObject().put("organization_id", org.id).put("employee_id", employeeId).put("paused", paused)); hold = null
            }) { Text(if (paused) "Hold" else "Release") } }, dismissButton = { TextButton(onClick = { hold = null }) { Text("Cancel") } })
    }
}

// Knowledge ---------------------------------------------------------------------------------------------------------

@Composable
private fun kindColor(kind: String): Color {
    val dark = MaterialTheme.colorScheme.background.luminance() < 0.5f
    return when (kind) {
        "decision" -> if (dark) Color(0xFFC3B2F2) else Color(0xFF6A4BB0)
        "report" -> if (dark) Color(0xFFF0C27A) else Color(0xFF8F5D16)
        "spec" -> if (dark) Color(0xFF7FD3CF) else Color(0xFF17767B)
        "finding" -> if (dark) Color(0xFFF4A39C) else Color(0xFFA13C36)
        else -> if (dark) Color(0xFF9DBEF0) else Color(0xFF2F6FB3)
    }
}

@Composable
private fun KindLabel(kind: String) {
    val color = kindColor(kind)
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        Box(Modifier.size(8.dp).clip(CircleShape).background(color))
        Text(OrganizationModel.KINDS.firstOrNull { it.first == kind }?.second ?: "Note", style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.SemiBold), color = color)
    }
}

@Composable
private fun NoteLine(note: OrgNote, detail: String, onOpen: () -> Unit) {
    Surface(onClick = onOpen, color = Color.Transparent, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)) {
        Row(Modifier.padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            KindLabel(note.kind)
            Text(note.title, Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (detail.isNotBlank()) Text(detail, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun KnowledgeTab(org: Organization, plan: OrgPlan, nav: OrgNav, busy: Boolean) {
    var query by rememberSaveable { mutableStateOf("") }
    var kinds by rememberSaveable { mutableStateOf(OrganizationModel.KINDS.map { it.first }) }
    var project by rememberSaveable { mutableStateOf("all") }
    val degree = remember(plan) { OrganizationModel.degree(plan) }
    val shown = plan.notes.filter { n ->
        n.kind in kinds && (project == "all" || (project == "org" && n.projectId.isBlank()) || n.projectId == project) &&
            (query.isBlank() || "${n.title}\n${n.summary}\n${n.topic}".contains(query.trim(), ignoreCase = true))
    }
    LazyColumn(Modifier.fillMaxSize().testTag("orgKnowledge"), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        item {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(query, { query = it.take(200) }, Modifier.weight(1f).testTag("orgKnowledgeSearch"), singleLine = true,
                    placeholder = { Text("Search notes") }, leadingIcon = { androidx.compose.material3.Icon(ThreadIcons.Search, null, Modifier.size(18.dp)) },
                    shape = MaterialTheme.shapes.large)
                FilledTonalButton(onClick = { nav.push("new:note") }, enabled = !busy, modifier = Modifier.testTag("orgNewNote")) { Text("New") }
            }
        }
        item {
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                OrganizationModel.KINDS.forEach { (kind, label) ->
                    FilterChip(kind in kinds, { kinds = if (kind in kinds) (kinds - kind).ifEmpty { kinds } else kinds + kind }, label = { Text(label) },
                        leadingIcon = { Box(Modifier.size(8.dp).clip(CircleShape).background(kindColor(kind))) })
                }
                if (plan.projects.isNotEmpty()) ChoiceMenu(when (project) { "all" -> "All notes"; "org" -> "Organization-wide"; else -> plan.projects.firstOrNull { it.id == project }?.name ?: "Project" },
                    "Project", listOf("all" to "All notes", "org" to "Organization-wide") + plan.projects.map { it.id to it.name }, project, MENU) { project = it }
            }
        }
        if (plan.notes.isEmpty()) item { Note("No notes yet. Workers save what they learn here, and you can add notes too.") }
        else if (shown.isEmpty()) item { Note("No notes match.") }
        OrganizationModel.topics(shown).forEach { (topic, notes) ->
            item(key = "t:$topic") { SectionTitle("$topic · ${notes.size}") }
            items(notes, key = { "n:" + it.id }) { n ->
                val links = degree[n.id] ?: 0
                val tickets = org.tasks.count { t -> t.extras?.let { n.id in it.notes || n.id in it.readNotes } == true }
                Card({ nav.push("note:${n.id}") }, stripe = kindColor(n.kind), modifier = Modifier.testTag("orgNote")) {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        KindLabel(n.kind)
                        if (n.secondBrain) Pill("Second Brain", OrgTone.ACCENT)
                        if (n.changed) Pill("Source changed", OrgTone.WAIT)
                        Spacer(Modifier.weight(1f))
                        if (n.updatedAt.isNotBlank()) Text(whenText(n.updatedAt, past = true), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    Text(n.title, style = MaterialTheme.typography.titleSmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    if (n.summary.isNotBlank()) Text(n.summary, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    Text(listOfNotNull(plural(links, "link"), tickets.takeIf { it > 0 }?.let { "used by ${plural(it, "ticket")}" }, n.author.ifBlank { null }).joinToString(" · "),
                        style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
    }
}

/** A note with its neighbors drawn around it; tap one to walk the graph. */
@RequiresApi(29)
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun NotePage(client: LiveClient, org: Organization, plan: OrgPlan, noteId: String, nav: OrgNav) {
    val key = OrganizationModel.NOTE + ":" + org.id + ":" + noteId
    val detail = client.desktopViews[key] as? OrgNoteDetail
    val summary = plan.notes.firstOrNull { it.id == noteId }
    val neighbors = remember(org, noteId) { OrganizationModel.neighbors(org, plan, noteId) }
    Column(Modifier.fillMaxSize()) {
        DetailHeader(detail?.title ?: summary?.title ?: "Note", nav, "NOTE")
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp).testTag("orgNotePage"), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            val kind = detail?.kind ?: summary?.kind ?: "note"
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp), itemVerticalAlignment = Alignment.CenterVertically) {
                KindLabel(kind)
                Pill(detail?.topic ?: summary?.topic ?: "General", OrgTone.IDLE)
                (detail?.projectId ?: summary?.projectId)?.let { id -> plan.projects.firstOrNull { it.id == id } }?.let { Pill(it.name, OrgTone.ACCENT) }
                if (detail?.secondBrain == true || summary?.secondBrain == true) Pill("From Second Brain", OrgTone.ACCENT)
                if (detail?.sourceMissing == true) Pill("Source missing", OrgTone.BAD)
            }
            Text(listOfNotNull((detail?.author ?: summary?.author)?.ifBlank { null }, (detail?.updatedAt ?: summary?.updatedAt)?.ifBlank { null }?.let { "updated ${whenText(it, past = true)}" })
                .joinToString(" · "), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            (detail?.summary ?: summary?.summary)?.takeIf { it.isNotBlank() }?.let { Text(it, style = MaterialTheme.typography.bodyLarge) }
            if (neighbors.notes.isNotEmpty() || neighbors.tickets.isNotEmpty()) NoteGraph(summary, neighbors, kind, nav)
            Box(Modifier.fillMaxWidth()) {
                DesktopGate(client, key, "Loading the note…") {
                    if (detail != null) Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        if (detail.body.isNotBlank()) ChatMarkdown(detail.body) else Note("This note has no body.")
                        if (detail.truncated) Note("The rest of this note is on your computer.")
                    }
                }
            }
            if (neighbors.notes.isNotEmpty()) SettingsGroup("Linked notes · ${neighbors.notes.size}") {
                neighbors.notes.forEach { n ->
                    val quote = detail?.connections?.firstOrNull { c -> c.kind == "asserted" && (c.from == "kb:${n.id}" || c.to == "kb:${n.id}") }
                    NoteLine(n, if (quote != null) (if (quote.state == "warn") "Quote changed" else "Checked") else "") { nav.push("note:${n.id}") }
                    quote?.quote?.takeIf { it.isNotBlank() }?.let { Text("“$it”", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 3, overflow = TextOverflow.Ellipsis) }
                }
            }
            if (neighbors.tickets.isNotEmpty()) SettingsGroup("Used by tickets · ${neighbors.tickets.size}") {
                neighbors.tickets.forEach { t -> TicketCard(org, plan, t, { nav.push("ticket:${t.id}") }) }
            }
            Note("Notes are edited and linked in Organizations on your computer.")
        }
    }
}

@Composable
private fun NoteGraph(center: OrgNote?, neighbors: OrganizationModel.Neighbors, kind: String, nav: OrgNav) {
    val nodes = neighbors.notes.take(10).map { "note:${it.id}" to it.kind } + neighbors.tickets.take(6).map { "ticket:${it.id}" to "ticket" }
    val colors = OrganizationModel.KINDS.associate { it.first to kindColor(it.first) } + ("ticket" to MaterialTheme.colorScheme.outline)
    val centerColor = kindColor(kind)
    val edge = MaterialTheme.colorScheme.outlineVariant
    val labels = nodes.map { (ref, _) ->
        neighbors.notes.firstOrNull { "note:${it.id}" == ref }?.title ?: neighbors.tickets.firstOrNull { "ticket:${it.id}" == ref }?.let { OrganizationModel.key(it) }.orEmpty()
    }
    Surface(shape = RoundedCornerShape(12.dp), color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f), modifier = Modifier.fillMaxWidth()
        .semantics { contentDescription = "Map of ${plural(neighbors.notes.size, "linked note")} and ${plural(neighbors.tickets.size, "ticket")}. The lists below open them." }) {
        Canvas(Modifier.fillMaxWidth().height(180.dp).pointerInput(nodes) {
            detectTapGestures { tap ->
                val c = Offset(size.width / 2f, size.height / 2f)
                val r = min(size.width, size.height) * 0.38f
                nodes.forEachIndexed { i, (ref, _) ->
                    val a = 2 * Math.PI * i / nodes.size - Math.PI / 2
                    val p = Offset(c.x + r * cos(a).toFloat() * 1.6f, c.y + r * sin(a).toFloat())
                    if ((tap - p).getDistance() < 28.dp.toPx()) { nav.push(ref); return@detectTapGestures }
                }
            }
        }) {
            val c = Offset(size.width / 2f, size.height / 2f)
            val r = min(size.width, size.height) * 0.38f
            val points = nodes.indices.map { i ->
                val a = 2 * Math.PI * i / nodes.size - Math.PI / 2
                Offset(c.x + r * cos(a).toFloat() * 1.6f, c.y + r * sin(a).toFloat())
            }
            points.forEach { drawLine(edge, c, it, strokeWidth = 2.dp.toPx()) }
            points.forEachIndexed { i, p ->
                val color = colors[nodes[i].second] ?: edge
                if (nodes[i].second == "ticket") drawRect(color, p - Offset(7.dp.toPx(), 7.dp.toPx()), androidx.compose.ui.geometry.Size(14.dp.toPx(), 14.dp.toPx()))
                else drawCircle(color, 9.dp.toPx(), p)
            }
            drawCircle(centerColor, 15.dp.toPx(), c)
            drawCircle(Color.White, 15.dp.toPx(), c, style = Stroke(3.dp.toPx()))
        }
    }
    // The map's text twin: what each dot is, in order around the circle.
    Text(labels.joinToString(" · ").ifBlank { center?.title.orEmpty() }, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
        maxLines = 2, overflow = TextOverflow.Ellipsis)
}

@RequiresApi(29)
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun NewNotePage(client: LiveClient, org: Organization, plan: OrgPlan, nav: OrgNav, busy: Boolean) {
    var kind by rememberSaveable { mutableStateOf("note") }
    var title by rememberSaveable { mutableStateOf("") }
    var topic by rememberSaveable { mutableStateOf("") }
    var summary by rememberSaveable { mutableStateOf("") }
    var body by rememberSaveable { mutableStateOf("") }
    var project by rememberSaveable { mutableStateOf("") }
    Column(Modifier.fillMaxSize()) {
        DetailHeader("New note", nav) {
            Button(enabled = title.isNotBlank() && !busy && client.organizationsV2 != false, modifier = Modifier.testTag("orgSaveNote"), onClick = {
                client.desktopAction(OrganizationModel.KNOWLEDGE, JSONObject().put("organization_id", org.id).put("action", "create").put("kind", kind)
                    .put("title", title.trim()).put("topic", topic.trim()).put("summary", summary.trim()).put("body", body.trim()).apply {
                        if (project.isNotBlank()) put("project_id", project)
                    })
                nav.pop()
            }) { Text("Save") }
        }
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                OrganizationModel.KINDS.forEach { (value, label) -> FilterChip(kind == value, { kind = value }, label = { Text(label) },
                    leadingIcon = { Box(Modifier.size(8.dp).clip(CircleShape).background(kindColor(value))) }) }
            }
            OutlinedTextField(title, { title = it.take(300) }, Modifier.fillMaxWidth().testTag("orgNoteTitle"), label = { Text("Title") }, singleLine = true)
            OutlinedTextField(topic, { topic = it.take(120) }, Modifier.fillMaxWidth(), label = { Text("Topic (optional)") }, singleLine = true,
                supportingText = { Text("Notes group by topic. Empty means General.") })
            if (plan.projects.isNotEmpty()) ChoiceMenu(plan.projects.firstOrNull { it.id == project }?.name ?: "Organization-wide", "Project",
                listOf("" to "Organization-wide") + plan.projects.map { it.id to it.name }, project) { project = it }
            OutlinedTextField(summary, { summary = it.take(1000) }, Modifier.fillMaxWidth(), label = { Text("Summary (optional)") }, maxLines = 3)
            OutlinedTextField(body, { body = it.take(20000) }, Modifier.fillMaxWidth().heightIn(min = 160.dp), label = { Text("Note") }, minLines = 6)
            Note("Workers can read this note. Don't put passwords or keys in it.")
        }
    }
}
