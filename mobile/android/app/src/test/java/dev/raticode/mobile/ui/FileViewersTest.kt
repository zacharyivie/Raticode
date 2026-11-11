package dev.raticode.mobile.ui

import dev.raticode.mobile.live.CodeHighlight
import dev.raticode.mobile.live.ExplorerEntry
import org.junit.Assert.*
import org.junit.Test

/** The explorer's text view and row details, without a screen. */
class FileViewersTest {
    @Test fun linesSplitLikeAnEditorShowsThem() {
        assertEquals(listOf("a", "b", ""), CodeLines.split("a\r\nb\n\n"))
        assertEquals(listOf(""), CodeLines.split(""))
        assertEquals(listOf(1, 3), CodeLines.matches(listOf("x", "Foo", "y", "a foo"), "FOO"))
        assertEquals(emptyList<Int>(), CodeLines.matches(listOf("x"), " "))
    }

    @Test fun aSelectionIsMatchedToItsLines() {
        val lines = listOf("fun a() {", "    val x = 1", "", "    return x", "}", "val x = 1")
        // One line, wherever the word sits on it.
        assertEquals(4..4, CodeLines.selectedLines(lines, "return x"))
        // Several lines: the end of the first, the whole of the middle, the start of the last, with the blank line left out.
        assertEquals(1..4, CodeLines.selectedLines(lines, "a() {\n    val x = 1\n    return"))
        // A word on several lines names them only when the long-pressed line settles it.
        assertNull(CodeLines.selectedLines(lines, "val x"))
        assertEquals(2..2, CodeLines.selectedLines(lines, "val x", near = 2))
        assertEquals(6..6, CodeLines.selectedLines(lines, "val x", near = 6))
        assertNull(CodeLines.selectedLines(lines, "val x", near = 5))
        // Everything, as Select all gives it.
        assertEquals(1..6, CodeLines.selectedLines(lines, lines.filter { it.isNotEmpty() }.joinToString("\n")))
        // Text the file doesn't hold, such as a rendered preview, names no lines.
        assertNull(CodeLines.selectedLines(lines, "not in this file"))
        assertNull(CodeLines.selectedLines(lines, ""))
    }

    @Test fun aCommentSpanningLinesKeepsItsColorOnEachLine() {
        val lines = listOf("val a = 1 /* starts", "still comment */ val b = \"s\"")
        val tokens = CodeLines.highlight(lines, 0, "kt")
        assertEquals(2, tokens.size)
        assertEquals(CodeHighlight.Kind.COMMENT, tokens[0].last().kind)
        assertEquals("still comment */", tokens[1].first().text)
        assertEquals(CodeHighlight.Kind.COMMENT, tokens[1].first().kind)
        assertTrue(tokens[1].any { it.kind == CodeHighlight.Kind.STRING && it.text == "\"s\"" })
        // Every character stays, in order.
        assertEquals(lines, tokens.map { line -> line.joinToString("") { it.text } })
        // Lines are highlighted in blocks, so a long file costs only what is on screen.
        val many = List(CodeLines.BLOCK * 2 + 5) { "x$it" }
        assertEquals(CodeLines.BLOCK, CodeLines.highlight(many, 0, "kt").size)
        assertEquals(5, CodeLines.highlight(many, 2, "kt").size)
        assertEquals("x${CodeLines.BLOCK * 2 + 4}", CodeLines.highlight(many, 2, null)[4].single().text)
    }

    @Test fun rowsSayWhatEachEntryIs() {
        val now = java.time.Instant.parse("2026-11-06T18:00:00Z").toEpochMilli()
        val time = "2026-11-03T08:00:00Z"
        assertTrue(entryDetail(ExplorerEntry("src", "folder", null, time, false), now).startsWith("Folder · "))
        assertTrue(entryDetail(ExplorerEntry("a.kt", "file", 2048, time, false), now).startsWith("2 KB · "))
        assertEquals("May hold passwords or keys · stays on your computer", entryDetail(ExplorerEntry(".env", "file", 5, time, true), now))
        assertEquals("Link · the phone doesn't follow links", entryDetail(ExplorerEntry("x", "link", null, time, false), now))
        assertEquals("Not a regular file", entryDetail(ExplorerEntry("fifo", "other", null, time, false), now))
        // A big video can't come to the phone; a big log still shows its start.
        assertEquals("45.0 MB · too large for the phone", entryDetail(ExplorerEntry("clip.mp4", "file", 45L * 1048576, time, false), now))
        assertFalse(entryDetail(ExplorerEntry("big.log", "file", 45L * 1048576, time, false), now).contains("too large"))
    }

    @Test fun svgIsPlacedInAPageThatFitsItToTheScreen() {
        val page = svgPage("<?xml version=\"1.0\"?>\n<!DOCTYPE svg PUBLIC \"-//W3C//DTD SVG 1.1//EN\" \"x.dtd\">\n<svg xmlns='http://www.w3.org/2000/svg'><circle r='5'/></svg>")
        assertTrue(page.contains("<body>\n\n<svg xmlns='http://www.w3.org/2000/svg'><circle r='5'/></svg></body>"))
        assertFalse(page.contains("<?xml") || page.contains("DOCTYPE svg"))
        assertTrue(page.contains("svg{display:block;margin:0 auto;max-width:100%;height:auto}"))
    }
}
