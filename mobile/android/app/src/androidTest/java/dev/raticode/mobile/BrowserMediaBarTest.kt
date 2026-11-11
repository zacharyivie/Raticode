package dev.raticode.mobile

import android.app.Application
import android.os.SystemClock
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Text
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertTextEquals
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithContentDescription
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.browser.BrowserMedia
import dev.raticode.mobile.browser.BrowserMediaRules
import dev.raticode.mobile.browser.BrowserRules
import dev.raticode.mobile.browser.BrowserRuntime
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.ui.BrowserMediaBar
import dev.raticode.mobile.ui.BrowserPage
import dev.raticode.mobile.ui.RaticodeTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * The media bar with a real WebView page: absent until a tab plays, truthful about what the page offers, and working while
 * another Raticode page is shown. The tab closing takes the bar with it. Needs the emulator's internet (example.com) and
 * skips without it.
 */
class BrowserMediaBarTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val app get() = instrumentation.targetContext.applicationContext as Application
    private var onBrowser by mutableStateOf(true)

    private fun shown(tag: String) = compose.onAllNodesWithTag(tag, useUnmergedTree = true).fetchSemanticsNodes().isNotEmpty()
    private fun button(label: String) = compose.onAllNodesWithContentDescription(label).fetchSemanticsNodes().isNotEmpty()
    private fun js(browser: BrowserRuntime, script: String): String {
        val latch = CountDownLatch(1); var out = ""
        compose.runOnUiThread { browser.view(browser.active!!)!!.evaluateJavascript(script) { out = it; latch.countDown() } }
        assertTrue("The page answered", latch.await(10, TimeUnit.SECONDS))
        return out.trim('"')
    }

    @Test fun theBarFollowsWhatTheTabIsDoing() {
        lateinit var browser: BrowserRuntime
        compose.runOnUiThread {
            browser = BrowserRuntime.get(app)
            browser.setEngine(BrowserRules.ENGINE_RATICODE)
            browser.tabs.map { it.id }.forEach(browser::close)
            browser.notice = null
            val client = LiveClient(app)
            compose.activity.setContent { RaticodeTheme { Column(Modifier.fillMaxSize()) {
                BrowserMedia.bar?.let { BrowserMediaBar(it, onOpen = { onBrowser = true }) }
                Box(Modifier.weight(1f)) { if (onBrowser) BrowserPage(client, null) {} else Text("Rem", Modifier.testTag("elsewhere")) }
            } } }
            browser.go("https://example.com/")
        }
        val online = runCatching { compose.waitUntil(20000) { browser.page.title.contains("Example", ignoreCase = true) && !browser.page.loading } }.isSuccess
        assumeTrue("Needs the emulator's internet connection", online)
        try {
            assertTrue("No bar before anything plays", !shown("browserMediaBar"))
            assertEquals("ready", js(browser, PLAYER))
            SystemClock.sleep(500)
            val page = compose.onNodeWithTag("browserContent").fetchSemanticsNode().boundsInRoot
            val origin = IntArray(2).also { compose.activity.window.decorView.getLocationOnScreen(it) }
            instrumentation.uiAutomation.executeShellCommand("input tap ${origin[0] + page.center.x.toInt()} ${origin[1] + page.center.y.toInt()}").close()

            // On the tab that plays, the page shows its own player: no bar, while the lock screen still has the track.
            compose.waitUntil(15000) { BrowserMedia.current != null }
            compose.waitForIdle()
            assertTrue("No bar over the tab that plays", !shown("browserMediaBar"))
            assertEquals("false", js(browser, "String(a.paused)"))

            // On another Raticode page it appears.
            onBrowser = false
            compose.waitUntil(15000) { shown("elsewhere") && shown("browserMediaBar") }

            // No metadata yet: the page title, the site, and no track buttons.
            compose.onNodeWithTag("browserMediaSite", useUnmergedTree = true).assertTextEquals("example.com")
            compose.onNodeWithTag("browserMediaNext").assertIsNotEnabled()
            compose.onNodeWithTag("browserMediaPrevious").assertIsNotEnabled()
            compose.onNodeWithTag("browserMediaToggle").assertIsEnabled()
            assertTrue(shown("browserMediaNoThumbnail"))

            // The page describes its track and offers Next, through the stand-in when WebView has no media session.
            js(browser, "navigator.mediaSession.metadata=new MediaMetadata({title:'Test tone',artist:'Raticode'});" +
                "navigator.mediaSession.setActionHandler('nexttrack',function(){window.nexts=(window.nexts||0)+1});'ok'")
            compose.waitUntil(10000) { BrowserMedia.current?.title == "Test tone" }
            compose.onNodeWithTag("browserMediaNext").assertIsEnabled().performClick()
            compose.waitUntil(5000) { js(browser, "String(window.nexts)") == "1" }

            // A pause keeps the bar for a while, so Play stays reachable.
            compose.onNodeWithTag("browserMediaToggle").performClick()
            compose.waitUntil(5000) { js(browser, "String(a.paused)") == "true" && button("Play") }
            SystemClock.sleep(BrowserMediaRules.STOP_GRACE_MS + 1000)
            compose.waitForIdle()
            assertTrue("Still there to resume", shown("browserMediaBar"))
            compose.onNodeWithTag("browserMediaToggle").performClick()
            compose.waitUntil(5000) { js(browser, "String(a.paused)") == "false" && button("Pause") }

            // Tapping the bar returns to the browser with the page still playing, and the bar steps aside.
            compose.onNodeWithTag("browserMediaOpen").performClick()
            compose.waitUntil(5000) { onBrowser && shown("browserContent") && !shown("browserMediaBar") }
            assertEquals("false", js(browser, "String(a.paused)"))

            // Paused while in view it stays hidden, and comes back with Play on another page during the hold.
            js(browser, "a.pause();'ok'")
            compose.waitUntil(5000) { BrowserMedia.current?.playing == false }
            compose.waitForIdle()
            assertTrue("No bar over the paused tab in view", !shown("browserMediaBar"))
            onBrowser = false
            compose.waitUntil(5000) { shown("elsewhere") && shown("browserMediaBar") && button("Play") }

            // Left paused, the bar goes when the grace ends.
            compose.waitUntil(BrowserMediaRules.PAUSE_GRACE_MS + BrowserRuntime.MEDIA_CHECK_MS + 4000) { !shown("browserMediaBar") }

            // The tab closing takes the bar at once.
            js(browser, "a.play();'ok'")
            compose.waitUntil(10000) { shown("elsewhere") && shown("browserMediaBar") }
            compose.runOnUiThread { browser.close(browser.active!!) }
            compose.waitUntil(2000) { !shown("browserMediaBar") }
        } finally {
            instrumentation.runOnMainSync { browser.stopSound() }
        }
    }

    private companion object {
        /** A 20-second tone in the page and a full-page Play button for the real tap sites need. */
        const val PLAYER = """(function(){var sr=8000,n=sr*20,b=new ArrayBuffer(44+n*2),v=new DataView(b);
function s(o,t){for(var i=0;i<t.length;i++)v.setUint8(o+i,t.charCodeAt(i))}
s(0,'RIFF');v.setUint32(4,36+n*2,true);s(8,'WAVE');s(12,'fmt ');v.setUint32(16,16,true);
v.setUint16(20,1,true);v.setUint32(24,sr,true);v.setUint32(28,sr*2,true);v.setUint16(32,2,true);v.setUint16(34,16,true);
v.setUint16(22,1,true);s(36,'data');v.setUint32(40,n*2,true);for(var i=0;i<n;i++)v.setInt16(44+i*2,Math.sin(i*2*Math.PI*440/sr)*8000,true);
window.a=new Audio(URL.createObjectURL(new Blob([b],{type:'audio/wav'})));a.loop=true;
document.body.innerHTML='<button id=p style="position:fixed;left:0;top:0;width:100vw;height:100vh;font-size:40px">Play</button>';
document.body.appendChild(a);document.getElementById('p').onclick=function(){a.play()};return 'ready'})()"""
    }
}
