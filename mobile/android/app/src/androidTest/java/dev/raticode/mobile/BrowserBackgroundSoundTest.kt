package dev.raticode.mobile

import android.accessibilityservice.AccessibilityService
import android.app.Application
import android.os.SystemClock
import androidx.activity.compose.setContent
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.lifecycle.Lifecycle
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.browser.BrowserMediaRules
import dev.raticode.mobile.browser.BrowserRules
import dev.raticode.mobile.browser.BrowserRuntime
import dev.raticode.mobile.live.LiveClient
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
 * A page playing sound in Raticode's browser keeps playing after Raticode leaves the screen (the Home button), with the
 * browser's playback notification held by a media foreground service; when it stops playing, the page pauses as every
 * page did before, and the service ends after the media bar's hold. Needs the emulator's internet (example.com) and skips without it. Waits use the
 * wall clock, as Compose's own waits don't advance while the app is in the background.
 */
class BrowserBackgroundSoundTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val app get() = instrumentation.targetContext.applicationContext as Application

    private fun shell(command: String) = instrumentation.uiAutomation.executeShellCommand(command).use {
        android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes().decodeToString()
    }
    private fun waitFor(what: String, timeout: Long = 15000, condition: () -> Boolean) {
        val deadline = SystemClock.uptimeMillis() + timeout
        while (!condition()) {
            if (SystemClock.uptimeMillis() > deadline) throw AssertionError(what)
            SystemClock.sleep(200)
        }
    }
    private fun js(browser: BrowserRuntime, script: String): String {
        val latch = CountDownLatch(1); var out = ""
        instrumentation.runOnMainSync { browser.view(browser.active!!)!!.evaluateJavascript(script) { out = it; latch.countDown() } }
        assertTrue("The page answered", latch.await(10, TimeUnit.SECONDS))
        return out.trim('"')
    }
    private fun serviceForeground() = shell("dumpsys activity services ${app.packageName}/.browser.BrowserPlaybackService").contains("isForeground=true")
    private fun stopped() = compose.activity.lifecycle.currentState == Lifecycle.State.CREATED

    @Test fun aPlayingPageKeepsPlayingAfterRaticodeLeavesTheScreen() {
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
        val online = runCatching { waitFor("example.com", 20000) { browser.page.title.contains("Example", ignoreCase = true) && !browser.page.loading } }.isSuccess
        assumeTrue("Needs the emulator's internet connection", online)
        try {
            // A looping tone and a 100 ms timer, started by a real tap as sites need.
            assertEquals("ready", js(browser, PLAYER))
            SystemClock.sleep(500)
            val page = compose.onNodeWithTag("browserContent").fetchSemanticsNode().boundsInRoot
            val origin = IntArray(2).also { compose.activity.window.decorView.getLocationOnScreen(it) }
            shell("input tap ${origin[0] + page.center.x.toInt()} ${origin[1] + page.center.y.toInt()}")
            waitFor("the tone started") { js(browser, "String(!a.paused && a.currentTime > 0.3)") == "true" }
            // Raticode notices within a check or two, and holds the playback notification while still in front.
            waitFor("the browser saw the page playing") { browser.playing.isNotEmpty() }
            waitFor("the playback service is in the foreground") { serviceForeground() }

            // Home: Raticode leaves the screen. The page keeps playing, and its scripts keep running.
            instrumentation.uiAutomation.performGlobalAction(AccessibilityService.GLOBAL_ACTION_HOME)
            waitFor("Raticode stopped") { stopped() }
            SystemClock.sleep(1000)
            val time = js(browser, "String(a.currentTime)").toDouble()
            val ticks = js(browser, "String(ticks)").toInt()
            SystemClock.sleep(3000)
            assertEquals("Still playing with Raticode away", "false", js(browser, "String(a.paused)"))
            assertTrue("The tone went on", js(browser, "String(a.currentTime)").toDouble() - time > 2.0)
            assertTrue("The page's timers ran", js(browser, "String(ticks)").toInt() - ticks >= 15)
            assertTrue(serviceForeground())

            // The music stops: the page pauses, as every page did before. The service and its lock-screen controls stay
            // through the media bar's hold, so Play still works, then end.
            js(browser, "a.pause(); 'paused'")
            waitFor("the browser saw the page stop") { browser.playing.isEmpty() }
            SystemClock.sleep(1000)
            val still = js(browser, "String(ticks)").toInt()
            SystemClock.sleep(2000)
            assertTrue("The paused page's timers stopped", js(browser, "String(ticks)").toInt() - still <= 1)
            waitFor("the service stopped once the hold ended", BrowserMediaRules.PAUSE_GRACE_MS + 10000) { !serviceForeground() }
        } finally {
            instrumentation.runOnMainSync { browser.stopSound() }
        }
    }

    private companion object {
        /** A 20-second 440 Hz tone in the page (the browser sees only players in the document), a 100 ms tick counter and a full-page Play button for the tap. */
        const val PLAYER = """(function(){var sr=8000,n=sr*20,b=new ArrayBuffer(44+n*2),v=new DataView(b);
function s(o,t){for(var i=0;i<t.length;i++)v.setUint8(o+i,t.charCodeAt(i))}
s(0,'RIFF');v.setUint32(4,36+n*2,true);s(8,'WAVE');s(12,'fmt ');v.setUint32(16,16,true);
v.setUint16(20,1,true);v.setUint32(24,sr,true);v.setUint32(28,sr*2,true);v.setUint16(32,2,true);v.setUint16(34,16,true);
v.setUint16(22,1,true);s(36,'data');v.setUint32(40,n*2,true);for(var i=0;i<n;i++)v.setInt16(44+i*2,Math.sin(i*2*Math.PI*440/sr)*8000,true);
window.ticks=0;setInterval(function(){ticks++},100);window.a=new Audio(URL.createObjectURL(new Blob([b],{type:'audio/wav'})));a.loop=true;
document.body.innerHTML='<button id=p style="position:fixed;left:0;top:0;width:100vw;height:100vh;font-size:40px">Play</button>';
document.body.appendChild(a);document.getElementById('p').onclick=function(){a.play()};return 'ready'})()"""
    }
}
