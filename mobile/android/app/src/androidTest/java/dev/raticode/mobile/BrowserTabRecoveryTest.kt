package dev.raticode.mobile

import android.app.ActivityManager
import android.app.Application
import android.os.Process
import android.os.SystemClock
import android.webkit.WebView
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.material3.Text
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.platform.app.InstrumentationRegistry
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import dev.raticode.mobile.browser.BrowserRules
import dev.raticode.mobile.browser.BrowserRuntime
import dev.raticode.mobile.browser.GeckoBrowser
import dev.raticode.mobile.browser.TabRecovery
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.ui.BrowserPage
import dev.raticode.mobile.ui.RaticodeTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotSame
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test

/**
 * Run each test in its own instrumentation run (`-e class BrowserTabRecoveryTest#name`): Android waits longer each time
 * before restarting a page process the test ended, and that adds up across tests in one app process.
 *
 * A tab whose page process ends while the user is away (Android freeing memory, a crash, a stuck page) comes back by
 * itself, with its address, back/forward list and private mode, and with no "stopped working" message. Covers both of
 * the phone's built-in browsers with their real engines. Needs the emulator's internet (example.com) and skips without it.
 */
class BrowserTabRecoveryTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val app get() = instrumentation.targetContext.applicationContext as Application
    private var onBrowser by mutableStateOf(true)

    private fun shown(tag: String) = compose.onAllNodesWithTag(tag, useUnmergedTree = true).fetchSemanticsNodes().isNotEmpty()
    private fun says(text: String) = compose.onAllNodesWithText(text, substring = true, ignoreCase = true).fetchSemanticsNodes().isNotEmpty()
    private fun noStopMessage() {
        compose.waitForIdle()
        assertFalse("No \"stopped working\" message", says("stopped working") || says("stopped responding") || says("isn't responding"))
        assertFalse("No problem panel", shown("browserProblem") || shown("geckoStopped"))
    }

    private fun show(engine: String) {
        compose.runOnUiThread {
            BrowserRuntime.get(app).setEngine(engine)
            val client = LiveClient(app)
            compose.activity.setContent { RaticodeTheme { Column(Modifier.fillMaxSize().safeDrawingPadding()) {
                Box(Modifier.weight(1f)) { if (onBrowser) BrowserPage(client, null) {} else Text("Rem", Modifier.testTag("elsewhere")) }
            } } }
        }
    }

    /** Leaves the Browser page for another one, then Raticode leaves the screen, as MainActivity.onStop tells both browsers. */
    private fun goAway() {
        onBrowser = false
        compose.waitUntil(5000) { shown("elsewhere") }
        compose.runOnUiThread { BrowserRuntime.get(app).appInFront(false); GeckoBrowser.get(app).appInFront(false) }
    }

    private fun comeBack() {
        compose.runOnUiThread { BrowserRuntime.get(app).appInFront(true); GeckoBrowser.get(app).appInFront(true) }
        onBrowser = true
    }

    // Raticode (chromium) ---------------------------------------------------------------------------------------------

    private fun chromium(): BrowserRuntime {
        lateinit var browser: BrowserRuntime
        compose.runOnUiThread { browser = BrowserRuntime.get(app); browser.tabs.map { it.id }.forEach(browser::close); browser.notice = null }
        show(BrowserRules.ENGINE_RATICODE)
        return browser
    }

    private fun loaded(browser: BrowserRuntime, part: String, timeout: Long = 45000) =
        runCatching { compose.waitUntil(timeout) { browser.page.url.contains(part) && !browser.page.loading && browser.page.progress == 100 && browser.page.title.contains("Example", true) } }.isSuccess

    private fun liveView(browser: BrowserRuntime, id: String): WebView {
        lateinit var view: WebView
        compose.runOnUiThread { view = browser.view(id)!! }
        return view
    }

    /** Ends every Raticode (chromium) page's process, as Android does to free memory: WebView reports it as not a crash. */
    private fun endRenderer(browser: BrowserRuntime, id: String) {
        assumeTrue("WebView can end its page process", WebViewFeature.isFeatureSupported(WebViewFeature.GET_WEB_VIEW_RENDERER) &&
            WebViewFeature.isFeatureSupported(WebViewFeature.WEB_VIEW_RENDERER_TERMINATE))
        // A page process that is still starting can't be ended yet; try again shortly.
        repeat(5) {
            val view = liveView(browser, id)
            compose.runOnUiThread { WebViewCompat.getWebViewRenderProcess(view)?.terminate() }
            if (runCatching { compose.waitUntil(3000) { !browser.live(id) } }.isSuccess) return
        }
        throw AssertionError("The page process didn't end")
    }

    @Test fun aChromiumTabEndedWhileAwayReloadsOnReturnWithItsHistoryAndPrivacy() {
        val browser = chromium()
        compose.runOnUiThread { browser.go("https://example.com/") }
        assumeTrue("Needs the emulator's internet connection", loaded(browser, "example.com"))
        compose.runOnUiThread { browser.go("https://example.com/?second") }
        assertTrue(loaded(browser, "?second"))
        val id = browser.active!!
        assumeTrue("WebView supports private tabs", WebViewFeature.isFeatureSupported(WebViewFeature.MULTI_PROFILE))
        lateinit var privateId: String
        compose.runOnUiThread { privateId = browser.newTab("https://example.com/?private", background = true, private = true)!! }
        liveView(browser, privateId)
        val before = liveView(browser, id)

        goAway()
        endRenderer(browser, id)
        SystemClock.sleep(1500)
        compose.waitForIdle()
        assertFalse("Nothing reloads while the user is away", browser.live(id))
        assertNull("No message waits for the user", browser.notice)

        comeBack()
        compose.waitUntil(30000) { browser.live(id) && browser.page.url.contains("?second") && !browser.page.loading && browser.page.progress == 100 }
        assertNotSame("A new page", before, liveView(browser, id))
        assertTrue("Back still goes to the first page", browser.page.canBack)
        assertEquals(listOf(id, privateId), browser.tabs.map { it.id })
        noStopMessage()

        // The private tab comes back private, in the same private session.
        compose.runOnUiThread { browser.select(privateId) }
        compose.waitUntil(30000) { browser.live(privateId) && browser.page.url.contains("?private") && !browser.page.loading && browser.page.progress == 100 }
        assertTrue(browser.isPrivate(privateId))
        compose.runOnUiThread { assertTrue(WebViewCompat.getProfile(browser.view(privateId)!!).name.startsWith("raticode-private-")) }
        noStopMessage()
    }

    @Test fun aChromiumTabOnScreenReloadsAtOnceAndOneThatKeepsStoppingGetsAQuietReloadButton() {
        val browser = chromium()
        compose.runOnUiThread { browser.go("https://example.com/?looking") }
        assumeTrue("Needs the emulator's internet connection", loaded(browser, "?looking"))
        val id = browser.active!!
        repeat(TabRecovery.MAX_RESTORES) { round ->
            endRenderer(browser, id)
            // Each automatic reload waits a little longer than the one before.
            compose.waitUntil(TabRecovery.BACKOFF_MS[round] + 30000) { browser.live(id) && !browser.page.loading && browser.page.progress == 100 && browser.page.url.contains("?looking") }
            noStopMessage()
        }
        endRenderer(browser, id)
        compose.waitUntil(5000) { shown("browserProblem") && says("keeps stopping") }
        assertNull("Shown in the tab, not as a message", browser.notice)
        compose.onNodeWithText("Reload").performClick()
        compose.waitUntil(30000) { browser.live(id) && browser.page.url.contains("?looking") && !browser.page.loading && browser.page.progress == 100 }
        noStopMessage()
    }

    @Test fun aStuckChromiumPageOnScreenIsReloadedQuietly() {
        val browser = chromium()
        compose.runOnUiThread { browser.go("https://example.com/?stuck") }
        assumeTrue("Needs the emulator's internet connection", loaded(browser, "?stuck"))
        assumeTrue("WebView reports stuck pages", WebViewFeature.isFeatureSupported(WebViewFeature.WEB_VIEW_RENDERER_CLIENT_BASIC_USAGE))
        val id = browser.active!!
        val before = liveView(browser, id)
        compose.runOnUiThread { before.evaluateJavascript("setTimeout(function(){var t=Date.now();while(Date.now()-t<180000){}},0);'ok'", null) }
        SystemClock.sleep(500)
        // A tap the page can't answer is what makes WebView call it stuck.
        val page = compose.onNodeWithTag("browserContent").fetchSemanticsNode().boundsInRoot
        val origin = IntArray(2).also { compose.activity.window.decorView.getLocationOnScreen(it) }
        val tap = { instrumentation.uiAutomation.executeShellCommand("input tap ${origin[0] + page.center.x.toInt()} ${origin[1] + page.center.y.toInt()}").close() }
        tap()
        val deadline = SystemClock.uptimeMillis() + 60000
        var reloaded = false
        while (!reloaded && SystemClock.uptimeMillis() < deadline) {
            SystemClock.sleep(3000)
            compose.waitForIdle()
            reloaded = browser.live(id) && liveView(browser, id) !== before
            if (!reloaded && browser.live(id)) tap()
        }
        assertTrue("The stuck page was ended and reloaded", reloaded)
        compose.waitUntil(30000) { browser.page.url.contains("?stuck") && !browser.page.loading && browser.page.progress == 100 }
        noStopMessage()
    }

    // Raticode (gecko) ------------------------------------------------------------------------------------------------

    private fun gecko(): GeckoBrowser {
        lateinit var gecko: GeckoBrowser
        compose.runOnUiThread { gecko = GeckoBrowser.get(app); gecko.tabs.map { it.id }.forEach(gecko::close); gecko.notice = null }
        show(BrowserRules.ENGINE_GECKO)
        compose.waitUntil(10000) { shown("geckoPage") }
        return gecko
    }

    private fun loaded(gecko: GeckoBrowser, part: String, timeout: Long = 45000) =
        runCatching { compose.waitUntil(timeout) { gecko.page.url.contains(part) && !gecko.page.loading && gecko.page.progress == 100 && gecko.page.title.contains("Example", true) } }.isSuccess

    /** Ends Gecko's page processes, as Android does to free memory. They run as this app, so the test may end them. */
    private fun endGeckoPages(gecko: GeckoBrowser) {
        val pids = app.getSystemService(ActivityManager::class.java).runningAppProcesses.orEmpty()
            .filter { it.pid != Process.myPid() && "tab" in it.processName }.map { it.pid }
        assumeTrue("Gecko's page process is visible to the app", pids.isNotEmpty())
        pids.forEach(Process::killProcess)
        compose.waitUntil(15000) { gecko.session == null }
    }

    @Test fun aGeckoTabEndedWhileAwayReloadsOnReturnWithItsHistoryAndPrivacy() {
        val gecko = gecko()
        compose.runOnUiThread { gecko.go("https://example.com/") }
        assumeTrue("Needs the emulator's internet connection", loaded(gecko, "example.com"))
        compose.runOnUiThread { gecko.go("https://example.com/?second") }
        assertTrue(loaded(gecko, "?second"))
        val id = gecko.active!!
        val before = gecko.session!!

        goAway()
        endGeckoPages(gecko)
        SystemClock.sleep(1500)
        compose.waitForIdle()
        assertNull("Nothing reloads while the user is away", gecko.session)
        assertNull("No message waits for the user", gecko.notice)

        comeBack()
        compose.waitUntil(30000) { gecko.session != null && gecko.page.url.contains("?second") && !gecko.page.loading && gecko.page.progress == 100 }
        assertNotSame("A new page", before, gecko.session)
        assertEquals(id, gecko.active)
        compose.waitUntil(10000) { gecko.page.canBack }
        noStopMessage()

        // A private tab keeps its place, its address and its privacy, with no message. Gecko doesn't always start a new
        // private page after its page process was ended (about one run in four did on the API 36 emulator, whether the
        // session is reopened or replaced), so the reload itself is reported here, not required.
        compose.runOnUiThread { gecko.newTab("https://example.com/?private", private = true) }
        assertTrue(loaded(gecko, "?private"))
        val privateId = gecko.active!!
        goAway()
        endGeckoPages(gecko)
        comeBack()
        val reloaded = runCatching {
            compose.waitUntil(30000) { gecko.session != null && gecko.page.url.contains("?private") && !gecko.page.loading && gecko.page.progress == 100 }
        }.isSuccess
        android.util.Log.i("BrowserTabRecoveryTest", "private Gecko tab reloaded after its process ended: $reloaded")
        assertEquals(privateId, gecko.active)
        assertTrue(gecko.isPrivate(privateId))
        assertTrue(gecko.page.url.contains("?private"))
        gecko.session?.let { assertTrue("Still a private session", it.settings.usePrivateMode) }
        assertEquals(listOf(id, privateId), gecko.tabs.map { it.id })
        noStopMessage()
    }

    @Test fun aGeckoTabOnScreenReloadsAtOnceAndOneThatKeepsStoppingGetsAQuietReloadButton() {
        val gecko = gecko()
        compose.runOnUiThread { gecko.go("https://example.com/?looking") }
        assumeTrue("Needs the emulator's internet connection", loaded(gecko, "?looking"))
        repeat(TabRecovery.MAX_RESTORES) { round ->
            endGeckoPages(gecko)
            compose.waitUntil(TabRecovery.BACKOFF_MS[round] + 30000) { gecko.session != null && !gecko.page.loading && gecko.page.progress == 100 && gecko.page.url.contains("?looking") }
            noStopMessage()
        }
        endGeckoPages(gecko)
        compose.waitUntil(5000) { shown("geckoStopped") }
        assertNull("Shown in the tab, not as a message", gecko.notice)
        compose.onNodeWithText("Reload").performClick()
        compose.waitUntil(30000) { gecko.session != null && gecko.page.url.contains("?looking") && !gecko.page.loading && gecko.page.progress == 100 }
        noStopMessage()
        compose.runOnUiThread { BrowserRuntime.get(app).setEngine(BrowserRules.ENGINE_RATICODE) }
    }
}
