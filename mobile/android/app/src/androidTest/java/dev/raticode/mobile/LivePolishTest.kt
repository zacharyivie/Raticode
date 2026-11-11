package dev.raticode.mobile

import android.app.Application
import androidx.activity.compose.setContent
import androidx.compose.runtime.mutableStateOf
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.isImeVisible
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.lifecycle.ViewModelStore
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.DeviceStore
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.ui.LiveScreen
import dev.raticode.mobile.ui.RaticodeTheme
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Rule
import org.junit.Test

/** Disposable UI fixtures only. No live desktop, provider, or user history. */
class LivePolishTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()

    @OptIn(ExperimentalLayoutApi::class)
    @Test fun reviewChatThreadsFilesSettingsAndPairing() {
        val landscape = InstrumentationRegistry.getArguments().getString("polishOrientation") == "landscape"
        compose.runOnUiThread { compose.activity.requestedOrientation = if (landscape) android.content.pm.ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE else android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT }
        compose.waitUntil(10000) { compose.activity.resources.configuration.orientation == if (landscape) android.content.res.Configuration.ORIENTATION_LANDSCAPE else android.content.res.Configuration.ORIENTATION_PORTRAIT }
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        val store = DeviceStore(app)
        store.reset()
        val thread = java.util.UUID.randomUUID().toString()
        val project = java.util.UUID.randomUUID().toString()
        val dark = mutableStateOf(false)
        val threads = JSONArray().put(JSONObject().put("thread_id", thread).put("title", "Polish the mobile conversation")
            .put("project_id", project).put("provider", "codex").put("model", "gpt-6-astra").put("can_create", true))
            .put(JSONObject().put("thread_id", java.util.UUID.randomUUID().toString()).put("title", "Desktop connection checks"))
        val messages = JSONArray()
        fun message(id: String, text: String, state: String) {
            messages.put(JSONObject().put("id", id).put("thread", thread).put("text", text).put("state", state))
        }
        message("question", "Can you tidy up the conversation view and keep the project easy to find?", "completed")
        message("reply", "The conversation now has one compact header. Your project and model stay together, with files a tap away.\n\nI also kept more room for messages while the keyboard is open.", "Rem")
        store.save(store.load().put("peer", JSONObject()).put("threads", threads).put("messages", messages)
            .put("catalog", JSONObject().put("projects", JSONArray().put(JSONObject().put("id", project).put("name", "Raticode Mobile")))))
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        var imeVisible = false
        var dismissKeyboard: () -> Unit = {}
        compose.runOnUiThread {
            client = LiveClient(app)
            owners.put("polish", client)
            compose.activity.setContent {
                val showing = WindowInsets.isImeVisible
                SideEffect { imeVisible = showing }
                val keyboard = LocalSoftwareKeyboardController.current
                val focus = LocalFocusManager.current
                dismissKeyboard = { keyboard?.hide(); focus.clearFocus() }
                RaticodeTheme(darkTheme = dark.value) { LiveScreen(liveClient = client, startPage = "Threads") }
            }
            org.junit.Assert.assertEquals(0, compose.activity.window.attributes.flags and android.view.WindowManager.LayoutParams.FLAG_SECURE)
        }
        fun capture(name: String) {
            compose.waitForIdle()
            Thread.sleep(500)
            val bitmap = InstrumentationRegistry.getInstrumentation().uiAutomation.takeScreenshot()
            java.io.File(app.getExternalFilesDir(null), "polish-$name.png").outputStream().use {
                bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it)
            }
            bitmap.recycle()
            // Export disposable screenshots outside app storage, which Gradle removes after tests.
            val folder = if (landscape) "landscape" else "portrait"
            InstrumentationRegistry.getInstrumentation().uiAutomation.executeShellCommand(
                "mkdir -p /sdcard/Download/raticode-ui/$folder"
            ).close()
            val source = java.io.File(app.getExternalFilesDir(null), "polish-$name.png").absolutePath
            android.os.ParcelFileDescriptor.AutoCloseInputStream(InstrumentationRegistry.getInstrumentation().uiAutomation.executeShellCommand(
                "cp $source /sdcard/Download/raticode-ui/$folder/polish-$name.png"
            )).use { it.readBytes() }
        }
        try {
            compose.waitUntil(10000) { client.messages.size == 2 }
            compose.runOnUiThread { client.disconnect() }
            compose.onNodeWithTag("messageComposer").assertDoesNotExist()
            compose.onNodeWithText("Polish the mobile conversation").performClick()
            capture("chat-light")
            compose.onNodeWithTag("messageComposer").performClick().performTextInput("Keep the same thread and project.")
            compose.onNodeWithContentDescription("Queue message").assertIsDisplayed()
            compose.waitUntil(10000) { imeVisible }
            compose.onNodeWithTag("messageComposer").assertIsDisplayed().assertTextContains("Keep the same thread and project.")
            capture("keyboard")
            compose.runOnUiThread { dismissKeyboard() }
            compose.waitUntil(10000) { !imeVisible }
            compose.runOnUiThread { dark.value = true }
            capture("chat-dark")
            compose.onNodeWithText("Files", substring = false).performClick()
            compose.onNodeWithText("Thread files").assertIsDisplayed()
            capture("files-dark")
            compose.runOnUiThread { dark.value = false }
            capture("files-light")
            compose.onNodeWithText("Desktop", substring = false).performClick()
            capture("desktop")
            compose.onNodeWithText("Enable response notifications").performScrollTo().assertIsDisplayed()
            capture("notifications")
            compose.onNodeWithText("Threads", substring = false).performClick()
            capture("threads")
            compose.onNodeWithTag("newThreadComposer").assertIsDisplayed()
            compose.onNodeWithText("Conversation name").assertDoesNotExist()
            capture("new-conversation")
            compose.onNodeWithTag("threadList").performScrollToNode(hasText("Polish the mobile conversation"))
            compose.onNodeWithText("Polish the mobile conversation").performClick()
            compose.onNodeWithTag("messageComposer").assertIsDisplayed().assertTextContains("Keep the same thread and project.")
            compose.onNodeWithContentDescription("Threads").performClick()
            compose.onNodeWithContentDescription("Thread list options").performClick()
            compose.onNodeWithText("Desktop · Offline", substring = false).performClick()
            compose.onNodeWithText("Forget pairing on this phone").performScrollTo().performClick()
            compose.onNodeWithText("Forget", substring = false).performClick()
            compose.waitUntil(10000) { !client.paired }
            capture("pairing")
        } finally {
            compose.runOnUiThread {
                owners.clear()
            }
            DeviceStore(app).reset()
        }
    }
}
