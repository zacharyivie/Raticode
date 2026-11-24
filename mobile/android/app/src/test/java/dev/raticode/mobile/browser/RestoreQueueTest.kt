package dev.raticode.mobile.browser

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Gecko tab-restore race: a tab reopened from its saved list, then given a new address. GeckoView applies the
 * restore later, so the address must wait for the restored page, whichever order Gecko reports things in.
 */
class RestoreQueueTest {
    private var now = 1_000_000L
    private val queue = RestoreQueue { now }

    /**
     * A tiny stand-in for a Gecko session: restoreState is applied whenever the test says, like GeckoView's own thread,
     * and loadUri wins only if it comes after the restore. It records what the tab finally shows.
     */
    private inner class FakeTab(val id: String) {
        var showing = "about:blank"
        val loads = mutableListOf<String>()
        fun restore() { queue.started(id) }
        fun loadUri(url: String) { loads += url; showing = url }
        fun restoreLands(restoredUrl: String) {
            showing = restoredUrl
            queue.locationChanged(id, restoredUrl)?.let { loadUri(it) }
        }
        /** What GeckoBrowser.load does with the queue. */
        fun load(url: String) { if (!queue.defer(id, url)) loadUri(url) }
    }

    @Test fun anAddressTypedWhileTheRestoreIsPendingLoadsAfterTheRestoredPage() {
        val tab = FakeTab("a")
        tab.restore()
        tab.load("https://new.example/")
        // Nothing loads before the restore, so the restored page can't replace the new address.
        assertEquals(emptyList<String>(), tab.loads)
        tab.restoreLands("https://old.example/")
        assertEquals("https://new.example/", tab.showing)
        assertEquals(listOf("https://new.example/"), tab.loads)
    }

    @Test fun aRestoreThatLandsBeforeTheAddressLetsItLoadAtOnce() {
        val tab = FakeTab("a")
        tab.restore()
        tab.restoreLands("https://old.example/")
        tab.load("https://new.example/")
        assertEquals("https://new.example/", tab.showing)
        assertEquals(listOf("https://new.example/"), tab.loads)
    }

    @Test fun theNewSessionsBlankPageDoesntEndTheWait() {
        val tab = FakeTab("a")
        tab.restore()
        tab.load("https://new.example/")
        assertNull(queue.locationChanged("a", "about:blank"))
        tab.restoreLands("https://old.example/")
        assertEquals("https://new.example/", tab.showing)
    }

    @Test fun theNewestAddressWins() {
        val tab = FakeTab("a")
        tab.restore()
        tab.load("https://first.example/")
        tab.load("https://second.example/")
        tab.restoreLands("https://old.example/")
        assertEquals(listOf("https://second.example/"), tab.loads)
    }

    @Test fun aRestoreThatNeverReportsStillLoadsTheAddressAfterTheWait() {
        val tab = FakeTab("a")
        tab.restore()
        tab.load("https://new.example/")
        now += RestoreQueue.WAIT_MS - 1
        assertNull(queue.expired("a"))
        now += 1
        assertEquals("https://new.example/", queue.expired("a"))
        // Only once, and a late restore no longer triggers anything.
        assertNull(queue.expired("a"))
        assertNull(queue.locationChanged("a", "https://old.example/"))
    }

    @Test fun anOverdueRestoreDoesntHoldUpLaterAddresses() {
        queue.started("a")
        now += RestoreQueue.WAIT_MS
        assertFalse(queue.defer("a", "https://new.example/"))
    }

    @Test fun ordinaryNavigationAndOtherTabsAreUntouched() {
        assertFalse(queue.defer("a", "https://new.example/"))
        queue.started("b")
        assertFalse(queue.defer("a", "https://new.example/"))
        assertTrue(queue.defer("b", "https://new.example/"))
        // A plain reopen with nothing waiting just ends the wait.
        queue.started("c")
        assertNull(queue.locationChanged("c", "https://old.example/"))
        assertFalse(queue.defer("c", "https://new.example/"))
    }

    @Test fun aClosedSessionDropsWhatWaited() {
        queue.started("a")
        assertTrue(queue.defer("a", "https://new.example/"))
        queue.forget("a")
        now += RestoreQueue.WAIT_MS
        assertNull(queue.expired("a"))
        assertNull(queue.locationChanged("a", "https://old.example/"))
    }
}
