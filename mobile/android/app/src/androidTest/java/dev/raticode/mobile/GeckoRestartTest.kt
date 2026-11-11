package dev.raticode.mobile

import android.app.Application
import android.os.SystemClock
import android.view.View
import android.view.ViewGroup
import androidx.activity.compose.setContent
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.browser.BrowserRules
import dev.raticode.mobile.browser.BrowserRuntime
import dev.raticode.mobile.browser.GeckoBrowser
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.ui.BrowserPage
import dev.raticode.mobile.ui.RaticodeTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.mozilla.geckoview.GeckoView

/**
 * The Gecko browser's tabs after Android ends the app. Two runs, each its own process, with `am instrument` so the app's
 * data survives between them (Gradle's connected tasks uninstall the app): `-e geckoRestart save`, then
 * `-e geckoRestart check`. Skipped without the argument. Needs the emulator's internet.
 */
class GeckoRestartTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    @get:Rule val keepControls = KeepBrowserControls()
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val app get() = instrumentation.targetContext.applicationContext as Application
    private val phase get() = InstrumentationRegistry.getArguments().getString("geckoRestart")

    private fun waitFor(what: String, timeout: Long = 45000, condition: () -> Boolean) {
        val deadline = SystemClock.uptimeMillis() + timeout
        while (!condition()) {
            if (SystemClock.uptimeMillis() > deadline) throw AssertionError(what)
            SystemClock.sleep(150)
        }
    }
    private fun shown(tag: String) = compose.onAllNodesWithTag(tag).fetchSemanticsNodes().isNotEmpty()
    private fun show() = compose.runOnUiThread { val client = LiveClient(app); compose.activity.setContent { RaticodeTheme { BrowserPage(client, null) {} } } }
    private fun geckoView(view: View): GeckoView? = when (view) {
        is GeckoView -> view
        is ViewGroup -> (0 until view.childCount).firstNotNullOfOrNull { geckoView(view.getChildAt(it)) }
        else -> null
    }
    /** The share of dark pixels in the page area of what Android really shows. */
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

    @Test fun savedTabsComeBackAfterTheProcessEnds() {
        assumeTrue("Pass geckoRestart=save, then geckoRestart=check", phase != null)
        if (phase == "save") {
            lateinit var gecko: GeckoBrowser
            compose.runOnUiThread {
                BrowserRuntime.get(app).setEngine(BrowserRules.ENGINE_GECKO)
                gecko = GeckoBrowser.get(app)
                gecko.tabs.map { it.id }.forEach(gecko::close)
            }
            show()
            waitFor("the Gecko page") { shown("geckoPage") }
            compose.runOnUiThread { gecko.go("https://www.iana.org/help/example-domains") }
            waitFor("first page") { gecko.page.url.contains("iana.org") && !gecko.page.loading }
            compose.runOnUiThread { gecko.newTab("https://example.com/") }
            waitFor("second page") { gecko.tabs.size == 2 && gecko.page.url == "https://example.com/" && !gecko.page.loading }
            SystemClock.sleep(1500) // The encrypted save runs in the background.
            return
        }
        // A new process: nothing has opened a Gecko session yet.
        assertEquals(BrowserRules.ENGINE_GECKO, BrowserRuntime.get(app).saved.engine)
        val gecko = GeckoBrowser.get(app)
        assertEquals(2, gecko.tabs.size)
        assertEquals("https://example.com/", gecko.page.url)
        show()
        waitFor("the restored tab's view") { shown("geckoView") }
        waitFor("a session attached to the view") { var attached = false; compose.runOnUiThread { attached = geckoView(compose.activity.window.decorView)?.session?.isOpen == true }; attached }
        waitFor("the restored page loaded") { gecko.page.title.contains("Example", ignoreCase = true) && !gecko.page.loading }
        waitFor("the restored page on screen", 20000) { pageInk() > 0.005 }
        assertNotNull(gecko.session)
        assertEquals(2, gecko.tabs.size)
        // The other restored tab opens when picked.
        val other = gecko.tabs.first { it.id != gecko.active }
        assertTrue(other.url.contains("iana.org"))
        compose.runOnUiThread { gecko.select(other.id) }
        waitFor("the other tab's page") { gecko.page.url.contains("iana.org") && !gecko.page.loading }
        compose.runOnUiThread { BrowserRuntime.get(app).setEngine(BrowserRules.ENGINE_RATICODE); gecko.tabs.map { it.id }.forEach(gecko::close) }
    }
}
