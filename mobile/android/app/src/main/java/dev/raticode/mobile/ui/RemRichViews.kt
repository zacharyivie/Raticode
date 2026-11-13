package dev.raticode.mobile.ui

import android.content.ClipData
import android.content.ClipboardManager
import androidx.annotation.RequiresApi
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.live.*
import java.time.Instant

/*
 * The rich parts of a Rem message, drawn from the same fields the desktop uses (RemRichParts.jsx):
 * details, result card, source cards, follow-ups, message actions and task progress.
 */

private val Success = Color(0xFF15803D)
private val SuccessDark = Color(0xFF6EE7B7)

/** Extra depth under a short reply, closed until opened so the reply stays the lead. */
@Composable
internal fun RemDetails(details: String, source: LinkSource?, mentions: List<String> = emptyList()) {
    if (details.isBlank()) return
    var open by rememberSaveable(details) { mutableStateOf(false) }
    Column {
        TextButton(onClick = { open = !open }, contentPadding = PaddingValues(horizontal = 0.dp),
            modifier = Modifier.semantics { stateDescription = if (open) "Expanded" else "Collapsed" }) {
            Text(if (open) "▾ Details" else "▸ Details", style = MaterialTheme.typography.labelLarge)
        }
        if (open) Row(Modifier.height(IntrinsicSize.Min)) {
            Box(Modifier.width(2.dp).fillMaxHeight().background(MaterialTheme.colorScheme.outlineVariant))
            Column(Modifier.padding(start = 10.dp)) { ChatMarkdown(details, source = source, mentions = mentions) }
        }
    }
}

/** A check count with a symbol and words, colored only as a second cue. */
@Composable
internal fun ChecksLine(checks: RemChecks) {
    val summary = RemRich.checks(checks) ?: return
    val dark = MaterialTheme.colorScheme.surface.luminance() < .5f
    val color = when (summary.tone) {
        RemRich.Tone.SUCCESS -> if (dark) SuccessDark else Success
        RemRich.Tone.DANGER -> MaterialTheme.colorScheme.error
        RemRich.Tone.NEUTRAL -> MaterialTheme.colorScheme.onSurfaceVariant
    }
    Text("${summary.symbol} ${summary.text}", Modifier.semantics { contentDescription = summary.text },
        style = MaterialTheme.typography.labelLarge, color = color)
}

/** What a finished task produced: checks, saved reports to open or save, and the pages it used. */
@RequiresApi(29)
@Composable
internal fun RemResultCard(client: LiveClient, result: RemResult, messageId: String, onThread: (String) -> Unit) {
    val links = LocalChatLinks.current
    val source = LinkSource.of("rem", null, messageId)
    Column(Modifier.widthIn(max = 520.dp).testTag("remResult"), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        ChecksLine(result.checks)
        result.artifacts.forEach { artifact ->
            Surface(shape = RoundedCornerShape(12.dp), color = MaterialTheme.colorScheme.surface,
                border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant)) {
                Row(Modifier.fillMaxWidth().padding(10.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Box(Modifier.size(44.dp).clip(RoundedCornerShape(9.dp)).background(MaterialTheme.colorScheme.primaryContainer), contentAlignment = Alignment.Center) {
                        Icon(ThreadIcons.FileText, null, Modifier.size(20.dp), tint = MaterialTheme.colorScheme.onPrimaryContainer)
                    }
                    Column(Modifier.weight(1f)) {
                        Text(artifact.title.ifBlank { artifact.name }, style = MaterialTheme.typography.labelLarge, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        Text(RemRich.artifactLine(artifact), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    // Opening fetches the report from the desktop the same way tapping its link does.
                    val link = ChatLinks.classify(artifact.href) as? ChatLink.File
                    OutlinedButton(onClick = { if (link != null) links?.open(link, source) }, enabled = link != null && client.connected && links != null,
                        modifier = Modifier.semantics { contentDescription = "Open ${artifact.title.ifBlank { artifact.name }}" }) { Text("Open") }
                }
            }
        }
        if (result.sources.isNotEmpty()) SourceCards(client, result.sources)
        if (result.artifacts.isEmpty() && result.threadId.isNotBlank()) TextButton(onClick = { onThread(result.threadId) }) { Text("Open work thread") }
    }
}

/** Web pages a worker used, as cards that scroll sideways. A tap opens the page in the phone's browser. */
@RequiresApi(29)
@Composable
internal fun SourceCards(client: LiveClient, sources: List<RemSource>) {
    val links = LocalChatLinks.current
    val opener = androidx.compose.ui.platform.LocalUriHandler.current
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text("${sources.size} ${if (sources.size == 1) "source" else "sources"}", style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            sources.forEach { source ->
                LaunchedEffect(source.url, client.richResults) { client.requestPreview(source.url) }
                val preview = (client.previews[source.url] as? ChatPreview.Ready)?.preview
                val host = remember(source.url) { runCatching { java.net.URI(source.url).host.removePrefix("www.") }.getOrNull().orEmpty() }
                val video = host.endsWith("youtube.com") || host == "youtu.be"
                val title = preview?.title?.takeIf { it.isNotBlank() } ?: source.title.ifBlank { host }
                Surface(onClick = { links?.open(ChatLink.Web(source.url), null) ?: runCatching { opener.openUri(source.url) } },
                    shape = RoundedCornerShape(10.dp), color = MaterialTheme.colorScheme.surface,
                    border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
                    modifier = Modifier.width(220.dp).semantics { contentDescription = "${if (video) "Video" else "Source"}: $title, $host" }) {
                    Row(Modifier.padding(8.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Box(Modifier.size(44.dp).clip(RoundedCornerShape(7.dp)).background(MaterialTheme.colorScheme.surfaceVariant), contentAlignment = Alignment.Center) {
                            val thumbnail = preview?.thumbnail
                            if (thumbnail != null) {
                                val bitmap = remember(thumbnail) { thumbnail.asImageBitmap() }
                                Image(bitmap, null, Modifier.fillMaxSize(), contentScale = ContentScale.Crop)
                            } else Text(host.take(1).uppercase(), style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            if (video) Box(Modifier.size(20.dp).background(Color.Black.copy(alpha = .65f), CircleShape), contentAlignment = Alignment.Center) {
                                Text("▶", color = Color.White, style = MaterialTheme.typography.labelSmall)
                            }
                        }
                        Column(Modifier.weight(1f)) {
                            Text(title, style = MaterialTheme.typography.labelMedium, maxLines = 2, overflow = TextOverflow.Ellipsis)
                            Text(if (video) "YouTube" else host, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                                maxLines = 1, overflow = TextOverflow.Ellipsis)
                        }
                    }
                }
            }
        }
    }
}

/**
 * Open report, Retry review and Run again under the newest update about a finished task. Each sends the same
 * desktop action as the task screen; none comes from what Rem wrote.
 */
@RequiresApi(29)
@Composable
internal fun RemResultActions(client: LiveClient, home: RemHome, message: RemMessage, live: Boolean) {
    val actions = RemRich.resultActions(message, home.tasks, live, client.reviewRetry)
    val links = LocalChatLinks.current
    val busy = "rem.home" in client.desktopBusy || !client.connected || !client.desktopControl
    RemResultActionRow(actions, enabled = { action -> if (action.id == "open") links != null && fileLink(action) != null else !busy }) { action ->
        when (action.id) {
            "open" -> fileLink(action)?.let { links?.open(it, LinkSource.of("rem", null, message.id)) }
            "retry" -> client.desktopAction("rem.run.retry_review", org.json.JSONObject().put("run_id", action.runId))
            else -> client.desktopAction("rem.task.run", org.json.JSONObject().put("task_id", action.taskId))
        }
    }
}

private fun fileLink(action: RemRich.Action) = action.artifact?.let { ChatLinks.classify(it.href) as? ChatLink.File }

/** The chips themselves, as buttons with their labels; [onAction] runs the matching control. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun RemResultActionRow(actions: List<RemRich.Action>, enabled: (RemRich.Action) -> Boolean = { true }, onAction: (RemRich.Action) -> Unit) {
    if (actions.isEmpty()) return
    FlowRow(Modifier.testTag("remResultActions").semantics { contentDescription = "Task actions" }, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        actions.forEach { action -> OutlinedButton(onClick = { onAction(action) }, enabled = enabled(action)) { Text(action.label) } }
    }
}

/** Likely next messages. A tap sends the text as your reply; the message box keeps its draft. Null [onSend] shows them disabled. */
@Composable
internal fun RemFollowUps(suggestions: List<String>, onSend: ((String) -> Unit)?) {
    if (suggestions.isEmpty()) return
    Row(Modifier.horizontalScroll(rememberScrollState()).testTag("remFollowUps").semantics { contentDescription = "Suggested replies" },
        horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        suggestions.forEach { text -> SuggestionChip(onClick = { onSend?.invoke(text) }, enabled = onSend != null, label = { Text(text, maxLines = 2) }) }
    }
}

/**
 * Reply, Copy, and the reply's model and time, behind a small menu so text selection keeps its long press. [onReply] is
 * null where the desktop can't take replies yet or the message is still on its way.
 */
@Composable
internal fun RemMessageMenu(message: RemMessage, onReply: (() -> Unit)?) {
    var open by remember { mutableStateOf(false) }
    val context = LocalContext.current
    val meta = RemRich.replyMeta(message.meta)
    Box {
        IconButton(onClick = { open = true }, Modifier.size(40.dp).semantics { contentDescription = "Message actions" }) {
            Text("⋯", style = MaterialTheme.typography.titleMedium, color = LocalRemColors.current.muted)
        }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            if (onReply != null) DropdownMenuItem(text = { Text("Reply") }, onClick = { onReply(); open = false })
            val copy = RemRich.copyText(message)
            if (copy.isNotEmpty()) DropdownMenuItem(text = { Text("Copy") }, onClick = {
                context.getSystemService(ClipboardManager::class.java)?.setPrimaryClip(ClipData.newPlainText("Rem", copy))
                open = false
            })
            if (meta.isNotEmpty()) DropdownMenuItem(text = { Text(meta, style = MaterialTheme.typography.labelSmall) }, onClick = {}, enabled = false)
        }
    }
}

/**
 * The message a reply refers to: above the reply's own words, and above the message box while one is written.
 * [onShow] scrolls to it when it is in the chat; [onCancel] unlinks the reply being written.
 */
@Composable
internal fun ReplyQuote(reference: RemReference, composing: Boolean = false, onShow: (() -> Unit)? = null, onCancel: (() -> Unit)? = null) {
    val (author, text) = RemRich.referenceLines(reference)
    val label = "${if (composing) "Replying to" else "In reply to"} $author"
    val accent = if (reference.unavailable) LocalRemColors.current.muted else MaterialTheme.colorScheme.primary
    // In a bubble the quote is as wide as its words; above the message box it spans the box.
    Row((if (composing) Modifier.fillMaxWidth() else Modifier).testTag(if (composing) "remReplyBar" else "remQuote"),
        verticalAlignment = Alignment.CenterVertically) {
        Row(Modifier.weight(1f, fill = composing).height(IntrinsicSize.Min).clip(RoundedCornerShape(6.dp)).background(LocalContentColor.current.copy(alpha = .07f))
            .then(if (onShow != null) Modifier.clickable(onClickLabel = "Show this message", role = Role.Button, onClick = onShow) else Modifier)
            .semantics(mergeDescendants = true) { contentDescription = "$label: $text" }) {
            Box(Modifier.width(3.dp).fillMaxHeight().background(accent))
            Column(Modifier.padding(horizontal = 8.dp, vertical = 4.dp)) {
                Text(label, style = MaterialTheme.typography.labelMedium, color = accent, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(text, style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis,
                    fontStyle = if (reference.unavailable) FontStyle.Italic else null)
            }
        }
        if (onCancel != null) IconButton(onClick = onCancel, Modifier.size(40.dp)) {
            Icon(ThreadIcons.Close, "Don't reply to this message", Modifier.size(18.dp))
        }
    }
}

/** Plan · Work · Check · Report, each said in words, with how long the work has taken. */
@Composable
internal fun RemProgress(run: RemRun) {
    val steps = RemRich.steps(run.state, reachedCheck = run.verificationThreadId.isNotBlank(), step = run.step)
    if (steps.isEmpty()) return
    var now by remember { mutableStateOf(Instant.now()) }
    LaunchedEffect(run.active) { while (run.active) { kotlinx.coroutines.delay(15_000); now = Instant.now() } }
    val elapsed = RemRich.elapsed(run.createdAt, RemRich.instant(run.finishedAt) ?: now)
    val accent = MaterialTheme.colorScheme.primary
    val track = MaterialTheme.colorScheme.outlineVariant
    Column(Modifier.testTag("remProgress"), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(5.dp)) {
            steps.forEach { step ->
                Column(Modifier.weight(1f).clearAndSetSemantics { contentDescription = step.spoken }, verticalArrangement = Arrangement.spacedBy(3.dp)) {
                    Box(Modifier.fillMaxWidth().height(3.dp).clip(RoundedCornerShape(2.dp)).background(when (step.state) {
                        RemRich.StepState.DONE -> accent
                        RemRich.StepState.CURRENT -> accent.copy(alpha = .55f)
                        RemRich.StepState.STOPPED -> MaterialTheme.colorScheme.error
                        // Held where it was, not failed; the ⏸ and "paused" say it without color.
                        RemRich.StepState.PAUSED -> accent.copy(alpha = .35f)
                        RemRich.StepState.TODO -> track
                    }))
                    Text("${step.symbol} ${step.label}", style = MaterialTheme.typography.labelSmall,
                        fontWeight = if (step.state == RemRich.StepState.CURRENT) FontWeight.SemiBold else null,
                        color = when (step.state) {
                            RemRich.StepState.STOPPED -> MaterialTheme.colorScheme.error
                            RemRich.StepState.CURRENT -> MaterialTheme.colorScheme.onSurface
                            else -> MaterialTheme.colorScheme.onSurfaceVariant
                        })
                }
            }
        }
        // The card's status line already names the step; this line adds checks and time.
        val detail = listOfNotNull(
            RemRich.checks(run.checks)?.text?.takeIf { run.active },
            elapsed.takeIf { it.isNotEmpty() }?.let { if (run.finishedAt.isNotBlank()) "Took $it" else "$it so far" },
        ).joinToString(" · ")
        if (detail.isNotEmpty()) Text(detail, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
    }
}

/** Rem's face in the header: a soft ring while it thinks or works, a dot when something needs you. */
@Composable
internal fun RemPresenceFace(presence: RemRich.Presence, size: Dp = 34.dp, icon: Dp = 22.dp) {
    val colors = LocalRemColors.current
    val animate = presence == RemRich.Presence.THINKING || presence == RemRich.Presence.WORKING
    val reduced = reducedMotion()
    val pulse = if (animate && !reduced) rememberInfiniteTransition(label = "presence")
        .animateFloat(1f, 1.08f, infiniteRepeatable(tween(if (presence == RemRich.Presence.WORKING) 1300 else 900), RepeatMode.Reverse), label = "ring").value else 1f
    Box(Modifier.size(size + 6.dp), contentAlignment = Alignment.Center) {
        if (animate) Box(Modifier.size(size + 6.dp).scale(pulse).border(2.dp,
            (if (presence == RemRich.Presence.WORKING) Color(0xFF10B981) else MaterialTheme.colorScheme.primary).copy(alpha = .6f), CircleShape))
        Box(Modifier.size(size).background(colors.sage, CircleShape), contentAlignment = Alignment.Center) {
            Icon(ThreadIcons.Rem, null, Modifier.size(icon), tint = colors.sageInk)
        }
        if (presence == RemRich.Presence.ATTENTION) Box(Modifier.align(Alignment.TopEnd).size(10.dp)
            .border(2.dp, MaterialTheme.colorScheme.background, CircleShape).background(Color(0xFFF59E0B), CircleShape))
    }
}

@Composable
private fun reducedMotion(): Boolean {
    val resolver = LocalContext.current.contentResolver
    return runCatching { android.provider.Settings.Global.getFloat(resolver, android.provider.Settings.Global.ANIMATOR_DURATION_SCALE, 1f) == 0f }.getOrDefault(false)
}

/** Shown when new messages arrive while you read further up. */
@Composable
internal fun NewMessagesPill(count: Int, onClick: () -> Unit, modifier: Modifier = Modifier) {
    if (count <= 0) return
    Surface(onClick = onClick, shape = RoundedCornerShape(999.dp), color = MaterialTheme.colorScheme.surface, shadowElevation = 4.dp,
        border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant), modifier = modifier.testTag("remNewMessages")) {
        Text(if (count == 1) "↓ 1 new message" else "↓ $count new messages", Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
            style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary)
    }
}
