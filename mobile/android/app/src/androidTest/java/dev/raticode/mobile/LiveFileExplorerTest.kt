package dev.raticode.mobile

import android.app.Application
import android.content.Context
import androidx.activity.compose.setContent
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.lifecycle.ViewModelStore
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.*
import dev.raticode.mobile.ui.LiveScreen
import dev.raticode.mobile.ui.RaticodeTheme
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import java.io.File

/**
 * The file explorer against `live-device-fixture.py --workspace --remote --desktop --files`, which runs the desktop's
 * real device application on a disposable "Fixture project" holding "Explorer samples": Markdown, code, a 470 KB log,
 * PDF, HTML, SVG, a .docx, PNG, an animated GIF, MP4, WAV, a 12 MB video, a .env file and a link outside the project.
 * Pass `explorerInvitationFile`. Screenshots go to /data/local/tmp/raticode-explorer-*.png.
 */
class LiveFileExplorerTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    private val app get() = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application

    private fun screenshot(name: String) {
        compose.waitForIdle()
        InstrumentationRegistry.getInstrumentation().uiAutomation.executeShellCommand("screencap -p /data/local/tmp/raticode-explorer-$name.png").use {
            android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
        }
    }

    private fun waitFor(what: String, client: LiveClient, timeout: Long = 20000, condition: () -> Boolean) {
        try { compose.waitUntil(timeout, condition) }
        catch (failure: ComposeTimeoutException) {
            throw AssertionError("$what: errors=${client.desktopErrors}; fetch=${client.explorerFetch}; status=${client.status}; files=${client.fileExplorer}", failure)
        }
    }

    private fun shows(text: String, substring: Boolean = true) = compose.onAllNodesWithText(text, substring = substring).fetchSemanticsNodes().isNotEmpty()
    private fun tagged(tag: String) = compose.onAllNodesWithTag(tag, useUnmergedTree = true).fetchSemanticsNodes().isNotEmpty()
    private fun pause(millis: Long) { val start = System.currentTimeMillis(); compose.waitUntil(millis + 5000) { System.currentTimeMillis() - start >= millis } }
    private fun webView(): android.webkit.WebView? {
        fun find(view: android.view.View): android.webkit.WebView? = view as? android.webkit.WebView
            ?: (view as? android.view.ViewGroup)?.let { group -> (0 until group.childCount).firstNotNullOfOrNull { find(group.getChildAt(it)) } }
        var found: android.webkit.WebView? = null
        compose.runOnUiThread { found = find(compose.activity.window.decorView) }
        return found
    }
    /** How often [text] is on the web view's page, by its own find-in-page, which works with JavaScript off; -1 before it answers. */
    private fun webMatches(text: String): Int {
        val count = java.util.concurrent.atomic.AtomicInteger(-1)
        compose.runOnUiThread { webView()?.let { web -> web.setFindListener { _, n, done -> if (done) count.set(n) }; web.findAllAsync(text) } }
        val start = System.currentTimeMillis()
        compose.waitUntil(10000) { count.get() >= 0 || System.currentTimeMillis() - start > 8000 }
        compose.runOnUiThread { webView()?.clearMatches() }
        return count.get()
    }
    /** Waits until the page holds [text], or fails. */
    private fun webShows(text: String) {
        val start = System.currentTimeMillis()
        while (webMatches(text) <= 0) {
            if (System.currentTimeMillis() - start > 30000) throw AssertionError("The web preview never showed \"$text\"")
            pause(1000)
        }
    }
    private fun back() { compose.runOnUiThread { compose.activity.onBackPressedDispatcher.onBackPressed() }; compose.waitForIdle() }
    /** Scrolls the folder to [name] (rows off screen aren't composed yet) and opens it. */
    private fun open(name: String, client: LiveClient) {
        waitFor("row $name", client) {
            runCatching { compose.onNodeWithTag("explorerEntries").performScrollToNode(hasTestTag("entry:$name")) }.isSuccess
        }
        compose.onNodeWithTag("entry:$name").performClick()
        compose.waitForIdle()
    }

    /**
     * Needs `-e olderDesktop true` and the same fixture run from a desktop without the file explorer (for example
     * `git archive f7d5753`). The phone says to update the desktop and never sends the explorer's requests.
     */
    @Test fun olderDesktopIsAskedToUpdate() {
        val path = InstrumentationRegistry.getArguments().getString("explorerInvitationFile")
        assumeTrue("Requires an older desktop fixture and -e olderDesktop true",
            path != null && InstrumentationRegistry.getArguments().getString("olderDesktop") == "true")
        DeviceStore(app).reset()
        app.getSharedPreferences("navigation", Context.MODE_PRIVATE).edit().clear().commit()
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app)
            owners.put("explorer", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client) } }
            client.preview(File(path!!).readText()); client.pair()
        }
        try {
            waitFor("older desktop", client, 30000) { client.connected && client.desktopControl && client.fileExplorer == false }
            compose.onNode(hasContentDescription("More") and hasAnyAncestor(hasTestTag("desktopNavigation"))).performClick()
            compose.onNodeWithText("Update Raticode on your computer to browse its files here").assertExists()
            compose.onNodeWithText("Files", substring = false).performScrollTo().performClick()
            waitFor("update note", client) { tagged("explorerUpdateDesktop") }
            screenshot("older-desktop")
            pause(12000)
            // Nothing was asked of the desktop, so nothing was refused.
            assertTrue(client.desktopErrors.isEmpty())
            assertFalse(client.desktopViews.keys.any { it.startsWith("files.") })
        } finally {
            compose.runOnUiThread { owners.clear() }
            app.getSharedPreferences("navigation", Context.MODE_PRIVATE).edit().clear().commit()
            DeviceStore(app).reset()
        }
    }

    /** The selection menu is the system's floating toolbar, so its items are found and pressed through accessibility. */
    private fun pressMenuItem(label: String) {
        val automation = InstrumentationRegistry.getInstrumentation().uiAutomation
        automation.serviceInfo = automation.serviceInfo.apply { flags = flags or android.accessibilityservice.AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS }
        var found: android.view.accessibility.AccessibilityNodeInfo? = null
        var opened = false
        compose.waitUntil(8000) {
            val roots = automation.windows.mapNotNull { it.root }
            found = roots.firstNotNullOfOrNull { root -> root.findAccessibilityNodeInfosByText(label).firstOrNull { it.text?.toString() == label } }
            if (found == null && !opened) roots.firstNotNullOfOrNull { it.findAccessibilityNodeInfosByText("More options").firstOrNull() }?.let {
                it.performAction(android.view.accessibility.AccessibilityNodeInfo.ACTION_CLICK); opened = true
            }
            found != null
        }
        val target = generateSequence(found!!) { it.parent }.firstOrNull { it.isClickable } ?: found!!
        assertTrue("$label pressed", target.performAction(android.view.accessibility.AccessibilityNodeInfo.ACTION_CLICK))
        compose.waitForIdle()
    }

    /** Text picked in a real file fills Rem's message with its path, project and line, and sends nothing; a secret-looking file offers nothing. */
    @Test fun askRemFromTheViewerFillsRemsMessage() {
        val path = InstrumentationRegistry.getArguments().getString("explorerInvitationFile")
        assumeTrue("Requires disposable fixture with --workspace --remote --desktop --files",
            path != null && InstrumentationRegistry.getArguments().getString("olderDesktop") == null)
        compose.runOnUiThread { compose.activity.requestedOrientation = android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT }
        DeviceStore(app).reset()
        app.getSharedPreferences("navigation", Context.MODE_PRIVATE).edit().clear().commit()
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app)
            owners.put("explorer", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client) } }
            client.preview(File(path!!).readText()); client.pair()
        }
        val more = hasContentDescription("More") and hasAnyAncestor(hasTestTag("desktopNavigation"))
        try {
            waitFor("desktop control", client, 30000) { client.connected && client.desktopControl && client.fileExplorer == true }
            compose.onNode(more).performClick()
            compose.onNodeWithText("Files", substring = false).performScrollTo().performClick()
            waitFor("projects", client) { (client.desktopViews[FileExplorer.LIST] as? ExplorerProjects)?.projects?.isNotEmpty() == true }
            compose.onNodeWithText("Fixture project").performClick()
            open("Explorer samples", client); open("code", client); open("app.kt", client)
            waitFor("code", client) { tagged("codeView") && shows("fun main") }
            screenshot("ask-rem-code")

            val sentBefore = client.messages.size
            compose.onNodeWithText("fun main() {").performTouchInput { longClick(centerLeft + androidx.compose.ui.geometry.Offset(30f, 0f)) }
            compose.waitForIdle()
            pressMenuItem("Ask Rem")
            waitFor("message box", client) { tagged("remComposer") }
            val box = compose.onNodeWithTag("remComposer")
            box.assert(hasText("About this file: Explorer samples/code/app.kt", substring = true))
            box.assert(hasText("Project: Fixture project", substring = true))
            box.assert(hasText("Lines: 3", substring = true))
            box.assert(hasText("fun", substring = true))
            assertEquals("Nothing is sent by Ask Rem", sentBefore, client.messages.size)
            // The cursor is after the words, so the question goes at the end.
            val box2 = box.fetchSemanticsNode().config
            assertEquals(box2[androidx.compose.ui.semantics.SemanticsProperties.EditableText].text.length,
                box2[androidx.compose.ui.semantics.SemanticsProperties.TextSelectionRange].end)
            screenshot("ask-rem-message")

            // A secret-looking file stays closed, so there's nothing to select or ask about.
            compose.runOnUiThread { androidx.core.view.WindowCompat.getInsetsController(compose.activity.window, compose.activity.window.decorView)
                .hide(androidx.core.view.WindowInsetsCompat.Type.ime()) }
            waitFor("bottom bar", client) { compose.onAllNodes(more).fetchSemanticsNodes().isNotEmpty() }
            compose.onNode(more).performClick()
            compose.onNodeWithText("Files", substring = false).performScrollTo().performClick()
            waitFor("the file stayed open", client) { tagged("codeView") }
            back(); back()
            open(".env", client)
            waitFor("refused", client) { tagged("fileProblem") }
            assertTrue(shows("may hold passwords, keys or sign-ins"))
            compose.onAllNodes(hasText("Ask Rem", substring = true)).assertCountEquals(0)
        } finally {
            compose.runOnUiThread { owners.clear() }
            app.getSharedPreferences("navigation", Context.MODE_PRIVATE).edit().clear().commit()
            DeviceStore(app).reset()
        }
    }

    @Test fun browseAndOpenEveryKindOfFile() {
        val path = InstrumentationRegistry.getArguments().getString("explorerInvitationFile")
        assumeTrue("Requires disposable fixture with --workspace --remote --desktop --files",
            path != null && InstrumentationRegistry.getArguments().getString("olderDesktop") == null)
        compose.runOnUiThread { compose.activity.requestedOrientation = android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT }
        DeviceStore(app).reset()
        app.getSharedPreferences("navigation", Context.MODE_PRIVATE).edit().clear().commit()
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app)
            owners.put("explorer", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client) } }
            client.preview(File(path!!).readText()); client.pair()
        }
        try {
            waitFor("desktop control", client, 30000) { client.connected && client.desktopControl && client.fileExplorer == true }

            // Files is in More with the other desktop pages.
            compose.onNode(hasContentDescription("More") and hasAnyAncestor(hasTestTag("desktopNavigation"))).performClick()
            compose.onNodeWithText("Browse and open files in your open projects").assertExists()
            compose.onNodeWithText("Files", substring = false).performScrollTo().performClick()
            waitFor("projects", client) { (client.desktopViews[FileExplorer.LIST] as? ExplorerProjects)?.projects?.isNotEmpty() == true }
            compose.onNodeWithText("Fixture project").assertIsDisplayed()
            screenshot("projects")
            compose.onNodeWithText("Fixture project").performClick()
            open("Explorer samples", client)
            waitFor("samples", client) { tagged("entry:code") }
            // Folders first; a secret-looking file and a link are listed and say why they won't open.
            val rows = compose.onNodeWithTag("explorerEntries")
            rows.performScrollToNode(hasTestTag("entry:.env"))
            compose.onNodeWithText("May hold passwords or keys · stays on your computer").assertExists()
            rows.performScrollToNode(hasTestTag("entry:outside"))
            compose.onNodeWithText("Link · the phone doesn't follow links").assertExists()
            rows.performScrollToNode(hasTestTag("entry:code"))
            compose.onNodeWithTag("explorerCrumbs").assertIsDisplayed()
            screenshot("folder")

            // The filter narrows the folder by name.
            compose.onNodeWithContentDescription("Filter this folder").performClick()
            compose.onNodeWithTag("explorerFilter").performTextInput("read")
            compose.waitForIdle()
            assertTrue(tagged("entry:README.md")); assertFalse(tagged("entry:code"))
            screenshot("filter")
            compose.onNodeWithContentDescription("Stop filtering").performClick()

            // Markdown shows rendered, like the desktop's preview, and its source on request.
            open("README.md", client)
            waitFor("markdown", client) { tagged("markdownView") }
            assertTrue(shows("Explorer samples")); assertFalse(shows("# Explorer samples"))
            screenshot("markdown")
            compose.onNodeWithContentDescription("Show source").performClick()
            waitFor("markdown source", client) { tagged("codeView") }
            assertTrue(shows("# Explorer samples"))
            back()

            // Code has line numbers and highlighting, find and wrapping.
            open("code", client); open("app.kt", client)
            waitFor("code", client) { tagged("codeView") && shows("fun main") }
            screenshot("code")
            compose.onNodeWithContentDescription("Find in file").performClick()
            compose.onNodeWithTag("findInFile").performTextInput("greeting")
            waitFor("matches", client) { shows("1 of 2") }
            compose.onNodeWithContentDescription("Next match").performClick()
            waitFor("second match", client) { shows("2 of 2") }
            screenshot("find")
            compose.onNodeWithContentDescription("Stop finding").performClick()
            compose.onNodeWithContentDescription("Wrap long lines").performClick()
            screenshot("nowrap")
            back(); back()

            // A long log shows its start, then the whole file on request.
            open("logs", client); open("big.log", client)
            waitFor("log start", client) { tagged("truncatedBar") }
            assertTrue(shows("Showing the first 256 KB of 469 KB."))
            compose.onNodeWithText("Show all").performClick()
            waitFor("whole log", client, 40000) { !tagged("truncatedBar") && tagged("codeView") }
            compose.onNodeWithContentDescription("Find in file").performClick()
            compose.onNodeWithTag("findInFile").performTextInput("019999 fixture")
            waitFor("last line", client) { shows("1 of 1") }
            screenshot("log-whole")
            back(); back()

            // Documents: PDF pages, HTML and SVG drawn without scripts or network, Office files to another app.
            open("docs", client); open("guide.pdf", client)
            waitFor("pdf", client, 30000) { tagged("pdfView") && compose.onAllNodesWithContentDescription("Page 1 of 1").fetchSemanticsNodes().isNotEmpty() }
            screenshot("pdf")
            back()
            open("page.html", client)
            waitFor("html", client) { tagged("webPreview") }
            // The page is shown, its script never ran, and it can't reach files or the network.
            webShows("Fixture page")
            assertEquals(0, webMatches("script ran"))
            val web = webView()!!
            var locked = listOf<Boolean>()
            compose.runOnUiThread { web.settings.let { locked = listOf(!it.javaScriptEnabled, it.blockNetworkLoads, !it.allowFileAccess, !it.allowContentAccess) } }
            assertEquals(listOf(true, true, true, true), locked)
            pause(2000)
            screenshot("html")
            compose.onNodeWithContentDescription("Show source").performClick()
            waitFor("html source", client) { shows("document.body.innerHTML") }
            back()
            open("logo.svg", client)
            waitFor("svg", client) { tagged("webPreview") }
            pause(3000)
            screenshot("svg")
            // The circle is drawn: the picture's purple shows in the preview.
            val shot = InstrumentationRegistry.getInstrumentation().uiAutomation.takeScreenshot()
            val purple = (0 until shot.height step 8).sumOf { y -> (0 until shot.width step 8).count { x ->
                shot.getPixel(x, y).let { android.graphics.Color.blue(it) > 200 && android.graphics.Color.green(it) < 90 && android.graphics.Color.red(it) in 90..160 } } }
            assertTrue("SVG not drawn ($purple purple samples)", purple > 50)
            back()
            open("deck.docx", client)
            waitFor("docx", client, 30000) { tagged("fileOther") }
            assertTrue(shows("The phone can't show .docx files itself"))
            screenshot("docx")
            back(); back()

            // Pictures, an animated GIF, video and sound download and show in the app.
            open("media", client); open("picture.png", client)
            waitFor("picture", client, 30000) { compose.onAllNodesWithContentDescription("picture.png").fetchSemanticsNodes().isNotEmpty() }
            screenshot("picture")
            back()
            open("moving.gif", client)
            waitFor("gif", client, 30000) { compose.onAllNodesWithContentDescription("moving.gif").fetchSemanticsNodes().isNotEmpty() }
            back()
            open("clip.mp4", client)
            waitFor("video plays", client, 30000) { compose.onAllNodesWithContentDescription("Pause").fetchSemanticsNodes().isNotEmpty() }
            screenshot("video")
            compose.onNodeWithContentDescription("Close").performClick()
            waitFor("back to media", client) { tagged("explorerEntries") }
            open("tone.wav", client)
            waitFor("sound plays", client, 30000) { compose.onAllNodesWithContentDescription("Pause").fetchSemanticsNodes().isNotEmpty() }
            screenshot("audio")
            compose.onNodeWithContentDescription("Close").performClick()
            waitFor("back to media again", client) { tagged("explorerEntries") }
            // Over the phone's 10 MB limit: said up front, nothing is asked of the desktop.
            compose.onNodeWithTag("explorerEntries").performScrollToNode(hasTestTag("entry:huge.mp4"))
            compose.onNodeWithText("12.0 MB · too large for the phone").assertExists()
            open("huge.mp4", client)
            assertTrue(shows("larger than the 10 MB a phone can download"))
            screenshot("too-large")
            back(); back()

            // The .env file and the link never leave the computer.
            open(".env", client)
            assertTrue(shows("may hold passwords, keys or sign-ins"))
            back()
            open("outside", client)
            assertTrue(shows("doesn't follow it"))
            back()

            // Offline: the last listing stays with a note, and a picture opened before still opens.
            open("media", client)
            compose.runOnUiThread { client.disconnect() }
            waitFor("offline", client) { !client.connected }
            assertTrue(shows("Offline. Showing what your computer last sent."))
            open("picture.png", client)
            waitFor("cached picture", client, 15000) { compose.onAllNodesWithContentDescription("picture.png").fetchSemanticsNodes().isNotEmpty() }
            back()
            open("tone.wav", client)
            screenshot("offline")
            // The sound was played before, so it plays offline too.
            waitFor("cached sound", client, 15000) { compose.onAllNodesWithContentDescription("Pause").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithContentDescription("Close").performClick()
        } finally {
            compose.runOnUiThread { owners.clear() }
            app.getSharedPreferences("navigation", Context.MODE_PRIVATE).edit().clear().commit()
            DeviceStore(app).reset()
        }
    }
}
