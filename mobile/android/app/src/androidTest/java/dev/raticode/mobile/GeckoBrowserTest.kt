package dev.raticode.mobile

import android.app.Application
import android.graphics.Bitmap
import android.os.SystemClock
import android.util.Log
import android.view.View
import android.view.ViewGroup
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.hasScrollToNodeAction
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performImeAction
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.unit.dp
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.browser.BrowserRules
import dev.raticode.mobile.browser.BrowserRuntime
import dev.raticode.mobile.browser.GeckoBrowser
import dev.raticode.mobile.browser.GeckoRules
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.ui.BrowserPage
import dev.raticode.mobile.ui.RaticodeTheme
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.mozilla.geckoview.GeckoView
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Raticode's Gecko browser with the real engine, above a stand-in for Raticode's bottom bar (the real one needs a paired
 * computer). Needs the emulator's internet; extension checks install real extensions from addons.mozilla.org and remove
 * them afterwards. The results for each extension go to logcat under `GeckoExtensions`.
 */
class GeckoBrowserTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    @get:Rule val keepControls = KeepBrowserControls()
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val app get() = instrumentation.targetContext.applicationContext as Application

    private fun waitFor(what: String, timeout: Long = 30000, condition: () -> Boolean) {
        val deadline = SystemClock.uptimeMillis() + timeout
        while (!condition()) {
            if (SystemClock.uptimeMillis() > deadline) throw AssertionError(what)
            SystemClock.sleep(150)
        }
    }
    private fun bounds(tag: String): Rect = compose.onNodeWithTag(tag).fetchSemanticsNode().boundsInRoot
    private fun shown(tag: String) = compose.onAllNodesWithTag(tag).fetchSemanticsNodes().isNotEmpty()

    private fun start(): GeckoBrowser {
        lateinit var gecko: GeckoBrowser
        compose.runOnUiThread {
            val browser = BrowserRuntime.get(app)
            browser.setEngine(BrowserRules.ENGINE_GECKO)
            gecko = GeckoBrowser.get(app)
            gecko.saved.profiles.filter { it.id != GeckoRules.DEFAULT_PROFILE }.forEach { gecko.deleteProfile(it.id) }
            gecko.tabs.map { it.id }.forEach(gecko::close)
            gecko.setColors(GeckoRules.COLORS_LIGHT)
            val client = LiveClient(app)
            // The same insets as the real app, so real taps at the top of the page land on Raticode and not the status bar.
            compose.activity.setContent { RaticodeTheme { Column(Modifier.fillMaxSize().safeDrawingPadding()) {
                Box(Modifier.weight(1f)) { BrowserPage(client, null) {} }
                Box(Modifier.fillMaxWidth().height(80.dp).testTag("raticodeBar"))
            } } }
        }
        waitFor("the Gecko page", 10000) { shown("geckoPage") }
        return gecko
    }
    private fun end() = compose.runOnUiThread { BrowserRuntime.get(app).setEngine(BrowserRules.ENGINE_RATICODE) }
    private fun online(gecko: GeckoBrowser): Boolean {
        compose.runOnUiThread { gecko.go("https://example.com/") }
        return runCatching { waitFor("example.com", 45000) { gecko.page.title.contains("Example", ignoreCase = true) && !gecko.page.loading } }.isSuccess
    }

    @Test fun controlsFoldWhileReadingAndComeBack() {
        val gecko = start()
        try {
            compose.runOnUiThread { gecko.go("https://addons.mozilla.org/en-US/android/") }
            val online = runCatching { waitFor("a long page", 45000) { gecko.page.url.contains("addons.mozilla.org") && !gecko.page.loading } }.isSuccess
            assumeTrue("Needs the emulator's internet", online)
            waitFor("Gecko's view") { shown("geckoView") && shown("geckoBack") }
            SystemClock.sleep(3000)
            val tall = bounds("geckoContent").height
            fun swipe(fromY: Float, toY: Float) {
                val origin = IntArray(2).also { compose.activity.window.decorView.getLocationOnScreen(it) }
                val x = origin[0] + bounds("geckoContent").center.x.toInt()
                instrumentation.uiAutomation.executeShellCommand("input swipe $x ${origin[1] + fromY.toInt()} $x ${origin[1] + toY.toInt()} 400").close()
            }
            fun middle() = bounds("geckoContent").center.y
            // Each swipe is given time to finish coasting, and a retry in case the busy emulator dropped it.
            fun swipeUntil(what: String, fromOffset: Float, toOffset: Float, condition: () -> Boolean) {
                var done = false
                repeat(4) {
                    if (done) return@repeat
                    SystemClock.sleep(1800)
                    swipe(middle() + fromOffset, middle() + toOffset)
                    done = runCatching { waitFor(what, 4000, condition) }.isSuccess
                }
                assertTrue(what, done)
            }
            swipeUntil("the strip", 300f, -300f) { shown("geckoControlsStrip") && !shown("geckoBack") }
            assertTrue("Gecko's page got the room", bounds("geckoContent").height > tall)
            assertEquals("The bar below hasn't moved", bounds("raticodeBar").top, bounds("geckoContent").bottom, 1f)
            swipeUntil("the toolbar", -150f, 150f) { shown("geckoBack") && !shown("geckoControlsStrip") }
            swipeUntil("the strip again", 300f, -300f) { shown("geckoControlsStrip") }
            // A real tap, as a user makes it, on the same screen the real swipes went to.
            // Compose's test clock runs the crossfade only while the test waits on it; a tap before it ends hits the outgoing row.
            val settled = SystemClock.uptimeMillis() + 1000
            compose.waitUntil(5000) { SystemClock.uptimeMillis() > settled }
            val strip = bounds("geckoControlsStrip")
            val origin = IntArray(2).also { compose.activity.window.decorView.getLocationOnScreen(it) }
            instrumentation.uiAutomation.executeShellCommand("input tap ${origin[0] + strip.center.x.toInt()} ${origin[1] + strip.center.y.toInt()}").close()
            waitFor("the toolbar after a tap", 5000) { shown("geckoBack") }
            assertTrue("The toolbar stays while the page finishes coasting", runCatching { waitFor("folded", 1000) { !shown("geckoBack") } }.isFailure)
            assertTrue("Still on the page", gecko.page.url.contains("addons.mozilla.org"))
        } finally { end() }
    }

    @Test fun browsesInsideRaticodeWithTabsProfilesFavoritesAndRules() {
        val gecko = start()
        try {
            // Layout: the controls and the address on one row, Gecko's page down to Raticode's bar.
            val address = bounds("geckoAddress"); val content = bounds("geckoContent"); val bar = bounds("raticodeBar")
            val touch = with(compose.density) { 48.dp.toPx() }
            for (tag in listOf("geckoBack", "geckoTabs", "geckoReload", "geckoMenu")) {
                val control = bounds(tag)
                assertEquals("$tag beside the address", address.center.y, control.center.y, 1f)
                assertTrue("$tag above the page", control.bottom <= content.top + 1)
                assertTrue("$tag is a full target", control.width >= touch - 1 && control.height >= touch - 1)
            }
            assertEquals("Nothing between the page and Raticode's bar", bar.top, content.bottom, 1f)

            assumeTrue("Needs the emulator's internet", online(gecko))
            assertTrue("Gecko reports the page as secure", gecko.page.secure)
            waitFor("Gecko's view is on screen") { shown("geckoView") }
            assertEquals("Gecko's page is laid out above Raticode's bar", bar.top, bounds("geckoView").bottom, 1f)

            // Back, forward and reload.
            compose.runOnUiThread { gecko.go("https://example.com/?second") }
            waitFor("second page") { gecko.page.url.endsWith("?second") && !gecko.page.loading && gecko.page.canBack }
            compose.onNodeWithTag("geckoBack").performClick()
            waitFor("back") { gecko.page.url == "https://example.com/" && gecko.page.canForward }
            compose.onNodeWithTag("geckoForward").performClick()
            waitFor("forward") { gecko.page.url.endsWith("?second") }
            compose.onNodeWithTag("geckoReload").performClick()
            waitFor("reload") { !gecko.page.loading }

            // Tabs keep their own pages.
            val first = gecko.active!!
            compose.onNodeWithTag("geckoTabs").performClick()
            waitFor("tab sheet") { shown("geckoNewTab") }
            compose.onNodeWithTag("geckoNewTab").performClick()
            waitFor("second tab") { gecko.tabs.size == 2 && gecko.active != first }
            compose.runOnUiThread { gecko.go("https://www.iana.org/help/example-domains") }
            waitFor("second tab's page", 45000) { gecko.page.url.contains("iana.org") && !gecko.page.loading }
            compose.runOnUiThread { gecko.select(first) }
            waitFor("first tab again") { gecko.page.url.endsWith("?second") }
            assertEquals(2, gecko.tabs.size)

            // Favorites and history belong to a profile.
            compose.runOnUiThread { gecko.toggleBookmark() }
            assertTrue(gecko.isBookmarked(gecko.page.url))
            assertTrue(gecko.history.any { it.url.contains("example.com") })
            val personal = gecko.bookmarks.size
            compose.runOnUiThread { assertTrue(gecko.addProfile("Work")) }
            waitFor("the new profile") { gecko.profile.name == "Work" && gecko.tabs.size == 1 }
            assertEquals("A new profile starts empty", 0, gecko.bookmarks.size)
            assertTrue(gecko.page.url == BrowserRules.HOME)
            compose.runOnUiThread { gecko.switchProfile(GeckoRules.DEFAULT_PROFILE) }
            waitFor("back to Personal") { gecko.tabs.size == 2 && gecko.bookmarks.size == personal }

            // Rules: internal and local addresses never load, and a bad certificate can't be passed.
            val before = gecko.page.url
            compose.runOnUiThread { gecko.go("about:config") }
            SystemClock.sleep(800)
            assertEquals("about:config stays closed", before, gecko.page.url)
            compose.runOnUiThread { gecko.go("file:///system/etc/hosts") }
            SystemClock.sleep(800)
            assertEquals("file: stays closed", before, gecko.page.url)
            compose.runOnUiThread { gecko.go("https://expired.badssl.com/") }
            waitFor("the certificate refusal", 45000) { gecko.page.title == "This connection isn't private" }
            assertFalse(gecko.page.secure)
        } finally { end() }
    }

    @Test fun tabsOpenedByPagesAndExtensionsKeepTheLimitAndDesktopSites() {
        val gecko = start()
        try {
            compose.runOnUiThread { gecko.setDesktopSites(true) }
            var opened: org.mozilla.geckoview.GeckoSession? = null
            compose.runOnUiThread { opened = gecko.openedTab("https://example.com/", gecko.saved.currentProfile); opened!!.open(gecko.runtime) }
            assertEquals("A page's new tab honours Desktop sites", org.mozilla.geckoview.GeckoSessionSettings.USER_AGENT_MODE_DESKTOP, opened!!.settings.userAgentMode)
            compose.runOnUiThread { gecko.setDesktopSites(false); repeat(BrowserRules.MAX_TABS) { gecko.newTab() } }
            assertEquals(BrowserRules.MAX_TABS, gecko.saved.tabs.size)
            var refused: org.mozilla.geckoview.GeckoSession? = opened
            compose.runOnUiThread { gecko.notice = null; refused = gecko.openedTab("https://example.com/", gecko.saved.currentProfile) }
            assertEquals("No tab past the limit", null, refused)
            assertEquals(BrowserRules.MAX_TABS, gecko.saved.tabs.size)
        } finally {
            compose.runOnUiThread { gecko.setDesktopSites(false); gecko.tabs.map { it.id }.forEach(gecko::close) }
            end()
        }
    }

    private fun latest(slug: String): String {
        val connection = URL("https://addons.mozilla.org/api/v5/addons/addon/$slug/").openConnection() as HttpURLConnection
        return try {
            JSONObject(connection.inputStream.bufferedReader().readText()).getJSONObject("current_version").getJSONObject("file").getString("url")
        } finally { connection.disconnect() }
    }

    /** Installs [slug] from Mozilla's site, answering Raticode's permission dialog; null when Gecko refused it. */
    private fun install(gecko: GeckoBrowser, slug: String): GeckoBrowser.Extension? {
        val known = gecko.extensions.map { it.id }.toSet()
        val url = latest(slug)
        assertTrue(url, GeckoRules.installable(url))
        compose.runOnUiThread { gecko.notice = null; gecko.install(url) }
        var prompt: GeckoBrowser.Ask.Install? = null
        waitFor("$slug to install or fail", 90000) {
            (gecko.asks.firstOrNull() as? GeckoBrowser.Ask.Install)?.let { ask ->
                prompt = ask
                compose.runOnUiThread { gecko.answered(ask); ask.answer(true) }
            }
            url !in gecko.installing
        }
        waitFor("$slug listed or refused", 15000) { gecko.extensions.any { it.id !in known } || gecko.notice != null }
        val added = gecko.extensions.firstOrNull { it.id !in known }
        Log.i("GeckoExtensions", "$slug: " + (added?.let { "installed ${it.version} enabled=${it.enabled} button=${it.action != null} options=${it.options != null}" } ?: "refused: ${gecko.notice}") +
            " permissions=${prompt?.permissions?.size} origins=${prompt?.origins?.size}")
        return added
    }

    private fun geckoView(view: View): GeckoView? = when (view) {
        is GeckoView -> view
        is ViewGroup -> (0 until view.childCount).firstNotNullOfOrNull { geckoView(view.getChildAt(it)) }
        else -> null
    }

    /** The share of light pixels on the page as Gecko drew it. */
    private fun lightShare(): Double {
        var bitmap: Bitmap? = null; var failure: Throwable? = null
        // Right after a reload the compositor can briefly refuse a capture.
        for (attempt in 1..20) {
            val latch = CountDownLatch(1)
            compose.runOnUiThread { geckoView(compose.activity.window.decorView)!!.capturePixels().accept({ bitmap = it; latch.countDown() }, { failure = it; latch.countDown() }) }
            assertTrue("Gecko drew the page", latch.await(30, TimeUnit.SECONDS))
            if (bitmap != null) break
            SystemClock.sleep(400)
        }
        val shot = bitmap ?: throw AssertionError("capturePixels failed: $failure")
        var light = 0; var total = 0
        for (x in 0 until shot.width step 20) for (y in 0 until shot.height step 20) {
            val pixel = shot.getPixel(x, y); total++
            if ((android.graphics.Color.red(pixel) + android.graphics.Color.green(pixel) + android.graphics.Color.blue(pixel)) / 3 > 128) light++
        }
        return light.toDouble() / total
    }

    @Test fun installsAndRunsFirefoxExtensions() {
        val gecko = start()
        try {
            assumeTrue("Needs the emulator's internet", online(gecko))
            waitFor("the view") { shown("geckoView") }
            SystemClock.sleep(2500)
            val before = lightShare()
            Log.i("GeckoExtensions", "light share before Dark Reader: $before")
            assertTrue("example.com starts light: $before", before > 0.5)

            // Dark Reader really changes how pages draw.
            val dark = install(gecko, "darkreader")
            assertTrue("Dark Reader installed: ${gecko.notice}", dark != null && dark.enabled)
            compose.runOnUiThread { gecko.reload() }
            waitFor("reloaded") { !gecko.page.loading }
            var after = before
            waitFor("Dark Reader darkens the page", 30000) { after = lightShare(); after < 0.3 }
            Log.i("GeckoExtensions", "light share after Dark Reader: $after")

            // Turned off, it stops.
            compose.runOnUiThread { gecko.setEnabled(gecko.extensions.first { it.id == dark!!.id }, false) }
            waitFor("disabled") { gecko.extensions.first { it.id == dark!!.id }.enabled.not() }
            SystemClock.sleep(1500)
            compose.runOnUiThread { gecko.go("https://example.com/?off") }
            waitFor("a fresh load") { gecko.page.url.endsWith("?off") && !gecko.page.loading }
            var off = 0.0
            val light = runCatching { waitFor("the page is light again", 20000) { off = lightShare(); off > 0.5 } }
            Log.i("GeckoExtensions", "light share with Dark Reader off: $off; enabled=${gecko.extensions.first { it.id == dark!!.id }.enabled}")
            light.getOrThrow()

            // uBlock Origin installs, and its toolbar button opens its popup inside Raticode.
            val ublock = install(gecko, "ublock-origin")
            assertTrue("uBlock Origin installed: ${gecko.notice}", ublock != null && ublock.enabled)
            waitFor("uBlock's button", 30000) { gecko.extensions.first { it.id == ublock!!.id }.action != null }
            compose.runOnUiThread { gecko.extensions.first { it.id == ublock!!.id }.action!!.click() }
            waitFor("uBlock's popup", 20000) { gecko.popup != null }
            waitFor("the popup on screen", 10000) { shown("geckoPopup") }
            SystemClock.sleep(1500)
            compose.runOnUiThread { gecko.closePopup() }

            // The rest of the sample: what Gecko accepts, recorded in logcat and asserted only for extensions made for Android.
            install(gecko, "bitwarden-password-manager")?.let { assertTrue(it.enabled) }

            // The way a user does it: search Mozilla's catalogue from Extensions in the menu, tap Add, then approve Raticode's dialog.
            val guid = JSONObject(URL("https://addons.mozilla.org/api/v5/addons/addon/sponsorblock/").readText()).getString("guid")
            compose.onNodeWithTag("geckoMenu").performClick()
            waitFor("menu") { shown("geckoMenuSheet") }
            compose.onNode(hasText("Extensions")).performClick()
            waitFor("extensions sheet") { shown("geckoExtensionSearch") }
            compose.onNodeWithTag("geckoExtensionSearch").performTextInput("SponsorBlock")
            compose.onNodeWithTag("geckoExtensionSearch").performImeAction()
            waitFor("search results", 30000) { compose.onAllNodes(hasText("From Firefox Add-ons")).fetchSemanticsNodes().isNotEmpty() }
            compose.onNode(hasScrollToNodeAction()).performScrollToNode(hasTestTag("geckoInstall-$guid"))
            compose.onNodeWithTag("geckoInstall-$guid").performClick()
            waitFor("the permission dialog", 60000) { shown("geckoInstallAllow") }
            compose.onNodeWithTag("geckoInstallAllow").performClick()
            waitFor("SponsorBlock added", 30000) { gecko.extensions.any { it.id == guid && it.enabled } }
            Log.i("GeckoExtensions", "sponsorblock via the sheet: installed ${gecko.extensions.first { it.id == guid }.version}")
            install(gecko, "multi-account-containers")
            install(gecko, "tree-style-tab")
            Log.i("GeckoExtensions", "total installed: ${gecko.extensions.size}")
        } finally {
            compose.runOnUiThread { gecko.extensions.forEach(gecko::uninstall) }
            SystemClock.sleep(1500)
            end()
        }
    }
}
