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
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import java.io.File

/**
 * Opening a task's work thread against `live-device-fixture.py --workspace --remote --desktop --work-thread`.
 * The fixture's thread is mostly tool and thinking records, like a real worker's, and its first read is slow.
 * Another run points at a thread the desktop no longer has. No provider or task runs.
 * Pass `screenshotSuffix` to keep screenshots from several screen sizes apart.
 */
class LiveWorkThreadTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()

    private fun waitFor(what: String, client: LiveClient, timeout: Long = 20000, condition: () -> Boolean) {
        try { compose.waitUntil(timeout, condition) }
        catch (failure: ComposeTimeoutException) {
            throw AssertionError("$what: errors=${client.desktopErrors}; busy=${client.desktopBusy}; request=${client.requestError}; status=${client.status}", failure)
        }
    }

    private fun screenshot(name: String) {
        compose.waitForIdle()
        val suffix = InstrumentationRegistry.getArguments().getString("screenshotSuffix").orEmpty()
        InstrumentationRegistry.getInstrumentation().uiAutomation.executeShellCommand("screencap -p /data/local/tmp/raticode-$name$suffix.png").use {
            android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
        }
    }

    @Test fun workThreadsFromTasksFitThePhone() {
        val path = InstrumentationRegistry.getArguments().getString("workThreadInvitationFile")
        assumeTrue("Requires disposable fixture with --workspace --remote --desktop --work-thread", path != null)
        compose.runOnUiThread { compose.activity.requestedOrientation = android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT }
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        DeviceStore(app).reset()
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app)
            owners.put("work-thread", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client, startPage = "Threads") } }
            client.preview(File(path!!).readText()); client.pair()
        }
        try {
            waitFor("desktop control", client, 30000) { client.connected && client.desktopControl }
            compose.waitUntil(5000) { compose.onAllNodesWithContentDescription("Rem").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithContentDescription("Rem").performClick()
            waitFor("Rem home", client) { client.desktopViews["rem.home"] is RemHome }
            compose.onNodeWithTag("remStatusHeader").performClick()
            compose.onNodeWithText("Tidy the phone thread view").performClick()
            waitFor("task detail", client) { client.desktopViews["rem.task:tidy-task"] is RemTask }

            // The running run's thread: loading first, then the grouped transcript.
            compose.onAllNodesWithText("Open work thread").onFirst().performScrollTo().performClick()
            compose.onNodeWithText("Loading this thread…").assertIsDisplayed()
            screenshot("work-thread-loading")
            waitFor("work thread", client) { client.desktopViews["rem.thread:long-thread"] is RemThread }
            compose.onNodeWithText("Working on your desktop").assertIsDisplayed()
            // It opens at the newest entry: the running group, open, with its live step.
            compose.onNodeWithText("Build the app", substring = true).assertIsDisplayed()
            compose.onNodeWithText("Hide thoughts").assertExists()
            screenshot("work-thread-running")

            // The finished round: its thoughts folded under one row, the report in full.
            compose.onNodeWithTag("remThread").performScrollToKey("final-1")
            compose.onNodeWithText("Work threads fit the phone").assertIsDisplayed()
            screenshot("work-thread-report")
            compose.onNodeWithTag("remThread").performScrollToKey("r1-tool-0-a")
            compose.onNodeWithText("Show thoughts").assertIsDisplayed().performClick()
            compose.onNodeWithText("Showing the last 60 of 141 steps", substring = true).assertIsDisplayed()
            compose.onNodeWithText("Run the unit tests", substring = true).performScrollTo().assertIsDisplayed()
            screenshot("work-thread-thoughts")
            compose.onAllNodesWithText("Hide thoughts").onFirst().performScrollTo().performClick()

            // The long instruction starts folded and opens on request.
            compose.onNodeWithTag("remThread").performScrollToKey("ask-1")
            compose.onNodeWithTag("remInstructionToggle").assertIsDisplayed().assertTextContains("Show more").performClick()
            compose.onNodeWithText("Background note 13", substring = true).performScrollTo().assertIsDisplayed()
            screenshot("work-thread-instruction")
            compose.onNodeWithContentDescription("Back").performClick()

            // A run whose thread the desktop removed: a clear message and a way back.
            compose.onAllNodesWithText("Open work thread")[1].performScrollTo().performClick()
            waitFor("unavailable thread", client) { client.desktopErrors.containsKey("rem.thread:missing-thread") }
            compose.onNodeWithText("This work thread isn't available.").assertIsDisplayed()
            compose.onNodeWithTag("remInstruction").assertDoesNotExist()
            screenshot("work-thread-unavailable")
            compose.onNodeWithText("Back").performClick()
            compose.onNodeWithText("Run now", substring = false).assertDoesNotExist()
            compose.onNodeWithText("Stop run").assertIsDisplayed()
        } finally {
            compose.runOnUiThread { owners.clear() }
            DeviceStore(app).reset()
        }
    }
}
