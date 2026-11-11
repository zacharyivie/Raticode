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
import java.util.concurrent.atomic.AtomicInteger

/** Actual Android/Python pinned TLS over Wi-Fi, dispatch bridge and durable journal, on
 * disposable sockets. No public service or paid provider is contacted.
 */
class LiveMessagingTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()

    @Test fun pairedPhoneCreatesThreadsSendsAndRecoversWifiWithoutLosingReplies() {
        val path = InstrumentationRegistry.getArguments().getString("deviceInvitationFile")
        assumeTrue("Requires a fresh disposable fixture", path != null)
        compose.runOnUiThread { compose.activity.requestedOrientation = android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT }
        compose.waitUntil(10000) { compose.activity.resources.configuration.orientation == android.content.res.Configuration.ORIENTATION_PORTRAIT }
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        DeviceStore(app).reset()
        val owners = ViewModelStore()
        val attempts = AtomicInteger()
        val reachable = java.util.concurrent.atomic.AtomicBoolean(true)
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app) { store, state, invitation, target, bind, status ->
                attempts.incrementAndGet()
                if (!reachable.get()) throw java.net.ConnectException("Disposable Wi-Fi outage")
                DeviceConnection(store, state, invitation, bind, status, target = target)
            }
            owners.put("live", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client, startPage = "Threads") } }
            client.preview(File(path!!).readText()); client.pair()
        }
        try {
            // The first snapshot can precede the fixture grant. Sync refreshes every five seconds.
            try {
                compose.waitUntil(20000) { client.connected && client.threads.isNotEmpty() }
            } catch (failure: Exception) {
                throw AssertionError("Fixture connection: ${client.status}; route=${client.route}; threads=${client.threads.size}; attempts=${attempts.get()}", failure)
            }
            assertEquals("Wi-Fi", client.route)
            assertTrue(client.lanAvailable)
            assertEquals(true, client.remReady)
            compose.onNodeWithTag("messageComposer").assertDoesNotExist()
            // The scope selector shows the same project name as the thread row.
            compose.onNode(hasText("Fixture project", substring = false) and hasAnyAncestor(hasTestTag("threadList"))).performClick()
            compose.onNodeWithTag("messageComposer").assertIsDisplayed()
            compose.onNodeWithTag("messageComposer").performTextInput("Question over Wi-Fi")
            compose.onNodeWithContentDescription("Send message").performClick()
            try { compose.waitUntil(15000) { client.messages.any { it.state == "Rem" } } }
            catch (failure: Exception) {
                throw AssertionError("Fixture reply: ${client.status}; connected=${client.connected}; route=${client.route}; states=${client.messages.map { it.state }}", failure)
            }
            val original = client.threads.single().first
            assertEquals("Completed", client.messages.first { it.thread == original }.state)
            compose.runOnUiThread { reachable.set(false); client.disconnect(); client.connect() }
            compose.waitUntil(10000) { !client.connected && client.status.contains("Desktop address could not be reached over Wi-Fi") }
            compose.runOnUiThread { reachable.set(true); client.connect() }
            compose.waitUntil(15000) { client.connected }
            assertEquals(1, client.messages.count { it.state == "Rem" })
            compose.onNodeWithContentDescription("Threads").performClick()
            compose.onNodeWithContentDescription("Project scope").performClick()
            compose.onAllNodesWithText("Fixture project", substring = false).filterToOne(hasAnyAncestor(isPopup())).performClick()
            compose.onNodeWithContentDescription("New thread").performClick()
            compose.waitUntil(15000) { client.threads.size == 2 }
            compose.onNodeWithTag("messageComposer").performTextInput("Question in the new thread")
            compose.onNodeWithContentDescription("Send message").performClick()
            compose.waitUntil(15000) { client.messages.count { it.state == "Rem" } == 2 }
            val created = client.threads.first { it.first != original }.first
            assertEquals(1, client.messages.count { it.thread == created && it.state == "Rem" })
            val beforeReconnect = attempts.get()
            compose.runOnUiThread { client.disconnect(); client.connect() }
            compose.waitUntil(15000) { client.connected && attempts.get() > beforeReconnect && client.status == "Authenticated desktop via Wi-Fi" }
            assertEquals(2, client.messages.count { it.state == "Rem" })
            assertEquals(2, client.threads.size)
            compose.runOnUiThread { compose.activity.window.clearFlags(android.view.WindowManager.LayoutParams.FLAG_SECURE) }
            compose.waitForIdle()
            Thread.sleep(300)
            InstrumentationRegistry.getInstrumentation().uiAutomation.takeScreenshot().let { bitmap ->
                java.io.File(app.filesDir, "live-messaging.png").outputStream().use {
                    bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it)
                }
                bitmap.recycle()
            }
            compose.runOnUiThread { compose.activity.window.addFlags(android.view.WindowManager.LayoutParams.FLAG_SECURE) }
        } finally {
            compose.runOnUiThread { owners.clear() }
            DeviceStore(app).reset()
        }
    }
}
