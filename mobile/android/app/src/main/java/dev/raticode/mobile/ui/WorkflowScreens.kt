package dev.raticode.mobile.ui

import androidx.activity.compose.BackHandler
import androidx.annotation.RequiresApi
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.raticode.mobile.live.*
import org.json.JSONObject
import java.time.Duration
import java.time.Instant
import java.time.OffsetDateTime

/** Workflows on the phone: view the graph, status and runs, and ask the desktop to run, stop or resume. Editing stays on the desktop. */

/** What the screen around a workflow knows: connection, an action in flight, the latest refusal and the desktop's last answer. */
internal data class WorkflowScreenState(val online: Boolean, val busy: Boolean, val error: String?, val notice: String?)

@Composable
private fun statusColor(status: String): Color {
    val dark = MaterialTheme.colorScheme.surface.luminance() < 0.5f
    return when (status) {
        "running", "queued", "stopping" -> MaterialTheme.colorScheme.primary
        "succeeded" -> if (dark) Color(0xFF7ED6A0) else Color(0xFF1E7A46)
        "failed", "interrupted" -> MaterialTheme.colorScheme.error
        "waiting" -> MaterialTheme.colorScheme.tertiary
        else -> MaterialTheme.colorScheme.onSurfaceVariant
    }
}

private fun statusIcon(status: String): ImageVector = when (status) {
    "succeeded" -> ThreadIcons.Succeeded; "failed", "interrupted" -> ThreadIcons.Failed; "running" -> ThreadIcons.Running
    "queued" -> ThreadIcons.Queued; "waiting" -> ThreadIcons.Waiting; "stopped", "stopping" -> ThreadIcons.Stopped
    "skipped" -> ThreadIcons.Skipped; else -> ThreadIcons.NotStarted
}

/** A status always reads as an icon and a word, never color alone. A running icon turns. */
@Composable
internal fun WorkflowStatus(status: String, modifier: Modifier = Modifier, text: String = Workflows.label(status), large: Boolean = false) {
    val color = statusColor(status)
    Row(modifier, verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        val angle = if (status == "running") rememberInfiniteTransition(label = "running").animateFloat(0f, 360f,
            infiniteRepeatable(tween(1200, easing = LinearEasing), RepeatMode.Restart), label = "spin").value else 0f
        Icon(statusIcon(status), null, Modifier.size(if (large) 20.dp else 16.dp).rotate(angle), tint = color)
        Text(text, style = if (large) MaterialTheme.typography.titleMedium else MaterialTheme.typography.labelMedium, color = color,
            maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

internal fun durationText(start: String, end: String, now: Instant = Instant.now()): String {
    val from = runCatching { OffsetDateTime.parse(start).toInstant() }.getOrNull() ?: return ""
    val to = if (end.isBlank()) now else runCatching { OffsetDateTime.parse(end).toInstant() }.getOrNull() ?: return ""
    val seconds = Duration.between(from, to).seconds.coerceAtLeast(0)
    return when {
        seconds < 60 -> "$seconds s"
        seconds < 3600 -> "${seconds / 60} min" + if (seconds % 60 != 0L && seconds < 600) " ${seconds % 60} s" else ""
        else -> "${seconds / 3600} h" + if (seconds % 3600 >= 60) " ${seconds % 3600 / 60} min" else ""
    }
}

internal fun triggerText(trigger: String) = when (trigger) {
    "" -> ""; "manual" -> "Started by hand"; "phone" -> "Started from the phone"; "schedule" -> "Scheduled"
    "webhook" -> "Webhook"; "rem" -> "Started by Rem"; else -> trigger.replace('_', ' ').replaceFirstChar { it.uppercase() }
}

/** The desktop's summary, unless it only repeats the status or duration already shown. */
internal fun runSummary(run: WorkflowRun): String = run.summary.trim().takeUnless {
    it.equals(Workflows.label(run.status), ignoreCase = true) || it.equals(run.status, ignoreCase = true) || Regex("(?i)took [0-9].*").matches(it)
}.orEmpty()

/** One line about a run: when it started and how long it took or has been going. */
internal fun runLine(run: WorkflowRun): String = listOf(
    run.startedAt.takeIf { it.isNotBlank() }?.let { "Started ${whenText(it, past = true)}" }.orEmpty(),
    durationText(run.startedAt, run.finishedAt).takeIf { it.isNotBlank() }?.let { if (run.active) "running $it" else "took $it" }.orEmpty(),
    triggerText(run.trigger)).filter { it.isNotBlank() }.joinToString(" · ")

@RequiresApi(29)
@Composable
internal fun WorkflowsRow(client: LiveClient, onClick: () -> Unit) {
    val list = client.desktopViews[Workflows.LIST] as? WorkflowList
    val running = list?.items?.count { it.active } ?: 0
    ConversationRow("Workflows", "", when {
        client.desktopControl && client.workflows == false -> "Update Raticode on your computer to use workflows here"
        running > 0 -> "$running running on your desktop"
        else -> "Graphs, run history, run and stop"
    }, onClick)
}

@RequiresApi(29)
@Composable
internal fun WorkflowsPage(client: LiveClient, onBack: (() -> Unit)?) {
    var open by rememberSaveable { mutableStateOf<String?>(null) }
    var runId by rememberSaveable { mutableStateOf<String?>(null) }
    val params = if (open == null) JSONObject() else JSONObject().put("workflow_id", open).apply { runId?.let { put("run_id", it) } }
    val op = if (open == null) Workflows.LIST else Workflows.READ
    DisposableEffect(op, params.toString()) { client.openDesktop(op, params); onDispose { client.openDesktop(null) } }
    BackHandler(enabled = open != null) { open = null; runId = null }
    Column(Modifier.fillMaxSize()) {
        if (open == null) {
            val list = client.desktopViews[Workflows.LIST] as? WorkflowList
            Header("Workflows", if (client.connected) "On your desktop" else "Offline", onBack)
            Box(Modifier.weight(1f)) {
                WorkflowGate(client, Workflows.LIST, "Loading workflows…") {
                    if (list != null) WorkflowList(list, WorkflowScreenState(client.connected, false, client.desktopErrors[Workflows.LIST], null),
                        onRetry = client::refreshDesktop) { open = it; runId = null }
                }
            }
        } else {
            val key = DesktopViews.key(Workflows.READ, params)
            val workflow = client.desktopViews[key] as? Workflow
            val summary = workflow?.summary ?: (client.desktopViews[Workflows.LIST] as? WorkflowList)?.items?.firstOrNull { it.id == open }
            // The run card below shows the status with its icon; the header names only the project.
            Header(summary?.name ?: "Workflow", summary?.project.orEmpty(), onBack = { open = null; runId = null })
            Box(Modifier.weight(1f)) {
                WorkflowGate(client, key, "Loading this workflow…") {
                    if (workflow != null) {
                        val notice = client.workflowNotice?.takeIf { it.first == workflow.id }?.second
                        WorkflowDetail(workflow, runId, WorkflowScreenState(client.connected, key in client.desktopBusy, client.desktopErrors[key], notice),
                            onSelectRun = { runId = it }, onRetry = client::refreshDesktop, onDismissNotice = client::dismissWorkflowNotice,
                            onAction = { action, run -> client.desktopAction(action, workflowAction(workflow, action, run)) })
                    }
                }
            }
        }
    }
}

/** A run names the saved version the phone showed, so a workflow changed on the desktop since is refused rather than run unseen. */
internal fun workflowAction(workflow: Workflow, action: String, run: String?): JSONObject = JSONObject().put("workflow_id", workflow.id).apply {
    if (action == "workflows.run") put("expected_revision", workflow.revision)
    run?.let { put("run_id", it) }
}

/** Adds the desktop's workflow support to the usual connection, opt-in, loading and error states. */
@RequiresApi(29)
@Composable
private fun WorkflowGate(client: LiveClient, key: String, loading: String, content: @Composable () -> Unit) {
    val loaded = client.desktopViews.containsKey(key)
    when {
        !loaded && client.connected && client.desktopControl && client.workflows == false ->
            Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Note(Workflows.UPDATE_DESKTOP); TextButton(onClick = client::checkDesktopFeatures) { Text("Check again") }
            }
        !loaded && client.connected && client.desktopControl && client.workflows == null ->
            Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                LinearProgressIndicator(Modifier.fillMaxWidth()); Note("Checking what your desktop supports…")
            }
        else -> DesktopGate(client, key, loading, content)
    }
}

/** Offline, refused and answered states shown above a loaded view, so stale data is never mistaken for live data. */
@Composable
private fun WorkflowBanners(state: WorkflowScreenState, onRetry: () -> Unit, onDismissNotice: () -> Unit = {}) {
    if (!state.online) Banner("Offline. Showing what your desktop last sent. Run, stop and resume return when you reconnect.", MaterialTheme.colorScheme.surfaceVariant)
    else if (state.error != null) Banner(state.error, MaterialTheme.colorScheme.errorContainer, "Try again", onRetry)
    if (state.busy && state.online) Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
        Text("Sending to your desktop…", style = MaterialTheme.typography.bodySmall)
    }
    else if (state.notice != null) Banner(state.notice, MaterialTheme.colorScheme.secondaryContainer, "OK", onDismissNotice)
}

@Composable
internal fun Banner(text: String, color: Color, action: String? = null, onAction: () -> Unit = {}) {
    Surface(color = color, shape = MaterialTheme.shapes.small, modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp)
        .semantics { liveRegion = LiveRegionMode.Polite }) {
        Row(Modifier.padding(start = 12.dp, end = 4.dp, top = 4.dp, bottom = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(text, Modifier.weight(1f).padding(vertical = 6.dp), style = MaterialTheme.typography.bodySmall)
            if (action != null) TextButton(onClick = onAction) { Text(action) }
        }
    }
}

@Composable
internal fun WorkflowList(list: WorkflowList, state: WorkflowScreenState, onRetry: () -> Unit, onOpen: (String) -> Unit) {
    var filter by rememberSaveable { mutableStateOf("all") }
    val running = list.items.count { it.active }
    val failed = list.items.count { !it.active && (it.latestRun?.status ?: it.status) in setOf("failed", "interrupted") }
    val shown = list.items.filter { when (filter) {
        "running" -> it.active
        "failed" -> !it.active && (it.latestRun?.status ?: it.status) in setOf("failed", "interrupted")
        else -> true
    } }.sortedByDescending { it.active }
    Column(Modifier.fillMaxSize()) {
        WorkflowBanners(state, onRetry)
        LazyColumn(Modifier.fillMaxSize().testTag("workflowList"), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            if (list.items.isNotEmpty()) item {
                Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilterChip(filter == "all", { filter = "all" }, label = { Text("All (${list.items.size})") })
                    FilterChip(filter == "running", { filter = "running" }, label = { Text("Running ($running)") })
                    FilterChip(filter == "failed", { filter = "failed" }, label = { Text("Failed ($failed)") })
                }
            }
            if (list.items.isEmpty()) item { Note("No workflows yet. Create one in Raticode on your computer, then it shows up here.") }
            else if (shown.isEmpty()) item { Note(if (filter == "running") "Nothing is running right now." else "No workflows failed on their latest run.") }
            items(shown, key = { it.id }) { workflow ->
                Surface(onClick = { onOpen(workflow.id) }, shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.surface,
                    modifier = Modifier.fillMaxWidth().testTag("workflow:${workflow.id}")) {
                    Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        if (workflow.project.isNotBlank()) Text(workflow.project, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.primary,
                            maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Text(workflow.name.ifBlank { "Untitled workflow" }, style = MaterialTheme.typography.titleMedium, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        if (workflow.description.isNotBlank()) Text(workflow.description, style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        val run = workflow.latestRun
                        WorkflowStatus(run?.status ?: workflow.status)
                        if (run != null) Text(runLine(run), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 2, overflow = TextOverflow.Ellipsis)
                    }
                }
            }
            if (list.earlier > 0) item { Note("${list.earlier} more ${if (list.earlier == 1) "workflow is" else "workflows are"} on your desktop.") }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun WorkflowDetail(workflow: Workflow, selectedRun: String?, state: WorkflowScreenState, onSelectRun: (String?) -> Unit,
                            onRetry: () -> Unit, onDismissNotice: () -> Unit, onAction: (String, String?) -> Unit) {
    var tab by rememberSaveable(workflow.id) { mutableStateOf("graph") }
    var node by remember { mutableStateOf<WorkflowNode?>(null) }
    var confirm by remember { mutableStateOf<Pair<String, String?>?>(null) }
    val layout = remember(workflow.nodes, workflow.edges) { GraphLayout.of(workflow.nodes, workflow.edges) }
    // The detail shown is for the chosen run only once the desktop sent it.
    val detail = workflow.run?.takeIf { selectedRun == null || it.run.id == selectedRun }
    val statuses = detail?.nodes?.associateBy { it.nodeId }.orEmpty()
    Column(Modifier.fillMaxSize()) {
        WorkflowBanners(state, onRetry, onDismissNotice)
        RunCard(workflow, detail, selectedRun, state, onLatest = { onSelectRun(null) }) { action, run -> confirm = action to run }
        PrimaryTabRow(selectedTabIndex = listOf("graph", "steps", "runs").indexOf(tab)) {
            Tab(tab == "graph", { tab = "graph" }, text = { Text("Graph") })
            Tab(tab == "steps", { tab = "steps" }, text = { Text("Steps") })
            Tab(tab == "runs", { tab = "runs" }, text = { Text(if (workflow.runs.isEmpty()) "Runs" else "Runs (${workflow.runs.size + workflow.earlierRuns})") })
        }
        Box(Modifier.weight(1f).fillMaxWidth()) {
            when (tab) {
                "graph" -> if (workflow.nodes.isEmpty()) Note("This workflow has no steps the desktop can show.", Modifier.padding(20.dp))
                    else WorkflowGraph(workflow, layout, statuses, Modifier.fillMaxSize()) { node = it }
                "steps" -> WorkflowSteps(workflow, layout, statuses) { node = it }
                else -> WorkflowRuns(workflow, detail?.run?.id ?: selectedRun) { onSelectRun(it); tab = "graph" }
            }
        }
    }
    node?.let { shown ->
        ModalBottomSheet(onDismissRequest = { node = null }) { NodeDetail(workflow, shown, statuses[shown.id]) }
    }
    confirm?.let { (action, run) ->
        val name = workflow.summary.name.ifBlank { "this workflow" }
        AlertDialog(onDismissRequest = { confirm = null }, title = { Text(when (action) {
            "workflows.run" -> "Run $name?"; "workflows.stop" -> "Stop this run?"; else -> "Resume this run?"
        }, maxLines = 2, overflow = TextOverflow.Ellipsis) }, text = { Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(when (action) {
                "workflows.run" -> "Raticode starts a new run on your computer with the workflow's saved inputs. Its approvals and limits still apply."
                "workflows.stop" -> "Raticode asks the run to stop on your computer. Steps that already finished stay finished."
                else -> "Raticode continues the run on your computer from where it stopped. Its approvals and limits still apply."
            })
            // The same preview the desktop shows before its Run button starts anything.
            if (action == "workflows.run") workflow.preview.let { preview ->
                if (preview.destructive.isNotEmpty()) PreviewList("This run can change things", preview.destructive, MaterialTheme.colorScheme.error)
                if (preview.warnings.isNotEmpty()) PreviewList("Warnings", preview.warnings, MaterialTheme.colorScheme.tertiary)
            }
        } }, confirmButton = { TextButton(onClick = { onAction(action, run); confirm = null }) { Text(when (action) {
            "workflows.run" -> "Run"; "workflows.stop" -> "Stop run"; else -> "Resume"
        }) } }, dismissButton = { TextButton(onClick = { confirm = null }) { Text("Cancel") } })
    }
}

@Composable
private fun PreviewList(title: String, items: List<String>, color: Color) {
    Text(title, style = MaterialTheme.typography.titleSmall, color = color)
    items.forEach { Text("• $it", style = MaterialTheme.typography.bodySmall) }
}

/** The run being shown, its status, and the controls the desktop allows for it, with the reason for any it does not. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun RunCard(workflow: Workflow, detail: WorkflowRunDetail?, selectedRun: String?, state: WorkflowScreenState, onLatest: () -> Unit,
                    onAction: (String, String?) -> Unit) {
    // The detail may carry fewer fields than the run history entry for the same run.
    val run = (detail?.run ?: workflow.runs.firstOrNull { it.id == selectedRun } ?: workflow.shownRun)?.let { shown ->
        workflow.runs.firstOrNull { it.id == shown.id }?.let { listed ->
            shown.copy(startedAt = shown.startedAt.ifBlank { listed.startedAt }, finishedAt = shown.finishedAt.ifBlank { listed.finishedAt },
                trigger = shown.trigger.ifBlank { listed.trigger }, summary = shown.summary.ifBlank { listed.summary })
        } ?: shown
    }
    val active = workflow.activeRun
    val ready = state.online && !state.busy
    Surface(color = MaterialTheme.colorScheme.surface, shape = MaterialTheme.shapes.medium, modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp)) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            if (selectedRun != null && run?.id != workflow.runs.firstOrNull()?.id) Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Earlier run", Modifier.weight(1f), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                TextButton(onClick = onLatest) { Text("Show latest") }
            }
            if (run == null) Text("No runs yet", style = MaterialTheme.typography.titleMedium)
            else {
                WorkflowStatus(run.status, large = true)
                runLine(run).takeIf { it.isNotBlank() }?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
                (detail?.error?.takeIf { it.isNotBlank() } ?: runSummary(run).takeIf { it.isNotBlank() })?.let {
                    Text(it, style = MaterialTheme.typography.bodySmall, color = if (detail?.error?.isNotBlank() == true) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface,
                        maxLines = 4, overflow = TextOverflow.Ellipsis)
                }
                if (selectedRun != null && detail == null) Text("Loading this run…", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                if (active != null) OutlinedButton(onClick = { onAction("workflows.stop", active.id) }, enabled = ready && workflow.stopControl.allowed,
                    colors = ButtonDefaults.outlinedButtonColors(contentColor = MaterialTheme.colorScheme.error), modifier = Modifier.testTag("workflowStop")) {
                    Icon(ThreadIcons.Stop, null, Modifier.size(16.dp)); Spacer(Modifier.width(6.dp)); Text("Stop")
                }
                else Button(onClick = { onAction("workflows.run", null) }, enabled = ready && workflow.runControl.allowed, modifier = Modifier.testTag("workflowRun")) {
                    Icon(ThreadIcons.Play, null, Modifier.size(16.dp)); Spacer(Modifier.width(6.dp)); Text("Run")
                }
                val resumable = run != null && !run.active && run.status in setOf("failed", "stopped", "interrupted")
                if (resumable && active == null) OutlinedButton(onClick = { onAction("workflows.resume", run!!.id) },
                    enabled = ready && workflow.resumeControl.allowed && detail != null, modifier = Modifier.testTag("workflowResume")) {
                    Icon(ThreadIcons.Resume, null, Modifier.size(16.dp)); Spacer(Modifier.width(6.dp)); Text("Resume")
                }
            }
            // Say why a control is off; a disabled button alone does not explain itself.
            val reasons = buildList {
                if (active != null && !workflow.stopControl.allowed) add("Stop: " + workflow.stopControl.reason.ifBlank { "Your desktop doesn't allow stopping this run." })
                if (active == null && !workflow.runControl.allowed) add("Run: " + workflow.runControl.reason.ifBlank { "Your desktop doesn't allow running this workflow from the phone." })
                if (active == null && run != null && run.status in setOf("failed", "stopped", "interrupted") && !workflow.resumeControl.allowed)
                    add("Resume: " + workflow.resumeControl.reason.ifBlank { "Your desktop can't resume this run." })
            }
            reasons.forEach { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.testTag("workflowReason")) }
        }
    }
}

private val NODE_WIDTH = 140.dp
private val GAP_X = 16.dp
private val GAP_Y = 40.dp
private val PAD = 16.dp
private val LANE = 28.dp

/**
 * The graph drawn top to bottom, sized for a phone: two steps side by side fit a 360 dp screen,
 * wider graphs scroll sideways, and steps that loop back are joined by dashed lines on the right.
 */
@Composable
private fun WorkflowGraph(workflow: Workflow, layout: GraphLayout, statuses: Map<String, WorkflowNodeRun>, modifier: Modifier, onNode: (WorkflowNode) -> Unit) {
    val density = LocalDensity.current
    // Taller steps at larger font sizes keep two lines of label readable.
    val nodeHeight = 80.dp * density.fontScale.coerceIn(1f, 1.6f)
    val rowSize = layout.slots.values.groupingBy { it.layer }.eachCount()
    val lane = if (layout.backEdges.isEmpty()) 0.dp else LANE
    val width = PAD * 2 + NODE_WIDTH * layout.columns + GAP_X * (layout.columns - 1).coerceAtLeast(0) + lane
    val height = PAD * 2 + nodeHeight * layout.layers + GAP_Y * (layout.layers - 1).coerceAtLeast(0)
    fun x(slot: GraphSlot): Dp = PAD + (NODE_WIDTH + GAP_X) * ((layout.columns - (rowSize[slot.layer] ?: 1)) / 2f) + (NODE_WIDTH + GAP_X) * slot.column
    fun y(slot: GraphSlot): Dp = PAD + (nodeHeight + GAP_Y) * slot.layer
    val byId = workflow.nodes.associateBy { it.id }
    val taken = MaterialTheme.colorScheme.primary
    val idle = MaterialTheme.colorScheme.outline
    val labelColor = MaterialTheme.colorScheme.onSurfaceVariant
    val labelBack = MaterialTheme.colorScheme.background
    val measurer = rememberTextMeasurer()
    val horizontal = rememberScrollState()
    val vertical = rememberScrollState()
    // Bring the first running step into view.
    val focus = layout.order.firstOrNull { statuses[it]?.status?.let(Workflows::active) == true }
    LaunchedEffect(workflow.id, focus) {
        val slot = focus?.let { layout.slots[it] } ?: return@LaunchedEffect
        with(density) {
            horizontal.animateScrollTo((x(slot) - 24.dp).roundToPx().coerceAtLeast(0))
            vertical.animateScrollTo((y(slot) - 24.dp).roundToPx().coerceAtLeast(0))
        }
    }
    Box(modifier.verticalScroll(vertical).horizontalScroll(horizontal).testTag("workflowGraph")) {
        Box(Modifier.size(width, height).drawBehind {
            fun started(id: String) = statuses[id]?.status?.let { it.isNotBlank() && it != "pending" && it != "skipped" } == true
            layout.edges.forEach { edge ->
                val a = layout.slots.getValue(edge.from); val b = layout.slots.getValue(edge.to)
                val color = if (started(edge.from) && started(edge.to)) taken else idle
                val back = edge in layout.backEdges
                val path = Path()
                val label: Offset
                val end: Offset
                if (back) {
                    // Leave below the step, run up the right-hand lane and enter the earlier step from above,
                    // so the line only crosses the gaps between rows, never another step.
                    val startX = (x(a) + NODE_WIDTH - 16.dp).toPx()
                    val below = (y(a) + nodeHeight + GAP_Y * 0.3f).toPx()
                    val laneX = (width - LANE / 2).toPx()
                    val above = (y(b) - GAP_Y * 0.3f).toPx()
                    end = Offset((x(b) + NODE_WIDTH - 16.dp).toPx(), y(b).toPx())
                    path.moveTo(startX, (y(a) + nodeHeight).toPx()); path.lineTo(startX, below); path.lineTo(laneX, below)
                    path.lineTo(laneX, above); path.lineTo(end.x, above); path.lineTo(end.x, end.y)
                    label = Offset((laneX + end.x) / 2, above)
                } else {
                    val start = Offset((x(a) + NODE_WIDTH / 2).toPx(), (y(a) + nodeHeight).toPx())
                    end = Offset((x(b) + NODE_WIDTH / 2).toPx(), y(b).toPx())
                    val bend = (end.y - start.y) / 2
                    path.moveTo(start.x, start.y); path.cubicTo(start.x, start.y + bend, end.x, end.y - bend, end.x, end.y)
                    label = Offset((start.x + end.x) / 2, (start.y + end.y) / 2)
                }
                drawPath(path, color, style = Stroke(2.dp.toPx(), pathEffect = if (back) PathEffect.dashPathEffect(floatArrayOf(10f, 8f)) else null))
                arrow(end, Offset(0f, 1f), color)
                if (edge.label.isNotBlank()) {
                    val text = measurer.measure(edge.label.take(20), TextStyle(fontSize = 10.sp, color = labelColor))
                    val topLeft = Offset(label.x - text.size.width / 2f, label.y - text.size.height / 2f)
                    drawRect(labelBack, topLeft, androidx.compose.ui.geometry.Size(text.size.width.toFloat(), text.size.height.toFloat()))
                    drawText(text, topLeft = topLeft)
                }
            }
        }) {
            layout.order.forEach { id ->
                val node = byId.getValue(id)
                val slot = layout.slots.getValue(id)
                GraphNode(node, statuses[id], Modifier.offset(x(slot), y(slot)).size(NODE_WIDTH, nodeHeight)) { onNode(node) }
            }
        }
    }
}

private fun DrawScope.arrow(tip: Offset, direction: Offset, color: Color) {
    val size = 7.dp.toPx()
    val side = Offset(-direction.y, direction.x)
    val base = tip - direction * size
    drawPath(Path().apply {
        moveTo(tip.x, tip.y); lineTo(base.x + side.x * size / 2, base.y + side.y * size / 2)
        lineTo(base.x - side.x * size / 2, base.y - side.y * size / 2); close()
    }, color)
}

@Composable
private fun GraphNode(node: WorkflowNode, run: WorkflowNodeRun?, modifier: Modifier, onClick: () -> Unit) {
    val status = run?.status.orEmpty()
    val color = if (status.isBlank()) MaterialTheme.colorScheme.outlineVariant else statusColor(status)
    val label = node.label.ifBlank { node.id }
    Surface(onClick = onClick, shape = MaterialTheme.shapes.small, color = MaterialTheme.colorScheme.surface,
        border = BorderStroke(if (Workflows.active(status)) 2.dp else 1.dp, color),
        modifier = modifier.testTag("workflowNode:${node.id}").semantics {
            role = Role.Button
            contentDescription = listOf(label, node.kind, if (status.isBlank()) "" else Workflows.label(status)).filter { it.isNotBlank() }.joinToString(", ")
        }) {
        Column(Modifier.padding(horizontal = 10.dp, vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(label, style = MaterialTheme.typography.labelLarge, maxLines = 2, overflow = TextOverflow.Ellipsis)
            if (status.isNotBlank()) WorkflowStatus(status)
            else if (node.kind.isNotBlank()) Text(node.kind.replace('_', ' ').replaceFirstChar { it.uppercase() }, style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    }
}

/** The same steps as a list in drawing order, easier to read on a small screen and with a screen reader. */
@Composable
private fun WorkflowSteps(workflow: Workflow, layout: GraphLayout, statuses: Map<String, WorkflowNodeRun>, onNode: (WorkflowNode) -> Unit) {
    val byId = workflow.nodes.associateBy { it.id }
    LazyColumn(Modifier.fillMaxSize().testTag("workflowSteps"), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        if (workflow.nodes.isEmpty()) item { Note("This workflow has no steps the desktop can show.") }
        items(layout.order, key = { it }) { id ->
            val node = byId.getValue(id)
            val run = statuses[id]
            Surface(onClick = { onNode(node) }, shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.surface, modifier = Modifier.fillMaxWidth()) {
                Row(Modifier.padding(12.dp), horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.Top) {
                    Text("${(layout.slots[id]?.layer ?: 0) + 1}", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.width(20.dp).semantics { contentDescription = "Stage ${(layout.slots[id]?.layer ?: 0) + 1}" })
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                        Text(node.label.ifBlank { node.id }, style = MaterialTheme.typography.titleSmall)
                        if (node.kind.isNotBlank()) Text(node.kind.replace('_', ' ').replaceFirstChar { it.uppercase() }, style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                        if (run != null && run.status.isNotBlank()) {
                            WorkflowStatus(run.status, text = Workflows.label(run.status) + durationText(run.startedAt, run.finishedAt).let { if (it.isBlank() || run.status == "pending") "" else " · $it" })
                            if (run.detail.isNotBlank()) Text(run.detail, style = MaterialTheme.typography.bodySmall, maxLines = 3, overflow = TextOverflow.Ellipsis)
                        }
                    }
                }
            }
        }
        item { Note("Workflows are edited in Raticode on your computer. The phone shows them and can run, stop or resume them.") }
    }
}

@Composable
private fun WorkflowRuns(workflow: Workflow, shown: String?, onSelect: (String?) -> Unit) {
    val latest = workflow.runs.firstOrNull()?.id
    LazyColumn(Modifier.fillMaxSize().testTag("workflowRuns"), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        if (workflow.runs.isEmpty()) item { Note("No runs yet. Tap Run to start one on your desktop.") }
        items(workflow.runs, key = { it.id }) { run ->
            val selected = run.id == (shown ?: latest)
            Surface(onClick = { onSelect(if (run.id == latest) null else run.id) }, shape = MaterialTheme.shapes.medium,
                color = if (selected) MaterialTheme.colorScheme.secondaryContainer else MaterialTheme.colorScheme.surface,
                modifier = Modifier.fillMaxWidth().semantics { this.selected = selected }) {
                Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        WorkflowStatus(run.status, Modifier.weight(1f))
                        if (run.id == latest) Text("Latest", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    runLine(run).takeIf { it.isNotBlank() }?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
                    runSummary(run).takeIf { it.isNotBlank() }?.let { Text(it, style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis) }
                }
            }
        }
        if (workflow.earlierRuns > 0) item { Note("${workflow.earlierRuns} earlier ${if (workflow.earlierRuns == 1) "run is" else "runs are"} on your desktop.") }
    }
}

@Composable
private fun NodeDetail(workflow: Workflow, node: WorkflowNode, run: WorkflowNodeRun?) {
    val names = workflow.nodes.associate { it.id to it.label.ifBlank { it.id } }
    Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(start = 20.dp, end = 20.dp, bottom = 32.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(node.label.ifBlank { node.id }, style = MaterialTheme.typography.titleLarge)
        if (node.kind.isNotBlank()) Note(node.kind.replace('_', ' ').replaceFirstChar { it.uppercase() })
        if (run == null || run.status.isBlank()) Note("No status for this step in the run shown.")
        else {
            WorkflowStatus(run.status, large = true)
            listOf(run.startedAt.takeIf { it.isNotBlank() }?.let { "Started ${whenText(it, past = true)}" }.orEmpty(),
                durationText(run.startedAt, run.finishedAt).takeIf { it.isNotBlank() && run.status != "pending" }?.let { if (Workflows.active(run.status)) "running $it" else "took $it" }.orEmpty())
                .filter { it.isNotBlank() }.joinToString(" · ").takeIf { it.isNotBlank() }?.let { Note(it) }
            if (run.detail.isNotBlank()) Text(run.detail, style = MaterialTheme.typography.bodyMedium)
        }
        val from = workflow.edges.filter { it.to == node.id && it.from in names }
        val to = workflow.edges.filter { it.from == node.id && it.to in names }
        if (from.isNotEmpty()) SettingsGroup("Comes after") { from.forEach { Text(names.getValue(it.from) + it.label.let { l -> if (l.isBlank()) "" else " ($l)" }) } }
        if (to.isNotEmpty()) SettingsGroup("Leads to") { to.forEach { Text(names.getValue(it.to) + it.label.let { l -> if (l.isBlank()) "" else " ($l)" }) } }
        Note("To change this step, edit the workflow in Raticode on your computer.")
    }
}
