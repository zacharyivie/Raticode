package dev.raticode.mobile

import android.app.Application
import android.os.SystemClock
import androidx.activity.compose.setContent
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.browser.BrowserMedia
import dev.raticode.mobile.browser.BrowserMediaRules
import dev.raticode.mobile.browser.BrowserRules
import dev.raticode.mobile.browser.BrowserRuntime
import dev.raticode.mobile.browser.GeckoBrowser
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
 * Android's media controls for Raticode's browser, read back from Android itself (`dumpsys media_session` and
 * `dumpsys notification`) and pressed the way a lock screen, headset or car does (`cmd media_session dispatch`). Needs the
 * emulator's internet and skips without it. Waits use the wall clock.
 */
class BrowserMediaControlsTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val app get() = instrumentation.targetContext.applicationContext as Application

    private fun shell(command: String) = instrumentation.uiAutomation.executeShellCommand(command).use {
        android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes().decodeToString()
    }
    private fun waitFor(what: String, timeout: Long = 15000, condition: () -> Boolean) {
        val deadline = SystemClock.uptimeMillis() + timeout
        while (!condition()) {
            if (SystemClock.uptimeMillis() > deadline) throw AssertionError("$what\n--- session:\n${session()}")
            SystemClock.sleep(200)
        }
    }

    /** Raticode's media session as Android reports it, or "" when it has none. */
    private fun session(): String {
        val lines = shell("dumpsys media_session").lines()
        val at = lines.indexOfFirst { it.trim() == "package=${app.packageName}" }
        if (at < 0) return ""
        return lines.subList(maxOf(0, at - 3), minOf(lines.size, at + 16)).joinToString("\n")
    }
    private fun sessionActive() = session().contains("active=true")
    private fun sessionState(state: String) = Regex("state=(PlaybackState \\{)?state=$state").containsMatchIn(session()) ||
        Regex("state=PlaybackState \\{state=[A-Z_]*\\(${if (state == "PLAYING") 3 else 2}\\)").containsMatchIn(session())
    private fun description() = Regex("description=([^\\n]*)").find(session())?.groupValues?.get(1).orEmpty()
    private fun serviceForeground() = shell("dumpsys activity services ${app.packageName}/.browser.BrowserPlaybackService").contains("isForeground=true")
    private fun notification() = shell("dumpsys notification --noredact").lines().filter { app.packageName in it || "android.title" in it || "actions=" in it }.joinToString("\n")
    private fun dispatch(key: String) { shell("cmd media_session dispatch $key") }

    private fun js(browser: BrowserRuntime, script: String): String {
        val latch = CountDownLatch(1); var out = ""
        instrumentation.runOnMainSync { browser.view(browser.active!!)!!.evaluateJavascript(script) { out = it; latch.countDown() } }
        assertTrue("The page answered", latch.await(10, TimeUnit.SECONDS))
        return out.trim('"')
    }

    @Test fun chromiumTabsShowAndObeyTheLockScreenControls() {
        lateinit var browser: BrowserRuntime
        var keyguardWasOff = false
        compose.runOnUiThread {
            browser = BrowserRuntime.get(app)
            browser.setEngine(BrowserRules.ENGINE_RATICODE)
            browser.tabs.map { it.id }.forEach(browser::close)
            browser.notice = null
            BrowserMedia.setLockScreenTitles(app, true)
            val client = LiveClient(app)
            compose.activity.setContent { RaticodeTheme { BrowserPage(client, null) {} } }
            browser.go("https://example.com/")
        }
        val online = runCatching { waitFor("example.com", 20000) { browser.page.title.contains("Example", ignoreCase = true) && !browser.page.loading } }.isSuccess
        assumeTrue("Needs the emulator's internet connection", online)
        try {
            assertFalse("No controls before anything plays", sessionActive())
            assertEquals("ready", js(browser, PLAYER))
            SystemClock.sleep(500)
            val page = compose.onNodeWithTag("browserContent").fetchSemanticsNode().boundsInRoot
            val origin = IntArray(2).also { compose.activity.window.decorView.getLocationOnScreen(it) }
            shell("input tap ${origin[0] + page.center.x.toInt()} ${origin[1] + page.center.y.toInt()}")
            waitFor("the tone started") { js(browser, "String(!a.paused && a.currentTime > 0.3)") == "true" }

            // Playing: a foreground media service with an active session that names the page, offering only Pause.
            waitFor("the controls appeared") { serviceForeground() && sessionActive() && sessionState("PLAYING") }
            waitFor("the session names the page") { description().startsWith("Example Domain") }
            assertTrue(notification(), notification().contains("Pause"))
            assertFalse("The page offers no Next", notification().contains("Next"))

            // A press from outside the app pauses the page; the controls stay through the hold so Play still works.
            dispatch("pause")
            waitFor("the page paused") { js(browser, "String(a.paused)") == "true" }
            waitFor("the controls show it paused") { sessionState("PAUSED") }
            assertTrue("The controls stay while paused", serviceForeground() && sessionActive())
            dispatch("play")
            waitFor("the page played again") { js(browser, "String(a.paused)") == "false" }
            waitFor("the controls show it playing") { sessionState("PLAYING") }

            // The page describes its track and offers Next: the controls follow, and Next reaches the page.
            js(browser, "navigator.mediaSession.metadata=new MediaMetadata({title:'Lock Song',artist:'The Band'});" +
                "navigator.mediaSession.setActionHandler('nexttrack',function(){window.nexted=(window.nexted||0)+1});'ok'")
            waitFor("the page's own title") { description().startsWith("Lock Song") && description().contains("The Band") }
            waitFor("Next offered") { notification().contains("Next") }
            dispatch("next")
            waitFor("Next reached the page") { js(browser, "String(window.nexted||0)") == "1" }
            shell("cmd statusbar expand-notifications"); SystemClock.sleep(1500)
            shell("screencap -p /data/local/tmp/raticode-media-controls-shade.png")
            shell("cmd statusbar collapse")

            // On the lock screen with titles on, the track shows there too. A swipe lock screen (no PIN) is turned on
            // for this, and put back as it was afterwards.
            keyguardWasOff = shell("locksettings get-disabled").trim() == "true"
            shell("locksettings set-disabled false")
            shell("input keyevent KEYCODE_SLEEP"); SystemClock.sleep(1000)
            shell("input keyevent KEYCODE_WAKEUP"); SystemClock.sleep(2000)
            assertTrue("Titles stay on the lock screen when allowed", description().startsWith("Lock Song"))
            shell("screencap -p /data/local/tmp/raticode-media-controls-lock.png")
            shell("wm dismiss-keyguard"); SystemClock.sleep(1000)

            // A private tab (here a Gecko tab whose page went away) never shows its page, and a press for a tab that no
            // longer exists removes it instead of doing anything else.
            val ghost = BrowserMediaRules.Key(BrowserMediaRules.Engine.GECKO, "ghost-tab")
            instrumentation.runOnMainSync {
                BrowserMedia.report(ghost, BrowserMediaRules.Report(true, "Secret song", "Secret band", "secret.example", canToggle = true, private = true))
            }
            waitFor("the private tab's generic title") { description().startsWith(BrowserMediaRules.PRIVATE_TITLE) }
            assertFalse(session(), session().contains("Secret"))
            assertFalse(notification().contains("Secret"))
            dispatch("pause")
            waitFor("the gone tab left the controls") { description().startsWith("Lock Song") }
            assertEquals("The real page kept playing", "false", js(browser, "String(a.paused)"))

            // With titles off for the lock screen, a screen turning off hides the title; unlocking brings it back.
            instrumentation.runOnMainSync { BrowserMedia.setLockScreenTitles(app, false) }
            shell("input keyevent KEYCODE_SLEEP")
            waitFor("the title hidden while locked") { description().startsWith(BrowserMediaRules.HIDDEN_TITLE) }
            assertFalse(session().contains("Lock Song"))
            shell("input keyevent KEYCODE_WAKEUP"); SystemClock.sleep(2000)
            assertTrue("Still hidden on the lock screen", description().startsWith(BrowserMediaRules.HIDDEN_TITLE))
            shell("screencap -p /data/local/tmp/raticode-media-controls-lock-hidden.png")
            shell("wm dismiss-keyguard")
            waitFor("the title back once unlocked") { description().startsWith("Lock Song") }

            // Closing the tab takes the controls at once.
            compose.runOnUiThread { browser.close(browser.active!!) }
            waitFor("the controls went with the tab", 5000) { !serviceForeground() && !sessionActive() }
        } finally {
            instrumentation.runOnMainSync { BrowserMedia.setLockScreenTitles(app, true); browser.stopSound() }
            shell("input keyevent KEYCODE_WAKEUP")
            shell("wm dismiss-keyguard")
            if (keyguardWasOff) shell("locksettings set-disabled true")
        }
    }

    /** The Gecko engine with a real audio file. Skips when Gecko can't start playing it here. */
    @Test fun geckoTabsShowAndObeyTheLockScreenControls() {
        lateinit var gecko: GeckoBrowser
        compose.runOnUiThread {
            val browser = BrowserRuntime.get(app)
            browser.setEngine(BrowserRules.ENGINE_GECKO)
            gecko = GeckoBrowser.get(app)
            gecko.tabs.map { it.id }.forEach(gecko::close)
            val client = LiveClient(app)
            compose.activity.setContent { RaticodeTheme { BrowserPage(client, null) {} } }
        }
        waitFor("the Gecko page", 10000) { compose.onAllNodesWithTag("geckoPage").fetchSemanticsNodes().isNotEmpty() }
        try {
            compose.runOnUiThread { gecko.go(AUDIO) }
            var playing = runCatching { waitFor("Gecko played the file", 20000) { gecko.playing.isNotEmpty() } }.isSuccess
            if (!playing) {
                // Autoplay was held back: tap the player as a person would.
                val page = compose.onNodeWithTag("geckoPage").fetchSemanticsNode().boundsInRoot
                val origin = IntArray(2).also { compose.activity.window.decorView.getLocationOnScreen(it) }
                shell("input tap ${origin[0] + page.center.x.toInt()} ${origin[1] + page.center.y.toInt()}")
                playing = runCatching { waitFor("Gecko played the file after a tap", 20000) { gecko.playing.isNotEmpty() } }.isSuccess
            }
            if (!playing) shell("screencap -p /data/local/tmp/raticode-media-controls-gecko.png")
            assumeTrue("Gecko needs the emulator's internet connection and must be able to play the file " +
                "(page ${gecko.page.url} '${gecko.page.title}' loading=${gecko.page.loading}, notice ${gecko.notice})", playing)

            waitFor("the controls appeared") { serviceForeground() && sessionActive() && sessionState("PLAYING") }
            dispatch("pause")
            waitFor("Gecko paused") { gecko.playing.isEmpty() && sessionState("PAUSED") }
            assertTrue("The controls stay while paused", serviceForeground())
            dispatch("play")
            waitFor("Gecko played again") { gecko.playing.isNotEmpty() && sessionState("PLAYING") }

            compose.runOnUiThread { gecko.tabs.map { it.id }.forEach(gecko::close) }
            waitFor("the controls went with the tab", 5000) { !serviceForeground() && !sessionActive() }
        } finally {
            instrumentation.runOnMainSync { gecko.stopSound() }
            compose.runOnUiThread { BrowserRuntime.get(app).setEngine(BrowserRules.ENGINE_RATICODE) }
        }
    }

    private companion object {
        /** A public six-minute MP3 served over https. */
        const val AUDIO = "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3"

        /** A 20-second looping tone in the page and a full-page Play button for the real tap sites need. */
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
