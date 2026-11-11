package dev.raticode.mobile.voice

import android.provider.Settings
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceSetupTest {
    @Test fun theLaunchOfferOnlyAppearsWhenRaticodeCanBeChosen() {
        assertTrue(VoiceSetup.offerOnLaunch(AssistantSupport.NOT_SELECTED, paired = true, allowed = true))
        assertFalse("Already the assistant", VoiceSetup.offerOnLaunch(AssistantSupport.SELECTED, paired = true, allowed = true))
        assertFalse("Android 11 or older", VoiceSetup.offerOnLaunch(AssistantSupport.UNSUPPORTED, paired = true, allowed = true))
        assertFalse("No assistant choice, such as a work profile", VoiceSetup.offerOnLaunch(AssistantSupport.ROLE_UNAVAILABLE, paired = true, allowed = true))
        assertFalse("Pairing comes first", VoiceSetup.offerOnLaunch(AssistantSupport.NOT_SELECTED, paired = false, allowed = true))
        assertFalse("Don't ask again", VoiceSetup.offerOnLaunch(AssistantSupport.NOT_SELECTED, paired = true, allowed = false))
    }

    @Test fun eachMakerGetsItsClosestSettingsPageFirst() {
        assertEquals(listOf(Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS, Settings.ACTION_VOICE_INPUT_SETTINGS), VoiceSetup.settingsActions("samsung"))
        assertEquals(listOf(Settings.ACTION_VOICE_INPUT_SETTINGS, Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS), VoiceSetup.settingsActions("Google"))
        assertEquals(Settings.ACTION_VOICE_INPUT_SETTINGS, VoiceSetup.settingsActions("OnePlus").first())
    }

    @Test fun galaxyS20ModelsAreRecognized() {
        listOf("SM-G980F", "SM-G981U", "SM-G981U1", "SM-G986B", "SM-G988N", "SM-G780G", "SM-G781V").forEach {
            assertTrue(it, VoiceSetup.isGalaxyS20("samsung", it))
        }
        listOf("SM-G991B", "SM-S921U", "SM-G973F", "SM-A546B").forEach { assertFalse(it, VoiceSetup.isGalaxyS20("samsung", it)) }
        assertFalse(VoiceSetup.isGalaxyS20("Google", "SM-G981U"))
    }

    @Test fun stepsNameTheMenusOnEachPhone() {
        val s20 = VoiceSetup.steps("samsung", "SM-G981U")
        assertTrue(s20.choose.contains("Apps › Choose default apps, tap Digital assistant app"))
        assertTrue(s20.button.startsWith("On the Galaxy S20"))
        assertTrue(s20.button.contains("Side key › Press and hold"))
        assertTrue(s20.button.contains("Talk to Rem tile"))
        assertTrue(VoiceSetup.steps("samsung", "SM-S928B").button.contains("Side button"))
        assertTrue(VoiceSetup.steps("Google", "Pixel 8").button.contains("System › Gestures › Press and hold power button"))
        assertTrue(VoiceSetup.steps("Xiaomi", "23049PCD8G").button.contains("Not every phone offers it"))
    }
}
