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

class LiveWorkspaceTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()

    @Test fun desktopHistoryModelSelectionAndPhoneCreationUseTheSameConversation() {
        val path = InstrumentationRegistry.getArguments().getString("workspaceInvitationFile")
        assumeTrue("Requires disposable fixture with --workspace", path != null)
        val landscape = InstrumentationRegistry.getArguments().getString("workspaceOrientation") == "landscape"
        compose.runOnUiThread {
            compose.activity.requestedOrientation = if (landscape) android.content.pm.ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE else android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
        }
        compose.waitUntil(10000) { compose.activity.resources.configuration.orientation ==
            if (landscape) android.content.res.Configuration.ORIENTATION_LANDSCAPE else android.content.res.Configuration.ORIENTATION_PORTRAIT }
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        DeviceStore(app).reset()
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app)
            owners.put("workspace", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client, startPage = "Threads") } }
            client.preview(File(path!!).readText()); client.pair()
        }
        try {
            compose.waitUntil(30000) { client.connected && client.threads.isNotEmpty() }
            compose.onNodeWithTag("messageComposer").assertDoesNotExist()
            compose.onNodeWithText("Desktop project chat").performClick()
            compose.waitUntil(15000) { client.messages.any { it.text == "The desktop history is here." } }
            compose.waitUntil(15000) { client.providers["codex"]?.contains("fixture-b") == true }
            val thread = client.threads.single().first
            compose.onNodeWithTag("messageComposer").assertIsDisplayed()
            compose.onNodeWithContentDescription("Thread settings").performClick()
            compose.onNodeWithText("fixture-b", substring = false).performClick()
            compose.onNodeWithText("Apply", substring = false).performClick()
            compose.waitUntil(10000) { client.threadContexts[thread]?.model == "fixture-b" }
            assertEquals(thread, client.threads.single().first)
            compose.onNodeWithTag("messageComposer").performTextInput("Continue my desktop thread")
            compose.onNodeWithContentDescription("Send message").performClick()
            compose.waitUntil(15000) { client.messages.any { it.text == "Disposable fixture response" } }
            compose.runOnUiThread { client.refresh() }
            Thread.sleep(1500)
            assertEquals(1, client.messages.count { it.text == "Disposable fixture response" })
            compose.runOnUiThread { compose.activity.window.clearFlags(android.view.WindowManager.LayoutParams.FLAG_SECURE) }
            compose.waitForIdle()
            InstrumentationRegistry.getInstrumentation().uiAutomation.takeScreenshot().let { bitmap ->
                File(app.getExternalFilesDir(null), "shared-chat.png").outputStream().use { bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it) }
                bitmap.recycle()
            }
            InstrumentationRegistry.getInstrumentation().uiAutomation.executeShellCommand("screencap -p /data/local/tmp/raticode-shared-chat.png").use {
                android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
            }
            compose.runOnUiThread {
                (compose.activity.getSystemService(android.content.Context.INPUT_METHOD_SERVICE) as android.view.inputmethod.InputMethodManager)
                    .hideSoftInputFromWindow(compose.activity.window.decorView.windowToken, 0)
            }
            compose.waitForIdle()
            Thread.sleep(500)
            compose.onNodeWithContentDescription("Threads").assertIsDisplayed()
            InstrumentationRegistry.getInstrumentation().uiAutomation.executeShellCommand("screencap -p /data/local/tmp/raticode-shared-chat-full.png").use {
                android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
            }
            compose.runOnUiThread { compose.activity.window.addFlags(android.view.WindowManager.LayoutParams.FLAG_SECURE) }
            compose.onNodeWithContentDescription("Threads").performClick()
            compose.onNodeWithContentDescription("Project scope").performClick()
            compose.onNodeWithText("Fixture project", substring = false).performClick()
            compose.onNodeWithContentDescription("New thread").performClick()
            compose.waitUntil(15000) { client.threads.size == 2 }
            compose.onNodeWithTag("messageComposer").performTextInput("A new conversation")
            compose.onNodeWithContentDescription("Send message").performClick()
            compose.waitUntil(15000) { client.messages.count { it.text == "Disposable fixture response" } == 2 }
        } finally {
            compose.runOnUiThread { owners.clear() }
            DeviceStore(app).reset()
        }
    }
}
