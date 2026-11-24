package dev.raticode.mobile.ui

import androidx.compose.foundation.text.InlineTextContent
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.Placeholder
import androidx.compose.ui.text.PlaceholderVerticalAlign
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.raticode.mobile.live.RemTicket
import org.commonmark.ext.gfm.tables.TableBlock
import org.commonmark.ext.gfm.tables.TableCell
import org.commonmark.ext.gfm.tables.TableRow
import org.commonmark.ext.gfm.tables.TablesExtension
import org.commonmark.node.Node
import org.commonmark.node.Paragraph
import org.commonmark.parser.Parser
import org.junit.Assert.assertEquals
import org.junit.Test

/** In a table cell a ticket key shows only the key, as a link, and its column is wide enough for it; pills stay elsewhere. */
class TicketTableTest {
    private val parser = Parser.builder().extensions(listOf(TablesExtension.create())).build()
    private val ticket = RemTicket("t137", "REM-137", "Read web page can render JavaScript pages", "done", "rem", "")
    private val other = RemTicket("t9", "AGE-9", "Agent eval", "in_progress", "organization", "org")
    private val tickets = mapOf(ticket.key to ticket, other.key to other)
    private val placeholder = Placeholder(10.sp, 10.sp, PlaceholderVerticalAlign.TextCenter)
    private val inline = tickets.keys.flatMap { listOf(ticketInlineId(it), ticketKeyInlineId(it)) }.associateWith { InlineTextContent(placeholder) {} }
    private val links = LinkContext(null, null, TextLinkStyles(), tickets = tickets, inline = inline,
        keyWidths = mapOf("REM-137" to 140.dp, "AGE-9" to 30.dp))

    private val markdown = listOf(
        "Landed REM-137 today.",
        "",
        "| Ticket | Win |",
        "| --- | --- |",
        "| REM-137 | **AGE-9** helped |",
        "| REM-404 | `REM-137` in code, xREM-137 and UTF-8 |",
        "| [REM-137](https://example.com) | plain |",
    ).joinToString("\n")

    private fun bodyRows(doc: Node): List<List<TableCell>> {
        val rows = mutableListOf<List<TableCell>>()
        fun visit(parent: Node) {
            var child = parent.firstChild
            while (child != null) {
                if (child is TableRow) rows.add(generateSequence(child.firstChild) { it.next }.filterIsInstance<TableCell>().toList()) else visit(child)
                child = child.next
            }
        }
        visit(doc)
        return rows
    }

    private fun AnnotatedString.inlineIds() =
        getStringAnnotations("androidx.compose.foundation.text.inlineContent", 0, length).map { text.substring(it.start, it.end) to it.item }

    @Test fun keysInTableCellsAreKeyLinksAndPillsStayOutsideTables() {
        val doc = parser.parse(markdown)
        val paragraph = doc.firstChild as Paragraph
        // Outside a table the key is still a full pill.
        assertEquals(listOf("REM-137" to "ticket:REM-137"), inlineMarkdown(paragraph, links).inlineIds())
        val rows = bodyRows(doc.firstChild.next as TableBlock)
        val cell = { row: Int, column: Int -> inlineMarkdown(rows[row][column], links.inTableCell()) }
        // In a cell, including inside emphasis, only the key shows, as the key link.
        assertEquals(listOf("REM-137" to "ticketKey:REM-137"), cell(1, 0).inlineIds())
        assertEquals("REM-137", cell(1, 0).text)
        assertEquals(listOf("AGE-9" to "ticketKey:AGE-9"), cell(1, 1).inlineIds())
        // Unknown keys, code, keys inside words and link text stay as written.
        assertEquals(emptyList<Pair<String, String>>(), cell(2, 0).inlineIds())
        assertEquals(emptyList<Pair<String, String>>(), cell(2, 1).inlineIds())
        assertEquals("REM-137 in code, xREM-137 and UTF-8", cell(2, 1).text)
        assertEquals(emptyList<Pair<String, String>>(), cell(3, 0).inlineIds())
    }

    @Test fun aColumnIsAtLeastAsWideAsTheKeyLinksInIt() {
        val rows = bodyRows(parser.parse(markdown))
        val widths = tableColumnWidths(rows, links.inTableCell())
        // REM-137's link is 140 dp here (a large font scale), so its column grows to fit it and its cell padding,
        // rather than the link drawing into the next column. The text estimate alone gives 7 * 8 + 24 = 80 dp.
        assertEquals(156.dp, widths[0])
        // A short key that fits the text estimate leaves the column at its longest text, capped at 260 dp.
        assertEquals(260.dp, widths[1])
        val plain = tableColumnWidths(rows, LinkContext(null, null, TextLinkStyles()).inTableCell())
        assertEquals(80.dp, plain[0])
    }
}
