package dev.raticode.mobile

import androidx.activity.ComponentActivity
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import dev.raticode.mobile.childlock.EdgeHandle
import dev.raticode.mobile.ui.ChildLockHandle
import dev.raticode.mobile.ui.RaticodeTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test

/**
 * The child lock's edge handle, on its own: it starts tucked away, a tap on the edge brings it out, a swipe right or
 * a few idle seconds put it away, two taps turn the lock on, and only a long hold asks to unlock. Needs no desktop and
 * never turns the real lock on.
 */
class ChildLockHandleTest {
    @get:Rule val compose = createAndroidComposeRule<ComponentActivity>()
    private var actions = 0

    @Before fun tuckAway() { compose.runOnUiThread { EdgeHandle.hide(); EdgeHandle.justLocked = false } }

    private fun show(locked: Boolean) = compose.setContent { RaticodeTheme {
        Box(Modifier.fillMaxSize().testTag("page")) {
            ChildLockHandle(locked = locked, onAction = { actions++ }, modifier = Modifier.align(Alignment.CenterEnd))
        }
    } }

    private fun handle(locked: Boolean) = compose.onNodeWithTag(if (locked) "childLockUnlock" else "childLockButton")
    private val pageRight get() = compose.onNodeWithTag("page").fetchSemanticsNode().boundsInRoot.right
    private fun isOut(locked: Boolean) = handle(locked).fetchSemanticsNode().boundsInRoot.right.let { kotlin.math.abs(it - pageRight) < 2f }
    private fun isAway(locked: Boolean) = handle(locked).fetchSemanticsNode().boundsInRoot.left >= pageRight - 1f
    private fun settle() { compose.mainClock.advanceTimeBy(1_000); compose.waitForIdle() }

    @Test fun startsAwayAndATapOnTheEdgeSlidesItIn() {
        show(locked = false)
        settle()
        assertTrue("starts past the edge", isAway(false))
        compose.onNodeWithTag("childLockReveal").performTouchInput { click(centerRight - androidx.compose.ui.geometry.Offset(4f, 0f)) }
        settle()
        assertTrue("slid in", isOut(false))
        handle(false).assertContentDescriptionEquals("Child lock")
    }

    @Test fun aSwipeRightPutsItAwayAndIdleTimeDoesToo() {
        show(locked = false)
        compose.runOnUiThread { EdgeHandle.show() }
        settle()
        assertTrue(isOut(false))
        handle(false).performTouchInput { swipeRight(startX = centerX - 20f, endX = right + 200f, durationMillis = 150) }
        settle()
        assertTrue("swiped away", isAway(false))
        assertFalse(EdgeHandle.shown)

        compose.runOnUiThread { EdgeHandle.show() }
        settle()
        assertTrue(isOut(false))
        compose.mainClock.advanceTimeBy(EdgeHandle.IDLE_MS * 4)
        settle()
        assertTrue("tucked itself away", isAway(false))
    }

    @Test fun aShortDragSnapsBack() {
        show(locked = false)
        compose.runOnUiThread { EdgeHandle.show() }
        settle()
        handle(false).performTouchInput { down(center); moveBy(androidx.compose.ui.geometry.Offset(width * 0.25f, 0f)); up() }
        settle()
        assertTrue("came back out", isOut(false))
        assertEquals(0, actions)
    }

    @Test fun twoTapsTurnTheLockOnAndOneDoesNot() {
        show(locked = false)
        compose.runOnUiThread { EdgeHandle.show() }
        settle()
        handle(false).performTouchInput { click() }
        handle(false).assertContentDescriptionEquals("Child lock. Tap again to turn it on")
        assertEquals(0, actions)
        handle(false).performTouchInput { click() }
        compose.waitForIdle()
        assertEquals(1, actions)
    }

    @Test fun lockedOnlyALongHoldAsksToUnlock() {
        show(locked = true)
        compose.runOnUiThread { EdgeHandle.show() }
        settle()
        handle(true).assertContentDescriptionEquals("Child lock is on. Press and hold to unlock")
        handle(true).performTouchInput { click() }
        settle()
        assertEquals("a tap does nothing", 0, actions)
        // The ring's animation would otherwise let the test clock run straight through the hold.
        compose.mainClock.autoAdvance = false
        handle(true).performTouchInput { down(center) }
        compose.mainClock.advanceTimeBy(EdgeHandle.HOLD_MS / 2)
        handle(true).performTouchInput { up() }
        compose.mainClock.advanceTimeBy(EdgeHandle.HOLD_MS * 2)
        assertEquals("half a hold does nothing", 0, actions)
        handle(true).performTouchInput { down(center) }
        compose.mainClock.advanceTimeBy(EdgeHandle.HOLD_MS + 100)
        assertEquals("a full hold asks once", 1, actions)
        handle(true).performTouchInput { up() }
        compose.mainClock.advanceTimeBy(500)
        compose.mainClock.autoAdvance = true
        compose.waitForIdle()
        assertEquals(1, actions)
    }

    @Test fun talkBackReachesTheLockFromTheEdgeMark() {
        show(locked = true)
        settle()
        val reveal = compose.onNodeWithTag("childLockReveal")
        reveal.assertContentDescriptionEquals("Child lock is on. Show the lock")
        reveal.performSemanticsAction(SemanticsActions.OnClick)
        settle()
        assertTrue(isOut(true))
        handle(true).performSemanticsAction(SemanticsActions.OnClick)
        compose.waitForIdle()
        assertEquals(1, actions)
    }
    /** Laid out like the browser's full-screen video dialog, which has its own copy of the handle. */
    @Test fun worksInAFullScreenVideoDialog() {
        compose.setContent { RaticodeTheme {
            Dialog(onDismissRequest = {}, properties = DialogProperties(usePlatformDefaultWidth = false, decorFitsSystemWindows = false)) {
                Box(Modifier.fillMaxSize().testTag("page")) {
                    ChildLockHandle(locked = false, onAction = { actions++ }, modifier = Modifier.align(Alignment.CenterEnd))
                }
            }
        } }
        settle()
        assertTrue(isAway(false))
        compose.onNodeWithTag("childLockReveal").performTouchInput { click(center) }
        settle()
        assertTrue("slid in over the video", isOut(false))
        handle(false).performTouchInput { click() }
        handle(false).performTouchInput { click() }
        compose.waitForIdle()
        assertEquals(1, actions)
        handle(false).performTouchInput { swipeRight(startX = centerX - 20f, endX = right + 200f, durationMillis = 150) }
        settle()
        assertTrue("swiped away", isAway(false))
    }
}
