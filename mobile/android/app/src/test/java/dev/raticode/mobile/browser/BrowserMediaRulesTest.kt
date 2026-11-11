package dev.raticode.mobile.browser

import dev.raticode.mobile.browser.BrowserMediaRules.Engine
import dev.raticode.mobile.browser.BrowserMediaRules.Key
import dev.raticode.mobile.browser.BrowserMediaRules.Report
import dev.raticode.mobile.browser.BrowserMediaRules.Track
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class BrowserMediaRulesTest {
    private val key = Key(Engine.CHROMIUM, "a")
    private fun playing(title: String = "Song", canToggle: Boolean = true) = Report(true, title, site = "music.example", canToggle = canToggle)
    private fun stopped(canToggle: Boolean = true) = Report(false, "Song", site = "music.example", canToggle = canToggle)
    private fun track(tab: String, playing: Boolean, startedAt: Long, stoppedAt: Long = 0, canToggle: Boolean = true) =
        Track(Key(Engine.GECKO, tab), tab, "", "site", playing, canToggle, false, false, null, startedAt, stoppedAt)
    private fun answer(json: JSONObject) = JSONObject.quote(json.toString())

    @Test fun aTabThatNeverPlayedIsNotAdded() = assertNull(BrowserMediaRules.merge(null, key, stopped(), 100))

    @Test fun aPlayingTabStaysUntilItStopsAndAPausedOneOnlyForTheGrace() {
        val started = BrowserMediaRules.merge(null, key, playing(), 100)!!
        assertEquals(100, started.startedAt)
        assertTrue(BrowserMediaRules.visible(started, 10_000_000))
        assertNull("Nothing to expire while it plays", BrowserMediaRules.expiresAt(started))

        val paused = BrowserMediaRules.merge(started, key, stopped(), 500)!!
        assertEquals(500, paused.stoppedAt)
        assertTrue(BrowserMediaRules.visible(paused, 500 + BrowserMediaRules.PAUSE_GRACE_MS - 1))
        assertFalse(BrowserMediaRules.visible(paused, 500 + BrowserMediaRules.PAUSE_GRACE_MS))
    }

    @Test fun aTabThatCannotResumeOnlyBridgesATrackChange() {
        val ended = BrowserMediaRules.merge(BrowserMediaRules.merge(null, key, playing(), 100), key, stopped(canToggle = false), 500)!!
        assertTrue(BrowserMediaRules.visible(ended, 500 + BrowserMediaRules.STOP_GRACE_MS - 1))
        assertFalse(BrowserMediaRules.visible(ended, 500 + BrowserMediaRules.STOP_GRACE_MS))
    }

    @Test fun aStopReportedTwiceKeepsTheFirstStopTime() {
        val paused = BrowserMediaRules.merge(BrowserMediaRules.merge(null, key, playing(), 100), key, stopped(), 500)!!
        assertEquals(500, BrowserMediaRules.merge(paused, key, stopped(), 900)!!.stoppedAt)
    }

    @Test fun playingAgainCountsAsANewStart() {
        val paused = BrowserMediaRules.merge(BrowserMediaRules.merge(null, key, playing(), 100), key, stopped(), 300)!!
        val resumed = BrowserMediaRules.merge(paused, key, playing(), 900)!!
        assertEquals(900, resumed.startedAt)
        assertEquals(0, resumed.stoppedAt)
        assertNull(BrowserMediaRules.expiresAt(resumed))
    }

    @Test fun somethingPlayingBeatsSomethingPausedThenTheLatestStart() {
        val older = track("older", playing = true, startedAt = 10)
        val newer = track("newer", playing = true, startedAt = 20)
        val held = track("held", playing = false, startedAt = 30, stoppedAt = 40)
        assertEquals("newer", BrowserMediaRules.current(listOf(older, newer, held), 50)?.key?.tab)
        assertEquals("older", BrowserMediaRules.current(listOf(older, held), 50)?.key?.tab)
        assertEquals("held", BrowserMediaRules.current(listOf(held), 50)?.key?.tab)
        assertNull("Nothing to show", BrowserMediaRules.current(emptyList(), 50))
    }

    @Test fun theBarLeavesTheTabInViewToItsPageAndSpeaksForAnotherTab() {
        val older = track("older", playing = true, startedAt = 10)
        val newer = track("newer", playing = true, startedAt = 20)
        val held = track("held", playing = false, startedAt = 30, stoppedAt = 40)
        val all = listOf(older, newer, held)
        assertEquals("Nothing in view: the same as the lock screen", "newer", BrowserMediaRules.forBar(all, 50, null)?.key?.tab)
        assertEquals("The next playing tab", "older", BrowserMediaRules.forBar(all, 50, newer.key)?.key?.tab)
        assertEquals("Another tab's paused track keeps Play reachable", "held", BrowserMediaRules.forBar(listOf(newer, held), 50, newer.key)?.key?.tab)
        assertNull("Only the tab in view plays", BrowserMediaRules.forBar(listOf(newer), 50, newer.key))
        assertEquals("The lock screen still follows it", "newer", BrowserMediaRules.current(listOf(newer), 50)?.key?.tab)
        assertEquals("A tab in view that isn't playing changes nothing", "newer", BrowserMediaRules.forBar(all, 50, Key(Engine.GECKO, "quiet"))?.key?.tab)
    }

    @Test fun theSameTabNameInTheOtherBrowserIsAnotherTab() {
        val gecko = track("t1", playing = true, startedAt = 10)
        assertEquals("t1", BrowserMediaRules.forBar(listOf(gecko), 50, Key(Engine.CHROMIUM, "t1"))?.key?.tab)
        assertNull(BrowserMediaRules.forBar(listOf(gecko), 50, Key(Engine.GECKO, "t1")))
    }

    @Test fun aPausedTabInViewStaysHiddenAndExpiresOnTheSameHold() {
        val paused = track("p", playing = false, startedAt = 1, stoppedAt = 100)
        assertNull(BrowserMediaRules.forBar(listOf(paused), 200, paused.key))
        assertEquals("p", BrowserMediaRules.forBar(listOf(paused), 200, null)?.key?.tab)
        assertNull("Gone after the hold", BrowserMediaRules.forBar(listOf(paused), 100 + BrowserMediaRules.PAUSE_GRACE_MS, null))
    }

    @Test fun anExpiredTrackIsNotShownAndTheNextExpiryIsTheSoonest() {
        val ended = track("ended", playing = false, startedAt = 1, stoppedAt = 100, canToggle = false)
        val paused = track("paused", playing = false, startedAt = 2, stoppedAt = 100)
        assertEquals("paused", BrowserMediaRules.current(listOf(ended, paused), 100 + BrowserMediaRules.STOP_GRACE_MS)?.key?.tab)
        assertEquals(100 + BrowserMediaRules.STOP_GRACE_MS, BrowserMediaRules.nextExpiry(listOf(ended, paused), 101))
        assertNull(BrowserMediaRules.nextExpiry(listOf(track("p", playing = true, startedAt = 1)), 101))
    }

    @Test fun pageTextIsOneShortLineWithoutTextReorderingCharacters() {
        assertEquals("Line one two", BrowserMediaRules.clean("Line\none\t  two\u0000"))
        assertEquals("gpj.exe", BrowserMediaRules.clean("‮gpj.exe‬"))
        assertEquals(BrowserMediaRules.MAX_TEXT, BrowserMediaRules.clean("x".repeat(5000)).length)
        assertEquals("", BrowserMediaRules.clean(null))
    }

    @Test fun theTitleFallsBackFromMediaToPageToSiteAndIsNeverEmpty() {
        val url = "https://music.example/watch"
        assertEquals("Track", BrowserMediaRules.title("Track", "Page", url))
        assertEquals("Page", BrowserMediaRules.title("  ", "Page", url))
        assertEquals("music.example", BrowserMediaRules.title(null, url, url))
        assertEquals(BrowserMediaRules.UNTITLED, BrowserMediaRules.title(null, "", "about:blank"))
        assertEquals(BrowserMediaRules.NO_SITE, BrowserMediaRules.site("about:blank"))
    }

    @Test fun aPagesAnswerIsReadAsUntrustedData() {
        val report = BrowserMediaRules.parse(answer(JSONObject().put("playing", true).put("canToggle", true).put("next", true)
            .put("title", "Big‮ Song").put("artist", "Band")))!!
        assertTrue(report.playing); assertTrue(report.canToggle); assertTrue(report.canNext); assertFalse(report.canPrevious)
        assertEquals("Big Song", report.title); assertEquals("Band", report.artist); assertNull(report.artwork)
    }

    @Test fun anythingElseIsNotAReport() {
        assertNull(BrowserMediaRules.parse(null))
        assertNull(BrowserMediaRules.parse("null"))
        assertNull(BrowserMediaRules.parse("true"))
        assertNull(BrowserMediaRules.parse("\"\""))
        assertNull(BrowserMediaRules.parse("\"not json\""))
        assertNull(BrowserMediaRules.parse(JSONObject.quote("x".repeat(70_000))))
    }

    @Test fun wrongTypesFromAPageDoNotClaimAnything() {
        val report = BrowserMediaRules.parse(answer(JSONObject().put("previous", 7).put("artwork", "nope")))!!
        assertFalse(report.playing); assertFalse(report.canPrevious); assertNull(report.artwork)
    }

    @Test fun artworkPicksTheSmallestSharpPicture() {
        val listed = listOf("https://cdn.example/512.png" to "512x512", "https://cdn.example/128.png" to "128x128", "https://cdn.example/48.png" to "48x48")
        assertEquals("https://cdn.example/128.png", BrowserMediaRules.artwork(listed, "https://cdn.example/poster.jpg"))
        assertEquals("https://cdn.example/96.png", BrowserMediaRules.artwork(listOf("https://cdn.example/96.png" to "96X96 48x48"), null))
    }

    @Test fun artworkFallsBackToTheLargestListedThenAnyThenThePoster() {
        val small = listOf("https://cdn.example/32.png" to "32x32", "https://cdn.example/64.png" to "64x64")
        assertEquals("https://cdn.example/64.png", BrowserMediaRules.artwork(small, null))
        assertEquals("https://cdn.example/any.png", BrowserMediaRules.artwork(listOf("https://cdn.example/any.png" to "any"), null))
        assertEquals("https://cdn.example/poster.jpg", BrowserMediaRules.artwork(listOf("http://cdn.example/x.png" to "96x96"), "https://cdn.example/poster.jpg"))
        assertNull(BrowserMediaRules.artwork(emptyList(), ""))
    }

    @Test fun artworkMustBeSecureAndOnTheInternet() {
        assertTrue(BrowserMediaRules.artworkAddress("https://i.ytimg.com/vi/x/hq.jpg"))
        listOf("http://i.ytimg.com/a.jpg", "https://localhost/a.png", "https://printer.local/a.png", "https://router/a.png", "https://192.168.1.1/a.png",
            "https://10.0.0.1:8443/a.png", "https://[::1]/a.png", "https://x.internal/a.png", "data:image/png;base64,AA", "blob:https://x.example/1", "file:///a.png",
            "https://cdn.example/" + "a".repeat(3000)).forEach { assertFalse(it, BrowserMediaRules.artworkAddress(it)) }
    }

    @Test fun privateAddressesAreRefusedHoweverTheyAreWritten() {
        listOf("https://localhost./a", "https://localhost../a", "https://printer.local./a", "https://nas.internal./a", "https://app.localhost./a:443", "https://printer.local.:443/a",
            "https://box.lan/a", "https://nas.home.arpa/a", "https://0x7f.0.0.1/a", "https://0x7f.1/a", "https://127.1/a", "https://2130706433/a", "https://017700000001/a",
            "https://127.0.0.1./a", "https://8.8.8.8/a", "https://[::ffff:127.0.0.1]/a", "https://[fe80::1]/a", "https://127%2e0%2e0%2e1/a", "https://cdn.example\\@127.0.0.1/a")
            .forEach { assertFalse(it, BrowserMediaRules.artworkAddress(it)) }
        listOf("https://10.example.com/a.png", "https://127.example.com/a.png", "https://cdn.example./a.png", "https://x1.cdn-1.example:8443/a.png")
            .forEach { assertTrue(it, BrowserMediaRules.artworkAddress(it)) }
    }

    @Test fun aRedirectIsCheckedBeforeItIsFollowed() {
        val from = "https://cdn.example/a/b.png"
        assertEquals("https://img.example/c.png", BrowserMediaRules.redirectTarget(from, "https://img.example/c.png"))
        assertEquals("https://cdn.example/a/c.png", BrowserMediaRules.redirectTarget(from, "c.png"))
        assertEquals("https://cdn.example/c.png", BrowserMediaRules.redirectTarget(from, "/c.png"))
        listOf(null, "", "  ", "http://img.example/c.png", "https://192.168.1.1/c.png", "https://printer.local./c.png", "https://0x7f.1/c.png", "//localhost/c.png", "file:///etc/hosts")
            .forEach { assertNull("$it", BrowserMediaRules.redirectTarget(from, it)) }
    }

    @Test fun onlyAnAddressChangeThatFollowsALoadIsANewDocument() {
        val documents = GeckoDocuments()
        assertFalse("A page changing its own address", documents.addressChanged("a"))
        documents.loadStarted("a")
        assertTrue("The new page arrived", documents.addressChanged("a"))
        assertFalse("Later address changes on that page", documents.addressChanged("a"))
        documents.loadStarted("a")
        documents.loadStopped("a")
        assertFalse("A download leaves the old page in place", documents.addressChanged("a"))
        documents.loadStarted("a")
        assertFalse("Another tab is unaffected", documents.addressChanged("b"))
        documents.loadStarted("a") // A redirect starts the load again.
        assertTrue(documents.addressChanged("a"))
    }

    @Test fun thumbnailsDecodeNearTheirDisplaySize() {
        assertEquals(1, BrowserMediaRules.sampleSize(192, 192))
        assertEquals(1, BrowserMediaRules.sampleSize(100, 50))
        assertEquals(2, BrowserMediaRules.sampleSize(400, 400))
        assertEquals(8, BrowserMediaRules.sampleSize(1600, 900))
    }

    @Test fun absurdPictureSizesAreRefused() {
        assertEquals(0, BrowserMediaRules.sampleSize(0, 100))
        assertEquals(0, BrowserMediaRules.sampleSize(-1, -1))
        assertEquals(0, BrowserMediaRules.sampleSize(20000, 100))
    }

    @Test fun theSpokenLabelAlwaysNamesTheSite() {
        val withArtist = Track(key, "Song", "Band", "music.example", true, true, false, false, null, 1, 0)
        assertEquals("Song, Band, from music.example", BrowserMediaRules.spoken(withArtist))
        assertEquals("Song, from music.example", BrowserMediaRules.spoken(withArtist.copy(artist = "")))
    }

    @Test fun everyActionHasItsOwnScriptThatAnswersWithState() {
        val scripts = BrowserMediaRules.Action.entries.map(BrowserMediaScript::control)
        assertEquals(scripts.size, scripts.toSet().size)
        scripts.forEach { assertNotNull(it); assertTrue(it, "JSON.stringify" in it) }
    }
}
