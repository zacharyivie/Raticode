package dev.raticode.mobile.ui

import androidx.annotation.RequiresApi
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.live.PhonePost
import dev.raticode.mobile.live.PhonePostVariant
import dev.raticode.mobile.live.PlatformCopy
import dev.raticode.mobile.live.Posts
import dev.raticode.mobile.live.PostsView

/** A decision waiting for the user's second tap: the action, its post, and the variant it is for. */
private data class PendingDecision(val decision: String, val post: PhonePost, val variant: PhonePostVariant? = null,
                                    val copy: PlatformCopy? = null)

/**
 * The computer's Posts dashboard on the phone: what's waiting for review, due, scheduled or stuck, with each platform's
 * text as it will appear. Every decision asks once more before it goes, and names what it does.
 */
@RequiresApi(29)
@Composable
internal fun PostsPage(client: LiveClient, onBack: (() -> Unit)?) {
    DisposableEffect(Unit) { client.openDesktop(Posts.LIST); onDispose { client.openDesktop(null) } }
    val view = client.desktopViews[Posts.LIST] as? PostsView
    val busy = Posts.LIST in client.desktopBusy
    var pending by remember { mutableStateOf<PendingDecision?>(null) }
    var confirmResume by remember { mutableStateOf(false) }
    val waiting = view?.posts?.count { it.lane == "review" } ?: 0
    Column(Modifier.fillMaxSize()) {
        Header("Posts", when {
            view == null -> ""
            view.paused -> "Publishing is paused"
            waiting > 0 -> "$waiting waiting for your review"
            else -> "Nothing waiting for you"
        }, onBack)
        ExperimentalBanner()
        Box(Modifier.weight(1f)) {
            PostsGate(client) {
                if (view != null) LazyColumn(Modifier.fillMaxSize().testTag("postsList"), contentPadding = PaddingValues(16.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    item {
                        PublishingSwitch(view.paused, busy) { pause ->
                            if (pause) client.desktopAction(Posts.DECIDE, Posts.pause(true)) else confirmResume = true
                        }
                    }
                    view.accounts.filter { it.needsReconnect }.forEach { account ->
                        item { Note("${account.name} needs to be reconnected in Posts on your computer.") }
                    }
                    if (view.posts.isEmpty()) item { PostsWelcome(view.accounts.isEmpty()) }
                    Posts.LANES.forEach { (lane, heading) ->
                        val posts = view.posts.filter { it.lane == lane }
                        if (posts.isNotEmpty()) {
                            item { Text(heading, style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = 8.dp)) }
                            items(posts, key = { it.id }) { post -> PostCard(post, busy, startOpen = lane == "review" || lane == "due") { pending = it } }
                        }
                    }
                    if (view.more > 0) item { Note("${view.more} older posts are only on your computer.") }
                }
            }
        }
    }
    pending?.let { decision -> DecisionDialog(decision, onDismiss = { pending = null }) { note, url ->
        val copy = decision.copy
        val params = if (copy != null) Posts.copyDecision(decision.decision, decision.post, copy)
            else Posts.decision(decision.decision, decision.post, decision.variant?.id, note, url,
                unseenConfirmed = decision.post.unseen && decision.decision in setOf("approve", "approve_now"))
        client.desktopAction(Posts.DECIDE, params); pending = null
    } }
    if (confirmResume) AlertDialog(onDismissRequest = { confirmResume = false },
        title = { Text("Resume publishing?") },
        text = { Text("Approved posts go out at their times again. Times that passed while paused are skipped, not caught up.") },
        confirmButton = { TextButton(onClick = { client.desktopAction(Posts.DECIDE, Posts.pause(false)); confirmResume = false }) { Text("Resume") } },
        dismissButton = { TextButton(onClick = { confirmResume = false }) { Text("Keep paused") } })
}

/** Adds the desktop's Posts support to the usual connection, opt-in, loading and error states. */
@RequiresApi(29)
@Composable
private fun PostsGate(client: LiveClient, content: @Composable () -> Unit) {
    val loaded = client.desktopViews.containsKey(Posts.LIST)
    when {
        !loaded && client.connected && client.desktopControl && client.desktopPosts == false ->
            Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Note(Posts.UPDATE_DESKTOP); TextButton(onClick = client::checkDesktopFeatures) { Text("Check again") }
            }
        !loaded && client.connected && client.desktopControl && client.desktopPosts == null ->
            Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                LinearProgressIndicator(Modifier.fillMaxWidth()); Note("Checking what your desktop supports…")
            }
        else -> DesktopGate(client, Posts.LIST, "Loading posts…", content)
    }
}

/** Posts is experimental; the banner stays above every state of the page, including loading and errors. */
@Composable
private fun ExperimentalBanner() {
    Surface(color = MaterialTheme.colorScheme.tertiaryContainer, modifier = Modifier.fillMaxWidth().testTag("postsExperimental")) {
        Row(Modifier.padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Surface(shape = MaterialTheme.shapes.small, color = MaterialTheme.colorScheme.tertiary) {
                Text(Posts.EXPERIMENTAL, Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                    style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.onTertiary)
            }
            Text(Posts.EXPERIMENTAL_NOTE, style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onTertiaryContainer)
        }
    }
}

@Composable
private fun PublishingSwitch(paused: Boolean, busy: Boolean, onChange: (Boolean) -> Unit) {
    Card(Modifier.fillMaxWidth()) {
        Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("Publishing", style = MaterialTheme.typography.titleMedium)
                Note(if (paused) "Paused. Nothing goes out until you resume." else "On. Approved posts go out at their times.")
            }
            Switch(checked = !paused, enabled = !busy, onCheckedChange = { on -> onChange(!on) },
                modifier = Modifier.testTag("postsPublishing").semantics { contentDescription = "Publishing" })
        }
    }
}

@Composable
private fun PostsWelcome(noAccounts: Boolean) {
    Column(Modifier.fillMaxWidth().padding(vertical = 24.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("No posts yet", style = MaterialTheme.typography.titleMedium)
        Note(if (noAccounts) "Connect your Discord, X, LinkedIn, Facebook Page or YouTube accounts in Posts on your computer first."
            else "Ask Rem to draft one, or write one in Posts on your computer.")
        Note("Rem, tasks and workflows can only propose. Each post waits here and on your computer until you approve it.")
    }
}

@Composable
private fun PostCard(post: PhonePost, busy: Boolean, startOpen: Boolean, onDecide: (PendingDecision) -> Unit) {
    var open by remember(post.id) { mutableStateOf(startOpen) }
    var why by remember(post.id) { mutableStateOf(false) }
    val context = LocalContext.current
    Card(Modifier.fillMaxWidth().testTag("post-${post.id}")) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(post.title, style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f), maxLines = 2, overflow = TextOverflow.Ellipsis)
                AssistChip(onClick = { open = !open }, label = { Text(Posts.label(post.state)) })
            }
            Note(listOf(Posts.author(post.createdBy), post.scheduledAt?.let { "goes out ${whenText(it)}" } ?: "goes out when approved",
                post.variants.joinToString(", ") { it.platformLabel }).joinToString(" · "))
            post.flags.forEach { Text("⚠ $it", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error) }
            post.timing.forEach { Text("🕒 $it", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
            post.copies.forEach { copy -> CopyNotDeleted(post, copy, busy, onDecide) { url -> openInBrowser(context, url) } }
            if (open) {
                post.variants.forEach { variant -> VariantPreview(post, variant, busy, onDecide) { url -> openInBrowser(context, url) } }
                if (post.rationale.isNotBlank() || post.sources.isNotEmpty()) {
                    TextButton(onClick = { why = !why }) { Text(if (why) "Hide why" else "Why ${Posts.author(post.createdBy)} proposed this") }
                    if (why) {
                        if (post.rationale.isNotBlank()) Text(post.rationale, style = MaterialTheme.typography.bodySmall)
                        post.sources.forEach { (title, url) ->
                            TextButton(onClick = { openInBrowser(context, url) }) { Text(title.ifBlank { url }, maxLines = 1, overflow = TextOverflow.Ellipsis) }
                        }
                    }
                }
                post.notes.forEach { (by, text) -> Note("${if (by == "user") "You" else Posts.author(by)}: $text") }
            } else TextButton(onClick = { open = true }) { Text("Show the text") }
            DecisionRow(post, busy, onDecide)
        }
    }
}

@Composable
private fun VariantPreview(post: PhonePost, variant: PhonePostVariant, busy: Boolean, onDecide: (PendingDecision) -> Unit, onOpen: (String) -> Unit) {
    val (shown, rest) = Posts.fold(variant)
    Surface(tonalElevation = 2.dp, shape = MaterialTheme.shapes.medium, modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("${variant.platformLabel} · ${variant.accountName}", style = MaterialTheme.typography.labelLarge, fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(Posts.label(variant.state), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Text(shown, style = MaterialTheme.typography.bodyMedium)
            if (rest.isNotEmpty()) {
                Text("${variant.platformLabel} shows “see more” here", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.primary)
                Text(rest, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            if (variant.textCut) Note("The rest of this text is only shown on your computer.")
            if (variant.link.isNotBlank()) Text(variant.link, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.primary,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (variant.mediaCount > 0) Note("${variant.mediaCount} media ${if (variant.mediaCount == 1) "file" else "files"}: open Posts on your computer to see them.")
            val over = variant.limit > 0 && variant.chars > variant.limit
            Text(listOfNotNull("${variant.chars} / ${variant.limit} characters".takeIf { variant.limit > 0 },
                variant.costUsd?.let { "about $%.3f".format(it) }).joinToString(" · "),
                style = MaterialTheme.typography.labelSmall, color = if (over) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant)
            if (variant.message.isNotBlank()) Note(variant.message)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if ("publish" in variant.actions) OutlinedButton(enabled = !busy, onClick = { onDecide(PendingDecision("publish", post, variant)) }) { Text("Publish now") }
                if ("mark_posted" in variant.actions) {
                    if (variant.openUrl.isNotBlank()) TextButton(onClick = { onOpen(variant.openUrl) }) { Text("Open ${variant.platformLabel}") }
                    OutlinedButton(enabled = !busy, onClick = { onDecide(PendingDecision("mark_posted", post, variant)) }) { Text("Mark posted") }
                }
                if (variant.url.isNotBlank()) TextButton(onClick = { onOpen(variant.url) }) { Text("View post") }
            }
        }
    }
}

/** A copy the platform still holds that the computer couldn't delete: its message, its link, Try again and I deleted it. */
@Composable
private fun CopyNotDeleted(post: PhonePost, copy: PlatformCopy, busy: Boolean, onDecide: (PendingDecision) -> Unit, onOpen: (String) -> Unit) {
    Surface(tonalElevation = 2.dp, shape = MaterialTheme.shapes.medium, modifier = Modifier.fillMaxWidth().testTag("postCopy-${copy.id}")) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("An earlier copy on ${copy.platformLabel} wasn't deleted", style = MaterialTheme.typography.labelLarge,
                fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.error)
            if (copy.accountName.isNotBlank()) Note(copy.accountName)
            if (copy.message.isNotBlank()) Text("${copy.platformLabel}: ${copy.message}", style = MaterialTheme.typography.bodySmall)
            Note(if (copy.waiting) "Your computer stopped trying. Fix the problem and tap Try again, or delete the copy on ${copy.platformLabel} yourself and tap I deleted it."
                else "Your computer tries again ${copy.nextTryAt?.let { whenText(it) } ?: "shortly"}.")
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                if (copy.url.isNotBlank()) TextButton(onClick = { onOpen(copy.url) }) { Text("View the copy") }
                if ("retry_copy" in copy.actions) OutlinedButton(enabled = !busy, onClick = { onDecide(PendingDecision("retry_copy", post, copy = copy)) }) { Text("Try again") }
                if ("copy_deleted" in copy.actions) OutlinedButton(enabled = !busy, onClick = { onDecide(PendingDecision("copy_deleted", post, copy = copy)) }) { Text("I deleted it") }
            }
        }
    }
}

@Composable
private fun DecisionRow(post: PhonePost, busy: Boolean, onDecide: (PendingDecision) -> Unit) {
    if (post.actions.isEmpty()) return
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        if ("approve" in post.actions) Button(enabled = !busy, onClick = { onDecide(PendingDecision("approve", post)) },
            modifier = Modifier.testTag("approve-${post.id}")) { Text(if (post.scheduledAt == null) "Approve and post" else "Approve") }
        if ("approve_now" in post.actions && post.scheduledAt != null) OutlinedButton(enabled = !busy,
            onClick = { onDecide(PendingDecision("approve_now", post)) }) { Text("Post now") }
        if ("request_changes" in post.actions) OutlinedButton(enabled = !busy,
            onClick = { onDecide(PendingDecision("request_changes", post)) }) { Text("Ask for changes") }
        if ("decline" in post.actions) TextButton(enabled = !busy, onClick = { onDecide(PendingDecision("decline", post)) }) { Text("Decline") }
        if ("cancel" in post.actions) TextButton(enabled = !busy, onClick = { onDecide(PendingDecision("cancel", post)) }) { Text("Cancel post") }
    }
}

/** The second tap: says exactly what the decision does, and takes a note or link where one helps. */
@Composable
private fun DecisionDialog(pending: PendingDecision, onDismiss: () -> Unit, onConfirm: (String, String) -> Unit) {
    var note by remember { mutableStateOf("") }
    var url by remember { mutableStateOf("") }
    val post = pending.post
    val where = post.variants.joinToString(", ") { it.platformLabel }
    val (title, body, confirm) = when (pending.decision) {
        "approve" -> Triple("Approve “${post.title}”?", if (post.scheduledAt == null) "It goes out to $where as soon as you approve."
            else "It goes out to $where ${whenText(post.scheduledAt)}. Any later change sends it back for review.", "Approve")
        "approve_now" -> Triple("Post “${post.title}” now?", "It goes out to $where now instead of at its planned time.", "Post now")
        "request_changes" -> Triple("Ask for changes?", "Say what to change. ${Posts.author(post.createdBy)} can revise it and it comes back for review.", "Send")
        "decline" -> Triple("Decline “${post.title}”?", "It won't be posted. You can say why.", "Decline")
        "cancel" -> Triple("Cancel “${post.title}”?", "Parts that haven't gone out yet won't. Parts already posted stay up.", "Cancel post")
        "publish" -> Triple("Publish to ${pending.variant?.platformLabel} now?", "It goes out to ${pending.variant?.accountName} right away.", "Publish")
        "retry_copy" -> Triple("Try deleting it again?", "Your computer asks ${pending.copy?.platformLabel} to delete the earlier copy now.", "Try again")
        "copy_deleted" -> Triple("Did you delete it?", "Only say so once the earlier copy is gone from ${pending.copy?.platformLabel}. Then this post can go out there again.", "I deleted it")
        else -> Triple("Mark as posted?", "Paste the link to the post you made on ${pending.variant?.platformLabel}, if you have it.", "Mark posted")
    }
    val approving = pending.decision in setOf("approve", "approve_now")
    AlertDialog(onDismissRequest = onDismiss, modifier = Modifier.testTag("postDecision"),
        title = { Text(title, maxLines = 3, overflow = TextOverflow.Ellipsis) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(body)
                if (approving && post.unseen) Text("Part of this post isn't shown on the phone: long text, media, or options such as a title. Approve only if you've checked it on your computer.",
                    color = MaterialTheme.colorScheme.error)
                if (approving && post.flags.isNotEmpty()) Text("Check the warnings on this post first.", color = MaterialTheme.colorScheme.error)
                if (pending.decision in setOf("request_changes", "decline", "cancel"))
                    OutlinedTextField(note, { note = it.take(2000) }, label = { Text(if (pending.decision == "request_changes") "What to change" else "Note (optional)") },
                        modifier = Modifier.fillMaxWidth().testTag("postNote"), minLines = 2)
                if (pending.decision == "mark_posted")
                    OutlinedTextField(url, { url = it.take(2000) }, label = { Text("Link to the post") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            }
        },
        confirmButton = {
            TextButton(enabled = pending.decision != "request_changes" || note.isNotBlank(), onClick = { onConfirm(note, url) },
                modifier = Modifier.testTag("postDecisionConfirm")) { Text(confirm) }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Back") } })
}
