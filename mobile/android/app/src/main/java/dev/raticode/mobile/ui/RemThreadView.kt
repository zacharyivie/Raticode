package dev.raticode.mobile.ui

import androidx.annotation.RequiresApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.scrollBy
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.live.*
import dev.raticode.mobile.live.RemThreadRows.Failure
import dev.raticode.mobile.live.RemThreadRows.Instruction
import dev.raticode.mobile.live.RemThreadRows.Reply
import dev.raticode.mobile.live.RemThreadRows.Thoughts
import org.json.JSONObject

/**
 * A task's work thread, laid out like the desktop's thread pane: Rem's instructions as
 * bubbles, the worker's replies in full, and each run of thoughts folded into one group.
 */
@RequiresApi(29)
@Composable
internal fun ColumnScope.RemThreadView(client: LiveClient, id: String, onBack: () -> Unit) {
    val key = "rem.thread:$id"
    val thread = client.desktopViews[key] as? RemThread
    val rows = remember(thread) { thread?.let(RemThreadRows::build).orEmpty() }
    Header(thread?.title ?: "Work thread", when {
        thread == null -> ""
        thread.running -> "Working on your desktop"
        else -> "Work thread"
    }, onBack) {
        if (thread?.running == true) StatusPill("Working", RemTaskStatus.Tone.ACTIVE, Modifier.padding(end = 8.dp).testTag("remThreadWorking"),
            icon = ThreadIcons.Running, spinning = true)
    }
    val error = client.desktopErrors[key]
    Box(Modifier.weight(1f)) {
        // A deleted or cleared thread: say so and offer the way back, not just the desktop's error.
        if (thread == null && error != null && client.connected && client.desktopControl) Column(Modifier.padding(20.dp).testTag("remThreadUnavailable"),
            verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("This work thread isn't available.", style = MaterialTheme.typography.titleSmall)
            Note(error)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = onBack) { Text("Back") }
                TextButton(onClick = client::refreshDesktop) { Text("Try again") }
            }
        } else DesktopGate(client, key, "Loading this thread…") {
            if (thread != null) {
                val list = rememberLazyListState()
                val leading = if (thread.earlier > 0 || rows.isEmpty()) 1 else 0
                // Open at the newest entry and follow new work, until the reader scrolls away from the end.
                var following by rememberSaveable(id) { mutableStateOf(true) }
                val moving = remember { booleanArrayOf(false) }
                LaunchedEffect(list) {
                    snapshotFlow { list.isScrollInProgress to list.canScrollForward }
                        .collect { (scrolling, more) -> if (scrolling && !moving[0]) following = !more }
                }
                val last = rows.lastOrNull()
                LaunchedEffect(rows.size, (last as? Thoughts)?.steps?.size) {
                    if (last == null || !following) return@LaunchedEffect
                    moving[0] = true
                    list.scrollToItem(leading + rows.lastIndex)
                    // Work in progress shows its newest step; a reply opens at its start.
                    if (last is Thoughts) list.scrollBy(100_000f)
                    moving[0] = false
                }
                LazyColumn(Modifier.fillMaxSize().testTag("remThread"), state = list,
                    contentPadding = PaddingValues(horizontal = 16.dp, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                    if (thread.earlier > 0) item(key = "earlier") {
                        Note("${thread.earlier} earlier ${if (thread.earlier == 1) "entry is" else "entries are"} on your desktop.")
                    } else if (rows.isEmpty()) item(key = "empty") {
                        Note(if (thread.running) "The worker is starting." else "No messages yet.")
                    }
                    val newestReply = rows.lastOrNull { it is Reply }?.id
                    val firstInstruction = rows.firstOrNull { it is Instruction }?.id
                    items(rows, key = { it.id }) { row ->
                        when (row) {
                            // Each later instruction starts a new round of work, set apart by a rule.
                            is Instruction -> Column {
                                if (row.id != firstInstruction) HorizontalDivider(Modifier.padding(top = 6.dp, bottom = 14.dp).testTag("remThreadRound"),
                                    color = MaterialTheme.colorScheme.outlineVariant)
                                InstructionCard(row)
                            }
                            is Thoughts -> ThoughtGroup(row)
                            // Work threads are threads on desktop, so replies use the thread text sizes. Only the newest
                            // reply starts open; earlier ones fold to a preview, as long messages do in Rem's chat.
                            is Reply -> WorkerReply(row, thread.id, newest = row.id == newestReply)
                            is Failure -> Surface(color = MaterialTheme.colorScheme.errorContainer, contentColor = MaterialTheme.colorScheme.onErrorContainer,
                                shape = MaterialTheme.shapes.medium, modifier = Modifier.fillMaxWidth()) {
                                SelectionContainer { Text(row.body, Modifier.padding(12.dp), style = MaterialTheme.typography.bodyMedium) }
                            }
                        }
                    }
                }
            }
        }
    }
    if (client.desktopControl && thread != null) {
        var draft by rememberSaveable(id) { mutableStateOf("") }
        ThreadComposer(draft, { draft = it }, "Instruct this task", client.connected && key !in client.desktopBusy, "remInstruction") {
            client.desktopAction("rem.instruct", JSONObject().put("thread_id", id).put("body", draft.trim())); draft = ""
        }
    }
}

/**
 * An instruction to the worker, from Rem or from you: a tinted card that names its author and keeps a long
 * prompt to its first lines until Show more, so the work below stays in reach on a phone.
 */
@Composable
private fun InstructionCard(row: Instruction) {
    val colors = LocalRemColors.current
    val long = remember(row.body) { RemThreadRows.longInstruction(row.body) }
    var open by rememberSaveable(row.id) { mutableStateOf(false) }
    val fromRem = row.author == "rem"
    val fill = if (fromRem) colors.sage else colors.bubble
    val ink = if (fromRem) colors.sageInk else colors.bubbleInk
    Surface(color = fill, contentColor = ink, shape = RoundedCornerShape(14.dp),
        modifier = Modifier.fillMaxWidth().testTag("remThreadInstruction")) {
        Column(Modifier.padding(start = 14.dp, end = 8.dp, top = 10.dp, bottom = if (long) 0.dp else 10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                if (fromRem) Icon(ThreadIcons.Rem, null, Modifier.size(16.dp))
                Text(when (row.author) { "rem" -> "Rem asked"; "user" -> "You asked"; else -> "Instruction" },
                    Modifier.testTag("remThreadAuthor").semantics { contentDescription = when (row.author) {
                        "rem" -> "Written by Rem"; "user" -> "Written by you"; else -> "Instruction" } },
                    style = MaterialTheme.typography.labelLarge, fontWeight = FontWeight.SemiBold)
            }
            SelectionContainer {
                Text(row.body, Modifier.padding(top = 4.dp, end = 6.dp), style = MaterialTheme.typography.bodyMedium,
                    maxLines = if (long && !open) 5 else Int.MAX_VALUE, overflow = TextOverflow.Ellipsis)
            }
            if (long) Row(Modifier.heightIn(min = 44.dp).clickable(role = Role.Button) { open = !open }
                .semantics { stateDescription = if (open) "Expanded" else "Collapsed" }.testTag("remInstructionToggle"),
                verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                Icon(if (open) ThreadIcons.ChevronUp else ThreadIcons.ChevronDown, null, Modifier.size(16.dp))
                Text(if (open) "Show less" else "Show more", style = MaterialTheme.typography.labelLarge)
            }
        }
    }
}

/** The worker's reply: a bordered card like a thread reply, folded to a preview unless it is the newest, with Copy. */
@Composable
private fun WorkerReply(row: Reply, threadId: String, newest: Boolean) {
    CompositionLocalProvider(LocalMarkdownStyle provides threadMarkdownStyle()) {
        Surface(Modifier.fillMaxWidth().testTag("remThreadReply"), shape = MaterialTheme.shapes.small, color = LocalStudioColors.current.reply,
            contentColor = LocalStudioColors.current.onChat, border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant)) {
            Column(Modifier.padding(start = 12.dp, end = 12.dp, top = 10.dp)) {
                FoldableText(startOpen = newest, toggleColor = MaterialTheme.colorScheme.primary) {
                    SelectionContainer { ChatMarkdown(row.body, source = LinkSource.of("rem_thread", threadId, row.id)) }
                }
                HorizontalDivider(Modifier.padding(top = 4.dp), color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = .7f))
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) { CopyButton(row.body, "Copy Markdown", "Copy reply as Markdown") }
            }
        }
    }
}

/** Matches the desktop's "Show thoughts" group: folded once a reply follows, open while the work runs. */
@Composable
private fun ThoughtGroup(row: Thoughts) {
    // A new reply folds the group again, as the desktop resets its toggle per reply.
    var open by rememberSaveable(row.id, row.replied) { mutableStateOf(!row.replied) }
    val muted = LocalRemColors.current.muted
    Column(Modifier.fillMaxWidth().testTag("remThoughts")) {
        Surface(onClick = { open = !open }, shape = MaterialTheme.shapes.small, color = MaterialTheme.colorScheme.surface,
            modifier = Modifier.fillMaxWidth().heightIn(min = 44.dp).semantics {
                role = Role.Button; stateDescription = if (open) "Expanded" else "Collapsed"
            }) {
            Row(Modifier.padding(horizontal = 4.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(if (open) "Hide thoughts" else "Show thoughts", style = MaterialTheme.typography.labelLarge)
                Text("${row.count}", style = MaterialTheme.typography.labelMedium, color = muted)
                if (row.running) CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 2.dp)
                Spacer(Modifier.weight(1f))
                Icon(if (open) Icons.Default.KeyboardArrowUp else Icons.Default.KeyboardArrowDown, null, Modifier.size(20.dp), tint = muted)
            }
        }
        if (open) {
            if (row.count > row.steps.size) Note("Showing the last ${row.steps.size} of ${row.count} steps. The rest are on your desktop.",
                Modifier.padding(start = 4.dp, top = 4.dp, bottom = 6.dp))
            val line = MaterialTheme.colorScheme.outlineVariant
            row.steps.forEachIndexed { index, step ->
                val dot = when {
                    step.status == "error" || step.status == "failed" -> MaterialTheme.colorScheme.error
                    step.kind == "tool" -> Color(0xFF10B981)
                    else -> muted
                }
                Box(Modifier.fillMaxWidth().padding(start = 8.dp).drawBehind {
                    drawLine(line, Offset(0f, 0f), Offset(0f, size.height), strokeWidth = 1.dp.toPx())
                }.padding(start = 16.dp, bottom = if (index == row.steps.lastIndex) 2.dp else 8.dp)) {
                    Box(Modifier.offset(x = (-20).dp, y = 5.dp).size(8.dp).background(dot, CircleShape))
                    StepText(step, muted)
                }
            }
        }
    }
}

/** One line per step where it fits, so a long run stays scannable on a narrow screen. */
@Composable
private fun StepText(step: RemThreadStep, muted: Color) {
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        val title = step.title.ifBlank { if (step.kind == "tool") "Tool" else "Thought" }
        val detail = RemThreadRows.stepDetail(step)
        val line = buildAnnotatedString {
            withStyle(SpanStyle(fontWeight = FontWeight.SemiBold)) { append(title) }
            if (detail.isNotBlank()) withStyle(SpanStyle(color = muted, fontFamily = if (step.detail.isBlank()) FontFamily.Monospace else null)) {
                append("  "); append(detail)
            }
            if (step.status == "running") withStyle(SpanStyle(color = muted, fontStyle = FontStyle.Italic)) { append("  running") }
        }
        Text(line, style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
        if (step.body.isNotBlank()) Text(step.body, style = MaterialTheme.typography.bodySmall, maxLines = 6, overflow = TextOverflow.Ellipsis)
    }
}
