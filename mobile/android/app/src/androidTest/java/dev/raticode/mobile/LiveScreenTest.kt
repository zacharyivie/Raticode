package dev.raticode.mobile

import android.app.Application
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.unit.dp
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

class LiveScreenTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()

    @Test fun pairedLaunchOpensRemWithThreadsBesideItOnTheLeft() {
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        val store = DeviceStore(app)
        store.reset()
        store.save(store.load().put("peer", JSONObject())
            .put("threads", JSONArray().put(JSONObject().put("thread_id", java.util.UUID.randomUUID().toString()).put("title", "Existing desktop thread"))))
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app)
            owners.put("screen", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client) } }
        }
        try {
            compose.waitUntil(10000) { client.paired }
            val bar = hasAnyAncestor(hasTestTag("desktopNavigation"))
            val left = listOf("Rem", "Threads", "Organizations", "Swarms", "More").map {
                compose.onNode(hasContentDescription(it) and bar).getUnclippedBoundsInRoot().left
            }
            org.junit.Assert.assertEquals(left.sorted(), left)
            compose.onNode(hasContentDescription("Rem") and bar).assertIsSelected()
            compose.onNode(hasContentDescription("Threads") and bar).assertIsNotSelected().performClick()
            compose.onNodeWithText("Existing desktop thread").assertIsDisplayed()
            compose.activity.runOnUiThread { compose.activity.onBackPressedDispatcher.onBackPressed() }
            compose.onNode(hasContentDescription("Rem") and bar).assertIsSelected()
        } finally {
            compose.runOnUiThread { owners.clear() }
            DeviceStore(app).reset()
        }
    }

    @Test fun narrowLiveComposerKeepsDraftAndOffersSafeCleanup() {
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        val store = DeviceStore(app)
        store.reset()
        val thread = java.util.UUID.randomUUID().toString()
        val otherThread = java.util.UUID.randomUUID().toString()
        val notification = mutableStateOf<String?>(thread)
        store.save(store.load().put("peer", JSONObject())
            .put("threads", JSONArray().put(JSONObject().put("thread_id", thread).put("title", "Existing desktop thread"))
                .put(JSONObject().put("thread_id", otherThread).put("title", "Another desktop thread")))
            .put("drafts", JSONObject().put(otherThread, "Saved draft in the notified thread")))
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app)
            owners.put("screen", client)
            compose.activity.setContent {
                RaticodeTheme { Box(Modifier.width(320.dp)) { LiveScreen({}, initialThread = notification.value, liveClient = client) } }
            }
        }
        try {
            compose.waitUntil(10000) { client.paired }
            compose.onNodeWithTag("messageComposer").performTextInput("Keep this draft")
            compose.onNodeWithContentDescription("Queue message").assertIsDisplayed()
            // Disposable fixture image only. The production window remains protected.
            compose.runOnUiThread { compose.activity.window.clearFlags(android.view.WindowManager.LayoutParams.FLAG_SECURE) }
            compose.waitForIdle()
            Thread.sleep(300)
            InstrumentationRegistry.getInstrumentation().uiAutomation.takeScreenshot().let { bitmap ->
                java.io.File(app.getExternalFilesDir(null), "live-composer.png").outputStream().use {
                    bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it)
                }
                bitmap.recycle()
            }
            compose.runOnUiThread { compose.activity.window.addFlags(android.view.WindowManager.LayoutParams.FLAG_SECURE) }
            compose.onNodeWithText("Files", substring = false).performClick()
            compose.onNodeWithText("Clear completed local history").performScrollTo().performClick()
            compose.onNodeWithText("Keep history").performClick()
            compose.onNodeWithText("Desktop", substring = false).performClick()
            compose.onNodeWithText("Threads", substring = false).performClick()
            compose.onNodeWithText("Existing desktop thread").performClick()
            compose.onNodeWithTag("messageComposer").assertTextContains("Keep this draft")
            compose.onNodeWithText("Files", substring = false).performClick()
            compose.onNodeWithText("Desktop", substring = false).performClick()
            compose.onNodeWithText("Enable response notifications").performScrollTo().assertIsDisplayed()
            compose.runOnUiThread { notification.value = otherThread }
            compose.onNodeWithTag("messageComposer").assertTextContains("Saved draft in the notified thread")
            compose.onNodeWithText("Keep this draft", substring = false).assertDoesNotExist()
            compose.onNodeWithText("Files", substring = false).performClick()
            compose.onNodeWithText("Desktop", substring = false).performClick()
            compose.onNodeWithText("Forget pairing on this phone").performScrollTo().performClick()
            compose.onNodeWithText("Forget", substring = false).performClick()
            compose.waitUntil(10000) { !client.paired }
            compose.onNodeWithText("Saved draft in the notified thread", substring = false).assertDoesNotExist()
        } finally {
            compose.runOnUiThread { owners.clear() }
            DeviceStore(app).reset()
        }
    }
}
