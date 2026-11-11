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

/** Rem, organizations, desktop settings, usage and swarm start against
 * `live-device-fixture.py --workspace --remote --desktop`. The fixture uses the desktop's real
 * device application and organization manager, an in-memory Rem and a stand-in settings window.
 * No provider, workflow, agent or swarm runs. */
class LiveDesktopControlTest {
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

    /** The bottom bar hides while the keyboard is open, as it does for people. */
    private fun navigate(target: String) {
        compose.runOnUiThread {
            (compose.activity.getSystemService(android.content.Context.INPUT_METHOD_SERVICE) as android.view.inputmethod.InputMethodManager)
                .hideSoftInputFromWindow(compose.activity.window.decorView.windowToken, 0)
        }
        compose.waitUntil(5000) { compose.onAllNodesWithContentDescription(target).fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithContentDescription(target).performClick()
    }

    @Test fun remOrganizationsSettingsAndUsage() {
        val path = InstrumentationRegistry.getArguments().getString("desktopInvitationFile")
        assumeTrue("Requires disposable fixture with --workspace --remote --desktop", path != null)
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

            // Rem: the conversation, a message and its reply, then a task and its run.
            navigate("Rem")
            waitFor("Rem home", client) { client.desktopViews["rem.home"] is RemHome }
            compose.onNodeWithText("Hi, I am Rem on your fixture desktop.").assertIsDisplayed()
            compose.onNodeWithTag("remComposer").performTextInput("What is running?")
            compose.onNodeWithContentDescription("Send").performClick()
            waitFor("Rem reply", client) { (client.desktopViews["rem.home"] as? RemHome)?.messages?.any { it.body == "Fixture Rem reply" } == true }
            compose.onNodeWithText("Fixture Rem reply").assertIsDisplayed()
            screenshot("desktop-rem.png")
            // A suggested reply sends at once, exactly once, and leaves what you were typing in the box.
            compose.onNodeWithTag("remComposer").performTextInput("A draft I'm keeping")
            // The keyboard can push the reply out of a small screen's list.
            compose.onNodeWithTag("remMessages").performScrollToNode(hasText("Anything else running?"))
            compose.onNodeWithText("Anything else running?").performSemanticsAction(androidx.compose.ui.semantics.SemanticsActions.OnClick)
            waitFor("suggested reply", client) {
                (client.desktopViews["rem.home"] as? RemHome)?.messages?.count { it.role == "user" && it.body == "Anything else running?" } == 1
            }
            compose.onNodeWithTag("remComposer").assert(hasText("A draft I'm keeping"))
            compose.waitForIdle()
            assertEquals(1, (client.desktopViews["rem.home"] as RemHome).messages.count { it.role == "user" && it.body == "Anything else running?" })
            screenshot("desktop-rem-follow-up.png")
            // No Chat and Tasks tabs: Rem's status opens the task list, and the system Back returns to the chat as it was.
            compose.onAllNodesWithText("Chat").assertCountEquals(0)
            compose.onNodeWithTag("remMessages").performScrollToIndex(0)
            compose.onNodeWithText("Hi, I am Rem on your fixture desktop.").assertIsDisplayed()
            compose.onNodeWithTag("remStatusHeader").assertHasClickAction().performClick()
            compose.onNodeWithTag("remTasks").assertIsDisplayed()
            compose.onNodeWithTag("remComposer").assertDoesNotExist()
            screenshot("desktop-rem-tasks.png")
            compose.runOnUiThread { compose.activity.onBackPressedDispatcher.onBackPressed() }
            compose.waitForIdle()
            compose.onNodeWithTag("remComposer").assert(hasText("A draft I'm keeping"))
            compose.onNodeWithText("Hi, I am Rem on your fixture desktop.").assertIsDisplayed()
            compose.onNodeWithTag("remStatusHeader").performClick()
            compose.onNodeWithText("Check failed workflow runs").performClick()
            waitFor("task detail", client) { client.desktopViews["rem.task:fixture-task"] is RemTask }
            compose.onNodeWithText("Mon at 09:00", substring = true).assertExists()
            compose.onNodeWithText("Run now", substring = false).performClick()
            waitFor("task run", client) { (client.desktopViews["rem.task:fixture-task"] as? RemTask)?.runs?.any { it.summary == "No failures found" } == true }
            compose.onNodeWithText("Open work thread", substring = false).performScrollTo().performClick()
            waitFor("work thread", client) { client.desktopViews["rem.thread:fixture-thread"] is RemThread }
            compose.onNodeWithText("No failed runs this week.").assertIsDisplayed()
            compose.onNodeWithContentDescription("Back").performClick()
            compose.onNodeWithContentDescription("Back").performClick()
            compose.onNodeWithText("New task", substring = false).performClick()
            compose.onNodeWithText("Title").performTextInput("Draft release notes")
            compose.onNodeWithText("What should Rem do?").performTextInput("Summarize merged changes")
            compose.onNodeWithText("Add task", substring = false).performClick()
            waitFor("new task", client) { (client.desktopViews["rem.home"] as? RemHome)?.tasks?.any { it.title == "Draft release notes" } == true }
            // The top-left arrow goes back to the chat, which kept the draft through the task, its thread and the list.
            compose.onNodeWithContentDescription("Back to chat").performClick()
            compose.onNodeWithTag("remComposer").assert(hasText("A draft I'm keeping"))

            // Organizations: a real desktop organization; approve its ticket's plan, then start and pause it.
            navigate("Organizations")
            waitFor("organizations", client) { client.desktopViews["organizations.list"] is List<*> }
            compose.onNodeWithText("Fixture engineering").performClick()
            waitFor("organization", client) { client.desktopViews.values.filterIsInstance<Organization>().firstOrNull()?.plan != null }
            compose.onNodeWithTag("orgTab-issues").performClick()
            compose.onNodeWithText("Ship the fixture release").performClick()
            compose.onNodeWithText("Approve plan", substring = false).performClick()
            waitFor("approval", client) {
                client.desktopViews.values.filterIsInstance<Organization>().firstOrNull()?.tasks?.first { it.title == "Ship the fixture release" }?.needsApproval == false
            }
            screenshot("desktop-organization.png")
            compose.onNodeWithContentDescription("Back").performClick()
            compose.onNodeWithTag("orgControl").performClick(); compose.onNodeWithTag("orgConfirm").performClick()
            waitFor("organization running", client) { client.desktopViews.values.filterIsInstance<Organization>().firstOrNull()?.state == "running" }
            compose.onNodeWithTag("orgControl").performClick(); compose.onNodeWithTag("orgConfirm").performClick()
            waitFor("organization paused", client) { client.desktopViews.values.filterIsInstance<Organization>().firstOrNull()?.state == "paused" }

            // Desktop settings: the window applies the change and publishes it back.
            navigate("More")
            compose.onNodeWithText("Desktop settings").performClick()
            waitFor("settings", client) { (client.desktopViews["settings.get"] as? DesktopSettings)?.available == true }
            compose.onNodeWithContentDescription("Theme").performScrollTo().performClick()
            compose.onNodeWithText("Dark", substring = false).performClick()
            waitFor("settings applied", client, 30000) {
                (client.desktopViews["settings.get"] as? DesktopSettings)?.let { it.values["appearance.theme"] == "dark" && it.pending == 0 } == true
            }
            screenshot("desktop-settings.png")
            compose.onNodeWithContentDescription("Back").performClick()
            compose.onNodeWithText("Usage").performClick()
            waitFor("usage", client) { client.desktopViews["usage.overview"] is DesktopUsage }
            compose.onNodeWithText("7 calls").assertIsDisplayed()
        } finally {
            compose.runOnUiThread { owners.clear() }
            DeviceStore(app).reset()
        }
    }
}
