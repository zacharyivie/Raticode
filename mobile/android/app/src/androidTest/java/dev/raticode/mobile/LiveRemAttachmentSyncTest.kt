package dev.raticode.mobile

import android.app.Application
import androidx.activity.compose.setContent
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.core.content.FileProvider
import androidx.lifecycle.ViewModelStore
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.data.CameraCaptures
import dev.raticode.mobile.data.FileMetadata
import dev.raticode.mobile.live.*
import dev.raticode.mobile.ui.LiveScreen
import dev.raticode.mobile.ui.RaticodeTheme
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import java.io.File

/**
 * Rem chat attachments on both devices, against `live-device-fixture.py --workspace --remote --desktop
 * --desktop-attachments`, whose chat has a message sent from the desktop window with a picture, a text file and a
 * file since deleted. The phone fetches the picture for its thumbnail at once and the text file when tapped, says
 * when a file is gone or the phone is offline, fetches its own sent file back once its copy is gone, and keeps the
 * fetched copies across a restart. Screenshots go to `/data/local/tmp/raticode-attachments-sync-*.png`.
 */
class LiveRemAttachmentSyncTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()

    private fun waitFor(what: String, client: LiveClient, timeout: Long = 30000, condition: () -> Boolean) {
        try { compose.waitUntil(timeout, condition) }
        catch (failure: ComposeTimeoutException) {
            throw AssertionError("$what: files=${client.files}; copies=${client.attachmentCopies}; fetches=${client.attachmentFetches}; status=${client.status}", failure)
        }
    }

    private fun screenshot(name: String) {
        compose.waitForIdle()
        instrumentation.uiAutomation.executeShellCommand("screencap -p /data/local/tmp/raticode-attachments-sync-$name").use {
            android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
        }
    }

    private fun tile(name: String) = hasTestTag("attachmentTile") and hasAnyDescendant(hasContentDescription(name, substring = true))
    private fun scrollTo(name: String) = compose.onNodeWithTag("remMessages").performScrollToNode(tile(name))

    @Test fun attachmentsFromEitherDeviceShowOnThePhone() {
        val path = InstrumentationRegistry.getArguments().getString("attachmentSyncInvitationFile")
        assumeTrue("Requires a disposable fixture with --workspace --remote --desktop --desktop-attachments", path != null)
        compose.runOnUiThread { compose.activity.requestedOrientation = android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT }
        val app = instrumentation.targetContext.applicationContext as Application
        DeviceStore(app).reset()
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        fun show(fresh: LiveClient) = compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = fresh, startPage = "Threads") } }
        compose.runOnUiThread {
            client = LiveClient(app)
            owners.put("sync", client)
            show(client)
            client.preview(File(path!!).readText()); client.pair()
        }
        val home = { client.desktopViews["rem.home"] as? RemHome }
        val desk = { home()?.messages?.firstOrNull { it.id == "desk-attach" } }
        try {
            waitFor("desktop control", client) { client.connected && client.desktopControl }
            compose.waitUntil(5000) { compose.onAllNodesWithContentDescription("Rem").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithContentDescription("Rem").performClick()
            waitFor("desktop message", client) { desk() != null && client.attachmentFiles }
            val (picture, notes, gone) = desk()!!.attachments

            // 1. A picture sent from the desktop window is fetched without a tap and shows as a thumbnail.
            scrollTo("desk.png")
            waitFor("picture fetched", client, 60_000) {
                client.attachmentCopies[picture.id]?.let { id -> client.files.firstOrNull { it.id == id }?.state } == "available"
            }
            val pictureCopy = client.files.single { it.id == client.attachmentCopies[picture.id] }
            assertEquals("desk.png", pictureCopy.name)
            assertEquals(picture.size, pictureCopy.size)
            waitFor("thumbnail", client) { client.mediaPreviews[pictureCopy.id] is MediaPreview.Ready }

            // 2. A file deleted on the computer says so instead of loading forever.
            scrollTo("gone.png")
            waitFor("gone file", client) { client.attachmentFetches[gone.id] == RemAttachments.Fetch.Gone }
            compose.onNode(tile("gone.png")).assert(hasAnyDescendant(hasText("Not on your computer")))
            screenshot("desktop-files.png")

            // 3. Offline, other files say to connect; a tap explains instead of failing.
            compose.runOnUiThread { client.disconnect() }
            waitFor("offline", client) { !client.connected }
            scrollTo("notes.txt")
            compose.onNode(tile("notes.txt")).assert(hasAnyDescendant(hasText("Not connected")))
            compose.onNode(hasClickAction() and hasContentDescription("notes.txt", substring = true)).performClick()
            compose.onNode(hasText("Connect", substring = true) and hasText("notes.txt", substring = true)).assertExists()
            compose.onNodeWithText("Close").performClick()
            compose.runOnUiThread { client.connect() }
            waitFor("online", client) { client.connected && client.attachmentFiles }

            // 4. Online, a tap downloads the file and opens it.
            scrollTo("notes.txt")
            compose.onNode(tile("notes.txt")).assert(hasAnyDescendant(hasText("Tap to download")))
            compose.onNode(hasClickAction() and hasContentDescription("notes.txt", substring = true)).performClick()
            waitFor("text preview", client, 60_000) { compose.onAllNodesWithTag("textPreview").fetchSemanticsNodes().isNotEmpty() }
            waitFor("its words", client) { compose.onAllNodesWithText("Notes from the desktop window", substring = true).fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithContentDescription("Close").performClick()

            // 5. A file this phone sent is fetched back from the computer once the phone's own copy is gone.
            val bytes = (1..6000).joinToString("\n") { "Line $it sent from the phone" }.toByteArray()
            val original = File(CameraCaptures.directory(app.cacheDir).apply { mkdirs() }, "phone-sent.txt").apply { writeBytes(bytes) }
            var uploaded: String? = null
            compose.runOnUiThread {
                client.importRemFile(FileProvider.getUriForFile(app, app.packageName + ".opened", original), "phone-sent.txt") { uploaded = it }
            }
            waitFor("upload", client) { uploaded != null && client.files.any { it.id == uploaded && it.state == "available" } }
            compose.runOnUiThread { client.sendRemMessage("Sent from the phone", listOf(uploaded!!)) }
            waitFor("phone message on the desktop", client) { home()?.messages?.any { it.role == "user" && it.attachments.any { a -> a.id == uploaded } } == true }
            val sent = home()!!.messages.single { it.role == "user" && it.attachments.any { a -> a.id == uploaded } }
            compose.runOnUiThread { client.removeFile(uploaded!!) }
            waitFor("own copy removed", client) { client.files.none { it.id == uploaded && FileTransfers.holds(it.direction, it.state) } }
            scrollTo("phone-sent.txt")
            compose.onNode(hasClickAction() and hasContentDescription("phone-sent.txt", substring = true)).performClick()
            waitFor("fetched back", client, 60_000) {
                client.attachmentCopies[uploaded]?.let { id -> client.files.firstOrNull { it.id == id }?.state } == "available"
            }
            var fetched: ByteArray? = null
            compose.runOnUiThread { client.readMedia(client.attachmentCopies[uploaded]!!) { fetched = it ?: ByteArray(0) } }
            waitFor("read back", client) { fetched != null }
            assertEquals(FileMetadata.digest(bytes), FileMetadata.digest(fetched!!))
            assertEquals("phone-sent.txt", sent.attachments.single().name)
            waitFor("its preview", client) { compose.onAllNodesWithTag("textPreview").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithContentDescription("Close").performClick()

            // 6. The fetched copies survive a restart, so nothing is asked for again.
            val copies = client.attachmentCopies
            compose.runOnUiThread { owners.clear() }
            compose.runOnUiThread { client = LiveClient(app); owners.put("sync", client); show(client) }
            waitFor("copies restored", client) { client.attachmentCopies == copies && client.files.any { it.id == copies[picture.id] && it.state == "available" } }
            compose.runOnUiThread { client.connect() }
            waitFor("reconnected", client) { client.connected && client.desktopControl }
            compose.onNodeWithContentDescription("Rem").performClick()
            waitFor("desktop message again", client) { desk() != null }
            scrollTo("desk.png")
            waitFor("thumbnail again", client) { client.mediaPreviews[copies[picture.id]] is MediaPreview.Ready }
            assertTrue(client.attachmentFetches[picture.id] == null)
            screenshot("after-restart.png")
        } finally {
            CameraCaptures.cleanup(app.cacheDir, keepMillis = 0)
            compose.runOnUiThread { client.reset(); owners.clear() }
        }
    }
}
