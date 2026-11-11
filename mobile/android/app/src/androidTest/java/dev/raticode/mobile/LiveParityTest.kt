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

/** Real pinned TLS against the disposable --parity desktop; no provider execution. */
class LiveParityTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    @Test fun scopeEffortGroupsFullHistorySearchAndScreenshots() {
        val args = InstrumentationRegistry.getArguments()
        val path = args.getString("parityInvitationFile")
        assumeTrue("Requires disposable --parity fixture", path != null)
        val landscape = args.getString("parityOrientation") == "landscape"
        compose.runOnUiThread { compose.activity.requestedOrientation = if (landscape) android.content.pm.ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE else android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT }
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        DeviceStore(app).reset()
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app); owners.put("parity", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client, startPage = "Threads") } }
            client.preview(File(path!!).readText()); client.pair()
        }
        fun capture(name: String) {
            compose.waitForIdle()
            Thread.sleep(700)
            assertEquals(0, compose.activity.window.attributes.flags and android.view.WindowManager.LayoutParams.FLAG_SECURE)
            val automation = InstrumentationRegistry.getInstrumentation().uiAutomation
            val output = "/sdcard/Download/parity-${if (landscape) "landscape" else "portrait"}-$name.png"
            android.os.ParcelFileDescriptor.AutoCloseInputStream(automation.executeShellCommand("screencap -p $output")).use { it.readBytes() }
        }
        fun hideKeyboard() {
            compose.runOnUiThread {
                (compose.activity.getSystemService(android.content.Context.INPUT_METHOD_SERVICE) as android.view.inputmethod.InputMethodManager).hideSoftInputFromWindow(compose.activity.window.decorView.windowToken, 0)
            }
            Thread.sleep(500)
        }
        try {
            compose.waitUntil(30000) { client.connected && client.threads.size == 3 && client.efforts.isNotEmpty() }
            compose.onNodeWithTag("messageComposer").assertDoesNotExist()
            compose.onNodeWithText("Pinned desktop thread").assertIsDisplayed()
            compose.onNodeWithText("Active desktop thread").assertIsDisplayed()
            val thread = client.threads.first { it.second == "Active desktop thread" }.first
            // Desktop sections: the unread desktop thread sits under Unread with its blue marker.
            compose.waitUntil(10000) { client.threadContexts[thread]?.unread == true }
            compose.onNodeWithTag("group-pinned").assertIsDisplayed()
            compose.onNodeWithTag("group-unread").assertIsDisplayed()
            compose.onNodeWithContentDescription("Rem response complete").assertIsDisplayed()
            capture("pinned")
            compose.onNodeWithText("Archived desktop thread").assertDoesNotExist()
            compose.onNodeWithTag("threadList").performScrollToNode(hasTestTag("group-archived"))
            compose.onNodeWithTag("group-archived").performClick()
            compose.onNodeWithTag("threadList").performScrollToNode(hasText("Archived desktop thread"))
            compose.onNodeWithText("Archived desktop thread").assertIsDisplayed()
            compose.onNodeWithContentDescription("Delete thread Archived desktop thread").assertExists()
            capture("archived")
            compose.onNodeWithTag("group-archived").performClick()
            compose.onNodeWithTag("threadList").performScrollToNode(hasText("Active desktop thread"))
            compose.onNodeWithText("Active desktop thread").performClick()
            compose.waitUntil(15000) { client.messages.count { it.thread == thread } >= 10 }
            assertFalse(client.messages.any { it.thread == thread && it.text.contains("buried README") })
            compose.onNodeWithContentDescription("Thread settings").performClick()
            compose.onNodeWithText("Code", substring = false).performScrollTo().performClick()
            compose.onNodeWithText("grok", substring = false).performScrollTo().assertIsEnabled()
            compose.onNodeWithText("antigravity", substring = false).performScrollTo().assertIsEnabled().performClick()
            compose.onNodeWithText("fixture-b", substring = false).performScrollTo().performClick()
            compose.onNodeWithText("high", substring = false).performScrollTo().performClick()
            capture("settings")
            compose.onNodeWithText("Apply", substring = false).performClick()
            compose.waitUntil(10000) { client.threadContexts[thread]?.let { it.provider == "antigravity" && it.model == "fixture-b" && it.effort == "high" && client.projects[it.project] == "Code" } == true }
            compose.onNodeWithContentDescription("Thread settings").performClick()
            compose.onNodeWithText("Global", substring = false).performScrollTo().performClick()
            compose.onNodeWithText("Apply", substring = false).performClick()
            compose.waitUntil(10000) { client.threadContexts[thread]?.project == null }
            assertEquals(3, client.threads.size)
            compose.onNodeWithTag("messageComposer").performTextInput("Verify the same desktop conversation")
            capture("keyboard")
            compose.onNodeWithContentDescription("Send message").performClick()
            hideKeyboard()
            compose.waitUntil(15000) { client.threadContexts[thread]?.running == true }
            capture("running")
            // Opening the chat marked it read on this phone; while Rem works it moves to Running.
            assertFalse(client.threadContexts[thread]?.unread == true)
            compose.onNodeWithContentDescription("Threads").performClick()
            compose.onNodeWithTag("group-running").assertIsDisplayed()
            compose.onNodeWithTag("group-unread").assertDoesNotExist()
            compose.onNodeWithTag("group-running").performClick()
            compose.onNodeWithContentDescription("Running").assertIsDisplayed()
            capture("running-home")
            compose.onNodeWithText("Active desktop thread").performClick()
            compose.waitUntil(20000) { client.messages.any { it.thread == thread && it.text == "Disposable fixture response" } }
            compose.onNodeWithContentDescription("Threads").performClick()
            compose.waitUntil(10000) { client.threadContexts[thread]?.running == false }
            compose.onNodeWithTag("group-read").assertIsDisplayed()
            compose.onNodeWithTag("group-running").assertDoesNotExist()
            compose.onNodeWithContentDescription("Search in threads").performClick()
            compose.onNodeWithTag("threadSearch").performTextInput("buried README")
            hideKeyboard()
            compose.waitUntil(15000) { client.searchResults.size == 3 && !client.searching }
            capture("search")
            compose.onNodeWithTag("threadList").performScrollToNode(hasText("Active desktop thread"))
            compose.onNodeWithText("Active desktop thread").performClick()
            compose.waitUntil(10000) { client.messages.any { it.thread == thread && it.text == "The buried README search result" } }
            compose.onNodeWithText("The buried README search result").assertIsDisplayed()
            capture("search-hit")
            assertNull(client.requestError)
        } finally {
            compose.runOnUiThread { owners.clear() }
            DeviceStore(app).reset()
        }
    }
}
