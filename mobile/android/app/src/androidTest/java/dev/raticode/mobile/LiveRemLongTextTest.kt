package dev.raticode.mobile

import android.app.Application
import androidx.activity.compose.setContent
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.lifecycle.ViewModelStore
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.data.HandoffFiles
import dev.raticode.mobile.data.LongText
import dev.raticode.mobile.live.*
import dev.raticode.mobile.ui.LiveScreen
import dev.raticode.mobile.ui.RaticodeTheme
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import java.io.File

/**
 * Long pasted text against `live-device-fixture.py --workspace --remote --desktop`: it becomes a text file chip with a
 * preview instead of using up the 4,000-character message, Put back returns it, the counter shows near the limit, and
 * the message reaches Rem with the typed note in the body and the whole text in the file. The fixture's desktop stores
 * the file through its real attachment code and writes its name, size and SHA-256 to raticode-test-desktop.json.
 */
class LiveRemLongTextTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()

    private fun waitFor(what: String, client: LiveClient, timeout: Long = 30000, condition: () -> Boolean) {
        try { compose.waitUntil(timeout, condition) }
        catch (failure: ComposeTimeoutException) { throw AssertionError("$what: files=${client.files}; status=${client.status}", failure) }
    }

    private fun screenshot(name: String) {
        compose.waitForIdle()
        instrumentation.uiAutomation.executeShellCommand("screencap -p /data/local/tmp/raticode-$name").use {
            android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
        }
    }

    private fun box() = compose.onNodeWithTag("remComposer")
    private fun boxText() = box().fetchSemanticsNode().config[androidx.compose.ui.semantics.SemanticsProperties.EditableText].text

    @Test fun longPastedTextGoesToRemAsAFile() {
        val path = InstrumentationRegistry.getArguments().getString("longTextInvitationFile")
        assumeTrue("Requires a disposable fixture with --workspace --remote --desktop", path != null)
        compose.runOnUiThread { compose.activity.requestedOrientation = android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT }
        val app = instrumentation.targetContext.applicationContext as Application
        DeviceStore(app).reset()
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app)
            owners.put("longText", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client, startPage = "Threads") } }
            client.preview(File(path!!).readText()); client.pair()
        }
        // About 6,000 characters, more than one message holds.
        val pasted = "Meeting notes from Tuesday. " + (1..240).joinToString(" ") { "Line $it of the notes, with a few words." }
        assertTrue(pasted.length > LongText.LIMIT)
        try {
            waitFor("desktop control", client) { client.connected && client.desktopControl }
            compose.waitUntil(5000) { compose.onAllNodesWithContentDescription("Rem").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithContentDescription("Rem").performClick()
            waitFor("Rem's file channel", client) { (client.desktopViews["rem.home"] as? RemHome)?.filesThread != null }

            // Typed words, then a paste: the paste becomes a file and the typed words stay.
            box().performTextInput("Summarize these notes")
            box().performTextInput(pasted)
            waitFor("text file upload", client) { client.files.any { it.name.startsWith("pasted-text-meeting-notes") && it.state == "available" } }
            val file = client.files.single { it.name.startsWith("pasted-text-") }
            assertEquals("pasted-text-meeting-notes-from-tuesday.txt", file.name)
            assertEquals(pasted.toByteArray().size.toLong(), file.size)
            assertEquals("Summarize these notes", boxText())
            compose.onNodeWithTag("remTextPreview").assertTextContains("Meeting notes from Tuesday", substring = true)
            // Too long to put back in one message, so there is no Put back.
            compose.onAllNodesWithTag("remTextPutBack").assertCountEquals(0)
            // The handoff file is gone once read; the phone's encrypted copy is the one that uploads.
            assertTrue(HandoffFiles.directory(app.cacheDir).listFiles().orEmpty().isEmpty())
            screenshot("long-text-chip.png")

            // A shorter text file can be put back into the message, and removing a chip stops its upload.
            box().performTextClearance()
            val shorter = "Short notes. " + "word ".repeat(450)
            box().performTextInput(shorter)
            waitFor("second text file", client) { client.files.count { it.name.startsWith("pasted-text-") } == 2 }
            compose.waitUntil(10_000) { compose.onAllNodesWithTag("remTextPutBack").fetchSemanticsNodes().size == 1 }
            compose.onNodeWithTag("remTextPutBack").performClick()
            compose.waitUntil(5000) { boxText() == shorter.trim() }
            compose.onAllNodesWithTag("remTextFile").assertCountEquals(1)

            // Near the limit the counter shows, and Attach as file moves the box's words into a file.
            box().performTextClearance()
            box().performTextInput("x ".repeat(950))
            box().performTextInput("y ".repeat(900))
            compose.waitUntil(5000) { compose.onAllNodesWithTag("remCharCount").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithTag("remCharCount").assertTextEquals("3,700 / 4,000")
            screenshot("long-text-counter.png")
            compose.onNodeWithTag("remToFile").performClick()
            waitFor("long message file", client) { client.files.any { it.name == "long-message-x-x-x-x.txt" && it.state == "available" } }
            compose.waitUntil(5000) { boxText().isEmpty() }
            compose.onNodeWithContentDescription("Remove long-message-x-x-x-x.txt").performClick()
            compose.onAllNodesWithTag("remTextFile").assertCountEquals(1)

            // Sent: the note is the message and the whole paste is the file, on the phone and in the desktop's copy.
            box().performTextInput("Summarize these notes")
            waitFor("connection", client) { client.connected }
            compose.onNodeWithContentDescription("Send").performClick()
            waitFor("Rem reply", client) { (client.desktopViews["rem.home"] as? RemHome)?.messages?.any { it.body == "Fixture Rem reply" } == true }
            val sent = (client.desktopViews["rem.home"] as RemHome).messages.last { it.role == "user" }
            assertEquals("Summarize these notes", sent.body)
            assertEquals(listOf("pasted-text-meeting-notes-from-tuesday.txt"), sent.attachments.map { it.name })
            assertEquals(pasted.toByteArray().size.toLong(), sent.attachments.single().size)
            compose.onAllNodesWithTag("remTextFile").assertCountEquals(0)
            screenshot("long-text-sent.png")
        } finally {
            compose.runOnUiThread { client.reset(); owners.clear() }
        }
    }
}
