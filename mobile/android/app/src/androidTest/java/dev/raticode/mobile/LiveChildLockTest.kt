package dev.raticode.mobile

import android.app.Application
import android.os.Build
import android.os.ParcelFileDescriptor
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.test.ComposeTimeoutException
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.swipeRight
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.runner.lifecycle.ActivityLifecycleMonitorRegistry
import androidx.test.runner.lifecycle.Stage
import dev.raticode.mobile.childlock.ChildLock
import dev.raticode.mobile.childlock.ChildLockRuntime
import dev.raticode.mobile.childlock.EdgeHandle
import dev.raticode.mobile.childlock.UnlockOutcome
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import kotlin.math.abs

/**
 * Child lock on real Raticode screens with Android's real prompt and a test PIN. Run only on a disposable emulator; it
 * needs no desktop. The one edge handle turns the lock on, becomes the only lock on screen, tucks itself away, and
 * turns the lock off again only after a full hold and the right PIN.
 */
class LiveChildLockTest {
    @get:Rule val compose = createEmptyComposeRule()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val app get() = instrumentation.targetContext.applicationContext as Application
    private val lock: ChildLock get() = ChildLockRuntime.get(app)
    private var scenario: ActivityScenario<MainActivity>? = null

    private fun shell(command: String) = instrumentation.uiAutomation.executeShellCommand(command).use {
        ParcelFileDescriptor.AutoCloseInputStream(it).readBytes().decodeToString()
    }
    private fun screenshot(name: String) { shell("screencap -p /data/local/tmp/raticode-childlock-$name.png") }
    private fun focus() = shell("dumpsys window").lines().firstOrNull { it.trim().startsWith("mCurrentFocus") }?.trim().orEmpty()
    private fun waitFor(what: String, timeout: Long = 20_000, condition: () -> Boolean) {
        try { compose.waitUntil(timeout, condition) }
        catch (failure: ComposeTimeoutException) { screenshot("failure"); throw AssertionError("$what: locked=${lock.locked}; focus=${focus()}", failure) }
    }
    private fun mainStage(stage: Stage): Boolean {
        var found = false
        instrumentation.runOnMainSync { found = ActivityLifecycleMonitorRegistry.getInstance().getActivitiesInStage(stage).any { it is MainActivity } }
        return found
    }
    private fun count(tag: String) = compose.onAllNodesWithTag(tag).fetchSemanticsNodes(atLeastOneRootRequired = false).size
    private fun bounds(tag: String): Rect = compose.onNodeWithTag(tag).fetchSemanticsNode().boundsInRoot
    private val screenWidth get() = app.resources.displayMetrics.widthPixels.toFloat()
    private fun isOut(tag: String) = count(tag) == 1 && abs(bounds(tag).right - screenWidth) < 2f
    private fun isAway(tag: String) = count(tag) == 1 && bounds(tag).left >= screenWidth - 1f
    private fun promptUp() = focus().let { it.contains("BiometricPrompt") || it.contains("systemui", ignoreCase = true) }
    private fun pause(ms: Long) { val start = System.currentTimeMillis(); compose.waitUntil(ms + 5_000) { System.currentTimeMillis() - start >= ms } }
    /** Holds the closed padlock for [ms] of real time, then lets go. */
    private fun hold(ms: Long) {
        compose.onNodeWithTag("childLockUnlock").performTouchInput { down(center) }
        pause(ms)
        runCatching { compose.onNodeWithTag("childLockUnlock").performTouchInput { up() } }
    }
    private fun reveal(tag: String) {
        compose.onNodeWithTag("childLockReveal").performClick()
        waitFor("handle slid in") { isOut(tag) }
    }

    @Before fun setUp() {
        assumeTrue("Needs Android 11's BiometricPrompt", Build.VERSION.SDK_INT >= 30)
        shell("input keyevent KEYCODE_WAKEUP"); shell("wm dismiss-keyguard")
        shell("locksettings set-pin 1111")
        instrumentation.runOnMainSync { lock.finishUnlock(UnlockOutcome.CONFIRMED); EdgeHandle.hide() }
    }

    @After fun tearDown() {
        instrumentation.runOnMainSync { lock.finishUnlock(UnlockOutcome.CONFIRMED) }
        runCatching { scenario?.close() }
        shell("locksettings clear --old 1111")
        shell("input keyevent KEYCODE_WAKEUP"); shell("wm dismiss-keyguard")
    }

    @Test fun theStartingLockIsTheUnlockingLock() {
        scenario = ActivityScenario.launch(MainActivity::class.java)
        waitFor("main screen in front") { mainStage(Stage.RESUMED) }

        // Tucked away until the edge is tapped; a swipe right puts it back.
        waitFor("handle starts away") { isAway("childLockButton") }
        screenshot("off-away")
        reveal("childLockButton")
        screenshot("off-out")
        compose.onNodeWithTag("childLockButton").performTouchInput { swipeRight(startX = centerX - 10f, endX = right + 300f, durationMillis = 150) }
        waitFor("swiped away") { isAway("childLockButton") }
        assertFalse(lock.locked)

        // Two taps on the same handle lock.
        reveal("childLockButton")
        val before = bounds("childLockButton")
        compose.onNodeWithTag("childLockButton").performClick()
        assertFalse("one tap only arms it", lock.locked)
        compose.onNodeWithTag("childLockButton").performClick()
        waitFor("lock screen") { count("childLockScreen") == 1 && lock.locked }
        waitFor("the lock screen's handle is out") { isOut("childLockUnlock") }
        screenshot("on-out")

        // One lock on screen, where the starting one was, and no second button anywhere.
        assertEquals("the main window's handle is gone", 0, count("childLockButton"))
        assertEquals("exactly one lock", 1, count("childLockUnlock"))
        val after = bounds("childLockUnlock")
        assertEquals("same edge", before.right, after.right, 2f)
        assertEquals("same height on the screen", before.center.y, after.center.y, 2f)
        assertTrue("main screen only pauses", mainStage(Stage.PAUSED))

        // Untouched, it tucks itself away; the rest of the screen still does nothing.
        waitFor("tucked away by itself", 15_000) { isAway("childLockUnlock") }
        screenshot("on-away")
        compose.onNodeWithTag("childLockScreen").performClick()
        shell("input keyevent KEYCODE_BACK")
        pause(500)
        assertTrue(lock.locked)
        assertEquals(1, count("childLockScreen"))

        // The compose test rule runs every window's clock, so a short press is checked in ChildLockHandleTest with a
        // stopped clock, and with Android's animations off by hand with adb.

        // A full hold asks; cancelling keeps the lock.
        reveal("childLockUnlock")
        hold(2_200)
        waitFor("Android's PIN pad") { promptUp() }
        screenshot("prompt")
        pause(1_500)
        shell("input keyevent KEYCODE_BACK")
        waitFor("back under the lock") { !promptUp() && count("childLockNotice") == 1 }
        assertTrue("cancel keeps it locked", lock.locked)
        screenshot("cancelled")

        // A wrong PIN keeps it locked; the right one unlocks.
        reveal("childLockUnlock")
        hold(2_200)
        waitFor("Android's PIN pad") { promptUp() }
        pause(1_500)
        shell("input text 0000"); shell("input keyevent KEYCODE_ENTER")
        pause(2_000)
        assertTrue("wrong PIN keeps it locked", lock.locked)
        repeat(2) {
            if (!lock.locked) return@repeat
            if (!promptUp()) { reveal("childLockUnlock"); hold(2_200); waitFor("Android's PIN pad") { promptUp() }; pause(1_500) }
            shell("input text 1111"); shell("input keyevent KEYCODE_ENTER")
            runCatching { compose.waitUntil(8_000) { !lock.locked } }
        }
        waitFor("unlocked") { !lock.locked && count("childLockScreen") == 0 }
        waitFor("main screen back") { mainStage(Stage.RESUMED) }
        assertEquals("the same handle is back with an open padlock", 1, count("childLockButton"))
        screenshot("unlocked")
    }
}
