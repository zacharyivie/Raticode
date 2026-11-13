package dev.raticode.mobile.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.annotation.RequiresApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.graphics.BlendMode
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.CompositingStrategy
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.takeOrElse
import androidx.compose.ui.layout.layout
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalWindowInfo
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.semantics.testTag
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.raticode.mobile.data.AttachPlan
import dev.raticode.mobile.data.LongText
import dev.raticode.mobile.live.*
import kotlinx.coroutines.launch
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale

/** Rem's chat laid out like desktop RemHomeWorkspace: rat face replies, lavender user bubbles, day labels. */

private val SUGGESTIONS = listOf(
    "What's running right now?",
    "Research a topic and save a report",
    "Every Monday, check for failed workflow runs",
    "Remind me Friday to send the release notes",
)

@Composable
internal fun RemFace(size: Dp = 28.dp, icon: Dp = 18.dp) {
    val colors = LocalRemColors.current
    Box(Modifier.size(size).background(colors.sage, CircleShape), contentAlignment = Alignment.Center) {
        Icon(ThreadIcons.Rem, null, Modifier.size(icon), tint = colors.sageInk)
    }
}

/**
 * Rem's face, name and status at the top of the Rem tab. With [onOpenTasks], the face, name and status are one button
 * with a chevron that opens Rem's tasks; [tasksLabel] is what TalkBack adds, such as "6 tasks".
 */
@Composable
internal fun RemHeader(status: String, presence: RemRich.Presence = RemRich.Presence.IDLE, onOpenTasks: (() -> Unit)? = null,
                       tasksLabel: String = "", actions: @Composable RowScope.() -> Unit = {}) {
    Row(Modifier.fillMaxWidth().padding(start = if (onOpenTasks == null) 16.dp else 8.dp, end = 8.dp, top = 10.dp, bottom = 6.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        val identity = if (onOpenTasks == null) Modifier else Modifier
            .heightIn(min = 48.dp).clip(RoundedCornerShape(14.dp))
            .clickable(onClickLabel = "Open tasks", role = Role.Button, onClick = onOpenTasks)
            .semantics { stateDescription = tasksLabel }
            .padding(start = 8.dp, end = 4.dp, top = 2.dp, bottom = 2.dp)
            .testTag("remStatusHeader")
        Row(Modifier.weight(1f).then(identity), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            RemPresenceFace(presence)
            Column(Modifier.weight(1f, fill = false)) {
                Text("Rem", style = MaterialTheme.typography.titleLarge, modifier = Modifier.semantics { heading() })
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(status, Modifier.weight(1f, fill = false).semantics { liveRegion = LiveRegionMode.Polite }, style = MaterialTheme.typography.bodySmall,
                        color = LocalRemColors.current.muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    // The chevron says the status opens something; it sits right after the words so it reads as part of them.
                    if (onOpenTasks != null) Icon(ThreadIcons.ChevronRight, null, Modifier.padding(start = 2.dp).size(14.dp), tint = LocalRemColors.current.muted)
                }
            }
        }
        actions()
    }
}

/** When a message was sent, in the phone's time zone, or null when the desktop sent no usable date. */
internal fun messageDate(value: String, zone: ZoneId = ZoneId.systemDefault()): ZonedDateTime? {
    if (value.isBlank()) return null
    return runCatching { OffsetDateTime.parse(value).atZoneSameInstant(zone) }.getOrNull()
        ?: runCatching { LocalDateTime.parse(value).atZone(zone) }.getOrNull()
}

private fun yearSuffix(day: LocalDate, today: LocalDate) = if (day.year != today.year) ", yyyy" else ""

internal fun dayLabel(day: LocalDate, today: LocalDate = LocalDate.now(), locale: Locale = Locale.getDefault()): String = when (day) {
    today -> "Today"
    today.minusDays(1) -> "Yesterday"
    else -> day.format(DateTimeFormatter.ofPattern("EEEE, MMM d${yearSuffix(day, today)}", locale))
}

/** The stamp under a message, worded like the desktop's; [full] is what TalkBack reads. */
internal data class MessageStamp(val label: String, val full: String)

internal fun messageStamp(at: ZonedDateTime, today: LocalDate = LocalDate.now(at.zone), use24Hour: Boolean = false,
                          locale: Locale = Locale.getDefault()): MessageStamp {
    val time = if (use24Hour) at.format(DateTimeFormatter.ofPattern("HH:mm"))
        else "${(at.hour % 12).let { if (it == 0) 12 else it }}:${at.minute.toString().padStart(2, '0')} ${if (at.hour < 12) "AM" else "PM"}"
    val day = at.toLocalDate()
    val date = when (day) {
        today -> ""
        today.minusDays(1) -> "Yesterday"
        else -> day.format(DateTimeFormatter.ofPattern("MMM d${yearSuffix(day, today)}", locale))
    }
    return MessageStamp(if (date.isEmpty()) time else "$date, $time",
        "${day.format(DateTimeFormatter.ofPattern("EEEE, MMMM d, yyyy", locale))}, $time")
}

internal sealed interface RemRow { val key: String }
internal data class DayRow(val label: String, override val key: String) : RemRow
/** [follows]: a Rem message right after another Rem message, drawn with a faint rule above it. */
internal data class MessageRow(val message: RemMessage, val first: Boolean, val stamp: MessageStamp?, val follows: Boolean = false,
                               override val key: String = message.id) : RemRow
/** Cards one self-work pass started together, shown as one Rem message, as on the desktop. */
internal data class BatchRow(val messages: List<RemMessage>, val first: Boolean, val stamp: MessageStamp?, val follows: Boolean = false,
                             override val key: String = messages.first().id) : RemRow

/** The self-work pass a message's card was started in, or null for anything else. */
private fun batchOf(message: RemMessage): String? =
    message.selfWork?.batch?.takeIf { it.isNotBlank() && message.role == "rem" && message.kind != "task_update" && message.work != null }

/**
 * Day labels and stamps as on the desktop. A message without a usable date stays with its neighbors.
 * Cards one self-work pass started one after another become one [BatchRow].
 */
internal fun remRows(messages: List<RemMessage>, today: LocalDate = LocalDate.now(), use24Hour: Boolean = false): List<RemRow> {
    val rows = mutableListOf<RemRow>()
    var day: LocalDate? = null
    var previous: RemMessage? = null
    for (message in messages) {
        val at = messageDate(message.createdAt)
        val current = at?.toLocalDate()
        val newDay = previous == null || (current != null && current != day)
        if (newDay) rows.add(DayRow(current?.let { dayLabel(it, today) } ?: "Earlier", "day-${message.id}"))
        if (current != null) day = current
        val batch = batchOf(message)
        val last = rows.lastOrNull()
        val joined = when {
            batch == null || newDay -> null
            last is BatchRow && batchOf(last.messages.first()) == batch -> last.copy(messages = last.messages + message)
            last is MessageRow && batchOf(last.message) == batch -> BatchRow(listOf(last.message, message), last.first, last.stamp, last.follows)
            else -> null
        }
        if (joined != null) rows[rows.lastIndex] = joined
        else {
            val first = previous == null || newDay || previous.role != message.role
            rows.add(MessageRow(message, first, at?.let { messageStamp(it, today, use24Hour) }, follows = !first && message.role != "user"))
        }
        previous = message
    }
    return rows
}

/** Keeps the message being replied to across rotation and process death, like the draft. */
internal val ReplyTargetSaver = androidx.compose.runtime.saveable.Saver<RemReference?, List<Any>>(
    save = { it?.let { r -> listOf(r.id, r.role, r.body, r.createdAt, r.kind, r.title, r.files, r.unavailable) } ?: emptyList() },
    restore = { v -> if (v.size == 8) RemReference(v[0] as String, v[1] as String, v[2] as String, v[3] as String, v[4] as String,
        v[5] as String, v[6] as Int, v[7] as Boolean) else null },
)

/** The list position of a message's row, counting the item before the rows; -1 when it isn't in the phone's part of the chat. */
internal fun rowIndex(rows: List<RemRow>, id: String): Int =
    rows.indexOfFirst { (it is MessageRow && it.message.id == id) || (it is BatchRow && it.messages.any { m -> m.id == id }) }.let { if (it < 0) -1 else it + 1 }

/** How many messages came after the one with [seenId], or 0 when it isn't in [messages]. */
internal fun messagesAfter(messages: List<RemMessage>, seenId: String): Int =
    messages.indexOfLast { it.id == seenId }.let { if (it < 0) 0 else messages.size - 1 - it }

/**
 * [onReply] starts a reply to a message; it is null when the desktop can't take replies. [showRequest] asks to scroll to
 * a message, such as the one the message box is replying to. [foundRequest] does the same for a search result, opens
 * the message if it is folded and marks it for a moment.
 */
@RequiresApi(29)
@Composable
internal fun RemConversation(client: LiveClient, home: RemHome, onTask: (String) -> Unit, onThread: (String) -> Unit, onSuggest: (String) -> Unit,
                             onFollowUp: ((String) -> Unit)? = null, onReply: ((RemReference) -> Unit)? = null,
                             showRequest: Pair<String, Int>? = null, onEditHeld: ((LiveClient.RemHeld) -> Unit)? = null,
                             foundRequest: Pair<String, Int>? = null, foundWords: List<String> = emptyList()) {
    val list = rememberLazyListState()
    val today = LocalDate.now()
    val use24Hour = android.text.format.DateFormat.is24HourFormat(LocalContext.current)
    // Earlier pages loaded from the desktop come first, then the newest messages rem.home sends.
    val messages = remember(client.remEarlier, home.messages) { RemSearch.joined(client.remEarlier, home.messages) }
    val rows = remember(messages, today, use24Hour) { remRows(messages, today, use24Hour) }
    val replying = home.turnState == "replying"
    val followUpsFor = RemRich.followUpsFor(messages)
    val newestRem = RemRich.latestRemMessageId(messages)
    // Only the newest message about a run shows its live progress, as on the desktop.
    val liveWork = remember(messages) { RemRich.liveWork(messages) }
    var exportId by rememberSaveable { mutableStateOf<String?>(null) }
    val exporter = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/octet-stream")) { uri ->
        exportId?.let { id -> if (uri != null) client.exportFile(id, uri) }
        exportId = null
    }
    val open = rememberFileOpener(client) { id, name -> exportId = id; exporter.launch(name) }
    // Follow new messages only while you're at the end; reading further up is never interrupted.
    // Saved with the scroll position, so coming back from Tasks or a task finds the chat where you left it.
    var pinned by rememberSaveable { mutableStateOf(true) }
    // New messages are counted from the last one seen, so loading earlier ones never counts as new.
    var seenId by rememberSaveable { mutableStateOf(messages.lastOrNull()?.id.orEmpty()) }
    var unseen by rememberSaveable { mutableIntStateOf(0) }
    val scope = rememberCoroutineScope()
    val newestId by rememberUpdatedState(messages.lastOrNull()?.id.orEmpty())
    LaunchedEffect(list) {
        snapshotFlow { list.isScrollInProgress to (!list.canScrollForward) }.collect { (scrolling, atEnd) ->
            if (scrolling || atEnd) pinned = atEnd
            if (atEnd) { unseen = 0; seenId = newestId }
        }
    }
    val last = rows.size + (if (replying) 1 else 0) + (if (client.remUnsent != null) 1 else 0) + (if (client.remHeld != null) 1 else 0)
    // The reply as it is written: small deltas from a desktop that sends them, else the whole draft in rem.home.
    val draft = client.remDraft.ifBlank { home.draftReply }
    LaunchedEffect(newestId, replying, draft.length / 200, client.remUnsent, client.remHeld) {
        if (rows.isEmpty()) return@LaunchedEffect
        if (pinned) { list.scrollToItem(last); seenId = newestId } else unseen = messagesAfter(messages, seenId)
    }
    // The keyboard opening, or the message box growing, shortens the chat from below; at the end, it stays at the end.
    val latestLast by rememberUpdatedState(last)
    LaunchedEffect(list) {
        var height = 0
        snapshotFlow { list.layoutInfo.viewportSize.height }.collect { now ->
            if (now in 1 until height && pinned && !list.isScrollInProgress) list.scrollToItem(latestLast)
            height = now
        }
    }
    // A reply's quote scrolls up to the message it refers to, when that message is in the phone's part of the chat.
    val show: (String) -> Unit = { id -> rowIndex(rows, id).takeIf { it >= 0 }?.let { index -> pinned = false; scope.launch { list.animateScrollToItem(index) } } }
    LaunchedEffect(showRequest) { showRequest?.let { show(it.first) } }
    // A search result: jump straight to it, however far back, open it if folded and mark it for a moment.
    var marked by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(foundRequest) {
        val id = foundRequest?.first ?: return@LaunchedEffect
        val index = rowIndex(rows, id).takeIf { it >= 0 } ?: return@LaunchedEffect
        pinned = false
        marked = id
        list.scrollToItem(index)
        // In a message taller than the chat, scroll on to about where the words are, once it has opened and laid out.
        val body = messages.firstOrNull { it.id == id }?.body.orEmpty()
        val at = foundWords.mapNotNull { word -> body.indexOf(word, ignoreCase = true).takeIf { it >= 0 } }.minOrNull()
        if (at != null && body.isNotEmpty()) {
            repeat(3) { withFrameNanos { } }
            val info = list.layoutInfo
            val viewport = info.viewportEndOffset - info.viewportStartOffset
            info.visibleItemsInfo.firstOrNull { it.index == index }?.takeIf { it.size > viewport }?.let { item ->
                list.scrollToItem(index, (item.size * at.toFloat() / body.length - viewport / 3f).toInt().coerceIn(0, item.size - viewport / 2))
            }
        }
        kotlinx.coroutines.delay(2400)
        marked = null
    }
    val shown = remember(messages) { messages.map { it.id }.toSet() }
    // Folding a message whose top you scrolled past brings its preview and Show more back into view.
    val folded: (String) -> Unit = { key ->
        val above = list.layoutInfo.visibleItemsInfo.firstOrNull { it.key == key }?.let { it.offset < 0 } ?: true
        val index = rows.indexOfFirst { it.key == key } + 1
        if (above && index > 0) scope.launch { list.scrollToItem(index) }
    }
    Box(Modifier.fillMaxSize()) {
    LazyColumn(Modifier.fillMaxSize().testTag("remMessages"), state = list,
        contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 8.dp, bottom = 20.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        item {
            RemEarlierRow(client, home)
            if (messages.isEmpty()) RemWelcome(client.connected && client.desktopControl, onSuggest)
        }
        items(rows, key = { it.key }) { row ->
            when (row) {
                is DayRow -> Text(row.label, Modifier.fillMaxWidth().padding(top = 18.dp, bottom = 8.dp), textAlign = TextAlign.Center,
                    style = MaterialTheme.typography.labelSmall, color = LocalRemColors.current.muted)
                is BatchRow -> FoundMark(row.messages.any { it.id == marked }) { RemBatchRow(home, row, liveWork, onTask, onThread) }
                is MessageRow -> FoundMark(row.message.id == marked) { RemMessageRow(client, home, row, onTask, onThread, open,
                    live = row.message.id in liveWork, followUps = row.message.id == followUpsFor, newest = row.message.id == newestRem,
                    onFolded = { folded(row.key) }, onFollowUp = onFollowUp,
                    onReply = onReply?.let { reply -> { reply(RemRich.replyTarget(row.message)) } },
                    onShow = row.message.refersTo.takeIf { it in shown }?.let { id -> { show(id) } },
                    reveal = foundRequest?.first == row.message.id) { id, name -> exportId = id; exporter.launch(name) } }
            }
        }
        if (replying) item {
            Row(Modifier.padding(top = 10.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                RemFace()
                Column(Modifier.weight(1f).padding(top = 3.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    // The reply as it is written. TalkBack reads the finished message instead of every fragment.
                    if (draft.isNotBlank()) Box(Modifier.clearAndSetSemantics { testTag = "remDraft" }) { ChatMarkdown(draft) }
                    Text(home.turnLabel.takeIf { it.isNotBlank() }?.let { "$it…" } ?: "Rem is replying…",
                        Modifier.semantics { liveRegion = LiveRegionMode.Polite }, style = MaterialTheme.typography.bodyMedium, color = LocalRemColors.current.muted)
                }
            }
        }
        client.remUnsent?.let { unsent -> item(key = "unsent") { RemUnsentRow(client, unsent, onSuggest, onReply) } }
        client.remHeld?.let { held -> item(key = "held") { RemHeldRow(client, held, onEditHeld) } }
    }
    NewMessagesPill(unseen, onClick = { pinned = true; unseen = 0; seenId = newestId; scope.launch { list.animateScrollToItem(last) } },
        Modifier.align(Alignment.BottomCenter).padding(bottom = 12.dp))
    }
}

/** A message the desktop refused stays here with the reason, Try again and Edit, instead of disappearing. */
@RequiresApi(29)
@Composable
private fun RemUnsentRow(client: LiveClient, unsent: LiveClient.RemUnsent, onSuggest: (String) -> Unit, onReply: ((RemReference) -> Unit)?) {
    Column(Modifier.fillMaxWidth().padding(top = 10.dp), horizontalAlignment = Alignment.End) {
        Surface(color = LocalRemColors.current.bubble.copy(alpha = .6f), contentColor = LocalRemColors.current.bubbleInk,
            shape = RoundedCornerShape(18.dp, 18.dp, 6.dp, 18.dp), modifier = Modifier.fillMaxWidth(.82f).wrapContentWidth(Alignment.End)) {
            Column(Modifier.padding(horizontal = 14.dp, vertical = 9.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                unsent.replyTo?.let { ReplyQuote(it) }
                Text(unsent.text.ifBlank { "Attached files" }, style = MaterialTheme.typography.bodyLarge)
            }
        }
        Text("Didn't send: ${unsent.detail}", Modifier.padding(top = 4.dp).semantics { liveRegion = LiveRegionMode.Polite },
            style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.error)
        Row {
            // Files that didn't finish uploading are gone from this phone, so only Edit can send it again.
            val retry = unsent.fileIds.all { id -> client.files.any { it.id == id && it.state == "available" } }
            if (retry) TextButton(onClick = { client.sendRemMessage(unsent.text, unsent.fileIds, replyTo = unsent.replyTo) }, enabled = client.connected) { Text("Try again") }
            // Edit keeps the reply link with the words.
            TextButton(onClick = { onSuggest(unsent.text); unsent.replyTo?.let { onReply?.invoke(it) }; client.dismissRemUnsent() }) { Text("Edit") }
        }
    }
}

/**
 * A message sent while its files upload. It shows each file's ring filling and goes to Rem once they have arrived;
 * Edit takes it back to the message box with its files.
 */
@RequiresApi(29)
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun RemHeldRow(client: LiveClient, held: LiveClient.RemHeld, onEdit: ((LiveClient.RemHeld) -> Unit)?) {
    val files = held.fileIds.mapNotNull { id -> client.files.firstOrNull { it.id == id } }
    val left = files.count { it.state != "available" }
    val size = files.sumOf { it.size }
    val confirmed = files.sumOf { if (it.state == "available") it.size else it.bytes }
    Column(Modifier.fillMaxWidth().padding(top = 10.dp).testTag("remHeld"), horizontalAlignment = Alignment.End) {
        Surface(color = LocalRemColors.current.bubble.copy(alpha = .6f), contentColor = LocalRemColors.current.bubbleInk,
            shape = RoundedCornerShape(18.dp, 18.dp, 6.dp, 18.dp), modifier = Modifier.fillMaxWidth(.82f).wrapContentWidth(Alignment.End)) {
            Column(Modifier.padding(horizontal = 14.dp, vertical = 9.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                held.replyTo?.let { ReplyQuote(it) }
                if (held.text.isNotBlank()) Text(held.text, style = MaterialTheme.typography.bodyLarge)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    held.fileIds.forEach { id -> client.files.firstOrNull { it.id == id }.let { AttachmentTile(id, it?.name ?: "Attachment", it?.size ?: 0) } }
                }
            }
        }
        // Spoken once per change of what it waits for, not at every percent.
        val waiting = when {
            !client.connected -> "Not connected. Sends to Rem once its files upload."
            left == 1 -> "Sends to Rem once 1 file uploads"
            else -> "Sends to Rem once $left files upload"
        }
        Row(Modifier.padding(top = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(waiting, Modifier.semantics { liveRegion = LiveRegionMode.Polite }, style = MaterialTheme.typography.labelSmall,
                color = LocalRemColors.current.muted)
            if (size > 0 && client.connected) Text(" · " + percent(UploadQueue.progress("receiving", confirmed, size)), style = MaterialTheme.typography.labelSmall,
                color = LocalRemColors.current.muted)
        }
        if (onEdit != null) TextButton(onClick = { client.editRemHeld(onEdit) }) { Text("Edit") }
    }
}

@Composable
private fun RemWelcome(enabled: Boolean, onSuggest: (String) -> Unit) {
    Column(Modifier.fillMaxWidth().padding(top = 40.dp, bottom = 16.dp), horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(10.dp)) {
        RemFace(84.dp, 44.dp)
        Text("Hi, I’m Rem.", style = MaterialTheme.typography.headlineSmall)
        Text("Tell me what you need. I’ll start the work on your desktop, check it, and let you know when it’s done.",
            style = MaterialTheme.typography.bodyMedium, color = LocalRemColors.current.muted, textAlign = TextAlign.Center)
        SUGGESTIONS.forEach { text ->
            OutlinedButton(onClick = { onSuggest(text) }, enabled = enabled, shape = RoundedCornerShape(999.dp)) { Text(text, style = MaterialTheme.typography.bodyMedium) }
        }
    }
}

@RequiresApi(29)
@Composable
private fun RemMessageRow(client: LiveClient, home: RemHome, row: MessageRow, onTask: (String) -> Unit, onThread: (String) -> Unit,
                          onOpen: (String, String) -> Unit, live: Boolean = true, followUps: Boolean = false, newest: Boolean = false,
                          onFolded: () -> Unit = {}, onFollowUp: ((String) -> Unit)? = null, onReply: (() -> Unit)? = null, onShow: (() -> Unit)? = null,
                          reveal: Boolean = false, onSave: (String, String) -> Unit) {
    val colors = LocalRemColors.current
    val message = row.message
    val first = row.first
    if (message.role == "user") {
        Column(Modifier.fillMaxWidth().padding(top = if (first) 10.dp else 0.dp), horizontalAlignment = Alignment.End) {
            Surface(color = colors.bubble, contentColor = colors.bubbleInk, shape = RoundedCornerShape(18.dp, 18.dp, 6.dp, 18.dp),
                modifier = Modifier.fillMaxWidth(.82f).wrapContentWidth(Alignment.End)) {
                Column(Modifier.padding(horizontal = 14.dp, vertical = 9.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    message.referenced?.let { ReplyQuote(it, onShow = onShow) }
                    if (message.attachments.isNotEmpty()) FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        message.attachments.forEach { AttachmentTile(it.id, it.name, it.size, messageId = message.id) }
                    }
                    if (message.body.isNotBlank()) FoldableText(onFolded = onFolded, reveal = reveal) {
                        SelectionContainer { Text(message.body, style = MaterialTheme.typography.bodyLarge) }
                    }
                }
            }
            if (message.state == "failed") Text("Not delivered", Modifier.padding(top = 4.dp), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.error)
            // Held by the desktop until Rem finishes its current reply; it has no place to reply to yet.
            else if (RemRich.waiting(message)) Text(RemRich.WAITING_NOTE, Modifier.padding(top = 4.dp).semantics { liveRegion = LiveRegionMode.Polite },
                style = MaterialTheme.typography.labelSmall, color = colors.muted)
            else Row(verticalAlignment = Alignment.CenterVertically) {
                // Your own messages can be copied and replied to as well.
                if (onReply != null || message.body.isNotBlank()) RemMessageMenu(message, onReply)
                if (message.queuedAfter.isNotEmpty()) Text(RemRich.queuedNote(message.queuedAfter), Modifier.padding(top = 3.dp, end = 8.dp),
                    style = MaterialTheme.typography.labelSmall, color = colors.muted)
                row.stamp?.let { MessageStampText(it, Modifier.padding(top = 3.dp, end = 4.dp)) }
            }
        }
        return
    }
    Row(Modifier.fillMaxWidth().padding(top = if (first) 10.dp else 0.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        if (first) RemFace() else Spacer(Modifier.width(28.dp))
        Column(Modifier.weight(1f).padding(top = 3.dp)) {
            if (row.follows) HorizontalDivider(Modifier.padding(top = 6.dp, bottom = 10.dp).testTag("remFollows"), color = MaterialTheme.colorScheme.outlineVariant)
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                message.referenced?.let { ReplyQuote(it, onShow = onShow) }
                val source = LinkSource.of("rem", null, message.id)
                val body = remember(message.body, message.result) { RemRich.bodyWithoutCardLinks(message.body, message.result) }
                if (body.isNotBlank()) FoldableText(startOpen = newest, toggleColor = MaterialTheme.colorScheme.primary, onFolded = onFolded, reveal = reveal) { SelectionContainer { ChatMarkdown(body, source = source, mentions = message.fileMentions) } }
                RemDetails(message.details, source, message.fileMentions)
                message.selfWorkReport?.takeIf { it.items.isNotEmpty() }?.let { SelfWorkReportList(it, onTask, onThread) }
                message.result?.let { RemResultCard(client, it, message.id, onThread) }
                message.files.forEach { file ->
                    // Pictures and videos show inline and open full screen; other files keep their row.
                    val kind = ChatMedia.kind(file.name)
                    if (kind != null) RemMediaFile(client, file, kind, onOpen) { OfferedFile(client, file, onSave, onOpen) }
                    else OfferedFile(client, file, onSave, onOpen)
                }
                message.work?.let { work -> WorkCard(home, work, onTask, onThread, live) }
                PhoneActionCards(message.phoneActions, colors.muted, rememberPhoneActionOpener(), rememberContactPicker())
                RemMemoryNoteView(client, message)
                RemResultActions(client, home, message, live)
                if (followUps) RemFollowUps(message.suggestions, onFollowUp)
            }
            // The stamp and the message's ⋯ menu (Reply, Copy, model and time) share one line.
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                row.stamp?.let { MessageStampText(it, Modifier.padding(top = 2.dp)) }
                Spacer(Modifier.weight(1f))
                if (onReply != null || message.body.isNotBlank()) RemMessageMenu(message, onReply)
            }
        }
    }
}

/** Cards one self-work pass started, as one Rem message: what started, then each task's card with its tickets. */
@Composable
private fun RemBatchRow(home: RemHome, row: BatchRow, liveWork: Set<String>, onTask: (String) -> Unit, onThread: (String) -> Unit) {
    Row(Modifier.fillMaxWidth().padding(top = if (row.first) 10.dp else 0.dp).testTag("remSelfWorkBatch"), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        if (row.first) RemFace() else Spacer(Modifier.width(28.dp))
        Column(Modifier.weight(1f).padding(top = 3.dp)) {
            if (row.follows) HorizontalDivider(Modifier.padding(top = 6.dp, bottom = 10.dp), color = MaterialTheme.colorScheme.outlineVariant)
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(TicketMentions.batchLine(row.messages), style = MaterialTheme.typography.bodyLarge)
                row.messages.forEach { message -> message.work?.let { WorkCard(home, it, onTask, onThread, message.id in liveWork) } }
            }
            row.stamp?.let { MessageStampText(it, Modifier.padding(top = 2.dp)) }
        }
    }
}

/**
 * A long message's text folds to a preview with Show more under it, using the desktop's limits for this screen's height.
 * Only the text folds; quotes, files, cards and actions stay visible, and TalkBack and Copy still get all of the text.
 * The choice is kept per message while the chat is open, including when the row scrolls away.
 */
@Composable
internal fun FoldableText(startOpen: Boolean = false, toggleColor: Color = Color.Unspecified, onFolded: () -> Unit = {}, reveal: Boolean = false,
                          content: @Composable () -> Unit) {
    val density = LocalDensity.current
    val limits = RemRich.collapseLimits(windowHeightDp())
    val preview = with(density) { limits.preview.dp.roundToPx() }
    val threshold = with(density) { limits.threshold.dp.roundToPx() }
    val fade = with(density) { 48.dp.toPx() }
    var open by rememberSaveable { mutableStateOf(startOpen) }
    var long by remember { mutableStateOf(false) }
    // A search result in a folded message opens it.
    LaunchedEffect(reveal) { if (reveal) open = true }
    Column {
        // Measured at full height and clipped in the same pass, so a long message never shows unfolded first.
        Box(Modifier.testTag("remFold").clipToBounds().layout { measurable, constraints ->
            val placeable = measurable.measure(constraints.copy(maxHeight = Constraints.Infinity))
            val isLong = placeable.height > threshold
            if (isLong != long) long = isLong
            layout(placeable.width, if (isLong && !open) preview else placeable.height) { placeable.place(0, 0) }
        }.then(if (long && !open) Modifier.graphicsLayer { compositingStrategy = CompositingStrategy.Offscreen }.drawWithContent {
            drawContent()
            val start = ((size.height - fade) / size.height).coerceIn(0f, 1f)
            drawRect(Brush.verticalGradient(0f to Color.Black, start to Color.Black, 1f to Color.Transparent), blendMode = BlendMode.DstIn)
        } else Modifier)) { content() }
        if (long) FoldToggle(open, toggleColor.takeOrElse { LocalContentColor.current }) { open = !open; if (!open) onFolded() }
    }
}

/** The app window's height in dp. The keyboard doesn't change it, so messages don't refold while you type. */
@Composable
internal fun windowHeightDp(): Int = with(LocalDensity.current) { LocalWindowInfo.current.containerSize.height.toDp().value.toInt() }

@Composable
private fun FoldToggle(open: Boolean, color: Color, onToggle: () -> Unit) {
    val label = if (open) "Show less" else "Show more"
    Row(Modifier.heightIn(min = 48.dp).clickable(role = Role.Button, onClick = onToggle)
        .semantics { stateDescription = if (open) "Expanded" else "Collapsed" }.testTag("remFoldToggle").padding(end = 8.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        Icon(if (open) ThreadIcons.ChevronUp else ThreadIcons.ChevronDown, null, Modifier.size(18.dp), tint = color)
        Text(label, style = MaterialTheme.typography.labelLarge, color = color)
    }
}

/** Shows the short stamp; TalkBack reads the full date and time. */
@Composable
private fun MessageStampText(stamp: MessageStamp, modifier: Modifier = Modifier) {
    Text(stamp.label, modifier.testTag("remStamp").semantics { contentDescription = stamp.full },
        style = MaterialTheme.typography.labelSmall, color = LocalRemColors.current.muted)
}

/** [onOpen], when set, makes the name and size a button that opens the file. */
@Composable
internal fun FileChip(name: String, size: Long, onOpen: (() -> Unit)? = null, trailing: @Composable RowScope.() -> Unit = {}) {
    Surface(shape = RoundedCornerShape(6.dp), color = LocalContentColor.current.copy(alpha = .07f),
        border = androidx.compose.foundation.BorderStroke(1.dp, LocalContentColor.current.copy(alpha = .22f))) {
        Row(Modifier.padding(end = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Row(Modifier.weight(1f, fill = false)
                .then(if (onOpen != null) Modifier.heightIn(min = 40.dp).clickable(onClickLabel = "Open $name", role = Role.Button, onClick = onOpen)
                    .testTag("remFileName") else Modifier)
                .padding(start = 8.dp, end = 2.dp, top = 4.dp, bottom = 4.dp),
                verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Icon(ThreadIcons.FileText, null, Modifier.size(14.dp))
                Text(name, Modifier.weight(1f, fill = false), style = MaterialTheme.typography.labelMedium, maxLines = 1, overflow = TextOverflow.Ellipsis,
                    textDecoration = if (onOpen != null) TextDecoration.Underline else null)
                Text(fileBytes(size), style = MaterialTheme.typography.labelSmall, color = LocalContentColor.current.copy(alpha = .7f))
            }
            Row(Modifier.padding(start = 4.dp), verticalAlignment = Alignment.CenterVertically) { trailing() }
        }
    }
}

/**
 * A file Rem sent to this phone. Tapping its name downloads it when needed and then opens it; Download,
 * Save and Open stay beside it. A file that has expired or failed can't be opened and says so.
 */
@RequiresApi(29)
@Composable
private fun OfferedFile(client: LiveClient, file: RemFile, onSave: (String, String) -> Unit, onOpen: (String, String) -> Unit) {
    val local = client.files.firstOrNull { it.id == file.id }
    val state = local?.state
    // Held only while the row is on screen, so a file never opens later out of the blue.
    var openWhenReady by remember(file.id) { mutableStateOf(false) }
    LaunchedEffect(file.id, state, openWhenReady) {
        when {
            !openWhenReady -> {}
            state == "available" -> { openWhenReady = false; onOpen(file.id, file.name) }
            state !in setOf("offered", "receiving") -> openWhenReady = false
        }
    }
    val tap = SentFileTap.of(state)
    FileChip(file.name, file.size, onOpen = tap?.let { action -> {
        if (action == SentFileTap.DOWNLOAD) client.requestFile(file.id)
        if (action == SentFileTap.OPEN) onOpen(file.id, file.name) else openWhenReady = true
    } }) {
        ReceivedFileActions(file.name, state, if (local != null && local.size > 0) (local.bytes * 100 / local.size).toInt() else 0,
            client.connected, onDownload = { client.requestFile(file.id) }, onSave = { onSave(file.id, file.name) }, onOpen = { onOpen(file.id, file.name) })
    }
}

/**
 * What tapping a sent file's name does in each download state; null for a file that expired, failed or was
 * cancelled. Offline, the request waits in the phone's outbox and goes out when the desktop reconnects, as
 * happens for a moment after coming back from a viewer.
 */
internal enum class SentFileTap {
    OPEN, DOWNLOAD, WAIT;
    companion object {
        fun of(state: String?): SentFileTap? = when (state) {
            "available" -> OPEN
            "offered" -> DOWNLOAD
            "receiving" -> WAIT
            else -> null
        }
    }
}

/**
 * Links a Rem message to its task and work thread. The links stay after a finished one-time task leaves the
 * task list; once the desktop has removed the task or thread, opening it says so.
 */
@Composable
private fun WorkCard(home: RemHome, work: RemWork, onTask: (String) -> Unit, onThread: (String) -> Unit, live: Boolean = true) {
    val task = home.tasks.firstOrNull { it.id == work.taskId }
    val run = listOfNotNull(task?.activeRun, task?.lastRun).plus(task?.runs.orEmpty()).firstOrNull { it.id == work.runId }
    Surface(shape = RoundedCornerShape(12.dp), color = MaterialTheme.colorScheme.surface,
        border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant), modifier = Modifier.widthIn(max = 520.dp)) {
        Column {
            val opens = work.taskId.isNotBlank() && work.taskAvailable
            Row(Modifier.fillMaxWidth().clickable(enabled = opens, onClickLabel = "Open task") { onTask(work.taskId) }
                .padding(start = 12.dp, end = 12.dp, top = 8.dp, bottom = 8.dp), verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                // The task's glyph and colored status, as in the task list, while the card is the live one.
                val current = task?.takeIf { live && work.taskAvailable }
                val activity = current?.let { RemTaskStatus.activity(it) }
                if (current != null && activity != null) StatusIcon(taskGlyph(activity, current.type), toneColor(activity.tone), spinning = activity.spinning, size = 18.dp)
                Column(Modifier.weight(1f)) {
                    Text(task?.title ?: work.title.ifBlank { "Task" }, style = MaterialTheme.typography.labelLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    (if (live) workStatus(work, task) else "Latest update below")?.let {
                        Text(it, style = MaterialTheme.typography.labelSmall, maxLines = 1, overflow = TextOverflow.Ellipsis,
                            color = activity?.tone?.takeIf { tone -> tone != RemTaskStatus.Tone.NEUTRAL }?.let { tone -> toneColor(tone) }
                                ?: MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
                if (opens) Icon(ThreadIcons.ChevronRight, null, Modifier.size(16.dp))
            }
            if (live && run != null) Box(Modifier.padding(start = 12.dp, end = 12.dp, bottom = 8.dp)) { RemProgress(run) }
            // The board tickets this task created, worked on or changed.
            val refs = TicketMentions.cardTickets(work, run)
            if (refs.isNotEmpty()) {
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                TicketPillRow(refs, LocalRemTickets.current, "Tickets for ${task?.title ?: work.title}", modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp))
            }
            if (work.threadId.isNotBlank() && work.threadAvailable) {
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                TextButton(onClick = { onThread(work.threadId) }, Modifier.padding(horizontal = 4.dp)) { Text("Open work thread") }
            }
        }
    }
}

/**
 * The tasks a period of self-work started, under its report message: each title with its outcome. Tapping one opens its
 * task as a task card does, or its work thread when the desktop named no task. Long lists fold after [SELF_WORK_REPORT_SHOWN].
 */
@Composable
private fun SelfWorkReportList(report: RemSelfWorkReport, onTask: (String) -> Unit, onThread: (String) -> Unit) {
    var all by rememberSaveable(report.periodId) { mutableStateOf(false) }
    val shown = if (all) report.items else report.items.take(SELF_WORK_REPORT_SHOWN)
    Surface(shape = RoundedCornerShape(12.dp), color = MaterialTheme.colorScheme.surface,
        border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
        modifier = Modifier.widthIn(max = 520.dp).testTag("remSelfWorkReport")) {
        Column {
            shown.forEachIndexed { index, item ->
                if (index > 0) HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                val open: (() -> Unit)? = when {
                    item.taskId.isNotBlank() -> { { onTask(item.taskId) } }
                    item.threadId.isNotBlank() -> { { onThread(item.threadId) } }
                    else -> null
                }
                val (glyph, info) = RemTaskStatus.selfWorkOutcome(item.outcome)
                val color = toneColor(info.tone)
                Row(Modifier.fillMaxWidth().heightIn(min = 44.dp)
                    .then(if (open != null) Modifier.clickable(onClickLabel = if (item.taskId.isNotBlank()) "Open task" else "Open work thread", onClick = open) else Modifier)
                    .padding(horizontal = 12.dp, vertical = 6.dp).testTag("remSelfWorkReportItem"),
                    verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    StatusIcon(runGlyph(glyph), color, size = 16.dp)
                    Text(item.title.ifBlank { "Task" }, Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    if (info.label.isNotBlank()) Text(info.label, style = MaterialTheme.typography.labelMedium,
                        color = if (info.tone == RemTaskStatus.Tone.NEUTRAL) MaterialTheme.colorScheme.onSurfaceVariant else color)
                    if (open != null) Icon(ThreadIcons.ChevronRight, null, Modifier.size(16.dp))
                }
            }
            if (report.items.size > SELF_WORK_REPORT_SHOWN) {
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                TextButton(onClick = { all = !all }, Modifier.padding(horizontal = 4.dp).testTag("remSelfWorkReportMore")) {
                    Text(if (all) "Show fewer" else "Show all ${report.items.size}")
                }
            }
        }
    }
}

private const val SELF_WORK_REPORT_SHOWN = 8

/** A work card's status: the task's live state, a note once the desktop removed it, or none for a task that left the list. */
internal fun workStatus(work: RemWork, task: RemTask?): String? = when {
    !work.taskAvailable -> "Removed from your desktop"
    task == null -> work.pausedBy.takeIf { it.isNotBlank() }?.let(RunPause::label)
    else -> remActivity(task)
}

internal fun fileBytes(size: Long): String = when {
    size < 1024 -> "$size B"
    size < 1024 * 1024 -> "${size / 1024} KB"
    else -> String.format(java.util.Locale.US, "%.1f MB", size / (1024.0 * 1024))
}

/**
 * The same message bar as threads: attach files, type, send. Files upload over the encrypted Wi-Fi link; Send doesn't
 * wait for them, and the message goes once they have arrived. [restored] are files of a waiting message taken back
 * with Edit; they return as chips, then [onRestored] runs.
 */
@RequiresApi(29)
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun RemComposer(client: LiveClient, home: RemHome?, draft: String, replyTo: RemReference? = null, onReplyTo: (RemReference?) -> Unit = {},
                         onShowReply: (String) -> Unit = {}, restored: List<String> = emptyList(), onRestored: () -> Unit = {},
                         handoff: RemHandoff? = null, onHandoffTaken: () -> Unit = {}, onDraft: (String) -> Unit) {
    var attachments by rememberSaveable { mutableStateOf(listOf<String>()) }
    LaunchedEffect(restored) {
        if (restored.isNotEmpty()) { attachments = (restored + attachments).distinct().take(AttachPlan.MAX_FILES); onRestored() }
    }
    var notice by remember { mutableStateOf<AttachNotice?>(null) }
    val context = LocalContext.current
    // Long text sent as a file, by attachment ID, so its chip previews it and can put it back in the message.
    // In memory only: it can be large, and the file itself is the copy that matters.
    val texts = remember { mutableStateMapOf<String, String>() }
    val latestDraft by rememberUpdatedState(draft)
    val setDraft by rememberUpdatedState(onDraft)
    val reader = remember(client) { ImportQueue<Picked> { picked, done ->
        val main = android.os.Handler(android.os.Looper.getMainLooper())
        // Done once the file is attached or refused; a camera photo or text file is deleted either way.
        val finish = { picked.photo?.delete(); done() }
        // Text that couldn't become a file goes back in the message, as much as fits, so it isn't lost.
        var gaveUp = false
        val keepText = { reason: String ->
            val text = picked.text
            if (text == null) notice = AttachNotice(reason)
            else {
                val joined = listOf(latestDraft.trimEnd(), text).filter { it.isNotEmpty() }.joinToString("\n\n")
                val back = LongText.edit(latestDraft, joined, canFile = false) as LongText.Edit.Keep
                setDraft(back.text)
                notice = AttachNotice("Couldn't send the text as a file. $reason" + if (back.cut > 0) " ${LongText.cutNote(back.cut, null)}" else "")
            }
        }
        // A safety net: the import itself gives up after 30 seconds, but a stopped connection answers neither way.
        val late = Runnable { gaveUp = true; keepText(AttachPlan.TOO_SLOW); finish() }
        main.postDelayed(late, 45_000)
        client.importRemFile(picked.uri, picked.name, onFailed = { reason -> main.removeCallbacks(late); if (!gaveUp || picked.text == null) { keepText(reason); finish() } }) { id ->
            main.removeCallbacks(late)
            // Text already put back in the message mustn't go a second time as a file.
            if (gaveUp && picked.text != null) { client.cancelFile(id); return@importRemFile }
            if (notice?.text == AttachPlan.TOO_SLOW) notice = null
            picked.text?.let { texts[id] = it }
            attachments = (attachments + id).distinct().take(AttachPlan.MAX_FILES); finish()
        }
    } }
    val room = AttachPlan.room(attachments.size, reader.waiting.size)
    val focus = remember { FocusRequester() }
    // Words put here from elsewhere leave the cursor after them, ready for the question; typing keeps the cursor where it is.
    var field by remember { mutableStateOf(TextFieldValue(draft, TextRange(draft.length))) }
    if (field.text != draft) field = TextFieldValue(draft, TextRange(draft.length))
    // Files and words handed over by another screen join what is there; the text typed so far stays, and nothing is sent.
    val fileBlocked = LongText.blocked(client.connected, home?.filesThread != null, room)
    /** Sends [text] as a file with this message. False when the phone couldn't write it, so the words stay where they are. */
    fun attachText(text: String, kind: LongText.Kind): Boolean {
        val picked = pickedText(context, text, kind) ?: run { notice = AttachNotice(LongText.FILE_FAILED); return false }
        notice = null
        reader.add(listOf(picked))
        return true
    }
    /** A paste, a keyboard's dictation or typing. A long addition goes as a file; otherwise the box stops at Rem's limit and says so. */
    fun typed(value: TextFieldValue) {
        val edit = LongText.edit(field.text, value.text, canFile = fileBlocked == null)
        if (edit is LongText.Edit.ToFile && attachText(edit.file, LongText.Kind.PASTED)) {
            field = TextFieldValue(edit.text, TextRange(edit.cursor)); onDraft(edit.text); return
        }
        val keep = edit as? LongText.Edit.Keep ?: LongText.edit(field.text, value.text, canFile = false) as LongText.Edit.Keep
        field = if (keep.cursor == null) value else TextFieldValue(keep.text, TextRange(keep.cursor))
        onDraft(keep.text)
        if (keep.cut > 0) notice = AttachNotice(LongText.cutNote(keep.cut, fileBlocked))
    }
    LaunchedEffect(handoff?.id) {
        val incoming = handoff ?: return@LaunchedEffect
        val words = incoming.text.trim()
        // Long words from another screen go as a file, as a paste does.
        if (words.isNotEmpty() && !(LongText.asFile(words) && fileBlocked == null && attachText(words.take(LongText.MAX_CHARS), LongText.Kind.PASTED))) {
            val joined = listOf(latestDraft.trimEnd(), words).filter { it.isNotEmpty() }.joinToString("\n\n").plus("\n\n")
            val keep = LongText.edit(latestDraft, joined, canFile = false) as LongText.Edit.Keep
            onDraft(keep.text)
            if (keep.cut > 0) notice = AttachNotice(LongText.cutNote(keep.cut, fileBlocked))
        }
        val (kept, note) = AttachPlan.fit(incoming.files, AttachPlan.room(attachments.size, reader.waiting.size))
        (incoming.files - kept.toSet()).forEach { it.photo?.delete() }
        if (note != null) notice = AttachNotice(note)
        reader.add(kept)
        onHandoffTaken()
        runCatching { focus.requestFocus() }
    }
    val replying = home?.turnState == "replying" || "rem.home" in client.desktopBusy
    // Read when the send is committed, so a reply chosen meanwhile isn't cleared.
    val currentReply by rememberUpdatedState(replyTo)
    val canAttach = home?.filesThread != null && client.connected && room > 0
    // Files still uploading don't hold up Send; one that stopped has to be removed or attached again first.
    val ready = attachments.all { id -> client.files.any { it.id == id && it.state in setOf("offered", "receiving", "available") } }
    val held = client.remHeld != null
    // Forget text whose file left the message, except a waiting message's, which Edit can bring back.
    LaunchedEffect(attachments, client.remHeld) { texts.keys.retainAll((attachments + client.remHeld?.fileIds.orEmpty()).toSet()) }
    Surface(color = MaterialTheme.colorScheme.surface, shadowElevation = 2.dp) {
        Column(Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            replyTo?.let { target -> Box(Modifier.padding(start = 8.dp)) {
                ReplyQuote(target, composing = true, onShow = { onShowReply(target.id) }, onCancel = { onReplyTo(null) })
            } }
            if (attachments.isNotEmpty()) FlowRow(Modifier.padding(start = 8.dp), horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp)) {
                attachments.forEach { id ->
                    val file = client.files.firstOrNull { it.id == id }
                    // Removing an unfinished file stops its upload, so it doesn't hold up the next one.
                    AttachmentTile(id, file?.name ?: "Attachment", file?.size ?: 0, onRemove = {
                        attachments = attachments - id
                        if (file?.direction == "upload" && file.state in setOf("offered", "receiving")) client.cancelFile(id)
                    })
                }
            }
            LongTextFiles(attachments.mapNotNull { id -> texts[id]?.let { id to it } }, draft, onPutBack = { id, text ->
                LongText.putBack(draft, text)?.let { joined ->
                    attachments = attachments - id; texts.remove(id)
                    client.files.firstOrNull { it.id == id }?.let { file -> if (file.direction == "upload" && file.state in setOf("offered", "receiving")) client.cancelFile(id) }
                    onDraft(joined); runCatching { focus.requestFocus() }
                }
            })
            if (reader.waiting.isNotEmpty()) Row(Modifier.padding(start = 8.dp), verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 2.dp)
                Text(reader.waiting.size.let { if (it == 1) (if (reader.waiting[0].text != null) "Attaching the text as a file…" else "Reading 1 file…") else "Reading $it files…" }, style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                AttachButton(canAttach, room, onPicked = { reader.add(it) }, onNotice = { notice = it })
                OutlinedTextField(field, ::typed, Modifier.weight(1f).focusRequester(focus).testTag("remComposer").semantics { contentDescription = "Message Rem" },
                    placeholder = { Text(when {
                        !client.connected -> "Rem isn't connected"
                        held -> "Your message sends once its files upload"
                        replying -> "Rem is replying…"
                        attachments.isNotEmpty() -> "Add a note about the attached file"
                        else -> "Message Rem"
                    }) }, shape = MaterialTheme.shapes.large, maxLines = 4, textStyle = MaterialTheme.typography.bodyLarge)
                FilledIconButton(onClick = {
                    val sent = attachments
                    val linked = replyTo
                    // Clears when the message is queued or, with files still uploading, waiting on this phone.
                    client.sendRemMessage(draft, sent, replyTo = linked, hold = true, onCommitted = {
                        onDraft(""); if (attachments == sent) attachments = emptyList(); if (currentReply == linked) onReplyTo(null); notice = null
                    })
                }, enabled = client.connected && !replying && !held && ready && reader.waiting.isEmpty() && (draft.isNotBlank() || attachments.isNotEmpty()),
                    shape = MaterialTheme.shapes.medium, modifier = Modifier.size(48.dp).semantics { contentDescription = "Send" }) {
                    Icon(ThreadIcons.Send, null, Modifier.size(20.dp))
                }
            }
            LongTextCounter(draft.length, fileBlocked, onToFile = { if (draft.isNotBlank() && attachText(draft.trim(), LongText.Kind.MESSAGE)) onDraft("") })
            notice?.let { shown -> Row(Modifier.fillMaxWidth().padding(start = 8.dp).semantics { liveRegion = LiveRegionMode.Polite },
                verticalAlignment = Alignment.CenterVertically) {
                Text(shown.text, Modifier.weight(1f).testTag("remAttachNotice"), style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                if (shown.settings) TextButton(onClick = { openAppSettings(context) }) { Text("Settings") }
                IconButton(onClick = { notice = null }, Modifier.size(48.dp)) { Icon(ThreadIcons.Close, "Dismiss", Modifier.size(16.dp)) }
            } }
            if (home != null && home.filesThread == null) Text("Update Raticode on your computer to attach files here.",
                Modifier.padding(start = 8.dp), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 11.sp)
        }
    }
}

/** Marks a search result's message for a moment, so the eye finds it after the jump. */
@Composable
private fun FoundMark(marked: Boolean, content: @Composable () -> Unit) {
    val tint by androidx.compose.animation.animateColorAsState(
        if (marked) MaterialTheme.colorScheme.primary.copy(alpha = .14f) else Color.Transparent,
        androidx.compose.animation.core.tween(if (marked) 150 else 900), label = "found")
    Box(Modifier.fillMaxWidth().background(tint, RoundedCornerShape(12.dp)).then(if (marked) Modifier.testTag("remFound") else Modifier)) { content() }
}

/**
 * Above the oldest message: how many earlier ones the desktop has. A desktop that pages back offers them a page at a
 * time; an older one, or no connection, just says they are on the desktop.
 */
@RequiresApi(29)
@Composable
private fun RemEarlierRow(client: LiveClient, home: RemHome) {
    val earlier = client.remEarlierCount ?: home.earlier
    val muted = LocalRemColors.current.muted
    Column(Modifier.fillMaxWidth().padding(vertical = 4.dp), horizontalAlignment = Alignment.CenterHorizontally) {
        if (earlier > 0) {
            if (home.search && client.connected && client.desktopControl) TextButton(onClick = { client.loadEarlierRem() }, enabled = !client.remHistoryBusy,
                modifier = Modifier.testTag("remEarlier")) {
                Text(if (client.remHistoryBusy) "Loading earlier messages…" else if (earlier == 1) "Show 1 earlier message" else "Show earlier messages (%,d)".format(earlier))
            }
            else Text(if (earlier == 1) "1 earlier message is on your desktop." else "%,d earlier messages are on your desktop.".format(earlier),
                Modifier.padding(vertical = 8.dp), style = MaterialTheme.typography.labelSmall, color = muted, textAlign = TextAlign.Center)
        }
        if (client.remHistoryError.isNotBlank()) Text(client.remHistoryError, Modifier.padding(bottom = 4.dp).semantics { liveRegion = LiveRegionMode.Polite },
            style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.error, textAlign = TextAlign.Center)
    }
}
