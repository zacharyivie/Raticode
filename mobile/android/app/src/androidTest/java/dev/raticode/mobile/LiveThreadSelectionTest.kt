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
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import java.io.File

/**
 * Selection, read and archive actions against a disposable desktop over pinned TLS
 * (`scripts/live-device-fixture.py --parity --threads --read-sync`). Marking a thread unread
 * only shows after the desktop accepted it and synced it back, so those steps prove the
 * phone-to-desktop read path. The fixture writes the desktop's final read state to
 * raticode-test-thread-read.json in its output folder on the host.
 */
class LiveThreadSelectionTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()

    @Test fun selectReadAndArchiveThreadsFromTheList() {
        val path = InstrumentationRegistry.getArguments().getString("threadSelectionInvitationFile")
        assumeTrue("Requires --parity --threads --read-sync fixture", path != null)
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        DeviceStore(app).reset()
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app); owners.put("selection", client)
            compose.activity.setContent { RaticodeTheme(darkTheme = true) { LiveScreen(liveClient = client, startPage = "Threads") } }
            client.preview(File(path!!).readText()); client.pair()
        }
        fun capture(name: String) {
            compose.waitForIdle(); Thread.sleep(400)
            val automation = InstrumentationRegistry.getInstrumentation().uiAutomation
            android.os.ParcelFileDescriptor.AutoCloseInputStream(automation.executeShellCommand("screencap -p /data/local/tmp/raticode-selection-$name.png")).use { it.readBytes() }
        }
        fun id(title: String) = client.threads.first { it.second == title }.first
        fun scrollTo(matcher: SemanticsMatcher) = compose.onNodeWithTag("threadList").performScrollToNode(matcher)
        fun longPress(title: String) { scrollTo(hasText(title)); compose.onNodeWithText(title).performTouchInput { longClick() } }
        fun more(action: String) {
            compose.onNodeWithContentDescription("More actions for selected threads").performClick()
            compose.onNodeWithText(action, substring = false).performClick()
        }
        try {
            compose.waitUntil(30000) { client.connected && client.threads.size == 3 && client.threadReadSync }
            val active = id("Active desktop thread"); val pinned = id("Pinned desktop thread"); val archived = id("Archived desktop thread")
            compose.waitUntil(10000) { client.threadContexts[active]?.unread == true }
            capture("start")

            // The Unread header selects its section, and the bar marks the selection read.
            scrollTo(hasContentDescription("Unread, 1 thread"))
            compose.onNode(hasContentDescription("Unread, 1 thread")).performClick()
            compose.onNodeWithText("1 selected").assertIsDisplayed()
            capture("unread-selected")
            compose.onNodeWithText("Mark read").performClick()
            compose.waitUntil(10000) { client.threadContexts[active]?.unread == false }
            compose.onNodeWithTag("threadSelectionBar").assertDoesNotExist()

            // Mark as unread comes back from the desktop, so this checks the read path both ways.
            longPress("Active desktop thread")
            more("Mark as unread")
            compose.waitUntil(15000) { client.threadContexts[active]?.unread == true }
            // Mark all read clears the whole Unread section in one step.
            scrollTo(hasContentDescription("Mark all 1 unread thread as read"))
            compose.onNodeWithContentDescription("Mark all 1 unread thread as read").performClick()
            compose.waitUntil(10000) { client.threadContexts[active]?.unread == false }

            // A long press starts selecting; a tap then adds another thread instead of opening it.
            longPress("Pinned desktop thread")
            scrollTo(hasText("Active desktop thread"))
            compose.onNodeWithText("Active desktop thread").performClick()
            compose.onNodeWithText("2 selected").assertIsDisplayed()
            capture("two-selected")
            compose.onNodeWithContentDescription("Archive selected threads").performClick()
            compose.waitUntil(15000) { listOf(active, pinned).all { client.threadContexts[it]?.group == "archived" } }

            // The Archived header opens its section and selects every thread in it; Back clears it.
            scrollTo(hasContentDescription("Archived, 3 threads"))
            compose.onNode(hasContentDescription("Archived, 3 threads")).performClick()
            compose.onNodeWithText("3 selected").assertIsDisplayed()
            compose.onNodeWithContentDescription("Show archived threads").assertDoesNotExist()
            capture("archived-selected")
            compose.runOnUiThread { compose.activity.onBackPressedDispatcher.onBackPressed() }
            compose.waitForIdle()
            compose.onNodeWithTag("threadSelectionBar").assertDoesNotExist()
            for ((name, thread) in listOf("active" to active, "pinned" to pinned, "archived" to archived))
                org.junit.Assert.assertEquals("$name thread read state ${client.threadContexts[thread]}", false, client.threadContexts[thread]?.unread)
            // Let the desktop's final state reach the fixture's report.
            Thread.sleep(3000)
        } finally {
            compose.runOnUiThread { owners.clear() }
        }
    }
}
