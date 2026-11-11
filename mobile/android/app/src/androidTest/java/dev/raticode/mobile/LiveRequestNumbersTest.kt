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
 * Request numbers against `live-device-fixture.py --workspace --remote --desktop --rich --many-sources`, whose desktop
 * device code refuses a new request that reuses a number. A result with eight sources makes the phone fetch previews
 * three at a time, each answer starting the next one. Before the fix, every such answer put the phone's request
 * counter back, so a later request was refused with "The desktop already has a different request with this number".
 * Rem messages sent meanwhile, and one queued offline and sent again on reconnecting, must arrive once.
 */
class LiveRequestNumbersTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    private lateinit var client: LiveClient
    private val sources = listOf("https://example.com/fixture-guide") + (1..7).map { "https://example.com/fixture-source-$it" }
    private val refusal = "different request with this number"

    private fun home() = client.desktopViews["rem.home"] as? RemHome
    private fun userMessages(body: String) = home()?.messages?.count { it.role == "user" && it.body == body } ?: 0
    private fun refused() = (listOfNotNull(client.requestError, client.remUnsent?.detail) + client.desktopErrors.values).filter { refusal in it }

    private fun waitFor(what: String, timeout: Long = 20000, condition: () -> Boolean) {
        try { compose.waitUntil(timeout, condition) }
        catch (failure: ComposeTimeoutException) {
            throw AssertionError("$what: refused=${refused()}; dialog=${client.requestError}; errors=${client.desktopErrors}; " +
                "previews=${client.previews}; status=${client.status}", failure)
        }
    }

    @Test fun previewsAndMessagesNeverReuseARequestNumber() {
        val args = InstrumentationRegistry.getArguments()
        val path = args.getString("manySourcesInvitationFile")
        assumeTrue("Requires disposable fixture with --workspace --remote --desktop --rich --many-sources", path != null)
        compose.runOnUiThread { compose.activity.requestedOrientation = android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT }
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        DeviceStore(app).reset()
        val owners = ViewModelStore()
        compose.runOnUiThread {
            client = LiveClient(app)
            owners.put("numbers", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client, startPage = "Threads") } }
            client.preview(File(path!!).readText()); client.pair()
        }
        try {
            waitFor("desktop control", 30000) { client.connected && client.desktopControl }
            compose.waitUntil(5000) { compose.onAllNodesWithContentDescription("Rem").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithContentDescription("Rem").performClick()
            waitFor("Rem home") { home() != null }
            val result = home()!!.messages.first { it.id == "rich-result" }.result
            assertEquals(sources, result?.sources?.map { it.url })

            // Showing the eight source cards starts the previews; a Rem message goes out while they load.
            compose.onNodeWithTag("remMessages").performScrollToNode(hasText("8 sources"))
            compose.runOnUiThread { client.sendRemMessage("Sent while previews load", emptyList()) }
            waitFor("every preview") { sources.all { client.previews[it] is ChatPreview.Ready } }
            waitFor("message delivered") { userMessages("Sent while previews load") == 1 }

            // Offline: the message waits in the saved queue, which is sent again on reconnecting.
            compose.runOnUiThread { client.disconnect() }
            waitFor("disconnected") { !client.connected }
            compose.runOnUiThread { client.sendRemMessage("Queued while offline", emptyList()) }
            compose.runOnUiThread { client.connect() }
            waitFor("reconnected", 30000) { client.connected }
            waitFor("offline message delivered") { userMessages("Queued while offline") == 1 }

            // Later polls and the next message still get fresh numbers, and nothing arrived twice.
            compose.runOnUiThread { client.sendRemMessage("Sent after reconnecting", emptyList()) }
            waitFor("last message delivered") { userMessages("Sent after reconnecting") == 1 }
            val settled = System.currentTimeMillis() + 4000
            compose.waitUntil(6000) { System.currentTimeMillis() >= settled }
            assertEquals(emptyList<String>(), refused())
            assertNull(client.requestError)
            assertNull(client.remUnsent)
            for (body in listOf("Sent while previews load", "Queued while offline", "Sent after reconnecting")) assertEquals(body, 1, userMessages(body))
        } finally {
            compose.runOnUiThread { owners.clear() }
            DeviceStore(app).reset()
        }
    }
}
