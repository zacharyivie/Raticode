package dev.raticode.mobile.ui

import android.content.ClipData
import android.content.ClipboardManager
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.*
import androidx.compose.foundation.text.appendInlineContent
import androidx.compose.ui.text.font.*
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.raticode.mobile.live.ChatLink
import dev.raticode.mobile.live.ChatLinks
import dev.raticode.mobile.live.CodeHighlight
import dev.raticode.mobile.live.LinkSource
import dev.raticode.mobile.live.RemChartSpec
import kotlinx.coroutines.delay
import org.commonmark.ext.gfm.strikethrough.Strikethrough
import org.commonmark.ext.gfm.strikethrough.StrikethroughExtension
import org.commonmark.ext.gfm.tables.TableBlock
import org.commonmark.ext.gfm.tables.TableCell
import org.commonmark.ext.gfm.tables.TableHead
import org.commonmark.ext.gfm.tables.TableRow
import org.commonmark.ext.gfm.tables.TablesExtension
import org.commonmark.ext.task.list.items.TaskListItemMarker
import org.commonmark.ext.task.list.items.TaskListItemsExtension
import org.commonmark.node.*
import org.commonmark.parser.Parser

/** GitHub-style Markdown, as the desktop renders it: tables, strikethrough and task lists. */
private val markdownParser = Parser.builder()
    .extensions(listOf(TablesExtension.create(), StrikethroughExtension.create(), TaskListItemsExtension.create()))
    .build()

/** Opens a tapped link: web addresses in the browser, file links by asking the desktop for the file. */
internal fun interface ChatLinkHandler { fun open(link: ChatLink, source: LinkSource?) }
internal val LocalChatLinks = staticCompositionLocalOf<ChatLinkHandler?> { null }

/**
 * [mentions] are full paths the desktop found in the text (longest first); they link exactly as written.
 * [tickets] are board tickets the desktop found by key; each key shows as the pill in [inline].
 */
internal class LinkContext(val handler: ChatLinkHandler?, val source: LinkSource?, val style: TextLinkStyles, val mentions: List<String> = emptyList(),
                           val tickets: Map<String, dev.raticode.mobile.live.RemTicket> = emptyMap(),
                           val inline: Map<String, androidx.compose.foundation.text.InlineTextContent> = emptyMap())

/**
 * CommonMark parsing avoids showing markup delimiters in thoughts and replies. No HTML execution.
 * [source] names the desktop message, so a tapped file link can be fetched from it. [mentions] are the full
 * paths of existing desktop files in the text, from the desktop; paths with spaces link only through them.
 */
/** Text sizes and block spacing for Markdown. Shared threads use desktop's thread sizes; Rem chat keeps its own. */
internal data class MarkdownStyle(val body: TextStyle, val compactBody: TextStyle, val spacing: Dp, val compactSpacing: Dp, val headings: List<TextStyle>,
                                   val listSpacing: Dp)

internal val LocalMarkdownStyle = staticCompositionLocalOf<MarkdownStyle?> { null }

/** Desktop thread Markdown: 14px text on 24px lines, 16px between blocks, 24/20/16/14/14/12px headings. */
@Composable
internal fun threadMarkdownStyle(): MarkdownStyle {
    val type = MaterialTheme.typography
    fun heading(size: Int, line: Int) = type.bodyMedium.copy(fontSize = size.sp, lineHeight = line.sp, fontWeight = FontWeight.SemiBold)
    return MarkdownStyle(type.bodyMedium.copy(lineHeight = 22.sp), type.bodySmall.copy(lineHeight = 20.sp), 14.dp, 8.dp,
        listOf(heading(24, 30), heading(20, 26), heading(16, 22), heading(14, 20), heading(14, 20), heading(12, 18)), 4.dp)
}

@Composable
private fun markdownStyle(): MarkdownStyle = LocalMarkdownStyle.current ?: MaterialTheme.typography.let { type ->
    MarkdownStyle(type.bodyLarge, type.bodySmall, 6.dp, 6.dp, listOf(type.titleLarge, type.titleMedium) + List(4) { type.titleSmall }, 6.dp)
}

@Composable
internal fun ChatMarkdown(value: String, compact: Boolean = false, source: LinkSource? = null, mentions: List<String> = emptyList()) {
    val document = remember(value) { markdownParser.parse(value) }
    val color = MaterialTheme.colorScheme.primary
    // Board ticket keys the desktop found on the board show as pills; Rem's chat provides them.
    val known = LocalRemTickets.current
    val named = remember(value, known) { dev.raticode.mobile.live.TicketMentions.keys(value).filter { it in known } }
    val tickets = remember(named, known) { named.associateWith { known.getValue(it) } }
    val links = LinkContext(LocalChatLinks.current, source,
        TextLinkStyles(SpanStyle(color = color, textDecoration = TextDecoration.Underline), pressedStyle = SpanStyle(color = color, background = color.copy(alpha = .14f))),
        mentions, tickets, ticketInlineContent(named, tickets))
    val style = markdownStyle()
    Column(verticalArrangement = Arrangement.spacedBy(if (compact) style.compactSpacing else style.spacing)) { MarkdownBlocks(document, compact, links) }
}

@Composable
private fun MarkdownBlocks(parent: Node, compact: Boolean, links: LinkContext) {
    val style = markdownStyle()
    val body = if (compact) style.compactBody else style.body
    var node = parent.firstChild
    while (node != null) {
        val block = node
        when (block) {
            is FencedCodeBlock -> {
                val language = CodeHighlight.language(block.info.orEmpty())
                val chart = if (language == "rem-chart") remember(block.literal) { RemChartSpec.parse(block.literal) } else null
                if (chart != null) RemChartView(chart) else CodeBlock(block.literal, language)
            }
            is IndentedCodeBlock -> CodeBlock(block.literal, "")
            is BulletList, is OrderedList -> {
                var child = block.firstChild
                var number = (block as? OrderedList)?.markerStartNumber ?: 1
                // Items sit closer together than blocks, like desktop's space-y-1 lists.
                Column(verticalArrangement = Arrangement.spacedBy(style.listSpacing)) { while (child != null) {
                    val task = child.firstChild as? TaskListItemMarker
                    val marker = when {
                        task != null -> if (task.isChecked) "☑ " else "☐ "
                        block is OrderedList -> "${number++}. "
                        else -> "• "
                    }
                    Row {
                        Text(marker, Modifier.alignByBaseline().semantics { if (task != null) contentDescription = if (task.isChecked) "Done" else "Not done" },
                            style = body)
                        Column(Modifier.weight(1f).alignByBaseline()) { MarkdownBlocks(child!!, compact, links) }
                    }
                    child = child.next
                } }
            }
            is TaskListItemMarker -> {}
            // The rule grows with the quote instead of stopping after one line.
            is BlockQuote -> Row(Modifier.height(IntrinsicSize.Min)) {
                Box(Modifier.width(2.dp).fillMaxHeight().background(MaterialTheme.colorScheme.outline))
                Column(Modifier.padding(start = 10.dp)) { MarkdownBlocks(block, compact, links) }
            }
            is ThematicBreak -> HorizontalDivider()
            is TableBlock -> MarkdownTable(block, links)
            is Heading -> Text(inlineMarkdown(block, links), Modifier.semantics { heading() },
                style = style.headings[(block.level - 1).coerceIn(0, style.headings.lastIndex)], inlineContent = links.inline)
            else -> {
                // Images render below their paragraph's text, in order, like desktop's inline images.
                val text = inlineMarkdown(block, links)
                if (text.isNotBlank() || markdownImages(block).isEmpty())
                    Text(text, style = body, inlineContent = links.inline)
                markdownImages(block).forEach { (url, alt) -> ChatImageView(url, alt) }
            }
        }
        node = block.next
    }
}

/** Token colors that keep at least 4.5:1 on the code background in light and dark themes. */
@Composable
internal fun codeTokenStyles(): Map<CodeHighlight.Kind, SpanStyle?> {
    val dark = MaterialTheme.colorScheme.surface.luminance() < .5f
    return CodeHighlight.Kind.entries.associateWith { kind ->
        when (kind) {
            CodeHighlight.Kind.KEYWORD -> SpanStyle(color = if (dark) Color(0xFFC4B5FD) else Color(0xFF6D28D9), fontWeight = FontWeight.SemiBold)
            CodeHighlight.Kind.STRING -> SpanStyle(color = if (dark) Color(0xFF5EEAD4) else Color(0xFF0F766E))
            CodeHighlight.Kind.NUMBER -> SpanStyle(color = if (dark) Color(0xFFFCD34D) else Color(0xFF92400E))
            CodeHighlight.Kind.COMMENT -> SpanStyle(color = if (dark) Color(0xFF94A3B8) else Color(0xFF475569), fontStyle = FontStyle.Italic)
            CodeHighlight.Kind.TAG -> SpanStyle(color = if (dark) Color(0xFFF9A8D4) else Color(0xFFBE185D))
            CodeHighlight.Kind.TEXT -> null
        }
    }
}

/** A code block that scrolls sideways instead of wrapping, with a Copy button like the desktop's. */
@Composable
private fun CodeBlock(value: String, language: String) {
    val code = value.trimEnd()
    val styles = codeTokenStyles()
    val text = remember(code, language, styles) {
        buildAnnotatedString {
            for (token in CodeHighlight.tokenize(code, language)) {
                val style = styles[token.kind]
                if (style == null) append(token.text) else withStyle(style) { append(token.text) }
            }
        }
    }
    val context = LocalContext.current
    var copied by remember { mutableStateOf(false) }
    LaunchedEffect(copied) { if (copied) { delay(2000); copied = false } }
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp)).border(1.dp, MaterialTheme.colorScheme.outlineVariant, RoundedCornerShape(8.dp))
        .background(MaterialTheme.colorScheme.surface)) {
        Text(text, Modifier.horizontalScroll(rememberScrollState()).padding(horizontal = 10.dp, vertical = 8.dp),
            fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall, softWrap = false)
        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
        TextButton(onClick = {
            context.getSystemService(ClipboardManager::class.java)?.setPrimaryClip(ClipData.newPlainText("Code", code))
            copied = true
        }, Modifier.align(Alignment.End).semantics { liveRegion = LiveRegionMode.Polite }) {
            Text(if (copied) "Copied" else "Copy code", style = MaterialTheme.typography.labelMedium)
        }
    }
}

/** A Markdown table that scrolls sideways; columns size to their longest cell within limits. */
@Composable
private fun MarkdownTable(table: TableBlock, links: LinkContext) {
    val rows = remember(table) {
        val found = mutableListOf<Pair<Boolean, List<TableCell>>>()
        fun collect(parent: Node, header: Boolean) {
            var child = parent.firstChild
            while (child != null) {
                when (child) {
                    is TableHead -> collect(child, true)
                    is TableRow -> found.add(header to generateSequence(child.firstChild) { it.next }.filterIsInstance<TableCell>().toList())
                    else -> collect(child, header)
                }
                child = child.next
            }
        }
        collect(table, false)
        found.toList()
    }
    val columns = rows.maxOfOrNull { it.second.size } ?: 0
    val widths = remember(rows) {
        (0 until columns).map { column ->
            val longest = rows.maxOf { (_, cells) -> cells.getOrNull(column)?.let(::plainLength) ?: 0 }
            (longest * 8 + 24).coerceIn(64, 260).dp
        }
    }
    val line = MaterialTheme.colorScheme.outlineVariant
    Column(Modifier.horizontalScroll(rememberScrollState()).border(1.dp, line, RoundedCornerShape(6.dp)).clip(RoundedCornerShape(6.dp))) {
        rows.forEachIndexed { index, (header, cells) ->
            Row(Modifier.background(if (header) MaterialTheme.colorScheme.surfaceVariant else Color.Transparent)
                .then(if (header) Modifier.semantics { heading() } else Modifier)) {
                for (column in 0 until columns) {
                    Text(cells.getOrNull(column)?.let { inlineMarkdown(it, links) } ?: AnnotatedString(""),
                        Modifier.width(widths[column]).padding(horizontal = 8.dp, vertical = 6.dp),
                        style = MaterialTheme.typography.bodyMedium, fontWeight = if (header) FontWeight.SemiBold else null, inlineContent = links.inline)
                }
            }
            if (index < rows.lastIndex) HorizontalDivider(color = line)
        }
    }
}

private fun plainLength(node: Node): Int {
    var length = 0
    fun visit(parent: Node) {
        var child = parent.firstChild
        while (child != null) {
            when (child) { is org.commonmark.node.Text -> length += child.literal.length; is Code -> length += child.literal.length; else -> visit(child) }
            child = child.next
        }
    }
    visit(node)
    return length
}

internal fun inlineMarkdown(node: Node, links: LinkContext): AnnotatedString = buildAnnotatedString {
    fun linked(link: ChatLink?, body: () -> Unit) {
        val annotation = when {
            link == null -> null
            // Without a handler (tests, previews), web links keep Compose's default opener.
            links.handler == null -> (link as? ChatLink.Web)?.let { LinkAnnotation.Url(it.url, links.style) }
            // A file link needs the message it came from; elsewhere it is shown but not fetched.
            link is ChatLink.File && links.source == null -> null
            else -> LinkAnnotation.Clickable(if (link is ChatLink.Web) link.url else (link as ChatLink.File).destination, links.style) {
                links.handler.open(link, links.source)
            }
        }
        if (annotation != null) withLink(annotation) { body() }
        else if (link is ChatLink.File) withStyle(SpanStyle(fontFamily = FontFamily.Monospace)) { body() }
        else body()
    }
    fun linkedText(value: String) {
        var at = 0
        for ((range, link) in ChatLinks.inText(value, links.mentions)) {
            append(value.substring(at, range.first))
            linked(link) { append(value.substring(range.first, range.last + 1)) }
            at = range.last + 1
        }
        append(value.substring(at))
    }
    // A known ticket key becomes its pill; the alternate text is what Copy and TalkBack's text get.
    fun text(value: String) {
        var at = 0
        for ((range, ticket) in dev.raticode.mobile.live.TicketMentions.inText(value, links.tickets)) {
            if (ticketInlineId(ticket.key) !in links.inline) continue
            linkedText(value.substring(at, range.first))
            appendInlineContent(ticketInlineId(ticket.key), ticket.key)
            at = range.last + 1
        }
        linkedText(value.substring(at))
    }
    fun visit(parent: Node) {
        var child = parent.firstChild
        while (child != null) {
            val current = child
            when (current) {
                is org.commonmark.node.Text -> text(current.literal)
                is SoftLineBreak, is HardLineBreak -> append("\n")
                // Code that is exactly a path or address links, keeping its code style; other code stays text.
                is Code -> ChatLinks.inCode(current.literal, links.mentions).let { link ->
                    if (link == null) withStyle(SpanStyle(fontFamily = FontFamily.Monospace)) { append(current.literal) }
                    else linked(link) { withStyle(SpanStyle(fontFamily = FontFamily.Monospace)) { append(current.literal) } }
                }
                is StrongEmphasis -> withStyle(SpanStyle(fontWeight = FontWeight.Bold)) { visit(current) }
                is Emphasis -> withStyle(SpanStyle(fontStyle = FontStyle.Italic)) { visit(current) }
                is Strikethrough -> withStyle(SpanStyle(textDecoration = TextDecoration.LineThrough)) { visit(current) }
                is Link -> {
                    val link = ChatLinks.classify(current.destination)
                    // Text inside a link is not linked again, and an empty label shows the address.
                    if (link == null) visit(current)
                    else linked(link) { if (current.firstChild == null) append(current.destination) else visitPlain(current) }
                }
                is Image, is TaskListItemMarker -> {}
                is HtmlInline -> append(current.literal)
                else -> visit(current)
            }
            child = current.next
        }
    }
    visit(node)
}

/** Appends a link's label with its emphasis but without autolinking text inside it. */
private fun AnnotatedString.Builder.visitPlain(parent: Node) {
    var child = parent.firstChild
    while (child != null) {
        val current = child
        when (current) {
            is org.commonmark.node.Text -> append(current.literal)
            is SoftLineBreak, is HardLineBreak -> append(" ")
            is Code -> withStyle(SpanStyle(fontFamily = FontFamily.Monospace)) { append(current.literal) }
            is StrongEmphasis -> withStyle(SpanStyle(fontWeight = FontWeight.Bold)) { visitPlain(current) }
            is Emphasis -> withStyle(SpanStyle(fontStyle = FontStyle.Italic)) { visitPlain(current) }
            is Strikethrough -> withStyle(SpanStyle(textDecoration = TextDecoration.LineThrough)) { visitPlain(current) }
            is Image -> {}
            is HtmlInline -> append(current.literal)
            else -> visitPlain(current)
        }
        child = current.next
    }
}

/** Images in a block as (address, alt text). */
internal fun markdownImages(node: Node): List<Pair<String, String>> {
    val found = mutableListOf<Pair<String, String>>()
    fun visit(parent: Node) {
        var child = parent.firstChild
        while (child != null) {
            if (child is Image) found.add(child.destination to altText(child)) else visit(child)
            child = child.next
        }
    }
    visit(node)
    return found
}

private fun altText(image: Image): String = buildString {
    fun visit(parent: Node) {
        var child = parent.firstChild
        while (child != null) { if (child is org.commonmark.node.Text) append(child.literal) else visit(child); child = child.next }
    }
    visit(image)
}
