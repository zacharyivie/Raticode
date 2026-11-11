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
 * A screenshot tour of the screens the desktop-parity audit compares, against
 * `live-device-fixture.py --workspace --remote --desktop --ui-tour`: Rem's tasks in every state, a running task, a long
 * worker thread with a report and a review, a task that needs you, and a swarm. No provider or task runs.
 * Screenshots go to /data/local/tmp/raticode-tour-*.png. With `tourExpectParity=true` it also checks the desktop-style
 * grouping, status and folding; without it, it only walks the screens, so the same tour can photograph an older build.
 */
class LiveUiTourTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()

    private val args get() = InstrumentationRegistry.getArguments()

    private fun waitFor(what: String, client: LiveClient, timeout: Long = 20000, condition: () -> Boolean) {
        try { compose.waitUntil(timeout, condition) }
        catch (failure: ComposeTimeoutException) {
            throw AssertionError("$what: errors=${client.desktopErrors}; busy=${client.desktopBusy}; request=${client.requestError}; status=${client.status}", failure)
        }
    }

    private fun screenshot(name: String) {
        compose.waitForIdle()
        Thread.sleep(400)
        val suffix = args.getString("screenshotSuffix").orEmpty()
        InstrumentationRegistry.getInstrumentation().uiAutomation.executeShellCommand("screencap -p /data/local/tmp/raticode-tour-$name$suffix.png").use {
            android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
        }
    }

    @Test fun tourOfTasksThreadsAndSwarms() {
        val path = args.getString("tourInvitationFile")
        assumeTrue("Requires disposable fixture with --workspace --remote --desktop --ui-tour", path != null)
        val parity = args.getString("tourExpectParity") == "true"
        compose.runOnUiThread { compose.activity.requestedOrientation = android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT }
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        DeviceStore(app).reset()
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app)
            owners.put("ui-tour", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client, startPage = "Threads") } }
            client.preview(File(path!!).readText()); client.pair()
        }
        try {
            waitFor("desktop control", client, 30000) { client.connected && client.desktopControl }
            compose.waitUntil(5000) { compose.onAllNodesWithContentDescription("Rem").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithContentDescription("Rem").performClick()
            waitFor("Rem home", client) { (client.desktopViews["rem.home"] as? RemHome)?.tasks?.any { it.id == "tour-audit" } == true }

            // Rem's status at the top opens the task list: which tasks work, wait, need you, stopped or finished.
            screenshot("rem-header")
            compose.onNodeWithTag("remStatusHeader").performClick()
            compose.onNodeWithText("Audit the phone UI against the desktop").assertIsDisplayed()
            if (parity) {
                compose.onNodeWithText("WORKING ON").assertIsDisplayed()
                compose.onNodeWithText("NEEDS YOU").assertIsDisplayed()
                compose.onNodeWithText("Couldn't finish", substring = true).assertDoesNotExist() // A failure with a summary shows the summary.
                compose.onNodeWithText("Gradle ran out of disk space during the build.").assertIsDisplayed()
                compose.onNodeWithText("Running the unit tests").assertIsDisplayed()
                // Done starts folded, as on the desktop.
                compose.onNodeWithText("Compare vector search options").assertDoesNotExist()
            }
            screenshot("tasks")
            compose.onNodeWithTag("remTasks").performTouchInput { swipeUp() }
            if (parity) {
                compose.onNodeWithTag("remTasks").performScrollToKey("group-done")
                compose.onNodeWithText("DONE").performClick()
                compose.onNodeWithTag("remTasks").performScrollToNode(hasText("Compare vector search options"))
                compose.onNodeWithText("Compare vector search options").assertIsDisplayed()
                compose.onNodeWithTag("remTasks").performScrollToNode(hasText("Stopped"))
                compose.onNodeWithText("Stopped").assertIsDisplayed()
            }
            screenshot("tasks-lower")

            // A running task: its status at the top, then runs and details.
            compose.onNodeWithTag("remTasks").performScrollToIndex(0)
            compose.onNodeWithText("Audit the phone UI against the desktop").performClick()
            waitFor("task detail", client) { client.desktopViews["rem.task:tour-audit"] is RemTask }
            if (parity) compose.onNodeWithTag("remTaskStatus").assertIsDisplayed()
            screenshot("task-running")

            // Its work thread opens at the newest reply.
            compose.onAllNodesWithText("Open work thread").onFirst().performScrollTo().performClick()
            waitFor("work thread", client) { client.desktopViews["rem.thread:tour-thread"] is RemThread }
            compose.waitForIdle()
            if (parity) compose.onNodeWithTag("remThreadWorking").assertIsDisplayed()
            screenshot("thread-newest")
            compose.onNodeWithTag("remThread").performScrollToKey("tour-review")
            screenshot("thread-review")
            compose.onNodeWithTag("remThread").performScrollToKey("tour-report")
            if (parity) {
                // An earlier long reply folds to a preview with Show more.
                compose.onAllNodesWithTag("remFoldToggle").onFirst().assertExists()
            }
            screenshot("thread-report")
            compose.onNodeWithTag("remThread").performScrollToKey("tour-ask")
            screenshot("thread-brief")
            compose.onNodeWithContentDescription("Back").performClick()
            compose.onNodeWithContentDescription("Back").performClick()

            // A task waiting on you.
            compose.onNodeWithTag("remTasks").performScrollToNode(hasText("Rename the release branch"))
            compose.onNodeWithText("Rename the release branch").performClick()
            waitFor("blocked task", client) { client.desktopViews["rem.task:tour-branch"] is RemTask }
            screenshot("task-blocked")
            compose.onNodeWithContentDescription("Back").performClick()

            // A swarm with a long board post.
            compose.onNodeWithContentDescription("Swarms").performClick()
            waitFor("swarms", client) { client.swarms.isNotEmpty() }
            compose.onNodeWithText("Fixture swarm").performClick()
            waitFor("swarm board", client) { client.swarms.firstOrNull()?.messages?.size == 2 }
            compose.waitForIdle()
            if (parity) compose.onNodeWithTag("swarmStatus").assertIsDisplayed()
            screenshot("swarm")
        } finally {
            compose.runOnUiThread { owners.clear() }
            DeviceStore(app).reset()
        }
    }
}
