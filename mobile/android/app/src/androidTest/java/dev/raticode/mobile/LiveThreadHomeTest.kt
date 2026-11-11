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

/** Disposable desktop and real pinned TLS, with canned replies and no provider execution. */
class LiveThreadHomeTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()

    @Test fun compactHomeOrganizationAndUnnamedCreation() {
        val args = InstrumentationRegistry.getArguments()
        val path = args.getString("threadHomeInvitationFile")
        assumeTrue("Requires --parity --threads fixture", path != null)
        val landscape = args.getString("threadHomeOrientation") == "landscape"
        compose.runOnUiThread { compose.activity.requestedOrientation = if (landscape) android.content.pm.ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE else android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT }
        compose.waitUntil(10000) { compose.activity.resources.configuration.orientation ==
            if (landscape) android.content.res.Configuration.ORIENTATION_LANDSCAPE else android.content.res.Configuration.ORIENTATION_PORTRAIT }
        compose.waitForIdle()
        Thread.sleep(500)
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        DeviceStore(app).reset()
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app); owners.put("home", client)
            compose.activity.setContent { RaticodeTheme(darkTheme = true) { LiveScreen(liveClient = client, startPage = "Threads") } }
            client.preview(File(path!!).readText()); client.pair()
        }
        fun capture(name: String) {
            compose.waitForIdle(); Thread.sleep(500)
            val automation = InstrumentationRegistry.getInstrumentation().uiAutomation
            val output = "/sdcard/Download/thread-home-${if (landscape) "landscape" else "portrait"}-$name.png"
            android.os.ParcelFileDescriptor.AutoCloseInputStream(automation.executeShellCommand("screencap -p $output")).use { it.readBytes() }
        }
        fun hideKeyboard() {
            compose.runOnUiThread { (compose.activity.getSystemService(android.content.Context.INPUT_METHOD_SERVICE) as android.view.inputmethod.InputMethodManager).hideSoftInputFromWindow(compose.activity.window.decorView.windowToken, 0) }
            Thread.sleep(500)
        }
        try {
            compose.waitUntil(30000) { client.connected && client.threads.size == 3 && client.providers.isNotEmpty() }
            hideKeyboard()
            capture("home")
            compose.onNodeWithText("Conversation name").assertDoesNotExist()
            compose.onNodeWithText("New conversation").assertDoesNotExist()
            compose.onNodeWithTag("threadList").performScrollToNode(hasTestTag("group-pinned"))
            compose.onNodeWithTag("group-pinned").assertIsDisplayed()
            compose.onNodeWithTag("threadList").performScrollToNode(hasText("Active desktop thread"))
            compose.onNodeWithText("Active desktop thread").assertIsDisplayed()
            compose.onNodeWithTag("newThreadComposer").assertIsDisplayed()
            capture("home")
            val active = client.threads.first { it.second == "Active desktop thread" }.first
            compose.onNodeWithTag("threadList").performScrollToNode(hasContentDescription("Thread options for Active desktop thread"))
            compose.onNodeWithContentDescription("Thread options for Active desktop thread").performClick()
            capture("menu")
            compose.onNodeWithText("Pin thread", substring = false).performClick()
            compose.waitUntil(10000) { client.threadContexts[active]?.group == "pinned" }
            compose.onNodeWithTag("threadList").performScrollToNode(hasContentDescription("Thread options for Active desktop thread"))
            compose.onNodeWithContentDescription("Thread options for Active desktop thread").performClick()
            compose.onNodeWithText("Unpin thread", substring = false).performClick()
            compose.waitUntil(10000) { client.threadContexts[active]?.group == "active" }
            compose.onNodeWithTag("threadList").performScrollToNode(hasContentDescription("Archive thread Active desktop thread"))
            compose.onNodeWithContentDescription("Archive thread Active desktop thread").performClick()
            compose.waitUntil(10000) { client.threadContexts[active]?.group == "archived" }
            compose.onNodeWithTag("threadList").performScrollToNode(hasTestTag("group-archived"))
            compose.onNodeWithTag("group-archived").performClick()
            compose.onNodeWithTag("threadList").performScrollToNode(hasContentDescription("Thread options for Active desktop thread"))
            compose.onNodeWithContentDescription("Thread options for Active desktop thread").performClick()
            compose.onNodeWithText("Restore thread", substring = false).performClick()
            compose.waitUntil(10000) { client.threadContexts[active]?.group == "active" }
            compose.onNodeWithTag("threadList").performScrollToNode(hasTestTag("group-archived"))
            compose.onNodeWithTag("group-archived").performClick()
            compose.onNodeWithContentDescription("Project scope").performClick()
            compose.onNodeWithText("Code", substring = false).performClick()
            compose.onNodeWithContentDescription("Model").performClick()
            compose.onNodeWithText("fixture-b", substring = false).performClick()
            compose.onNodeWithContentDescription("Effort").performClick()
            compose.onNodeWithText("High", substring = false).performClick()
            compose.onNodeWithContentDescription("Permissions").performClick()
            capture("permissions")
            compose.onNodeWithText("Full access", substring = false).performClick()
            compose.runOnUiThread { client.disconnect() }
            compose.waitUntil(5000) { !client.connected }
            compose.onNodeWithTag("newThreadComposer").performTextInput("One two three four five six seven eight nine ten")
            Thread.sleep(1000)
            compose.onNodeWithTag("newThreadComposer").assertIsFocused().performTextInput(" extra")
            capture("keyboard")
            compose.onNodeWithTag("newThreadComposer").assertIsDisplayed()
            compose.onNodeWithContentDescription("Queue message").assertIsDisplayed().performClick()
            hideKeyboard()
            compose.runOnUiThread { client.connect() }
            compose.waitUntil(30000) { client.threads.any { it.second == "One two three four five six seven eight..." } }
            val created = client.threads.first { it.second == "One two three four five six seven eight..." }.first
            compose.waitUntil(30000) { client.messages.any { it.thread == created && it.text == "Disposable fixture response" } }
            val info = client.threadContexts.getValue(created)
            assertEquals("Code", client.projects[info.project]); assertEquals("fixture-b", info.model)
            assertEquals("high", info.effort); assertEquals("danger-full-access", info.permission)
            compose.onNodeWithTag("messageComposer").assertIsDisplayed()
            capture("created")
            compose.runOnUiThread { client.disconnect(); client.connect() }
            compose.waitUntil(15000) { client.connected }
            Thread.sleep(1500)
            assertEquals(1, client.messages.count { it.thread == created && it.text == "One two three four five six seven eight nine ten extra" })
            compose.onNodeWithContentDescription("Threads", useUnmergedTree = true).performClick()
            compose.onNodeWithContentDescription("Thread options for One two three four five six seven eight...").performClick()
            compose.onNodeWithText("Delete thread", substring = false).performClick()
            compose.onNodeWithText("Cancel", substring = false).performClick()
            assertTrue(client.threads.any { it.first == created })
            compose.onNodeWithContentDescription("Thread options for One two three four five six seven eight...").performClick()
            compose.onNodeWithText("Delete thread", substring = false).performClick()
            capture("delete-confirmation")
            compose.onNodeWithText("Delete", substring = false).performClick()
            compose.waitUntil(10000) { client.threadContexts[created]?.deletePending == true }
            capture("delete-pending")
            compose.waitUntil(20000) { client.threads.none { it.first == created } }
            capture("finished")
        } finally { compose.runOnUiThread { owners.clear() } }
    }
}
