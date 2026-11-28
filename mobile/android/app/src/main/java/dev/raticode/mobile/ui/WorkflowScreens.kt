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
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.text.KeyboardOptions
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
import androidx.compose.ui.platform.LocalContext
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
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.raticode.mobile.live.*
import dev.raticode.mobile.share.WorkflowShare
import dev.raticode.mobile.share.WorkflowShareInbox
import dev.raticode.mobile.share.WorkflowShareStore
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONObject
import java.time.Duration
import java.time.Instant
import java.time.OffsetDateTime

/**
 * Workflows on the phone: view the graph, status and runs, and ask the desktop to run, stop, resume or run again. Each
 * workflow's triggers can be turned on or off, and all of them paused at once. Editing stays on the desktop.
 */

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

/** The first line of why a run stopped, or blank when the desktop didn't say. */
internal fun runErrorLine(run: WorkflowRun): String = run.error.lineSequence().map { it.trim() }.firstOrNull { it.isNotBlank() }.orEmpty()

/** Why a failed run stopped, under its status in a runs list; nothing when the desktop didn't say. */
@Composable
private fun RunErrorReason(run: WorkflowRun) {
    val line = runErrorLine(run)
    if (line.isNotBlank()) Text(line, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error,
        maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.testTag("runError:${run.id}"))
}

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
internal fun WorkflowsPage(client: LiveClient, onBack: (() -> Unit)?, openRun: Pair<String, String>? = null, consumeOpenRun: () -> Unit = {},
                           onAskRem: ((java.io.File, String) -> Unit)? = null) {
    var open by rememberSaveable { mutableStateOf<String?>(null) }
    var runId by rememberSaveable { mutableStateOf<String?>(null) }
    // The trigger whose changes are under review, and the file whose diff is open.
    var reviewing by rememberSaveable { mutableStateOf<String?>(null) }
    var reviewFile by rememberSaveable { mutableStateOf<String?>(null) }
    // A changed file opened in the file viewer from Review changes, by project ID and path, to read what its diff couldn't show.
    var reviewOpen by rememberSaveable { mutableStateOf<String?>(null) }
    var reviewProject by rememberSaveable { mutableStateOf("") }
    // Try another model: the workflow whose tests are open, and the candidate whose cost is shown with the revision it is for.
    var testing by rememberSaveable { mutableStateOf<String?>(null) }
    var estimating by remember { mutableStateOf<Pair<List<TriedModel>, String>?>(null) }
    // A tapped workflow message opens its run; one that named no workflow opens the list.
    LaunchedEffect(openRun) {
        openRun?.let { (workflow, run) -> open = workflow.ifBlank { null }; runId = run.ifBlank { null }.takeIf { workflow.isNotBlank() }; consumeOpenRun() }
    }
    // The All runs tab and its filters, kept while a run opened from it is shown.
    var allRuns by rememberSaveable { mutableStateOf(false) }
    var since by rememberSaveable { mutableStateOf("24h") }
    var status by rememberSaveable { mutableStateOf("") }
    var only by rememberSaveable { mutableStateOf("") }
    val showRuns = allRuns && client.workflowRuns == true
    // Text shared to Run a workflow waits here until a workflow and its input are chosen and Run is tapped.
    val context = LocalContext.current
    val share = WorkflowShareInbox.pending
    val scope = rememberCoroutineScope()
    // A shared file is read and uploaded only once Run is tapped; this says how that is going or why it stopped.
    var upload by remember(share?.id) { mutableStateOf<String?>(null) }
    LaunchedEffect(share?.id) {
        val waiting = share?.takeIf { it.target == null } ?: return@LaunchedEffect
        reviewing = null; reviewFile = null; reviewOpen = null; allRuns = false; runId = null
        // A direct-share shortcut names its workflow; the share sheet's own entry, or a share with no text, starts at the list.
        val named = waiting.workflowId?.takeIf { waiting.text.isNotEmpty() }
        open = named
        named?.let { WorkflowShareInbox.target(it) }
    }
    // Ask Rem about this failure: once the desktop answers, its text goes to Rem's message box as a file. Nothing sends.
    val failureRead = client.runFailure
    var askRemProblem by remember { mutableStateOf("") }
    LaunchedEffect(failureRead?.failure) {
        val failure = failureRead?.failure ?: return@LaunchedEffect
        val file = runCatching { dev.raticode.mobile.data.HandoffFiles.write(context.cacheDir, failure.text) }.getOrNull()
        client.clearRunFailure()
        askRemProblem = if (file == null) "Couldn't prepare the run for Rem. Free some phone storage and try again." else ""
        if (file != null) onAskRem?.invoke(file, failure.name)
    }
    // Leaving the chosen workflow goes back to choosing one.
    LaunchedEffect(open) { if (share?.target != null && share.target != open) WorkflowShareInbox.target(null) }
    BackHandler(enabled = open == null && share != null) { WorkflowShareInbox.clear() }
    // input_form tells the desktop this phone shows the Run form, so a workflow that needs inputs can run from here.
    val inTests = open != null && testing == open && reviewing == null
    val params = when {
        inTests && estimating != null -> Workflows.estimateParams(open.orEmpty(), estimating!!.second, estimating!!.first)
        inTests -> Workflows.testsParams(open.orEmpty())
        open != null && reviewing != null -> Workflows.changesParams(open.orEmpty(), reviewing.orEmpty())
        open != null -> JSONObject().put("workflow_id", open).put("input_form", true).apply { runId?.let { put("run_id", it) } }
        showRuns -> Workflows.runsParams(since, status, only)
        else -> JSONObject()
    }
    val op = when {
        inTests && estimating != null -> Workflows.ESTIMATE
        inTests -> Workflows.TESTS
        open != null && reviewing != null -> Workflows.CHANGES
        open != null -> Workflows.READ
        showRuns -> Workflows.RUNS
        else -> Workflows.LIST
    }
    // Keyed on reviewOpen too: the file viewer reads its own text while open, and the changes come back when it closes.
    DisposableEffect(op, params.toString(), reviewOpen) { client.openDesktop(op, params); onDispose { client.openDesktop(null) } }
    BackHandler(enabled = open != null) {
        when {
            reviewOpen != null -> reviewOpen = null
            reviewFile != null -> reviewFile = null
            reviewing != null -> reviewing = null
            inTests && estimating != null -> estimating = null
            inTests -> testing = null
            else -> { open = null; runId = null }
        }
    }
    Column(Modifier.fillMaxSize()) {
        if (open == null && share != null) {
            Header("Run a workflow", if (client.connected) "With what you shared" else "Offline", onBack = WorkflowShareInbox::clear, backLabel = "Cancel")
            Box(Modifier.weight(1f)) {
                WorkflowGate(client, Workflows.LIST, "Loading workflows…") {
                    (client.desktopViews[Workflows.LIST] as? WorkflowList)?.let { list ->
                        ShareWorkflowPicker(share, list, WorkflowScreenState(client.connected, Workflows.LIST in client.desktopBusy,
                            client.desktopErrors[Workflows.LIST], null), onRetry = client::refreshDesktop, onCancel = WorkflowShareInbox::clear) {
                            WorkflowShareInbox.target(it); open = it; runId = null
                        }
                    }
                }
            }
        } else if (open == null) {
            Header("Workflows", if (client.connected) "On your desktop" else "Offline", onBack)
            if (client.workflowRuns == true) Row(Modifier.padding(horizontal = 16.dp, vertical = 4.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilterChip(!allRuns, { allRuns = false }, label = { Text("Workflows") }, modifier = Modifier.testTag("workflowsTab"))
                FilterChip(allRuns, { allRuns = true }, label = { Text("All runs") }, modifier = Modifier.testTag("allRunsTab"))
            }
            Box(Modifier.weight(1f)) {
                if (showRuns) {
                    val key = DesktopViews.key(Workflows.RUNS, params)
                    WorkflowGate(client, key, "Loading runs…") {
                        (client.desktopViews[key] as? AllWorkflowRuns)?.let { runs ->
                            AllRunsList(runs, WorkflowScreenState(client.connected, false, client.desktopErrors[key], null), since, status, only,
                                onFilter = { s, st, w -> since = s; status = st; only = w }, onRetry = client::refreshDesktop) { run ->
                                open = run.workflowId; runId = run.run.id
                            }
                        }
                    }
                } else {
                    val list = client.desktopViews[Workflows.LIST] as? WorkflowList
                    WorkflowGate(client, Workflows.LIST, "Loading workflows…") {
                        val notice = client.workflowNotice?.takeIf { it.first.isEmpty() }?.second
                        if (list != null) WorkflowList(list, WorkflowScreenState(client.connected, Workflows.LIST in client.desktopBusy,
                            client.desktopErrors[Workflows.LIST], notice), onRetry = client::refreshDesktop,
                            onDismissNotice = client::dismissWorkflowNotice,
                            onPauseTriggers = if (client.workflowTriggers == true) { paused -> client.desktopAction(Workflows.PAUSE, Workflows.pauseAction(paused)) } else null) {
                            open = it; runId = null
                        }
                    }
                }
            }
        } else if (inTests) {
            val workflowId = open.orEmpty()
            val testsKey = Workflows.testsKey(workflowId)
            val tests = client.desktopViews[testsKey] as? WorkflowTests
            val estimateKey = DesktopViews.key(op, params).takeIf { op == Workflows.ESTIMATE }
            val name = (client.desktopViews[Workflows.READ + ":" + workflowId] as? Workflow)?.summary?.name
            Header("Tests", name.orEmpty(), onBack = { if (estimating != null) estimating = null else testing = null })
            Box(Modifier.weight(1f)) {
                WorkflowGate(client, testsKey, "Loading the tests…") {
                    if (tests != null) {
                        val key = estimateKey ?: testsKey
                        WorkflowTestsView(tests, WorkflowScreenState(client.connected, testsKey in client.desktopBusy, client.desktopErrors[key], null),
                            estimate = estimateKey?.let { client.desktopViews[it] as? TestEstimate }, checking = estimating != null,
                            onRetry = client::refreshDesktop, onEstimate = { estimating = it to tests.revision }, onChange = { estimating = null },
                            onTry = { models ->
                                client.desktopAction(Workflows.TRY, Workflows.tryAction(workflowId, estimating?.second ?: tests.revision, models))
                                estimating = null
                            },
                            onUse = { result -> client.desktopAction(Workflows.USE_MODEL, Workflows.useModelAction(workflowId, result)) })
                    }
                }
            }
        } else if (reviewing != null && reviewOpen != null) {
            FileViewerPage(client, reviewProject, "", reviewOpen.orEmpty(), null, "", "file", false, null) { reviewOpen = null }
        } else if (reviewing != null) {
            val workflowId = open.orEmpty()
            val shown = client.desktopViews[Workflows.READ + ":" + workflowId] as? Workflow
            val trigger = shown?.triggers?.firstOrNull { it.id == reviewing }
            val key = DesktopViews.key(Workflows.CHANGES, params)
            val changes = client.desktopViews[key] as? TriggerChanges
            val file = changes?.files?.firstOrNull { it.path == reviewFile }
            Header(if (file != null) file.path else "Review changes", if (file != null) file.kind else trigger?.title.orEmpty(),
                onBack = { if (reviewFile != null) reviewFile = null else reviewing = null })
            Box(Modifier.weight(1f)) {
                WorkflowGate(client, key, "Loading the changes…") {
                    if (file != null) TriggerFileDiff(file)
                    else if (changes != null) TriggerReview(changes, WorkflowScreenState(client.connected, key in client.desktopBusy,
                        client.desktopErrors[key], null), onRetry = client::refreshDesktop, onFile = { reviewFile = it.path },
                        onOpen = if (changes.projectId.isNotBlank() && client.fileExplorer == true) { file ->
                            reviewProject = changes.projectId; reviewOpen = file.openPath
                        } else null,
                        onCancel = { reviewing = null }) { shown, unseenConfirmed ->
                        client.desktopAction(Workflows.APPROVE, Workflows.approveAction(workflowId, shown, unseenConfirmed)); reviewing = null
                    }
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
                        val shared = share?.takeIf { it.target == workflow.id }?.let { waiting ->
                            val choices = waiting.file?.let { file -> WorkflowShare.fileInputs(workflow.inputs.orEmpty().filter { it.kind == "file" }
                                .map { WorkflowFileInput(it.name, it.label, it.accept, it.maxSize) }, file) } ?: WorkflowShare.textInputs(workflow.inputs)
                            val remembered = remember(workflow.id, waiting.id) { WorkflowShareStore.input(context, workflow.id) }
                            var picked by remember(workflow.id, waiting.id) { mutableStateOf<String?>(null) }
                            SharedRun(waiting.text, waiting.notices, choices,
                                picked?.takeIf { name -> choices.any { it.name == name } } ?: WorkflowShare.inputFor(choices, remembered),
                                onChoose = { picked = it; WorkflowShareStore.rememberInput(context, workflow.id, it) }, onDiscard = WorkflowShareInbox::clear,
                                file = waiting.file, status = upload)
                        }
                        WorkflowDetail(workflow, runId, WorkflowScreenState(client.connected, key in client.desktopBusy, client.desktopErrors[key], notice),
                            onSelectRun = { runId = it }, onRetry = client::refreshDesktop, onDismissNotice = client::dismissWorkflowNotice,
                            shared = shared,
                            askRem = if (onAskRem != null && client.workflowFailures == true) { run -> askRemProblem = ""; client.askRemAboutRun(workflow.id, run) } else null,
                            onTests = if (client.workflowTests == true) { { estimating = null; testing = workflow.id } } else null,
                            askingRem = failureRead?.let { it.failure == null && it.error.isEmpty() } == true,
                            askRemError = failureRead?.error?.takeIf { it.isNotBlank() } ?: askRemProblem,
                            onRunWithInputs = { inputs ->
                                val file = shared?.file
                                val input = shared?.input
                                if (file != null && input != null) {
                                    // Upload first, the way Share to Rem does, then ask for the run with the uploaded file's ID.
                                    upload = "Uploading ${file.name} to your computer…"
                                    scope.launch {
                                        val uploaded = uploadSharedFile(client, file)
                                        uploaded.onFailure { upload = it.message ?: "The upload didn't finish. Try again." }
                                        uploaded.onSuccess { id ->
                                            upload = null
                                            client.desktopAction("workflows.run", workflowAction(workflow, "workflows.run", null, inputs)
                                                .put("file_ids", JSONObject().put(input, id)))
                                            WorkflowShareStore.rememberInput(context, workflow.id, input)
                                            WorkflowShareStore.used(context, workflow.id, workflow.summary.name)
                                            WorkflowShareInbox.clear()
                                        }
                                    }
                                } else {
                                    client.desktopAction("workflows.run", workflowAction(workflow, "workflows.run", null, inputs))
                                    // The run request is queued; the shared text is now only in it. The workflow becomes a direct-share shortcut.
                                    if (shared?.input != null) {
                                        WorkflowShareStore.rememberInput(context, workflow.id, shared.input)
                                        WorkflowShareStore.used(context, workflow.id, workflow.summary.name)
                                        WorkflowShareInbox.clear()
                                    }
                                }
                            },
                            onAction = { action, run -> client.desktopAction(action, workflowAction(workflow, action, run)) },
                            onTrigger = { trigger, enabled ->
                                client.desktopAction(Workflows.TRIGGER, Workflows.triggerAction(workflow, trigger, enabled))
                                // Turning a trigger on approves nothing, so a change waiting for review opens right away.
                                if (enabled && trigger.needsApproval && client.workflowTriggerApproval == true) { reviewing = trigger.id; reviewFile = null }
                            },
                            onReview = if (client.workflowTriggerApproval == true) { trigger -> reviewing = trigger.id; reviewFile = null } else null)
                    }
                }
            }
        }
    }
}

/**
 * Uploads a shared file on the phone's Rem file channel, as Share to Rem does, and returns its file ID once the
 * computer has all of it. The computer checks it against the file input and copies it into the run.
 */
@RequiresApi(29)
private suspend fun uploadSharedFile(client: LiveClient, file: WorkflowShare.SharedFile): Result<String> {
    if ((client.desktopViews["rem.home"] as? RemHome)?.filesThread == null) {
        // The file channel is named in Rem's chat view; read it once if this session hasn't yet.
        client.shareStart()
        try {
            withTimeoutOrNull(UPLOAD_CONNECT_MS) { snapshotFlow { (client.desktopViews["rem.home"] as? RemHome)?.filesThread }.first { it != null } }
                ?: return Result.failure(IllegalStateException("Couldn't reach your computer to upload the file. Try again."))
        } finally { client.shareStop() }
    }
    val imported = CompletableDeferred<Result<String>>()
    client.importRemFile(android.net.Uri.parse(file.uri), file.name, onFailed = { imported.complete(Result.failure(IllegalStateException(it))) }) {
        imported.complete(Result.success(it))
    }
    val id = (withTimeoutOrNull(UPLOAD_READ_MS) { imported.await() } ?: Result.failure(IllegalStateException("Reading ${file.name} took too long.")))
        .getOrElse { return Result.failure(IllegalStateException("Couldn't read ${file.name}: ${it.message}")) }
    val state = withTimeoutOrNull(UPLOAD_SEND_MS) {
        snapshotFlow { client.files.firstOrNull { it.id == id }?.state }.first { it == null || it == "available" || it == "failed" || it == "cancelled" }
    }
    return if (state == "available") Result.success(id) else Result.failure(IllegalStateException("The upload didn't finish. Try again."))
}

private const val UPLOAD_CONNECT_MS = 20_000L
private const val UPLOAD_READ_MS = 120_000L
private const val UPLOAD_SEND_MS = 600_000L

/**
 * A run names the saved version the phone showed, so a workflow changed on the desktop since is refused rather than run unseen.
 * Inputs from the Run form go only to a desktop that offered the form, and only for a workflow that has inputs.
 */
internal fun workflowAction(workflow: Workflow, action: String, run: String?, inputs: JSONObject? = null): JSONObject = JSONObject().put("workflow_id", workflow.id).apply {
    if (action == "workflows.run") put("expected_revision", workflow.revision)
    if (action == "workflows.run" && inputs != null && !workflow.inputs.isNullOrEmpty()) put("inputs", inputs)
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
internal fun WorkflowBanners(state: WorkflowScreenState, onRetry: () -> Unit, onDismissNotice: () -> Unit = {}) {
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
internal fun WorkflowList(list: WorkflowList, state: WorkflowScreenState, onRetry: () -> Unit, onDismissNotice: () -> Unit = {},
                         onPauseTriggers: ((Boolean) -> Unit)? = null, onOpen: (String) -> Unit) {
    var filter by rememberSaveable { mutableStateOf("all") }
    var confirmResume by remember { mutableStateOf(false) }
    val running = list.items.count { it.active }
    val failed = list.items.count { !it.active && (it.latestRun?.status ?: it.status) in setOf("failed", "interrupted") }
    val shown = list.items.filter { when (filter) {
        "running" -> it.active
        "failed" -> !it.active && (it.latestRun?.status ?: it.status) in setOf("failed", "interrupted")
        else -> true
    } }.sortedByDescending { it.active }
    Column(Modifier.fillMaxSize()) {
        WorkflowBanners(state, onRetry, onDismissNotice)
        LazyColumn(Modifier.fillMaxSize().testTag("workflowList"), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            val pause = list.triggers
            if (pause != null && onPauseTriggers != null) item {
                PauseAllTriggers(pause, enabled = state.online && !state.busy) { paused -> if (paused) onPauseTriggers(true) else confirmResume = true }
            }
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
    val pause = list.triggers
    if (confirmResume && pause != null && onPauseTriggers != null) AlertDialog(onDismissRequest = { confirmResume = false },
        title = { Text("Resume all triggers?") },
        text = { Text("${pause.enabled} of ${pause.count} ${if (pause.count == 1) "trigger turns" else "triggers turn"} back on, as each was set, " +
            "and start workflows on your computer without asking. Scheduled times missed while paused stay skipped.") },
        confirmButton = { TextButton(onClick = { onPauseTriggers(false); confirmResume = false }, modifier = Modifier.testTag("resumeTriggersConfirm")) { Text("Resume") } },
        dismissButton = { TextButton(onClick = { confirmResume = false }) { Text("Cancel") } })
}

/** More › Workflows: one switch over every trigger on the desktop. Pausing keeps each trigger's own setting. */
@Composable
private fun PauseAllTriggers(pause: TriggerPause, enabled: Boolean, onChange: (Boolean) -> Unit) {
    Surface(shape = MaterialTheme.shapes.medium, color = if (pause.paused) MaterialTheme.colorScheme.tertiaryContainer else MaterialTheme.colorScheme.surface,
        modifier = Modifier.fillMaxWidth().testTag("pauseAllTriggers")
            .toggleable(pause.paused, enabled = enabled, role = Role.Switch) { onChange(it) }) {
        Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text("Pause all triggers", style = MaterialTheme.typography.titleSmall)
                Text(pauseText(pause), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                if (pause.dailyLimit.isNotBlank()) Text(pause.dailyLimit, style = MaterialTheme.typography.bodySmall,
                    color = if (pause.limitReached) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.testTag("triggersDailyLimit"))
            }
            Switch(checked = pause.paused, onCheckedChange = null, enabled = enabled)
        }
    }
}

internal fun pauseText(pause: TriggerPause): String = when {
    pause.paused -> "Paused" + (pause.pausedAt.takeIf { it.isNotBlank() }?.let { " since ${whenText(it, past = true)}" } ?: "") +
        ". Nothing starts on its own; scheduled times are skipped, not caught up."
    pause.count == 0 -> "No triggers yet. Add them to a workflow on your computer."
    else -> "${pause.enabled} of ${pause.count} ${if (pause.count == 1) "trigger" else "triggers"} on."
}

/**
 * Every open project's workflow runs, newest first, with time, status and workflow filters. Tapping a run opens it in
 * its workflow, as the desktop's Workflow runs view opens it in the Studio.
 */
@Composable
internal fun AllRunsList(runs: AllWorkflowRuns, state: WorkflowScreenState, since: String, status: String, workflow: String,
                         onFilter: (since: String, status: String, workflow: String) -> Unit, onRetry: () -> Unit, onOpen: (AllWorkflowRun) -> Unit) {
    Column(Modifier.fillMaxSize()) {
        WorkflowBanners(state, onRetry)
        LazyColumn(Modifier.fillMaxSize().testTag("allRunsList"), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            item {
                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Workflows.SPANS.forEach { (value, label) -> FilterChip(since == value, { onFilter(value, status, workflow) }, label = { Text(label) }) }
                    }
                    Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Workflows.STATUS_FILTERS.forEach { (value, label) -> FilterChip(status == value, { onFilter(since, value, workflow) }, label = { Text(label) }) }
                    }
                    if (runs.workflows.size > 1 || workflow.isNotBlank()) Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        FilterChip(workflow.isBlank(), { onFilter(since, status, "") }, label = { Text("All workflows") })
                        runs.workflows.forEach { (id, name) -> FilterChip(workflow == id, { onFilter(since, status, id) }, label = { Text(name.ifBlank { id }, maxLines = 1) }) }
                    }
                    if (runs.summary.isNotBlank()) Text(runs.summary, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(top = 4.dp).testTag("allRunsSummary"))
                }
            }
            if (runs.runs.isEmpty()) item { Note("No runs match these filters. Runs from your computer, triggers, this phone and Rem all show up here.") }
            items(runs.runs, key = { it.workflowId + "/" + it.run.id }) { row ->
                Surface(onClick = { onOpen(row) }, shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.surface,
                    modifier = Modifier.fillMaxWidth().testTag("run:${row.workflowId}/${row.run.id}")) {
                    Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        if (row.project.isNotBlank()) Text(row.project, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.primary,
                            maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Text(row.workflowName.ifBlank { row.workflowId }, style = MaterialTheme.typography.titleMedium, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        WorkflowStatus(row.run.status)
                        RunErrorReason(row.run)
                        Text(allRunLine(row), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 2, overflow = TextOverflow.Ellipsis)
                    }
                }
            }
            if (runs.earlier > 0) item { Note("${runs.earlier} older ${if (runs.earlier == 1) "run matches" else "runs match"}. See them in Workflow runs on your computer.") }
        }
    }
}

/** "Started 5 min ago · took 1 min 15 s · Trigger · 1,200 tokens · $0.30" */
internal fun allRunLine(row: AllWorkflowRun): String = listOf(
    row.run.startedAt.takeIf { it.isNotBlank() }?.let { "Started ${whenText(it, past = true)}" }.orEmpty(),
    durationText(row.run.startedAt, row.run.finishedAt).takeIf { it.isNotBlank() }?.let { if (row.run.active) "running $it" else "took $it" }.orEmpty(),
    Workflows.startedByText(row.startedBy),
    row.tokens.takeIf { it.isNotBlank() && it != "0" }?.let { "$it tokens" }.orEmpty(),
    row.cost.takeIf { it.isNotBlank() && it != "$0" }.orEmpty()).filter { it.isNotBlank() }.joinToString(" · ")

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun WorkflowDetail(workflow: Workflow, selectedRun: String?, state: WorkflowScreenState, onSelectRun: (String?) -> Unit,
                            onRetry: () -> Unit, onDismissNotice: () -> Unit, onRunWithInputs: (JSONObject) -> Unit = {},
                            onTrigger: (WorkflowTrigger, Boolean) -> Unit = { _, _ -> }, onReview: ((WorkflowTrigger) -> Unit)? = null,
                            shared: SharedRun? = null, askRem: ((String) -> Unit)? = null, askingRem: Boolean = false,
                            askRemError: String = "", onTests: (() -> Unit)? = null, onAction: (String, String?) -> Unit) {
    var tab by rememberSaveable(workflow.id) { mutableStateOf("graph") }
    var node by remember { mutableStateOf<WorkflowNode?>(null) }
    var confirm by remember { mutableStateOf<Pair<String, String?>?>(null) }
    var turnOn by remember { mutableStateOf<WorkflowTrigger?>(null) }
    val tabs = listOf("graph", "steps", "runs") + if (workflow.triggers != null) listOf("triggers") else emptyList()
    val layout = remember(workflow.nodes, workflow.edges) { GraphLayout.of(workflow.nodes, workflow.edges) }
    // The detail shown is for the chosen run only once the desktop sent it.
    val detail = workflow.run?.takeIf { selectedRun == null || it.run.id == selectedRun }
    val statuses = detail?.nodes?.associateBy { it.nodeId }.orEmpty()
    // Shared text opens the Run sheet by itself once, when the input is known and the desktop allows a run.
    var chooseInput by remember(shared?.text) { mutableStateOf(false) }
    var sharedOpened by remember(shared?.text) { mutableStateOf(false) }
    val canRunShared = shared?.input != null && workflow.runControl.allowed && !workflow.inputs.isNullOrEmpty()
    LaunchedEffect(shared?.text, shared?.input, canRunShared) {
        if (shared == null || sharedOpened) return@LaunchedEffect
        if (canRunShared) { sharedOpened = true; confirm = "workflows.run" to null }
        else if (shared.input == null && shared.choices.size > 1) { sharedOpened = true; chooseInput = true }
    }
    Column(Modifier.fillMaxSize()) {
        WorkflowBanners(state, onRetry, onDismissNotice)
        if (shared != null) SharedRunCard(shared, enabled = canRunShared && state.online && !state.busy,
            onRun = { confirm = "workflows.run" to null }, onChooseInput = { chooseInput = true })
        RunCard(workflow, detail, selectedRun, state, onLatest = { onSelectRun(null) }, askRem = askRem, askingRem = askingRem,
            askRemError = askRemError) { action, run -> confirm = action to run }
        // Try another model on this workflow's tests; the desktop runs both and the workflow only changes on Use this model.
        if (onTests != null) TextButton(onClick = onTests, modifier = Modifier.padding(horizontal = 8.dp).testTag("workflowTests")) {
            Text("Tests · Try another model")
        }
        PrimaryTabRow(selectedTabIndex = tabs.indexOf(tab).coerceAtLeast(0)) {
            Tab(tab == "graph", { tab = "graph" }, text = { Text("Graph") })
            Tab(tab == "steps", { tab = "steps" }, text = { Text("Steps") })
            Tab(tab == "runs", { tab = "runs" }, text = { Text(if (workflow.runs.isEmpty()) "Runs" else "Runs (${workflow.runs.size + workflow.earlierRuns})") })
            workflow.triggers?.let { triggers ->
                Tab(tab == "triggers", { tab = "triggers" }, text = { Text(if (triggers.isEmpty()) "Triggers" else "Triggers (${triggers.size})") },
                    modifier = Modifier.testTag("workflowTriggersTab"))
            }
        }
        Box(Modifier.weight(1f).fillMaxWidth()) {
            when (tab) {
                "graph" -> if (workflow.nodes.isEmpty()) Note("This workflow has no steps the desktop can show.", Modifier.padding(20.dp))
                    else WorkflowGraph(workflow, layout, statuses, Modifier.fillMaxSize()) { node = it }
                "steps" -> WorkflowSteps(workflow, layout, statuses) { node = it }
                "triggers" -> WorkflowTriggers(workflow, enabled = state.online && !state.busy, onReview = onReview) { trigger, on ->
                    if (on) turnOn = trigger else onTrigger(trigger, false)
                }
                else -> WorkflowRuns(workflow, detail?.run?.id ?: selectedRun) { onSelectRun(it); tab = "graph" }
            }
        }
    }
    node?.let { shown ->
        ModalBottomSheet(onDismissRequest = { node = null }) { NodeDetail(workflow, shown, statuses[shown.id]) }
    }
    // Turning a trigger on starts unattended work, so the phone asks first. The desktop refuses it unconfirmed.
    turnOn?.let { trigger ->
        AlertDialog(onDismissRequest = { turnOn = null }, title = { Text("Turn on ${trigger.title}?", maxLines = 2, overflow = TextOverflow.Ellipsis) },
            text = { Text(buildString {
                append("It starts ${workflow.summary.name.ifBlank { "this workflow" }} on your computer without asking, each time it fires. ")
                append("Runs use the trigger's saved inputs; approvals and limits still apply.")
                if (trigger.needsApproval) append(" Turning it on doesn't approve the changes since you last approved it: Review changes opens next, and triggered starts are skipped until you approve them.")
                if (workflow.triggersPaused) append(" All triggers are paused, so it waits until you resume them.")
            }) },
            confirmButton = { TextButton(onClick = { onTrigger(trigger, true); turnOn = null }, modifier = Modifier.testTag("triggerOnConfirm")) { Text("Turn on") } },
            dismissButton = { TextButton(onClick = { turnOn = null }) { Text("Cancel") } })
    }
    if (chooseInput && shared != null) SharedInputChooser(shared, onDismiss = { chooseInput = false }) { name ->
        shared.onChoose(name); chooseInput = false
        if (workflow.runControl.allowed && !workflow.inputs.isNullOrEmpty()) confirm = "workflows.run" to null
    }
    val form = workflow.inputs.orEmpty()
    confirm?.takeIf { it.first == "workflows.run" && form.isNotEmpty() }?.let {
        val prefill = shared?.input?.let { mapOf(it to shared.prefill) }.orEmpty()
        val label = shared?.input?.let { name -> shared.choices.firstOrNull { it.name == name }?.label }
        RunSheet(workflow, form, state, prefill = prefill,
            note = label?.let { if (shared.file != null) "The shared file goes in $it. " + WorkflowShare.FILE_CAUTION else "What you shared is in $it. " + WorkflowShare.CAUTION },
            onDismiss = { confirm = null }) { inputs -> onRunWithInputs(inputs); confirm = null }
    }
    confirm?.takeUnless { it.first == "workflows.run" && form.isNotEmpty() }?.let { (action, run) ->
        val name = workflow.summary.name.ifBlank { "this workflow" }
        val failed = action == "workflows.resume" && (detail?.run?.status ?: workflow.runs.firstOrNull { it.id == run }?.status) == "failed"
        AlertDialog(onDismissRequest = { confirm = null }, title = { Text(when (action) {
            "workflows.run" -> "Run $name?"; "workflows.stop" -> "Stop this run?"; Workflows.RERUN -> "Run $name again with the same inputs?"
            else -> if (failed) "Resume from the failed step?" else "Resume this run?"
        }, maxLines = 2, overflow = TextOverflow.Ellipsis) }, text = { Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(when (action) {
                "workflows.run" -> "Raticode starts a new run on your computer with the workflow's saved inputs. Its approvals and limits still apply."
                "workflows.stop" -> "Raticode asks the run to stop on your computer. Steps that already finished stay finished."
                Workflows.RERUN -> "Raticode starts a new run on your computer with the inputs and trigger events this run used, saved on your computer. It runs the workflow as it is now, and its approvals and limits still apply."
                else -> "Raticode starts a new run on your computer that reuses the steps that finished and continues from where this one stopped. Its approvals and limits still apply."
            })
            // The steps a resume runs again: the one that failed or was cut off, and those after it as the graph reaches them.
            if (action == "workflows.resume" && workflow.resumeControl.rerunNodes.isNotEmpty())
                PreviewList("Runs again", workflow.resumeControl.rerunNodes, MaterialTheme.colorScheme.onSurface)
            // The same preview the desktop shows before its Run button starts anything.
            if (action == "workflows.run") workflow.preview.let { preview ->
                if (preview.destructive.isNotEmpty()) PreviewList("This run can change things", preview.destructive, MaterialTheme.colorScheme.error)
                if (preview.warnings.isNotEmpty()) PreviewList("Warnings", preview.warnings, MaterialTheme.colorScheme.tertiary)
            }
        } }, confirmButton = { TextButton(onClick = { onAction(action, run); confirm = null }) { Text(when (action) {
            "workflows.run" -> "Run"; "workflows.stop" -> "Stop run"; Workflows.RERUN -> "Run again"; else -> "Resume"
        }) } }, dismissButton = { TextButton(onClick = { confirm = null }) { Text("Cancel") } })
    }
}

/**
 * The Run sheet for a workflow that takes inputs: one field per input, from the same schema the desktop's Run dialog
 * reads, with the desktop's preview below. Problems show under each field before anything is sent; the desktop checks
 * the values again before it starts the run.
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
internal fun RunSheet(workflow: Workflow, inputs: List<WorkflowInput>, state: WorkflowScreenState, prefill: Map<String, String> = emptyMap(),
                      note: String? = null, onDismiss: () -> Unit, onRun: (JSONObject) -> Unit) {
    // Shared text replaces its input's default; it is never put in a password field.
    var drafts by remember(workflow.id, workflow.revision, prefill) {
        mutableStateOf(RunForm.drafts(inputs) + prefill.filterKeys { name -> inputs.any { it.name == name && (it.kind == "text" || it.kind == "file") && !it.secret } })
    }
    var errors by remember(workflow.id, workflow.revision) { mutableStateOf<Map<String, String>>(emptyMap()) }
    val name = workflow.summary.name.ifBlank { "this workflow" }
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
        Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).imePadding().padding(start = 20.dp, end = 20.dp, bottom = 24.dp)
            .testTag("workflowRunSheet"), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("Run $name", style = MaterialTheme.typography.titleLarge, maxLines = 2, overflow = TextOverflow.Ellipsis)
            Text("Raticode starts a new run on your computer with these inputs. Its approvals and limits still apply.",
                style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (note != null) Text(note, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.tertiary,
                modifier = Modifier.testTag("workflowRunShareNote"))
            inputs.forEach { input ->
                RunInputField(input, drafts[input.name].orEmpty(), errors[input.name]) { value ->
                    drafts = drafts + (input.name to value); errors = errors - input.name
                }
            }
            workflow.preview.let { preview ->
                if (preview.destructive.isNotEmpty()) PreviewList("This run can change things", preview.destructive, MaterialTheme.colorScheme.error)
                if (preview.warnings.isNotEmpty()) PreviewList("Warnings", preview.warnings, MaterialTheme.colorScheme.tertiary)
            }
            if (errors.isNotEmpty()) Text("Fix the fields marked above, then run.", style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error, modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.End)) {
                TextButton(onClick = onDismiss) { Text("Cancel") }
                Button(enabled = state.online && !state.busy, modifier = Modifier.testTag("workflowRunSend"), onClick = {
                    val (values, problems) = RunForm.values(inputs, drafts)
                    errors = problems
                    if (problems.isEmpty()) onRun(values)
                }) { Icon(ThreadIcons.Play, null, Modifier.size(16.dp)); Spacer(Modifier.width(6.dp)); Text("Run") }
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun RunInputField(input: WorkflowInput, draft: String, error: String?, onChange: (String) -> Unit) {
    val label = input.label + if (input.needed) " *" else ""
    val hint = error ?: when {
        input.kind == "json" -> RunForm.jsonHint(input)
        input.hasDefault && draft.isBlank() && input.kind != "boolean" -> "Leave blank to use the workflow's default."
        else -> null
    }
    val supporting: (@Composable () -> Unit)? = listOfNotNull(input.description.takeIf { it.isNotBlank() }, hint).takeIf { it.isNotEmpty() }?.let { lines ->
        { Text(lines.joinToString("\n")) }
    }
    val tag = Modifier.testTag("runInput:${input.name}")
    when (input.kind) {
        // Only a file shared from another app fills a file input; the sheet shows which one.
        "file" -> Column(tag, verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(label, style = MaterialTheme.typography.bodyLarge)
            if (input.description.isNotBlank()) Text(input.description, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(draft.ifEmpty { if (input.needed) RunForm.FILE_NEEDED else "No file. Share one to Raticode from another app to fill this." },
                style = MaterialTheme.typography.bodyMedium, color = if (draft.isEmpty()) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface)
            if (error != null) Text(error, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
        }
        "boolean" -> Row(tag.fillMaxWidth().toggleable(draft == "true", role = Role.Switch) { onChange(it.toString()) }.padding(vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(label, style = MaterialTheme.typography.bodyLarge)
                if (input.description.isNotBlank()) Text(input.description, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Switch(checked = draft == "true", onCheckedChange = null)
        }
        "choice" -> Column(tag, verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(label, style = MaterialTheme.typography.bodyLarge)
            if (input.description.isNotBlank()) Text(input.description, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            val chosen = draft.toIntOrNull()
            if (input.choices.size <= 6) FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                input.choices.forEachIndexed { index, choice ->
                    FilterChip(chosen == index, { onChange(if (chosen == index && !input.needed) "" else index.toString()) },
                        label = { Text(RunForm.choiceText(choice), maxLines = 1, overflow = TextOverflow.Ellipsis) })
                }
            } else {
                var open by remember { mutableStateOf(false) }
                Box {
                    OutlinedButton(onClick = { open = true }, modifier = Modifier.fillMaxWidth()) {
                        Text(chosen?.let { input.choices.getOrNull(it) }?.let(RunForm::choiceText) ?: "Choose…", Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                    DropdownMenu(open, { open = false }) {
                        if (!input.needed) DropdownMenuItem(text = { Text("No choice") }, onClick = { onChange(""); open = false })
                        input.choices.forEachIndexed { index, choice ->
                            DropdownMenuItem(text = { Text(RunForm.choiceText(choice)) }, onClick = { onChange(index.toString()); open = false })
                        }
                    }
                }
            }
            if (error != null) Text(error, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
            else if (input.hasDefault && chosen == null) Text("Leave unchosen to use the workflow's default.", style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        else -> OutlinedTextField(draft, onChange, tag.fillMaxWidth(), label = { Text(label) }, supportingText = supporting, isError = error != null,
            singleLine = input.kind == "integer" || input.kind == "number" || input.secret, minLines = if (input.kind == "json") 2 else 1, maxLines = 6,
            textStyle = if (input.kind == "json") LocalTextStyle.current.copy(fontFamily = FontFamily.Monospace) else LocalTextStyle.current,
            visualTransformation = if (input.secret) PasswordVisualTransformation() else VisualTransformation.None,
            keyboardOptions = KeyboardOptions(keyboardType = when {
                input.secret -> KeyboardType.Password; input.kind == "integer" -> KeyboardType.Number
                input.kind == "number" -> KeyboardType.Decimal; else -> KeyboardType.Text
            }, autoCorrectEnabled = input.kind == "text" && !input.secret))
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
                    askRem: ((String) -> Unit)? = null, askingRem: Boolean = false, askRemError: String = "",
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
                    Icon(ThreadIcons.Resume, null, Modifier.size(16.dp)); Spacer(Modifier.width(6.dp))
                    Text(if (run?.status == "failed") "Resume from failed step" else "Resume")
                }
                val rerun = workflow.rerunControl
                if (rerun != null && run != null && !run.active) OutlinedButton(onClick = { onAction(Workflows.RERUN, run.id) },
                    enabled = ready && rerun.allowed && detail != null, modifier = Modifier.testTag("workflowRerun")) {
                    Icon(ThreadIcons.Play, null, Modifier.size(16.dp)); Spacer(Modifier.width(6.dp)); Text("Run again with the same inputs")
                }
                // Puts the run's failure in Rem's message box as a file; the user writes the question and sends it.
                if (askRem != null && run != null && !run.active && Workflows.canAskRem(run.status)) OutlinedButton(onClick = { askRem(run.id) },
                    enabled = state.online && !askingRem && detail != null, modifier = Modifier.testTag("workflowAskRem")) {
                    Icon(ThreadIcons.Rem, null, Modifier.size(16.dp)); Spacer(Modifier.width(6.dp))
                    Text(if (askingRem) "Preparing for Rem…" else "Ask Rem about this failure")
                }
            }
            if (askRemError.isNotBlank()) Text(askRemError, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error,
                modifier = Modifier.testTag("workflowAskRemError"))
            // Say why a control is off; a disabled button alone does not explain itself.
            val reasons = buildList {
                if (active != null && !workflow.stopControl.allowed) add("Stop: " + workflow.stopControl.reason.ifBlank { "Your desktop doesn't allow stopping this run." })
                if (active == null && !workflow.runControl.allowed) add("Run: " + workflow.runControl.reason.ifBlank { "Your desktop doesn't allow running this workflow from the phone." })
                if (active == null && run != null && run.status in setOf("failed", "stopped", "interrupted") && !workflow.resumeControl.allowed)
                    add("Resume: " + workflow.resumeControl.reason.ifBlank { "Your desktop can't resume this run." })
                // Names any password inputs the desktop didn't save, which the phone can't fill in.
                workflow.rerunControl?.takeIf { run != null && !run.active && !it.allowed }?.let {
                    add("Run again: " + it.reason.ifBlank { "Your desktop can't run this run again." })
                }
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
        item { Note("Workflows are edited in Raticode on your computer. The phone shows them and can run, stop, resume or run them again.") }
    }
}

/** A workflow's triggers: what starts it, when it next runs and how its last start went, each with its own switch. */
@Composable
private fun WorkflowTriggers(workflow: Workflow, enabled: Boolean, onReview: ((WorkflowTrigger) -> Unit)? = null,
                             onSwitch: (WorkflowTrigger, Boolean) -> Unit) {
    val triggers = workflow.triggers.orEmpty()
    LazyColumn(Modifier.fillMaxSize().testTag("workflowTriggers"), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        if (workflow.triggersPaused) item {
            Note("All triggers are paused. Each keeps its own switch; resume them all from the Workflows list or in Workflow runs on your computer.")
        }
        if (triggers.isEmpty()) item { Note("No triggers. Add a schedule, file watch or webhook to this workflow in Raticode on your computer.") }
        items(triggers, key = { it.id }) { trigger ->
            Surface(shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.surface, modifier = Modifier.fillMaxWidth()
                .testTag("trigger:${trigger.id}").toggleable(trigger.enabled, enabled = enabled, role = Role.Switch) { onSwitch(trigger, it) }) {
                Row(Modifier.padding(12.dp), verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                        Text(trigger.title, style = MaterialTheme.typography.titleSmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        Text(listOf(trigger.kindLabel.takeIf { it != trigger.title }.orEmpty(), trigger.description).filter { it.isNotBlank() }.joinToString(" · "),
                            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        Text(triggerNextText(trigger, workflow.triggersPaused), style = MaterialTheme.typography.bodySmall,
                            color = if (trigger.enabled && workflow.triggersPaused) MaterialTheme.colorScheme.tertiary else MaterialTheme.colorScheme.onSurface)
                        if (trigger.dailyLimit.isNotBlank()) Text(trigger.dailyLimit, style = MaterialTheme.typography.bodySmall,
                            color = if (trigger.limitReached) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.testTag("triggerDailyLimit:${trigger.id}"))
                        if (trigger.timeLimit.isNotBlank()) Text(trigger.timeLimit, style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.testTag("triggerTimeLimit:${trigger.id}"))
                        trigger.lastResult?.let { last ->
                            WorkflowStatus(Workflows.triggerStatus(last.status), text = "Last: " + Workflows.triggerResultLabel(last.status) +
                                (last.at.takeIf { it.isNotBlank() }?.let { " · ${whenText(it, past = true)}" } ?: ""))
                            if (last.message.isNotBlank()) Text(last.message, style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        }
                        // Triggered starts are skipped until the user approves the workflow as it is now.
                        if (trigger.needsApproval) {
                            Text(trigger.approvalMessage, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error,
                                modifier = Modifier.testTag("triggerApproval:${trigger.id}"))
                            if (onReview != null) OutlinedButton(onClick = { onReview(trigger) }, enabled = enabled,
                                modifier = Modifier.testTag("triggerReview:${trigger.id}")) { Text("Review changes") }
                            else Text(Workflows.UPDATE_APPROVAL, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                    Switch(checked = trigger.enabled, onCheckedChange = null, enabled = enabled)
                }
            }
        }
        item { Note("Add or change triggers in Raticode on your computer. Turning one on from here asks first.") }
    }
}

/**
 * Review changes: each file that differs from the version this trigger was approved to run, then Approve this version,
 * which approves exactly what is listed here. A file opens its diff. When the phone couldn't show a file's whole change,
 * Approve this version asks a second time, names those files and offers to open them ([onOpen], null when it can't).
 */
@Composable
internal fun TriggerReview(changes: TriggerChanges, state: WorkflowScreenState, onRetry: () -> Unit, onFile: (TriggerChangedFile) -> Unit,
                           onCancel: () -> Unit, onOpen: ((TriggerChangedFile) -> Unit)? = null,
                           onApprove: (TriggerChanges, Boolean) -> Unit) {
    var askUnseen by remember(changes.fingerprint) { mutableStateOf(false) }
    Column(Modifier.fillMaxSize()) {
        WorkflowBanners(state, onRetry) {}
        LazyColumn(Modifier.weight(1f).testTag("triggerReview"), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            item { Note(triggerReviewText(changes)) }
            items(changes.files, key = { it.change + ":" + it.path }) { file ->
                Surface(onClick = { onFile(file) }, shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.surface,
                    modifier = Modifier.fillMaxWidth().testTag("triggerFile:${file.path}")) {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                        Text(file.path, style = MaterialTheme.typography.titleSmall, fontFamily = FontFamily.Monospace, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        Text("${file.kind} · ${Workflows.fileChangeLabel(file.change)}", style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                        if (file.unseen.isNotBlank() || file.warning.isNotBlank()) Text(
                            file.warning.ifBlank { Workflows.unseenLabel(file.unseen) }, style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.error, modifier = Modifier.testTag("triggerUnseen:${file.path}"))
                    }
                }
            }
            if (changes.truncated) item { Note("Some diffs are long, so only their start is shown. See all of them on your computer.") }
        }
        Row(Modifier.fillMaxWidth().padding(16.dp), horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.End)) {
            TextButton(onClick = onCancel) { Text("Not now") }
            Button(onClick = { if (changes.needsUnseenConfirm) askUnseen = true else onApprove(changes, false) },
                enabled = state.online && !state.busy && changes.approval != "approved" && changes.fingerprint.isNotBlank(),
                modifier = Modifier.testTag("triggerApprove")) { Text("Approve this version") }
        }
    }
    if (askUnseen) UnseenConfirm(changes, onOpen = onOpen?.let { open -> { file: TriggerChangedFile -> askUnseen = false; open(file) } },
        onBack = { askUnseen = false }) { askUnseen = false; onApprove(changes, true) }
}

/** The second question before approving changes the phone couldn't show in full, naming each file. */
@Composable
private fun UnseenConfirm(changes: TriggerChanges, onOpen: ((TriggerChangedFile) -> Unit)?, onBack: () -> Unit, onConfirm: () -> Unit) {
    val files = changes.unseen
    AlertDialog(onDismissRequest = onBack, modifier = Modifier.testTag("triggerUnseenConfirm"),
        title = { Text("Approve changes you haven't seen?") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(unseenConfirmText(changes))
                files.forEach { file ->
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Column(Modifier.weight(1f)) {
                            Text(file.path, style = MaterialTheme.typography.bodyMedium, fontFamily = FontFamily.Monospace, maxLines = 2, overflow = TextOverflow.Ellipsis)
                            Text(Workflows.unseenLabel(file.unseen), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        if (onOpen != null && file.openPath.isNotBlank()) TextButton(onClick = { onOpen(file) },
                            modifier = Modifier.testTag("triggerUnseenOpen:${file.path}")) { Text("Open") }
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = onConfirm, modifier = Modifier.testTag("triggerUnseenApprove")) { Text("Approve without seeing all") } },
        dismissButton = { TextButton(onClick = onBack) { Text("Back") } })
}

/** Why Approve this version asks again: which changes the phone couldn't show, and which MCP servers can change. */
internal fun unseenConfirmText(changes: TriggerChanges): String {
    val unpinned = changes.unseen.count { it.unseen == "unpinned" }
    val count = changes.unseen.size - unpinned
    val parts = mutableListOf<String>()
    if (count > 0) parts += if (count == 1) "Your phone couldn't show all of this file's change." else "Your phone couldn't show all of these $count files' changes."
    if (changes.unseenHidden > 0) parts += "${changes.unseenHidden} more changed ${if (changes.unseenHidden == 1) "file isn't" else "files aren't"} listed here."
    if (count > 0 || changes.unseenHidden > 0) parts += "Open them to read what changed, or review them on your computer, before you approve."
    if (unpinned > 0) parts += (if (unpinned == 1) "This MCP server runs a package with no exact version, " else "These $unpinned MCP servers run packages with no exact version, ") +
        "so each run fetches the newest release, which nobody has reviewed. Pin a version in Rem's MCP server list (pkg@1.2.3 or pkg==1.2.3) to stop that."
    return parts.joinToString(" ")
}

/** The line above a trigger's changed files: why it waits and when its version was last approved. */
internal fun triggerReviewText(changes: TriggerChanges): String = when (changes.approval) {
    "approved" -> "Nothing to approve: this trigger runs the workflow as it is now."
    "missing" -> "The workflow file is missing on your computer, so this trigger's runs fail until it's back."
    else -> buildString {
        append(changes.message.ifBlank { "Workflow changed since you approved it." })
        append(" Triggered starts are skipped until you approve this version.")
        if (changes.approvedAt.isNotBlank()) append(" You last approved it ${whenText(changes.approvedAt, past = true)}.")
        if (changes.files.isEmpty()) append(" No file's text differs, but the workflow now uses its files differently.")
    }
}

/** One changed file's diff, in the same view as Source control's. */
@Composable
private fun TriggerFileDiff(file: TriggerChangedFile) {
    when {
        file.change == "removed" && file.diff.isBlank() -> Note("The workflow no longer runs this file.", Modifier.padding(20.dp))
        file.change == "missing" && file.diff.isBlank() -> Note("The workflow names this file, but it isn't on your computer.", Modifier.padding(20.dp))
        else -> DiffView(SourceDiff(file.path, "trigger", file.diff, file.truncated, binary = !file.diffAvailable), "", "", null)
    }
}

/** "Next: in 5 min", "Off", or "Paused with all triggers". */
internal fun triggerNextText(trigger: WorkflowTrigger, paused: Boolean): String = when {
    !trigger.enabled -> "Off"
    paused -> "Paused with all triggers"
    trigger.nextRunAt.isNotBlank() -> "Next: ${whenText(trigger.nextRunAt)}"
    trigger.kind == "file" -> "On: runs when its files change"
    trigger.kind == "webhook" -> "On: runs when its webhook is called"
    else -> "On"
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
                    RunErrorReason(run)
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
