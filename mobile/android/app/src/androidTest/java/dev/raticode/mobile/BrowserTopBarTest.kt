package dev.raticode.mobile

import android.app.Application
import android.os.SystemClock
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.material3.Text
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.unit.dp
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.browser.BrowserRules
import dev.raticode.mobile.browser.BrowserRuntime
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.ui.BrowserPage
import dev.raticode.mobile.ui.RaticodeTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Raticode's own browser above Raticode's bottom bar (a stand-in here, as the real one needs a paired computer):
 * - Back, Tabs, Reload and the menu share one compact row with the address, and nothing sits between the page and the bar.
 * - The row folds to a strip while reading and comes back when the user scrolls up or taps the strip.
 * - Tabs open and switch from the tab button, and Back, Forward and Reload work from the top.
 * - A page playing sound keeps playing, and its timers keep running, while another Raticode page is shown, and the
 *   browser comes back with its tabs.
 * The navigation and sound parts need the emulator's internet (example.com) and skip without it.
 */
class BrowserTopBarTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    @get:Rule val keepControls = KeepBrowserControls()
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val app get() = instrumentation.targetContext.applicationContext as Application
    private var onBrowser by mutableStateOf(true)

    private fun bounds(tag: String): Rect = compose.onNodeWithTag(tag).fetchSemanticsNode().boundsInRoot
    private fun shown(tag: String) = compose.onAllNodesWithTag(tag).fetchSemanticsNodes().isNotEmpty()

    /** Runs [script] in the active tab's page, which may be off screen, and returns its result. */
    private fun js(browser: BrowserRuntime, script: String): String {
        val latch = CountDownLatch(1); var out = ""
        compose.runOnUiThread { browser.view(browser.active!!)!!.evaluateJavascript(script) { out = it; latch.countDown() } }
        assertTrue("The page answered", latch.await(10, TimeUnit.SECONDS))
        return out.trim('"')
    }

    @Test fun pageControlsAreAtTheTopAndPagesKeepPlayingElsewhereInRaticode() {
        lateinit var browser: BrowserRuntime
        compose.runOnUiThread {
            browser = BrowserRuntime.get(app)
            browser.setEngine(BrowserRules.ENGINE_RATICODE)
            browser.tabs.map { it.id }.forEach(browser::close)
            browser.notice = null
            val client = LiveClient(app)
            compose.activity.setContent { RaticodeTheme { Column(Modifier.fillMaxSize()) {
                Box(Modifier.weight(1f)) { if (onBrowser) BrowserPage(client, null) {} else Text("Rem", Modifier.testTag("elsewhere")) }
                Box(Modifier.fillMaxWidth().height(80.dp).testTag("raticodeBar"))
            } } }
        }
        compose.waitUntil(8000) { shown("browserBack") }

        // Layout: the controls and the address are one row above the page, each control a full touch target.
        val address = bounds("browserAddress"); val content = bounds("browserContent"); val bar = bounds("raticodeBar")
        val touch = with(compose.density) { 48.dp.toPx() }
        val controls = listOf("browserBack", "browserTabs", "browserReload", "browserMenu").map(::bounds)
        for (control in controls) {
            assertTrue("Control above the page: $control, page $content", control.bottom <= content.top + 1)
            assertTrue("Control at least 48dp: $control", control.width >= touch - 1 && control.height >= touch - 1)
        }
        assertEquals("All controls on one row", 1, (controls + address).map { it.center.y.toInt() }.distinct().size)
        assertTrue("The whole toolbar is one row: ${content.top - controls[0].top}", content.top - controls[0].top <= with(compose.density) { 50.dp.toPx() })
        assertFalse("Forward only shows when there is somewhere to go", shown("browserForward"))
        assertEquals("Nothing between the page and Raticode's bar", bar.top, content.bottom, 1f)

        // Tabs: a second tab opens from the tab button, and the first one is still there to switch back to.
        compose.onNodeWithTag("browserTabs").performClick()
        compose.waitUntil(5000) { shown("browserNewTab") }
        compose.onNodeWithTag("browserNewTab").performClick()
        compose.waitUntil(5000) { browser.tabs.size == 2 && !shown("browserTabsSheet") }
        val first = browser.tabs.first().id
        compose.runOnUiThread { browser.select(first) }
        compose.waitUntil(5000) { browser.active == first }

        compose.runOnUiThread { browser.go("https://example.com/") }
        val online = runCatching { compose.waitUntil(20000) { browser.page.title.contains("Example", ignoreCase = true) && !browser.page.loading } }.isSuccess
        assumeTrue("Needs the emulator's internet connection", online)
        compose.runOnUiThread { browser.go("https://example.com/?second") }
        compose.waitUntil(20000) { browser.page.url.endsWith("?second") && !browser.page.loading && browser.page.canBack }
        compose.onNodeWithTag("browserBack").performClick()
        compose.waitUntil(20000) { browser.page.url == "https://example.com/" && browser.page.canForward }
        // On a narrow phone Forward moves to the menu so the address stays readable.
        if (shown("browserForward")) compose.onNodeWithTag("browserForward").performClick()
        else {
            compose.onNodeWithTag("browserMenu").performClick()
            compose.waitUntil(5000) { shown("browserMenuSheet") }
            compose.onNodeWithText("Forward").performClick()
        }
        compose.waitUntil(20000) { browser.page.url.endsWith("?second") && !browser.page.loading }
        js(browser, "window.marker = 'before reload'")
        compose.onNodeWithTag("browserReload").performClick()
        compose.waitUntil(20000) { !browser.page.loading && js(browser, "String(window.marker)") == "undefined" }

        // Sound: the page plays a looping tone after a real tap, as sites need.
        assertEquals("ready", js(browser, PLAYER))
        SystemClock.sleep(500)
        val page = bounds("browserContent")
        val origin = IntArray(2).also { compose.activity.window.decorView.getLocationOnScreen(it) }
        instrumentation.uiAutomation.executeShellCommand("input tap ${origin[0] + page.center.x.toInt()} ${origin[1] + page.center.y.toInt()}").close()
        compose.waitUntil(10000) { js(browser, "String(!a.paused && a.currentTime > 0.3)") == "true" }

        // Another Raticode page in front: the browser page is gone from the screen, Raticode isn't.
        onBrowser = false
        compose.waitUntil(5000) { shown("elsewhere") && !shown("browserBack") }
        val before = js(browser, "String(a.currentTime)").toDouble()
        val ticksBefore = js(browser, "String(ticks)").toInt()
        SystemClock.sleep(3000)
        assertEquals("Still playing on another Raticode page", "false", js(browser, "String(a.paused)"))
        val after = js(browser, "String(a.currentTime)").toDouble()
        assertTrue("The tone went on while away: $before then $after", after - before > 2.0)
        // The page's own timers ran too (about 30 in 3 seconds). Streaming players fetch the next audio and change tracks
        // with them, so a page whose timers stop goes quiet once its buffer runs out, even though a plain tone plays on.
        val ticks = js(browser, "String(ticks)").toInt() - ticksBefore
        assertTrue("The page's timers ran while away: $ticks ticks", ticks >= 15)

        // Back on the browser: both tabs, the same page.
        onBrowser = true
        compose.waitUntil(5000) { shown("browserBack") }
        assertEquals(2, browser.tabs.size)
        assertTrue(browser.page.url.endsWith("?second"))
        assertFalse(js(browser, "String(a.paused)").toBoolean())
        js(browser, "a.pause(); 'stopped'")
    }

    @Test fun controlsFoldWhileReadingAndComeBack() {
        lateinit var browser: BrowserRuntime
        compose.runOnUiThread {
            browser = BrowserRuntime.get(app)
            browser.setEngine(BrowserRules.ENGINE_RATICODE)
            browser.tabs.map { it.id }.forEach(browser::close)
            browser.notice = null
            val client = LiveClient(app)
            compose.activity.setContent { RaticodeTheme { BrowserPage(client, null) {} } }
            browser.go("https://example.com/")
        }
        val online = runCatching { compose.waitUntil(20000) { browser.page.title.contains("Example", ignoreCase = true) && !browser.page.loading } }.isSuccess
        assumeTrue("Needs the emulator's internet connection", online)
        compose.waitUntil(5000) { shown("browserBack") }
        val tall = bounds("browserContent").height
        js(browser, "document.body.style.height='8000px'; 'tall'")

        // A real finger pushing the page up folds the controls and gives the page the room.
        fun swipe(fromY: Float, toY: Float) {
            val origin = IntArray(2).also { compose.activity.window.decorView.getLocationOnScreen(it) }
            val x = origin[0] + bounds("browserContent").center.x.toInt()
            instrumentation.uiAutomation.executeShellCommand("input swipe $x ${origin[1] + fromY.toInt()} $x ${origin[1] + toY.toInt()} 350").close()
        }
        fun middle() = bounds("browserContent").center.y
        // Each swipe is given time to finish coasting, and a retry in case the busy emulator dropped it.
        fun swipeUntil(fromOffset: Float, toOffset: Float, condition: () -> Boolean) {
            var done = false
            repeat(4) {
                if (done) return@repeat
                SystemClock.sleep(1800)
                swipe(middle() + fromOffset, middle() + toOffset)
                done = runCatching { compose.waitUntil(4000, condition) }.isSuccess
            }
            assertTrue("The controls changed after a real swipe", done)
        }
        swipeUntil(300f, -300f) { shown("browserControlsStrip") && !shown("browserBack") }
        assertTrue("The page grew: ${bounds("browserContent").height} over $tall", bounds("browserContent").height > tall)
        assertEquals("The tab is unchanged", "https://example.com/", browser.page.url)

        // Scrolling back up returns them.
        swipeUntil(-150f, 150f) { shown("browserBack") && !shown("browserControlsStrip") }

        // So does a tap on the strip, even on a page that won't scroll.
        SystemClock.sleep(600)
        js(browser, "window.scrollTo(0, 3000); 'moved'")
        compose.waitUntil(8000) { shown("browserControlsStrip") }
        compose.onNodeWithTag("browserControlsStrip").performClick()
        compose.waitUntil(5000) { shown("browserBack") }

        // Left alone they fold by themselves, unless something needs them.
        compose.onNodeWithTag("browserMenu").performClick()
        compose.waitUntil(5000) { shown("browserMenuSheet") }
        dev.raticode.mobile.browser.BrowserControlsMotion.idleMillis = 400
        val until = SystemClock.uptimeMillis() + 1500
        compose.waitUntil(5000) { SystemClock.uptimeMillis() > until }
        assertTrue("Open while the menu is up", shown("browserBack"))
        instrumentation.uiAutomation.executeShellCommand("input keyevent KEYCODE_BACK").close()
        compose.waitUntil(8000) { shown("browserControlsStrip") }
    }

    private companion object {
        /** Builds a 20-second 440 Hz tone (longer than the test waits, so its time never wraps) in the page and a full-page Play button for the tap. */
        const val PLAYER = """(function(){var sr=8000,n=sr*20,b=new ArrayBuffer(44+n*2),v=new DataView(b);
function s(o,t){for(var i=0;i<t.length;i++)v.setUint8(o+i,t.charCodeAt(i))}
s(0,'RIFF');v.setUint32(4,36+n*2,true);s(8,'WAVE');s(12,'fmt ');v.setUint32(16,16,true);v.setUint16(20,1,true);
v.setUint16(22,1,true);v.setUint32(24,sr,true);v.setUint32(28,sr*2,true);v.setUint16(32,2,true);v.setUint16(34,16,true);
s(36,'data');v.setUint32(40,n*2,true);for(var i=0;i<n;i++)v.setInt16(44+i*2,Math.sin(i*2*Math.PI*440/sr)*8000,true);
window.ticks=0;setInterval(function(){ticks++},100);window.a=new Audio(URL.createObjectURL(new Blob([b],{type:'audio/wav'})));a.loop=true;
document.body.innerHTML='<button id=p style="position:fixed;left:0;top:0;width:100vw;height:100vh;font-size:40px">Play</button>';
document.getElementById('p').onclick=function(){a.play()};return 'ready'})()"""
    }
}
