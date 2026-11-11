package dev.raticode.mobile

import android.app.Application
import android.content.ContentUris
import android.os.Build
import android.os.SystemClock
import android.provider.MediaStore
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.browser.BrowserRules
import dev.raticode.mobile.browser.BrowserRuntime
import dev.raticode.mobile.browser.GeckoBrowser
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.ui.BrowserPage
import dev.raticode.mobile.ui.RaticodeTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.mozilla.geckoview.GeckoRuntimeSettings
import org.mozilla.geckoview.GeckoSession

/**
 * Files a page builds itself (blob: and data: links with a download attribute), as Open WebUI's "Download" makes them,
 * reach Downloads from the phone's Gecko browser after the usual question, while a page still can't take the tab to a
 * data: address. The test page is a data: document loaded by the test itself, so the emulator needs no internet.
 */
class GeckoPageFileDownloadTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    @get:Rule val keepControls = KeepBrowserControls()
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val app get() = instrumentation.targetContext.applicationContext as Application

    private fun waitFor(what: () -> String, timeout: Long = 30000, condition: () -> Boolean) {
        val deadline = SystemClock.uptimeMillis() + timeout
        while (!condition()) {
            if (SystemClock.uptimeMillis() > deadline) throw AssertionError(what())
            SystemClock.sleep(150)
        }
    }

    private fun shell(command: String) = instrumentation.uiAutomation.executeShellCommand(command).use {
        android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes().decodeToString()
    }

    private fun savedFile(name: String): ByteArray? {
        val resolver = app.contentResolver
        resolver.query(MediaStore.Downloads.EXTERNAL_CONTENT_URI, arrayOf(MediaStore.Downloads._ID),
            "${MediaStore.Downloads.DISPLAY_NAME} = ? OR ${MediaStore.Downloads.DISPLAY_NAME} LIKE ?",
            // Android numbers a name that clashes with a file an earlier install left: "name (1).html".
            arrayOf(name, name.substringBeforeLast('.') + " (%)." + name.substringAfterLast('.')), "${MediaStore.Downloads.DATE_ADDED} DESC")?.use { cursor ->
            if (!cursor.moveToFirst()) return null
            val uri = ContentUris.withAppendedId(MediaStore.Downloads.EXTERNAL_CONTENT_URI, cursor.getLong(0))
            // The row appears before its bytes are written; only a written file counts.
            val bytes = resolver.openInputStream(uri)?.use { it.readBytes() }?.takeIf { it.isNotEmpty() } ?: return null
            resolver.delete(uri, null, null)
            return bytes
        }
        return null
    }

    @Test fun filesAPageBuildsItselfAreSavedAfterAsking() {
        assumeTrue("Saving needs Android 10", Build.VERSION.SDK_INT >= 29)
        lateinit var gecko: GeckoBrowser
        compose.runOnUiThread {
            BrowserRuntime.get(app).setEngine(BrowserRules.ENGINE_GECKO)
            gecko = GeckoBrowser.get(app)
            gecko.tabs.map { it.id }.forEach(gecko::close)
            val client = LiveClient(app)
            compose.activity.setContent { RaticodeTheme { Box(Modifier.fillMaxSize()) { BrowserPage(client, null) {} } } }
        }
        waitFor({ "the Gecko page" }, 10000) { compose.onAllNodesWithTag("geckoPage").fetchSemanticsNodes().isNotEmpty() }
        try {
            compose.runOnUiThread { gecko.go("about:blank") }
            waitFor({ "a Gecko tab" }) { gecko.session != null }
            // Gecko's page shows each notice and clears it; keep a copy for failure messages.
            compose.runOnUiThread {
                compose.activity.lifecycleScope.launch { androidx.compose.runtime.snapshotFlow { gecko.notice }.collect { it?.let(notices::add) } }
            }
            val html = "<!doctype html><title>Chat</title><p>Exported chat</p>"
            val quoted = org.json.JSONObject.quote(html)
            // Each run reinstalls the app, which then can't see files an earlier install saved, and Android gives a
            // new file with a clashing name a number. A name of this run's own keeps the check exact.
            val run = java.lang.Long.toString(System.currentTimeMillis() % 1_000_000_000L, 36)
            val cases = listOf(
                // A file from the server, for comparison.
                "server-$run.html" to "var a=document.createElement('a');a.href='/file.html';a.download='server-$run.html';document.body.append(a);a.click();a.remove();",
                // FileSaver.js: a detached link, clicked on the next task, its blob kept for 40 seconds.
                "file-saver-$run.html" to "var a=document.createElement('a');a.href=URL.createObjectURL(new Blob([$quoted],{type:'text/html'}));a.download='file-saver-$run.html';setTimeout(function(){a.dispatchEvent(new MouseEvent('click'))},0);",
                "notes-$run.html" to "var a=document.createElement('a');a.href='data:text/html;charset=utf-8,'+encodeURIComponent($quoted);a.download='notes-$run.html';document.body.append(a);a.click();a.remove();",
                // Open WebUI: an attached link, clicked, its blob revoked at once.
                "chat-export-$run.html" to "var u=URL.createObjectURL(new Blob([$quoted],{type:'text/html'}));var a=document.createElement('a');a.href=u;a.download='chat-export-$run.html';document.body.append(a);a.click();a.remove();URL.revokeObjectURL(u);",
            )
            val pages = cases.mapIndexed { index, (_, script) -> "/export$index" to "<!doctype html><title>Exporter</title><body style=margin:0><button id=b style='position:fixed;left:0;top:0;width:100vw;height:100vh;font-size:40px'>Export</button><script>b.onclick=function(){document.title='Clicked';$script}</script>" }.toMap() +
                ("/file.html" to html) + ("/trap" to "<!doctype html><title>Start</title><script>setTimeout(function(){location.href='data:text/html,<title>Fake sign-in</title>'},300)</script>")
            server = serve(pages)
            val base = "http://127.0.0.1:${server!!.localPort}"
            // Raticode's Gecko is HTTPS-only; the test's loopback page is plain HTTP, so allow it for this test only.
            compose.runOnUiThread { gecko.runtime.settings.setAllowInsecureConnections(GeckoRuntimeSettings.ALLOW_ALL) }
            for ((index, case) in cases.withIndex()) {
                val (name, _) = case
                compose.runOnUiThread {
                    gecko.notice = null
                    gecko.asks.toList().forEach(gecko::answered)
                    gecko.session!!.load(GeckoSession.Loader().uri("$base/export$index").flags(GeckoSession.LOAD_FLAGS_BYPASS_LOAD_URI_DELEGATE))
                }
                waitFor({ "$name: the page didn't load: ${gecko.page.url.take(60)} '${gecko.page.title}'" }) { gecko.page.title == "Exporter" && !gecko.page.loading }
                SystemClock.sleep(800)
                // The user's tap on the page's Export button.
                val page = compose.onNodeWithTag("geckoContent").fetchSemanticsNode().boundsInRoot
                val origin = IntArray(2).also { compose.activity.window.decorView.getLocationOnScreen(it) }
                // A freshly started Gecko can drop the first tap; tap again only while the page shows no click.
                for (attempt in 1..5) {
                    shell("input tap ${origin[0] + page.center.x.toInt()} ${origin[1] + page.center.y.toInt()}")
                    if (runCatching { waitFor({ "" }, 3000) { gecko.page.title == "Clicked" } }.isSuccess) break
                }
                waitFor({ "$name: the tap didn't reach the page: '${gecko.page.title}'" }, 2000) { gecko.page.title == "Clicked" }
                waitFor({ "$name: Gecko never asked to save; notice: ${gecko.notice}; asks: ${gecko.asks}" }) {
                    gecko.asks.any { it is GeckoBrowser.Ask.Confirm && it.title == "Save to Downloads?" }
                }
                val ask = gecko.asks.first { it is GeckoBrowser.Ask.Confirm && it.title == "Save to Downloads?" } as GeckoBrowser.Ask.Confirm
                assertTrue("$name: ${ask.message}", ask.message.startsWith(name))
                compose.runOnUiThread { gecko.answered(ask); ask.answer(true) }
                // The page shows the "Saved" notice and clears it, so look in Downloads instead.
                var saved: ByteArray? = null
                waitFor({ "$name: not saved; notices: $notices; title: ${gecko.page.title}" }, 50000) { savedFile(name).also { saved = it } != null }
                assertEquals(name, html, saved?.decodeToString())
            }

            // A page still can't take the tab to a data: address of its own.
            compose.runOnUiThread { gecko.session!!.load(GeckoSession.Loader().uri("$base/trap").flags(GeckoSession.LOAD_FLAGS_BYPASS_LOAD_URI_DELEGATE)) }
            waitFor({ "the trap page didn't load: '${gecko.page.title}'" }) { gecko.page.title == "Start" || gecko.page.title == "Fake sign-in" }
            SystemClock.sleep(3000)
            assertTrue("the page reached its data: address: ${gecko.page.title}", gecko.page.title != "Fake sign-in")
        } finally {
            server?.close()
            compose.runOnUiThread {
                gecko.runtime.settings.setAllowInsecureConnections(GeckoRuntimeSettings.HTTPS_ONLY)
                BrowserRuntime.get(app).setEngine(BrowserRules.ENGINE_RATICODE)
            }
        }
    }

    private var server: java.net.ServerSocket? = null
    private val notices = java.util.Collections.synchronizedList(ArrayList<String>())

    /** A plain HTTP server on this phone's loopback address, so the test page has a real origin without the internet. */
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
