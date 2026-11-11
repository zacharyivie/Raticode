package dev.raticode.mobile

import android.app.Application
import android.content.Intent
import android.net.Uri
import android.os.Build
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.DeviceStore
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.live.LiveRuntime
import dev.raticode.mobile.share.ShareActivity
import dev.raticode.mobile.share.ShareSession
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test

/** Share to Rem without a desktop: what another app shares is shown for review, refused items say why, and nothing sends unpaired. */
class ShareUiTest {
    @get:Rule val compose = createAndroidComposeRule<androidx.activity.ComponentActivity>()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val app get() = instrumentation.targetContext.applicationContext as Application

    @Before fun unpaired() {
        assumeTrue("Share to Rem needs Android 10", Build.VERSION.SDK_INT >= 29)
        DeviceStore(app).reset()
        lateinit var client: LiveClient
        instrumentation.runOnMainSync { client = LiveClient(app); LiveRuntime.install(client) }
        compose.waitUntil(10000) { client.loaded }
    }

    private fun share(intent: Intent) {
        app.startActivity(intent.setClass(app, ShareActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        compose.waitUntil(10000) { compose.onAllNodesWithTag("shareSend").fetchSemanticsNodes(atLeastOneRootRequired = false).isNotEmpty() }
    }

    @Test fun sharedLinkWaitsForReviewAndDoesntSendUnpaired() {
        share(Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_SUBJECT, "Bread recipe")
            .putExtra(Intent.EXTRA_TEXT, "https://example.com/bread"))
        compose.onNodeWithTag("shareText").assertTextContains("Bread recipe\n\nhttps://example.com/bread")
        compose.onNodeWithTag("shareSend").assertIsNotEnabled()
        compose.onNodeWithTag("shareError").assertTextEquals(ShareSession.NOT_PAIRED)
        compose.onNodeWithText("Open Raticode").assertIsDisplayed()
    }

    @Test fun raticodesOwnFilesAndPlainPathsAreLeftOut() {
        share(Intent(Intent.ACTION_SEND_MULTIPLE).setType("image/*").putParcelableArrayListExtra(Intent.EXTRA_STREAM, arrayListOf(
            Uri.parse("content://${app.packageName}.opened/opened/secret.png"),
            Uri.parse("file:///sdcard/Pictures/photo.png"),
        )))
        compose.onNodeWithText("One item came from Raticode itself and was left out.").assertIsDisplayed()
        compose.onNodeWithText("One item wasn't shared through Android's file system and was left out.").assertIsDisplayed()
        compose.onAllNodesWithContentDescription("Remove", substring = true).assertCountEquals(0)
        compose.onNodeWithTag("shareSend").assertIsNotEnabled()
    }
}
