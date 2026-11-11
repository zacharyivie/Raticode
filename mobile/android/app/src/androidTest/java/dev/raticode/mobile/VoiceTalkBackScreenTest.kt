package dev.raticode.mobile

import android.app.Application
import android.content.Intent
import android.os.Build
import android.os.ParcelFileDescriptor
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.DeviceStore
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.live.LiveRuntime
import dev.raticode.mobile.ui.RaticodeTheme
import dev.raticode.mobile.ui.VoiceAssistantSettings
import dev.raticode.mobile.voice.OnDeviceSpeech
import dev.raticode.mobile.voice.RemSpeaker
import dev.raticode.mobile.voice.RemVoiceInteractionService
import dev.raticode.mobile.voice.ScreenShare
import dev.raticode.mobile.voice.SpeechEvents
import dev.raticode.mobile.voice.SpeechSession
import dev.raticode.mobile.voice.VoiceActivity
import dev.raticode.mobile.voice.VoiceLogic
import dev.raticode.mobile.voice.VoicePrefs
import dev.raticode.mobile.voice.VoiceRuntime
import dev.raticode.mobile.voice.VoiceState
import dev.raticode.mobile.ui.Picked
import android.net.Uri
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test

/**
 * "Talk back to me" on a phone with no offline voice, and reading the screen through Android's assist data. The
 * screen test makes Raticode the assistant with `cmd role`, standing in for the user's choice, and restores Google.
 */
class VoiceTalkBackScreenTest {
    @get:Rule val compose = createAndroidComposeRule<androidx.activity.ComponentActivity>()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val app get() = instrumentation.targetContext.applicationContext as Application
    private val controller get() = VoiceRuntime.controller(app)

    private fun shell(command: String) = instrumentation.uiAutomation.executeShellCommand(command).use {
        ParcelFileDescriptor.AutoCloseInputStream(it).readBytes().decodeToString()
    }
    private fun shown(tag: String) = compose.onAllNodesWithTag(tag).fetchSemanticsNodes(atLeastOneRootRequired = false).isNotEmpty()
    private fun onMain(block: () -> Unit) = instrumentation.runOnMainSync(block)

    /** Listening without a microphone, so the overlay stays open quietly. */
    private class Quiet : SpeechSession {
        override fun start() = true
        override fun finish() = Unit
        override fun close() = Unit
    }

    @Before fun noVoice() {
        assumeTrue("Voice mode needs Android 12", Build.VERSION.SDK_INT >= 31)
        DeviceStore(app).reset()
        // A phone with no offline voice installed for any language.
        RemSpeaker.voiceLookup = { null }
        lateinit var client: LiveClient
        onMain {
            client = LiveClient(app); LiveRuntime.install(client)
            controller.cancel(); controller.speech = { _: SpeechEvents -> Quiet() }
        }
        compose.waitUntil(10000) { client.loaded }
    }

    @After fun restore() {
        RemSpeaker.voiceLookup = { RemSpeaker.offlineVoice(it) }
        onMain { controller.cancel(); controller.speech = { OnDeviceSpeech(app, it) } }
        VoicePrefs.setSpeak(app, true); VoicePrefs.setScreen(app, false)
        RemVoiceInteractionService.screenSettingChanged()
        shell("cmd role remove-role-holder android.app.role.ASSISTANT ${app.packageName}")
        shell("cmd role add-role-holder android.app.role.ASSISTANT com.google.android.googlequicksearchbox")
        shell("input keyevent KEYCODE_HOME")
    }

    @Test fun talkBackSwitchesWithNoVoiceAndSaysNothingWhenOff() {
        VoicePrefs.setSpeak(app, true)
        compose.setContent { RaticodeTheme { androidx.compose.foundation.layout.Column(Modifier.verticalScroll(rememberScrollState())) {
            VoiceAssistantSettings(LiveRuntime.client(app)) } } }
        compose.onNodeWithTag("voiceSpeak").performScrollTo().assertIsOn().assertTextContains("Talk back to me")
        compose.waitUntil(10000) { shown("voiceOutputStatus") }
        compose.onNodeWithTag("voiceOutputStatus").assertTextEquals(VoiceLogic.VOICE_MISSING)
        shell("screencap -p /data/local/tmp/raticode-talkback-on-no-voice.png")
        compose.onNodeWithTag("voiceSpeak").performClick()
        assertFalse(VoicePrefs.speak(app))
        compose.onNodeWithTag("voiceSpeak").assertIsOff()
        compose.waitUntil(5000) { !shown("voiceOutputStatus") }
        assertTrue(compose.onAllNodesWithText(VoiceLogic.VOICE_MISSING).fetchSemanticsNodes().isEmpty())
        shell("screencap -p /data/local/tmp/raticode-talkback-off-no-voice.png")
        compose.onNodeWithTag("voiceSpeak").performClick()
        assertTrue(VoicePrefs.speak(app))
        compose.waitUntil(10000) { shown("voiceOutputStatus") }
    }

    @Test fun voiceModeTurnsTalkBackOnAndOffWithNoVoice() {
        VoicePrefs.setSpeak(app, false)
        shell("pm grant ${app.packageName} android.permission.RECORD_AUDIO")
        app.startActivity(VoiceActivity.intent(app).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        compose.waitUntil(10000) { shown("voiceMic") }
        // On this small screen the listening row gives way to attach and share; the switch is there before and after.
        onMain { controller.cancel() }
        compose.waitUntil(10000) { shown("voiceTalkBack") }
        val talkBack = compose.onNodeWithTag("voiceTalkBack")
        talkBack.assert(SemanticsMatcher.expectValue(SemanticsProperties.StateDescription, "Off"))
        talkBack.performClick()
        compose.waitUntil(5000) { VoicePrefs.speak(app) }
        talkBack.assert(SemanticsMatcher.expectValue(SemanticsProperties.StateDescription, "On"))
        assertTrue(controller.talkBack)
        talkBack.performClick()
        compose.waitUntil(5000) { !VoicePrefs.speak(app) }
        assertFalse(controller.talkBack)
        compose.onNodeWithContentDescription("Close voice mode").performClick()
    }

    @Test fun theScreenIsSharedOnlyWhenTappedForThisMessage() {
        VoicePrefs.setSpeak(app, false)
        shell("pm grant ${app.packageName} android.permission.RECORD_AUDIO")
        shell("cmd role add-role-holder android.app.role.ASSISTANT ${app.packageName}")
        compose.waitUntil(10000) { shell("cmd role get-role-holders android.app.role.ASSISTANT").contains(app.packageName) }

        // Sharing not allowed in settings: Android isn't asked for the screen and there is no share button.
        VoicePrefs.setScreen(app, false); RemVoiceInteractionService.screenSettingChanged()
        assist()
        compose.waitUntil(10000) { shown("voicePanel") }
        Thread.sleep(1500)
        assertEquals(ScreenShare.None, controller.screen)
        assertFalse(shown("voiceShareScreen"))
        compose.onNodeWithContentDescription("Close voice mode").performClick()
        compose.waitUntil(5000) { !shown("voicePanel") }

        // Allowed: the text is read, but nothing says so and nothing is shared until the button is tapped.
        VoicePrefs.setScreen(app, true); RemVoiceInteractionService.screenSettingChanged()
        assist()
        compose.waitUntil(15000) { controller.screen != ScreenShare.None }
        val share = controller.screen
        assertTrue("Got $share", share is ScreenShare.Ready)
        share as ScreenShare.Ready
        assertTrue(share.screen.app, share.screen.app.contains("Settings"))
        assertTrue(share.screen.text.isNotBlank() && share.screen.text.length <= 6000)
        compose.waitUntil(5000) { shown("voiceShareScreen") }
        val button = compose.onNodeWithTag("voiceShareScreen")
        button.assert(SemanticsMatcher.expectValue(SemanticsProperties.StateDescription, "Not shared"))
        assertFalse(controller.shareScreen)
        assertFalse(shown("voiceScreenShared") || shown("voiceScreenNote"))
        shell("screencap -p /data/local/tmp/raticode-screen-not-shared.png")
        button.performClick()
        compose.waitUntil(5000) { controller.shareScreen && shown("voiceScreenShared") }
        button.assert(SemanticsMatcher.expectValue(SemanticsProperties.StateDescription, "Sharing ${share.screen.app}"))
        shell("screencap -p /data/local/tmp/raticode-screen-shared.png")
        button.performClick()
        compose.waitUntil(5000) { !controller.shareScreen && !shown("voiceScreenShared") }
        // Chosen, then Rem opened again: the choice doesn't carry over.
        button.performClick()
        compose.waitUntil(5000) { controller.shareScreen }
        compose.onNodeWithContentDescription("Close voice mode").performClick()
        compose.waitUntil(5000) { !shown("voicePanel") }
        assist()
        compose.waitUntil(15000) { controller.screen is ScreenShare.Ready && shown("voiceShareScreen") }
        assertFalse(controller.shareScreen)
        compose.onNodeWithContentDescription("Close voice mode").performClick()
    }

    @Test fun attachingFromTheOverlayKeepsTheWordsAndOpensTheMenu() {
        VoicePrefs.setSpeak(app, false)
        val sessions = mutableListOf<SpeechEvents>()
        onMain { controller.speech = { events -> sessions += events; Quiet() } }
        shell("pm grant ${app.packageName} android.permission.RECORD_AUDIO")
        shell("cmd role add-role-holder android.app.role.ASSISTANT ${app.packageName}")
        compose.waitUntil(10000) { shell("cmd role get-role-holders android.app.role.ASSISTANT").contains(app.packageName) }
        assist()
        compose.waitUntil(10000) { shown("voiceAttach") && sessions.isNotEmpty() }
        onMain { sessions.last().partial("Book a meeting") }
        compose.waitUntil(5000) { (controller.state as? VoiceState.Listening)?.heard == "Book a meeting" }
        compose.onNodeWithTag("voiceAttach").performClick()
        // The overlay can't open pickers, so the voice screen takes the turn and shows Rem's attach menu.
        compose.waitUntil(15000) { shown("remAttachSheet") }
        val paused = controller.state as VoiceState.Listening
        assertTrue("Listening pauses while choosing", paused.paused)
        assertEquals("Book a meeting", paused.heard)
        compose.waitForIdle()
        compose.onNodeWithText("Photos and videos").assertIsDisplayed()
        shell("screencap -p /data/local/tmp/raticode-voice-attach-menu.png")
        // Backing out of the menu: listening carries on with the same words.
        shell("input keyevent KEYCODE_BACK")
        compose.waitUntil(10000) { !shown("remAttachSheet") && (controller.state as? VoiceState.Listening)?.paused == false }
        assertEquals("Book a meeting", (controller.state as VoiceState.Listening).heard)
        // A file the desktop can't take is refused with the reason, and the words stay.
        onMain { controller.attach(listOf(Picked(Uri.parse("content://media/external/file/1")))) }
        compose.waitUntil(15000) { shown("voiceAttachNotice") }
        assertTrue(controller.attachments.isEmpty())
        compose.onNodeWithContentDescription("Close voice mode").performClick()
    }

    /** Opens Android's Settings, an app with plenty of text, then Rem over it as the assist key does. */
    private fun assist() {
        shell("am start -W -a android.settings.SETTINGS")
        Thread.sleep(1500)
        shell("input keyevent KEYCODE_ASSIST")
    }
}
