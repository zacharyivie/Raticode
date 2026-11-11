package dev.raticode.mobile

import android.app.Application
import android.content.ContentValues
import android.provider.MediaStore
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

/** Canned provider, real pinned TLS, real import/upload and durable replay. */
class LiveChatHistoryTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    @Test fun threadListThoughtDisclosuresAndAttachmentHistory() {
        val args = InstrumentationRegistry.getArguments()
        val path = args.getString("historyInvitationFile")
        assumeTrue("Requires disposable --history peer", path != null)
        val landscape = args.getString("historyOrientation") == "landscape"
        compose.runOnUiThread { compose.activity.requestedOrientation = if (landscape) 0 else 1 }
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        DeviceStore(app).reset()
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app); owners.put("history", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client, startPage = "Threads") } }
            client.preview(File(path!!).readText()); client.pair()
        }
        fun capture(name: String) {
            compose.waitForIdle()
            Thread.sleep(400)
            val automation = InstrumentationRegistry.getInstrumentation().uiAutomation
            android.os.ParcelFileDescriptor.AutoCloseInputStream(automation.executeShellCommand("screencap -p /sdcard/Download/history-${if (landscape) "landscape" else "portrait"}-$name.png")).use { it.readBytes() }
        }
        val resolver = app.contentResolver
        val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, ContentValues().apply {
            put(MediaStore.Downloads.DISPLAY_NAME, "README-mobile-fixture.md")
            put(MediaStore.Downloads.MIME_TYPE, "text/markdown")
        })!!
        resolver.openOutputStream(uri)!!.use { it.write("# File attached on phone\n".toByteArray()) }
        try {
            compose.waitUntil(30000) { client.connected && client.threads.isNotEmpty() }
            compose.onNodeWithTag("threadList").assertIsDisplayed()
            compose.onNodeWithTag("messageComposer").assertDoesNotExist()
            capture("threads")
            compose.runOnUiThread { client.refresh() }
            Thread.sleep(1500)
            compose.onNodeWithTag("messageComposer").assertDoesNotExist()
            compose.onNodeWithText("Desktop project chat").performClick()
            val thread = client.threads.single().first
            compose.waitUntil(15000) { client.messages.any { it.kind == "thought" } }
            compose.onNodeWithTag("liveMessages").performScrollToNode(hasText("Show thoughts"))
            // Hidden desktop context neither shows nor splits the thoughts into two groups.
            compose.onAllNodesWithText("Show thoughts").assertCountEquals(1)
            compose.onNodeWithText("Compacted earlier conversation").assertDoesNotExist()
            compose.onNodeWithText("Continuation of the previous turn").assertDoesNotExist()
            compose.onNodeWithText("Show thoughts").performClick()
            compose.onNodeWithTag("liveMessages").performScrollToNode(hasText("Prioritizing skill review"))
            compose.onNodeWithText("Prioritizing skill review").assertIsDisplayed()
            compose.onNodeWithText("**Prioritizing skill review**").assertDoesNotExist()
            capture("thought-markers")
            compose.onNodeWithTag("liveMessages").performScrollToNode(hasText("Running bash commands"))
            compose.onNodeWithText("Running bash commands").performClick()
            compose.onNodeWithTag("liveMessages").performScrollToNode(hasText("/fixture"))
            compose.onNodeWithText("/fixture").assertIsDisplayed()
            capture("thoughts")
            compose.onNodeWithTag("liveMessages").performScrollToNode(hasText("Hide thoughts"))
            compose.onNodeWithText("Hide thoughts").performClick()
            compose.onNodeWithTag("liveMessages").performScrollToNode(hasText("The desktop history is here."))
            capture("collapsed")
            var attachment: String? = null
            compose.runOnUiThread { client.importFile(thread, uri) { attachment = it } }
            compose.waitUntil(15000) { attachment != null && client.files.any { it.id == attachment && it.state == "available" } }
            compose.onNodeWithText("Files", substring = false).performClick()
            compose.onNodeWithText("Attach to message").performScrollTo().performClick()
            compose.onNodeWithContentDescription("Send message").performClick()
            compose.waitUntil(15000) { client.messages.any { m -> m.attachments.any { it.id == attachment } && m.state == "Completed" } }
            compose.waitUntil(15000) { client.messages.any { it.text == "Disposable fixture response" } }
            compose.onNodeWithTag("liveMessages").performScrollToNode(hasText("README-mobile-fixture.md"))
            compose.onNodeWithText("README-mobile-fixture.md").assertIsDisplayed()
            capture("attachment")
            compose.runOnUiThread { client.refresh() }
            Thread.sleep(1500)
            assertEquals(1, client.messages.count { m -> m.attachments.any { it.id == attachment } })
            compose.onNodeWithContentDescription("Threads").performClick()
            compose.runOnUiThread { client.refresh() }
            Thread.sleep(1000)
            compose.onNodeWithTag("threadList").assertIsDisplayed()
            compose.onNodeWithTag("messageComposer").assertDoesNotExist()
            assertNull(client.requestError)
        } finally {
            compose.runOnUiThread { owners.clear() }
            resolver.delete(uri, null, null)
            DeviceStore(app).reset()
        }
    }
}
