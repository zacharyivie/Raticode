package dev.raticode.mobile

import android.app.Application
import android.graphics.Bitmap
import android.os.SystemClock
import androidx.activity.compose.setContent
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.ViewModelStore
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.browser.BrowserControlsMotion
import dev.raticode.mobile.browser.BrowserRules
import dev.raticode.mobile.browser.BrowserRuntime
import dev.raticode.mobile.browser.GeckoBrowser
import dev.raticode.mobile.live.DeviceStore
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.ui.LiveScreen
import dev.raticode.mobile.ui.NavBarPrefs
import dev.raticode.mobile.ui.RaticodeTheme
import org.json.JSONObject
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.mozilla.geckoview.GeckoRuntimeSettings
import org.mozilla.geckoview.GeckoSession
import java.io.File
import java.net.URLDecoder
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Typing into a web page in either built-in browser, on the real Browser page with Raticode's real bottom bar (a paired
 * computer is stood in for, as in VideoFullscreenTest). Two fields, each tapped as the user would with the on-screen
 * keyboard: one at the very bottom of a long page, and one at the top of a comment sheet fixed to the bottom of the
 * screen, as on m.youtube.com. With the keyboard open the page fills the room down to the keyboard, the field is in
 * view, and what `input text` types lands in it.
 *
 * Each check runs twice: as the emulator does it, and in a window that also shrinks for the keyboard, as the user's
 * Galaxy does (see LiveRemKeyboardLayoutTest). Counting the keyboard twice there left the page a strip under the
 * address bar with an empty band down to the keyboard (the user's m.youtube.com screenshot), and fails here.
 *
 * Raticode (chromium) writes its test page into example.com (it opens secure pages only) and skips without the
 * emulator's internet; Raticode (gecko) serves its page on the phone's loopback address.
 */
class BrowserKeyboardTypingTest {
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
    private fun boundsInWindow(tag: String): Rect = compose.onNodeWithTag(tag, useUnmergedTree = true).fetchSemanticsNode().boundsInWindow
    private fun shell(command: String) = instrumentation.uiAutomation.executeShellCommand(command).close()
    private val density get() = compose.activity.resources.displayMetrics.density

    /** The keyboard's height in pixels, as Android reports it to the window. */
    private fun keyboard(): Int {
        var bottom = 0
        instrumentation.runOnMainSync {
            bottom = ViewCompat.getRootWindowInsets(compose.activity.window.decorView)?.getInsets(WindowInsetsCompat.Type.ime())?.bottom ?: 0
        }
        return bottom
    }
    private fun windowHeight(): Int { var height = 0; instrumentation.runOnMainSync { height = compose.activity.window.decorView.height }; return height }
    /** Back, as the user closes the keyboard; the keyboard takes it. (Hiding it through the window's insets instead can
     *  leave the emulator's keyboard refusing the next page's request to show.) */
    private fun hideKeyboard() {
        shell("input keyevent KEYCODE_BACK")
        waitFor({ "the keyboard closed" }) { keyboard() == 0 && shown("desktopNavigation") }
        SystemClock.sleep(500)
    }

    /**
     * Some phones shrink the window for the keyboard and still report the keyboard to the app. The emulator never does,
     * so do the same here: the activity's content shrinks above the keyboard while Compose still hears about it.
     */
    private fun shrinkForKeyboard(on: Boolean) {
        instrumentation.runOnMainSync {
            val content = compose.activity.findViewById<android.view.ViewGroup>(android.R.id.content)
            if (on) ViewCompat.setOnApplyWindowInsetsListener(content) { view, insets ->
                view.setPadding(0, 0, 0, insets.getInsets(WindowInsetsCompat.Type.ime()).bottom); insets
            } else { ViewCompat.setOnApplyWindowInsetsListener(content, null); content.setPadding(0, 0, 0, 0) }
            ViewCompat.requestApplyInsets(content)
        }
        compose.waitForIdle()
    }

    /** Evidence for the report, saved in the app's own files (pulled by the runner, removed with the app). */
    private fun screenshot(name: String) {
        compose.waitForIdle()
        val shot = instrumentation.uiAutomation.takeScreenshot() ?: return
        app.getExternalFilesDir(null)?.let { dir -> File(dir, "typing-$name.png").outputStream().use { shot.compress(Bitmap.CompressFormat.PNG, 100, it) } }
    }

    /** What the page says about itself in its title: the focused field, its words, the viewport and where the field is. */
    private data class Report(val focused: String, val value: String, val height: Int, val visualTop: Int, val visualHeight: Int, val top: Int, val bottom: Int)
    private fun report(title: String): Report? {
        val parts = title.split('|')
        if (parts.size != 8 || parts[0] != "T") return null
        return runCatching {
            Report(parts[1], URLDecoder.decode(parts[2], "UTF-8"), parts[3].toInt(), parts[4].toInt(), parts[5].toInt(), parts[6].toInt(), parts[7].toInt())
        }.getOrNull()
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
        // The emulator has a hardware keyboard; show the on-screen one anyway, as on a phone.
        shell("settings put secure show_ime_with_hard_keyboard 1")
    }
    private fun cleanUp() {
        runCatching { shrinkForKeyboard(false) }
        runCatching { compose.runOnUiThread { owners.clear(); BrowserRuntime.get(app).setEngine(BrowserRules.ENGINE_RATICODE) } }
        if (::savedBar.isInitialized) NavBarPrefs.save(app, savedBar)
        DeviceStore(app).reset()
    }

    /**
     * Taps [field] on the page in [content] (its first report is [before]), types into it, and checks the page with the
     * keyboard open. Returns every broken expectation.
     */
    private fun typeInto(label: String, engine: String, prefix: String, content: String, field: String, before: Report, title: () -> String): List<String> {
        val failures = mutableListOf<String>()
        val folded = shown("${prefix}ControlsStrip")
        val area = boundsInWindow(content)
        val origin = IntArray(2).also { instrumentation.runOnMainSync { compose.activity.window.decorView.getLocationOnScreen(it) } }
        val x = origin[0] + area.center.x.toInt()
        val y = origin[1] + area.top + (before.top + before.bottom) / 2f * density
        // A freshly started engine can drop the first tap.
        for (attempt in 1..4) {
            shell("input tap $x ${y.toInt()}")
            if (runCatching { waitFor({ "" }, 4000) { keyboard() > 0 && report(title())?.focused == field } }.isSuccess) break
        }
        waitFor({ "$label: the keyboard opened on '$field': ime=${keyboard()} page='${title()}'" }) { keyboard() > 0 && report(title())?.focused == field }
        // The keyboard slides in and the page follows; measure once both have settled.
        var last = ""
        waitFor({ "$label: the page settled" }, 10000) {
            val now = "${keyboard()} ${boundsInWindow(content)} ${title()}"
            (now == last).also { last = now; SystemClock.sleep(250) }
        }
        // As the on-screen keyboard types: each letter committed through the engine's input connection.
        val typed = "Typed in $engine"
        var connection: android.view.inputmethod.InputConnection? = null
        instrumentation.runOnMainSync { connection = compose.activity.window.decorView.findFocus()?.onCreateInputConnection(android.view.inputmethod.EditorInfo()) }
        if (connection == null) failures += "$label: nothing on the screen takes typing"
        for (letter in typed) { instrumentation.runOnMainSync { connection?.commitText(letter.toString(), 1) }; SystemClock.sleep(80) }
        runCatching { waitFor({ "" }, 8000) { report(title())?.value == typed } }
        if (report(title())?.value != typed) failures += "$label: the keyboard typed '$typed', the field holds '${report(title())?.value}'"
        // And as key presses, which `adb input text` sends all at once. Every letter must land. GeckoView itself can put
        // a burst of key presses out of order (seen on the emulator, never with the keyboard's own path above), so for
        // Raticode (gecko) only the letters are compared.
        val keys = " by adb"
        shell("input text ${keys.replace(" ", "%s")}")
        val words = typed + keys
        runCatching { waitFor({ "" }, 8000) { report(title())?.value?.length == words.length } }
        val landed = report(title())?.value.orEmpty()
        val inOrder = engine != "gecko"
        if (if (inOrder) landed != words else landed.toList().sorted() != words.toList().sorted() || !landed.startsWith(typed))
            failures += "$label: key presses '$keys' left '$landed'"

        val page = boundsInWindow(content)
        val keyboardTop = windowHeight() - keyboard()
        val now = report(title())
        val facts = "$label: window=${windowHeight()} keyboardTop=$keyboardTop content=$page density=$density page='${title()}'"
        if (now == null) return listOf("No page report. $facts")
        fun dp(px: Float) = px / density
        // The page reaches down to the keyboard, not a keyboard's height above it, and the bar steps aside.
        if (page.bottom > keyboardTop + 1) failures += "The page runs ${dp(page.bottom - keyboardTop)} dp under the keyboard. $facts"
        if (dp(keyboardTop - page.bottom) > 24f) failures += "An empty band of ${dp(keyboardTop - page.bottom)} dp between the page and the keyboard. $facts"
        if (shown("desktopNavigation")) failures += "Raticode's bottom bar shows over the keyboard. $facts"
        // The engine itself was given that room: what it shows (its visual viewport) matches the page area. Gecko, like
        // Firefox, keeps the layout viewport and shrinks only the visual one; Chromium's WebView shrinks both.
        if (kotlin.math.abs(now.visualHeight - dp(page.height)) > 8f) failures += "The page shows ${now.visualHeight} dp in a ${dp(page.height)} dp area. $facts"
        if (now.visualHeight < 200) failures += "The page has only ${now.visualHeight} dp to show. $facts"
        // The field is in view, under the address strip and above the keyboard. Gecko scrolls to the field once, as the
        // keyboard opens; now and then it does so before its final viewport is known and stops up to 13 dp short, so its
        // field's bottom edge may sit that far under the keyboard (its words stay in view).
        val slack = if (engine == "gecko") 16 else 1
        if (now.top < now.visualTop - 1 || now.bottom > now.visualTop + now.visualHeight + slack) failures += "The field is out of view (${now.top}..${now.bottom} in ${now.visualTop}+${now.visualHeight}). $facts"
        // The controls stay as they were: the page moving with the keyboard isn't the user scrolling, and unfolding them
        // would push the field down behind the keyboard.
        if (folded && !shown("${prefix}ControlsStrip")) failures += "The browser controls unfolded as the keyboard opened. $facts"
        // And the words landed in it.
        if (now.focused != field) failures += "Focus moved to '${now.focused}'. $facts"
        if (now.value.length != words.length) failures += "The field holds '${now.value}', not '$words'. $facts"
        screenshot("$engine-$label")
        return failures
    }

    /**
     * The page at its end with the browser's controls folded into the address strip, as they are after a few seconds
     * of reading. The idle wait is made short, and opening and closing the browser's menu starts it again with that.
     */
    private fun loadFolded(prefix: String, title: () -> String, load: () -> Report): Report {
        load()
        BrowserControlsMotion.idleMillis = 300
        try {
            for (attempt in 1..3) {
                if (shown("${prefix}ControlsStrip")) break
                compose.onNodeWithTag("${prefix}Menu", useUnmergedTree = true).performClick()
                // Back before the sheet is up would go back a page instead.
                compose.waitUntil(5000) { shown("${prefix}MenuSheet") }
                SystemClock.sleep(300)
                shell("input keyevent KEYCODE_BACK")
                compose.waitUntil(5000) { !shown("${prefix}MenuSheet") }
                // waitUntil moves the test's clock on, which the idle wait's delay runs by.
                if (runCatching { compose.waitUntil(4000) { shown("${prefix}ControlsStrip") } }.isSuccess) break
            }
            assertTrue("the controls folded while reading: '${title()}'", shown("${prefix}ControlsStrip"))
        } finally { BrowserControlsMotion.idleMillis = Long.MAX_VALUE }
        var last = ""
        waitFor({ "the page stopped moving" }, 10000) { (title() == last && report(title()) != null).also { last = title(); SystemClock.sleep(300) } }
        return report(title())!!
    }

    /** Both fields, in a plain window and in one that shrinks for the keyboard. [load] shows the page in [mode] and returns its first report. */
    private fun checkEngine(engine: String, prefix: String, content: String, title: () -> String, load: (String) -> Report): List<String> {
        val failures = mutableListOf<String>()
        for (shrink in listOf(false, true)) {
            shrinkForKeyboard(shrink)
            for (mode in listOf("bottom", "sheet")) {
                val label = "$mode${if (shrink) "-shrunk" else ""}"
                val before = if (mode == "bottom") loadFolded(prefix, title) { load(mode) } else load(mode)
                failures += typeInto(label, engine, prefix, content, if (mode == "sheet") "comment" else "bottom", before, title)
                val typed = report(title())?.value
                hideKeyboard()
                // The words stay in the field when the keyboard closes.
                if (report(title())?.value != typed) failures += "$label: the words went with the keyboard: '${title()}'"
            }
        }
        shrinkForKeyboard(false)
        return failures
    }

    @Test fun typingInRaticodeChromiumPagesShowsTheFieldAndTheWords() {
        lateinit var browser: BrowserRuntime
        compose.runOnUiThread {
            browser = BrowserRuntime.get(app)
            browser.tabs.map { it.id }.forEach(browser::close)
            browser.notice = null
        }
        try {
            showBrowserPage(BrowserRules.ENGINE_RATICODE)
            compose.runOnUiThread { browser.go("https://example.com/") }
            val online = runCatching { waitFor({ "example.com" }, 30000) { browser.page.title.contains("Example", ignoreCase = true) && !browser.page.loading } }.isSuccess
            assumeTrue("Needs the emulator's internet connection", online)
            val failures = checkEngine("chromium", "browser", "browserContent", { browser.page.title }) { mode ->
                for (attempt in 1..3) {
                    js(browser, "document.open();document.write(${JSONObject.quote(page(mode))});document.close();'ok'")
                    if (runCatching { waitFor({ "" }, 5000) { report(browser.page.title)?.let { it.focused == "none" && it.height > 0 } == true } }.isSuccess) break
                }
                waitFor({ "the $mode test page: '${browser.page.title}'" }, 5000) { report(browser.page.title)?.let { it.focused == "none" && it.height > 0 } == true }
                SystemClock.sleep(500)
                report(browser.page.title)!!
            }
            assertTrue(failures.joinToString("\n"), failures.isEmpty())
        } finally {
            cleanUp()
        }
    }

    @Test fun typingInRaticodeGeckoPagesShowsTheFieldAndTheWords() {
        lateinit var gecko: GeckoBrowser
        compose.runOnUiThread {
            gecko = GeckoBrowser.get(app)
            gecko.tabs.map { it.id }.forEach(gecko::close)
        }
        val server = serve(mapOf("/bottom" to page("bottom"), "/sheet" to page("sheet")))
        try {
            showBrowserPage(BrowserRules.ENGINE_GECKO)
            waitFor({ "the Gecko page" }) { shown("geckoPage") }
            compose.runOnUiThread { gecko.go("about:blank") }
            waitFor({ "a Gecko tab" }) { gecko.session != null }
            // Raticode's Gecko is HTTPS-only; the test's loopback page is plain HTTP, so allow it for this test only.
            compose.runOnUiThread { gecko.runtime.settings.setAllowInsecureConnections(GeckoRuntimeSettings.ALLOW_ALL) }
            val failures = checkEngine("gecko", "gecko", "geckoContent", { gecko.page.title }) { mode ->
                compose.runOnUiThread {
                    gecko.session!!.load(GeckoSession.Loader().uri("http://127.0.0.1:${server.localPort}/$mode").flags(GeckoSession.LOAD_FLAGS_BYPASS_LOAD_URI_DELEGATE))
                }
                waitFor({ "the $mode test page: '${gecko.page.title}'" }, 30000) {
                    report(gecko.page.title)?.let { it.focused == "none" && it.value.isEmpty() && it.height > 0 } == true && !gecko.page.loading && gecko.page.url.endsWith("/$mode")
                }
                SystemClock.sleep(800)
                report(gecko.page.title)!!
            }
            assertTrue(failures.joinToString("\n"), failures.isEmpty())
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
     * The test page. "bottom": a long page scrolled to its end, with a field as the last thing on it, where the keyboard
     * will cover it. "sheet": a comment sheet fixed to the bottom of the screen with its field at the top, as on
     * m.youtube.com. The title reports, a few times a second: the focused element, the field's words, the page's viewport
     * height, the visual viewport's top and height, and the field's top and bottom, all in CSS pixels.
     */
    private fun page(mode: String): String {
        val field = if (mode == "sheet") "comment" else "bottom"
        val rows = (1..30).joinToString("") { "<div class=row>Row $it</div>" }
        val body = if (mode == "sheet") """$rows<div id=sheet><div class=head>Comments</div><textarea id=comment placeholder="Add a comment..."></textarea>
            <div id=list>${(1..20).joinToString("") { "<div class=row>Comment $it</div>" }}</div></div>"""
            else """$rows<input id=bottom placeholder="Field at the bottom">"""
        return """<!doctype html><meta name=viewport content="width=device-width,initial-scale=1"><title>Loading</title>
            <style>body{margin:0;background:#123;color:#fff;font:16px sans-serif}.row{height:56px;border-bottom:1px solid #345}
            #bottom{display:block;box-sizing:border-box;width:100%;height:48px;font-size:18px}
            #sheet{position:fixed;left:0;right:0;bottom:0;height:70vh;background:#222;display:flex;flex-direction:column}
            .head{height:56px;font-size:20px;padding:12px}#comment{height:48px;font-size:18px;margin:0 8px}#list{flex:1;overflow:auto}</style>
            <body>$body<script>
            var field=document.getElementById('$field');
            function report(){var f=document.activeElement,id=f&&f.id||'none',r=field.getBoundingClientRect(),v=window.visualViewport;
              document.title=['T',id,encodeURIComponent(field.value||''),Math.round(innerHeight),Math.round(v?v.offsetTop:0),Math.round(v?v.height:innerHeight),Math.round(r.top),Math.round(r.bottom)].join('|')}
            ${if (mode == "bottom") "window.scrollTo(0,document.body.scrollHeight);" else ""}
            report();setInterval(report,150);
            </script>""".trimIndent()
    }

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
