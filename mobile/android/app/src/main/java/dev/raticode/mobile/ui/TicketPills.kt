package dev.raticode.mobile.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.minimumInteractiveComponentSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.onClick
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.Placeholder
import androidx.compose.ui.text.PlaceholderVerticalAlign
import androidx.compose.foundation.text.InlineTextContent
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.raticode.mobile.live.RemTicket
import dev.raticode.mobile.live.RemTicketRef
import dev.raticode.mobile.live.TicketMentions

/** Board tickets Rem's chat may show as pills, by key. Rem's chat provides them; other Markdown shows keys as text. */
internal val LocalRemTickets = compositionLocalOf<Map<String, RemTicket>> { emptyMap() }

/** Opens a ticket on the phone's global board. Null where no board can open, such as previews. */
internal val LocalTicketOpener = staticCompositionLocalOf<((RemTicket) -> Unit)?> { null }

/** Inline pills keep the title short, so a long one doesn't push the line apart. */
private const val INLINE_TITLE = 28

private fun statusIcon(status: String): ImageVector = when (status) {
    "backlog" -> ThreadIcons.CircleDashed
    "in_progress", "in_review" -> ThreadIcons.CircleDot
    "blocked" -> ThreadIcons.Waiting
    "done" -> ThreadIcons.Succeeded
    "cancelled" -> ThreadIcons.CircleSlash
    else -> ThreadIcons.NotStarted
}

@Composable
private fun statusColor(status: String): Color {
    val dark = MaterialTheme.colorScheme.surface.luminance() < .5f
    return when (status) {
        "in_progress", "in_review" -> MaterialTheme.colorScheme.primary
        "done" -> if (dark) Color(0xFF6EE7B7) else Color(0xFF047857)
        "blocked" -> if (dark) Color(0xFFFCD34D) else Color(0xFFB45309)
        else -> MaterialTheme.colorScheme.onSurfaceVariant
    }
}

private fun shortTitle(title: String) = if (title.length <= INLINE_TITLE) title else title.take(INLINE_TITLE - 1).trimEnd() + "…"

/**
 * A board ticket shown like a smart link: a status mark, its key and its title. Tapping opens it on the global board.
 * Each status has its own mark, so color is never the only cue, and TalkBack reads it as a button with the status.
 */
@Composable
internal fun TicketChip(ticket: RemTicket, modifier: Modifier = Modifier, inline: Boolean = false, role: String = "") {
    val open = LocalTicketOpener.current
    val label = TicketMentions.label(ticket) + if (role.isNotBlank()) ". $role" else ""
    Surface(shape = RoundedCornerShape(6.dp), color = MaterialTheme.colorScheme.surface,
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
        modifier = modifier.testTag("ticketPill-${ticket.key}")
            .then(if (inline) Modifier else Modifier.minimumInteractiveComponentSize())
            .clearAndSetSemantics {
                contentDescription = label
                this.role = Role.Button
                if (open != null) onClick("Open on the board") { open(ticket); true }
            }
            .then(if (open != null) Modifier.clickable(onClickLabel = "Open on the board") { open(ticket) } else Modifier)) {
        ChipContent(statusIcon(ticket.status), statusColor(ticket.status), ticket.key, if (inline) shortTitle(ticket.title) else ticket.title,
            muted = ticket.status == "done" || ticket.status == "cancelled", inline = inline)
    }
}

/** A ticket a card remembers that is no longer on the board: still named, struck through and not tappable. */
@Composable
internal fun GoneTicketChip(ref: RemTicketRef) {
    Surface(shape = RoundedCornerShape(6.dp), color = Color.Transparent, border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant.copy(alpha = .6f)),
        modifier = Modifier.testTag("ticketPillGone-${ref.key}").clearAndSetSemantics { contentDescription = TicketMentions.goneLabel(ref) }) {
        ChipContent(ThreadIcons.CircleSlash, MaterialTheme.colorScheme.onSurfaceVariant, ref.key, ref.title, muted = true, struck = true, inline = false)
    }
}

@Composable
private fun ChipContent(icon: ImageVector, tint: Color, key: String, title: String, muted: Boolean, inline: Boolean, struck: Boolean = false) {
    Row(Modifier.padding(horizontal = 6.dp, vertical = if (inline) 0.dp else 3.dp).then(if (inline) Modifier.fillMaxSize() else Modifier),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        Icon(icon, null, Modifier.size(12.dp), tint = tint)
        Text(key, style = PILL_KEY, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, softWrap = false)
        if (title.isNotBlank()) Text(title, style = PILL_TITLE.copy(textDecoration = if (struck) TextDecoration.LineThrough else null),
            color = if (muted) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface,
            maxLines = 1, overflow = TextOverflow.Ellipsis, softWrap = false, modifier = Modifier.widthIn(max = 240.dp))
    }
}

private val PILL_KEY = TextStyle(fontFamily = FontFamily.Monospace, fontWeight = FontWeight.SemiBold, fontSize = 11.sp, lineHeight = 14.sp)
private val PILL_TITLE = TextStyle(fontWeight = FontWeight.Medium, fontSize = 12.sp, lineHeight = 14.sp)

/** The inline id of a ticket pill in Markdown text. */
internal fun ticketInlineId(key: String) = "ticket:$key"

/**
 * Inline pills for the tickets [keys] names, sized to their text so the pill sits in the line like a word.
 * Text holds a placeholder of this size and draws the pill in it.
 */
@Composable
internal fun ticketInlineContent(keys: List<String>, tickets: Map<String, RemTicket>): Map<String, InlineTextContent> {
    val measurer = rememberTextMeasurer()
    val density = LocalDensity.current
    return remember(keys, tickets, density) {
        keys.mapNotNull { tickets[it] }.associate { ticket ->
            val keyWidth = measurer.measure(AnnotatedString(ticket.key), PILL_KEY).size.width
            val title = shortTitle(ticket.title)
            val titleWidth = if (title.isBlank()) 0 else measurer.measure(AnnotatedString(title), PILL_TITLE).size.width
            // Icon 12, gaps 4 each, padding 6 on each side, border 1 on each side, and a little room for rounding.
            val width = with(density) { (keyWidth + titleWidth).toDp() + 12.dp + (if (title.isBlank()) 4.dp else 8.dp) + 14.dp + 6.dp }
            ticketInlineId(ticket.key) to InlineTextContent(
                Placeholder(with(density) { width.toSp() }, 20.sp, PlaceholderVerticalAlign.TextCenter)) {
                TicketChip(ticket, Modifier.fillMaxSize(), inline = true)
            }
        }
    }
}

/**
 * The tickets a task created, worked on or changed, as pills under its card or in its details.
 * A deleted ticket keeps its key and old title. Long lists show the first few and a button for the rest.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun TicketPillRow(refs: List<RemTicketRef>, tickets: Map<String, RemTicket>, label: String, modifier: Modifier = Modifier, limit: Int = 6) {
    if (refs.isEmpty()) return
    var all by rememberSaveable(refs.size) { mutableStateOf(false) }
    val shown = if (all) refs else refs.take(limit)
    FlowRow(modifier.testTag("ticketPills").semantics { contentDescription = "$label: ${refs.size}" },
        horizontalArrangement = Arrangement.spacedBy(4.dp), verticalArrangement = Arrangement.spacedBy(0.dp), itemVerticalAlignment = Alignment.CenterVertically) {
        shown.forEach { ref ->
            val live = TicketMentions.live(ref, tickets)
            if (live != null) TicketChip(live, role = TicketMentions.roleLabel(ref.role)) else GoneTicketChip(ref)
        }
        if (refs.size > shown.size) TextButton(onClick = { all = true }) { Text("+${refs.size - shown.size} more") }
    }
}
