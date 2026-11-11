package dev.raticode.mobile

import android.accessibilityservice.AccessibilityService
import android.app.Application
import android.os.SystemClock
import android.util.Log
import androidx.activity.compose.setContent
import androidx.compose.ui.unit.dp
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.lifecycle.ViewModelStore
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.browser.BrowserRules
import dev.raticode.mobile.browser.BrowserRuntime
import dev.raticode.mobile.browser.GeckoBrowser
import dev.raticode.mobile.live.DeviceStore
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.ui.LiveScreen
import dev.raticode.mobile.ui.NavBarPrefs
import dev.raticode.mobile.ui.RaticodeTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import java.io.File

/**
 * The Gecko browser in the real app: paired with a `--workspace --remote --desktop --browser` fixture (pass
 * `browserInvitationFile`), with Browser on Raticode's real bottom bar. The bar stays visible under Gecko's page, going to
 * Rem and back keeps the tabs, and a long audio file keeps playing after Home. Needs the emulator's internet.
 */
class LiveGeckoBrowserTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    @get:Rule val keepControls = KeepBrowserControls()
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val app get() = instrumentation.targetContext.applicationContext as Application

    private fun shell(command: String) = instrumentation.uiAutomation.executeShellCommand(command).use {
        android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes().decodeToString()
    }
    private fun waitFor(what: String, timeout: Long = 30000, condition: () -> Boolean) {
        val deadline = SystemClock.uptimeMillis() + timeout
        while (!condition()) {
            if (SystemClock.uptimeMillis() > deadline) throw AssertionError(what)
            SystemClock.sleep(150)
        }
    }
    private fun nav(label: String) = compose.onNode(hasText(label) and hasAnyAncestor(hasTestTag("desktopNavigation")))
    private fun shown(tag: String) = compose.onAllNodesWithTag(tag).fetchSemanticsNodes().isNotEmpty()
    /** The share of dark pixels in the page area of the real screen. */
    private fun pageInk(): Double {
        val shot = instrumentation.uiAutomation.takeScreenshot() ?: return 0.0
        val area = compose.onNodeWithTag("geckoView").fetchSemanticsNode().boundsInWindow
        var dark = 0; var total = 0
        for (x in area.left.toInt() until minOf(area.right.toInt(), shot.width) step 4) for (y in area.top.toInt() until minOf(area.bottom.toInt(), shot.height) step 4) {
            val pixel = shot.getPixel(x, y); total++
            if ((android.graphics.Color.red(pixel) + android.graphics.Color.green(pixel) + android.graphics.Color.blue(pixel)) / 3 < 128) dark++
        }
        return if (total == 0) 0.0 else dark.toDouble() / total
    }
    private fun serviceForeground() = shell("dumpsys activity services ${app.packageName}/.browser.BrowserPlaybackService").contains("isForeground=true")

    @Test fun geckoKeepsRaticodesBarTabsAndMusic() {
        val path = InstrumentationRegistry.getArguments().getString("browserInvitationFile")
        assumeTrue("Requires the disposable fixture", path != null)
        DeviceStore(app).reset()
        NavBarPrefs.save(app, listOf("Rem", "Threads", "Browser"))
        lateinit var gecko: GeckoBrowser
        lateinit var client: LiveClient
        val owners = ViewModelStore()
        compose.runOnUiThread {
            BrowserRuntime.get(app).setEngine(BrowserRules.ENGINE_GECKO)
            gecko = GeckoBrowser.get(app)
            gecko.tabs.map { it.id }.forEach(gecko::close)
            client = LiveClient(app)
            owners.put("gecko", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client, startPage = "Rem") } }
            client.preview(File(path!!).readText()); client.pair()
        }
        try {
            waitFor("paired") { client.connected }
            waitFor("bottom bar") { shown("desktopNavigation") }
            nav("Browser").performClick()
            waitFor("the Gecko page under Raticode's bar") { shown("geckoPage") }
            val bar = compose.onNodeWithTag("desktopNavigation").fetchSemanticsNode().boundsInRoot
            val content = compose.onNodeWithTag("geckoContent").fetchSemanticsNode().boundsInRoot
            assertEquals("Gecko's page runs down to the real bottom bar", bar.top, content.bottom, 2f)

            compose.runOnUiThread { gecko.go("https://example.com/") }
            val online = runCatching { waitFor("example.com", 45000) { gecko.page.title.contains("Example", ignoreCase = true) && !gecko.page.loading } }.isSuccess
            assumeTrue("Needs the emulator's internet", online)
            waitFor("Gecko's view above the bar") { shown("geckoView") }
            assertTrue("The bar is still showing with a page open", shown("desktopNavigation"))
            // Tapping Browser on the real bar brings folded controls back.
            dev.raticode.mobile.browser.BrowserControlsMotion.idleMillis = 300
            compose.runOnUiThread { gecko.reload() }
            compose.waitUntil(30000) { shown("geckoControlsStrip") } // waitUntil runs the Compose clock the idle wait uses
            val settled = SystemClock.uptimeMillis() + 1000
            compose.waitUntil(5000) { SystemClock.uptimeMillis() > settled }
            nav("Browser").performClick()
            waitFor("the toolbar after tapping Browser", 5000) { shown("geckoBack") }
            dev.raticode.mobile.browser.BrowserControlsMotion.idleMillis = Long.MAX_VALUE
            assertTrue("A 56dp bar: ${compose.onNodeWithTag("desktopNavigation").fetchSemanticsNode().boundsInRoot.height}",
                compose.onNodeWithTag("desktopNavigation").fetchSemanticsNode().boundsInRoot.height <= with(compose.density) { 57.dp.toPx() })
            // What Android itself shows on screen, so a page hidden behind Raticode's own drawing would fail here.
            waitFor("the page's text on screen", 20000) { pageInk() > 0.005 }
            shell("screencap -p /data/local/tmp/raticode-gecko-page.png")

            // A second tab, then away to Rem and back: the tabs and pages are kept.
            compose.runOnUiThread { gecko.newTab("https://www.iana.org/help/example-domains") }
            waitFor("second tab", 45000) { gecko.tabs.size == 2 && gecko.page.url.contains("iana.org") && !gecko.page.loading }
            nav("Rem").performClick()
            waitFor("Rem") { !shown("geckoPage") }
            SystemClock.sleep(1000)
            nav("Browser").performClick()
            waitFor("back in the browser") { shown("geckoView") }
            assertEquals(2, gecko.tabs.size)
            assertTrue(gecko.page.url.contains("iana.org"))

            // Music: a long audio file. Gecko's player starts on load or on a tap in the middle of the page.
            compose.runOnUiThread { gecko.go("https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3") }
            waitFor("audio page") { gecko.page.url.endsWith(".mp3") }
            SystemClock.sleep(4000)
            val page = compose.onNodeWithTag("geckoContent").fetchSemanticsNode().boundsInRoot
            if (gecko.playing.isEmpty()) shell("input tap ${page.center.x.toInt()} ${page.center.y.toInt()}")
            val playing = runCatching { waitFor("Gecko reports playing", 25000) { gecko.playing.isNotEmpty() } }.isSuccess
            Log.i("GeckoMusic", "playing=$playing tabs=${gecko.tabs.size}")
            shell("screencap -p /data/local/tmp/raticode-gecko-music.png")
            assertTrue("Gecko's media session reports the file playing", playing)
            waitFor("the playback notification") { serviceForeground() }

            // On Raticode's other pages it keeps playing.
            nav("Rem").performClick()
            waitFor("Rem") { !shown("geckoPage") }
            SystemClock.sleep(3000)
            assertTrue("Still playing on Rem", gecko.playing.isNotEmpty())
            nav("Browser").performClick()
            waitFor("browser again") { shown("geckoView") }

            // Home: Raticode leaves the screen, the music and its notification stay.
            instrumentation.uiAutomation.performGlobalAction(AccessibilityService.GLOBAL_ACTION_HOME)
            SystemClock.sleep(6000)
            assertTrue("Still playing after Home", gecko.playing.isNotEmpty())
            assertTrue(serviceForeground())
            Log.i("GeckoMusic", "after home playing=${gecko.playing.size}")

            // Stop, as the notification's button does: the service ends.
            instrumentation.runOnMainSync { gecko.stopSound() }
            waitFor("the service stopped") { gecko.playing.isEmpty() && !serviceForeground() }
        } finally {
            instrumentation.runOnMainSync { GeckoBrowser.peek()?.stopSound(); BrowserRuntime.get(app).setEngine(BrowserRules.ENGINE_RATICODE) }
            NavBarPrefs.save(app, listOf("Rem", "Threads", "Organizations", "Swarms"))
            compose.runOnUiThread { client.disconnect(); owners.clear() }
        }
    }
}
