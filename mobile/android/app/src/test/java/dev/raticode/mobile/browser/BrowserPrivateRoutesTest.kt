package dev.raticode.mobile.browser

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/** Reads how the long-press menus are wired: everything a private tab opens from them has to stay private. */
class BrowserPrivateRoutesTest {
    private val sources = listOf("src/main/java", "app/src/main/java").map(::File).first { it.isDirectory }
    private fun function(file: String, start: String): String {
        val text = sources.walk().first { it.name == file }.readText()
        val from = text.indexOf(start).also { assertTrue("$start is in $file", it >= 0) }
        return text.substring(from, text.indexOf("\n}\n", from))
    }

    @Test fun theWebViewLinkMenuKeepsPrivateTabsPrivate() {
        val menu = function("BrowserPage.kt", "private fun LinkMenuSheet(")
        assertFalse("Opens only through openFrom", menu.contains("browser.newTab("))
        assertEquals(3, Regex("browser\\.openFrom\\(menu\\.tab,").findAll(menu).count())
        assertTrue("Open link on your computer is hidden", menu.lineSequence().first { "val canSend" in it }.contains("!browser.isPrivate(menu.tab)"))
    }

    @Test fun theGeckoLinkMenuKeepsPrivateTabsPrivate() {
        val menu = function("GeckoPage.kt", "private fun LinkMenu(")
        assertFalse("Opens only through openFrom", menu.contains("gecko.newTab("))
        assertEquals(3, Regex("gecko\\.openFrom\\(menu\\.tab,").findAll(menu).count())
    }

    @Test fun openFromCarriesThePrivateFlag() {
        for (file in listOf("BrowserRuntime.kt", "GeckoBrowser.kt")) {
            val text = sources.walk().first { it.name == file }.readText()
            assertTrue(file, text.contains("fun openFrom(from: String, url: String, background: Boolean = false) = newTab(url, background, private = isPrivate(from))"))
        }
    }

    @Test fun thePagePromptNamesWhatWasAskedFor() {
        assertEquals("camera and microphone", BrowserRules.mediaAsk(camera = true, microphone = true))
        assertEquals("camera", BrowserRules.mediaAsk(camera = true, microphone = false))
        assertEquals("microphone", BrowserRules.mediaAsk(camera = false, microphone = true))
    }
}
