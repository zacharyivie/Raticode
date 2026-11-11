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

/** Resource toggles, approvals and the swarm console against `live-device-fixture.py --workspace --remote`.
 * The fixture's swarm and approval services are in-memory; no workflow, agent or provider runs. */
class LiveRemoteTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()

    private fun screenshot(app: Application, name: String) {
        compose.waitForIdle()
        // Outside the app's data, so the image survives the test runner uninstalling the app.
        InstrumentationRegistry.getInstrumentation().uiAutomation.executeShellCommand("screencap -p /data/local/tmp/raticode-$name").use {
            android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
        }
    }

    @Test fun resourcesApprovalsAndSwarmConsole() {
        val path = InstrumentationRegistry.getArguments().getString("remoteInvitationFile")
        assumeTrue("Requires disposable fixture with --workspace --remote", path != null)
        compose.runOnUiThread { compose.activity.requestedOrientation = android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT }
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        DeviceStore(app).reset()
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app)
            owners.put("remote", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client, startPage = "Threads") } }
            client.preview(File(path!!).readText()); client.pair()
        }
        try {
            compose.waitUntil(30000) { client.connected && client.threads.isNotEmpty() && client.remoteControl }
            val thread = client.threads.single().first

            // Resources: the open thread lists its desktop tools; turn Web off.
            compose.onNode(hasText("Desktop project chat", substring = false) and hasAnyAncestor(hasTestTag("threadList"))).performClick()
            compose.waitUntil(15000) { client.threadResources[thread].orEmpty().any { it.name == "Web" && it.enabled } }
            compose.onAllNodesWithContentDescription("Thread settings").onFirst().performClick()
            compose.onNodeWithText("Resources").performScrollTo().assertIsDisplayed()
            compose.onNodeWithText("Web", substring = false).performScrollTo().performClick()
            compose.onNodeWithText("Apply", substring = false).performClick()
            compose.waitUntil(15000) { client.threadResources[thread].orEmpty().let { r -> r.any { it.name == "Web" && !it.enabled } && r.any { it.name == "Commands" && it.enabled } } }
            assertEquals(thread, client.threads.single().first)

            // Approvals: one workflow gate and one swarm request.
            compose.onNodeWithContentDescription("Threads").performClick()
            compose.onNodeWithContentDescription("Thread list options").performClick()
            compose.onNodeWithText("Approvals", substring = true).performClick()
            compose.waitUntil(20000) { client.approvals.size == 2 }
            screenshot(app, "remote-approvals.png")
            compose.onNodeWithText("Approve", substring = false).performClick()
            compose.onAllNodesWithText("Approve", substring = false).filterToOne(hasAnyAncestor(isDialog())).performClick()
            compose.waitUntil(15000) { client.approvals.size == 1 && client.approvals.single().source == "swarm" }
            compose.onNodeWithText("Redirect", substring = false).performClick()
            compose.onNodeWithText("Send", substring = false).assertIsNotEnabled()
            compose.onNodeWithText("What should it do instead?").performTextInput("Run the checks first")
            compose.onNodeWithText("Send", substring = false).performClick()
            compose.waitUntil(15000) { client.approvals.isEmpty() }

            // Swarm console: status, board, pause and message.
            compose.onNodeWithContentDescription("Back").performClick()
            compose.onNodeWithContentDescription("Swarms").performClick()
            compose.waitUntil(15000) { client.swarms.any { it.title == "Fixture swarm" } }
            compose.onNodeWithText("Fixture swarm", substring = false).performClick()
            compose.waitUntil(15000) { client.swarms.firstOrNull()?.messages?.any { it.body.contains("Human response") } == true }
            compose.onNodeWithText("Pause", substring = false).performClick()
            compose.waitUntil(15000) { client.swarms.first().status == "paused" }
            compose.onNodeWithTag("swarmMessage").performTextInput("Ship after tests")
            compose.waitForIdle()
            compose.onNodeWithText("Send", substring = false).assertIsEnabled().performClick()
            try { compose.waitUntil(15000) { client.swarms.first().messages.orEmpty().any { it.body == "Ship after tests" && it.sender == "You" } } }
            catch (failure: androidx.compose.ui.test.ComposeTimeoutException) {
                throw AssertionError("Swarm message: ${client.swarms.first().messages}; error=${client.requestError}; status=${client.status}", failure)
            }
            // Controls stay pinned while the board follows the newest message.
            compose.onNodeWithText("Resume", substring = false).assertIsDisplayed()
            screenshot(app, "remote-swarm.png")
        } finally {
            compose.runOnUiThread { owners.clear() }
            DeviceStore(app).reset()
        }
    }
}
