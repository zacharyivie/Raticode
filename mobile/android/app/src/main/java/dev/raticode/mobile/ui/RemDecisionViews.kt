package dev.raticode.mobile.ui

import androidx.activity.compose.BackHandler
import androidx.annotation.RequiresApi
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.live.*
import org.json.JSONObject

/*
 * Rem's decisions on the phone: the same list and changes as the desktop's Decisions panel (RemDecisions.jsx), in
 * three parts. Waiting holds questions for you, each with Rem's one recommended answer and what it checked; Decided
 * holds the answers Rem keeps using, split into how Rem works here and the design decisions Raticode keeps as Markdown
 * files in a project's repository; History holds replaced and turned-down ones. A design decision opens on its own page
 * with its scope, folded sections, Edit and Replace with…. The desktop applies every change.
 */

/** The button that opens the Decisions screen from Rem's header, with how many wait for you. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun RemDecisionsButton(waiting: Int, onOpen: () -> Unit) {
    val label = if (waiting > 0) "Decisions, $waiting waiting for you" else "Decisions"
    IconButton(onClick = onOpen, modifier = Modifier.testTag("remDecisions").semantics { contentDescription = label }) {
        BadgedBox(badge = { if (waiting > 0) Badge { Text(if (waiting > 9) "9+" else "$waiting") } }) {
            Icon(ThreadIcons.Scale, null)
        }
    }
}

@RequiresApi(29)
@Composable
internal fun RemDecisionScreen(client: LiveClient, focus: String? = null, onFocused: () -> Unit = {}, onTask: (String) -> Unit = {}, onBack: () -> Unit) {
    val view = client.desktopViews[RemDecisions.READ] as? RemDecisionList
    val busy = RemDecisions.READ in client.desktopBusy || !client.connected || !client.desktopControl
    val error = client.desktopErrors[RemDecisions.READ]
    val groups = remember(view) { view?.let(RemDecisions::grouped).orEmpty() }
    val waiting = groups[RemDecisions.Section.WAITING].orEmpty().size + view?.edits.orEmpty().size
    // Opens on Waiting when something waits, otherwise on what Rem keeps using.
    var section by rememberSaveable { mutableStateOf<String?>(null) }
    // The decision a tapped "Rem has a question" alert opened: its section, first in the list and framed.
    var focused by rememberSaveable { mutableStateOf<String?>(null) }
    LaunchedEffect(focus, view) {
        if (focus == null || view == null) return@LaunchedEffect
        RemDecisions.sectionOf(view, focus)?.let { section = it.name }
        focused = focus
        onFocused()
    }
    val shownSection = section?.let { RemDecisions.Section.valueOf(it) }
        ?: if (view != null && waiting == 0) RemDecisions.Section.DECIDED else RemDecisions.Section.WAITING
    // A design decision's own page, while it is still listed.
    var openRecord by rememberSaveable { mutableStateOf<String?>(null) }
    val record = openRecord?.let { id -> view?.decisions?.firstOrNull { it.id == id && it.isRecord } }
    BackHandler(enabled = record != null) { openRecord = null }
    if (record != null) {
        Header("Design decision", listOf(record.project, record.id).filter { it.isNotBlank() }.joinToString(" · "), { openRecord = null })
        DesktopGate(client, RemDecisions.READ, "Loading Rem's decisions…") {
            val shown = view ?: return@DesktopGate
            RecordPage(record, shown.revision, busy, error, onChange = { client.desktopAction(RemDecisions.CHANGE, it) }) { action, decision, text ->
                RemDecisions.changeParams(action, decision.id, shown.revision, answer = text, reason = text)
                    ?.let { client.desktopAction(RemDecisions.CHANGE, it) }
                if (action == "forget" || action == "retire") openRecord = null
            }
        }
        return
    }
    Header("Rem's decisions", if (waiting > 0) "$waiting waiting for you" else "", onBack)
    DesktopGate(client, RemDecisions.READ, "Loading Rem's decisions…") {
        val shown = view ?: return@DesktopGate
        val send: (String, RemDecision, String?) -> Unit = { action, decision, text ->
            RemDecisions.changeParams(action, decision.id, shown.revision, answer = text, reason = text)
                ?.let { client.desktopAction(RemDecisions.CHANGE, it) }
        }
        val open: (RemDecision) -> Unit = { openRecord = it.id }
        Column(Modifier.fillMaxSize()) {
            SingleChoiceSegmentedButtonRow(Modifier.padding(horizontal = 16.dp, vertical = 4.dp).fillMaxWidth().testTag("remDecisionSections")) {
                RemDecisions.Section.entries.forEachIndexed { index, part ->
                    val count = groups[part].orEmpty().size + if (part == RemDecisions.Section.WAITING) shown.edits.size else 0
                    SegmentedButton(shownSection == part, { section = part.name }, SegmentedButtonDefaults.itemShape(index, RemDecisions.Section.entries.size),
                        Modifier.semantics { contentDescription = "${part.label}, $count" }) {
                        Text(if (count > 0) "${part.label} $count" else part.label, maxLines = 1)
                    }
                }
            }
            val items = groups[shownSection].orEmpty()
            LazyColumn(Modifier.fillMaxSize().testTag("remDecisionList"), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                if (error != null) item {
                    Text(error, Modifier.semantics { liveRegion = LiveRegionMode.Polite }, style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error)
                }
                item {
                    Note(when (shownSection) {
                        RemDecisions.Section.WAITING -> "Questions Rem or a worker brought you, each with one recommended answer and what Rem checked. Your answer is kept, so Rem doesn't ask again."
                        RemDecisions.Section.DECIDED -> "The answers Rem uses instead of asking. Design decisions are also kept as files in the project, and workers keep to them. Ask again when one no longer fits."
                        RemDecisions.Section.HISTORY -> "Decisions that were replaced or turned down."
                    }, Modifier.padding(bottom = 2.dp))
                }
                // Changes asked for in chat wait here too, above the questions.
                if (shownSection == RemDecisions.Section.WAITING) items(shown.edits, key = { "edit-" + it.id }) { edit ->
                    RemDecisionEditCard(client, null, edit)
                }
                val queued = RemDecisions.queuedLine(shown)
                if (items.isEmpty() && (shownSection != RemDecisions.Section.WAITING || (shown.edits.isEmpty() && queued.isEmpty()))) item {
                    Note(when (shownSection) {
                        RemDecisions.Section.WAITING -> "Nothing is waiting for you."
                        RemDecisions.Section.DECIDED -> "Nothing decided yet."
                        RemDecisions.Section.HISTORY -> "No earlier decisions."
                    })
                }
                if (shownSection == RemDecisions.Section.DECIDED) {
                    val (facts, records) = RemDecisions.decidedParts(items)
                    if (records.isNotEmpty() && facts.isNotEmpty()) item(key = "facts") { GroupHeading("How Rem works here") }
                    items(RemDecisions.focusedFirst(facts, focused), key = { it.id }) { decision ->
                        Focused(decision.id == focused) { DecidedDecision(decision, busy, send) }
                    }
                    if (records.isNotEmpty()) item(key = "records") { GroupHeading("Design decisions") }
                    items(RemDecisions.focusedFirst(records, focused), key = { it.id }) { decision ->
                        Focused(decision.id == focused) { RecordSummary(decision, open) }
                    }
                } else items(RemDecisions.focusedFirst(if (shownSection == RemDecisions.Section.WAITING) RemDecisions.looseWaiting(shown, items) else items,
                    focused), key = { it.id }) { decision ->
                    Focused(decision.id == focused) {
                        when (shownSection) {
                            RemDecisions.Section.WAITING -> WaitingDecision(decision, busy, send, open, onTask)
                            else -> ClosedDecision(decision, busy, send, open)
                        }
                    }
                }
                // Plan with me: a plan's questions together under its title, with how many are answered.
                if (shownSection == RemDecisions.Section.WAITING) RemDecisions.planGroups(shown).forEach { group ->
                    item(key = "plan-" + group.plan.id) { PlanHeading(group.plan) }
                    items(group.waiting, key = { it.id }) { decision -> WaitingDecision(decision, busy, send, open, onTask) }
                    RemDecisions.planQueuedLine(group.plan).takeIf { it.isNotEmpty() }?.let { line ->
                        item(key = "plan-queued-" + group.plan.id) { Note(line, Modifier.testTag("remDecisionPlanQueued")) }
                    }
                }
                // Questions past the three waiting cards: counted here, each gets its card when one above is settled.
                if (shownSection == RemDecisions.Section.WAITING && queued.isNotEmpty()) item(key = "queued") {
                    Note(queued, Modifier.testTag("remDecisionQueued"))
                }
                if (shown.more > 0) item { Note("${shown.more} older ${if (shown.more == 1) "entry is" else "entries are"} only on your desktop.") }
            }
        }
    }
}

/** The decision a tapped alert opened, framed in the accent color so it stands out from the rest. */
@Composable
private fun Focused(on: Boolean, content: @Composable () -> Unit) {
    if (!on) return content()
    Box(Modifier.fillMaxWidth().border(BorderStroke(2.dp, MaterialTheme.colorScheme.primary), MaterialTheme.shapes.medium)
        .testTag("remDecisionFocused")) { content() }
}

/** A plan's title above its questions, with how many of them are answered and its project. */
@Composable
private fun PlanHeading(plan: RemDecisionPlan) {
    Column(Modifier.fillMaxWidth().padding(top = 6.dp).testTag("remDecisionPlan").semantics(mergeDescendants = true) { heading() },
        verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text("Plan · ${plan.title}", style = MaterialTheme.typography.titleSmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
        Text(listOf(RemDecisions.planLine(plan), plan.project).filter { it.isNotBlank() }.joinToString(" · "),
            Modifier.testTag("remDecisionPlanLine"), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/** The tag row above a decision: its project (or About you) and its ID. */
@Composable
private fun DecisionTags(decision: RemDecision) {
    Text(listOf(decision.project.ifBlank { "About you" }, decision.id).joinToString(" · "), style = MaterialTheme.typography.labelSmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
}

@Composable
private fun DecisionCardFrame(tag: String, content: @Composable ColumnScope.() -> Unit) {
    Surface(shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.surface,
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant), modifier = Modifier.fillMaxWidth().testTag(tag)) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp), content = content)
    }
}

/** Rem's recommended answer, why, and the evidence it checked, on one card or in the chat. */
@Composable
private fun Recommendation(recommended: String, why: String, evidence: List<RemDecisionEvidence>, tried: String, provisional: Boolean) {
    if (recommended.isNotBlank()) {
        Text(if (provisional) "Rem chose" else "Rem recommends", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary)
        Text(recommended, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium)
        if (why.isNotBlank()) Text(why, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    } else if (tried.isNotBlank()) {
        Text("Rem tried: $tried", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
    val line = RemDecisions.evidenceLine(evidence)
    if (line.isNotBlank()) Text("Evidence: $line", Modifier.testTag("remDecisionEvidence"), style = MaterialTheme.typography.labelSmall,
        color = LocalRemColors.current.muted, maxLines = 2, overflow = TextOverflow.Ellipsis)
}

/** One text field for the user's own answer (or, when not [required], an optional note), with [saveLabel] and Cancel. */
@Composable
private fun AnswerField(initial: String, label: String, busy: Boolean, onSave: (String) -> Unit, required: Boolean = true, saveLabel: String = "Save",
                        onCancel: () -> Unit) {
    var text by rememberSaveable { mutableStateOf(initial) }
    OutlinedTextField(text, { if (it.length <= RemDecisions.ANSWER_MAX) text = it }, Modifier.fillMaxWidth().testTag("remDecisionAnswer"),
        label = { Text(label) }, keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences))
    Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        Button(onClick = { onSave(text.trim()) }, enabled = !busy && (!required || text.isNotBlank())) { Text(saveLabel) }
        TextButton(onClick = onCancel) { Text("Cancel") }
    }
}

@Composable
private fun WaitingDecision(decision: RemDecision, busy: Boolean, send: (String, RemDecision, String?) -> Unit, onOpen: (RemDecision) -> Unit,
                            onTask: (String) -> Unit = {}) {
    var changing by rememberSaveable(decision.id) { mutableStateOf(false) }
    var declining by rememberSaveable(decision.id) { mutableStateOf(false) }
    val provisional = decision.status == "provisional"
    DecisionCardFrame("remDecisionWaiting") {
        DecisionTags(decision)
        Text(decision.question, style = MaterialTheme.typography.titleSmall)
        AskedLine(decision, onTask)
        if (provisional) Text("Self-work's auto mode is on, so Rem applied its own recommendation. It stays in force until you keep or change it.",
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        RemDecisions.reopenedLine(decision).takeIf { it.isNotBlank() }?.let {
            Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        if (decision.askedAgain && decision.currentAnswer.isNotBlank())
            Text("Current answer: ${decision.currentAnswer}", Modifier.testTag("remDecisionCurrent"), style = MaterialTheme.typography.bodyMedium)
        if (decision.supersedes.isNotBlank()) Text("Your decision ${decision.supersedes} stays in force until you choose here.",
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        ConflictNote(decision.supersedesAll, decision.conflict)
        Recommendation(RemDecisions.answer(decision), decision.why, decision.evidence, decision.tried, provisional)
        decision.record?.let { record ->
            ScopeChips(record, decision.pathCounts, max = 3)
            TextButton(onClick = { onOpen(decision) }, modifier = Modifier.testTag("remDecisionOpenRecord")) { Text("Read the design decision") }
        }
        decision.alternatives.forEach {
            Text("Not chosen: ${it.answer}" + if (it.whyNot.isNotBlank()) " (${it.whyNot})" else "", style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        when {
            changing -> AnswerField("", "Your answer", busy, onSave = { send("change", decision, it); changing = false }) { changing = false }
            declining -> AnswerField("", "Why not? (optional)", busy, onSave = { send("reject", decision, it); declining = false },
                required = false, saveLabel = "Turn down") { declining = false }
            // Turning an Ask again question down keeps the current answer, so it says that and asks no reason.
            decision.askedAgain -> DecisionButtons(decision.answer.isNotBlank() || decision.recommended.isNotBlank(), provisional, busy,
                onUse = { send("use", decision, null) }, onChange = { changing = true }, onDecline = { send("reject", decision, null) },
                declineLabel = "Keep current answer")
            else -> DecisionButtons(decision.answer.isNotBlank() || decision.recommended.isNotBlank(), provisional, busy,
                onUse = { send("use", decision, null) }, onChange = { changing = true }, onDecline = { declining = true })
        }
    }
}

/** Who asked a waiting question, when, and what waits on it; tapping it opens the task when one asked or waits. */
@Composable
private fun AskedLine(decision: RemDecision, onTask: (String) -> Unit) {
    val line = RemDecisions.askedLine(decision).ifEmpty { return }
    val asked = decision.asked ?: return
    val color = if (asked.waiting.isNotEmpty()) MaterialTheme.colorScheme.tertiary else MaterialTheme.colorScheme.onSurfaceVariant
    val tap = if (asked.opens.isBlank()) Modifier else Modifier.clip(RoundedCornerShape(4.dp))
        .clickable(onClickLabel = "Open the task", role = Role.Button) { onTask(asked.opens) }
    Text(line, tap.testTag("remDecisionAsked"), style = MaterialTheme.typography.labelSmall, color = color)
}

/** Use this (Keep for Rem's own answer), Change and Turn down; Use this only when there is an answer to take. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun DecisionButtons(canUse: Boolean, provisional: Boolean, busy: Boolean, onUse: () -> Unit, onChange: () -> Unit, onDecline: (() -> Unit)?,
                            declineLabel: String = "Turn down") {
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        if (canUse) Button(onClick = onUse, enabled = !busy, modifier = Modifier.testTag("remDecisionUse").semantics {
            contentDescription = if (provisional) "Keep Rem's answer as your decision" else "Take Rem's recommendation"
        }) { Text(if (provisional) "Keep" else "Use this") }
        OutlinedButton(onClick = onChange, enabled = !busy, modifier = Modifier.testTag("remDecisionChange")) { Text("Change") }
        if (onDecline != null) TextButton(onClick = onDecline, enabled = !busy, modifier = Modifier.testTag("remDecisionDecline")) { Text(declineLabel) }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun DecidedDecision(decision: RemDecision, busy: Boolean, send: (String, RemDecision, String?) -> Unit) {
    var editing by rememberSaveable(decision.id) { mutableStateOf(false) }
    var forgetting by rememberSaveable(decision.id) { mutableStateOf(false) }
    DecisionCardFrame("remDecisionDecided") {
        Text(decision.question, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        if (editing) AnswerField(RemDecisions.answer(decision), "Answer", busy,
            onSave = { if (it != decision.answer) send("edit", decision, it); editing = false }) { editing = false }
        else Text(RemDecisions.answer(decision), style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium)
        if (decision.why.isNotBlank()) Text(decision.why, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(RemDecisions.decidedLine(decision), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        RemDecisions.checkLine(decision).takeIf { it.isNotBlank() }?.let {
            Text(it, Modifier.testTag("remDecisionCheck"), style = MaterialTheme.typography.labelSmall,
                color = if (decision.lastCheck?.passed == false) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant)
        }
        val line = RemDecisions.evidenceLine(decision.evidence)
        if (line.isNotBlank()) Text("Evidence: $line", style = MaterialTheme.typography.labelSmall, color = LocalRemColors.current.muted,
            maxLines = 2, overflow = TextOverflow.Ellipsis)
        HistoryFold(decision)
        if (!editing) FlowRow(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            TextButton(onClick = { editing = true }, enabled = !busy) { Text("Edit answer") }
            TextButton(onClick = { send("reopen", decision, null) }, enabled = !busy,
                modifier = Modifier.semantics { contentDescription = RemDecisions.askAgainDescription(decision.topic) }) { Text("Ask again") }
            // Reads the project's files again; anything new waits for you and this answer stays.
            if (decision.project.isNotBlank()) TextButton(onClick = { send("scan", decision, null) }, enabled = !busy,
                modifier = Modifier.testTag("remDecisionScan").semantics {
                    contentDescription = "Look again in ${decision.project}'s files for its check command and landing branch"
                }) { Text("Look again") }
            TextButton(onClick = { forgetting = true }, enabled = !busy,
                colors = ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.error)) { Text(RemDecisions.removalLabel(decision)) }
        }
    }
    if (forgetting) ForgetDialog(decision, onDismiss = { forgetting = false }) { send(RemDecisions.removalAction(decision), decision, null); forgetting = false }
}

@Composable
private fun ClosedDecision(decision: RemDecision, busy: Boolean, send: (String, RemDecision, String?) -> Unit, onOpen: (RemDecision) -> Unit) {
    var forgetting by rememberSaveable(decision.id) { mutableStateOf(false) }
    DecisionCardFrame("remDecisionClosed") {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) { DecisionTags(decision) }
            Text(RemDecisions.closedLabel(decision), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Text(decision.question, style = MaterialTheme.typography.bodyMedium)
        RemDecisions.answer(decision).takeIf { it.isNotBlank() }?.let {
            Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        decision.history.lastOrNull()?.takeIf { it.note.isNotBlank() && decision.status == "rejected" }?.let {
            Text("You said: ${it.note}", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            if (decision.isRecord) TextButton(onClick = { onOpen(decision) }) { Text("Open") }
            // A closed record whose file is in the repository stays: deleting it would let the file bring it back.
            if (!RemDecisions.written(decision)) TextButton(onClick = { forgetting = true }, enabled = !busy,
                colors = ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.error)) {
                Text("Forget")
            }
        }
    }
    if (forgetting) ForgetDialog(decision, onDismiss = { forgetting = false }) { send(RemDecisions.removalAction(decision), decision, null); forgetting = false }
}

@Composable
private fun ForgetDialog(decision: RemDecision, onDismiss: () -> Unit, onForget: () -> Unit) {
    val label = RemDecisions.removalLabel(decision)
    val text = if (RemDecisions.written(decision))
        "Workers and reviewers stop getting “${RemDecisions.answer(decision).take(200)}”. Its file ${decision.recordPath.take(200)} stays in the repository: Raticode rewrites it with status: rejected and commits that on the project's landing branch. The decision moves to History."
    else "Rem stops using “${RemDecisions.answer(decision).take(200)}” and may ask “${decision.question.take(200)}” again. This can't be undone."
    AlertDialog(onDismissRequest = onDismiss, title = { Text("$label this decision?") },
        text = { Text(text) },
        confirmButton = { TextButton(onClick = onForget, colors = ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.error)) { Text(label) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } })
}

/**
 * Under a task update that brings a question: Rem's recommendation with Use this and Change while it waits, or how it
 * was settled. A fallback card (Raticode's own, when Rem's request couldn't be used) shows what was found, with no buttons.
 */
@RequiresApi(29)
@Composable
internal fun RemDecisionChatCard(client: LiveClient, home: RemHome, card: RemDecisionCard) {
    val (status, revision) = RemDecisions.live(card, home, client.desktopViews[RemDecisions.READ] as? RemDecisionList)
    val state = RemDecisions.cardState(card, status)
    val busy = RemDecisions.READ in client.desktopBusy || !client.connected || !client.desktopControl
    var changing by rememberSaveable(card.id) { mutableStateOf(false) }
    Surface(shape = RoundedCornerShape(12.dp), color = MaterialTheme.colorScheme.surface, border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
        modifier = Modifier.widthIn(max = 520.dp).fillMaxWidth().testTag("remDecisionCard")) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            if (state is RemDecisions.CardState.Fallback) {
                Text(card.title.ifBlank { "Rem couldn't settle this. Here is what it found." }, style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.primary)
                Text(card.question, style = MaterialTheme.typography.titleSmall)
                if (card.found.isNotBlank()) Text("What it found: ${card.found}", style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                if (card.candidate.isNotBlank()) Text("A possible answer, not checked: ${card.candidate}", style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text("Answer in the message box.", style = MaterialTheme.typography.labelSmall, color = LocalRemColors.current.muted)
                return@Column
            }
            val open = state as? RemDecisions.CardState.Open
            Text(if (open?.provisional == true) "Rem decided this for now" else "Rem needs your decision", style = MaterialTheme.typography.labelMedium,
                color = if (open != null) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
            Text(card.question, style = MaterialTheme.typography.titleSmall)
            when (state) {
                is RemDecisions.CardState.Open -> {
                    ConflictNote(card.supersedesAll, card.conflict)
                    Recommendation(card.recommended, card.why, card.evidence, card.tried, state.provisional)
                    // Without the revision (an older desktop) a change can't be checked, so the list is the place for it.
                    if (revision != null) {
                        if (changing) AnswerField("", "Your answer", busy, onSave = { text ->
                            RemDecisions.changeParams("change", card.id, revision, answer = text)?.let { client.desktopAction(RemDecisions.CHANGE, it) }
                            changing = false
                        }) { changing = false }
                        else DecisionButtons(card.recommended.isNotBlank(), state.provisional, busy, onUse = {
                            RemDecisions.changeParams("use", card.id, revision)?.let { client.desktopAction(RemDecisions.CHANGE, it) }
                        }, onChange = { changing = true }, onDecline = null)
                    }
                }
                is RemDecisions.CardState.Decided -> Text(if (state.replaced) "Replaced by a later decision." else "Decided.",
                    Modifier.semantics { liveRegion = LiveRegionMode.Polite }, style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                RemDecisions.CardState.Queued -> Text("Queued behind the questions already waiting for you.",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                RemDecisions.CardState.TurnedDown -> Text("Turned down.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                RemDecisions.CardState.Withdrawn -> Text("You withdrew this question.", style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                else -> Text("No longer listed.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

/**
 * A change the user asked Rem for in chat: each part's text before and after, and OK and Cancel while it waits. The
 * desktop applies it only on OK, and only if the decision hasn't changed since; a settled card says what happened.
 */
@RequiresApi(29)
@Composable
internal fun RemDecisionEditCard(client: LiveClient, home: RemHome?, edit: RemDecisionEdit) {
    val list = client.desktopViews[RemDecisions.READ] as? RemDecisionList
    val busy = RemDecisions.READ in client.desktopBusy || !client.connected || !client.desktopControl
    val error = client.desktopErrors[RemDecisions.READ]
    val revision = list?.revision ?: home?.decisionsRevision ?: 0L
    Surface(shape = RoundedCornerShape(12.dp), color = MaterialTheme.colorScheme.surface,
        border = BorderStroke(1.dp, if (edit.waiting) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outlineVariant),
        modifier = Modifier.widthIn(max = 520.dp).fillMaxWidth().testTag("remDecisionEdit")) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(RemDecisions.editTitle(edit), Modifier.weight(1f), style = MaterialTheme.typography.labelMedium,
                    color = if (edit.waiting) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
                Text(edit.id, style = MaterialTheme.typography.labelSmall, color = LocalRemColors.current.muted)
            }
            if (edit.question.isNotBlank()) Text(listOf(edit.question, edit.project).filter { it.isNotBlank() }.joinToString(" · "),
                style = MaterialTheme.typography.titleSmall)
            edit.changes.forEach { change ->
                Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Text(change.label, style = MaterialTheme.typography.labelSmall, color = LocalRemColors.current.muted)
                    Text("Before: ${change.before.ifBlank { "(empty)" }}", Modifier.fillMaxWidth().clip(RoundedCornerShape(6.dp))
                        .background(MaterialTheme.colorScheme.surfaceVariant).padding(horizontal = 8.dp, vertical = 4.dp),
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text("After: ${change.after.ifBlank { "(empty)" }}", Modifier.fillMaxWidth().clip(RoundedCornerShape(6.dp))
                        .background(MaterialTheme.colorScheme.primaryContainer).padding(horizontal = 8.dp, vertical = 4.dp),
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onPrimaryContainer)
                }
            }
            if (edit.waiting) {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = { RemDecisions.editParams(edit.id, true, revision)?.let { client.desktopAction(RemDecisions.CHANGE, it) } },
                        enabled = !busy, modifier = Modifier.semantics { contentDescription = "OK, make this change to ${edit.decision}" }) { Text("OK") }
                    OutlinedButton(onClick = { RemDecisions.editParams(edit.id, false, revision)?.let { client.desktopAction(RemDecisions.CHANGE, it) } },
                        enabled = !busy, modifier = Modifier.semantics { contentDescription = "Cancel this change to ${edit.decision}" }) { Text("Cancel") }
                }
                if (error != null) Text(error, Modifier.semantics { liveRegion = LiveRegionMode.Polite }, style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error)
            } else Text(RemDecisions.editSettled(edit), Modifier.semantics { liveRegion = LiveRegionMode.Polite },
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/** A heading inside Decided: How Rem works here, or Design decisions. */
@Composable
private fun GroupHeading(text: String) {
    Text(text, Modifier.padding(top = 6.dp).semantics { heading() }, style = MaterialTheme.typography.titleSmall,
        color = MaterialTheme.colorScheme.primary)
}

/** A conflict's one fix: the decisions it replaces when you use it, and why they conflict. Nothing for an ordinary request. */
@Composable
private fun ConflictNote(replaces: List<String>, conflict: String) {
    if (replaces.isEmpty() && conflict.isBlank()) return
    Callout(conflict.ifBlank { "These decisions conflict." }, OrgTone.WAIT, Modifier.testTag("remDecisionConflict"),
        title = RemDecisions.replacesLine(replaces).ifBlank { "Decisions conflict" })
}

/**
 * One scope chip, cut with an ellipsis so a long path glob stays on one line. [count] is how many tracked files it matches
 * on the landing branch; a [warn]ing count (nothing to apply to, or files a Never path forbids) shows in the warning tone.
 */
@Composable
private fun ScopeChip(text: String, tone: OrgTone, count: String = "", warn: Boolean = false) {
    val (ink, fill) = orgColors(tone)
    val style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.SemiBold, fontFamily = FontFamily.Monospace)
    Row(Modifier.widthIn(max = 280.dp).clip(RoundedCornerShape(50)).background(fill).padding(horizontal = 8.dp, vertical = 2.dp),
        horizontalArrangement = Arrangement.spacedBy(5.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(text, Modifier.weight(1f, fill = false), color = ink, style = style, maxLines = 1, overflow = TextOverflow.Ellipsis)
        if (count.isNotBlank()) Text(count, color = if (warn) orgColors(OrgTone.WAIT).first else MaterialTheme.colorScheme.onSurfaceVariant,
            style = MaterialTheme.typography.labelSmall.copy(fontWeight = if (warn) FontWeight.Bold else FontWeight.Normal), maxLines = 1)
    }
}

/**
 * Where a design decision applies (its path globs) and what a change must never add, labelled "Never:", each with how many
 * tracked files it matches on the landing branch when the desktop counted them.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ScopeChips(record: RemDecisionRecord, counts: RemDecisionPathCounts?, max: Int = RemDecisions.LIST_ITEMS) {
    if (record.appliesTo.isEmpty() && record.forbids.isEmpty()) return
    val applies = record.appliesTo.take(max)
    val never = record.forbids.take(max)
    val more = record.appliesTo.size - applies.size + record.forbids.size - never.size
    fun spoken(glob: String, note: RemDecisions.CountNote) = if (note.text.isBlank()) glob else "$glob, ${note.text}"
    val appliesNotes = applies.map { it to RemDecisions.countNote(false, counts?.appliesTo?.get(it)) }
    val neverNotes = never.map { it to RemDecisions.countNote(true, counts?.forbids?.get(it)) }
    FlowRow(Modifier.testTag("remDecisionScope").semantics(mergeDescendants = true) {
        contentDescription = listOfNotNull(appliesNotes.takeIf { it.isNotEmpty() }?.joinToString(", ", "Applies to ") { (g, n) -> spoken(g, n) },
            neverNotes.takeIf { it.isNotEmpty() }?.joinToString(", ", "Never ") { (g, n) -> spoken(g, n) }, if (more > 0) "$more more" else null).joinToString(". ")
    }, horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        appliesNotes.forEach { (glob, note) -> ScopeChip(glob, OrgTone.ACCENT, note.text, note.warn) }
        neverNotes.forEach { (glob, note) -> ScopeChip("Never: $glob", OrgTone.BAD, note.text, note.warn) }
        if (more > 0) ScopeChip("+$more more", OrgTone.IDLE)
    }
}

/**
 * Whether the record's file is as approved: edited outside Raticode, changed by a Rem run and waiting to be put back, or not
 * written yet and why.
 */
@Composable
private fun RecordFileNotes(decision: RemDecision) {
    if (decision.editedOutside) Callout("Raticode reads your changes to the file and keeps this entry in step.", OrgTone.WAIT,
        Modifier.testTag("remDecisionEditedOutside"), icon = ThreadIcons.Info, title = "Edited outside Raticode")
    RemDecisions.fileNote(decision)?.let { (title, text) ->
        if (decision.recordRestore) Callout(text, OrgTone.WAIT, Modifier.testTag("remDecisionRestore"), title = title)
        else Callout(text, OrgTone.BAD, Modifier.testTag("remDecisionNotInRepo"), title = title)
    }
}

/** A decision's history, newest first: one row per change with its date, who (You, Rem or Raticode), what and the note. */
@Composable
private fun HistoryRows(rows: List<RemDecisions.HistoryRow>) {
    Column(Modifier.testTag("remDecisionHistory"), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        rows.forEach { row ->
            Row(Modifier.fillMaxWidth().semantics(mergeDescendants = true) {}, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(row.date, Modifier.width(78.dp), style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                Column(Modifier.weight(1f)) {
                    Text("${row.actor} ${row.action}".trim(), style = MaterialTheme.typography.bodySmall)
                    if (row.note.isNotBlank()) Text(row.note, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
    }
}

/** History (n) on a decided card: folded until tapped, like the desktop's fold. */
@Composable
private fun HistoryFold(decision: RemDecision) {
    val rows = remember(decision.history) { RemDecisions.historyRows(decision) }
    if (rows.isEmpty()) return
    var open by rememberSaveable(decision.id) { mutableStateOf(false) }
    TextButton(onClick = { open = !open }, contentPadding = PaddingValues(horizontal = 0.dp), modifier = Modifier.testTag("remDecisionHistoryToggle").semantics {
        contentDescription = if (open) "Hide the history of ${decision.id}" else "Show the history of ${decision.id}, ${rows.size} entries"
    }) { Text(if (open) "Hide history" else "History (${rows.size})", style = MaterialTheme.typography.labelMedium) }
    if (open) HistoryRows(rows)
}

/** A design decision in Decided: the decision sentence, its scope and file at a glance, and Open for the rest. */
@Composable
private fun RecordSummary(decision: RemDecision, onOpen: (RemDecision) -> Unit) {
    DecisionCardFrame("remDecisionRecord") {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Column(Modifier.weight(1f)) { DecisionTags(decision) }
            Pill(if (decision.status == "provisional") "Rem's, for now" else "In force", if (decision.status == "provisional") OrgTone.WAIT else OrgTone.DONE)
        }
        Text(RemDecisions.answer(decision), style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium)
        decision.record?.let { ScopeChips(it, decision.pathCounts, max = 3) }
        if (decision.editedOutside) Text("Edited outside Raticode", style = MaterialTheme.typography.labelSmall, color = orgColors(OrgTone.WAIT).first)
        if (decision.recordRestore) Text("File differs from the approved text", Modifier.testTag("remDecisionRestore"),
            style = MaterialTheme.typography.labelSmall, color = orgColors(OrgTone.WAIT).first, maxLines = 1, overflow = TextOverflow.Ellipsis)
        if (decision.recordPath.isBlank()) Text("Not in the repo yet" + decision.recordError.takeIf { it.isNotBlank() }?.let { ": $it" }.orEmpty(),
            style = MaterialTheme.typography.labelSmall, color = orgColors(OrgTone.BAD).first, maxLines = 2, overflow = TextOverflow.Ellipsis)
        else Text(decision.recordPath, style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
            color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
        TextButton(onClick = { onOpen(decision) }, modifier = Modifier.testTag("remDecisionOpenRecord").semantics {
            contentDescription = "Open the design decision ${decision.id}"
        }) { Text("Open") }
    }
}

/**
 * A design decision's page: the decision sentence first, then its scope, the replaced decisions of a conflict's fix,
 * whether its file is in the repository, and each section folded to its first sentence. Edit and Replace with… open a
 * form on the page while the decision is in force; [onChange] sends what they make.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun RecordPage(decision: RemDecision, revision: Long, busy: Boolean, error: String?, onChange: (JSONObject) -> Unit,
                       send: (String, RemDecision, String?) -> Unit) {
    var mode by rememberSaveable(decision.id) { mutableStateOf<String?>(null) }
    var forgetting by rememberSaveable(decision.id) { mutableStateOf(false) }
    val record = decision.record ?: RemDecisionRecord()
    LazyColumn(Modifier.fillMaxSize().testTag("remDecisionRecordPage"), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        if (error != null) item {
            Text(error, Modifier.semantics { liveRegion = LiveRegionMode.Polite }, style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error)
        }
        item {
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(RemDecisions.answer(decision), Modifier.testTag("remDecisionSentence"), style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold)
                if (decision.question.isNotBlank()) Text(decision.question, style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                if (decision.why.isNotBlank()) Text(decision.why, style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                val closed = RemDecisions.closedLabel(decision)
                Text(closed.ifBlank { RemDecisions.decidedLine(decision) }, style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                ScopeChips(record, decision.pathCounts)
            }
        }
        if (decision.supersedesAll.isNotEmpty() || decision.conflict.isNotBlank()) item { ConflictNote(decision.supersedesAll, decision.conflict) }
        item {
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                RecordFileNotes(decision)
                if (decision.recordPath.isNotBlank()) Text("File: ${decision.recordPath}", Modifier.testTag("remDecisionFile"),
                    style = MaterialTheme.typography.labelMedium.copy(fontFamily = FontFamily.Monospace), color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        val sections = listOf("Context" to record.context, "Options we didn't choose" to record.options, "Consequences" to record.consequences,
            "How we check it" to record.check).filter { it.second.isNotBlank() }
        items(sections, key = { it.first }) { (title, text) ->
            DetailCard(title) { SummaryText(text, tag = "remDecisionSection") }
        }
        if (record.signals.isNotEmpty()) item {
            InfoChip("Words that flag a contradiction", record.signals.joinToString(", "), tag = "remDecisionSignals")
        }
        val evidence = RemDecisions.evidenceLine(decision.evidence, max = 5)
        val check = RemDecisions.checkLine(decision)
        if (evidence.isNotBlank() || check.isNotBlank()) item {
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                if (evidence.isNotBlank()) Text("Evidence: $evidence", Modifier.testTag("remDecisionEvidence"), style = MaterialTheme.typography.labelSmall,
                    color = LocalRemColors.current.muted)
                if (check.isNotBlank()) Text(check, Modifier.testTag("remDecisionCheck"), style = MaterialTheme.typography.labelSmall,
                    color = if (decision.lastCheck?.passed == false) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        val history = RemDecisions.historyRows(decision)
        if (history.isNotEmpty()) item(key = "history") {
            DetailCard("History") { HistoryRows(history) }
        }
        if (decision.status in RemDecisions.IN_FORCE) item {
            when (val shownMode = mode) {
                null -> FlowRow(horizontalArrangement = Arrangement.spacedBy(4.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    OutlinedButton(onClick = { mode = "edit" }, enabled = !busy, modifier = Modifier.testTag("remDecisionEdit")) { Text("Edit") }
                    OutlinedButton(onClick = { mode = "replace" }, enabled = !busy, modifier = Modifier.testTag("remDecisionReplace").semantics {
                        contentDescription = "Replace with a new decision"
                    }) { Text("Replace with…") }
                    TextButton(onClick = { send("reopen", decision, null) }, enabled = !busy,
                        modifier = Modifier.semantics { contentDescription = RemDecisions.askAgainDescription(decision.topic) }) { Text("Ask again") }
                    TextButton(onClick = { forgetting = true }, enabled = !busy,
                        colors = ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.error)) { Text(RemDecisions.removalLabel(decision)) }
                }
                else -> RecordForm(shownMode, decision, revision, busy, onSave = { onChange(it); mode = null }) { mode = null }
            }
        }
    }
    if (forgetting) ForgetDialog(decision, onDismiss = { forgetting = false }) { send(RemDecisions.removalAction(decision), decision, null); forgetting = false }
}

/**
 * Edit (every part of the record, the lists as comma-separated text) or Replace with… (a new decision in place of this
 * one; its parts start from this one's). Save stays off until there is something the desktop would take.
 */
@Composable
private fun RecordForm(mode: String, decision: RemDecision, revision: Long, busy: Boolean, onSave: (JSONObject) -> Unit, onCancel: () -> Unit) {
    val start = remember(decision, mode) { RemDecisions.draft(decision, if (mode == "replace") "" else RemDecisions.answer(decision)) }
    val key = "${decision.id}-$mode"
    var answer by rememberSaveable(key) { mutableStateOf(start.answer) }
    var why by rememberSaveable(key) { mutableStateOf(if (mode == "replace") "" else start.why) }
    var context by rememberSaveable(key) { mutableStateOf(start.context) }
    var options by rememberSaveable(key) { mutableStateOf(start.options) }
    var consequences by rememberSaveable(key) { mutableStateOf(start.consequences) }
    var check by rememberSaveable(key) { mutableStateOf(start.check) }
    var applies by rememberSaveable(key) { mutableStateOf(start.appliesTo) }
    var forbids by rememberSaveable(key) { mutableStateOf(start.forbids) }
    var note by rememberSaveable(key) { mutableStateOf("") }
    val draft = RemDecisionDraft(answer, why, context, options, consequences, check, applies, forbids)
    val params = RemDecisions.recordChangeParams(mode, decision, revision, draft, note)
    DetailCard(if (mode == "replace") "Replace ${decision.id} with a new decision" else "Edit ${decision.id}", Modifier.testTag("remDecisionRecordForm")) {
        RecordField(if (mode == "replace") "The new decision" else "Decision", answer, RemDecisions.ANSWER_MAX, "remDecisionFieldAnswer") { answer = it }
        RecordField("Why", why, RemDecisions.WHY_MAX, "remDecisionFieldWhy") { why = it }
        RecordField("Context", context, RemDecisions.SECTION_MAX, "remDecisionFieldContext") { context = it }
        RecordField("Options we didn't choose", options, RemDecisions.SECTION_MAX, "remDecisionFieldOptions") { options = it }
        RecordField("Consequences", consequences, RemDecisions.SECTION_MAX, "remDecisionFieldConsequences") { consequences = it }
        RecordField("How we check it", check, RemDecisions.CHECK_MAX, "remDecisionFieldCheck") { check = it }
        RecordField("Applies to (paths, comma-separated)", applies, 2200, "remDecisionFieldApplies", single = true,
            problem = RemDecisions.listProblem(applies)) { applies = it }
        RecordField("Never (paths, comma-separated)", forbids, 2200, "remDecisionFieldForbids", single = true,
            problem = RemDecisions.listProblem(forbids)) { forbids = it }
        RemDecisions.pathWarnings(decision.pathCounts, applies, forbids).takeIf { it.isNotEmpty() }?.let { warnings ->
            Text(warnings.joinToString("\n"), Modifier.testTag("remDecisionPathWarnings"), style = MaterialTheme.typography.bodySmall,
                color = orgColors(OrgTone.WAIT).first)
        }
        RecordField("What changed? (optional)", note, RemDecisions.NOTE_MAX, "remDecisionFieldNote") { note = it }
        Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            Button(onClick = { params?.let(onSave) }, enabled = !busy && params != null, modifier = Modifier.testTag("remDecisionSaveRecord")) {
                Text(if (mode == "replace") "Replace" else "Save")
            }
            TextButton(onClick = onCancel) { Text("Cancel") }
        }
    }
}

/** One field of a record's form; text past [max] characters isn't taken, and a [problem] shows under the field. */
@Composable
private fun RecordField(label: String, value: String, max: Int, tag: String, single: Boolean = false, problem: String? = null, onValue: (String) -> Unit) {
    OutlinedTextField(value, { if (it.length <= max) onValue(it) }, Modifier.fillMaxWidth().testTag(tag), label = { Text(label) },
        singleLine = single, isError = problem != null, supportingText = problem?.let { { Text(it) } },
        keyboardOptions = KeyboardOptions(capitalization = if (single) KeyboardCapitalization.None else KeyboardCapitalization.Sentences))
}
