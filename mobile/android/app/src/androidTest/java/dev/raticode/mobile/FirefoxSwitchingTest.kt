package dev.raticode.mobile

import android.accessibilityservice.AccessibilityService
import android.app.Application
import android.view.accessibility.AccessibilityNodeInfo
import androidx.activity.compose.setContent
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.browser.BrowserRules
import dev.raticode.mobile.browser.BrowserRuntime
import dev.raticode.mobile.browser.FirefoxApps
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.ui.BrowserPage
import dev.raticode.mobile.ui.RaticodeTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test

/**
 * Going between Raticode and Firefox without losing Firefox's page or its music, with Mozilla's Firefox for Android on the
 * emulator. Pass `musicPage`: a page with a "Play music" button that loops a tone (the host serves it, for example
 * `http://10.0.2.2:8771/index.html`). Checks, with Android's own media sessions as the record of what is playing:
 * - Over Raticode (a Custom Tab): Back, like its ✕, closes the page and its sound stops. This was the user's problem.
 * - In the Firefox app (now the default): Raticode coming back in front leaves Firefox playing, and Return to
 *   Firefox brings back the same page, still playing.
 * Screenshots go to `/data/local/tmp/raticode-switch-*.png`.
 *
 * Not yet reliable: an earlier version of this test passed once (November 7, 2026, 21 s). Later runs stalled at the
 * first switch between Firefox and Raticode, with Firefox in front and the test's thread asleep in [waitFor], and the
 * cause wasn't found (turning off Android's app freezer didn't help). This version hasn't run to the end.
 */
class FirefoxSwitchingTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val app get() = instrumentation.targetContext.applicationContext as Application
    private val firefox = "org.mozilla.firefox"

    private fun shell(command: String) = instrumentation.uiAutomation.executeShellCommand(command).use {
        android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes().decodeToString()
    }
    private fun screenshot(name: String) { shell("screencap -p /data/local/tmp/raticode-switch-$name.png") }
    private fun pause(ms: Long) = Thread.sleep(ms)
    private fun front(): String? = instrumentation.uiAutomation.rootInActiveWindow?.packageName?.toString()
    /**
     * Waits on another app with a wall clock. Compose's waitUntil never timed out here while Raticode was stopped behind
     * Firefox, which left runs hanging for about ten minutes.
     */
    private fun waitFor(what: String, timeout: Long = 20000, condition: () -> Boolean) {
        val deadline = android.os.SystemClock.uptimeMillis() + timeout
        while (!condition()) {
            if (android.os.SystemClock.uptimeMillis() > deadline) throw AssertionError("$what; front=${front()}; media=${firefoxMedia()}")
            Thread.sleep(250)
        }
    }

    /** Firefox's media sessions as Android records them: "playing" while any is playing, else what is left. */
    private fun firefoxMedia(): String {
        val dump = shell("dumpsys media_session")
        val sessions = dump.split(Regex("\n(?=\\s{4}\\S.*package=)")).filter { "package=$firefox" in it }
        return when {
            sessions.any { Regex("state=PlaybackState \\{state=(PLAYING|3)\\b").containsMatchIn(it) } -> "playing"
            sessions.isEmpty() -> "none"
            else -> "not playing"
        }
    }

    /**
     * Nodes in the active window whose text or description is one of [wanted]. Firefox's Compose screens don't answer
     * find-by-text, so this walks the window, at most 300 nodes and 4 seconds: some reads from Firefox's busy home
     * page wait seconds each, and an unbounded walk there stalled a run for ten minutes.
     */
    private fun labelled(wanted: Collection<String>, root: AccessibilityNodeInfo? = instrumentation.uiAutomation.rootInActiveWindow): Map<String, AccessibilityNodeInfo> {
        val found = mutableMapOf<String, AccessibilityNodeInfo>()
        val deadline = System.currentTimeMillis() + 4000
        var visited = 0
        val queue = ArrayDeque(listOfNotNull(root))
        while (queue.isNotEmpty() && visited < 300 && System.currentTimeMillis() < deadline) {
            val node = queue.removeFirst(); visited++
            (node.text ?: node.contentDescription)?.toString()?.takeIf { it in wanted }?.let { found.putIfAbsent(it, node) }
            for (i in 0 until node.childCount) node.getChild(i)?.let(queue::addLast)
        }
        return found
    }
    private fun findText(text: String): AccessibilityNodeInfo? = labelled(listOf(text))[text]
    private fun tap(node: AccessibilityNodeInfo) {
        val bounds = android.graphics.Rect().also(node::getBoundsInScreen)
        shell("input tap ${bounds.centerX()} ${bounds.centerY()}")
    }

    /** A fresh Firefox's first-run screens: Continue, Cancel on Android's default-browser question, then Not now. */
    private fun finishFirefoxWelcome() {
        var idle = 0
        while (idle < 3) {
            val root = instrumentation.uiAutomation.rootInActiveWindow
            val pkg = root?.packageName?.toString().orEmpty()
            val choices = when {
                pkg == firefox -> listOf("Not now", "Skip", "Maybe later", "Continue", "Start browsing", "Got it")
                pkg.endsWith("permissioncontroller") -> listOf("Cancel")
                else -> emptyList()
            }
            val found = if (choices.isEmpty()) emptyMap() else labelled(choices, root)
            val button = choices.firstNotNullOfOrNull { found[it] }
            if (button == null) { idle++; pause(500); continue }
            idle = 0; tap(button); pause(2000)
        }
    }

    /**
     * Raticode back in front while Firefox keeps running. A user does this with Recents or the launcher icon, which both
     * resume Raticode's task. The test's own activity is started by the test runner, not the launcher, so Android lists
     * it in neither (seen on the emulator: the task existed but was missing from Recents). The test therefore brings
     * that activity forward itself, which leaves Firefox exactly as a Recents switch would.
     */
    private fun raticodeBack() {
        instrumentation.startActivitySync(android.content.Intent(app, MainActivity::class.java)
            .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK or android.content.Intent.FLAG_ACTIVITY_REORDER_TO_FRONT))
        waitFor("Raticode in front", 10000) { front() == app.packageName }
    }

    /** Opens the music page in Firefox from the Firefox page's address field and starts it playing. */
    private fun playInFirefox(page: String) {
        compose.onNodeWithTag("firefoxAddress").performTextReplacement(page)
        compose.onNodeWithTag("firefoxAddress").performImeAction()
        waitFor("Firefox in front") { front() == firefox }
        waitFor("the music page in Firefox") { findText("Play music") != null }
        pause(1000)
        tap(findText("Play music")!!)
        waitFor("Firefox playing") { firefoxMedia() == "playing" }
    }

    @Test fun switchingKeepsFirefoxAndItsMusic() {
        val page = InstrumentationRegistry.getArguments().getString("musicPage")
        assumeTrue("Pass musicPage, a page with a Play music button", page != null)
        assumeTrue("Requires Mozilla's Firefox for Android on the emulator", FirefoxApps.find(app)?.usable == true)
        lateinit var browser: BrowserRuntime
        compose.runOnUiThread {
            browser = BrowserRuntime.get(app)
            browser.setEngine(BrowserRules.ENGINE_FIREFOX)
            browser.setFirefoxMode(FirefoxApps.MODE_APP)
            val client = LiveClient(app)
            compose.activity.setContent { RaticodeTheme { BrowserPage(client, null) {} } }
        }
        try {
            compose.waitUntil(8000) { compose.onAllNodesWithTag("firefoxPage").fetchSemanticsNodes().isNotEmpty() }
            // The Firefox app is the default, offered first, and the page says how to switch.
            compose.onNodeWithTag("firefoxSwitchHelp").assertExists()
            compose.onNodeWithTag("firefoxOpens-app").assertIsSelected()
            // Not in split screen, so there is no button that would only open Firefox full screen.
            assertTrue(compose.onAllNodesWithTag("firefoxBeside").fetchSemanticsNodes().isEmpty())
            pause(800)
            screenshot("page")

            // A fresh Firefox's welcome, once, as a user's own Firefox has long finished.
            compose.onNodeWithTag("firefoxReturn").performScrollTo().performClick()
            waitFor("Firefox in front") { front() == firefox }
            pause(1500); finishFirefoxWelcome()
            raticodeBack()

            // Over Raticode (the old default): closing the Custom Tab with Back, as with its ✕, stops the music.
            compose.onNodeWithTag("firefoxOpens-tab").performScrollTo().performClick()
            assertEquals(FirefoxApps.MODE_TAB, browser.saved.firefoxMode)
            playInFirefox(page!!)
            screenshot("custom-tab-playing")
            instrumentation.uiAutomation.performGlobalAction(AccessibilityService.GLOBAL_ACTION_BACK)
            waitFor("Raticode in front after closing the Custom Tab", 10000) { front() == app.packageName }
            waitFor("the Custom Tab's music stopped", 10000) { firefoxMedia() != "playing" }

            // In the Firefox app: Raticode back in front leaves Firefox's page playing.
            compose.onNodeWithTag("firefoxOpens-app").performScrollTo().performClick()
            playInFirefox(page)
            screenshot("app-playing")
            raticodeBack()
            pause(3000)
            assertEquals("Firefox kept playing while Raticode was in front", "playing", firefoxMedia())
            screenshot("raticode-while-playing")

            // Return to Firefox brings back the same page, still playing, without opening another.
            compose.onNodeWithTag("firefoxReturn").performScrollTo().performClick()
            waitFor("Firefox in front") { front() == firefox }
            waitFor("the same page") { findText("Play music") != null }
            assertEquals("playing", firefoxMedia())
            screenshot("returned")
            // And Raticode is still on its Browser page when it comes back.
            raticodeBack()
            assertFalse("Raticode stayed on its Browser page", compose.onAllNodesWithTag("firefoxPage").fetchSemanticsNodes().isEmpty())
        } catch (failure: Throwable) {
            runCatching { screenshot("failure") }
            throw failure
        } finally {
            compose.runOnUiThread { BrowserRuntime.get(app).setEngine(BrowserRules.ENGINE_RATICODE) }
            shell("am force-stop $firefox")
        }
    }
}
