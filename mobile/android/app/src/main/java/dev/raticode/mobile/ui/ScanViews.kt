package dev.raticode.mobile.ui

import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.*
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * Pieces that make a detail screen readable at a glance, in the desktop's status colors ([orgColors]): a status chip, a grid
 * of icon-and-value facts, explanations folded into a chip, long text folded to its first sentence, a column strip, cards
 * with a titled header and a bar of actions pinned under the page. Every folded text is still reachable with one tap, and
 * TalkBack reads each fact as "label: value".
 */
internal object ScanText {
    private val LINK = Regex("""\[([^\]]*)]\([^)]*\)""")
    private val EMPHASIS = Regex("""\*\*|`""")
    private val LINE_MARK = Regex("""^(#{1,6}\s+|>\s*|[-*+]\s+|\d+[.)]\s+)""")
    private val SENTENCE_END = Regex("""[.!?](?=\s+["'(\[]?[A-Za-z0-9]|$)""")

    /** [text] as one line of plain words: Markdown links keep their words, list and heading marks and bold go. */
    fun plain(text: String): String = text.replace(LINK) { it.groupValues[1] }.replace(EMPHASIS, "").lineSequence()
        .map { it.trim().replace(LINE_MARK, "").trim() }.filter { it.isNotEmpty() }.joinToString(" ").replace(Regex("\\s+"), " ").trim()

    /**
     * The first sentence of [text] as plain words, at most [max] characters, for a one-line preview of a longer explanation.
     * A sentence ends at ".", "!" or "?" followed by a space and a word, or at the end; "0.34" and "1.5" don't end one.
     */
    fun firstSentence(text: String, max: Int = 160): String {
        val words = plain(text)
        if (words.isEmpty()) return ""
        val end = SENTENCE_END.find(words)?.range?.last
        val sentence = if (end != null) words.substring(0, end + 1) else words
        return if (sentence.length <= max) sentence else sentence.take(max - 1).trimEnd().trimEnd(',', ';', ':') + "…"
    }

    /** True when [firstSentence] leaves something out, so a Show more is worth offering. */
    fun hasMore(text: String, max: Int = 160): Boolean = firstSentence(text, max).removeSuffix("…").length < plain(text).length

    /**
     * The steps a board ticket goes through, for a strip of dots: Backlog, To do, In progress, Review, Done. Blocked isn't a
     * step; a blocked ticket shows where it stopped. Returns the step index for [column], or -1 for one off the strip.
     */
    val STEPS = listOf("backlog", "todo", "running", "review", "done")
    fun stepOf(column: String?): Int = STEPS.indexOf(column)
}

/** A status that matters most on a page: bigger than [Pill], with a colored dot or icon, and its state in words. */
@Composable
internal fun StatusChip(text: String, tone: OrgTone, modifier: Modifier = Modifier, icon: ImageVector? = null) {
    val (ink, fill) = orgColors(tone)
    Row(modifier.semantics(mergeDescendants = true) {}.clip(RoundedCornerShape(50)).background(fill).heightIn(min = 28.dp).padding(horizontal = 10.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        if (icon != null) Icon(icon, null, Modifier.size(14.dp), tint = ink)
        else Box(Modifier.size(8.dp).clip(CircleShape).background(ink))
        Text(text, color = ink, style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.SemiBold), maxLines = 1)
    }
}

/** One fact for [FactGrid]: an icon, a short label and its value. [onClick] makes the tile a button, such as one that opens a menu. */
internal data class Fact(val icon: ImageVector, val label: String, val value: String, val tone: OrgTone? = null,
                         val tag: String = "", val onClickLabel: String? = null, val onClick: (() -> Unit)? = null)

/** Facts two to a row, each a small tile, instead of a paragraph that has to be read to find them. */
@Composable
internal fun FactGrid(facts: List<Fact>, modifier: Modifier = Modifier) {
    if (facts.isEmpty()) return
    Column(modifier.fillMaxWidth().testTag("factGrid"), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        facts.chunked(2).forEach { row ->
            Row(Modifier.fillMaxWidth().height(IntrinsicSize.Min), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                row.forEach { FactTile(it, Modifier.weight(1f).fillMaxHeight()) }
                if (row.size == 1) Spacer(Modifier.weight(1f))
            }
        }
    }
}

@Composable
internal fun FactTile(fact: Fact, modifier: Modifier = Modifier) {
    val shape = RoundedCornerShape(12.dp)
    val accent = fact.tone?.let { orgColors(it) }
    val tile = modifier.clip(shape).background(MaterialTheme.colorScheme.surface).border(1.dp, MaterialTheme.colorScheme.outlineVariant, shape)
        .then(if (fact.onClick != null) Modifier.clickable(onClickLabel = fact.onClickLabel, role = Role.Button, onClick = fact.onClick) else Modifier)
        .heightIn(min = 56.dp).padding(horizontal = 10.dp, vertical = 8.dp)
        .semantics(mergeDescendants = true) { contentDescription = "${fact.label}: ${fact.value}" }
        .then(if (fact.tag.isNotBlank()) Modifier.testTag(fact.tag) else Modifier)
    Row(tile, verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        Box(Modifier.size(30.dp).clip(CircleShape).background(accent?.second ?: MaterialTheme.colorScheme.surfaceVariant), contentAlignment = Alignment.Center) {
            Icon(fact.icon, null, Modifier.size(16.dp), tint = accent?.first ?: MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Column(Modifier.weight(1f)) {
            Text(fact.label.uppercase(), style = MaterialTheme.typography.labelSmall.copy(letterSpacing = 0.6.sp), color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1)
            Text(fact.value, style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold), maxLines = 2, overflow = TextOverflow.Ellipsis,
                color = accent?.first ?: MaterialTheme.colorScheme.onSurface)
        }
        if (fact.onClick != null) Icon(ThreadIcons.ExpandMore, null, Modifier.size(16.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/**
 * An explanation folded into a small chip: [label] says what it is about and a tap shows [text]. For notes that are true
 * but rarely needed, such as why a ticket was approved without you.
 */
@Composable
internal fun InfoChip(label: String, text: String, modifier: Modifier = Modifier, tone: OrgTone = OrgTone.IDLE, icon: ImageVector = ThreadIcons.Info,
                      tag: String = "infoChip") {
    var open by rememberSaveable(label, text) { mutableStateOf(false) }
    val (ink, fill) = orgColors(tone)
    val shape = RoundedCornerShape(14.dp)
    Column(modifier.clip(shape).background(fill).clickable(role = Role.Button, onClickLabel = if (open) "Hide" else "Explain") { open = !open }
        .semantics { stateDescription = if (open) "Expanded" else "Collapsed" }.testTag(tag).animateContentSize()
        .padding(horizontal = 10.dp, vertical = 6.dp)) {
        Row(Modifier.heightIn(min = 24.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Icon(icon, null, Modifier.size(14.dp), tint = ink)
            Text(label, color = ink, style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.SemiBold))
            Icon(if (open) ThreadIcons.ChevronUp else ThreadIcons.ChevronDown, null, Modifier.size(14.dp), tint = ink)
        }
        if (open) Text(text, Modifier.padding(top = 4.dp, bottom = 2.dp).testTag("$tag-text"), style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurface)
    }
}

/** A tinted line for something that needs attention, such as why a ticket is blocked. Short and colored, never a gray paragraph. */
@Composable
internal fun Callout(text: String, tone: OrgTone, modifier: Modifier = Modifier, icon: ImageVector = ThreadIcons.Waiting, title: String = "") {
    val (ink, fill) = orgColors(tone)
    val shape = RoundedCornerShape(12.dp)
    Row(modifier.fillMaxWidth().clip(shape).background(fill).padding(12.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        Icon(icon, null, Modifier.size(18.dp).padding(top = 1.dp), tint = ink)
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            if (title.isNotBlank()) Text(title, color = ink, style = MaterialTheme.typography.labelLarge)
            ClampedText(text, lines = 3, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurface)
        }
    }
}

/** A card with an icon and title on top, like the desktop's panels; [trailing] holds a small action. */
@Composable
internal fun DetailCard(title: String, modifier: Modifier = Modifier, icon: ImageVector? = null, tone: OrgTone? = null,
                        trailing: @Composable RowScope.() -> Unit = {}, content: @Composable ColumnScope.() -> Unit) {
    val shape = RoundedCornerShape(14.dp)
    val accent = tone?.let { orgColors(it).first } ?: MaterialTheme.colorScheme.onSurfaceVariant
    Surface(shape = shape, color = MaterialTheme.colorScheme.surface, modifier = modifier.fillMaxWidth().border(1.dp, MaterialTheme.colorScheme.outlineVariant, shape)) {
        Column(Modifier.padding(horizontal = 14.dp, vertical = 10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(Modifier.fillMaxWidth().heightIn(min = 32.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (icon != null) Icon(icon, null, Modifier.size(18.dp), tint = accent)
                Text(title, Modifier.weight(1f).semantics { heading() }, style = MaterialTheme.typography.titleSmall.copy(fontWeight = FontWeight.SemiBold))
                trailing()
            }
            content()
        }
    }
}

/**
 * Long text folded to its first sentence, with Show more for the rest. [full] draws the whole text when opened, such as
 * Markdown; the preview is always plain.
 */
@Composable
internal fun SummaryText(text: String, modifier: Modifier = Modifier, tag: String = "summaryText", full: @Composable () -> Unit = { Text(text, style = MaterialTheme.typography.bodyMedium) }) {
    var open by rememberSaveable(text) { mutableStateOf(false) }
    val more = remember(text) { ScanText.hasMore(text) }
    Column(modifier.fillMaxWidth().testTag(tag).animateContentSize()) {
        if (open || !more) full()
        else Text(ScanText.firstSentence(text), Modifier.testTag("$tag-preview"), style = MaterialTheme.typography.bodyMedium, maxLines = 3, overflow = TextOverflow.Ellipsis)
        if (more) Row(Modifier.heightIn(min = 40.dp).clickable(role = Role.Button) { open = !open }.semantics { stateDescription = if (open) "Expanded" else "Collapsed" }
            .testTag("$tag-toggle"), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            Icon(if (open) ThreadIcons.ChevronUp else ThreadIcons.ChevronDown, null, Modifier.size(16.dp), tint = MaterialTheme.colorScheme.primary)
            Text(if (open) "Show less" else "Show more", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary)
        }
    }
}

/**
 * Where a ticket is on its way across the board: one dot per step in [ScanText.STEPS], filled up to [column]. A blocked ticket
 * shows a red dot at [stoppedAt], the step it stopped in, when known.
 */
@Composable
internal fun StageStrip(column: String?, labels: Map<String, String>, modifier: Modifier = Modifier, stoppedAt: String? = null) {
    val blocked = column == "blocked"
    val at = ScanText.stepOf(if (blocked) stoppedAt else column)
    val tone = when { blocked -> OrgTone.BAD; column == "done" -> OrgTone.DONE; column == "review" -> OrgTone.WAIT; column == "running" -> OrgTone.RUN; else -> OrgTone.ACCENT }
    val lit = orgColors(tone).first
    val dim = MaterialTheme.colorScheme.outlineVariant
    val words = labels[column] ?: column.orEmpty()
    Column(modifier.fillMaxWidth().semantics(mergeDescendants = true) { contentDescription = "Stage: $words" }.testTag("stageStrip"),
        verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            ScanText.STEPS.forEachIndexed { i, _ ->
                val reached = at >= 0 && i <= at
                if (i > 0) Box(Modifier.weight(1f).height(2.dp).background(if (reached) lit else dim))
                Box(Modifier.size(if (i == at) 12.dp else 8.dp).clip(CircleShape).background(if (reached) lit else dim))
            }
        }
        Row(Modifier.fillMaxWidth()) {
            ScanText.STEPS.forEachIndexed { i, step ->
                Text(labels[step].orEmpty(), Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis, fontSize = 10.sp,
                    textAlign = when (i) { 0 -> androidx.compose.ui.text.style.TextAlign.Start; ScanText.STEPS.lastIndex -> androidx.compose.ui.text.style.TextAlign.End
                        else -> androidx.compose.ui.text.style.TextAlign.Center },
                    color = if (i == at) lit else MaterialTheme.colorScheme.onSurfaceVariant,
                    fontWeight = if (i == at) FontWeight.SemiBold else FontWeight.Normal)
            }
        }
    }
}

/** A tappable row for a related item: a status dot, an optional key, the title and its status in words. */
@Composable
internal fun LinkRow(title: String, status: String, tone: OrgTone, modifier: Modifier = Modifier, key: String = "", muted: Boolean = false,
                     onClick: (() -> Unit)? = null, trailing: @Composable RowScope.() -> Unit = {}) {
    val shape = RoundedCornerShape(10.dp)
    Row(modifier.fillMaxWidth().clip(shape).background(MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.6f))
        .then(if (onClick != null) Modifier.clickable(role = Role.Button, onClickLabel = "Open", onClick = onClick) else Modifier)
        .heightIn(min = 48.dp).padding(start = 10.dp, end = 4.dp, top = 6.dp, bottom = 6.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Box(Modifier.size(10.dp).clip(CircleShape).background(orgColors(tone).first).semantics { contentDescription = status })
        Column(Modifier.weight(1f)) {
            if (key.isNotBlank()) Text(key, style = MaterialTheme.typography.labelSmall.copy(fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace),
                color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1)
            Text(title, style = MaterialTheme.typography.bodyMedium, maxLines = 2, overflow = TextOverflow.Ellipsis,
                color = if (muted) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface)
        }
        Pill(status, tone)
        trailing()
        if (onClick != null) Icon(ThreadIcons.ChevronRight, null, Modifier.size(16.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/** The page's main actions, pinned under its scrolling content so they never scroll away. */
@Composable
internal fun ActionBar(modifier: Modifier = Modifier, content: @Composable RowScope.() -> Unit) {
    Surface(color = MaterialTheme.colorScheme.surface, modifier = modifier.fillMaxWidth().testTag("actionBar")) {
        Column {
            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
            Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp), content = content)
        }
    }
}

/** A subtle tinted circle behind an icon, used to give list rows and headers a visual anchor. */
@Composable
internal fun IconBadge(icon: ImageVector, tone: OrgTone, size: androidx.compose.ui.unit.Dp = 36.dp) {
    val (ink, fill) = orgColors(tone)
    Box(Modifier.size(size).clip(CircleShape).background(fill), contentAlignment = Alignment.Center) {
        Icon(icon, null, Modifier.size(size * 0.5f), tint = ink)
    }
}
