package dev.raticode.mobile.ui

import androidx.annotation.RequiresApi
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.selection.toggleable
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.live.Computer
import dev.raticode.mobile.live.ComputerList
import dev.raticode.mobile.live.Computers
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.live.Placement
import dev.raticode.mobile.live.PlacementView
import kotlinx.coroutines.delay

/**
 * Usage › Computers: each computer Raticode can run work on, with its worker limit and load, and, on a desktop that
 * says so, where Rem and each organization run.
 */
@RequiresApi(29)
@Composable
internal fun ComputersPage(client: LiveClient, onBack: () -> Unit) {
    val supported = client.desktopComputers
    val placesWork = client.desktopComputerPlacement == true
    var tab by rememberSaveable { mutableStateOf(Computers.READ) }
    val shown = if (placesWork) tab else Computers.READ
    DisposableEffect(supported, shown) {
        if (supported == true) client.openDesktop(shown)
        onDispose { client.openDesktop(null) }
    }
    val list = client.desktopViews[Computers.READ] as? ComputerList
    Column(Modifier.fillMaxSize().testTag("computersPage")) {
        Header("Computers", "Where Raticode can run work", onBack)
        if (supported == true && placesWork) {
            val tabs = listOf(Computers.READ to "Computers", Computers.PLACEMENT to "Where work runs")
            SingleChoiceSegmentedButtonRow(Modifier.padding(horizontal = 16.dp, vertical = 4.dp).fillMaxWidth().testTag("computersTabs")) {
                tabs.forEachIndexed { index, (op, label) ->
                    SegmentedButton(shown == op, { tab = op }, SegmentedButtonDefaults.itemShape(index, tabs.size), Modifier.testTag("computersTab:$op")) {
                        Text(label, maxLines = 1)
                    }
                }
            }
        }
        Box(Modifier.weight(1f)) {
            if (supported == false) Column(Modifier.padding(20.dp)) { Note(Computers.UPDATE_DESKTOP) }
            else if (shown == Computers.PLACEMENT) DesktopGate(client, Computers.PLACEMENT, "Loading where work runs…") {
                (client.desktopViews[Computers.PLACEMENT] as? PlacementView)?.let { view ->
                    PlacementRows(view, busy = Computers.PLACEMENT in client.desktopBusy, error = client.desktopErrors[Computers.PLACEMENT]) { params ->
                        client.desktopAction(Computers.PLACE, params)
                    }
                }
            }
            else DesktopGate(client, Computers.READ, "Loading computers…") {
                if (list != null) ComputerRows(list, busy = Computers.READ in client.desktopBusy) { limit ->
                    client.desktopAction(Computers.SET, Computers.setParams(limit))
                }
            }
        }
    }
}

/** Rem's and each organization's strategy and assigned computers; a change goes to the desktop with `computers.place`. */
@Composable
internal fun PlacementRows(view: PlacementView, busy: Boolean, error: String?, onPlace: (org.json.JSONObject) -> Unit) {
    // A manual pick not sent yet, by consumer: manual waits for at least one computer.
    var drafts by remember { mutableStateOf(mapOf<String, Pair<List<String>, List<String>>>()) }
    var manual by remember { mutableStateOf(setOf<String>()) }
    // The desktop's refusal, shown under the row it answered.
    var sent by remember { mutableStateOf<String?>(null) }
    var refusals by remember { mutableStateOf(mapOf<String, String>()) }
    LaunchedEffect(error) {
        val consumer = sent
        if (error != null && consumer != null) { refusals = refusals + (consumer to error); sent = null }
    }
    LaunchedEffect(view) {
        // A new answer replaces the picks it saved; a pick still waiting for a computer, or one refused, stays.
        val waiting = drafts.filter { (consumer, pick) -> pick.first.isEmpty() || consumer in refusals }.keys
        drafts = drafts.filterKeys { it in waiting }
        manual = manual.filter { it in waiting }.toSet()
    }
    fun place(p: Placement, strategy: String, computers: List<String> = emptyList(), shared: List<String> = emptyList()) {
        refusals = refusals - p.consumer
        if (strategy == "manual") {
            drafts = drafts + (p.consumer to (computers to shared.filter { it in computers }))
            manual = manual + p.consumer
            if (computers.isEmpty()) return
        } else { drafts = drafts - p.consumer; manual = manual - p.consumer }
        sent = p.consumer
        onPlace(Computers.placeParams(p.consumer, strategy, computers, shared))
    }
    LazyColumn(Modifier.fillMaxSize().testTag("placements"), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item {
            Note("Work starts on your computer for now. A paired desktop takes work only when you pick Run there on waiting work, " +
                "or through an organization's fleet execution target. A computer can be picked for two organizations, or Rem and an organization, only when both mark it shared.")
        }
        items(view.consumers, key = { it.consumer }) { p ->
            val strategy = if (p.consumer in manual) "manual" else p.strategy
            val (picked, shared) = drafts[p.consumer] ?: (p.computers to p.shared)
            SettingsGroup(p.name) {
                Column(Modifier.testTag("placement:${p.consumer}"), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Fact("Runs on", Computers.assignedText(p, view).ifBlank { "No computer picked yet" })
                    Column(Modifier.selectableGroup()) {
                        Computers.STRATEGIES.forEach { (value, label) ->
                            Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).testTag("strategy:${p.consumer}:$value")
                                .selectable(strategy == value, enabled = !busy, role = Role.RadioButton) {
                                    if (value != strategy) place(p, value, if (value == "manual") p.computers else emptyList(), if (value == "manual") p.shared else emptyList())
                                }, verticalAlignment = Alignment.CenterVertically) {
                                RadioButton(strategy == value, null, enabled = !busy)
                                Text(label, Modifier.padding(start = 12.dp), style = MaterialTheme.typography.bodyLarge)
                            }
                        }
                    }
                    if (strategy == "manual") {
                        HorizontalDivider(Modifier.padding(vertical = 4.dp))
                        val choices = view.order + picked.filter { it !in view.order }
                        choices.forEach { id ->
                            val on = id in picked
                            Row(Modifier.fillMaxWidth().heightIn(min = 48.dp), verticalAlignment = Alignment.CenterVertically) {
                                Row(Modifier.weight(1f).heightIn(min = 48.dp).testTag("pick:${p.consumer}:$id")
                                    .toggleable(on, enabled = !busy, role = Role.Checkbox) { checked ->
                                        val next = if (checked) picked + id else picked - id
                                        place(p, "manual", next, shared.filter { it in next })
                                    }, verticalAlignment = Alignment.CenterVertically) {
                                    Checkbox(on, null, enabled = !busy)
                                    Text(view.computerName(id) + if (id in p.missing) " (missing)" else "", Modifier.padding(start = 8.dp), style = MaterialTheme.typography.bodyLarge)
                                }
                                if (on) Row(Modifier.heightIn(min = 48.dp).testTag("share:${p.consumer}:$id")
                                    .toggleable(id in shared, enabled = !busy, role = Role.Checkbox) { checked ->
                                        place(p, "manual", picked, if (checked) shared + id else shared - id)
                                    }, verticalAlignment = Alignment.CenterVertically) {
                                    Checkbox(id in shared, null, enabled = !busy)
                                    Text("Shared", style = MaterialTheme.typography.bodyMedium)
                                }
                            }
                        }
                        if (picked.isEmpty()) Note("Pick at least one computer. Nothing is saved until you do.")
                    }
                    if (p.missing.isNotEmpty()) Note("Missing: ${p.missing.joinToString(", ") { view.computerName(it) }}. No longer paired, " +
                        "so work can't run there. Uncheck it to save this placement again.")
                    if (p.notice.isNotBlank()) Note(p.notice)
                    refusals[p.consumer]?.let { text ->
                        Text(text, Modifier.testTag("placementRefusal:${p.consumer}").semantics { liveRegion = LiveRegionMode.Polite },
                            style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.error)
                    }
                }
            }
        }
    }
}

@Composable
internal fun ComputerRows(list: ComputerList, busy: Boolean, onLimit: (Int) -> Unit) {
    LazyColumn(Modifier.fillMaxSize().testTag("computers"), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item {
            Note("Room is how many new workers a computer can take now: its limit, lowered while CPU or memory is under " +
                "pressure, minus the workers running. A computer that just came back takes one at a time for its first " +
                "${list.rejoinSeconds} seconds.")
        }
        items(list.computers, key = { it.id }) { computer -> ComputerCard(computer, busy, onLimit) }
    }
}

@Composable
private fun ComputerCard(c: Computer, busy: Boolean, onLimit: (Int) -> Unit) {
    SettingsGroup(if (c.thisComputer) "${c.name} (this computer)" else c.name) {
        Column(Modifier.testTag("computer:${c.id}"), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Fact("State", Computers.stateText(c))
            Fact("Running workers", Computers.runningText(c))
            Fact("Worker limit", Computers.limitText(c))
            Fact("Room", Computers.roomText(c))
            if (c.online) {
                Fact("CPU pressure", Computers.pressureText(c.cpuPressure))
                Fact("Memory pressure", Computers.pressureText(c.memoryPressure))
            }
            if (c.thisComputer) LimitStepper(c, busy, onLimit)
            else Note("Change this computer's limit in Raticode on it.")
        }
    }
}

@Composable
private fun Fact(label: String, value: String) {
    Row(Modifier.fillMaxWidth().semantics(mergeDescendants = true) {}, horizontalArrangement = Arrangement.SpaceBetween) {
        Text(label, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value, style = MaterialTheme.typography.bodyMedium, textAlign = TextAlign.End)
    }
}

/** Settings › Computers › Worker limit on the computer: 0 picks a number from its cores and memory. */
@Composable
private fun LimitStepper(c: Computer, busy: Boolean, onLimit: (Int) -> Unit) {
    val saved = c.maxThreadsSetting ?: 0
    // Several quick taps become one change, sent when the taps stop.
    var pending by remember { mutableStateOf<Int?>(null) }
    LaunchedEffect(saved) { pending = null }
    LaunchedEffect(pending) {
        val chosen = pending ?: return@LaunchedEffect
        delay(600)
        if (chosen != saved) onLimit(chosen)
    }
    val shown = pending ?: saved
    val label = if (shown == 0) "Auto" else shown.toString()
    HorizontalDivider(Modifier.padding(vertical = 4.dp))
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Column(Modifier.weight(1f)) {
            Text("Worker limit", style = MaterialTheme.typography.bodyLarge)
            Text(if (shown == 0) "Auto picks ${c.defaultMaxThreads ?: "a number"} from this computer's cores and memory."
                else "How many Rem tasks, self-work, phone turns, organization workers, swarm turns and work from other desktops run at once.",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        FilledTonalIconButton(onClick = { pending = (shown - 1).coerceAtLeast(0) }, enabled = !busy && shown > 0,
            modifier = Modifier.size(48.dp).testTag("computerFewer").semantics { contentDescription = "Fewer workers" }) { Text("−", style = MaterialTheme.typography.titleLarge) }
        Text(label, Modifier.widthIn(min = 48.dp).testTag("computerLimit").semantics { contentDescription = "Worker limit $label" },
            style = MaterialTheme.typography.titleLarge.copy(fontFamily = FontFamily.Monospace), textAlign = TextAlign.Center)
        FilledTonalIconButton(onClick = { pending = (shown + 1).coerceAtMost(Computers.MAX_THREADS) }, enabled = !busy && shown < Computers.MAX_THREADS,
            modifier = Modifier.size(48.dp).testTag("computerMore").semantics { contentDescription = "More workers" }) { Text("+", style = MaterialTheme.typography.titleLarge) }
    }
}
