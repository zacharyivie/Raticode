package dev.raticode.mobile.live

import dev.raticode.mobile.ui.computerTitle
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** The computer's browser tabs as the desktop sends them, and pages sent to it. */
class DesktopBrowserTest {
    private fun answer() = JSONObject("""{"available":true,"stale":false,"pending":1,"search_url":"https://duckduckgo.com/?q={query}","tabs":[
        {"tab_id":"browser:1","title":"Docs","url":"https://example.com/docs","owner":"you","active":true},
        {"tab_id":"browser:2","title":"Issue","url":"https://github.com/a/b/issues/1?q=a|b","owner":"rem","label":"Rem","active":false},
        {"tab_id":"browser:3","title":"Local","url":"file:///etc/passwd","owner":"you"},
        {"tab_id":"browser:4","title":"Script","url":"javascript:alert(1)","owner":"you"}],"digest":"abc"}""")

    @Test fun readsTheComputersWebTabsOnly() {
        val view = DesktopViews.parse(DesktopBrowser.TABS, answer()) as DesktopBrowser.View
        assertTrue(view.available); assertFalse(view.stale); assertEquals(1, view.pending)
        assertEquals(listOf("browser:1", "browser:2"), view.tabs.map { it.id })
        assertTrue(view.tabs[1].rem); assertTrue(view.tabs[0].active)
        assertEquals("https://duckduckgo.com/?q={query}", view.searchUrl)
        val gone = DesktopBrowser.parse(JSONObject("""{"available":false,"detail":"Open Raticode on your computer.","tabs":[],"search_url":"javascript:{query}"}"""))
        assertFalse(gone.available); assertEquals("", gone.searchUrl); assertEquals("Open Raticode on your computer.", gone.detail)
    }

    @Test fun saysWhenTheComputersTabsRunInFirefox() {
        assertTrue(DesktopBrowser.parse(answer().put("engine", "firefox")).firefox)
        assertFalse(DesktopBrowser.parse(answer()).firefox)
        assertFalse(DesktopBrowser.parse(answer().put("engine", "gecko")).firefox)
    }

    @Test fun saysWhenTheComputersTabsRunInEdge() {
        val edge = DesktopBrowser.parse(answer().put("engine", "edge"))
        assertTrue(edge.edge); assertFalse(edge.firefox); assertEquals("edge", edge.engine)
        assertFalse(DesktopBrowser.parse(answer()).edge)
        assertFalse(DesktopBrowser.parse(answer().put("engine", "firefox")).edge)
        for (odd in listOf("Edge", "msedge", "chromium")) assertEquals(odd, "raticode", DesktopBrowser.parse(answer().put("engine", odd)).engine)
    }

    @Test fun desktopSettingsOfferEveryEngineTheComputerLists() {
        // The Web browser switch shows the desktop's own choices, so a desktop that accepts Edge offers it and an older one doesn't.
        val settings = DesktopViews.parse("settings.get", JSONObject("""{"available":true,"pending":0,"values":{"browser.engine":"edge"},
            "settings":[{"key":"browser.engine","group":"Browser","label":"Web browser","kind":"choice",
            "options":[["raticode","Raticode"],["firefox","Firefox"],["edge","Microsoft Edge"]]}]}""")) as DesktopSettings
        assertEquals("edge", settings.values["browser.engine"])
        assertEquals(listOf("raticode", "firefox", "edge"), settings.items.single().options.map { it.first })
        assertEquals("Microsoft Edge", settings.items.single().options.last().second)
    }

    @Test fun saysWhenTheComputersTabsRunInChromeAndUnknownEnginesMeanRaticode() {
        val chrome = DesktopBrowser.parse(answer().put("engine", "chrome"))
        assertEquals("chrome", chrome.engine); assertFalse(chrome.firefox)
        assertEquals("Chrome", DesktopBrowser.NAMES[chrome.engine])
        assertEquals("firefox", DesktopBrowser.parse(answer().put("engine", "firefox")).engine)
        // An older desktop says nothing; a value this phone doesn't know yet reads as Raticode's own engine.
        for (engine in listOf(null, "", "raticode", "Chrome", "chromium", "gecko")) {
            val json = answer().also { if (engine != null) it.put("engine", engine) }
            assertEquals(engine.toString(), "raticode", DesktopBrowser.parse(json).engine)
        }
        assertEquals(null, DesktopBrowser.NAMES["raticode"])
        // Desktop settings' Web browser switch lists Chrome when the computer offers it, and sends it like any other choice.
        val settings = DesktopViews.parse("settings.get", JSONObject("""{"available":true,"pending":0,"values":{"browser.engine":"chrome"},
            "settings":[{"key":"browser.engine","group":"Browser","label":"Web browser","kind":"choice",
            "options":[["raticode","Raticode"],["firefox","Firefox"],["chrome","Chrome"],["edge","Microsoft Edge"]]}]}""")) as DesktopSettings
        assertEquals("chrome", settings.values["browser.engine"])
        assertEquals("chrome" to "Chrome", settings.items.single().options[2])
        assertEquals("settings.get", DesktopViews.key("settings.update", JSONObject().put("changes", JSONObject().put("browser.engine", "chrome"))))
    }

    @Test fun eachComputerTabNamesTheBrowserItRunsIn() {
        val json = answer().put("engine", "firefox")
        val list = json.getJSONArray("tabs")
        list.getJSONObject(0).put("engine", "gecko")
        list.getJSONObject(1).put("engine", "chrome")
        val view = DesktopBrowser.parse(json)
        assertEquals(listOf("gecko", "chrome"), view.tabs.map { it.engine })
        assertEquals(listOf("Raticode (gecko)", "Chrome"), view.tabs.map(DesktopBrowser::engineName))
        assertTrue(view.perTabEngines)
        // The top-level engine stays the computer's default browser for tabs Rem and this phone open there.
        assertEquals("firefox", view.engine)
        // Tabs from several browsers share the list, so its title names none of them; each tab's badge does.
        assertEquals("On your computer", computerTitle(view))
        for ((engine, name) in listOf("raticode" to "Raticode (chromium)", "gecko" to "Raticode (gecko)", "firefox" to "Firefox", "chrome" to "Chrome", "edge" to "Edge")) {
            val one = answer().also { it.getJSONArray("tabs").getJSONObject(0).put("engine", engine) }
            assertEquals(name, DesktopBrowser.engineName(DesktopBrowser.parse(one).tabs[0]))
        }
        // A value this phone doesn't know yet reads as Raticode (chromium), whatever the computer's default.
        for (odd in listOf("Gecko", "chromium", "netscape")) {
            val one = answer().put("engine", "edge").also { it.getJSONArray("tabs").getJSONObject(0).put("engine", odd) }
            assertEquals(odd, "raticode", DesktopBrowser.parse(one).tabs[0].engine)
        }
    }

    @Test fun anOlderComputersTabsTakeItsOneBrowser() {
        // No per-tab field: every tab runs in the top-level browser, and the list keeps its old title.
        val firefox = DesktopBrowser.parse(answer().put("engine", "firefox"))
        assertEquals(listOf("firefox", "firefox"), firefox.tabs.map { it.engine })
        assertEquals(listOf("Firefox", "Firefox"), firefox.tabs.map(DesktopBrowser::engineName))
        assertFalse(firefox.perTabEngines)
        assertEquals("Firefox on your computer", computerTitle(firefox))
        val plain = DesktopBrowser.parse(answer())
        assertEquals(listOf("Raticode (chromium)", "Raticode (chromium)"), plain.tabs.map(DesktopBrowser::engineName))
        assertEquals("On your computer", computerTitle(plain))
    }

    @Test fun opsAreKnownAndKeyedByName() {
        assertTrue(DesktopBrowser.TABS in DesktopViews.READS)
        assertTrue(DesktopBrowser.OPEN in DesktopViews.ACTIONS)
        assertEquals(DesktopBrowser.OPEN, DesktopViews.key(DesktopBrowser.OPEN, JSONObject().put("url", "https://e.com")))
        assertEquals("Sent.", (DesktopViews.parse(DesktopBrowser.OPEN, JSONObject().put("message", "Sent.")) as DesktopBrowser.Sent).message)
        assertTrue((DesktopViews.parse("overview", JSONObject().put("browser", true)) as DesktopOverview).browser)
        assertFalse((DesktopViews.parse("overview", JSONObject()) as DesktopOverview).browser)
    }

    @Test fun webAddressesMatchTheDesktopsRule() {
        for (ok in listOf("https://example.com", "http://192.168.1.2:5173/x", "https://[::1]:8443/", "https://e.com/?q=a|b"))
            assertTrue(ok, BrowserAddress.isWeb(ok))
        for (bad in listOf("javascript:alert(1)", "file:///x", "https://", "https://u:p@e.com/", "https://e.com:0/", "https://e.com:99999/",
                "https://e.com/a b", "https://e.com/\nx", "ftp://e.com/", "https://" + "a".repeat(2050)))
            assertFalse(bad, BrowserAddress.isWeb(bad))
    }

    @Test fun reconnectingDropsQueuedTabReadsButKeepsSentPages() {
        fun event(type: String, op: String) = JSONObject().put("type", type).put("payload", JSONObject().put("op", op)).toString()
        val state = JSONObject().put("outbox", JSONArray(listOf(event("desktop.read", DesktopBrowser.TABS), event("desktop.action", DesktopBrowser.OPEN),
            event("desktop.read", "rem.home"))))
        assertTrue(DesktopBrowser.dropQueuedReads(state))
        assertEquals(2, state.getJSONArray("outbox").length())
        assertFalse(DesktopBrowser.dropQueuedReads(state))
    }
}
