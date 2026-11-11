package dev.raticode.mobile

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.unit.dp
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.ChatMedia
import dev.raticode.mobile.live.MediaKind
import dev.raticode.mobile.live.MediaPreview
import dev.raticode.mobile.ui.MediaTile
import dev.raticode.mobile.ui.RaticodeTheme
import dev.raticode.mobile.ui.VideoPlayerDialog
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

/** The inline media tile in each download state, and the in-app player on a real MP4. No fixture is needed. */
class RemMediaUiTest {
    @get:Rule val compose = createComposeRule()

    private fun screenshot(name: String) {
        compose.waitForIdle()
        Thread.sleep(600)
        InstrumentationRegistry.getInstrumentation().uiAutomation.executeShellCommand("screencap -p /data/local/tmp/raticode-$name").use {
            android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
        }
    }

    private fun sample(): ByteArray = InstrumentationRegistry.getInstrumentation().context.assets.open("rem-sample.mp4").use { it.readBytes() }

    @Test fun tilesSayWhatIsHappening() {
        val picture = Bitmap.createBitmap(480, 320, Bitmap.Config.ARGB_8888).also { Canvas(it).drawColor(Color.rgb(90, 120, 200)) }
        val poster = ChatMedia.videoPoster(sample(), ChatMedia.THUMB_SIDE)
        var dark by mutableStateOf(false)
        val opened = mutableListOf<String>()
        compose.setContent {
            RaticodeTheme(darkTheme = dark) {
                Surface(color = MaterialTheme.colorScheme.background) {
                    Column(Modifier.verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        MediaTile("chart.png", MediaKind.IMAGE, "available", 100, true, MediaPreview.Ready(picture), onView = { opened += "chart" })
                        MediaTile("demo.mp4", MediaKind.VIDEO, "available", 100, true, poster, onView = { opened += "demo" })
                        MediaTile("big.mp4", MediaKind.VIDEO, "receiving", 45, true, null, onView = {})
                        MediaTile("later.png", MediaKind.IMAGE, "offered", 0, false, null, onView = {})
                        MediaTile("old.png", MediaKind.IMAGE, null, 0, true, null, onView = {})
                        MediaTile("odd.png", MediaKind.IMAGE, "available", 100, true, MediaPreview.Failed, onView = {})
                    }
                }
            }
        }
        compose.onNodeWithContentDescription("Picture: chart.png").performClick()
        compose.onNodeWithContentDescription("Video: demo.mp4, 0:03").performClick()
        assertEquals(listOf("chart", "demo"), opened)
        compose.onNodeWithText("Downloading 45%").assertExists()
        compose.onNodeWithText("Connect to your desktop to load this picture").assertExists()
        compose.onNodeWithText("This picture is no longer available. Ask Rem to send it again.").assertExists()
        compose.onNodeWithText("This phone can't preview this picture. Open it in another app instead.").assertExists()
        // A tile without a preview can't be tapped into an empty viewer.
        compose.onNodeWithContentDescription("big.mp4, Downloading 45%").assertHasNoClickAction()
        screenshot("rem-media-tiles-light.png")
        dark = true
        screenshot("rem-media-tiles-dark.png")
    }

    @Test fun playerPlaysPausesAndZeroesTheBytes() {
        val bytes = sample()
        var open by mutableStateOf(true)
        compose.setContent {
            RaticodeTheme {
                if (open) VideoPlayerDialog("demo.mp4", null, load = { done -> done(bytes) }, onClose = { open = false }, onOpenElsewhere = null)
            }
        }
        compose.waitUntil(15000) { compose.onAllNodesWithContentDescription("Pause").fetchSemanticsNodes().isNotEmpty() }
        // The time moves while it plays.
        compose.waitUntil(10000) { compose.onAllNodesWithText("0:01 / 0:03").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithTag("videoToggle").performClick()
        compose.onNodeWithContentDescription("Play").assertExists()
        compose.onNodeWithContentDescription("Close").performClick()
        compose.waitForIdle()
        compose.onNodeWithTag("videoPlayer").assertDoesNotExist()
        // Closing the player releases it and wipes the decrypted video from memory.
        assertTrue(bytes.all { it == 0.toByte() })
    }

    @Test fun anUnplayableVideoOffersAnotherApp() {
        var elsewhere = false
        compose.setContent {
            RaticodeTheme {
                VideoPlayerDialog("broken.mp4", null, load = { done -> done("not a video".toByteArray()) }, onClose = {}, onOpenElsewhere = { elsewhere = true })
            }
        }
        compose.waitUntil(15000) { compose.onAllNodesWithText("This phone can't play this video.").fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("Open in another app").performClick()
        assertTrue(elsewhere)
    }
}
