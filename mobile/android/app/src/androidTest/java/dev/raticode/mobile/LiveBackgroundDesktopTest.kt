package dev.raticode.mobile

import android.app.Application
import androidx.activity.compose.setContent
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
 * Driven by scripts/background-desktop-acceptance.mjs against a real, isolated Raticode
 * desktop whose window is closed to the tray. The phone must keep seeing desktop changes and
 * its deletion must finish, which needs the hidden desktop window to keep syncing.
 */
class LiveBackgroundDesktopTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()

    @Test fun hiddenDesktopKeepsSharedThreadsInSync() {
        val args = InstrumentationRegistry.getArguments()
        val path = args.getString("backgroundInvitationFile")
        assumeTrue("Run through scripts/background-desktop-acceptance.mjs", path != null)
        val hiddenWait = (args.getString("hiddenWaitSeconds") ?: "600").toLong() * 1000
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        DeviceStore(app).reset()
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app)
            owners.put("background", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client, startPage = "Threads") } }
            client.preview(File(path!!).readText()); client.pair()
        }
        try {
            compose.waitUntil(180000) { client.connected && client.threads.any { it.second == "Background test thread" } }
            val thread = client.threads.first { it.second == "Background test thread" }.first
            compose.runOnUiThread { client.openThread(thread) }
            compose.waitUntil(60000) { client.messages.any { it.thread == thread && it.text == "Desktop reply" } }
            // The harness now closes the desktop window, waits past Chromium's background
            // throttling threshold and then adds a message on the hidden desktop.
            compose.waitUntil(hiddenWait) { client.messages.any { it.thread == thread && it.text == "Added while hidden" } }
            var attempts = 0
            while (client.threads.any { it.first == thread }) {
                check(++attempts <= 5) { "Deletion did not finish: ${client.status}" }
                val revision = client.threadContexts[thread]?.revision ?: 1
                compose.runOnUiThread { client.dismissRequestError(); client.manageThread(thread, "delete", revision) }
                try { compose.waitUntil(60000) { client.threads.none { it.first == thread } || client.requestError != null } }
                catch (_: androidx.compose.ui.test.ComposeTimeoutException) { }
            }
        } finally {
            compose.runOnUiThread { owners.clear() }
            DeviceStore(app).reset()
        }
    }
}
