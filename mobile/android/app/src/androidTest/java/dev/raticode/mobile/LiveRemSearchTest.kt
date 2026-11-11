package dev.raticode.mobile

import android.app.Application
import androidx.activity.compose.setContent
import androidx.compose.ui.semantics.SemanticsProperties
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
 * Searching Rem's chat against `live-device-fixture.py --workspace --remote --desktop --search`: the phone holds only
 * the newest messages, so it asks the desktop's real search code, pages back to a result far before them, opens its
 * folded text and marks it. Closing search keeps the chat and the message box's draft. Pass the invitation as
 * `searchInvitationFile`. No provider or task runs.
 */
class LiveRemSearchTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()

    private fun waitFor(what: String, client: LiveClient, timeout: Long = 20000, condition: () -> Boolean) {
        try { compose.waitUntil(timeout, condition) }
        catch (failure: ComposeTimeoutException) {
            throw AssertionError("$what: search=${client.remSearch}; history=${client.remHistoryError}/${client.remHistoryBusy}; errors=${client.desktopErrors}; status=${client.status}", failure)
        }
    }

    private fun screenshot(name: String) {
        compose.waitForIdle()
        InstrumentationRegistry.getInstrumentation().uiAutomation.executeShellCommand("screencap -p /data/local/tmp/raticode-$name").use {
            android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
        }
    }

    private fun composerText() = compose.onNodeWithTag("remComposer").fetchSemanticsNode().config[SemanticsProperties.EditableText].text

    @Test fun searchFindsAnOldMessageOnTheDesktopAndJumpsBackToIt() {
        val path = InstrumentationRegistry.getArguments().getString("searchInvitationFile")
        assumeTrue("Requires disposable fixture with --workspace --remote --desktop --search", path != null)
        compose.runOnUiThread { compose.activity.requestedOrientation = android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT }
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        DeviceStore(app).reset()
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app)
            owners.put("search", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client, startPage = "Threads") } }
            client.preview(File(path!!).readText()); client.pair()
        }
        try {
            waitFor("desktop control", client, 30000) { client.connected && client.desktopControl }
            compose.waitUntil(5000) { compose.onAllNodesWithContentDescription("Rem").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithContentDescription("Rem").performClick()
            waitFor("Rem home", client) { (client.desktopViews["rem.home"] as? RemHome)?.search == true }
            val home = client.desktopViews["rem.home"] as RemHome
            // The phone holds only the newest messages; the old one is on the desktop.
            assertTrue(home.earlier > 0)
            assertTrue(home.messages.none { it.id == "orchid" })

            compose.onNodeWithTag("remComposer").performTextInput("A half-written thought")
            compose.onNodeWithContentDescription("Search chat").performClick()
            compose.onNodeWithTag("remSearchField").performTextInput("ORCHID budget")
            waitFor("search results", client) { compose.onAllNodesWithTag("remSearchResult").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithTag("remSearchSummary").assertTextEquals("1 match")
            val page = client.remSearch.page!!
            assertFalse("The desktop searched its whole history", page.local)
            assertEquals(listOf("orchid", "budget"), page.results.single().snippet.filter { it.match }.map { it.text })
            screenshot("search-results.png")

            // Back closes search: the chat and the draft are as they were.
            compose.runOnUiThread { compose.activity.onBackPressedDispatcher.onBackPressed() }
            compose.waitUntil(5000) { compose.onAllNodesWithTag("remSearchResults").fetchSemanticsNodes().isEmpty() }
            assertEquals("A half-written thought", composerText())

            // Searching again and tapping the result pages back to it, opens its folded text and marks it.
            compose.onNodeWithContentDescription("Search chat").performClick()
            compose.onNodeWithTag("remSearchField").performTextInput("orchid budget")
            waitFor("search results again", client) { compose.onAllNodesWithTag("remSearchResult").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithTag("remSearchResult").performClick()
            waitFor("the old message", client, 30000) { compose.onAllNodesWithTag("remFound").fetchSemanticsNodes().isNotEmpty() }
            assertTrue(client.remEarlier.any { it.id == "orchid" })
            compose.onAllNodesWithTag("remSearchResults").assertCountEquals(0)
            compose.onNodeWithText("the orchid budget was approved", substring = true).assertIsDisplayed()
            // The message is taller than the chat and its words are near its end, so the chat scrolled on into it.
            compose.waitForIdle()
            val messageTop = compose.onNodeWithText("the orchid budget was approved", substring = true).fetchSemanticsNode().positionInRoot.y
            val listTop = compose.onNodeWithTag("remMessages").fetchSemanticsNode().boundsInRoot.top
            assertTrue("scrolled into the long message ($messageTop vs $listTop)", messageTop < listTop - 200)
            screenshot("search-jump.png")
            assertEquals("A half-written thought", composerText())
            // Every message from the old one to the newest is in the chat, with none missing between the pages.
            val chat = RemSearch.joined(client.remEarlier, (client.desktopViews["rem.home"] as RemHome).messages).map { it.id }
            assertEquals(listOf("orchid") + (0 until 160).map { "later-$it" }, chat.dropWhile { it != "orchid" })
        } finally {
            compose.runOnUiThread { owners.clear() }
            DeviceStore(app).reset()
        }
    }
}
