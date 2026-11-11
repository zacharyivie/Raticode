package dev.raticode.mobile.browser

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.atomic.AtomicInteger

/** Reading what Firefox writes. The samples keep the shape of files written by Firefox 158. */
class GeckoImportTest {
    private val settings = GeckoRules.Settings()
    private fun read(text: String, name: String = "file") = GeckoImport.read(name, text, settings, "p")

    @Test fun readsAProfilesExtensionListAndLeavesFirefoxsOwnPartsOut() {
        val preview = read("""{"schemaVersion":37,"addons":[
            {"id":"addon@darkreader.org","type":"extension","location":"app-profile","active":true,"version":"4.9.135","defaultLocale":{"name":"Dark Reader"}},
            {"id":"off@example.test","type":"extension","location":"app-profile","active":false,"version":"1","defaultLocale":{"name":"Turned off"}},
            {"id":"formautofill@mozilla.org","type":"extension","location":"app-builtin-addons","active":true,"version":"1"},
            {"id":"old@mozilla.org","type":"extension","location":"app-system-defaults","active":true,"version":"1"},
            {"id":"firefox-compact-dark@mozilla.org","type":"theme","location":"app-builtin","active":false,"version":"1"},
            {"id":"en@dictionaries","type":"dictionary","location":"app-profile","active":true,"version":"1"}]}""")
        assertEquals(listOf("addon@darkreader.org", "off@example.test"), preview.extensions.map { it.id })
        assertEquals("Dark Reader", preview.extensions[0].name)
        assertFalse(preview.extensions[1].active)
        assertEquals(2, preview.builtIn); assertEquals(1, preview.themes)
    }

    @Test fun readsTroubleshootingDataWithExtensionsAndSettings() {
        val preview = read("""{"application":{"name":"Firefox"},"addons":[
            {"name":"Dark Reader","type":"extension","version":"4","isActive":true,"id":"addon@darkreader.org","locationName":"app-profile"},
            {"name":"Search Detection","type":"extension","version":"3","isActive":true,"id":"s@mozilla.com","locationName":"app-builtin-addons"}],
            "modifiedPreferences":{"browser.contentblocking.category":"strict","layout.css.prefers-color-scheme.content-override":1,"network.trr.mode":3,"services.sync.username":"me@example.test"}}""")
        assertEquals(listOf("addon@darkreader.org"), preview.extensions.map { it.id })
        assertEquals(setOf(GeckoImport.TRACKING, GeckoImport.COLORS), preview.settings.map { it.key }.toSet())
        assertTrue(preview.notes.any { it.what == "DNS over HTTPS" })
    }

    private val prefs = """// Mozilla User Preferences
user_pref("browser.contentblocking.category", "strict");
user_pref("browser.urlbar.placeholderName", "DuckDuckGo");
user_pref("layout.css.prefers-color-scheme.content-override", 0);
user_pref("network.trr.mode", 3);
user_pref("places.history.enabled", false);
user_pref("services.sync.username", "me@example.test");
user_pref("identity.fxaccounts.account.device.name", "secret");
"""

    @Test fun mapsOnlyTheSettingsThisBrowserHas() {
        val preview = read(prefs, "prefs.js")
        assertEquals(mapOf(GeckoImport.TRACKING to GeckoRules.TRACKING_STRICT, GeckoImport.COLORS to GeckoRules.COLORS_DARK,
            GeckoImport.SEARCH to "https://duckduckgo.com/?q={query}", GeckoImport.HISTORY to "off"), preview.settings.associate { it.key to it.value })
        assertTrue(preview.notes.any { it.what == "DNS over HTTPS" })
        assertFalse("Account details are never kept", preview.toString().contains("example.test") || preview.toString().contains("secret"))
        val applied = preview.settings.fold(settings, GeckoImport::apply)
        assertEquals(GeckoRules.TRACKING_STRICT, applied.tracking); assertEquals(GeckoRules.COLORS_DARK, applied.colors); assertFalse(applied.keepHistory)
    }

    @Test fun onlyOffersWhatWouldChange() {
        val already = GeckoRules.Settings(tracking = GeckoRules.TRACKING_STRICT, colors = GeckoRules.COLORS_DARK, keepHistory = false, search = "https://duckduckgo.com/?q={query}")
        assertTrue(GeckoImport.read("prefs.js", prefs, already, "p").settings.isEmpty())
    }

    @Test fun anEngineThisBrowserLacksIsExplainedNotGuessed() {
        val preview = read("""user_pref("browser.urlbar.placeholderName", "Kagi");""", "prefs.js")
        assertTrue(preview.settings.isEmpty())
        assertTrue(preview.notes.single().what.contains("Kagi"))
    }

    @Test fun readsBothBookmarkFormats() {
        val html = read("""<!DOCTYPE NETSCAPE-Bookmark-file-1><DT><A HREF="https://example.com/a?x=1&amp;y=2">Example A &amp; co</A><DT><A HREF="place:sort=8">Recent</A>""")
        assertEquals(listOf("https://example.com/a?x=1&y=2"), html.bookmarks.map { it.url })
        val json = read("""{"guid":"root________","type":"text/x-moz-place-container","root":"placesRoot","children":[{"title":"toolbar","type":"text/x-moz-place-container","children":[
            {"title":"Mozilla","type":"text/x-moz-place","uri":"https://www.mozilla.org/"},{"title":"Recent","type":"text/x-moz-place","uri":"place:sort=8"}]}]}""")
        assertEquals(listOf("https://www.mozilla.org/"), json.bookmarks.map { it.url })
        assertEquals("p", json.bookmarks.single().profile)
    }

    @Test fun refusesCredentialFilesByNameBeforeOpeningThem() {
        for (name in listOf("logins.json", "logins-backup.json", "key4.db", "key3.db", "cookies.sqlite", "cookies.sqlite-wal", "cert9.db", "signedInUser.json", "places.sqlite", "search.json.mozlz4", "x/y/LOGINS.JSON"))
            assertNotNull(name, GeckoImport.refused(name))
        for (name in listOf("extensions.json", "addons.json", "prefs.js", "user.js", "bookmarks.html", "bookmarks.json"))
            assertNull(name, GeckoImport.refused(name))
    }

    @Test fun somethingElseIsSaidPlainly() {
        assertTrue(read("hello there", "notes.txt").notes.single().what == "notes.txt")
        assertTrue(read("""{"a":1}""", "other.json").notes.single().what == "other.json")
        assertTrue(read("""{"addons":[]}""", "empty.json").notes.single().why.contains("Nothing"))
    }

    @Test fun onlyMozillasSiteAnswersLookupsAndCollections() {
        val urls = GeckoImport.lookupUrls((1..30).map { "id$it@example.test" })
        assertEquals(2, urls.size); assertTrue(urls.all { it.startsWith("https://addons.mozilla.org/api/v5/addons/search/") })
        assertTrue(urls[0].contains("id1%40example.test"))
        assertEquals("https://addons.mozilla.org/api/v5/accounts/account/mozilla/collections/featured-add-ons/addons/?page_size=50&lang=en-US&page=1",
            GeckoImport.collectionApi("https://addons.mozilla.org/en-US/firefox/collections/mozilla/featured-add-ons/"))
        assertNotNull(GeckoImport.collectionApi("https://addons.mozilla.org/en-US/android/collections/123/my-set"))
        for (bad in listOf("http://addons.mozilla.org/en-US/firefox/collections/a/b/", "https://addons.mozilla.org.evil.test/en-US/firefox/collections/a/b/",
                "https://user@addons.mozilla.org/en-US/firefox/collections/a/b/", "https://addons.mozilla.org/en-US/firefox/addon/a/", "not a link"))
            assertNull(bad, GeckoImport.collectionApi(bad))
        val found = GeckoImport.parseCollection("""{"next":null,"results":[{"notes":null,"addon":{"guid":"a@b","slug":"a","name":{"en-US":"Alpha"},
            "current_version":{"file":{"url":"https://addons.mozilla.org/firefox/downloads/file/1/alpha.xpi"},"compatibility":{"android":{}}}}},
            {"addon":{"guid":"e@f","current_version":{"file":{"url":"https://evil.test/e.xpi"}}}}]}""")
        assertEquals(listOf("a@b"), found.map { it.id })
        assertFalse(GeckoImport.hasNextPage("""{"next":null}"""))
    }

    @Test fun firefoxPartsUpdatedIntoTheProfileAreDroppedOnlyOnceTheSiteHasAnswered() {
        val preview = GeckoImport.Preview(extensions = listOf(GeckoImport.FoundExtension("newtab@mozilla.org", "New Tab", "1", true),
            GeckoImport.FoundExtension("FirefoxColor@mozilla.com", "Firefox Color", "1", true), GeckoImport.FoundExtension("gone@example.test", "Gone", "1", true)))
        assertEquals("Not yet answered", 3, GeckoImport.withoutFirefoxParts(preview, emptySet(), emptySet()).extensions.size)
        val done = GeckoImport.withoutFirefoxParts(preview, setOf("FirefoxColor@mozilla.com"), setOf("newtab@mozilla.org", "FirefoxColor@mozilla.com", "gone@example.test"))
        assertEquals(listOf("FirefoxColor@mozilla.com", "gone@example.test"), done.extensions.map { it.id })
        assertEquals(1, done.builtIn)
    }

    @Test fun mergingKeepsTheLatestSettingAndNoDuplicates() {
        val one = GeckoImport.Preview(extensions = listOf(GeckoImport.FoundExtension("a", "A", "1", true)), settings = listOf(GeckoImport.SettingChange("k", "L", "x", "y", "1")))
        val two = GeckoImport.Preview(extensions = listOf(GeckoImport.FoundExtension("a", "A", "1", true)), settings = listOf(GeckoImport.SettingChange("k", "L", "x", "z", "2")))
        val merged = GeckoImport.merge(one, two)
        assertEquals(1, merged.extensions.size); assertEquals("2", merged.settings.single().value)
    }

    @Test fun recognisesFirefoxTroubleshootingDataOnlyWhenItReallyIsThat() {
        assertTrue(GeckoImport.isTroubleshootingData("""{"application":{"name":"Firefox"},"addons":[]}"""))
        assertTrue("Leading space from a clipboard", GeckoImport.isTroubleshootingData("""  {"application":{"name":"Firefox"},"addons":[{"id":"a@b"}]}"""))
        assertFalse("What Raticode copied for the address bar", GeckoImport.isTroubleshootingData(GeckoImport.SUPPORT_PAGE))
        assertFalse("Someone's copied text", GeckoImport.isTroubleshootingData("my password is hunter2"))
        assertFalse("Other JSON", GeckoImport.isTroubleshootingData("""{"addons":[]}"""))
        assertFalse("Other JSON", GeckoImport.isTroubleshootingData("""{"application":{"name":"Firefox"},"addons":"none"}"""))
        assertFalse("Broken JSON", GeckoImport.isTroubleshootingData("""{"application":"""))
    }

    @Test fun aHandoverStaysOpenForTenMinutesOnly() {
        val start = 5_000_000L
        assertTrue(GeckoImport.handoverOpen(start, start))
        assertTrue(GeckoImport.handoverOpen(start, start + GeckoImport.HANDOVER_MS))
        assertFalse("Too late", GeckoImport.handoverOpen(start, start + GeckoImport.HANDOVER_MS + 1))
        assertFalse("None was saved", GeckoImport.handoverOpen(0, GeckoImport.HANDOVER_MS))
        assertFalse("The uptime clock restarted, so the phone did too", GeckoImport.handoverOpen(start, start - 1))
    }

    @Test fun looksAtTheClipboardEachTimeFocusComesBackAndStays() = runBlocking {
        val focus = MutableStateFlow(false)
        val checks = AtomicInteger()
        suspend fun until(count: Int) = withTimeout(5000) { while (checks.get() < count) delay(10) }
        val watching = launch(Dispatchers.Default) { GeckoImport.onFocusBack(focus, settleMs = 300) { checks.incrementAndGet() } }
        delay(50)
        assertEquals("Not while Firefox has the screen", 0, checks.get())
        focus.value = true
        until(1)
        focus.value = false
        delay(50)
        focus.value = true
        until(2)
        assertEquals("Once for each return", 2, checks.get())
        focus.value = false
        delay(50)
        focus.value = true
        focus.value = false
        delay(600)
        assertEquals("Focus that doesn't stay doesn't count", 2, checks.get())
        watching.cancel()
    }

    @Test fun anExtensionTurnedOffInFirefoxIsSeenAsOff() {
        val preview = read("""{"application":{"name":"Firefox"},"addons":[
            {"name":"Off","type":"extension","version":"1","isActive":false,"id":"off@example.test","locationName":"app-profile"}]}""")
        assertFalse(preview.extensions.single().active)
    }

    @Test fun thePhoneListHasNothingToTakeWhenOnlyFirefoxPartsAreThere() {
        val preview = read("""{"application":{"name":"Firefox"},"addons":[
            {"name":"Reader View","type":"extension","version":"1","isActive":true,"id":"readerview@mozac.org","locationName":"app-builtin"}]}""")
        assertTrue(GeckoImport.isEmpty(preview))
        assertEquals(1, preview.builtIn)
    }
}
