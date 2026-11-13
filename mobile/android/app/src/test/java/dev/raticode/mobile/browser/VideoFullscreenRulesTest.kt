package dev.raticode.mobile.browser

import android.content.pm.ActivityInfo
import dev.raticode.mobile.browser.BrowserMediaRules.Engine
import dev.raticode.mobile.browser.VideoFullscreenRules.Video
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

class VideoFullscreenRulesTest {
    private val wide = Video(Engine.CHROMIUM, "a", 1920, 1080)
    private val upright = Video(Engine.GECKO, "b", 1080, 1920)
    private val unknown = Video(Engine.CHROMIUM, "a")

    @After fun reset() { VideoFullscreen.leave(Engine.CHROMIUM); VideoFullscreen.leave(Engine.GECKO) }

    @Test fun aFullScreenVideoOnTheBrowserPageTakesTheWholeScreenInLandscape() {
        assertTrue(VideoFullscreenRules.covers(wide, "Browser"))
        assertTrue(VideoFullscreenRules.takesWindow(wide, "Browser", appLocked = false))
        assertEquals(ActivityInfo.SCREEN_ORIENTATION_USER_LANDSCAPE, VideoFullscreenRules.orientation(wide, "Browser", appLocked = false))
    }

    @Test fun aVideoOfUnknownSizeTurnsToLandscapeAndAnUprightOneStaysUpright() {
        assertEquals(ActivityInfo.SCREEN_ORIENTATION_USER_LANDSCAPE, VideoFullscreenRules.orientation(unknown, "Browser", false))
        assertEquals(ActivityInfo.SCREEN_ORIENTATION_USER_PORTRAIT, VideoFullscreenRules.orientation(upright, "Browser", false))
        // A square video counts as landscape, as phones show most players that way.
        assertEquals(ActivityInfo.SCREEN_ORIENTATION_USER_LANDSCAPE, VideoFullscreenRules.orientation(wide.copy(width = 720, height = 720), "Browser", false))
    }

    @Test fun nothingChangesWithoutAFullScreenVideoOrAwayFromTheBrowser() {
        assertFalse(VideoFullscreenRules.covers(null, "Browser"))
        assertFalse(VideoFullscreenRules.covers(wide, "Rem"))
        assertFalse(VideoFullscreenRules.takesWindow(wide, "Threads", false))
        assertEquals(VideoFullscreenRules.RESTORE_ORIENTATION, VideoFullscreenRules.orientation(null, "Browser", false))
        assertEquals(VideoFullscreenRules.RESTORE_ORIENTATION, VideoFullscreenRules.orientation(wide, "Rem", false))
        assertEquals(ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED, VideoFullscreenRules.RESTORE_ORIENTATION)
    }

    @Test fun appLockGetsTheBarsAndTheUsualOrientationButTheVideoStaysFullScreenUnderIt() {
        assertTrue("Raticode's own bars stay hidden under the lock's cover", VideoFullscreenRules.covers(wide, "Browser"))
        assertFalse(VideoFullscreenRules.takesWindow(wide, "Browser", appLocked = true))
        assertEquals(VideoFullscreenRules.RESTORE_ORIENTATION, VideoFullscreenRules.orientation(wide, "Browser", appLocked = true))
    }

    @Test fun enteringSizingAndLeavingFollowTheOwningTab() {
        val entered = VideoFullscreenRules.entered(null, Engine.CHROMIUM, "a")
        assertEquals(unknown, entered)
        val sized = VideoFullscreenRules.sized(entered, Engine.CHROMIUM, "a", 1920, 1080)
        assertEquals(wide, sized)
        // Entering again for the same tab keeps the size already learned.
        assertSame(sized, VideoFullscreenRules.entered(sized, Engine.CHROMIUM, "a"))
        // Another tab or browser can't resize or end it, and a nonsense size is ignored.
        assertSame(sized, VideoFullscreenRules.sized(sized, Engine.CHROMIUM, "b", 10, 20))
        assertSame(sized, VideoFullscreenRules.sized(sized, Engine.GECKO, "a", 10, 20))
        assertSame(sized, VideoFullscreenRules.sized(sized, Engine.CHROMIUM, "a", 0, 20))
        assertSame(sized, VideoFullscreenRules.left(sized, Engine.CHROMIUM, "b"))
        assertSame(sized, VideoFullscreenRules.left(sized, Engine.GECKO, "a"))
        assertNull(VideoFullscreenRules.left(sized, Engine.CHROMIUM, "a"))
        // Another browser going full screen takes over.
        assertEquals(Video(Engine.GECKO, "z"), VideoFullscreenRules.entered(sized, Engine.GECKO, "z"))
    }

    @Test fun showingAnotherTabOfTheSameBrowserEndsFullScreen() {
        assertTrue(VideoFullscreenRules.endsOnSelect(wide, Engine.CHROMIUM, "other"))
        assertFalse(VideoFullscreenRules.endsOnSelect(wide, Engine.CHROMIUM, "a"))
        assertFalse(VideoFullscreenRules.endsOnSelect(wide, Engine.GECKO, "other"))
        assertFalse(VideoFullscreenRules.endsOnSelect(null, Engine.CHROMIUM, "other"))
    }

    @Test fun thePagesAnswerAboutTheVideoSizeIsReadCarefully() {
        assertEquals(1920L to 1080L, VideoFullscreenRules.parseSize("\"1920x1080\""))
        assertEquals(360L to 640L, VideoFullscreenRules.parseSize("360x640"))
        for (bad in listOf(null, "", "\"\"", "null", "0x0", "1920", "1920x", "x1080", "-1x5", "axb", "1x2x3", "999999x1"))
            assertNull(bad, VideoFullscreenRules.parseSize(bad))
    }

    @Test fun theSharedStateFollowsBothBrowsers() {
        VideoFullscreen.enter(Engine.GECKO, "g")
        assertEquals(Video(Engine.GECKO, "g"), VideoFullscreen.current)
        VideoFullscreen.size(Engine.GECKO, "g", 640, 360)
        assertEquals(Video(Engine.GECKO, "g", 640, 360), VideoFullscreen.current)
        VideoFullscreen.leave(Engine.CHROMIUM)
        assertEquals("The other browser can't end it", Engine.GECKO, VideoFullscreen.current?.engine)
        VideoFullscreen.leave(Engine.GECKO, "other")
        assertEquals("Another tab can't end it", "g", VideoFullscreen.current?.tab)
        VideoFullscreen.leave(Engine.GECKO, "g")
        assertNull(VideoFullscreen.current)
    }
}
