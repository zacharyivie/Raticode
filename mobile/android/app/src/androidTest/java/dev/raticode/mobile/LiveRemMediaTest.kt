package dev.raticode.mobile

import android.app.Application
import androidx.activity.compose.setContent
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.lifecycle.ViewModelStore
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.data.OpenedFiles
import dev.raticode.mobile.live.*
import dev.raticode.mobile.ui.LiveScreen
import dev.raticode.mobile.ui.RaticodeTheme
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import java.io.File

/**
 * Pictures and videos on Rem's replies against `live-device-fixture.py --workspace --remote --desktop --media`. The
 * fixture's Rem replies with a PNG, an MP4 and a text file named `.png`; the desktop offers them through its real
 * reply-file path to the phone that asked, and the phone downloads them over pinned TLS without a tap.
 */
class LiveRemMediaTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()

    private fun waitFor(what: String, client: LiveClient, timeout: Long = 30000, condition: () -> Boolean) {
        try { compose.waitUntil(timeout, condition) }
        catch (failure: ComposeTimeoutException) {
            throw AssertionError("$what: files=${client.files}; previews=${client.mediaPreviews}; status=${client.status}", failure)
        }
    }

    private fun screenshot(name: String) {
        compose.waitForIdle()
        Thread.sleep(500)
        InstrumentationRegistry.getInstrumentation().uiAutomation.executeShellCommand("screencap -p /data/local/tmp/raticode-$name").use {
            android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
        }
    }

    @Test fun picturesAndVideosShowInlineAndOpenFullScreen() {
        val path = InstrumentationRegistry.getArguments().getString("mediaInvitationFile")
        assumeTrue("Requires a disposable fixture with --workspace --remote --desktop --media", path != null)
        compose.runOnUiThread { compose.activity.requestedOrientation = android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT }
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        DeviceStore(app).reset()
        OpenedFiles.cleanup(app.cacheDir, keepMillis = 0)
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app)
            owners.put("media", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client, startPage = "Threads") } }
            client.preview(File(path!!).readText()); client.pair()
        }
        try {
            waitFor("desktop control", client) { client.connected && client.desktopControl }
            compose.waitUntil(5000) { compose.onAllNodesWithContentDescription("Rem").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithContentDescription("Rem").performClick()
            compose.onNodeWithTag("remComposer").performTextInput("Send me the chart and the demo")
            compose.onNodeWithContentDescription("Send").performClick()
            compose.runOnUiThread {
                (compose.activity.getSystemService(android.content.Context.INPUT_METHOD_SERVICE) as android.view.inputmethod.InputMethodManager)
                    .hideSoftInputFromWindow(compose.activity.window.decorView.windowToken, 0)
            }

            // Nothing is tapped: the pictures and the video download and their previews appear by themselves.
            waitFor("downloads", client) { client.files.count { it.state == "available" } == 3 }
            waitFor("previews", client) { client.mediaPreviews.size == 3 && client.mediaPreviews.values.none { it == MediaPreview.Loading } }
            val picture = client.files.single { it.name == "Fixture chart.png" }
            val video = client.files.single { it.name == "Fixture demo.mp4" }
            assertTrue((client.mediaPreviews[picture.id] as MediaPreview.Ready).poster!!.width == 480)
            val clip = client.mediaPreviews[video.id] as MediaPreview.Ready
            assertNotNull(clip.poster)
            assertEquals(3, ((clip.durationMs + 500) / 1000).toInt())
            assertEquals(MediaPreview.Failed, client.mediaPreviews[client.files.single { it.name == "Not a picture.png" }.id])
            compose.onNodeWithContentDescription("Picture: Fixture chart.png").performScrollTo().assertIsDisplayed()
            screenshot("rem-media-inline.png")

            // The picture opens full screen; Close goes back to the chat.
            compose.onNodeWithContentDescription("Picture: Fixture chart.png").performClick()
            compose.onNodeWithTag("mediaViewer").assertIsDisplayed()
            screenshot("rem-media-picture.png")
            compose.onNodeWithContentDescription("Close").performClick()
            compose.onNodeWithTag("mediaViewer").assertDoesNotExist()

            // The video opens a player that starts playing from memory.
            compose.onNodeWithContentDescription("Video: Fixture demo.mp4, 0:03").performScrollTo().performClick()
            compose.onNodeWithTag("videoPlayer").assertIsDisplayed()
            compose.waitUntil(15000) { compose.onAllNodesWithContentDescription("Pause").fetchSemanticsNodes().isNotEmpty() }
            // Pause right away: the sample is only three seconds long.
            compose.onNodeWithTag("videoToggle").performClick()
            compose.onNodeWithContentDescription("Play").assertExists()
            screenshot("rem-media-video.png")
            compose.onNodeWithContentDescription("Close").performClick()
            compose.onNodeWithTag("videoPlayer").assertDoesNotExist()
            // Viewing and playing never wrote a plaintext copy for another app.
            assertTrue(OpenedFiles.directory(app.cacheDir).listFiles().isNullOrEmpty())

            // A file named like a picture that isn't one says so; Save and Open stay available.
            compose.onNodeWithText("This phone can't preview this picture. Open it in another app instead.").performScrollTo().assertIsDisplayed()
            compose.onNodeWithContentDescription("Open Not a picture.png").assertExists()
            screenshot("rem-media-unsupported.png")
        } finally {
            compose.runOnUiThread { owners.clear() }
        }
    }
}
