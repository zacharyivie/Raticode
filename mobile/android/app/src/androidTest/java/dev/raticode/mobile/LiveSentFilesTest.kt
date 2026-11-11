package dev.raticode.mobile

import android.app.Application
import androidx.activity.compose.setContent
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.lifecycle.ViewModelStore
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.data.OpenedFiles
import dev.raticode.mobile.live.*
import dev.raticode.mobile.ui.LiveScreen
import dev.raticode.mobile.ui.RaticodeTheme
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import java.io.File

/**
 * Files on Rem's replies against `live-device-fixture.py --workspace --remote --desktop --sent-files`. The fixture's
 * Rem replies with a PDF and a `.tar`, offered through the desktop's real reply-file path to the phone that asked.
 * Tapping a file's name downloads it and opens it with no Download button and no further question in Raticode.
 */
class LiveSentFilesTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()

    private fun waitFor(what: String, client: LiveClient, timeout: Long = 30000, condition: () -> Boolean) {
        try { compose.waitUntil(timeout, condition) }
        catch (failure: ComposeTimeoutException) { throw AssertionError("$what: files=${client.files}; status=${client.status}", failure) }
    }

    private fun foreground(): String? = instrumentation.uiAutomation.rootInActiveWindow?.packageName?.toString()

    /** The default app for [type], asked through the shell because package visibility hides it from this app. */
    private fun defaultViewer(type: String): String? = instrumentation.uiAutomation
        .executeShellCommand("cmd package resolve-activity --brief -a android.intent.action.VIEW -t $type -d content://x/y")
        .let { android.os.ParcelFileDescriptor.AutoCloseInputStream(it).bufferedReader().readLines() }.lastOrNull { "/" in it }?.substringBefore('/')

    private fun screenshot(name: String) {
        Thread.sleep(1500)
        instrumentation.uiAutomation.executeShellCommand("screencap -p /data/local/tmp/raticode-$name").use {
            android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
        }
    }

    /** Waits for [expected] to come to the front, then goes back to Raticode. waitUntil also runs the phone's auto-open. */
    private fun openedIn(expected: String, screenshot: String? = null) {
        val own = instrumentation.targetContext.packageName
        try { compose.waitUntil(30000) { foreground() == expected } } catch (_: ComposeTimeoutException) {}
        assertEquals("The file should open in its viewer without another tap", expected, foreground())
        screenshot?.let(::screenshot)
        repeat(5) {
            if (foreground() != own) {
                instrumentation.uiAutomation.performGlobalAction(android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_BACK)
                Thread.sleep(800)
            }
        }
        compose.waitUntil(10000) { foreground() == own }
    }

    /** Scrolls the chat to the file's row first; follow-up suggestions under the reply can push it below the fold. */
    private fun tapName(name: String) {
        compose.onNodeWithTag("remMessages").performScrollToNode(hasText(name))
        compose.onNode(hasText(name) and hasClickAction()).performClick()
    }

    @Test fun tappingASentFilesNameDownloadsAndOpensIt() {
        val path = InstrumentationRegistry.getArguments().getString("sentFilesInvitationFile")
        assumeTrue("Requires a disposable fixture with --workspace --remote --desktop --sent-files", path != null)
        val pdfViewer = defaultViewer("application/pdf")
        assumeTrue("This device has no default PDF viewer", pdfViewer != null)
        compose.runOnUiThread { compose.activity.requestedOrientation = android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT }
        val app = instrumentation.targetContext.applicationContext as Application
        DeviceStore(app).reset()
        OpenedFiles.cleanup(app.cacheDir, keepMillis = 0)
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app)
            owners.put("sent", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client, startPage = "Threads") } }
            client.preview(File(path!!).readText()); client.pair()
        }
        try {
            waitFor("desktop control", client) { client.connected && client.desktopControl }
            compose.waitUntil(5000) { compose.onAllNodesWithContentDescription("Rem").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithContentDescription("Rem").performClick()
            compose.onNodeWithTag("remComposer").performTextInput("Send me the notes")
            compose.onNodeWithContentDescription("Send").performClick()
            compose.runOnUiThread {
                (compose.activity.getSystemService(android.content.Context.INPUT_METHOD_SERVICE) as android.view.inputmethod.InputMethodManager)
                    .hideSoftInputFromWindow(compose.activity.window.decorView.windowToken, 0)
            }
            waitFor("offers", client) { client.files.count { it.state == "offered" } == 2 }
            waitFor("reply files", client) { (client.desktopViews["rem.home"] as? RemHome)?.messages?.any { it.files.size == 2 } == true }
            compose.onNodeWithTag("remMessages").performScrollToNode(hasText("Fixture data.tar"))
            // Files that aren't pictures or videos stay on the desktop until asked for.
            assertTrue(client.files.none { it.state == "available" })
            compose.onNodeWithTag("remMessages").performScrollToNode(hasText("Fixture data.tar"))
            screenshot("sent-file-rows.png")

            // One tap on the name: it downloads, then opens in the phone's PDF viewer.
            tapName("Fixture notes.pdf")
            openedIn(pdfViewer!!, "sent-file-opened.png")
            val notes = client.files.single { it.name == "Fixture notes.pdf" }
            assertEquals("available", notes.state)
            // Tapping it again opens the same download.
            tapName("Fixture notes.pdf")
            openedIn(pdfViewer)
            assertEquals(1, client.files.count { it.name == "Fixture notes.pdf" })

            // Nothing on the phone opens a .tar: it still downloads, then says so and offers Save instead.
            tapName("Fixture data.tar")
            waitFor("no viewer", client) { compose.onAllNodesWithText("Can't open Fixture data.tar").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithText("Save instead").assertIsDisplayed()
            assertEquals("available", client.files.single { it.name == "Fixture data.tar" }.state)
            screenshot("sent-file-no-viewer.png")
            compose.onNodeWithText("Close").performClick()
            // Save and Open stay beside each file.
            compose.onNodeWithContentDescription("Save Fixture notes.pdf").assertExists()
            compose.onNodeWithContentDescription("Open Fixture notes.pdf").assertExists()
        } finally {
            compose.runOnUiThread { owners.clear() }
        }
    }
}
