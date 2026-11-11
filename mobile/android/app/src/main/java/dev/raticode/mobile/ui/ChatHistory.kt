package dev.raticode.mobile.ui

import android.content.ClipData
import android.content.ClipboardManager
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.live.LinkSource
import dev.raticode.mobile.live.LiveClient.LiveMessage
import kotlinx.coroutines.delay

/**
 * One row of a thread. A thought group carries the reply that follows it ([replyId]); like
 * desktop, a group is open until that reply exists, then folds away.
 */
internal data class ChatItem(val id: String, val messages: List<LiveMessage>, val thoughts: Boolean = false,
                             val anchors: List<String> = messages.map { it.id }, val replyId: String? = null)

/** Compaction memory and continuation notes: context for providers, never shown, as in desktop's buildChatItems. */
private val HIDDEN_KINDS = setOf("memory", "continuation-context")

/** Desktop drops this restart notice from the transcript; the turn that follows explains itself. */
private const val INTERRUPTED = "The previous process was interrupted."

private val LiveMessage.fromRem get() = state == "Rem"
private val LiveMessage.system get() = state == "Desktop" || kind == "system"

/** Match desktop's buildChatItems: every run of consecutive thoughts is one group. Deterministic history fragments are joined first. */
@android.annotation.SuppressLint("NewApi") // Lint 9.1 mistakes this data class's generated copy() for a platform call.
internal fun buildChatItems(messages: List<LiveMessage>): List<ChatItem> {
    val joined = mutableListOf<Pair<LiveMessage, MutableList<LiveMessage>>>()
    for (message in messages) {
        // Hidden context must not split adjacent thoughts. Desktop also leaves it out of phone history pages.
        if (message.kind in HIDDEN_KINDS || message.system && message.text.startsWith(INTERRUPTED)) continue
        val last = joined.lastOrNull()
        if (last != null && last.first.sourceId == message.sourceId && message.offset > 0 &&
            message.offset == last.first.offset + last.first.text.codePointCount(0, last.first.text.length)) {
            joined[joined.lastIndex] = last.first.copy(text = last.first.text + message.text) to last.second.apply { add(message) }
        } else joined.add(message to mutableListOf(message))
    }
    val items = mutableListOf<ChatItem>()
    var index = 0
    while (index < joined.size) {
        val (message, fragments) = joined[index++]
        if (message.kind != "thought") {
            if (message.kind == "turn-summary" || message.text.isNotBlank() || message.attachments.isNotEmpty())
                items.add(ChatItem(message.id, listOf(message) + fragments.drop(1)))
            continue
        }
        // Desktop groups by position, not groupId, so one turn never splits into several groups.
        val group = message.groupId.ifBlank { "legacy-${message.sourceId}" }
        val thoughts = mutableListOf(message)
        val anchors = fragments.map { it.id }.toMutableList()
        while (index < joined.size && joined[index].first.kind == "thought") {
            anchors.addAll(joined[index].second.map { it.id })
            thoughts.add(joined[index++].first)
        }
        // Desktop suppresses a trailing provider output that repeats the final reply.
        val next = joined.getOrNull(index)?.first
        while (thoughts.isNotEmpty() && next != null && duplicateOutput(thoughts.last(), next)) thoughts.removeAt(thoughts.lastIndex)
        if (thoughts.isNotEmpty()) items.add(ChatItem("thought-group-$group-${message.id}", thoughts, true, anchors))
    }
    // The reply a group belongs to is the next Rem answer before the next message of yours.
    var reply: String? = null
    for (position in items.indices.reversed()) {
        val item = items[position]
        val message = item.messages.first()
        when {
            item.thoughts -> items[position] = item.copy(replyId = reply)
            !message.fromRem && !message.system && message.kind != "turn-summary" -> reply = null
            message.fromRem && message.kind != "error" && message.text.isNotBlank() -> reply = item.id
        }
    }
    return items
}

private fun duplicateOutput(thought: LiveMessage, final: LiveMessage): Boolean {
    if (final.state != "Rem" || final.kind == "thought" || final.kind == "memory" || thought.trace["kind"] == "tool") return false
    fun normalized(value: String) = value.replace(Regex("\\r\\n?"), "\n")
        .replace(Regex("```[^\\n]*\\n?"), "")
        .replace(Regex("(?m)^\\s{0,3}(?:#{1,6}\\s+|>\\s*|[-+*]\\s+|\\d+[.)]\\s+)"), "")
        .replace(Regex("\\[([^\\]]+)\\]\\([^\\s)]+\\)"), "$1")
        .replace(Regex("[*_~`]"), "").replace(Regex("\\s+"), " ").trim()
    val body = normalized(thought.trace["body"] ?: thought.text)
    val answer = normalized(final.text)
    if (body.isBlank() || answer.isBlank()) return false
    val prefix = body.replace(Regex("(?:\\.\\.\\.|…)$"), "").trim()
    return body == answer || prefix.length >= 48 && answer.startsWith(prefix)
}

/** Desktop's buildThoughtTrace: updates to one step merge, keyed by its group and trace ID. */
@android.annotation.SuppressLint("NewApi") // Lint 9.1 mistakes this data class's generated copy() for a platform call.
internal fun thoughtEntries(messages: List<LiveMessage>): List<LiveMessage> {
    val entries = linkedMapOf<String, LiveMessage>()
    for (message in messages) {
        val trace = message.trace
        val body = trace["body"] ?: if (trace.isEmpty()) message.text else ""
        if (trace["kind"] != "tool" && Regex("(?i)^(?:tokens? used\\s*:?\\s*[\\d,]+|[\\d,]+\\s+tokens? used)$").matches(body.trim())) continue
        val key = trace["id"]?.takeIf { it.isNotBlank() }?.let { "trace-${message.groupId}-$it" } ?: message.id
        val old = entries[key]
        if (old == null) entries[key] = message else {
            val merged = old.trace.toMutableMap()
            for ((field, value) in trace) if (value.isNotBlank()) {
                merged[field] = when (field) {
                    "input" -> merged[field]?.takeIf { it.isNotBlank() } ?: value
                    "title" -> if (trace["kind"] == "tool" && merged[field] != "Tool result") merged[field] ?: value else value
                    else -> value
                }
            }
            entries[key] = old.copy(trace = merged)
        }
    }
    return entries.values.toList()
}

/** Desktop's turn timing: "9:05 AM · Ran for 1m 2s", "<1s" under a second, or "Completed" without a time. */
internal fun turnTiming(completedAt: String, durationMs: Long?, zone: java.time.ZoneId = java.time.ZoneId.systemDefault(),
                        locale: java.util.Locale = java.util.Locale.getDefault()): String {
    val completed = runCatching {
        java.time.Instant.parse(completedAt).atZone(zone)
            .format(java.time.format.DateTimeFormatter.ofLocalizedTime(java.time.format.FormatStyle.SHORT).withLocale(locale))
    }.getOrDefault("Completed")
    val milliseconds = maxOf(0L, durationMs ?: 0L)
    return "$completed · Ran for ${if (milliseconds < 1000) "<1s" else duration(Math.round(milliseconds / 1000.0) * 1000)}"
}

internal fun duration(milliseconds: Long): String {
    val seconds = maxOf(0L, milliseconds) / 1000
    return if (seconds < 60) "${seconds}s" else "${seconds / 60}m ${seconds % 60}s"
}

/** Delivery states the phone still shows under your message. Desktop shows none once a turn is done. */
internal fun deliveryNote(state: String): String? = state.takeUnless { it in setOf("Completed", "completed", "Rem", "Desktop") }

@Composable
internal fun ChatHistoryItem(item: ChatItem, searchId: String?, newest: Boolean = true) {
    CompositionLocalProvider(LocalMarkdownStyle provides threadMarkdownStyle()) { HistoryItem(item, searchId, newest) }
}

/** The newest reply in [items], which starts open; earlier long replies fold to a preview. */
internal fun newestReplyId(items: List<ChatItem>): String? =
    items.lastOrNull { !it.thoughts && it.messages.firstOrNull()?.let { m -> m.fromRem && !m.system && m.kind !in setOf("turn-summary", "error") } == true }?.id

@Composable
private fun HistoryItem(item: ChatItem, searchId: String?, newest: Boolean) {
    if (item.thoughts) {
        ThoughtGroup(item, searchId)
        return
    }
    val message = item.messages.first()
    when {
        message.kind == "turn-summary" -> TurnSummary(message)
        message.kind == "error" -> Surface(Modifier.fillMaxWidth().semantics { liveRegion = LiveRegionMode.Polite },
            shape = MaterialTheme.shapes.small, color = MaterialTheme.colorScheme.errorContainer, contentColor = MaterialTheme.colorScheme.onErrorContainer,
            border = BorderStroke(1.dp, MaterialTheme.colorScheme.error.copy(alpha = .4f))) {
            SelectionContainer { Text(message.text, Modifier.padding(horizontal = 12.dp, vertical = 8.dp), style = MaterialTheme.typography.bodyMedium) }
        }
        message.system -> Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
            Surface(Modifier.fillMaxWidth(.86f).wrapContentWidth(), shape = MaterialTheme.shapes.small,
                color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = .5f), contentColor = MaterialTheme.colorScheme.onSurfaceVariant,
                border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant)) {
                Text(message.text, Modifier.padding(horizontal = 12.dp, vertical = 8.dp), style = MaterialTheme.typography.labelMedium)
            }
        }
        // A search match opens its reply so the matching words are on screen.
        message.fromRem -> RemReply(message, open = newest, matched = searchId != null && searchId in item.anchors)
        else -> YourMessage(message)
    }
}

/** Rem's answer: a full-width bordered card with Copy Markdown under it, as on desktop. */
@Composable
private fun RemReply(message: LiveMessage, open: Boolean = true, matched: Boolean = false) {
    Surface(Modifier.fillMaxWidth().testTag("remReply"), shape = MaterialTheme.shapes.small, color = LocalStudioColors.current.reply,
        contentColor = LocalStudioColors.current.onChat, border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant)) {
        Column(Modifier.padding(start = 12.dp, end = 12.dp, top = 8.dp)) {
            Column(Modifier.padding(bottom = 8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (message.attachments.isNotEmpty()) FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    message.attachments.forEach { AttachmentTile(it.id, it.name, it.size) }
                }
                // Whether a reply starts open is decided once, so a new reply doesn't fold the one being read.
                if (message.text.isNotBlank()) key(matched) {
                    FoldableText(startOpen = open || matched, toggleColor = MaterialTheme.colorScheme.primary) {
                        SelectionContainer { ChatMarkdown(message.text, source = LinkSource.of("thread", message.thread, message.sourceId)) }
                    }
                }
            }
            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = .7f))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                CopyButton(message.text, "Copy Markdown", "Copy response as Markdown")
            }
        }
    }
}

/** Your message: a full-width brand bubble with Copy, plus the phone's delivery state while it is still pending. */
@Composable
private fun YourMessage(message: LiveMessage) {
    Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.End) {
        Surface(Modifier.fillMaxWidth().testTag("yourMessage"), shape = MaterialTheme.shapes.small,
            color = MaterialTheme.colorScheme.primary, contentColor = MaterialTheme.colorScheme.onPrimary) {
            // Copy sits beside the text so a one-line message stays one line tall.
            Row(Modifier.padding(start = 12.dp), verticalAlignment = Alignment.Top) {
                Column(Modifier.weight(1f).padding(vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    if (message.attachments.isNotEmpty()) FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        message.attachments.forEach { AttachmentTile(it.id, it.name, it.size) }
                    }
                    if (message.text.isNotBlank()) SelectionContainer { Text(message.text, style = threadMarkdownStyle().body) }
                }
                CopyButton(message.text, null, "Copy message")
            }
        }
        if (message.state in setOf("failed", "cancelled", "Desktop rejected request", "outcome_unknown") && message.detail.isNotBlank())
            Text(message.detail, Modifier.padding(top = 4.dp), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        deliveryNote(message.state)?.let {
            Text(it, Modifier.padding(top = 4.dp), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
internal fun CopyButton(text: String, label: String?, description: String) {
    val context = LocalContext.current
    var copied by remember { mutableStateOf(false) }
    LaunchedEffect(copied) { if (copied) { delay(2000); copied = false } }
    TextButton(onClick = {
        context.getSystemService(ClipboardManager::class.java)?.setPrimaryClip(ClipData.newPlainText("Message", text)); copied = true
    }, contentPadding = PaddingValues(horizontal = 8.dp), modifier = Modifier.heightIn(min = 48.dp)
        .semantics { contentDescription = if (copied) description.replace("Copy", "Copied") else description },
        colors = ButtonDefaults.textButtonColors(contentColor = LocalContentColor.current.copy(alpha = .8f))) {
        Icon(if (copied) ThreadIcons.Check else ThreadIcons.Copy, null, Modifier.size(14.dp))
        if (label != null) { Spacer(Modifier.width(4.dp)); Text(if (copied) "Copied" else label, style = MaterialTheme.typography.labelSmall) }
    }
}

/** Desktop's run line under a turn: a spinner and live "Running for 12s", then the finish time and duration. */
@Composable
private fun TurnSummary(message: LiveMessage) {
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(message.running) { while (message.running) { now = System.currentTimeMillis(); delay(1000) } }
    val timing = if (message.running) message.startedAt?.let { "Running for ${duration(now - it)}" } ?: "Running"
        else remember(message.completedAt, message.durationMs) { turnTiming(message.completedAt, message.durationMs) }
    Row(Modifier.padding(horizontal = 4.dp).semantics(mergeDescendants = true) { contentDescription = if (message.running) "Rem running. $timing" else timing },
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        if (message.running) CircularProgressIndicator(Modifier.size(11.dp), strokeWidth = 1.5.dp)
        Text(timing, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun ThoughtGroup(item: ChatItem, searchId: String?) {
    val entries = remember(item.messages) { thoughtEntries(item.messages) }
    if (entries.isEmpty()) return
    // A tap is remembered for the reply it was made against; a new reply folds the group again, as on desktop.
    var choice by rememberSaveable(item.id) { mutableStateOf<String?>(null) }
    val replyKey = item.replyId.orEmpty()
    val expanded = choice?.takeIf { it.substringBeforeLast('|') == replyKey }?.endsWith("|open") ?: (item.replyId == null)
    LaunchedEffect(searchId) { if (searchId in item.anchors) choice = "$replyKey|open" }
    Column(Modifier.fillMaxWidth()) {
        Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).semantics { stateDescription = if (expanded) "Expanded" else "Collapsed" }
            .clickable(role = Role.Button) { choice = "$replyKey|${if (expanded) "closed" else "open"}" }, verticalAlignment = Alignment.CenterVertically) {
            Text(if (expanded) "Hide thoughts" else "Show thoughts", style = MaterialTheme.typography.labelLarge, fontWeight = FontWeight.SemiBold)
            Text(" ${entries.size}", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.weight(1f))
            Icon(if (expanded) Icons.Default.KeyboardArrowUp else Icons.Default.KeyboardArrowDown, null, Modifier.size(20.dp))
        }
        val railColor = MaterialTheme.colorScheme.outlineVariant
        val markerBaselineOffset = with(LocalDensity.current) { MaterialTheme.typography.bodySmall.fontSize.toPx() / 3f }
        if (expanded) entries.forEachIndexed { index, entry ->
            Row(Modifier.fillMaxWidth().drawBehind {
                drawLine(railColor, Offset(5.dp.toPx(), 0f), Offset(5.dp.toPx(), size.height), 1.dp.toPx())
            }) {
                // Follow the first text baseline, including padded tool buttons and font scaling.
                Box(Modifier.width(22.dp).height(8.dp).alignBy { (it.measuredHeight / 2f + markerBaselineOffset).toInt() }) {
                    Box(Modifier.offset(x = 1.dp).size(8.dp).background(
                        if (entry.trace["status"] == "error") MaterialTheme.colorScheme.error
                        else if (entry.trace["kind"] == "tool") Color(0xff339879)
                        else MaterialTheme.colorScheme.outline, androidx.compose.foundation.shape.CircleShape))
                }
                Column(Modifier.weight(1f).alignByBaseline().padding(bottom = if (index == entries.lastIndex) 4.dp else 16.dp)) { ThoughtEntry(entry) }
            }
        }
    }
}


@Composable
private fun ThoughtEntry(message: LiveMessage) {
    val trace = message.trace
    if (trace["kind"] != "tool") {
        val body = trace["body"] ?: if (trace.isEmpty()) message.text else ""
        if (body.isNotBlank()) SelectionContainer { ChatMarkdown(body, compact = true) }
        else Text(listOfNotNull(trace["title"], trace["detail"]).joinToString(" "), style = MaterialTheme.typography.bodySmall)
    } else {
        var expanded by rememberSaveable(message.id) { mutableStateOf(false) }
        val title = trace["title"].orEmpty().ifBlank { "Tool" }
        val shell = trace["category"] == "shell" || title.lowercase() in setOf("bash", "sh", "zsh", "fish", "powershell", "pwsh", "cmd", "shell", "terminal")
        val editing = trace["category"] == "edit"
        val label = if (shell) "Running ${trace["shell"].orEmpty().ifBlank { title }} commands" else if (editing) "Editing files" else title
        Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).semantics { stateDescription = if (expanded) "Expanded" else "Collapsed" }.clickable(role = Role.Button) { expanded = !expanded }, verticalAlignment = Alignment.CenterVertically) {
            Text(label, Modifier.weight(1f), style = MaterialTheme.typography.labelMedium,
                color = if (trace["status"] == "error") MaterialTheme.colorScheme.error else LocalContentColor.current)
            Icon(if (expanded) Icons.Default.KeyboardArrowDown else Icons.AutoMirrored.Filled.KeyboardArrowRight, null, Modifier.size(18.dp))
        }
        if (expanded) {
            if (!trace["detail"].isNullOrBlank()) Text(trace.getValue("detail"), style = MaterialTheme.typography.bodySmall)
            for ((key, labelText) in listOf("command" to "Command", "input" to "In", "output" to "Out")) {
                val value = trace[key].orEmpty()
                if (value.isNotBlank()) {
                    Text(labelText, Modifier.padding(top = 8.dp), style = MaterialTheme.typography.labelSmall)
                    SelectionContainer { Text(value, Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surface).padding(8.dp), style = MaterialTheme.typography.bodySmall, fontFamily = FontFamily.Monospace) }
                }
            }
        }
    }
    if (message.truncated) Text("Long tool details shortened for mobile sync.", style = MaterialTheme.typography.labelSmall)
}
