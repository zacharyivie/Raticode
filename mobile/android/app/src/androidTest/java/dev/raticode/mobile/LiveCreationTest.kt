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

class LiveCreationTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()

    @Test fun projectArrivingEnablesComposerCreationOverDelayedLan() {
        val args = InstrumentationRegistry.getArguments()
        val path = args.getString("creationInvitationFile")
        assumeTrue("Requires --workspace --delay-grant --slow-lan fixture", path != null)
        val landscape = args.getString("creationOrientation") == "landscape"
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
            owners.put("creation", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client, startPage = "Threads") } }
            client.preview(File(path!!).readText()); client.pair()
        }
        try {
            compose.waitUntil(20000) { client.connected }
            assertEquals("Wi-Fi", client.route)
            assertTrue(client.creationTemplates.isEmpty())
            compose.onNodeWithTag("newThreadComposer").performTextInput("Check the recovered connection")
            compose.onNodeWithContentDescription("Send message").assertIsNotEnabled()
            compose.waitUntil(25000) { client.creationTemplates.isNotEmpty() }
            compose.runOnUiThread {
                (compose.activity.getSystemService(android.content.Context.INPUT_METHOD_SERVICE) as android.view.inputmethod.InputMethodManager).hideSoftInputFromWindow(compose.activity.window.decorView.windowToken, 0)
            }
            compose.onNodeWithContentDescription("Project scope").performClick()
            compose.onNodeWithText("Fixture project", substring = false).performClick()
            compose.onNodeWithContentDescription("Send message").assertIsEnabled().performClick()
            compose.waitUntil(15000) { client.threads.any { it.second == "Check the recovered connection" } }
            compose.waitUntil(15000) { client.messages.any { it.text == "Disposable fixture response" } }
            compose.runOnUiThread { client.disconnect() }
            compose.waitUntil(5000) { !client.connected }
            compose.runOnUiThread { client.connect() }
            compose.waitUntil(15000) { client.connected }
            compose.runOnUiThread { client.refresh() }
            compose.waitForIdle()
            assertEquals(1, client.messages.count { it.text == "Disposable fixture response" })
        } finally {
            compose.runOnUiThread { owners.clear() }
            DeviceStore(app).reset()
        }
    }
}
