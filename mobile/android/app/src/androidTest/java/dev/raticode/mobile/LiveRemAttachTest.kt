package dev.raticode.mobile

import android.Manifest
import android.app.Application
import android.view.accessibility.AccessibilityNodeInfo
import androidx.activity.compose.setContent
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.lifecycle.ViewModelStore
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.data.CameraCaptures
import dev.raticode.mobile.live.*
import dev.raticode.mobile.ui.LiveScreen
import dev.raticode.mobile.ui.RaticodeTheme
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import java.io.File

/**
 * The paperclip against `live-device-fixture.py --workspace --remote --desktop`: a file picked in Android's file
 * browser and a photo from the camera upload over pinned TLS, show as chips, and go to Rem in one message. The
 * fixture's desktop stores both through its real attachment code and writes their names to raticode-test-desktop.json.
 */
class LiveRemAttachTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val automation get() = instrumentation.uiAutomation

    private fun waitFor(what: String, client: LiveClient, timeout: Long = 30000, condition: () -> Boolean) {
        try { compose.waitUntil(timeout, condition) }
        catch (failure: ComposeTimeoutException) { throw AssertionError("$what: files=${client.files}; status=${client.status}", failure) }
    }

    private fun shell(command: String) = automation.executeShellCommand(command).close()

    private fun screenshot(name: String) {
        compose.waitForIdle()
        automation.executeShellCommand("screencap -p /data/local/tmp/raticode-$name").use {
            android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
        }
    }

    @Test fun aPickedFileAndAPhotoReachRemInOneMessage() {
        val path = InstrumentationRegistry.getArguments().getString("attachInvitationFile")
        assumeTrue("Requires a disposable fixture with --workspace --remote --desktop", path != null)
        compose.runOnUiThread { compose.activity.requestedOrientation = android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT }
        val app = instrumentation.targetContext.applicationContext as Application
        DeviceStore(app).reset()
        // Named to sort first in Download, so it shows without scrolling. A system file gives it real bytes.
        val name = "0-rem-attach-${System.currentTimeMillis()}.txt"
        shell("cp /system/etc/hosts /sdcard/Download/$name")
        automation.grantRuntimePermission(app.packageName, Manifest.permission.CAMERA)
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app)
            owners.put("attach", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client, startPage = "Threads") } }
            client.preview(File(path!!).readText()); client.pair()
        }
        try {
            waitFor("desktop control", client) { client.connected && client.desktopControl }
            compose.waitUntil(5000) { compose.onAllNodesWithContentDescription("Rem").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithContentDescription("Rem").performClick()
            waitFor("Rem's file channel", client) { (client.desktopViews["rem.home"] as? RemHome)?.filesThread != null }

            // A file from Downloads, through All files in Android's file browser.
            waitFor("paperclip", client) { runCatching { compose.onNodeWithTag("remAttach").assertIsEnabled() }.isSuccess }
            compose.onNodeWithTag("remAttach").performClick()
            compose.onNodeWithText("Up to 8 files, 10 MB each").assertExists()
            compose.onNodeWithTag("attachFiles").performScrollTo().performClick()
            awaitWindow { it.contains("documentsui") }
            awaitText("Files on")
            tapText("Download")
            awaitText("Files in Download")
            tapText(name)
            awaitWindow { it == app.packageName }
            waitFor("file upload", client) { client.files.any { it.name == name && it.state == "available" } }
            compose.onNodeWithText(name).assertExists()

            // A photo from the camera, which never reaches the gallery and is deleted once read.
            compose.onNodeWithTag("remAttach").performClick()
            compose.onNodeWithText("Up to 7 more files, 10 MB each").assertExists()
            compose.onNodeWithTag("attachCamera").performClick()
            awaitWindow { it != app.packageName }
            for (attempt in 1..3) {
                tapDescription("Shutter")
                if (runCatching { tapDescription("Done", 8_000) }.isSuccess) break
            }
            awaitWindow { it == app.packageName }
            waitFor("photo upload", client) { client.files.count { it.state == "available" && it.name.startsWith("Photo ") } == 1 }
            val photo = client.files.single { it.name.startsWith("Photo ") }
            assertTrue(photo.name.endsWith(".jpg"))
            assertTrue(CameraCaptures.directory(app.cacheDir).listFiles().orEmpty().isEmpty())
            screenshot("attach-chips.png")

            // Both go to Rem with the note in one message.
            waitFor("connection", client) { client.connected }
            compose.onNodeWithTag("remComposer").performTextInput("Here are the hosts file and a photo")
            compose.onNodeWithContentDescription("Send").performClick()
            waitFor("Rem reply", client) { (client.desktopViews["rem.home"] as? RemHome)?.messages?.any { it.body == "Fixture Rem reply" } == true }
            // The chips are gone from the message box, so all eight places are free again.
            compose.onNodeWithTag("remAttach").performClick()
            compose.onNodeWithText("Up to 8 files, 10 MB each").assertExists()
            compose.onNodeWithTag("remAttachSheet").performTouchInput { swipeDown() }
            screenshot("attach-sent.png")
        } finally {
            shell("rm -f /sdcard/Download/$name")
            compose.runOnUiThread { client.reset(); owners.clear() }
        }
    }

    private fun awaitWindow(match: (String) -> Boolean) {
        compose.waitUntil(20_000) { automation.rootInActiveWindow?.packageName?.toString()?.let(match) == true }
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
        val bounds = android.graphics.Rect().also { target!!.getBoundsInScreen(it) }
        shell("input tap ${bounds.centerX()} ${bounds.centerY()}")
    }

    private fun tapDescription(description: String, timeout: Long = 20_000) {
        automation.serviceInfo = automation.serviceInfo.apply { flags = flags or android.accessibilityservice.AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS }
        var target: AccessibilityNodeInfo? = null
        compose.waitUntil(timeout) {
            target = (listOfNotNull(automation.rootInActiveWindow) + automation.windows.mapNotNull { it.root })
                .filter { it.packageName?.toString() != instrumentation.targetContext.packageName }
                .firstNotNullOfOrNull { described(it, description) }
            target != null
        }
        val bounds = android.graphics.Rect().also { target!!.getBoundsInScreen(it) }
        shell("input tap ${bounds.centerX()} ${bounds.centerY()}")
    }

    private fun described(node: AccessibilityNodeInfo, description: String): AccessibilityNodeInfo? {
        if (node.contentDescription?.toString() == description && node.isVisibleToUser) return node
        return (0 until node.childCount).firstNotNullOfOrNull { i -> node.getChild(i)?.let { described(it, description) } }
    }
}
