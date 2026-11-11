package dev.raticode.mobile.childlock

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class EdgeHandleTest {
    @Test fun aShortSlowDragSnapsBackAndALongOneTucksAway() {
        assertFalse(EdgeHandle.hidesAfterDrag(0.1f, 0f))
        assertFalse(EdgeHandle.hidesAfterDrag(EdgeHandle.HIDE_SHARE, 0f))
        assertTrue(EdgeHandle.hidesAfterDrag(0.6f, 0f))
    }

    @Test fun aFlickDecidesOnItsOwn() {
        assertTrue("a quick flick right hides it", EdgeHandle.hidesAfterDrag(0.05f, EdgeHandle.FLING_DP_PER_S + 1))
        assertFalse("a quick flick back out keeps it", EdgeHandle.hidesAfterDrag(0.9f, -EdgeHandle.FLING_DP_PER_S - 1))
    }

    @Test fun itStaysOutWhileArmedHeldOrAsking() {
        assertTrue(EdgeHandle.mayIdleHide(armed = false, holding = false, asking = false))
        assertFalse(EdgeHandle.mayIdleHide(armed = true, holding = false, asking = false))
        assertFalse(EdgeHandle.mayIdleHide(armed = false, holding = true, asking = false))
        assertFalse(EdgeHandle.mayIdleHide(armed = false, holding = false, asking = true))
    }

    @Test fun startsAwayAndEveryShowRestartsTheIdleTime() {
        EdgeHandle.hide()
        assertFalse(EdgeHandle.shown)
        val before = EdgeHandle.touches
        EdgeHandle.show()
        assertTrue(EdgeHandle.shown)
        assertEquals(before + 1, EdgeHandle.touches)
        EdgeHandle.touched()
        assertEquals(before + 2, EdgeHandle.touches)
        EdgeHandle.hide()
        assertFalse(EdgeHandle.shown)
    }

    @Test fun theLockedHintSaysHowToReachTheLock() {
        assertTrue(EdgeHandle.lockedHint(shown = false).contains("Tap the right edge"))
        assertTrue(EdgeHandle.lockedHint(shown = true).contains("Press and hold the lock"))
    }

    @Test fun theHoldIsLongEnoughToBeDeliberate() {
        assertTrue(EdgeHandle.HOLD_MS >= 1_000L)
    }
}
