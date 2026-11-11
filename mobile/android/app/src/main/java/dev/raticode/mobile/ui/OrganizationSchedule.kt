package dev.raticode.mobile.ui

import androidx.activity.compose.BackHandler
import androidx.annotation.RequiresApi
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.*
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.live.*
import java.time.ZoneId
import java.util.Locale

// The organization's work schedule: what's on now on the Organization tab, and the block list and editor as pages.

/** The Organization tab's schedule card: the block on now, the next one, warnings and who works under which block. */
@RequiresApi(29)
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun ScheduleCard(client: LiveClient, org: Organization, plan: OrgPlan, nav: OrgNav, busy: Boolean) {
    val schedule = plan.schedule
    if (schedule == null) { Note(WorkSchedule.UPDATE_DESKTOP, Modifier.testTag("orgScheduleUpdate")); return }
    val editable = client.organizationSchedule != false
    if (!schedule.configured) {
        OrgCard(null, Modifier.testTag("orgScheduleEmpty")) {
            Text("No work schedule yet. Workers use their usual priorities.", style = MaterialTheme.typography.bodyMedium)
            if (!editable) Note(WorkSchedule.UPDATE_DESKTOP)
            else FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                if (schedule.template.isNotEmpty()) FilledTonalButton(onClick = { nav.push("schedule:template") }, enabled = !busy,
                    modifier = Modifier.testTag("orgScheduleSuggested")) { Text("Use suggested schedule") }
                TextButton(onClick = { nav.push("schedule") }, enabled = !busy, modifier = Modifier.testTag("orgScheduleBuild")) { Text("Build your own") }
            }
        }
        return
    }
    val zone = schedule.timezone
    val now = schedule.current
    val run = orgColors(OrgTone.RUN).first
    OrgCard({ nav.push("schedule") }, Modifier.testTag("orgSchedule"), stripe = if (now != null) run else null) {
        if (now != null) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Pill("Now", OrgTone.RUN)
                Text(now.block.label, Modifier.weight(1f), style = MaterialTheme.typography.titleSmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
            if (now.block.focus.isNotBlank()) Text(now.block.focus, style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
            WorkSchedule.clock(now.endsAt, zone).takeIf { it.isNotBlank() }?.let {
                Text("until $it", style = MaterialTheme.typography.labelMedium.copy(fontFamily = FontFamily.Monospace), color = run)
            }
        } else Text("No block right now. Workers use their usual priorities.", style = MaterialTheme.typography.bodyMedium)
        val at = WorkSchedule.clock(schedule.changesAt, zone)
        when {
            at.isBlank() -> Unit
            schedule.next != null -> Text("Next: ${schedule.next.label} at $at", style = MaterialTheme.typography.bodySmall)
            now != null -> Text("No block from $at", style = MaterialTheme.typography.bodySmall)
        }
        schedule.issues.take(4).forEach { issue ->
            Text(issue.text, Modifier.semantics { contentDescription = "Warning: ${issue.text}" }, style = MaterialTheme.typography.bodySmall,
                color = orgColors(OrgTone.WAIT).first, maxLines = 2, overflow = TextOverflow.Ellipsis)
        }
        if (schedule.underway.isNotEmpty()) {
            Text("Working now", Modifier.padding(top = 4.dp), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            schedule.underway.take(4).forEach { u -> UnderwayRow(org, u) }
            if (schedule.underway.size > 4) Text("and ${schedule.underway.size - 4} more", style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Text("${schedule.blocks.size} ${if (schedule.blocks.size == 1) "block" else "blocks"} · times in $zone", style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        if (!editable) Note(WorkSchedule.UPDATE_DESKTOP)
    }
}

@Composable
private fun UnderwayRow(org: Organization, item: OrgUnderway) {
    val name = item.name.ifBlank { org.employees.firstOrNull { it.id == item.employeeId }?.name ?: "A worker" }
    val doing = if (item.checkIn) "Checking in" else item.title.ifBlank { "On a ticket" }
    val under = item.blockLabel.takeIf { it.isNotBlank() }?.let { "under $it" } ?: "outside any block"
    Row(Modifier.fillMaxWidth().semantics(mergeDescendants = true) {}, verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Avatar(name, item.employeeId, 22.dp)
        Column(Modifier.weight(1f)) {
            Text("$name · $doing", style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(under, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    }
}

/**
 * The block list. Changes stay on the phone until Save sends the whole schedule; the desktop checks it again and its
 * reason is shown when it refuses. [fromTemplate] starts from the suggested schedule, which is only saved by Save.
 */
@RequiresApi(29)
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun SchedulePage(client: LiveClient, org: Organization, plan: OrgPlan, fromTemplate: Boolean, nav: OrgNav, busy: Boolean) {
    val schedule = plan.schedule
    if (schedule == null) {
        Column(Modifier.fillMaxSize()) { DetailHeader("Work schedule", nav); Note(WorkSchedule.UPDATE_DESKTOP, Modifier.padding(20.dp)) }
        return
    }
    val key = "organizations.read:${org.id}"
    val readOnly = client.organizationSchedule == false || !client.connected
    var draftState by rememberSaveable { mutableStateOf(WorkSchedule.encode(if (fromTemplate) schedule.template else schedule.blocks)) }
    val draft = remember(draftState) { WorkSchedule.decode(draftState) }
    var zone by rememberSaveable { mutableStateOf(schedule.timezone) }
    // The block open in the editor: its place in the list, or -1 for a new one.
    var editing by rememberSaveable { mutableStateOf<Int?>(null) }
    var saving by rememberSaveable { mutableStateOf(false) }
    var savedFrom by rememberSaveable { mutableLongStateOf(0L) }
    var failure by rememberSaveable { mutableStateOf<String?>(null) }
    var discard by remember { mutableStateOf(false) }
    val dirty = fromTemplate || WorkSchedule.sendable(draft) != WorkSchedule.sendable(schedule.blocks) || zone.trim() != schedule.timezone
    val problem = WorkSchedule.problem(draft) ?: if (!WorkSchedule.validZone(zone)) "Use a time zone name such as America/New_York." else null
    val clash = remember(draftState) { WorkSchedule.clash(draft) }

    // The read after a save carries the new revision and the saved blocks; a refusal leaves its reason.
    val error = client.desktopErrors[key]
    LaunchedEffect(saving, plan.revision, error, client.requestError) {
        if (!saving) return@LaunchedEffect
        when {
            plan.revision != savedFrom && schedule.blocks.map { it.id } == draft.map { it.id } -> { saving = false; nav.pop() }
            error != null -> { saving = false; failure = error }
            client.requestError != null && key !in client.desktopBusy -> { saving = false; failure = client.requestError }
        }
    }

    val open = editing
    if (open != null) {
        val original = draft.getOrNull(open)
        BlockEditor(original ?: WorkSchedule.blank(), original == null, draft.filterIndexed { i, _ -> i != open }.map { it.id }, zone.trim(), nav,
            onCancel = { editing = null },
            onDelete = if (original == null) null else ({ draftState = WorkSchedule.encode(draft.filterIndexed { i, _ -> i != open }); editing = null }),
            onDone = { block ->
                draftState = WorkSchedule.encode(if (original == null) draft + block else draft.mapIndexed { i, b -> if (i == open) block else b })
                editing = null
            })
        return
    }
    BackHandler(enabled = dirty && !saving) { discard = true }
    Column(Modifier.fillMaxSize()) {
        DetailHeader("Work schedule", nav, "ORGANIZATION", onBack = { if (dirty && !saving) discard = true else nav.pop() }) {
            if (!readOnly) Button(enabled = dirty && problem == null && !busy && !saving, modifier = Modifier.testTag("orgScheduleSave"), onClick = {
                failure = null; client.dismissDesktopError(key); client.dismissRequestError()
                savedFrom = plan.revision; saving = true
                client.desktopAction(WorkSchedule.OP, WorkSchedule.params(org.id, plan.revision, draft, zone, schedule.timezone))
            }) { Text(if (saving) "Saving…" else "Save") }
        }
        LazyColumn(Modifier.fillMaxSize().testTag("orgSchedulePage"), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            if (fromTemplate) item { Note("The suggested schedule. Change anything you like; nothing applies until you save.") }
            if (readOnly) item { Note(if (client.organizationSchedule == false) WorkSchedule.UPDATE_DESKTOP else "Offline. Connect to your computer to change the schedule.") }
            item {
                OutlinedTextField(zone, { zone = it.take(80) }, Modifier.fillMaxWidth().testTag("orgScheduleZone"), label = { Text("Time zone") }, singleLine = true,
                    readOnly = readOnly, isError = !WorkSchedule.validZone(zone),
                    supportingText = { Text("Shared with working hours. A name such as America/New_York.") })
                val phone = ZoneId.systemDefault().id
                if (!readOnly && zone.trim() != phone) TextButton(onClick = { zone = phone }) { Text("Use this phone's time zone ($phone)") }
            }
            item { SectionTitle("Blocks · ${draft.size}") }
            if (draft.isEmpty()) item { Note("No blocks. Workers use their usual priorities. Saving an empty schedule turns it off.") }
            itemsIndexed(draft, key = { i, b -> "$i:${b.id}" }) { i, block -> BlockRow(block, if (readOnly) null else ({ editing = i })) }
            if (!readOnly) item {
                OutlinedButton(onClick = { editing = -1 }, enabled = draft.size < WorkSchedule.MAX_BLOCKS, modifier = Modifier.fillMaxWidth().testTag("orgScheduleAdd")) {
                    Icon(ThreadIcons.Plus, null, Modifier.size(16.dp)); Spacer(Modifier.width(6.dp)); Text("Add block")
                }
            }
            (failure ?: problem ?: clash?.let(WorkSchedule::clashText))?.let { text ->
                item { Text(text, Modifier.testTag("orgScheduleProblem").semantics { liveRegion = LiveRegionMode.Polite }, style = MaterialTheme.typography.bodySmall,
                    color = orgColors(if (failure != null || problem != null) OrgTone.BAD else OrgTone.WAIT).first) }
            }
            item {
                Note("When blocks overlap, the longer cadence wins (quarterly, then monthly, weekly, daily), then the shorter block. " +
                    "Workers favor tickets that match the block on now; a block change never stops running work.", Modifier.padding(top = 8.dp))
            }
        }
    }
    if (discard) AlertDialog(onDismissRequest = { discard = false }, title = { Text("Discard changes?") },
        text = { Text("Your changes to the work schedule haven't been saved.") },
        confirmButton = { TextButton(onClick = { discard = false; nav.pop() }, modifier = Modifier.testTag("orgScheduleDiscard")) { Text("Discard") } },
        dismissButton = { TextButton(onClick = { discard = false }) { Text("Keep editing") } })
}

@Composable
private fun BlockRow(block: OrgScheduleBlock, onEdit: (() -> Unit)?) {
    val problem = WorkSchedule.problem(block)
    OrgCard(onEdit, Modifier.testTag("orgScheduleBlock"), stripe = if (problem != null) orgColors(OrgTone.BAD).first else null) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(block.label.ifBlank { "Unnamed block" }, Modifier.weight(1f), style = MaterialTheme.typography.titleSmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
            Pill(WorkSchedule.CADENCES.firstOrNull { it.first == block.cadence }?.second ?: block.cadence, OrgTone.IDLE)
        }
        Text(WorkSchedule.describe(block), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        if (block.focus.isNotBlank()) Text(block.focus, style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
        if (problem != null) Text(problem, style = MaterialTheme.typography.bodySmall, color = orgColors(OrgTone.BAD).first)
    }
}

/** One block's fields. Done keeps the change in the list; only the list's Save sends it. */
@OptIn(ExperimentalLayoutApi::class, ExperimentalMaterial3Api::class)
@Composable
private fun BlockEditor(initial: OrgScheduleBlock, isNew: Boolean, otherIds: List<String>, zone: String, nav: OrgNav,
                        onCancel: () -> Unit, onDelete: (() -> Unit)?, onDone: (OrgScheduleBlock) -> Unit) {
    var formState by rememberSaveable { mutableStateOf(WorkSchedule.encode(listOf(initial))) }
    val form = remember(formState) { WorkSchedule.decode(formState).first() }
    fun update(change: (OrgScheduleBlock) -> OrgScheduleBlock) { formState = WorkSchedule.encode(listOf(change(form))) }
    var keywords by rememberSaveable { mutableStateOf(initial.keywords.joinToString(", ")) }
    var picking by rememberSaveable { mutableStateOf<String?>(null) }
    val result = WorkSchedule.finish(form, WorkSchedule.keywords(keywords), initial, isNew, otherIds)
    val problem = WorkSchedule.problem(result)
    BackHandler(onBack = onCancel)
    Column(Modifier.fillMaxSize()) {
        DetailHeader(if (isNew) "New block" else "Edit block", nav, "WORK SCHEDULE", onBack = onCancel) {
            Button(onClick = { onDone(result) }, enabled = form.label.isNotBlank(), modifier = Modifier.testTag("orgBlockDone")) { Text("Done") }
        }
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp).testTag("orgBlockEditor"), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            OutlinedTextField(form.label, { v -> update { it.copy(label = v.take(WorkSchedule.MAX_LABEL)) } }, Modifier.fillMaxWidth().testTag("orgBlockName"),
                label = { Text("Name") }, singleLine = true)
            OutlinedTextField(form.focus, { v -> update { it.copy(focus = v.take(WorkSchedule.MAX_FOCUS)) } }, Modifier.fillMaxWidth().testTag("orgBlockFocus"),
                label = { Text("Focus") }, minLines = 2, maxLines = 6,
                supportingText = { Text("What workers favor in this block · ${form.focus.length}/${WorkSchedule.MAX_FOCUS}") })

            Text("How often", Modifier.semantics { heading() }, style = MaterialTheme.typography.titleSmall)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                WorkSchedule.CADENCES.forEach { (value, label) ->
                    FilterChip(form.cadence == value, { update { WorkSchedule.withCadence(it, value) } }, label = { Text(label) },
                        modifier = Modifier.testTag("orgBlockCadence-$value"))
                }
            }
            when (form.cadence) {
                "weekly" -> {
                    Text("On", style = MaterialTheme.typography.labelLarge)
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        WorkSchedule.DAY_NAMES.forEachIndexed { day, short ->
                            FilterChip(day in form.days, { update { b -> b.copy(days = if (day in b.days) b.days - day else (b.days + day).sorted()) } },
                                label = { Text(short, Modifier.semantics { contentDescription = WorkSchedule.LONG_DAYS[day] }) },
                                modifier = Modifier.testTag("orgBlockDay-$day"))
                        }
                    }
                }
                "monthly", "quarterly" -> MonthRule(form, ::update)
            }
            if (form.cadence == "quarterly") {
                Text("Month of the quarter", style = MaterialTheme.typography.labelLarge)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    (1..3).forEach { n -> FilterChip(form.quarterMonth == n, { update { it.copy(quarterMonth = n) } }, label = {
                        Text(WorkSchedule.ordinal(n), Modifier.semantics { contentDescription = "${WorkSchedule.ordinal(n)} month of the quarter" }) },
                        modifier = Modifier.testTag("orgBlockQuarter-$n")) }
                }
                Note("That's ${QUARTER_MONTHS[form.quarterMonth - 1]}.")
            }

            Text("Time", Modifier.semantics { heading() }, style = MaterialTheme.typography.titleSmall)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                TimeField("Starts", form.start, Modifier.weight(1f).testTag("orgBlockStart"), { v -> update { it.copy(start = v) } }) { picking = "start" }
                TimeField("Ends", form.end, Modifier.weight(1f).testTag("orgBlockEnd"), { v -> update { it.copy(end = v) } }) { picking = "end" }
            }
            WorkSchedule.span(form.start, form.end).takeIf { it.isNotBlank() }?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = orgColors(OrgTone.ACCENT).first) }
            Note("24-hour times in the organization's time zone${if (zone.isNotBlank()) ", $zone" else ""}.")

            val words = WorkSchedule.keywords(keywords)
            OutlinedTextField(keywords, { keywords = it.take(600) }, Modifier.fillMaxWidth().testTag("orgBlockKeywords"), label = { Text("Keywords") },
                isError = words.size > WorkSchedule.MAX_KEYWORDS || words.any { it.length > WorkSchedule.MAX_KEYWORD },
                supportingText = { Text("Separate with commas, up to ${WorkSchedule.MAX_KEYWORDS}. Tickets that mention them come first in this block.") })

            if (problem != null && form.label.isNotBlank()) Text(problem, Modifier.semantics { liveRegion = LiveRegionMode.Polite },
                style = MaterialTheme.typography.bodySmall, color = orgColors(OrgTone.BAD).first)
            if (onDelete != null) TextButton(onClick = onDelete, modifier = Modifier.testTag("orgBlockDelete"),
                colors = ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.error)) { Text("Delete block") }
        }
    }
    picking?.let { which ->
        TimeDialog(if (which == "start") form.start else form.end, if (which == "start") "Starts" else "Ends", onDismiss = { picking = null }) { value ->
            update { if (which == "start") it.copy(start = value) else it.copy(end = value) }; picking = null
        }
    }
}

private val QUARTER_MONTHS = listOf("January, April, July and October", "February, May, August and November", "March, June, September and December")

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun MonthRule(form: OrgScheduleBlock, update: ((OrgScheduleBlock) -> OrgScheduleBlock) -> Unit) {
    val byDay = form.monthDay != null || form.nthWeek == null
    Text("On", style = MaterialTheme.typography.labelLarge)
    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        FilterChip(byDay, { update { it.copy(monthDay = it.monthDay ?: 1, nthWeek = null, weekday = null) } }, label = { Text("Day of the month") },
            modifier = Modifier.testTag("orgBlockByDay"))
        FilterChip(!byDay, { update { it.copy(monthDay = null, nthWeek = it.nthWeek ?: 1, weekday = it.weekday ?: 0) } }, label = { Text("A weekday") },
            modifier = Modifier.testTag("orgBlockByWeekday"))
    }
    if (byDay) {
        val day = form.monthDay ?: 1
        ChoiceMenu(if (day == 31) "Day 31 (the last day)" else "Day $day", "Day of the month, $day",
            (1..31).map { "$it" to if (it == 31) "31 (the last day)" else "$it" }, "$day", MENU.testTag("orgBlockMonthDay")) { v ->
            update { it.copy(monthDay = v.toInt()) }
        }
        Note("29, 30 and 31 fall on the last day of shorter months, so 31 always means the last day.")
    } else Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        Text("The", style = MaterialTheme.typography.bodyMedium)
        ChoiceMenu(WorkSchedule.ordinal(form.nthWeek), "Which week, ${WorkSchedule.ordinal(form.nthWeek)}",
            WorkSchedule.ORDINALS.map { "${it.first}" to it.second }, "${form.nthWeek}", MENU.testTag("orgBlockNth")) { v -> update { it.copy(nthWeek = v.toInt()) } }
        val weekday = form.weekday ?: 0
        ChoiceMenu(WorkSchedule.LONG_DAYS[weekday], "Weekday, ${WorkSchedule.LONG_DAYS[weekday]}",
            WorkSchedule.LONG_DAYS.mapIndexed { i, name -> "$i" to name }, "$weekday", MENU.testTag("orgBlockWeekday")) { v -> update { it.copy(weekday = v.toInt()) } }
    }
}

/** HH:MM typed or picked. Four digits become HH:MM as you type. */
@Composable
private fun TimeField(label: String, value: String, modifier: Modifier, onValue: (String) -> Unit, onPick: () -> Unit) {
    OutlinedTextField(value, { typed ->
        val kept = typed.filter { it.isDigit() || it == ':' }.take(5)
        onValue(if (kept.length == 4 && kept.all { it.isDigit() }) kept.take(2) + ":" + kept.drop(2) else kept)
    }, modifier, label = { Text(label) }, singleLine = true, placeholder = { Text("HH:MM") },
        isError = value.isNotEmpty() && !WorkSchedule.validClock(value), keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
        trailingIcon = { IconButton(onClick = onPick) { Icon(ThreadIcons.Recent, "Pick the time it ${label.lowercase(Locale.US)}") } })
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun TimeDialog(value: String, title: String, onDismiss: () -> Unit, onPick: (String) -> Unit) {
    val valid = WorkSchedule.validClock(value)
    val state = rememberTimePickerState(if (valid) value.take(2).toInt() else 9, if (valid) value.takeLast(2).toInt() else 0, is24Hour = true)
    AlertDialog(onDismissRequest = onDismiss, title = { Text(title) }, text = { TimePicker(state) },
        confirmButton = { TextButton(onClick = { onPick(String.format(Locale.US, "%02d:%02d", state.hour, state.minute)) }, modifier = Modifier.testTag("orgTimeOk")) { Text("OK") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } })
}
