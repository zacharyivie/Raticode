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
 * Ticket pills against `live-device-fixture.py --workspace --remote --desktop --tickets`: the desktop's real board code
 * resolves the ticket named in Rem's chat, the phone shows it as a pill, and tapping the pill opens that ticket on the
 * phone's global board. Pass the invitation as `ticketsInvitationFile`. No provider or task runs.
 */
class LiveTicketPillsTest {
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

    @Test fun aTicketPillInRemsChatOpensTheTicketOnTheBoard() {
        val path = InstrumentationRegistry.getArguments().getString("ticketsInvitationFile")
        assumeTrue("Requires disposable fixture with --workspace --remote --desktop --tickets", path != null)
        compose.runOnUiThread { compose.activity.requestedOrientation = android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT }
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        DeviceStore(app).reset()
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app)
            owners.put("tickets", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client, startPage = "Threads") } }
            client.preview(File(path!!).readText()); client.pair()
        }
        try {
            waitFor("desktop control", client, 30000) { client.connected && client.desktopControl }
            compose.waitUntil(5000) { compose.onAllNodesWithContentDescription("Rem").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithContentDescription("Rem").performClick()
            waitFor("Rem home", client) { (client.desktopViews["rem.home"] as? RemHome)?.tickets?.isNotEmpty() == true }
            val home = client.desktopViews["rem.home"] as RemHome
            // The desktop resolved only the key of a ticket on its board.
            assertEquals(listOf("REM-1"), home.tickets.keys.toList())
            assertEquals("Fix the sync conflict on reconnect", home.tickets.getValue("REM-1").title)
            assertEquals(listOf("REM-1"), home.messages.single { it.id == "ticket-card" }.work!!.tickets.map { it.key })

            val label = "Ticket REM-1: Fix the sync conflict on reconnect (To do)"
            compose.onNodeWithText("REM-404 isn't a ticket", substring = true).performScrollTo().assertIsDisplayed()
            // In the reply's text and in the self-work card's own line, plus the card's ticket list.
            compose.onAllNodesWithContentDescription(label, useUnmergedTree = true).assertCountEquals(2)
            compose.onAllNodesWithContentDescription(label, useUnmergedTree = true).onFirst().assertIsDisplayed()
            compose.onNodeWithContentDescription("$label. Worked on by this task", useUnmergedTree = true).assertExists()
            screenshot("tickets-chat.png")

            // Tapping the pill in Rem's text opens that ticket on the phone's global board.
            compose.onAllNodesWithContentDescription(label, useUnmergedTree = true).onFirst().performClick()
            // The ticket's page loads it from the desktop (its history, or the board from an older desktop).
            waitFor("the ticket's page", client) { compose.onAllNodesWithTag("kanbanTicketPage").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithText("REM-1").assertIsDisplayed()
            compose.onAllNodesWithText("Fix the sync conflict on reconnect").onFirst().assertIsDisplayed()
            screenshot("tickets-board.png")
        } finally {
            compose.runOnUiThread { owners.clear() }
            DeviceStore(app).reset()
        }
    }
}
