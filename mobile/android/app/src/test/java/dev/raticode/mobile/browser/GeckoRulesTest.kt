package dev.raticode.mobile.browser

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** What Raticode's Gecko browser saves and what it may install. */
class GeckoRulesTest {
    /** A page, even one in a background tab, can't bury the browser in dialogs Back doesn't close. */
    @Test fun onlyTheTabOnScreenAsksAndOnlyAFewQuestionsWait() {
        assertTrue(GeckoRules.mayAsk(activeTab = true, waiting = 0))
        assertTrue(GeckoRules.mayAsk(activeTab = true, waiting = GeckoRules.MAX_WAITING_ASKS - 1))
        assertFalse(GeckoRules.mayAsk(activeTab = true, waiting = GeckoRules.MAX_WAITING_ASKS))
        assertFalse(GeckoRules.mayAsk(activeTab = false, waiting = 0))
    }

    @Test fun savedStateRoundTripsAndKeepsProfilesApart() {
        val work = GeckoRules.Profile("w1", "Work")
        val saved = GeckoRules.Saved(
            tabs = listOf(GeckoRules.Tab("a", "https://example.com/", "Example", GeckoRules.DEFAULT_PROFILE), GeckoRules.Tab("b", BrowserRules.HOME, "", "w1")),
            active = "b", profiles = listOf(GeckoRules.Profile(GeckoRules.DEFAULT_PROFILE, "Personal"), work), currentProfile = "w1",
            bookmarks = listOf(GeckoRules.Link("https://a.test/", "A", "w1")), history = listOf(GeckoRules.Link("https://b.test/", "B")),
            settings = GeckoRules.Settings(tracking = GeckoRules.TRACKING_STRICT, colors = GeckoRules.COLORS_DARK, keepHistory = false, desktopSites = true))
        assertEquals(saved, GeckoRules.decode(GeckoRules.encode(saved)))
        assertTrue(GeckoRules.contextId("w1") != GeckoRules.contextId(GeckoRules.DEFAULT_PROFILE))
    }

    @Test fun unreadableOrUnsafeStateFallsBack() {
        assertEquals(GeckoRules.Saved(), GeckoRules.decode("not json"))
        val odd = GeckoRules.decode("""{"tabs":[{"id":"a","url":"javascript:alert(1)","profile":"gone"}],"profiles":[{"id":"x","name":"X"}],
            "bookmarks":[{"url":"file:///etc/passwd","title":"no"},{"url":"https://ok.test/","title":"yes"}],"settings":{"tracking":"none","colors":"neon","search":"javascript:{query}"}}""")
        assertEquals(BrowserRules.HOME, odd.tabs.single().url)
        assertEquals(GeckoRules.DEFAULT_PROFILE, odd.tabs.single().profile)
        assertTrue("The default profile always exists", odd.profiles.any { it.id == GeckoRules.DEFAULT_PROFILE })
        assertEquals(listOf("https://ok.test/"), odd.bookmarks.map { it.url })
        assertEquals(GeckoRules.TRACKING_STANDARD, odd.settings.tracking)
        assertEquals(GeckoRules.COLORS_SYSTEM, odd.settings.colors)
        assertEquals(BrowserRules.DEFAULT_SEARCH, odd.settings.search)
    }

    @Test fun historySkipsRepeatsAndNonWebAddresses() {
        val once = GeckoRules.remember(emptyList(), "https://a.test/", "A", "p")
        assertEquals(1, GeckoRules.remember(once, "https://a.test/", "A again", "p").size)
        assertEquals("A again", GeckoRules.remember(once, "https://a.test/", "A again", "p").single().title)
        assertEquals(2, GeckoRules.remember(once, "https://b.test/", "B", "p").size)
        assertEquals(once, GeckoRules.remember(once, "moz-extension://abc/page.html", "X", "p"))
    }

    @Test fun extensionsInstallOnlyFromMozillasSite() {
        assertTrue(GeckoRules.installable("https://addons.mozilla.org/firefox/downloads/file/4412345/ublock_origin-1.60.0.xpi"))
        for (bad in listOf("http://addons.mozilla.org/x.xpi", "https://addons.mozilla.org.evil.test/x.xpi", "https://evil.test/addons.mozilla.org/x.xpi",
                "https://user@addons.mozilla.org/x.xpi", "https://addons.mozilla.org:8443/x.xpi", "https://addons.mozilla.org/x.zip", "file:///sdcard/x.xpi", "resource://android/x.xpi"))
            assertFalse(bad, GeckoRules.installable(bad))
        assertTrue(GeckoRules.looksLikeExtension("https://evil.test/a.XPI?x=1"))
        assertFalse(GeckoRules.looksLikeExtension("https://example.com/page"))
        assertTrue(GeckoRules.searchUrl("dark reader & more").contains("q=dark%20reader%20%26%20more"))
    }

    @Test fun readsMozillasCatalogue() {
        val results = GeckoRules.parseSearch("""{"results":[
            {"guid":"a@b","slug":"a","name":{"en-US":"Alpha"},"summary":{"en-US":"Does A"},"average_daily_users":10,"ratings":{"average":4.5},"icon_url":"https://x.test/i.png",
             "current_version":{"file":{"url":"https://addons.mozilla.org/firefox/downloads/file/1/alpha.xpi"},"compatibility":{"firefox":{},"android":{}}}},
            {"guid":"c@d","slug":"c","name":"Plain","summary":"Words",
             "current_version":{"file":{"url":"https://addons.mozilla.org/firefox/downloads/file/2/plain.xpi"},"compatibility":{"firefox":{}}}},
            {"guid":"e@f","slug":"e","name":"Elsewhere","current_version":{"file":{"url":"https://evil.test/e.xpi"}}}]}""")
        assertEquals(listOf("Alpha", "Plain"), results.map { it.name })
        assertTrue(results[0].android); assertFalse(results[1].android)
        assertEquals(emptyList<GeckoRules.Listing>(), GeckoRules.parseSearch("nope"))
    }

    @Test fun openPressesTheButtonOrOpensSettingsOrSaysWhyNot() {
        val options = "moz-extension://abc/options.html"
        fun open(enabled: Boolean = true, button: Boolean = true, on: Boolean? = true, settings: String? = null, private: Boolean = false) =
            GeckoRules.extensionOpen("uBlock", enabled, button, on, settings, private)
        // A toolbar button is pressed, whether or not there is a settings page, and when the extension didn't say it's on.
        assertEquals(GeckoRules.ExtensionOpen.Button, open())
        assertEquals(GeckoRules.ExtensionOpen.Button, open(settings = options))
        assertEquals(GeckoRules.ExtensionOpen.Button, open(on = null))
        // No button: its settings page; a button that's off for this page: settings if there are any.
        assertEquals(GeckoRules.ExtensionOpen.Options(options), open(button = false, on = null, settings = options))
        assertEquals(GeckoRules.ExtensionOpen.Options(options), open(on = false, settings = options))
        // Nothing to open, said in words and never silently.
        for (refused in listOf(open(enabled = false), open(on = false), open(button = false, on = null), open(private = true), open(private = true, settings = options)))
            assertTrue("$refused", refused is GeckoRules.ExtensionOpen.Refused && (refused as GeckoRules.ExtensionOpen.Refused).message.contains("uBlock"))
        assertTrue((open(enabled = false) as GeckoRules.ExtensionOpen.Refused).message.startsWith("Turn uBlock on"))
        assertTrue((open(on = false) as GeckoRules.ExtensionOpen.Refused).message.contains("off on this page"))
        assertTrue((open(button = false, on = null) as GeckoRules.ExtensionOpen.Refused).message.contains("nothing to open"))
        // Extensions don't run in private tabs, so a button press there is refused; a settings-only extension still opens them.
        assertTrue((open(private = true) as GeckoRules.ExtensionOpen.Refused).message.contains("private tabs"))
        assertEquals(GeckoRules.ExtensionOpen.Options(options), open(button = false, on = null, settings = options, private = true))
        assertTrue(GeckoRules.extensionActedOnPage("uBlock").contains("page you're viewing"))
    }

    @Test fun extensionsAreToldOnlyAboutTheRegularTabInView() {
        assertEquals("a", GeckoRules.extensionTab("a", private = false, live = true))
        // A private tab is never named to extensions, a tab without a page session can't be, and nor can no tab.
        assertEquals(null, GeckoRules.extensionTab("a", private = true, live = true))
        assertEquals(null, GeckoRules.extensionTab("a", private = false, live = false))
        assertEquals(null, GeckoRules.extensionTab(null, private = false, live = true))
    }

    @Test fun installErrorsAreInPlainWords() {
        assertTrue(GeckoRules.installError(-5).contains("signed"))
        assertTrue(GeckoRules.installError(-10).contains("blocked"))
        assertTrue(GeckoRules.installError(-999).contains("-999"))
    }

    @Test fun readsAFirefoxBookmarksExport() {
        val html = """<DT><A HREF="https://a.test/?x=1&amp;y=2" ADD_DATE="1">A &amp; B</A><DT><A HREF="place:sort=8">Recent</A><DT><A HREF="https://a.test/?x=1&amp;y=2">Dup</A><DT><a href="javascript:x()">Bad</a>"""
        val links = GeckoRules.parseBookmarks(html, "p")
        assertEquals(listOf("https://a.test/?x=1&y=2"), links.map { it.url })
        assertEquals("A & B", links.single().title)
    }

    @Test fun privateTabsNeverReachStorage() {
        val secret = "https://secret.example/private-page"
        val saved = GeckoRules.Saved(
            tabs = listOf(GeckoRules.Tab("open", "https://open.example/"), GeckoRules.Tab("hidden", secret, "Hidden title", private = true)),
            active = "hidden",
        )
        val text = GeckoRules.encode(saved)
        assertFalse(text.contains("secret.example")); assertFalse(text.contains("Hidden title")); assertFalse(text.contains("\"hidden\""))
        val back = GeckoRules.decode(text)
        assertEquals(listOf("open"), back.tabs.map { it.id })
        assertEquals("The saved current tab falls back to one that was saved", "open", back.active)
        assertTrue(back.tabs.none { it.private })
    }

    @Test fun closedTabsAndTextSizeComeBack() {
        val saved = GeckoRules.Saved(closed = listOf(GeckoRules.Tab("c", "https://a.example/", "A", "default")), settings = GeckoRules.Settings(textZoom = 130))
        val back = GeckoRules.decode(GeckoRules.encode(saved))
        assertEquals(listOf("https://a.example/"), back.closed.map { it.url })
        assertEquals(130, back.settings.textZoom)
        assertEquals("Older saved data has neither", 100, GeckoRules.decode("{}").settings.textZoom)
    }

    @Test fun formFieldsTakeOnlyValuesHtmlAccepts() {
        assertEquals("2026-11-08", GeckoRules.formValue(GeckoRules.FORM_DATE, " 2026-11-08 "))
        assertEquals(null, GeckoRules.formValue(GeckoRules.FORM_DATE, "2026-02-31"))
        assertEquals(null, GeckoRules.formValue(GeckoRules.FORM_DATE, "11/08/2026"))
        assertEquals("Clearing the field is allowed", "", GeckoRules.formValue(GeckoRules.FORM_DATE, "  "))
        assertEquals("2026-11", GeckoRules.formValue(GeckoRules.FORM_MONTH, "2026-11"))
        assertEquals("2026-W45", GeckoRules.formValue(GeckoRules.FORM_WEEK, "2026-W45"))
        assertEquals(null, GeckoRules.formValue(GeckoRules.FORM_WEEK, "2026-W54"))
        assertEquals("14:30", GeckoRules.formValue(GeckoRules.FORM_TIME, "14:30"))
        assertEquals(null, GeckoRules.formValue(GeckoRules.FORM_TIME, "25:00"))
        assertEquals("2026-11-08T14:30", GeckoRules.formValue(GeckoRules.FORM_DATETIME, "2026-11-08 14:30"))
        assertEquals("#ff8800", GeckoRules.formValue(GeckoRules.FORM_COLOR, "#FF8800"))
        assertEquals(null, GeckoRules.formValue(GeckoRules.FORM_COLOR, "orange"))
        assertEquals("Before the field's minimum", null, GeckoRules.formValue(GeckoRules.FORM_DATE, "2026-01-01", min = "2026-06-01"))
        assertEquals("After the field's maximum", null, GeckoRules.formValue(GeckoRules.FORM_DATE, "2026-12-01", max = "2026-06-01"))
        assertEquals("2026-06-01", GeckoRules.formValue(GeckoRules.FORM_DATE, "2026-06-01", "2026-06-01", "2026-06-01"))
    }

    @Test fun theEngineChoiceIsSaved() {
        val saved = BrowserRules.Saved(engine = BrowserRules.ENGINE_GECKO)
        assertEquals(BrowserRules.ENGINE_GECKO, BrowserRules.decode(BrowserRules.encode(saved)).engine)
    }
}
