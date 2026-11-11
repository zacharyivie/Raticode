package dev.raticode.mobile

import android.app.Application
import android.content.ContentUris
import android.os.Build
import android.os.SystemClock
import android.provider.MediaStore
import androidx.activity.compose.setContent
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.browser.BrowserRules
import dev.raticode.mobile.browser.BrowserRuntime
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.ui.BrowserPage
import dev.raticode.mobile.ui.RaticodeTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Files a page builds itself (blob: and data: links with a download attribute), as Open WebUI's "Download" makes them,
 * reach Downloads from the phone's WebView browser after the usual question, including a blob the page revokes right
 * after the click. Needs the emulator's internet connection (example.com) and skips without it.
 */
class BrowserPageFileDownloadTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val app get() = instrumentation.targetContext.applicationContext as Application

    private fun waitFor(what: String, timeout: Long = 15000, condition: () -> Boolean) {
        val deadline = SystemClock.uptimeMillis() + timeout
        while (!condition()) {
            if (SystemClock.uptimeMillis() > deadline) throw AssertionError(what)
            SystemClock.sleep(100)
        }
    }

    private fun js(browser: BrowserRuntime, script: String): String {
        val latch = CountDownLatch(1); var out = ""
        instrumentation.runOnMainSync { browser.view(browser.active!!)!!.evaluateJavascript(script) { out = it; latch.countDown() } }
        assertTrue("The page answered", latch.await(10, TimeUnit.SECONDS))
        return out.trim('"')
    }

    /** The saved file's bytes, read back through MediaStore, which the app may read for files it saved. */
    private fun savedFile(name: String): ByteArray? {
        if (Build.VERSION.SDK_INT < 29) return null
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
        val html = "<!doctype html><title>Chat</title><p>Exported chat</p>"
        // A name of this run's own, as each run reinstalls the app and an earlier install's files are out of its sight.
        val run = java.lang.Long.toString(System.currentTimeMillis() % 1_000_000_000L, 36)
        val cases = listOf(
            // Open WebUI: an attached link, clicked, its blob revoked at once.
            Triple("revoke-now", "chat-export-$run.html",
                "(function(){var u=URL.createObjectURL(new Blob([@H@],{type:'text/html'}));var a=document.createElement('a');a.href=u;a.download='chat-export-$run.html';document.body.append(a);a.click();a.remove();URL.revokeObjectURL(u);return 'ok'})()"),
            // FileSaver.js: a detached link, so the page's name can't be seen; the type names it.
            Triple("file-saver", "download.html",
                "(function(){var a=document.createElement('a');a.href=URL.createObjectURL(new Blob([@H@],{type:'text/html'}));a.download='ignored.html';setTimeout(function(){a.dispatchEvent(new MouseEvent('click'))},0);return 'ok'})()"),
            Triple("data", "notes-$run.html",
                "(function(){var a=document.createElement('a');a.href='data:text/html;charset=utf-8,'+encodeURIComponent(@H@);a.download='notes-$run.html';document.body.append(a);a.click();a.remove();return 'ok'})()"),
        )
        for ((label, name, script) in cases) {
            instrumentation.runOnMainSync { browser.notice = null }
            assertEquals("ok", js(browser, script.replace("@H@", org.json.JSONObject.quote(html))))
            waitFor("$label: the browser asked to save") { (browser.ask as? BrowserRuntime.Ask.Download)?.fromPage == true }
            val ask = browser.ask as BrowserRuntime.Ask.Download
            // WebView may pass a detached link's own name as a Content-Disposition; either name is right.
            assertTrue("$label: ${ask.name}", ask.name == name || (label == "file-saver" && ask.name == "ignored.html"))
            assertEquals(label, html.length.toLong(), ask.size)
            assertEquals(label, "example.com", ask.host)
            instrumentation.runOnMainSync { browser.download(compose.activity) }
            // The page shows the "Saved" notice and clears it, so look in Downloads instead.
            var saved: ByteArray? = null
            waitFor("$label: saved") { savedFile(ask.name).also { saved = it } != null }
            assertEquals(label, html, saved?.decodeToString())
        }

        // Cancelling keeps nothing.
        instrumentation.runOnMainSync { browser.notice = null }
        js(browser, cases[0].third.replace("@H@", org.json.JSONObject.quote(html)).replace("chat-export-$run.html", "kept-not-$run.html"))
        waitFor("asked again") { (browser.ask as? BrowserRuntime.Ask.Download)?.fromPage == true }
        instrumentation.runOnMainSync { browser.answerNo() }
        SystemClock.sleep(500)
        assertNull(savedFile("kept-not-$run.html"))
        assertNull(browser.ask)
    }
}
