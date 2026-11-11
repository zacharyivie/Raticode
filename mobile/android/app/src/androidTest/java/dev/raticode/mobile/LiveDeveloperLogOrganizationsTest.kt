package dev.raticode.mobile

import android.app.Application
import android.content.ClipboardManager
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
 * The Developer log and the five-section Organizations view against `live-device-fixture.py --workspace --remote --desktop`.
 * The fixture's desktop writes an app log with an entry each second and has an organization with a project, milestones,
 * an epic, two workers and a note, all through the desktop's real organization manager. Nothing runs. Pass
 * `devlogInvitationFile`. Afterwards `raticode-test-desktop.json` shows what reached Rem (never the draft), the workers'
 * holds and the notes.
 */
class LiveDeveloperLogOrganizationsTest {
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

    private fun navigate(target: String) {
        compose.runOnUiThread {
            (compose.activity.getSystemService(android.content.Context.INPUT_METHOD_SERVICE) as android.view.inputmethod.InputMethodManager)
                .hideSoftInputFromWindow(compose.activity.window.decorView.windowToken, 0)
        }
        compose.waitUntil(5000) { compose.onAllNodesWithContentDescription(target).fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithContentDescription(target).performClick()
    }

    private fun organization(client: LiveClient) = client.desktopViews.values.filterIsInstance<Organization>().firstOrNull()

    @Test fun developerLogAndOrganizationSections() {
        val path = InstrumentationRegistry.getArguments().getString("devlogInvitationFile")
        assumeTrue("Requires disposable fixture with --workspace --remote --desktop", path != null)
        compose.runOnUiThread { compose.activity.requestedOrientation = android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT }
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        DeviceStore(app).reset()
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app)
            owners.put("devlog", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client, startPage = "Rem") } }
            client.preview(File(path!!).readText()); client.pair()
        }
        try {
            waitFor("desktop control", client, 30000) { client.connected && client.desktopControl }

            // A draft in Rem's message box stays through a trip to another page.
            navigate("Rem")
            waitFor("Rem home", client) { client.desktopViews["rem.home"] is RemHome }
            val before = (client.desktopViews["rem.home"] as RemHome).messages.size
            compose.onNodeWithTag("remComposer").performTextInput("Keep this draft")

            // The Developer log: the desktop's entries, live, with Debug off until chosen.
            navigate("More")
            compose.onNodeWithText("Developer log").performScrollTo().performClick()
            waitFor("log entries", client) { client.developerLog.any { it.message == "Fixture phone request was slow" } }
            assertEquals(true, client.developerLogSupported)
            // Newest first: on a small screen the older entry is further down.
            compose.onNodeWithTag("logList").performScrollToNode(hasText("Fixture phone request was slow"))
            compose.onNodeWithText("Fixture phone request was slow").assertIsDisplayed()
            assertTrue(client.developerLog.none { it.level == "debug" })
            assertTrue(client.developerLog.first { it.message.startsWith("Fixture failure") }.message.contains("token=[redacted]"))
            val beats = client.developerLog.count { it.message.startsWith("Fixture heartbeat") }
            waitFor("a live entry", client) { client.developerLog.count { it.message.startsWith("Fixture heartbeat") } > beats }
            compose.onNodeWithTag("logLevel-debug").performClick()
            waitFor("debug entries", client) { client.developerLog.any { it.message == "Fixture debug detail" } }
            // This phone's own connection events share the list.
            assertTrue(client.phoneLogEntries.any { it.message.startsWith("Connected to desktop") })
            screenshot("devlog.png")

            // Long-press selects; Copy puts the entry on the clipboard, oldest first. Selecting holds the list still.
            compose.onNodeWithTag("logList").performScrollToNode(hasText("Fixture phone request was slow"))
            compose.onNodeWithText("Fixture phone request was slow").performTouchInput { longClick() }
            screenshot("devlog-longpress.png")
            compose.onNodeWithTag("logSelectionBar").assertIsDisplayed()
            compose.onNodeWithText("1 entry selected").assertIsDisplayed()
            compose.onNodeWithTag("logCopy").performClick()
            var copied = ""
            compose.runOnUiThread { copied = compose.activity.getSystemService(ClipboardManager::class.java).primaryClip?.getItemAt(0)?.text?.toString().orEmpty() }
            assertTrue(copied, copied.contains("WARN devices: Fixture phone request was slow"))
            // Hold and drag takes in the rows between.
            compose.onNodeWithTag("logList").performTouchInput {
                down(center.copy(y = top + 120f)); advanceEventTime(viewConfiguration.longPressTimeoutMillis + 200)
                moveTo(center.copy(y = top + 420f)); up()
            }
            compose.waitForIdle()
            assertTrue(compose.onAllNodesWithText("selected", substring = true).fetchSemanticsNodes().isNotEmpty())
            screenshot("devlog-selected.png")

            // Ask Rem: the entries go to Rem's message box next to the draft, and nothing is sent.
            compose.onNodeWithTag("logAskRem").performClick()
            waitFor("log attachment", client) { client.files.any { it.name.startsWith("raticode-log-") && it.name.endsWith(".txt") } }
            compose.onNodeWithTag("remComposer").assertTextContains("Keep this draft")
            compose.onNodeWithTag("remComposer").assertIsFocused()
            compose.waitForIdle()
            assertEquals(before, (client.desktopViews["rem.home"] as RemHome).messages.size)
            // The fixture's raticode-test-desktop.json lists what reached Rem; the host checks it holds no draft.
            screenshot("devlog-ask-rem.png")

            // Organizations: the five sections from the desktop's real organization manager.
            navigate("Organizations")
            waitFor("organizations", client) { client.desktopViews["organizations.list"] is List<*> }
            compose.onNodeWithText("Fixture engineering").performClick()
            waitFor("five sections", client) { organization(client)?.plan != null }
            compose.onNodeWithText("Ship calm fixture releases").assertIsDisplayed()
            screenshot("org-overview.png")

            // The work schedule: none yet, so the suggested one opens in the editor and saves only from Save.
            if (organization(client)?.plan?.schedule != null) {
                compose.onNodeWithTag("orgOverview").performScrollToNode(hasTestTag("orgScheduleEmpty"))
                screenshot("org-schedule-empty.png")
                compose.onNodeWithTag("orgScheduleSuggested").performClick()
                compose.onNodeWithTag("orgSchedulePage").assertIsDisplayed()
                assertFalse(organization(client)!!.plan!!.schedule!!.configured)
                screenshot("org-schedule-list.png")
                compose.onAllNodesWithTag("orgScheduleBlock")[0].performClick()
                compose.onNodeWithTag("orgBlockName").assertTextContains("New feature work")
                compose.onNodeWithTag("orgBlockCadence-monthly").performClick()
                compose.onNodeWithTag("orgBlockByWeekday").performScrollTo().performClick()
                screenshot("org-schedule-block.png")
                // Back without Done keeps the suggested block as it was.
                compose.runOnUiThread { compose.activity.onBackPressedDispatcher.onBackPressed() }
                compose.onNodeWithTag("orgSchedulePage").assertIsDisplayed()
                compose.onNodeWithTag("orgScheduleSave").performClick()
                waitFor("saved schedule", client) { organization(client)?.plan?.schedule?.configured == true }
                assertEquals(7, organization(client)!!.plan!!.schedule!!.blocks.size)
                assertEquals("daily", organization(client)!!.plan!!.schedule!!.blocks[0].cadence)
                compose.onNodeWithTag("orgOverview").performScrollToNode(hasTestTag("orgSchedule"))
                compose.onNodeWithTag("orgSchedule").assertIsDisplayed()
                screenshot("org-schedule-card.png")
                compose.onNodeWithTag("orgOverview").performScrollToIndex(0)
            }

            compose.onNodeWithTag("orgTab-projects").performClick()
            compose.onNodeWithText("Fixture app").performClick()
            compose.onNodeWithText("Fixture epic").assertIsDisplayed()
            compose.onNodeWithText("Fixture epic").performClick()
            compose.onNodeWithText("Fixture epic ticket").assertIsDisplayed()
            screenshot("org-epic.png")

            // Issues: a new Backlog ticket, then moved to To do by touch.
            compose.onNodeWithTag("orgTab-issues").performClick()
            compose.onNodeWithTag("orgColumn-backlog").performClick()
            compose.onNodeWithTag("orgNewTicket-backlog").performClick()
            compose.onNodeWithTag("orgTicketTitle").performTextInput("Phone ticket")
            compose.onNodeWithTag("orgCreateTicket").performClick()
            waitFor("new ticket", client) { organization(client)?.tasks?.any { it.title == "Phone ticket" && it.status == "backlog" } == true }
            compose.onNodeWithText("Phone ticket").performTouchInput { longClick() }
            screenshot("org-move.png")
            compose.onNodeWithTag("orgMove-todo").performClick()
            waitFor("moved ticket", client) { organization(client)?.tasks?.any { it.title == "Phone ticket" && it.status == "todo" } == true }
            screenshot("org-board.png")

            // Workers: the reporting tree, and a hold that only reaches the desktop through the worker action.
            compose.onNodeWithTag("orgTab-workers").performClick()
            compose.onNodeWithText("Fixture builder").assertIsDisplayed()
            compose.onNodeWithText("Fixture lead").performClick()
            compose.onNodeWithText("Careful and brief").assertIsDisplayed()
            compose.onNodeWithTag("orgHold").performClick(); compose.onNodeWithTag("orgConfirm").performClick()
            waitFor("held", client) { organization(client)?.employees?.first { it.id == "lead" }?.paused == true }
            compose.onNodeWithTag("orgRelease").performClick(); compose.onNodeWithTag("orgConfirm").performClick()
            waitFor("released", client) { organization(client)?.employees?.first { it.id == "lead" }?.paused == false }
            screenshot("org-worker.png")

            // Knowledge: a new note, then a note's body read on its own.
            compose.onNodeWithTag("orgTab-knowledge").performClick()
            compose.onNodeWithText("Fixture finding").assertIsDisplayed()
            compose.onNodeWithTag("orgNewNote").performClick()
            compose.onNodeWithTag("orgNoteTitle").performTextInput("Phone note")
            compose.onNodeWithTag("orgSaveNote").performClick()
            waitFor("new note", client) { organization(client)?.plan?.notes?.any { it.title == "Phone note" } == true }
            compose.onNodeWithText("Fixture finding").performClick()
            waitFor("note body", client) { client.desktopViews.values.filterIsInstance<OrgNoteDetail>().any { it.body == "The fixture body text." } }
            compose.onNodeWithText("The fixture body text.").performScrollTo().assertIsDisplayed()
            screenshot("org-note.png")
        } finally {
            compose.runOnUiThread { owners.clear() }
        }
    }
}
