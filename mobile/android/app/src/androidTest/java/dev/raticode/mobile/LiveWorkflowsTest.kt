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
 * Workflows against `live-device-fixture.py --workspace --remote --desktop --workflows`, which uses the
 * desktop's real workflow projection and run service with disposable workflows whose steps only wait.
 * With `-e workflowsExpected false` and a desktop without workflows, the phone must say to update it
 * and never send a workflow request.
 */
class LiveWorkflowsTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()

    private fun screenshot(name: String) {
        compose.waitForIdle()
        InstrumentationRegistry.getInstrumentation().uiAutomation.executeShellCommand("screencap -p /data/local/tmp/raticode-$name").use {
            android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
        }
    }

    private fun waitFor(what: String, client: LiveClient, timeout: Long = 20000, condition: () -> Boolean) {
        try { compose.waitUntil(timeout, condition) }
        catch (failure: ComposeTimeoutException) {
            throw AssertionError("$what: errors=${client.desktopErrors}; busy=${client.desktopBusy}; request=${client.requestError}; status=${client.status}", failure)
        }
    }

    @Test fun viewRunAndStopAWorkflow() {
        val args = InstrumentationRegistry.getArguments()
        val path = args.getString("workflowsInvitationFile")
        assumeTrue("Requires disposable fixture with --workspace --remote --desktop --workflows", path != null)
        val expected = args.getString("workflowsExpected") != "false"
        compose.runOnUiThread { compose.activity.requestedOrientation = android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT }
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        DeviceStore(app).reset()
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app)
            owners.put("desktop", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client, startPage = "Threads") } }
            client.preview(File(path!!).readText()); client.pair()
        }
        try {
            waitFor("desktop control", client, 30000) { client.connected && client.desktopControl }
            waitFor("desktop features", client) { client.workflows != null }
            compose.onNodeWithContentDescription("More").performClick()
            compose.onNodeWithText("Workflows").performClick()
            if (!expected) {
                assertEquals(false, client.workflows)
                compose.waitUntil(5000) { compose.onAllNodesWithText(Workflows.UPDATE_DESKTOP).fetchSemanticsNodes().isNotEmpty() }
                compose.onNodeWithText("Check again").assertIsDisplayed()
                screenshot("workflows-older-desktop.png")
                // Sync keeps working and no workflow request was ever queued.
                waitFor("sync", client) { client.connected }
                assertTrue(client.desktopViews.keys.none { it.startsWith("workflows.") })
                return
            }
            waitFor("workflow list", client) { (client.desktopViews[Workflows.LIST] as? WorkflowList)?.items?.size == 2 }
            compose.onNodeWithText("Fixture report").assertIsDisplayed()
            compose.onAllNodesWithText("Not run yet", useUnmergedTree = true).assertCountEquals(2)
            screenshot("workflows-list.png")

            compose.onNodeWithText("Fixture report").performClick()
            val key = { (client.desktopViews[Workflows.LIST] as WorkflowList).items.first { it.name == "Fixture report" }.id.let { "workflows.read:$it" } }
            waitFor("workflow view", client) { client.desktopViews[key()] is Workflow }
            compose.onNodeWithTag("workflowNode:summarize").assertExists()
            compose.onNodeWithTag("workflowNode:publish").assertExists()
            compose.onNodeWithText("No runs yet").assertIsDisplayed()

            compose.onNodeWithTag("workflowRun").performClick()
            compose.onNode(hasText("Run") and hasAnyAncestor(isDialog())).performClick()
            waitFor("run started", client) { (client.desktopViews[key()] as? Workflow)?.activeRun != null }
            waitFor("step running", client) { (client.desktopViews[key()] as? Workflow)?.run?.nodes?.any { it.status == "running" } == true }
            compose.onNodeWithTag("workflowStop").assertIsDisplayed().assertIsEnabled()
            screenshot("workflows-running.png")

            compose.onNodeWithTag("workflowStop").performClick()
            compose.onNodeWithText("Stop run").performClick()
            waitFor("run stopped", client, 30000) { (client.desktopViews[key()] as? Workflow)?.let { it.activeRun == null && it.runs.firstOrNull()?.status == "stopped" } == true }
            compose.onNodeWithTag("workflowRun").assertIsDisplayed()
            // Rattish runs can't be resumed; the desktop says so and the phone shows its reason.
            compose.onNodeWithTag("workflowResume").assertIsNotEnabled()
            compose.onNodeWithText("Resume: Resuming Rattish workflow runs is not supported yet. Start a new run instead.").assertIsDisplayed()
            compose.onNodeWithText("Runs (1)").performClick()
            compose.onNodeWithTag("workflowRuns").assertIsDisplayed()
            screenshot("workflows-stopped.png")
            assertNull(client.requestError)
        } finally {
            compose.runOnUiThread { owners.clear() }
            DeviceStore(app).reset()
        }
    }
}
