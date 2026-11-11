package dev.raticode.mobile

import android.app.Application
import android.os.Build
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.foundation.verticalScroll
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.DeviceStore
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.live.LiveRuntime
import dev.raticode.mobile.ui.RaticodeTheme
import dev.raticode.mobile.ui.VoiceAssistantSettings
import dev.raticode.mobile.voice.VoiceActivity
import dev.raticode.mobile.voice.VoiceController
import dev.raticode.mobile.voice.VoicePrefs
import dev.raticode.mobile.voice.VoiceRuntime
import dev.raticode.mobile.voice.VoiceState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test

/** Voice mode without a desktop: the microphone prompt, typing instead, the unpaired error and the settings section. */
class VoiceUiTest {
    @get:Rule val compose = createAndroidComposeRule<androidx.activity.ComponentActivity>()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val app get() = instrumentation.targetContext.applicationContext as Application

    private fun shell(command: String) = instrumentation.uiAutomation.executeShellCommand(command).use {
        android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes().decodeToString()
    }

    @Before fun unpaired() {
        assumeTrue("Voice mode needs Android 12", Build.VERSION.SDK_INT >= 31)
        DeviceStore(app).reset()
        lateinit var client: LiveClient
        instrumentation.runOnMainSync { client = LiveClient(app); LiveRuntime.install(client) }
        compose.waitUntil(10000) { client.loaded }
    }

    @Test fun typedRequestWithoutPairingExplainsWhatToDo() {
        app.startActivity(VoiceActivity.intent(app).addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK))
        run {
            compose.waitUntil(10000) { compose.onAllNodesWithContentDescription("Type instead").fetchSemanticsNodes(atLeastOneRootRequired = false).isNotEmpty() }
            // Without the permission the turn starts at the microphone step; revoking it here would end the test process.
            if (androidx.core.content.ContextCompat.checkSelfPermission(app, android.Manifest.permission.RECORD_AUDIO) != android.content.pm.PackageManager.PERMISSION_GRANTED) {
                compose.onNodeWithTag("voiceStatus").assertContentDescriptionEquals("Microphone needed")
                compose.onNodeWithText("Allow").assertIsDisplayed()
            }
            compose.onNodeWithContentDescription("Type instead").performClick()
            compose.onNodeWithTag("voiceInput").performTextInput("What is running?")
            compose.onNodeWithTag("voiceSend").performClick()
            compose.waitUntil(10000) { compose.onAllNodesWithTag("voiceProblem").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithTag("voiceProblem").assertTextEquals(VoiceController.NOT_PAIRED)
            compose.onNodeWithTag("voiceStatus").assertContentDescriptionEquals("Not sent")
            // Pairing problems can't be retried from here; talking again and typing remain.
            assertTrue(compose.onAllNodesWithText("Try again").fetchSemanticsNodes().isEmpty())
            // The words stay: talking again adds to them.
            compose.onNodeWithContentDescription("Keep talking").assertIsDisplayed()
            compose.onNodeWithContentDescription("Close voice mode").performClick()
            compose.waitUntil(5000) { compose.onAllNodesWithTag("voicePanel").fetchSemanticsNodes(atLeastOneRootRequired = false).isEmpty() }
        }
        compose.waitUntil(5000) { VoiceRuntime.controller(app).state == VoiceState.Idle }
    }

    @Test fun settingsExplainSetupAndKeepSwitches() {
        VoicePrefs.setWhileLocked(app, false); VoicePrefs.setSpeak(app, true)
        compose.setContent { RaticodeTheme { androidx.compose.foundation.layout.Column(
            androidx.compose.ui.Modifier.verticalScroll(androidx.compose.foundation.rememberScrollState())) { VoiceAssistantSettings(LiveRuntime.client(app)) } } }
        compose.onNodeWithTag("voiceAssistantRole").assertTextContains("digital assistant", substring = true)
        compose.onNodeWithText("Choose digital assistant", substring = true).assertExists()
        compose.onNodeWithTag("voiceSpeechStatus").assertExists()
        compose.waitForIdle(); shell("screencap -p /data/local/tmp/raticode-voice-settings.png")
        // Nothing sends by itself any more, so there is no auto-send switch.
        assertTrue(compose.onAllNodesWithTag("voiceAutoSend").fetchSemanticsNodes().isEmpty())
        compose.onNodeWithTag("voiceWhileLocked").performScrollTo().assertIsOff()
        compose.onNodeWithTag("voiceSpeak").performScrollTo().performClick()
        assertFalse(VoicePrefs.speak(app))
        VoicePrefs.setSpeak(app, true)
        assertEquals("Talk to Rem", app.getString(R.string.voice_label))
    }
}
