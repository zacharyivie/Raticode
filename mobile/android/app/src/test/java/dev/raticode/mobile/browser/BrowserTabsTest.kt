package dev.raticode.mobile.browser

import dev.raticode.mobile.browser.BrowserTabs.Tab
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** One tab list for Raticode (chromium) and Raticode (gecko), the browser the page shows, and the names people see. */
class BrowserTabsTest {
    private val chromium = BrowserTabs.CHROMIUM
    private val gecko = BrowserTabs.GECKO
    private fun c(id: String, url: String = "https://$id.example/", private: Boolean = false, playing: Boolean = false) = Tab(chromium, id, url, id, private, playing)
    private fun g(id: String, url: String = "https://$id.example/", private: Boolean = false, playing: Boolean = false) = Tab(gecko, id, url, id, private, playing)

    @Test fun builtInBrowsersAreNamedForTheirEnginesAndStoredValuesStay() {
        assertEquals("Raticode (chromium)", BrowserTabs.name("raticode"))
        assertEquals("Raticode (gecko)", BrowserTabs.name("gecko"))
        assertEquals("Firefox", BrowserTabs.name("firefox"))
        assertEquals("Chrome", BrowserTabs.name("chrome"))
        assertEquals("Edge", BrowserTabs.name("edge"))
        assertEquals("Raticode (chromium)", BrowserTabs.name("netscape"))
        assertEquals("chromium", BrowserTabs.badge("raticode")); assertEquals("gecko", BrowserTabs.badge("gecko"))
        // Only the names changed: the saved values are the ones every earlier version wrote.
        assertEquals("raticode", BrowserRules.ENGINE_RATICODE); assertEquals("gecko", BrowserRules.ENGINE_GECKO)
    }

    @Test fun bothBrowsersTabsShareOneListInAFixedOrder() {
        val list = BrowserTabs.list(listOf(c("a"), c("b")), listOf(g("music"), g("docs")), showing = chromium, chromiumActive = "b", geckoActive = "music")
        assertEquals(listOf("raticode:a", "raticode:b", "gecko:music", "gecko:docs"), list.map { it.key })
        assertEquals("Only the tab on screen is current", listOf("raticode:b"), list.filter { it.current }.map { it.key })
        assertEquals(1, BrowserTabs.currentIndex(list))
        // Switching to the Gecko page keeps every tab in its place; only the current one moves.
        val onGecko = BrowserTabs.list(listOf(c("a"), c("b")), listOf(g("music"), g("docs")), showing = gecko, chromiumActive = "b", geckoActive = "music")
        assertEquals(list.map { it.key }, onGecko.map { it.key })
        assertEquals(listOf("gecko:music"), onGecko.filter { it.current }.map { it.key })
        assertEquals(2, BrowserTabs.currentIndex(onGecko))
        assertEquals(0, BrowserTabs.currentIndex(emptyList()))
    }

    @Test fun theOtherBrowsersUnusedBlankTabIsLeftOut() {
        val home = BrowserRules.HOME
        // Each browser always keeps a tab; a Gecko tab nobody used doesn't fill the Chromium page's list.
        val list = BrowserTabs.list(listOf(c("a")), listOf(g("blank", home)), showing = chromium, chromiumActive = "a", geckoActive = "blank")
        assertEquals(listOf("raticode:a"), list.map { it.key })
        // The blank tab on screen shows, and so do a private blank tab and one still playing in the other browser.
        val mine = BrowserTabs.list(listOf(c("new", home)), listOf(g("p", home, private = true), g("x", home, playing = true)), showing = chromium, chromiumActive = "new", geckoActive = "p")
        assertEquals(listOf("raticode:new", "gecko:p", "gecko:x"), mine.map { it.key })
        assertEquals("New tab", BrowserTabs.title(mine[0].tab))
    }

    @Test fun rowsSayPrivatePlayingAndCurrentBesideTheSite() {
        val list = BrowserTabs.list(listOf(c("a", private = true)), listOf(g("m", "https://www.music.example/play", playing = true)), showing = gecko,
            chromiumActive = "a", geckoActive = "m")
        assertEquals("Private · a.example", BrowserTabs.detail(list[0]))
        assertEquals("Playing · Current · music.example", BrowserTabs.detail(list[1]))
        assertNull(BrowserTabs.detail(BrowserTabs.Entry(c("n", BrowserRules.HOME), current = false)))
    }

    @Test fun pickingATabOfTheOtherBrowserShowsItWithoutChangingTheDefault() {
        val start = BrowserRules.Saved(engine = BrowserRules.ENGINE_RATICODE)
        assertEquals(chromium, BrowserRules.pageEngine(start))
        val onGecko = BrowserRules.showing(start, gecko)
        assertEquals(gecko, BrowserRules.pageEngine(onGecko)); assertEquals("The default stays", chromium, onGecko.engine)
        assertEquals("New tabs still open in the default", chromium, BrowserRules.newTabEngine(onGecko))
        // Back to a Chromium tab: showing the default is stored as nothing, so the page follows the default again.
        assertNull(BrowserRules.showing(onGecko, chromium).showing)
        // External apps can't hold tabs, so they're never "shown" this way.
        assertEquals(onGecko, BrowserRules.showing(onGecko, BrowserRules.ENGINE_FIREFOX))
        // Choosing a default browser shows it, whatever tab was on screen.
        val firefox = BrowserRules.withEngine(onGecko, BrowserRules.ENGINE_FIREFOX)
        assertEquals(BrowserRules.ENGINE_FIREFOX, firefox.engine); assertNull(firefox.showing); assertEquals(BrowserRules.ENGINE_FIREFOX, BrowserRules.pageEngine(firefox))
        assertEquals(chromium, BrowserRules.withEngine(onGecko, "netscape").engine)
    }

    @Test fun newTabsOpenInTheDefaultWhenItIsBuiltIn() {
        assertEquals(gecko, BrowserRules.newTabEngine(BrowserRules.Saved(engine = gecko)))
        assertEquals(chromium, BrowserRules.newTabEngine(BrowserRules.Saved(engine = chromium, showing = gecko)))
        // With Firefox or Chrome as the default, a new tab inside Raticode opens in the built-in browser on screen.
        assertEquals(gecko, BrowserRules.newTabEngine(BrowserRules.Saved(engine = BrowserRules.ENGINE_FIREFOX, showing = gecko)))
        assertEquals(chromium, BrowserRules.newTabEngine(BrowserRules.Saved(engine = ChromeApps.ENGINE)))
        assertTrue(BrowserRules.inApp(chromium)); assertTrue(BrowserRules.inApp(gecko))
        assertFalse(BrowserRules.inApp(BrowserRules.ENGINE_EDGE)); assertFalse(BrowserRules.inApp(null))
    }

    @Test fun theBrowserOnScreenIsSavedAndOlderSavesKeepTheirChoice() {
        // Saves from before the shared tab list have no "showing": the page shows the saved default, as before.
        for (engine in listOf("raticode", "gecko", "firefox", "edge", "chrome")) {
            val old = BrowserRules.decode("""{"v":1,"tabs":[],"engine":"$engine"}""")
            assertEquals(engine, old.engine); assertNull(old.showing); assertEquals(engine, BrowserRules.pageEngine(old))
        }
        val saved = BrowserRules.Saved(engine = BrowserRules.ENGINE_EDGE, showing = gecko)
        val read = BrowserRules.decode(BrowserRules.encode(saved))
        assertEquals(BrowserRules.ENGINE_EDGE, read.engine); assertEquals(gecko, read.showing); assertEquals(gecko, BrowserRules.pageEngine(read))
        // Showing the default isn't written; odd values read as nothing.
        assertFalse(JSONObject(BrowserRules.encode(BrowserRules.Saved(engine = gecko, showing = gecko))).has("showing"))
        assertFalse(JSONObject(BrowserRules.encode(BrowserRules.Saved())).has("showing"))
        for (odd in listOf("firefox", "chrome", "netscape", "", "raticode"))
            assertNull(odd, BrowserRules.decode("""{"engine":"raticode","showing":"$odd"}""").showing)
    }
}
