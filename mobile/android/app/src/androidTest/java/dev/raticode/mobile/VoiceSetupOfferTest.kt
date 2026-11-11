package dev.raticode.mobile

import androidx.activity.ComponentActivity
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.lifecycle.Lifecycle
import dev.raticode.mobile.ui.RaticodeTheme
import dev.raticode.mobile.ui.VoiceSetupHost
import dev.raticode.mobile.ui.VoiceSetupOffer
import dev.raticode.mobile.voice.AssistantSteps
import dev.raticode.mobile.voice.AssistantSupport
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/** The launch offer against a stand-in for Android: each answer the phone can give, and each choice the user can make. */
class VoiceSetupOfferTest {
    @get:Rule val compose = createAndroidComposeRule<ComponentActivity>()

    private class Phone(var support: AssistantSupport, val settingsOpen: Boolean = true) : VoiceSetupHost {
        var asks = true; var opened = 0; var tried = 0
        /** What the user picks in Settings, applied when they come back. */
        var chooses: AssistantSupport? = null
        override fun support() = support
        override fun openSettings(): Boolean { opened++; chooses?.let { support = it }; return settingsOpen }
        override val steps = AssistantSteps("Pick Raticode in Settings.", "Hold the side key.")
        override fun tryVoice() { tried++ }
        override fun askOnLaunch() = asks
        override fun stopAsking() { asks = false }
    }

    private var done = 0
    private var voicePage = 0
    private var paired by mutableStateOf(true)

    private var phone by mutableStateOf<Phone?>(null)
    private var started = false
    /** Each new phone is a fresh launch of the offer. */
    private fun show(next: Phone) {
        phone = next
        if (started) return
        started = true
        compose.setContent { RaticodeTheme {
            phone?.let { current -> key(current) { VoiceSetupOffer(paired, onDone = { done++ }, onOpenVoicePage = { voicePage++ }, host = current) } }
        } }
    }
    private fun shown(tag: String) = compose.onAllNodesWithTag(tag).fetchSemanticsNodes().isNotEmpty()
    /** Leaving for Settings and coming back. */
    private fun returnFromSettings() {
        compose.activityRule.scenario.moveToState(Lifecycle.State.STARTED)
        compose.activityRule.scenario.moveToState(Lifecycle.State.RESUMED)
        compose.waitForIdle()
    }

    @Test fun notNowAsksAgainNextLaunch() {
        val p = Phone(AssistantSupport.NOT_SELECTED)
        show(p)
        compose.onNodeWithText("Use Rem as your assistant?").assertIsDisplayed()
        compose.onNodeWithText("Pick Raticode in Settings.", substring = true).assertIsDisplayed()
        compose.onNodeWithTag("voiceOfferLater").performClick()
        compose.waitForIdle()
        assertFalse(shown("voiceOffer")); assertEquals(1, done); assertTrue(p.asks); assertEquals(0, p.opened)
    }

    @Test fun dontAskAgainIsRemembered() {
        val p = Phone(AssistantSupport.NOT_SELECTED)
        show(p)
        compose.onNodeWithTag("voiceOfferNever").performClick()
        compose.waitForIdle()
        assertFalse(shown("voiceOffer")); assertEquals(1, done); assertFalse(p.asks)
    }

    @Test fun choosingRaticodeInSettingsIsConfirmed() {
        val p = Phone(AssistantSupport.NOT_SELECTED).apply { chooses = AssistantSupport.SELECTED }
        show(p)
        compose.onNodeWithTag("voiceOfferSetUp").performClick()
        compose.waitForIdle()
        assertEquals(1, p.opened); assertFalse(shown("voiceOffer"))
        returnFromSettings()
        compose.onNodeWithText("Rem is your assistant").assertIsDisplayed()
        compose.onNodeWithText("Hold the side key.", substring = true).assertIsDisplayed()
        compose.onNodeWithText("Try it").performClick()
        compose.waitForIdle()
        assertEquals(1, p.tried); assertEquals(1, done); assertFalse(shown("voiceOfferSelected"))
    }

    @Test fun comingBackWithoutChoosingOffersSettingsAgain() {
        val p = Phone(AssistantSupport.NOT_SELECTED)
        show(p)
        compose.onNodeWithTag("voiceOfferSetUp").performClick()
        returnFromSettings()
        compose.onNodeWithText("Raticode isn't chosen yet").assertIsDisplayed()
        p.chooses = AssistantSupport.SELECTED
        compose.onNodeWithText("Open settings again").performClick()
        returnFromSettings()
        assertEquals(2, p.opened)
        compose.onNodeWithText("Rem is your assistant").assertIsDisplayed()
        compose.onNodeWithText("Done").performClick()
        compose.waitForIdle()
        assertEquals(1, done)
    }

    @Test fun phonesWithoutTheSettingsPageGetTheStepsAndTheVoicePage() {
        val p = Phone(AssistantSupport.NOT_SELECTED, settingsOpen = false)
        show(p)
        compose.onNodeWithTag("voiceOfferSetUp").performClick()
        compose.onNodeWithText("Open Settings yourself").assertIsDisplayed()
        compose.onNodeWithText("Pick Raticode in Settings.", substring = true).assertIsDisplayed()
        compose.onNodeWithText("Voice assistant page").performClick()
        compose.waitForIdle()
        assertEquals(1, voicePage); assertEquals(1, done)
    }

    @Test fun noOfferWhenChosenUnsupportedUnavailableOrDeclined() {
        for ((support, asks) in listOf(AssistantSupport.SELECTED to true, AssistantSupport.UNSUPPORTED to true,
                AssistantSupport.ROLE_UNAVAILABLE to true, AssistantSupport.NOT_SELECTED to false)) {
            done = 0
            val p = Phone(support).apply { this.asks = asks }
            show(p)
            compose.waitForIdle()
            assertFalse("$support asks=$asks", shown("voiceOffer"))
            assertEquals("$support asks=$asks settles the launch", 1, done)
        }
    }

    @Test fun anUnpairedPhoneIsOfferedSetupOncePaired() {
        paired = false
        show(Phone(AssistantSupport.NOT_SELECTED))
        compose.waitForIdle()
        assertFalse(shown("voiceOffer")); assertEquals(0, done)
        paired = true
        compose.waitUntil(5000) { shown("voiceOffer") }
    }
}
