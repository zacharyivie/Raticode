package dev.raticode.mobile

import android.Manifest
import android.accessibilityservice.AccessibilityService
import android.content.pm.PackageManager
import android.view.accessibility.AccessibilityNodeInfo
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.core.content.ContextCompat
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.data.AttachPlan
import dev.raticode.mobile.data.CameraCaptures
import dev.raticode.mobile.data.DocumentImport
import dev.raticode.mobile.ui.AttachButton
import dev.raticode.mobile.ui.AttachNotice
import dev.raticode.mobile.ui.Picked
import dev.raticode.mobile.ui.RaticodeTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/**
 * The paperclip's menu on a real Android: each entry opens the right system screen, backing out attaches nothing,
 * a file chosen in the file browser comes back, and the camera asks for permission, says so when refused, and returns
 * a photo the app can read.
 */
class RemAttachUiTest {
    @get:Rule val compose = createComposeRule()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val app = instrumentation.targetContext
    private val automation get() = instrumentation.uiAutomation

    @Test fun eachSourceOpensItsScreenAndBackingOutAttachesNothing() {
        // Named to sort first among Download's files, so it shows without scrolling on a small screen.
        val name = "0-rem-attach-check-${System.currentTimeMillis()}.txt"
        // The test shell splits on spaces and has no redirection, so the file is made empty.
        shell("touch /sdcard/Download/$name")
        val picked = mutableListOf<Picked>()
        var notice by mutableStateOf<AttachNotice?>(null)
        compose.setContent {
            RaticodeTheme { Surface(color = MaterialTheme.colorScheme.background) {
                AttachButton(enabled = true, room = 3, onPicked = { picked += it }, onNotice = { notice = it })
            } }
        }
        try {
            open()
            compose.onNodeWithText("Up to 3 more files, 10 MB each").assertExists()
            compose.onNodeWithText("In Android's file browser").assertExists()
            for (label in listOf("Photos and videos", "Camera", "Recent files", "Downloads", "All files")) compose.onNodeWithText(label).assertExists()
            compose.onNodeWithTag("attachFiles").performScrollTo().assertIsDisplayed()
            compose.waitForIdle()
            shell("screencap -p /data/local/tmp/raticode-attach-sheet.png")

            // Downloads opens Android's file browser at the Downloads folder.
            compose.onNodeWithTag("attachDownloads").performClick()
            awaitWindow { it.contains("documentsui") }
            awaitText("Downloads")
            back()
            assertTrue(picked.isEmpty()); assertNull(notice)

            // Recent files opens the same file browser.
            open(); compose.onNodeWithTag("attachRecent").performClick()
            awaitWindow { it.contains("documentsui") }
            back()

            // Photos and videos opens Android's photo picker.
            open(); compose.onNodeWithTag("attachMedia").performClick()
            awaitWindow { it.contains("photopicker") || it.contains("providers.media") || it.contains("documentsui") }
            back()
            assertTrue(picked.isEmpty()); assertNull(notice)

            // All files opens at phone storage; a file chosen there comes back to the composer.
            open(); compose.onNodeWithTag("attachFiles").performClick()
            awaitWindow { it.contains("documentsui") }
            // Wait for each folder to finish loading, or a tap lands on the previous screen.
            awaitText("Files on")
            tapText("Download")
            awaitText("Files in Download")
            // A tap while the list still settles can miss, so tap again while the file browser is still open.
            for (attempt in 1..3) {
                tapText(name)
                if (runCatching { compose.waitUntil(5_000) { picked.isNotEmpty() } }.isSuccess) break
            }
            assertEquals(1, picked.size)
            assertEquals("content", picked[0].uri.scheme)
            assertNull(notice)
            picked.clear()

            // The camera asks first. Refusing explains why nothing was attached.
            val granted = ContextCompat.checkSelfPermission(app, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
            if (!granted) {
                open(); compose.onNodeWithTag("attachCamera").performClick()
                awaitWindow { it.contains("permissioncontroller") }
                // The dialog can still be animating in, so tap again if the answer didn't arrive.
                for (attempt in 1..3) {
                    tapText("Don’t allow", "Don't allow", "Deny")
                    if (runCatching { compose.waitUntil(5_000) { notice != null } }.isSuccess) break
                }
                assertEquals(AttachPlan.CAMERA_DENIED, notice?.text)
                assertTrue(picked.isEmpty())
                automation.grantRuntimePermission(app.packageName, Manifest.permission.CAMERA)
            }
            // With permission, the camera app opens; backing out leaves no notice and no photo behind.
            open(); compose.onNodeWithTag("attachCamera").performClick()
            awaitWindow { !it.contains(app.packageName) && !it.contains("permissioncontroller") }
            back()
            compose.waitUntil(10_000) { CameraCaptures.directory(app.cacheDir).listFiles().orEmpty().isEmpty() }
            assertTrue(picked.isEmpty())
            assertTrue(notice == null || notice?.text == AttachPlan.CAMERA_DENIED)

            // A photo taken comes back under a dated name and reads through the same bounded import as any file.
            open(); compose.onNodeWithTag("attachCamera").performClick()
            awaitWindow { !it.contains(app.packageName) }
            // The emulator's camera can ignore a shutter press while it starts, so press again until it offers Done.
            for (attempt in 1..3) {
                tapDescription("Shutter")
                if (runCatching { tapDescription("Done", 8_000) }.isSuccess) break
            }
            compose.waitUntil(20_000) { picked.isNotEmpty() }
            assertTrue("The camera never offered Done", picked.isNotEmpty())
            val photo = picked.single()
            assertEquals(app.packageName + ".opened", photo.uri.authority)
            assertTrue(photo.name!!.matches(Regex("""Photo \d{4}-\d{2}-\d{2} \d{2}\.\d{2}\.\d{2}\.jpg""")))
            assertTrue(photo.photo!!.length() > 0)
            assertNull(notice)
            val read = java.util.concurrent.CompletableFuture<Result<DocumentImport.Prepared>>()
            instrumentation.runOnMainSync { DocumentImport(app.contentResolver).start(photo.uri, name = photo.name) { read.complete(it) } }
            read.get(30, java.util.concurrent.TimeUnit.SECONDS).getOrThrow().use { prepared ->
                assertEquals(photo.name, prepared.name)
                assertEquals(photo.photo!!.length(), prepared.bytes.size.toLong())
            }
            photo.photo!!.delete()
        } finally {
            shell("rm -f /sdcard/Download/$name")
        }
    }

    private fun open() {
        compose.waitUntil(15_000) { compose.onAllNodesWithContentDescription("Attach file").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithContentDescription("Attach file").performClick()
        compose.onNodeWithTag("remAttachSheet").assertExists()
    }

    private fun shell(command: String) = automation.executeShellCommand(command).close()

    /** Polls inside compose.waitUntil so Compose effects keep running while another app is in front. */
    private fun awaitWindow(match: (String) -> Boolean) {
        compose.waitUntil(20_000) { automation.rootInActiveWindow?.packageName?.toString()?.let(match) == true }
    }

    private fun awaitText(text: String) {
        compose.waitUntil(15_000) { automation.rootInActiveWindow?.findAccessibilityNodeInfosByText(text)?.isNotEmpty() == true }
    }

    private fun tapText(vararg texts: String) {
        var target: AccessibilityNodeInfo? = null
        compose.waitUntil(15_000) {
            val root = automation.rootInActiveWindow?.takeIf { it.packageName?.toString() != app.packageName }
            target = texts.firstNotNullOfOrNull { text ->
                root?.findAccessibilityNodeInfosByText(text)?.firstOrNull { it.text?.toString() == text && it.isVisibleToUser }
            }
            target != null
        }
        // Some file browser rows take touches without exposing a click action, so tap where the text is.
        val bounds = android.graphics.Rect().also { target!!.getBoundsInScreen(it) }
        shell("input tap ${bounds.centerX()} ${bounds.centerY()}")
    }

    private fun tapDescription(description: String, timeout: Long = 20_000) {
        automation.serviceInfo = automation.serviceInfo.apply { flags = flags or android.accessibilityservice.AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS }
        var target: AccessibilityNodeInfo? = null
        compose.waitUntil(timeout) {
            // Searching by text skips some camera controls, so walk every window's tree for the label.
            target = (listOfNotNull(automation.rootInActiveWindow) + automation.windows.mapNotNull { it.root }).filter { it.packageName?.toString() != app.packageName }
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

    /** Backs out of another app. Some pickers take a second Back, such as one still opening its sheet. */
    private fun back() {
        val home = { automation.rootInActiveWindow?.packageName?.toString() == app.packageName }
        for (attempt in 1..3) {
            automation.performGlobalAction(AccessibilityService.GLOBAL_ACTION_BACK)
            if (runCatching { compose.waitUntil(5_000) { home() } }.isSuccess) break
        }
        assertTrue("Still in another app after Back", home())
        compose.waitForIdle()
    }
}
