package dev.raticode.mobile

import android.app.Application
import android.content.pm.ActivityInfo
import android.content.res.Configuration
import android.graphics.Bitmap
import android.os.SystemClock
import androidx.activity.compose.setContent
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onRoot
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.ViewModelStore
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.browser.BrowserRules
import dev.raticode.mobile.browser.BrowserRuntime
import dev.raticode.mobile.browser.GeckoBrowser
import dev.raticode.mobile.browser.VideoFullscreen
import dev.raticode.mobile.live.DeviceStore
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.ui.LiveScreen
import dev.raticode.mobile.ui.NavBarPrefs
import dev.raticode.mobile.ui.RaticodeTheme
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.mozilla.geckoview.GeckoRuntimeSettings
import org.mozilla.geckoview.GeckoSession
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * A video's full-screen button in either built-in browser, on the real Browser page with Raticode's real bottom bar (a
 * paired computer is stood in for, as in LiveScreenTest): the video fills the whole screen in landscape, with Raticode's
 * bar, the browser's controls and Android's bars hidden, and without the screen being rebuilt. Back, showing another tab
 * and Raticode leaving the screen are handled, and leaving full screen gives back the page as it was, still playing.
 * The Raticode (chromium) part writes its test page into example.com (it opens secure pages only) and skips without the
 * emulator's internet; the Raticode (gecko) part serves its page on the phone's loopback address.
 */
class VideoFullscreenTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    @get:Rule val keepControls = KeepBrowserControls()
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val app get() = instrumentation.targetContext.applicationContext as Application

    private fun waitFor(what: () -> String, timeout: Long = 20000, condition: () -> Boolean) {
        val deadline = SystemClock.uptimeMillis() + timeout
        // The test rule moves Compose's frames on only while the test waits for Compose, so let it catch up each time.
        while (!runCatching { compose.waitForIdle(); condition() }.getOrDefault(false)) {
            if (SystemClock.uptimeMillis() > deadline) throw AssertionError(what())
            SystemClock.sleep(150)
        }
    }
    private fun shown(tag: String) = compose.onAllNodesWithTag(tag, useUnmergedTree = true).fetchSemanticsNodes().isNotEmpty()
    private fun bounds(tag: String): Rect = compose.onNodeWithTag(tag, useUnmergedTree = true).fetchSemanticsNode().boundsInRoot
    private fun barsVisible(): Boolean {
        var visible = true
        instrumentation.runOnMainSync {
            val insets = ViewCompat.getRootWindowInsets(compose.activity.window.decorView)
            visible = insets == null || insets.isVisible(WindowInsetsCompat.Type.statusBars()) || insets.isVisible(WindowInsetsCompat.Type.navigationBars())
        }
        return visible
    }
    private fun landscape() = compose.activity.resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE
    private fun requested(): Int { var value = 0; instrumentation.runOnMainSync { value = compose.activity.requestedOrientation }; return value }
    private fun shell(command: String) = instrumentation.uiAutomation.executeShellCommand(command).close()
    private fun back() = instrumentation.runOnMainSync { compose.activity.onBackPressedDispatcher.onBackPressed() }
    /** The user's tap on the page's button, which covers the lower half of the page. */
    private fun tapPage(tag: String) {
        val page = bounds(tag)
        val origin = IntArray(2).also { instrumentation.runOnMainSync { compose.activity.window.decorView.getLocationOnScreen(it) } }
        shell("input tap ${origin[0] + page.center.x.toInt()} ${origin[1] + (page.top + page.height * 0.75f).toInt()}")
    }
    /** Evidence for the report: what the screen showed, saved in the app's own files (pulled by the runner, removed with the app). */
    private fun screenshot(name: String) {
        val shot = instrumentation.uiAutomation.takeScreenshot() ?: return
        app.getExternalFilesDir(null)?.let { dir -> File(dir, "fullscreen-$name.png").outputStream().use { shot.compress(Bitmap.CompressFormat.PNG, 100, it) } }
    }

    private lateinit var savedBar: List<String>
    private val owners = ViewModelStore()

    /** Raticode's real screen, paired, on the Browser page with Browser on the bottom bar. */
    private fun showBrowserPage(engine: String) {
        savedBar = NavBarPrefs.load(app)
        NavBarPrefs.save(app, listOf("Rem", "Threads", "Browser"))
        val store = DeviceStore(app)
        store.reset()
        store.save(store.load().put("peer", JSONObject()))
        compose.runOnUiThread {
            BrowserRuntime.get(app).setEngine(engine)
            val client = LiveClient(app)
            owners.put("screen", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client, startPage = "Browser") } }
        }
        waitFor({ "the paired Browser page with Raticode's bar" }) { shown("desktopNavigation") }
    }
    private fun cleanUp() {
        runCatching { compose.runOnUiThread { BrowserRuntime.get(app).exitFullscreen(); GeckoBrowser.peek()?.exitFullscreen() } }
        runCatching { compose.runOnUiThread { owners.clear(); BrowserRuntime.get(app).setEngine(BrowserRules.ENGINE_RATICODE) } }
        if (::savedBar.isInitialized) NavBarPrefs.save(app, savedBar)
        DeviceStore(app).reset()
    }

    /** Full screen as it should be: everything Raticode and Android show is gone, in landscape, on the same screen. */
    private fun assertFullscreen(activity: MainActivity, video: String) {
        waitFor({ "full screen with the video in front: ${VideoFullscreen.current}" }) { VideoFullscreen.current != null && shown(video) }
        waitFor({ "landscape: requested ${requested()}, configuration ${compose.activity.resources.configuration.orientation}" }) {
            requested() == ActivityInfo.SCREEN_ORIENTATION_USER_LANDSCAPE && landscape()
        }
        waitFor({ "Android's status and navigation bars hidden" }) { !barsVisible() }
        compose.waitForIdle()
        assertTrue("Raticode's bottom bar is hidden", !shown("desktopNavigation"))
        assertTrue("The media bar is hidden", !shown("browserMediaBar"))
        val root = compose.onRoot().fetchSemanticsNode().size
        waitFor({ "the video fills the whole screen: ${bounds(video)} in ${root}" }) {
            val area = bounds(video)
            area.left <= 1f && area.top <= 1f && area.width >= root.width - 1 && area.height >= root.height - 1 && root.width > root.height
        }
        assertSame("Turning the screen didn't rebuild it", activity, compose.activity)
    }
    private fun assertRestored(title: () -> String, id: String, controls: String) {
        waitFor({ "full screen ended: ${VideoFullscreen.current}" }) { VideoFullscreen.current == null }
        waitFor({ "Raticode's bar and the browser controls are back" }) { shown("desktopNavigation") && shown(controls) }
        waitFor({ "Android's bars are back" }) { barsVisible() }
        assertEquals("The phone follows its own orientation again", ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED, requested())
        waitFor({ "the same page, still playing, not reloaded: '${title()}'" }) { title() == "Inline $id playing" }
    }

    @Test fun raticodeChromiumVideoGoesFullScreenInLandscapeAndComesBack() {
        lateinit var browser: BrowserRuntime
        compose.runOnUiThread {
            browser = BrowserRuntime.get(app)
            browser.tabs.map { it.id }.forEach(browser::close)
            browser.notice = null
        }
        try {
            showBrowserPage(BrowserRules.ENGINE_RATICODE)
            val activity = compose.activity
            compose.runOnUiThread { browser.go("https://example.com/") }
            val online = runCatching { waitFor({ "example.com" }, 30000) { browser.page.title.contains("Example", ignoreCase = true) && !browser.page.loading } }.isSuccess
            assumeTrue("Needs the emulator's internet connection", online)
            val original = browser.active!!

            fun load(width: Int, height: Int): String {
                // A tab just brought back to the front can miss the first write while it resumes.
                for (attempt in 1..3) {
                    js(browser, "document.open();document.write(${JSONObject.quote(page(width, height))});document.close();'ok'")
                    if (runCatching { waitFor({ "" }, 5000) { browser.page.title.startsWith("Ready ") } }.isSuccess) break
                }
                waitFor({ "the test page: '${browser.page.title}' / '${js(browser, "document.title")}'" }, 5000) { browser.page.title.startsWith("Ready ") }
                SystemClock.sleep(500)
                return browser.page.title.removePrefix("Ready ")
            }
            fun enter() {
                for (attempt in 1..4) {
                    tapPage("browserContent")
                    if (runCatching { waitFor({ "" }, 4000) { VideoFullscreen.current != null } }.isSuccess) break
                }
            }

            // Portrait to start with, the bar showing.
            assertTrue(!landscape() && shown("desktopNavigation") && barsVisible())
            val id = load(320, 180)
            enter()
            assertFullscreen(activity, "browserFullscreen")
            screenshot("chromium-full")

            // Raticode leaving the screen and coming back keeps the video full screen.
            compose.activityRule.scenario.moveToState(Lifecycle.State.CREATED)
            compose.activityRule.scenario.moveToState(Lifecycle.State.RESUMED)
            assertFullscreen(activity, "browserFullscreen")

            // Back ends it: the page as it was, still playing, in portrait with every bar back.
            back()
            assertRestored({ browser.page.title }, id, "browserBack")
            waitFor({ "portrait again" }) { !landscape() }
            screenshot("chromium-after")

            // Showing another tab, as a link or Rem can, ends it too.
            enter()
            assertFullscreen(activity, "browserFullscreen")
            compose.runOnUiThread { browser.newTab() }
            waitFor({ "full screen ended by the new tab" }) { VideoFullscreen.current == null && shown("desktopNavigation") }
            assertEquals(ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED, requested())

            // An upright video stays upright, still with every bar hidden.
            compose.runOnUiThread { browser.select(original) }
            waitFor({ "back on the first tab: '${browser.page.title}'" }) { browser.active == original && !browser.page.loading && shown("browserContent") }
            load(180, 320)
            enter()
            waitFor({ "the upright video's size: ${VideoFullscreen.current}" }) { (VideoFullscreen.current?.height ?: 0) == 320L }
            waitFor({ "upright: requested ${requested()}, now ${VideoFullscreen.current}, title '${browser.page.title}', bar ${shown("desktopNavigation")}, overlay ${shown("browserFullscreen")}" }) { requested() == ActivityInfo.SCREEN_ORIENTATION_USER_PORTRAIT && !landscape() }
            waitFor({ "Android's bars hidden" }) { !barsVisible() }
            assertTrue(!shown("desktopNavigation"))
            screenshot("chromium-upright")
            back()
            waitFor({ "upright full screen ended" }) { VideoFullscreen.current == null && shown("desktopNavigation") }
        } finally {
            cleanUp()
        }
    }

    @Test fun raticodeGeckoVideoGoesFullScreenInLandscapeAndComesBack() {
        lateinit var gecko: GeckoBrowser
        compose.runOnUiThread {
            gecko = GeckoBrowser.get(app)
            gecko.tabs.map { it.id }.forEach(gecko::close)
        }
        val server = serve(mapOf("/video" to page(320, 180)))
        try {
            showBrowserPage(BrowserRules.ENGINE_GECKO)
            val activity = compose.activity
            waitFor({ "the Gecko page" }) { shown("geckoPage") }
            compose.runOnUiThread { gecko.go("about:blank") }
            waitFor({ "a Gecko tab" }) { gecko.session != null }
            // Raticode's Gecko is HTTPS-only; the test's loopback page is plain HTTP, so allow it for this test only.
            compose.runOnUiThread {
                gecko.runtime.settings.setAllowInsecureConnections(GeckoRuntimeSettings.ALLOW_ALL)
                gecko.session!!.load(GeckoSession.Loader().uri("http://127.0.0.1:${server.localPort}/video").flags(GeckoSession.LOAD_FLAGS_BYPASS_LOAD_URI_DELEGATE))
            }
            waitFor({ "the test page: '${gecko.page.title}'" }, 30000) { gecko.page.title.startsWith("Ready ") && !gecko.page.loading }
            SystemClock.sleep(800)
            val id = gecko.page.title.removePrefix("Ready ")
            fun enter() {
                // A freshly started Gecko can drop the first tap.
                for (attempt in 1..5) {
                    tapPage("geckoContent")
                    if (runCatching { waitFor({ "" }, 4000) { VideoFullscreen.current != null } }.isSuccess) break
                }
            }

            assertTrue(!landscape() && shown("desktopNavigation") && barsVisible())
            enter()
            assertFullscreen(activity, "geckoContent")
            screenshot("gecko-full")

            compose.activityRule.scenario.moveToState(Lifecycle.State.CREATED)
            compose.activityRule.scenario.moveToState(Lifecycle.State.RESUMED)
            assertFullscreen(activity, "geckoContent")

            back()
            assertRestored({ gecko.page.title }, id, "geckoAddress")
            waitFor({ "portrait again" }) { !landscape() }
            screenshot("gecko-after")

            enter()
            assertFullscreen(activity, "geckoContent")
            compose.runOnUiThread { gecko.newTab() }
            waitFor({ "full screen ended by the new tab" }) { VideoFullscreen.current == null && shown("desktopNavigation") }
            assertEquals(ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED, requested())
        } finally {
            server.close()
            runCatching { compose.runOnUiThread { gecko.runtime.settings.setAllowInsecureConnections(GeckoRuntimeSettings.HTTPS_ONLY) } }
            cleanUp()
        }
    }

    private fun js(browser: BrowserRuntime, script: String): String {
        val latch = CountDownLatch(1); var out = ""
        compose.runOnUiThread { browser.view(browser.active!!)!!.evaluateJavascript(script) { out = it; latch.countDown() } }
        assertTrue("The page answered", latch.await(10, TimeUnit.SECONDS))
        return out.trim('"')
    }

    /**
     * A page with a [width] x [height] video drawn live from a canvas (no network or media files), and a button that plays
     * it full screen. The title names the page load, so a reload shows, and says whether the video still plays after.
     */
    private fun page(width: Int, height: Int) = """<!doctype html><meta name=viewport content="width=device-width"><title>Loading</title>
        <body style="margin:0;background:#222"><canvas id=c width=$width height=$height style="width:4px;height:4px"></canvas>
        <video id=v muted playsinline style="width:100vw;height:45vh;background:#000"></video>
        <button id=b style="position:fixed;left:0;top:50vh;width:100vw;height:50vh;font-size:32px">Full screen</button>
        <script>var id=String(Date.now()%1000000),x=c.getContext('2d'),n=0;
        setInterval(function(){x.fillStyle='hsl('+(n++*9%360)+',80%,50%)';x.fillRect(0,0,c.width,c.height)},50);
        v.srcObject=c.captureStream(20);document.title='Ready '+id;
        b.onclick=function(){v.play();v.requestFullscreen().then(function(){document.title='Full '+id},function(e){document.title='Refused '+e.name})};
        document.addEventListener('fullscreenchange',function(){if(!document.fullscreenElement)setTimeout(function(){document.title='Inline '+id+' '+(v.paused?'paused':'playing')},300)});
        </script>""".trimIndent()

    /** A plain HTTP server on this phone's loopback address, so the Gecko page has a real origin without the internet. */
    private fun serve(pages: Map<String, String>): java.net.ServerSocket {
        val socket = java.net.ServerSocket(0, 16, java.net.InetAddress.getByAddress(byteArrayOf(127, 0, 0, 1)))
        Thread {
            while (!socket.isClosed) {
                val client = runCatching { socket.accept() }.getOrNull() ?: break
                runCatching {
                    client.use {
                        val path = it.getInputStream().bufferedReader().readLine().orEmpty().split(' ').getOrElse(1) { "/" }
                        val body = (pages[path] ?: "").toByteArray()
                        val status = if (pages.containsKey(path)) "200 OK" else "404 Not Found"
                        it.getOutputStream().apply {
                            write("HTTP/1.1 $status\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: ${body.size}\r\nConnection: close\r\n\r\n".toByteArray())
                            write(body); flush()
                        }
                    }
                }
            }
        }.apply { isDaemon = true }.start()
        return socket
    }

}
