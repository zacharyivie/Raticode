package dev.raticode.mobile.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.live.LiveClient

private const val OPT_IN = "To answer approvals and control swarms from this phone, open Paired devices on your computer and turn on Allow approvals and swarm controls."

@Composable
private fun PageHeader(title: String, onBack: (() -> Unit)?, action: @Composable RowScope.() -> Unit = {}) {
    Row(Modifier.fillMaxWidth().padding(start = if (onBack == null) 20.dp else 12.dp, end = 12.dp), verticalAlignment = Alignment.CenterVertically) {
        if (onBack != null) IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") }
        Text(title, style = MaterialTheme.typography.titleLarge, modifier = Modifier.weight(1f).heightIn(min = 48.dp).wrapContentHeight(Alignment.CenterVertically))
        action()
    }
}

@androidx.annotation.RequiresApi(29)
@Composable
internal fun ApprovalsPage(client: LiveClient, onBack: (() -> Unit)?) {
    LaunchedEffect(client.connected) { if (client.connected) client.refreshApprovals() }
    var answering by remember { mutableStateOf<Pair<LiveClient.Approval, Boolean>?>(null) }
    var instruction by rememberSaveable { mutableStateOf("") }
    Column(Modifier.fillMaxSize()) {
        PageHeader("Approvals", onBack) {
            TextButton(onClick = client::refreshApprovals, enabled = client.connected) { Text("Refresh") }
        }
        LazyColumn(Modifier.weight(1f).fillMaxWidth().testTag("approvalList"), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
            item {
                Text(when {
                    !client.connected -> "Connect to your desktop to see approvals."
                    !client.remoteControl -> OPT_IN
                    !client.approvalsLoaded -> "Checking desktop approvals…"
                    client.approvals.isEmpty() -> "Nothing is waiting for your approval."
                    else -> "${client.approvals.size} waiting. Your answer goes to the desktop, which carries it out."
                }, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            items(client.approvals, key = { it.id }) { approval ->
                SettingsGroup(approval.title) {
                    // Waiting on you, in the desktop's attention color, with the kind of request in words.
                    StatusPill(if (approval.source == "swarm") "Swarm request" else "Workflow approval", RemTaskStatus.Tone.ATTENTION, icon = ThreadIcons.Waiting)
                    ClampedText(approval.summary, 6, style = MaterialTheme.typography.bodyMedium)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(onClick = { instruction = ""; answering = approval to true }, enabled = client.connected) { Text(if (approval.source == "swarm") "Proceed" else "Approve") }
                        OutlinedButton(onClick = { instruction = ""; answering = approval to false }, enabled = client.connected) { Text(if (approval.source == "swarm") "Redirect" else "Reject") }
                    }
                }
            }
        }
    }
    answering?.let { (approval, approve) ->
        val needsText = !approve && approval.instructionRequired
        AlertDialog(onDismissRequest = { answering = null },
            title = { Text(if (approve) "Approve on desktop?" else if (approval.source == "swarm") "Redirect the swarm?" else "Reject on desktop?") },
            text = { Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(approval.summary, style = MaterialTheme.typography.bodySmall)
                OutlinedTextField(instruction, { instruction = it.take(2000) }, modifier = Modifier.fillMaxWidth(),
                    label = { Text(if (needsText) "What should it do instead?" else "Note (optional)") }, maxLines = 4)
            } },
            confirmButton = { TextButton(enabled = !needsText || instruction.isNotBlank(), onClick = {
                client.resolveApproval(approval, approve, instruction); answering = null
            }) { Text(if (approve) "Approve" else "Send") } },
            dismissButton = { TextButton(onClick = { answering = null }) { Text("Cancel") } })
    }
}

private fun statusLabel(status: String) = when (status) {
    "awaiting_approval" -> "Waiting for you"
    "cancelled" -> "Stopped"
    else -> status.replaceFirstChar { it.uppercase() }
}

/** A swarm's milestone progress as a thin bar with its project and percent. */
@Composable
private fun SwarmProgress(progress: Int, project: String) {
    Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
        LinearProgressIndicator({ (progress / 100f).coerceIn(0f, 1f) }, Modifier.fillMaxWidth().height(4.dp),
            trackColor = MaterialTheme.colorScheme.surfaceVariant, drawStopIndicator = {})
        Text(listOf(project, "$progress% complete").filter { it.isNotBlank() }.joinToString(" · "), style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

@OptIn(ExperimentalLayoutApi::class)
@androidx.annotation.RequiresApi(29)
@Composable
internal fun SwarmsPage(client: LiveClient, onBack: (() -> Unit)?, onApprovals: () -> Unit) {
    var open by rememberSaveable { mutableStateOf<String?>(null) }
    DisposableEffect(open) { client.openSwarms(open); onDispose { } }
    DisposableEffect(Unit) { onDispose { client.closeSwarms() } }
    // The list's own back is the app's; only an open swarm is closed here.
    androidx.activity.compose.BackHandler(enabled = open != null) { open = null }
    val current = client.swarms.firstOrNull { it.id == open }
    var message by rememberSaveable(open) { mutableStateOf("") }
    var confirmStop by remember { mutableStateOf(false) }
    var starting by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxSize()) {
        PageHeader(if (current != null) current.title else "Swarms", if (open != null) ({ open = null }) else onBack)
        if (current == null) {
            LazyColumn(Modifier.weight(1f).fillMaxWidth().testTag("swarmList"), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                item {
                    Text(when {
                        !client.connected -> "Connect to your desktop to see swarms."
                        !client.remoteControl -> OPT_IN
                        !client.swarmsLoaded -> "Loading desktop swarms…"
                        client.swarms.isEmpty() -> "No swarms in your open desktop projects."
                        else -> "Swarms in your open desktop projects. They run on your computer."
                    }, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                items(client.swarms, key = { it.id }) { swarm ->
                    Surface(onClick = { open = swarm.id }, shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.surface, modifier = Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                Text(swarm.title, Modifier.weight(1f), style = MaterialTheme.typography.titleSmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
                                DotStatus(statusLabel(swarm.status), swarmTone(swarm.status))
                            }
                            SwarmProgress(swarm.progress, swarm.project)
                            if (swarm.detail.isNotBlank()) Text(swarm.detail, style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis,
                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }
        } else {
            val board = androidx.compose.foundation.lazy.rememberLazyListState()
            val latest = current.messages?.size ?: 0
            LaunchedEffect(current.id, latest) { if (latest > 0) board.animateScrollToItem(board.layoutInfo.totalItemsCount.coerceAtLeast(1) - 1) }
            // Status and controls stay pinned while the board scrolls to the newest message.
            Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                DotStatus(statusLabel(current.status), swarmTone(current.status), Modifier.testTag("swarmStatus"))
                SwarmProgress(current.progress, current.project)
                current.agents?.takeIf { it.isNotEmpty() }?.let { agents ->
                    // The desktop overview's counts: who is working and who waits.
                    val working = agents.count { it.state == "working" }
                    Text("$working working · ${agents.size - working} idle", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                if (current.detail.isNotBlank()) Text(current.detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    val enabled = client.connected && client.remoteControl
                    if (current.status in setOf("running", "awaiting_approval")) OutlinedButton(onClick = { client.controlSwarm(current.id, "pause") }, enabled = enabled) { Text("Pause") }
                    if (current.status in setOf("paused", "cancelled")) OutlinedButton(onClick = { client.controlSwarm(current.id, "resume") }, enabled = enabled) { Text("Resume") }
                    if (current.status in setOf("running", "awaiting_approval", "paused")) TextButton(onClick = { confirmStop = true }, enabled = enabled,
                        colors = ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.error)) { Text("Stop") }
                    if (current.status == "awaiting_approval") TextButton(onClick = onApprovals) { Text("Open approvals") }
                    if (client.desktopControl && current.status in setOf("idle", "completed", "failed"))
                        Button(onClick = { starting = true }, enabled = client.connected && "swarms.start" !in client.desktopBusy) { Text("Start run") }
                }
                HorizontalDivider()
            }
            LazyColumn(Modifier.weight(1f).fillMaxWidth().testTag("swarmDetail"), state = board, contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                current.agents?.takeIf { it.isNotEmpty() }?.let { agents ->
                    item { SettingsGroup("Agents") { agents.forEach { agent ->
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                            Text(agent.name, Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            DotStatus(agent.state.replace('_', ' ').replaceFirstChar { it.uppercase() }, swarmTone(agent.state))
                        }
                    } } }
                }
                item { Text("Board", style = MaterialTheme.typography.titleMedium) }
                val messages = current.messages.orEmpty()
                if (messages.isEmpty()) item { Text(if (current.messages == null) "Loading activity…" else "No messages yet.", style = MaterialTheme.typography.bodySmall) }
                items(messages.size) { index ->
                    val item = messages[index]
                    // Long board posts keep to a few lines until opened, as the desktop's board does.
                    Column {
                        Text(item.sender, style = MaterialTheme.typography.labelMedium, fontWeight = androidx.compose.ui.text.font.FontWeight.SemiBold,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                        ClampedText(item.body, 6, style = MaterialTheme.typography.bodyMedium)
                    }
                }
            }
            Row(Modifier.fillMaxWidth().padding(8.dp), verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(message, { message = it.take(4000) }, modifier = Modifier.weight(1f).testTag("swarmMessage"),
                    placeholder = { Text("Message the swarm") }, maxLines = 4, enabled = client.remoteControl)
                TextButton(onClick = { client.messageSwarm(current.id, message); message = "" },
                    enabled = client.connected && client.remoteControl && message.isNotBlank()) { Text("Send") }
            }
        }
    }
    if (starting && current != null) {
        var task by rememberSaveable { mutableStateOf("") }
        AlertDialog(onDismissRequest = { starting = false }, title = { Text("Start ${current.title}") },
            text = { OutlinedTextField(task, { task = it.take(4000) }, Modifier.fillMaxWidth(), label = { Text("What should the swarm do?") }, minLines = 3, maxLines = 6) },
            confirmButton = { TextButton(enabled = task.isNotBlank(), onClick = {
                client.desktopAction("swarms.start", org.json.JSONObject().put("swarm_id", current.id).put("task", task.trim())); starting = false
            }) { Text("Start") } },
            dismissButton = { TextButton(onClick = { starting = false }) { Text("Cancel") } })
    }
    if (confirmStop && current != null) AlertDialog(onDismissRequest = { confirmStop = false }, title = { Text("Stop ${current.title}?") },
        text = { Text("The desktop stops the swarm's running agents. You can resume it later.") },
        confirmButton = { TextButton(onClick = { client.controlSwarm(current.id, "stop"); confirmStop = false }) { Text("Stop swarm") } },
        dismissButton = { TextButton(onClick = { confirmStop = false }) { Text("Keep running") } })
}
