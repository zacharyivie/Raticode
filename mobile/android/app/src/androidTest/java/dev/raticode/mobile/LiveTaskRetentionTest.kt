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
 * Rem's task order and finished one-time tasks against `live-device-fixture.py --workspace --remote --desktop --retention`.
 * The fixture's in-memory Rem has two recurring tasks that ran, a one-time task that finished, and a chat link to a task
 * the desktop already removed. Pass `retentionExpected=false` for a desktop without task retention, which sends every task
 * and no availability flags. No provider or task runs.
 */
class LiveTaskRetentionTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()

    private fun waitFor(what: String, client: LiveClient, timeout: Long = 20000, condition: () -> Boolean) {
        try { compose.waitUntil(timeout, condition) }
        catch (failure: ComposeTimeoutException) {
            throw AssertionError("$what: errors=${client.desktopErrors}; busy=${client.desktopBusy}; request=${client.requestError}; status=${client.status}", failure)
        }
    }

    private fun screenshot(name: String) {
        compose.waitForIdle()
        InstrumentationRegistry.getInstrumentation().uiAutomation.executeShellCommand("screencap -p /data/local/tmp/raticode-$name").use {
            android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
        }
    }

    private fun top(text: String) = compose.onNodeWithText(text).fetchSemanticsNode().boundsInRoot.top

    @Test fun newestRunFirstAndFinishedOneTimeTasksStayLinked() {
        val args = InstrumentationRegistry.getArguments()
        val path = args.getString("retentionInvitationFile")
        assumeTrue("Requires disposable fixture with --workspace --remote --desktop --retention", path != null)
        val retention = args.getString("retentionExpected") != "false"
        compose.runOnUiThread { compose.activity.requestedOrientation = android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT }
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        DeviceStore(app).reset()
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app)
            owners.put("retention", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client, startPage = "Threads") } }
            client.preview(File(path!!).readText()); client.pair()
        }
        try {
            waitFor("desktop control", client, 30000) { client.connected && client.desktopControl }
            compose.waitUntil(5000) { compose.onAllNodesWithContentDescription("Rem").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithContentDescription("Rem").performClick()
            waitFor("Rem home", client) { client.desktopViews["rem.home"] is RemHome }
            val home = client.desktopViews["rem.home"] as RemHome
            // A desktop with retention leaves the finished one-time task out; an older one sends it and the phone hides it.
            assertEquals(!retention, home.tasks.any { it.id == "once-done" })

            // The chat keeps both links for the finished one-time task.
            compose.onNodeWithText("Done: I archived the logs.").performScrollTo()
            compose.onNodeWithText("Rename the release branch").assertIsDisplayed()
            // The card names the task without a note about the task list.
            compose.onAllNodesWithText("in the task list", substring = true, ignoreCase = true).assertCountEquals(0)
            if (retention) {
                // The desktop says the other task and its thread are gone: no task link and no thread button.
                compose.onNodeWithText("Removed from your desktop").assertIsDisplayed()
                compose.onAllNodesWithText("Open work thread").assertCountEquals(1)
            } else {
                compose.onAllNodesWithText("Open work thread").assertCountEquals(2)
            }
            screenshot("retention-chat.png")
            compose.onAllNodesWithText("Open work thread").onFirst().performClick()
            waitFor("work thread", client) { client.desktopViews["rem.thread:once-thread"] is RemThread }
            compose.onNodeWithText("Renamed release to release-2026.").assertIsDisplayed()
            compose.onNodeWithContentDescription("Back").performClick()

            compose.onNodeWithText("Rename the release branch").performScrollTo().performClick()
            waitFor("finished task", client) { client.desktopViews["rem.task:once-done"] is RemTask }
            compose.onNodeWithText("This one-time task finished", substring = true).performScrollTo().assertIsDisplayed()
            if (retention) compose.onNodeWithText("Your desktop removes it on", substring = true).assertIsDisplayed()
            screenshot("retention-task.png")
            compose.onNodeWithContentDescription("Back").performClick()

            // The task list in the desktop's groups: Coming up, then Done (folded until opened) with the newest run first,
            // and the finished one-time task left out. Rem's status opens it.
            compose.onNodeWithTag("remStatusHeader").performClick()
            compose.onNodeWithText("3 tasks", substring = true).assertIsDisplayed()
            compose.onNodeWithTag("remTasks").assertIsDisplayed()
            compose.onNodeWithText("Rename the release branch").assertDoesNotExist()
            compose.onNodeWithText("Check dependency updates").assertDoesNotExist()
            compose.onNodeWithText("DONE").performClick()
            val order = listOf("Check failed workflow runs", "Check dependency updates", "Summarize yesterday's commits")
            assertEquals(order, order.sortedBy(::top))
            screenshot("retention-tasks.png")
        } finally {
            compose.runOnUiThread { owners.clear() }
            DeviceStore(app).reset()
        }
    }
}
