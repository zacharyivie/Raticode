package dev.raticode.mobile

import android.app.Application
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
 * Rem's rich results against `live-device-fixture.py --workspace --remote --desktop --rich`, which runs the real desktop
 * device code with an in-memory Rem: a finished task's result card, a running task's progress, Rem's reply as it is written,
 * and a source preview fetched by the desktop through `media.preview` (the fixture's previews are made up; no site is
 * contacted). Pass `richExpected=false` with a desktop that has no rich results: the phone must then show the plain
 * message and send no `media.preview` read. No provider or task runs.
 */
class LiveRichResultsTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()

    private fun waitFor(what: String, client: LiveClient, timeout: Long = 20000, condition: () -> Boolean) {
        try { compose.waitUntil(timeout, condition) }
        catch (failure: ComposeTimeoutException) {
            throw AssertionError("$what: errors=${client.desktopErrors}; previews=${client.previews}; link=${client.linkFetch}; status=${client.status}", failure)
        }
    }

    /** Holds back the app a downloaded report opens in, and keeps the request, so the test stays on Raticode's screen. */
    private class ViewerMonitor : android.app.Instrumentation.ActivityMonitor() {
        val opened = java.util.concurrent.atomic.AtomicReference<android.content.Intent?>()
        override fun onStartActivity(intent: android.content.Intent): android.app.Instrumentation.ActivityResult? {
            if (intent.action != android.content.Intent.ACTION_VIEW) return null
            opened.compareAndSet(null, intent)
            return android.app.Instrumentation.ActivityResult(android.app.Activity.RESULT_CANCELED, null)
        }
    }

    private fun screenshot(name: String) {
        compose.waitForIdle()
        InstrumentationRegistry.getInstrumentation().uiAutomation.executeShellCommand("screencap -p /data/local/tmp/raticode-$name").use {
            android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
        }
    }

    @Test fun resultCardsProgressAndPreviewsComeFromTheDesktop() {
        val args = InstrumentationRegistry.getArguments()
        val path = args.getString("richInvitationFile")
        assumeTrue("Requires disposable fixture with --workspace --remote --desktop --rich", path != null)
        val rich = args.getString("richExpected") != "false"
        compose.runOnUiThread { compose.activity.requestedOrientation = android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT }
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        DeviceStore(app).reset()
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app)
            owners.put("rich", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client, startPage = "Threads") } }
            client.preview(File(path!!).readText()); client.pair()
        }
        try {
            waitFor("desktop control", client, 30000) { client.connected && client.desktopControl }
            compose.waitUntil(5000) { compose.onAllNodesWithContentDescription("Rem").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithContentDescription("Rem").performClick()
            waitFor("Rem home", client) { client.desktopViews["rem.home"] is RemHome }
            val home = client.desktopViews["rem.home"] as RemHome
            assertEquals(rich, home.richResults)
            val message = home.messages.first { it.id == "rich-result" }
            if (!rich) {
                // An older desktop: the whole answer and its report link are still there, and no preview is asked for.
                assertNull(message.result)
                compose.onNodeWithTag("remMessages").performScrollToNode(hasText("Vector search"))
                compose.onNodeWithText("Vector search").assertIsDisplayed()
                assertTrue(client.previews.isEmpty())
                // Nor any task chips, Retry review action or draft reads, which it wouldn't understand.
                compose.onAllNodesWithTag("remResultActions").assertCountEquals(0)
                assertFalse(client.reviewRetry)
                assertEquals("", client.remDraft)
                return
            }
            assertEquals("Reading earlier messages", home.turnLabel)
            compose.onNodeWithTag("remMessages").performScrollToNode(hasText("✓ 3 of 3 checks passed"))
            compose.onNodeWithText("✓ 3 of 3 checks passed").assertIsDisplayed()
            compose.onNodeWithText("Vector search for Second Brain").assertIsDisplayed()
            // The desktop fetched the page preview; the phone never contacted the site.
            waitFor("source preview", client) { client.previews["https://example.com/fixture-guide"] is ChatPreview.Ready }
            compose.onNodeWithText("Fixture guide page").assertExists()
            // The running task shows its step and checks in words.
            compose.onNodeWithTag("remMessages").performScrollToNode(hasContentDescription("Check in progress"))
            compose.onNodeWithText("Checking the work").assertExists()
            compose.onNodeWithText("1 of 3 checks passed so far", substring = true).assertExists()
            compose.onNodeWithTag("remMessages").performScrollToNode(hasText("Reading earlier messages…"))
            screenshot("rich-live.png")

            // Open on the result card asks the desktop for the report exactly like tapping its link. The small report
            // downloads at once, its sheet closes by itself, and it opens in a viewer. The test holds that viewer back,
            // so Raticode stays on screen, instead of racing to close the sheet first.
            compose.onNodeWithTag("remMessages").performScrollToNode(hasText("Vector search for Second Brain"))
            val viewer = ViewerMonitor()
            val instrumentation = InstrumentationRegistry.getInstrumentation()
            instrumentation.addMonitor(viewer)
            try {
                compose.onNodeWithContentDescription("Open Vector search for Second Brain").performClick()
                waitFor("report offered", client) { client.linkFetch?.fileId != null }
                assertEquals("Vector search.html", client.linkFetch!!.name)
                waitFor("report opened", client) { viewer.opened.get() != null }
                assertEquals("text/html", viewer.opened.get()!!.type)
                waitFor("sheet closed", client) { client.linkFetch == null }
            } finally { instrumentation.removeMonitor(viewer) }

            // Rem's reply grows through small rem.draft deltas, past the whole draft rem.home sent.
            waitFor("draft deltas", client) { client.remDraft.split(' ').size >= 10 }
            assertTrue(client.remDraft.startsWith("Here is what I found"))

            // The finished task's chips: Retry review runs the desktop's retry and then leaves, as the review runs again.
            compose.onNodeWithTag("remMessages").performScrollToNode(hasText("Retry review"))
            compose.onNodeWithText("Run again").assertIsDisplayed()
            screenshot("rich-actions-live.png")
            compose.onNodeWithText("Retry review").performClick()
            waitFor("review retried", client) { (client.desktopViews["rem.home"] as? RemHome)?.tasks?.firstOrNull { it.id == "rich-task" }?.activeRun != null }
            compose.waitUntil(5000) { compose.onAllNodesWithText("Retry review").fetchSemanticsNodes().isEmpty() }
            compose.onAllNodesWithText("Run again").assertCountEquals(0)
        } finally {
            compose.runOnUiThread { owners.clear() }
            DeviceStore(app).reset()
        }
    }
}
