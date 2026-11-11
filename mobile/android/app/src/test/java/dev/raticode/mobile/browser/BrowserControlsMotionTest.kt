package dev.raticode.mobile.browser

import dev.raticode.mobile.browser.BrowserControlsMotion.Companion.HIDE_AFTER_DP
import dev.raticode.mobile.browser.BrowserControlsMotion.Companion.REVEAL_HOLD_MS
import dev.raticode.mobile.browser.BrowserControlsMotion.Companion.SETTLE_MS
import dev.raticode.mobile.browser.BrowserControlsMotion.Companion.SHOW_AFTER_DP
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BrowserControlsMotionTest {
    private val motion = BrowserControlsMotion()
    private var clock = 1_000L
    private fun scroll(y: Int) { clock += 16; motion.scrolled(y, clock) }
    private fun settle() { clock += REVEAL_HOLD_MS + SETTLE_MS + 1 }

    @Test fun scrollingDownFoldsOnceThePageHasReallyMoved() {
        scroll(500); scroll(500 + HIDE_AFTER_DP - 1)
        assertTrue("A nudge leaves the controls alone", motion.expanded)
        scroll(500 + HIDE_AFTER_DP)
        assertFalse(motion.expanded)
    }

    @Test fun scrollingUpBringsThemBackAfterAShortDistance() {
        scroll(500); scroll(600); assertFalse(motion.expanded)
        settle()
        scroll(600); scroll(600 - SHOW_AFTER_DP + 1)
        assertFalse("A small movement while reading doesn't", motion.expanded)
        scroll(600 - SHOW_AFTER_DP)
        assertTrue(motion.expanded)
    }

    @Test fun aChangeOfDirectionStartsTheDistanceAgain() {
        scroll(500); scroll(520); scroll(505); scroll(525)
        assertTrue("Back and forth under the distance never folds", motion.expanded)
    }

    @Test fun reachingTheTopAlwaysBringsThemBack() {
        scroll(100); scroll(200); assertFalse(motion.expanded)
        settle()
        scroll(10); assertTrue("Far enough up", motion.expanded)
        settle()
        scroll(300); assertFalse(motion.expanded)
        settle()
        scroll(300); scroll(296); assertFalse(motion.expanded)
        scroll(1); assertTrue("At the top, however short the move", motion.expanded)
    }

    @Test fun theTopOfThePageDoesNotFold() {
        scroll(0); scroll(2)
        assertTrue(motion.expanded)
    }

    @Test fun thePageMovingBecauseItWasResizedIsIgnored() {
        scroll(500); scroll(600); assertFalse(motion.expanded)
        // Folding gave the page more room, and at the end of a page the browser pulls the position back up.
        scroll(600 - SHOW_AFTER_DP - 10)
        assertFalse("No fold and unfold loop", motion.expanded)
        settle()
        scroll(600 - SHOW_AFTER_DP - 10 - SHOW_AFTER_DP)
        assertTrue("The user's own scrolling counts again", motion.expanded)
    }

    @Test fun heldControlsStayOpenAndKeepTrackOfThePage() {
        scroll(500)
        motion.hold(true, clock)
        scroll(900); assertTrue(motion.expanded)
        motion.hold(false, clock)
        settle()
        scroll(900 + HIDE_AFTER_DP - 1)
        assertTrue("Only scrolling after the hold counts", motion.expanded)
        scroll(900 + HIDE_AFTER_DP); assertFalse(motion.expanded)
    }

    @Test fun holdingOpensFoldedControls() {
        scroll(500); scroll(600); assertFalse(motion.expanded)
        motion.hold(true, clock)
        assertTrue(motion.expanded)
    }

    @Test fun anotherPageStartsFresh() {
        scroll(4000); scroll(4100); assertFalse(motion.expanded)
        motion.pageChanged(clock)
        assertTrue("A new page shows the controls", motion.expanded)
        settle()
        scroll(0); scroll(40); assertFalse(motion.expanded)
    }

    @Test fun idleWaitsAfterTheLastUse() {
        motion.touched(clock)
        assertEquals(5000, motion.idleRemaining(clock, 5000))
        motion.touched(clock + 3000)
        assertEquals(2000, motion.idleRemaining(clock + 6000, 5000))
        assertEquals(Long.MAX_VALUE, motion.idleRemaining(clock, Long.MAX_VALUE))
        motion.idled(clock + 8000)
        assertFalse(motion.expanded)
    }

    @Test fun scrollingUpCountsAsUse() {
        scroll(500); settle(); scroll(490)
        assertEquals(5000, motion.idleRemaining(clock, 5000))
    }

    @Test fun nothingFoldsWhileHeld() {
        motion.hold(true, clock)
        motion.idled(clock + 60_000)
        assertTrue(motion.expanded)
    }

    @Test fun revealRestartsTheWait() {
        motion.touched(clock); motion.reveal(clock + 4000)
        assertEquals(5000, motion.idleRemaining(clock + 4000, 5000))
    }

    @Test fun aFlingThatWasStillRunningDoesNotFoldThemAgain() {
        scroll(500); scroll(800); assertFalse(motion.expanded)
        settle(); scroll(900)
        motion.reveal(clock)
        for (y in 1000..2000 step 100) { clock += 100; motion.scrolled(y, clock); assertTrue("Still at $y", motion.expanded) }
        // The swipe has run out; the next drag is the user's.
        clock += REVEAL_HOLD_MS
        motion.scrolled(2001, clock); assertTrue(motion.expanded)
        clock += 16; motion.scrolled(2001 + HIDE_AFTER_DP, clock); assertFalse(motion.expanded)
    }

    @Test fun aNewPageCanFoldStraightAway() {
        motion.pageChanged(clock)
        scroll(0); scroll(HIDE_AFTER_DP + 3)
        assertFalse(motion.expanded)
    }

    @Test fun forwardGoesToTheMenuRatherThanSqueezeTheAddress() {
        assertTrue(BrowserControlsMotion.forwardFits(360, hasPageBack = false))
        assertFalse("Small phone", BrowserControlsMotion.forwardFits(320, hasPageBack = false))
        assertFalse("Opened from More at 360 dp", BrowserControlsMotion.forwardFits(360, hasPageBack = true))
        assertTrue("Opened from More on a wide phone", BrowserControlsMotion.forwardFits(411, hasPageBack = true))
    }
}
