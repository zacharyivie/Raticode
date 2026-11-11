package dev.raticode.mobile.browser

import dev.raticode.mobile.browser.BrowserRules.Target
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The phone browser's address bar, navigation rules, history, bookmarks, downloads and saved state. */
class BrowserRulesTest {
    private val google = BrowserRules.DEFAULT_SEARCH

    @Test fun addressBarLoadsAddressesAndSearchesEverythingElse() {
        assertEquals(Target.Load("https://example.com"), BrowserRules.resolve("example.com", google))
        assertEquals(Target.Load("https://example.com/a?b=c"), BrowserRules.resolve("  example.com/a?b=c \n", google))
        assertEquals(Target.Load("https://localhost:3000"), BrowserRules.resolve("localhost:3000", google))
        assertEquals(Target.Load("https://192.168.1.20:8443/x"), BrowserRules.resolve("192.168.1.20:8443/x", google))
        assertEquals(Target.Load("https://docs.raticode.dev/"), BrowserRules.resolve("https://docs.raticode.dev/", google))
        assertEquals(Target.Load("https://www.google.com/search?q=what%20is%20kotlin"), BrowserRules.resolve("what is kotlin", google))
        // A colon in ordinary words is still a search, not an address.
        assertEquals(Target.Load("https://www.google.com/search?q=note%3A%20buy%20milk"), BrowserRules.resolve("note: buy milk", google))
        assertEquals(Target.Load("https://duckduckgo.com/?q=a%26b"), BrowserRules.resolve("a&b", "https://duckduckgo.com/?q={query}"))
        assertNull(BrowserRules.resolve("   ", google))
        assertEquals(Target.Load("about:blank"), BrowserRules.resolve("about:blank", google))
    }

    @Test fun anUnusableSearchTemplateFallsBackToGoogle() {
        assertTrue(BrowserRules.search("x", "javascript:{query}").startsWith("https://www.google.com/"))
        assertTrue(BrowserRules.search("x", "https://example.com/no-placeholder").startsWith("https://www.google.com/"))
    }

    @Test fun onlySecurePagesLoadAndPlainHttpIsExplained() {
        assertEquals(Target.Insecure("http://example.com/"), BrowserRules.resolve("http://example.com/", google))
        assertEquals(Target.Insecure("http://192.168.1.5:5173/"), BrowserRules.classify("http://192.168.1.5:5173/"))
        assertEquals("https://example.com/a", BrowserRules.secureVersion("http://example.com/a"))
        assertNull(BrowserRules.secureVersion("https://example.com/a"))
    }

    @Test fun scriptsFilesAndInternalPagesNeverOpen() {
        for (url in listOf("javascript:alert(1)", "file:///sdcard/x.html", "content://dev.raticode.mobile.opened/x",
                "data:text/html,<b>x</b>", "blob:https://e.com/1", "chrome://settings", "view-source:https://e.com", "about:config")) {
            assertTrue(url, BrowserRules.classify(url) is Target.Blocked)
        }
        assertTrue(BrowserRules.resolve("javascript:alert(1)", google) is Target.Blocked)
        assertTrue(BrowserRules.classify("https://user:pw@example.com/") is Target.Blocked)
    }

    @Test fun signInHandOffsWithLongAddressesLoad() {
        // Sign-in pages carry tokens and nested return addresses. Pages used to be held to the computer link's 2,048
        // characters, which refused such an address with "can't open this kind of address"; the likeliest cause of a
        // refusal seen after Google's sign-in, not a measured one. The address below is made up.
        val handOff = "https://accounts.youtube.com/accounts/SetSID?ssdc=1&sidt=" + "A".repeat(2600) +
            "&continue=https%3A%2F%2Fmyaccount.google.com%2F%3Fhl%3Den"
        assertEquals(Target.Load(handOff), BrowserRules.classify(handOff))
        assertEquals(Target.Insecure("http://e.com/?" + "x".repeat(3000)), BrowserRules.classify("http://e.com/?" + "x".repeat(3000)))
        assertEquals("accounts.youtube.com", BrowserRules.host(handOff))
        val huge = "https://e.com/?" + "x".repeat(BrowserRules.MAX_PAGE_ADDRESS)
        assertEquals(Target.Blocked(huge, BrowserRules.BLOCKED_TOO_LONG), BrowserRules.classify(huge))
        // Saved tabs keep a short address without the one-time tokens; bookmarks and history skip such pages.
        assertEquals("https://accounts.youtube.com/accounts/SetSID", BrowserRules.storable(handOff))
        assertEquals("https://e.com/", BrowserRules.storable("https://e.com/" + "p".repeat(3000) + "?q=1"))
        assertEquals("https://e.com/a?b=c", BrowserRules.storable("https://e.com/a?b=c"))
        assertEquals("about:blank", BrowserRules.storable("javascript:alert(1)"))
        assertTrue(BrowserRules.toggleBookmark(emptyList(), handOff, "Google", 1).isEmpty())
        assertTrue(BrowserRules.record(emptyList(), handOff, "Google", 1).isEmpty())
    }

    @Test fun refusalsSayWhichKindOfAddressWasRefused() {
        assertEquals(Target.Blocked("https://", BrowserRules.BLOCKED_MALFORMED), BrowserRules.classify("https://"))
        assertEquals(Target.Blocked("data:text/html,x", "Raticode's browser can't open data: addresses."), BrowserRules.classify("data:text/html,x"))
        assertEquals(Target.Load("about:blank#top"), BrowserRules.classify("about:blank#top"))
        assertEquals(Target.Load("about:blank"), BrowserRules.classify("ABOUT:BLANK"))
        assertTrue(BrowserRules.classify("about:config") is Target.Blocked)
    }

    @Test fun otherAppsOpenOnlyThroughAQuestion() {
        for (url in listOf("mailto:me@example.com", "tel:+15551234", "geo:0,0?q=cafe", "market://details?id=x",
                "intent://scan/#Intent;scheme=zxing;package=com.x;end")) {
            assertEquals(url, Target.App(url), BrowserRules.classify(url))
        }
        // A page may name its own app scheme; the user still has to agree before it opens.
        assertEquals(Target.App("slack://open"), BrowserRules.classify("slack://open"))
        assertTrue(BrowserRules.resolve("mailto:me@example.com", google) is Target.App)
    }

    @Test fun hostsAndTitlesAreShort() {
        assertEquals("example.com", BrowserRules.host("https://www.Example.com:8443/a?b#c"))
        assertEquals("", BrowserRules.host("about:blank"))
        assertEquals("Docs", BrowserRules.title("  Docs \n", "https://e.com"))
        assertEquals("e.com", BrowserRules.title("", "https://e.com/x"))
        assertEquals("New tab", BrowserRules.title(null, "about:blank"))
        assertEquals(BrowserRules.Security.SECURE, BrowserRules.security("https://e.com"))
        assertEquals(BrowserRules.Security.BLANK, BrowserRules.security("about:blank"))
        assertEquals(BrowserRules.Security.OTHER, BrowserRules.security("http://e.com"))
    }

    @Test fun historyKeepsSecurePagesNewestFirstWithoutRepeats() {
        var history = BrowserRules.record(emptyList(), "https://a.com/", "A", 1000)
        history = BrowserRules.record(history, "https://a.com/", null, 2000)
        assertEquals(1, history.size); assertEquals("A", history[0].title); assertEquals(2000, history[0].at)
        history = BrowserRules.record(history, "https://b.com/", "B", 3000)
        history = BrowserRules.record(history, "http://c.com/", "C", 4000)
        history = BrowserRules.record(history, "about:blank", "", 5000)
        assertEquals(listOf("https://b.com/", "https://a.com/"), history.map { it.url })
        val full = (0 until BrowserRules.MAX_HISTORY + 5).fold(emptyList<BrowserRules.Visit>()) { h, i -> BrowserRules.record(h, "https://e.com/$i", "", i.toLong()) }
        assertEquals(BrowserRules.MAX_HISTORY, full.size)
        assertEquals("https://e.com/${BrowserRules.MAX_HISTORY + 4}", full.first().url)
    }

    @Test fun clearingHistoryHonoursTheRange() {
        val now = 10_000_000L
        val history = listOf(BrowserRules.Visit("https://new.com", "", now - 60_000), BrowserRules.Visit("https://old.com", "", now - 7_200_000))
        assertEquals(listOf("https://old.com"), BrowserRules.clearHistory(history, now, 3600).map { it.url })
        assertTrue(BrowserRules.clearHistory(history, now, null).isEmpty())
    }

    @Test fun bookmarksToggleAndStaySecure() {
        var marks = BrowserRules.toggleBookmark(emptyList(), "https://a.com", "A", 1)
        assertEquals(1, marks.size)
        marks = BrowserRules.toggleBookmark(marks, "http://b.com", "B", 2)
        assertEquals(1, marks.size)
        assertTrue(BrowserRules.toggleBookmark(marks, "https://a.com", "A", 3).isEmpty())
    }

    @Test fun suggestionsMatchEveryWordAndPreferBookmarks() {
        val marks = listOf(BrowserRules.Bookmark("https://docs.raticode.dev", "Raticode docs", 1))
        val history = listOf(BrowserRules.Visit("https://docs.raticode.dev", "Raticode docs", 2), BrowserRules.Visit("https://news.com/raticode", "News", 3))
        val tabs = listOf("https://github.com/raticode" to "GitHub")
        val found = BrowserRules.suggest("raticode", marks, tabs, history)
        assertEquals(listOf("Bookmark", "Open tab", "History"), found.map { it.kind })
        assertEquals(listOf("https://docs.raticode.dev"), BrowserRules.suggest("raticode docs", marks, tabs, history).map { it.url })
        assertTrue(BrowserRules.suggest(" ", marks, tabs, history).isEmpty())
    }

    @Test fun desktopSiteKeepsTheEngineVersionButNotTheMobilePlatform() {
        val mobile = "Mozilla/5.0 (Linux; Android 16; Pixel 8 Build/BP2A; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/141.0.0.0 Mobile Safari/537.36"
        val desktop = BrowserRules.desktopUserAgent(mobile)
        assertEquals("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/141.0.0.0 Safari/537.36", desktop)
        assertEquals(200, BrowserRules.textZoom(195, 10)); assertEquals(50, BrowserRules.textZoom(55, -10))
    }

    @Test fun downloadsAreNamedSafelyAndInstallersWarn() {
        assertTrue(BrowserRules.risky("app.apk", null))
        assertTrue(BrowserRules.risky("file.bin", "application/vnd.android.package-archive"))
        assertFalse(BrowserRules.risky("report.pdf", "application/pdf"))
        assertEquals("passwd", BrowserRules.safeName("../../etc/passwd"))
        assertEquals("bashrc", BrowserRules.safeName(".bashrc"))
        assertEquals("download", BrowserRules.safeName("\u0000"))
        assertEquals("ab.txt", BrowserRules.safeName("a<|>b.txt"))
        val long = BrowserRules.safeName("x".repeat(300) + ".pdf")
        assertEquals(120, long.length); assertTrue(long.endsWith(".pdf"))
        assertEquals("1.5 MB", BrowserRules.size(1_572_864)); assertEquals("Unknown size", BrowserRules.size(-1))
    }

    @Test fun savedStateRoundTripsAndDropsAnythingButWebAddresses() {
        val saved = BrowserRules.Saved(
            tabs = listOf(BrowserRules.SavedTab("t1", "https://a.com", "A"), BrowserRules.SavedTab("t2", "about:blank", "")),
            active = "t2", history = listOf(BrowserRules.Visit("https://a.com", "A", 5)),
            bookmarks = listOf(BrowserRules.Bookmark("https://a.com", "A", 6)), keepHistory = false, linksInApp = false,
            search = "https://duckduckgo.com/?q={query}", computerSearch = "https://www.bing.com/search?q={query}", textZoom = 120)
        assertEquals(saved, BrowserRules.decode(BrowserRules.encode(saved)))
        val tampered = BrowserRules.encode(saved).replace("https://a.com", "javascript:alert(1)")
        val read = BrowserRules.decode(tampered)
        assertEquals("about:blank", read.tabs[0].url)
        assertTrue(read.history.isEmpty()); assertTrue(read.bookmarks.isEmpty())
        assertEquals(BrowserRules.Saved(), BrowserRules.decode("not json"))
        assertEquals("computer", BrowserRules.decode("""{"search":"javascript:{query}"}""").search)
        assertEquals(BrowserRules.DEFAULT_SEARCH, BrowserRules.searchTemplate(BrowserRules.Saved()))
        assertEquals("https://www.bing.com/search?q={query}", BrowserRules.searchTemplate(BrowserRules.Saved(computerSearch = "https://www.bing.com/search?q={query}")))
    }

    @Test fun privateTabsNeverReachStorage() {
        val saved = BrowserRules.Saved(
            tabs = listOf(BrowserRules.SavedTab("open", "https://open.example/", "Open"), BrowserRules.SavedTab("hidden", "https://secret.example/", "Hidden title", private = true)),
            active = "hidden",
        )
        val text = BrowserRules.encode(saved)
        assertFalse(text.contains("secret.example")); assertFalse(text.contains("Hidden title")); assertFalse(text.contains("\"hidden\""))
        val back = BrowserRules.decode(text)
        assertEquals(listOf("open"), back.tabs.map { it.id })
        assertEquals("open", back.active)
    }
}
