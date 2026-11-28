package dev.raticode.mobile.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** The color the preview's web view sits on, read from a page's CSS without running it. */
class PageBackdropTest {
    private fun page(css: String) = "<!DOCTYPE html><html><head><style>$css</style></head><body><p>Report</p></body></html>"

    @Test fun aDarkReportWithAGradientBackgroundGetsItsDarkColor() {
        // The shape of Rem's 2026-11-27 synthwave report, which showed light text on white on a phone: the base color
        // is a gradient through custom properties, and the print styles set a white page later in the file.
        val css = ":root{--void:#0B0620;--night:#140838;--text:#F4ECFF}" +
            "body{margin:0;background:linear-gradient(180deg,var(--night),var(--void) 420px);color:var(--text)}" +
            ".panel{background:#fff}" +
            "@media print{@page{margin:16mm}body{background:#fff;color:#1A0B3D}}"
        assertEquals(0xFF140838.toInt(), PageBackdrop.color(page(css)))
    }

    @Test fun htmlOutranksBodyAndLaterRulesWin() {
        assertEquals(0xFF112233.toInt(), PageBackdrop.color(page("body{background:#445566}html{background-color:#123}")))
        assertEquals(0xFF000000.toInt(), PageBackdrop.color(page("body{background:#fff}body{background:black}")))
        assertEquals(0xFF0A0B0C.toInt(), PageBackdrop.color(page("html, body { background: rgb(10, 11, 12) }")))
    }

    @Test fun pagesWithoutAReadableBaseColorKeepWhite() {
        assertNull(PageBackdrop.color("<html><body style=\"background:#000\">x</body></html>"))
        assertNull(PageBackdrop.color(page(".hero{background:#000}")))
        assertNull(PageBackdrop.color(page("body{background:url(sky.png)}")))
        // See-through colors and dark-mode overrides don't name what the page shows.
        assertNull(PageBackdrop.color(page("body{background:rgba(0,0,0,.5)}")))
        assertNull(PageBackdrop.color(page("@media (prefers-color-scheme:dark){body{background:#000}}")))
        assertNull(PageBackdrop.color(page("body{background:var(--missing)}")))
    }

    @Test fun onlyTopLevelRulesAreRead() {
        val rules = PageBackdrop.topLevelRules("@import x;a{b:c}@media print{d{e:f}}g{h:i}")
        assertEquals(listOf("a" to "b:c", "g" to "h:i"), rules)
    }

    @Test fun styleTagsAndCommentsAreFoundAsARegexWouldFindThem() {
        // Upper case and attributes on the tag, space before the closing >, several blocks, a comment across blocks.
        assertEquals(0xFF123456.toInt(), PageBackdrop.color("<STYLE media=\"all\">body{background:#123456}</Style >"))
        assertEquals(0xFF000000.toInt(), PageBackdrop.color("<style>body{background:#fff}/*</style><style>*/</style><style>body{background:black}</style>"))
        // <styles> isn't a style tag, and a closing tag that isn't one keeps the block open.
        assertNull(PageBackdrop.color("<styles>body{background:#000}</styles>"))
        assertEquals(0xFF000000.toInt(), PageBackdrop.color("<style>body{background:#fff}</stylex>;body{background:#000}</style>"))
        // A block that never closes, and an unclosed comment, leave the page as the blocks before them say.
        assertEquals(0xFF112233.toInt(), PageBackdrop.color("<style>body{background:#123}</style><style>body{background:#000}"))
        assertEquals(0xFF112233.toInt(), PageBackdrop.color(page("body{background:#123}/* body{background:#000}")))
        // Custom properties are found anywhere in a rule, later ones win, and a dash run without a colon is skipped.
        assertEquals(0xFF0A0B0C.toInt(), PageBackdrop.color(page(":root{x---y;--a:#fff;--a : rgb(10,11,12)}body{background:var(--a)}")))
        assertNull(PageBackdrop.color(page(":root{--a:;--b}body{background:var(--a)}")))
    }

    // Each of these took from seconds to minutes before the scans were linear, on the UI thread of the phone.
    @Test(timeout = 1_000) fun manyUnclosedStyleTagsAreReadQuickly() {
        assertNull(PageBackdrop.color("<style>".repeat(40_000)))
    }

    @Test(timeout = 1_000) fun aLongRunOfDashesInARuleIsReadQuickly() {
        assertEquals(0xFF112233.toInt(), PageBackdrop.color(page("body{background:#123;" + "-".repeat(200_000) + "}")))
    }

    @Test(timeout = 1_000) fun manyUnclosedCommentsAndColorsAreReadQuickly() {
        assertNull(PageBackdrop.color(page("/* ".repeat(60_000))))
        assertNull(PageBackdrop.color(page("body{background:" + "rgb( var(--a ".repeat(15_000) + "}")))
    }

    @Test(timeout = 1_000) fun customPropertiesThatNameEachOtherManyTimesAreReadQuickly() {
        // Each value names the next property hundreds of times and none ends in a color.
        val chain = (0 until 5).joinToString(";") { level -> "--p$level:" + "var(--p${level + 1}) ".repeat(800) }
        assertNull(PageBackdrop.color(page(":root{$chain;--p5:none}body{background:var(--p0)}")))
    }
}
