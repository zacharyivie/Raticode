package dev.raticode.mobile.browser

import dev.raticode.mobile.browser.BrowserMediaRules.Action
import dev.raticode.mobile.browser.BrowserMediaRules.Engine
import dev.raticode.mobile.browser.BrowserMediaRules.Key
import dev.raticode.mobile.browser.BrowserMediaRules.Report
import dev.raticode.mobile.browser.BrowserMediaRules.Track
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** What Android's lock screen and notification controls may show and obey, from the same track the media bar shows. */
class BrowserMediaControlsRulesTest {
    private fun track(playing: Boolean = true, canToggle: Boolean = true, previous: Boolean = false, next: Boolean = false,
                      private: Boolean = false, artist: String = "", artwork: String? = "https://img.example/a.jpg") =
        Track(Key(Engine.CHROMIUM, "a"), "Song", artist, "music.example", playing, canToggle, previous, next, artwork, 1, 0, private)

    @Test fun anOrdinaryTabShowsItsTitleSiteAndPicture() {
        val shown = BrowserMediaRules.shown(track(artist = "Band"), hidden = false)
        assertEquals("Song", shown.title)
        assertEquals("Band · music.example", shown.subtitle)
        assertEquals("https://img.example/a.jpg", shown.artwork)
        assertEquals("music.example", BrowserMediaRules.shown(track(), hidden = false).subtitle)
    }

    @Test fun aPrivateTabNeverShowsItsPage() {
        for (hidden in listOf(false, true)) {
            val shown = BrowserMediaRules.shown(track(private = true, artist = "Band"), hidden)
            assertEquals(BrowserMediaRules.PRIVATE_TITLE, shown.title)
            assertFalse("Song" in shown.subtitle || "music.example" in shown.subtitle || "Band" in shown.subtitle)
            assertNull(shown.artwork)
        }
    }

    @Test fun hiddenShowsOnlyThatTheBrowserIsPlaying() {
        val shown = BrowserMediaRules.shown(track(artist = "Band"), hidden = true)
        assertEquals(BrowserMediaRules.HIDDEN_TITLE, shown.title)
        assertEquals(BrowserMediaRules.APP_SUBTITLE, shown.subtitle)
        assertNull(shown.artwork)
    }

    @Test fun titlesHideOnlyOnALockedPhoneWhenTurnedOffOrAppLocked() {
        assertFalse(BrowserMediaRules.hideOnLockScreen(phoneLocked = false, showTitles = false, appLock = true))
        assertFalse(BrowserMediaRules.hideOnLockScreen(phoneLocked = true, showTitles = true, appLock = false))
        assertTrue(BrowserMediaRules.hideOnLockScreen(phoneLocked = true, showTitles = false, appLock = false))
        assertTrue(BrowserMediaRules.hideOnLockScreen(phoneLocked = true, showTitles = true, appLock = true))
    }

    @Test fun buttonsMatchWhatThePageOffers() {
        assertEquals(listOf(Action.PAUSE), BrowserMediaRules.buttons(track()))
        assertEquals(listOf(Action.PLAY), BrowserMediaRules.buttons(track(playing = false)))
        assertEquals(listOf(Action.PREVIOUS, Action.PAUSE, Action.NEXT), BrowserMediaRules.buttons(track(previous = true, next = true)))
        assertEquals(listOf(Action.NEXT), BrowserMediaRules.buttons(track(canToggle = false, next = true)))
        assertEquals(emptyList<Action>(), BrowserMediaRules.buttons(track(canToggle = false)))
    }

    @Test fun pressesFromOutsideOnlyDoWhatTheBarWould() {
        val playing = track(next = true)
        assertTrue(BrowserMediaRules.allowed(playing, Action.PAUSE))
        assertTrue(BrowserMediaRules.allowed(playing, Action.NEXT))
        assertFalse("Already playing", BrowserMediaRules.allowed(playing, Action.PLAY))
        assertFalse("The page offers no Previous", BrowserMediaRules.allowed(playing, Action.PREVIOUS))
        assertFalse("Nothing to resume", BrowserMediaRules.allowed(track(playing = false, canToggle = false), Action.PLAY))
    }

    @Test fun aTrackRemembersItCameFromAPrivateTab() {
        val key = Key(Engine.GECKO, "p")
        val started = BrowserMediaRules.merge(null, key, Report(true, "Song", site = "music.example", canToggle = true, private = true), 10)!!
        assertTrue(started.private)
        assertTrue(BrowserMediaRules.merge(started, key, Report(false, "Song", site = "music.example", canToggle = true, private = true), 20)!!.private)
        assertFalse(BrowserMediaRules.merge(null, key, Report(true, "Song"), 10)!!.private)
    }
}
