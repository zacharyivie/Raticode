package dev.raticode.mobile

import android.app.Application
import android.content.ContentValues
import android.graphics.Bitmap
import android.net.Uri
import android.provider.MediaStore
import android.view.accessibility.AccessibilityNodeInfo
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
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.Random

/**
 * Pictures of about 600 KB from the paperclip's Downloads, against
 * `live-device-fixture.py --workspace --remote --desktop --slow-files`, whose desktop takes 150 ms per 16 KiB chunk so
 * each upload is still going when the test acts. The ring fills with the desktop's confirmed share; Send works
 * mid-upload and the message goes once the file arrives; Edit takes a waiting message back with its file; removing
 * a chip cancels its upload; a waiting message survives losing the connection and goes once, after reconnecting. The
 * fixture lists each stored file's name, size and SHA-256 under `attachments` in raticode-test-desktop.json.
 */
class LiveUploadHoldTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val automation get() = instrumentation.uiAutomation
    private val downloads = mutableListOf<Uri>()

    private fun waitFor(what: String, client: LiveClient, timeout: Long = 30000, condition: () -> Boolean) {
        try { compose.waitUntil(timeout, condition) }
        catch (failure: ComposeTimeoutException) { throw AssertionError("$what: files=${client.files}; held=${client.remHeld}; status=${client.status}", failure) }
    }

    private fun screenshot(name: String, held: Boolean = false) {
        if (held) {
            // The keyboard covers the waiting message on a small screen.
            compose.runOnUiThread {
                compose.activity.getSystemService(android.view.inputmethod.InputMethodManager::class.java)
                    .hideSoftInputFromWindow(compose.activity.window.decorView.windowToken, 0)
            }
            compose.waitForIdle()
            compose.onNodeWithTag("remMessages").performScrollToNode(hasTestTag("remHeld"))
        }
        compose.waitForIdle()
        automation.executeShellCommand("screencap -p /data/local/tmp/raticode-$name").use {
            android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
        }
    }

    @Test fun picturesUploadWithProgressAndAMessageCanBeSentBeforeTheyFinish() {
        val path = InstrumentationRegistry.getArguments().getString("uploadHoldInvitationFile")
        assumeTrue("Requires a disposable fixture with --workspace --remote --desktop --slow-files", path != null)
        compose.runOnUiThread { compose.activity.requestedOrientation = android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT }
        val app = instrumentation.targetContext.applicationContext as Application
        DeviceStore(app).reset()
        val stamp = System.currentTimeMillis()
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app)
            owners.put("upload", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client, startPage = "Threads") } }
            client.preview(File(path!!).readText()); client.pair()
        }
        val home = { client.desktopViews["rem.home"] as? RemHome }
        val replies = { home()?.messages?.count { it.body == "Fixture Rem reply" } ?: 0 }
        val sentBodies = { body: String -> home()?.messages?.count { it.role == "user" && it.body == body } ?: 0 }
        try {
            waitFor("desktop control", client) { client.connected && client.desktopControl }
            compose.waitUntil(5000) { compose.onAllNodesWithContentDescription("Rem").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithContentDescription("Rem").performClick()
            waitFor("Rem's file channel", client) { home()?.filesThread != null }

            // 1. The ring fills as the desktop confirms chunks, and Send works before the upload is done.
            val first = download(app, "0-upload-$stamp-a.png", 1)
            assertTrue("about 600 KB: ${first.second}", first.second in 550_000..700_000)
            pick(client, first.first)
            val seen = mutableListOf<Long>()
            waitFor("upload under way", client) {
                client.files.firstOrNull { it.name == first.first }?.let { if (it.bytes !in seen) seen += it.bytes; it.bytes in 1 until it.size } == true
            }
            compose.onNodeWithTag("uploadRing").assertExists()
            compose.onNode(hasText("Uploading · ", substring = true)).assertExists()
            screenshot("upload-progress.png")
            compose.onNodeWithTag("remComposer").performTextInput("A picture sent while it uploads")
            compose.onNodeWithContentDescription("Send").assertIsEnabled().performClick()
            waitFor("message waiting for its file", client) {
                client.remHeld?.text == "A picture sent while it uploads" &&
                    compose.onAllNodesWithContentDescription("Remove ${first.first}").fetchSemanticsNodes().isEmpty()
            }
            assertTrue(client.files.single { it.name == first.first }.state != "available")
            compose.onNodeWithTag("remHeld").assertExists()
            compose.onNode(hasText("Sends to Rem once 1 file uploads")).assertExists()
            // Its chip left the message box; the waiting message shows the file instead.
            compose.onNodeWithContentDescription("Remove ${first.first}").assertDoesNotExist()
            compose.onNode(hasText(first.first) and hasAnyAncestor(hasTestTag("remHeld"))).assertExists()
            screenshot("upload-held.png", held = true)
            waitFor("Rem reply", client, 60_000) {
                client.files.firstOrNull { it.name == first.first }?.let { if (it.bytes !in seen) seen += it.bytes }
                client.remHeld == null && replies() == 1
            }
            client.files.single { it.name == first.first }.let { file ->
                seen += file.bytes
                assertEquals("available", file.state)
            }
            // Progress only moved forward and came in many steps, not one jump at the end.
            assertEquals(seen.sorted(), seen)
            assertTrue("progress steps: $seen", seen.size >= 5)
            assertEquals(1, sentBodies("A picture sent while it uploads"))

            // 2. Edit takes a waiting message back, with its words and its file.
            val second = download(app, "0-upload-$stamp-b.png", 2, side = 620)
            pick(client, second.first)
            waitFor("second upload under way", client) { client.files.firstOrNull { it.name == second.first }?.state == "receiving" }
            compose.onNodeWithTag("remComposer").performTextInput("Edit me")
            compose.onNodeWithContentDescription("Send").performClick()
            waitFor("second message waiting", client) { client.remHeld != null }
            compose.onNodeWithTag("remMessages").performScrollToNode(hasText("Edit"))
            compose.onNode(hasText("Edit") and hasAnyAncestor(hasTestTag("remHeld"))).performClick()
            waitFor("message back in the box", client) { client.remHeld == null }
            compose.onNodeWithTag("remComposer").assert(hasText("Edit me"))
            compose.onNodeWithContentDescription("Remove ${second.first}").assertExists()
            compose.onNodeWithTag("remComposer").performTextClearance()
            compose.onNodeWithTag("remComposer").performTextInput("Edited and sent")
            compose.onNodeWithContentDescription("Send").performClick()
            waitFor("edited message answered", client, 60_000) { client.remHeld == null && replies() == 2 }
            assertEquals(1, sentBodies("Edited and sent"))
            assertEquals(0, sentBodies("Edit me"))

            // 3. Removing a chip cancels its upload on this phone at once.
            val third = download(app, "0-upload-$stamp-c.png", 3, side = 620)
            pick(client, third.first)
            waitFor("third upload under way", client) { client.files.firstOrNull { it.name == third.first }?.state == "receiving" }
            compose.onNodeWithContentDescription("Remove ${third.first}").performClick()
            waitFor("upload cancelled", client) { client.files.firstOrNull { it.name == third.first }?.state == "cancelled" }
            val stopped = client.files.single { it.name == third.first }
            // Late answers to chunks already sent don't bring it back or move it on.
            runCatching { compose.waitUntil(3000) { client.files.single { it.name == third.first } != stopped } }
            assertEquals(stopped, client.files.single { it.name == third.first })
            assertTrue(stopped.bytes < stopped.size)

            // 4. A waiting message keeps through a lost connection and goes once after reconnecting.
            val fourth = download(app, "0-upload-$stamp-d.png", 4)
            pick(client, fourth.first)
            waitFor("fourth upload under way", client) { client.files.firstOrNull { it.name == fourth.first }?.state == "receiving" }
            compose.onNodeWithTag("remComposer").performTextInput("Sent across a reconnect")
            compose.onNodeWithContentDescription("Send").performClick()
            waitFor("fourth message waiting", client) { client.remHeld != null }
            compose.runOnUiThread { client.disconnect() }
            waitFor("offline", client) { !client.connected }
            compose.onNode(hasText("Not connected. Sends to Rem once its files upload.")).assertExists()
            screenshot("upload-offline.png", held = true)
            assertNotNull(client.remHeld)
            compose.runOnUiThread { client.connect() }
            waitFor("message sent after reconnecting", client, 90_000) { client.remHeld == null && replies() == 3 }
            assertEquals(1, sentBodies("Sent across a reconnect"))
            assertEquals("available", client.files.single { it.name == fourth.first }.state)
        } finally {
            downloads.forEach { app.contentResolver.delete(it, null, null) }
            compose.runOnUiThread { client.reset(); owners.clear() }
        }
    }

    /** A noise PNG, which doesn't compress, saved in Downloads. Returns its name and size. */
    private fun download(app: Application, name: String, seed: Long, side: Int = 440): Pair<String, Int> {
        val random = Random(seed)
        val pixels = IntArray(side * side) { 0xFF000000.toInt() or (random.nextInt() and 0xFFFFFF) }
        val bitmap = Bitmap.createBitmap(pixels, side, side, Bitmap.Config.ARGB_8888).apply { setHasAlpha(false) }
        val bytes = ByteArrayOutputStream().also { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }.toByteArray()
        bitmap.recycle()
        val values = ContentValues().apply { put(MediaStore.Downloads.DISPLAY_NAME, name); put(MediaStore.Downloads.MIME_TYPE, "image/png") }
        val uri = app.contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)!!
        downloads += uri
        app.contentResolver.openOutputStream(uri)!!.use { it.write(bytes) }
        // To compare with the SHA-256 of the copy the fixture's desktop stored.
        android.util.Log.i("RaticodeUpload", "$name: ${bytes.size} bytes, sha256 ${dev.raticode.mobile.data.FileMetadata.digest(bytes)}")
        return name to bytes.size
    }

    /** Picks [name] through the paperclip's Downloads, in Android's file browser. */
    private fun pick(client: LiveClient, name: String) {
        val app = instrumentation.targetContext.packageName
        waitFor("paperclip", client) { runCatching { compose.onNodeWithTag("remAttach").assertIsEnabled() }.isSuccess }
        compose.onNodeWithTag("remAttach").performClick()
        compose.onNodeWithTag("attachDownloads").performScrollTo().performClick()
        compose.waitUntil(20_000) { automation.rootInActiveWindow?.packageName?.toString()?.contains("documentsui") == true }
        try {
            awaitText(name)
            // In grid view a tap on a picture opens Android's preview of it; in list view it picks the file.
            automation.rootInActiveWindow?.let { root -> described(root, "List view") }?.let { tap(it) }
            tapText(name)
        } catch (failure: Throwable) { screenshot("upload-pick-failed.png"); throw failure }
        compose.waitUntil(20_000) { automation.rootInActiveWindow?.packageName?.toString() == app }
        waitFor("chip for $name", client) { client.files.any { it.name == name } }
    }

    private fun awaitText(text: String) {
        compose.waitUntil(15_000) { automation.rootInActiveWindow?.findAccessibilityNodeInfosByText(text)?.isNotEmpty() == true }
    }

    private fun tapText(text: String) {
        var target: AccessibilityNodeInfo? = null
        compose.waitUntil(15_000) {
            target = automation.rootInActiveWindow?.takeIf { it.packageName?.toString() != instrumentation.targetContext.packageName }
                ?.findAccessibilityNodeInfosByText(text)?.firstOrNull { it.text?.toString() == text && it.isVisibleToUser }
            target != null
        }
        tap(target!!)
    }

    private fun tap(node: AccessibilityNodeInfo) {
        val bounds = android.graphics.Rect().also { node.getBoundsInScreen(it) }
        automation.executeShellCommand("input tap ${bounds.centerX()} ${bounds.centerY()}").close()
        compose.waitForIdle()
    }

    private fun described(node: AccessibilityNodeInfo, description: String): AccessibilityNodeInfo? {
        if (node.contentDescription?.toString() == description && node.isVisibleToUser) return node
        return (0 until node.childCount).firstNotNullOfOrNull { i -> node.getChild(i)?.let { described(it, description) } }
    }
}
