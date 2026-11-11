package dev.raticode.mobile

import android.app.Application
import android.content.Intent
import android.os.Build
import android.os.ParcelFileDescriptor
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.DeviceStore
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.live.LiveRuntime
import dev.raticode.mobile.voice.VoicePrefs
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import java.io.File

/**
 * The voice setup offer on real launches of Raticode against `live-device-fixture.py --workspace --remote --desktop`:
 * Set up opens the phone's own settings, choosing Raticode there is confirmed on return, a chosen assistant isn't
 * offered again, Not now asks again and Don't ask again stops. More › Voice assistant opens the setup page.
 * The test stands in for the user's choice in Settings with `cmd role`, then restores the previous assistant.
 */
class LiveVoiceSetupTest {
    @get:Rule val compose = createEmptyComposeRule()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val app get() = instrumentation.targetContext.applicationContext as Application

    private fun shell(command: String) = instrumentation.uiAutomation.executeShellCommand(command).use {
        ParcelFileDescriptor.AutoCloseInputStream(it).readBytes().decodeToString()
    }
    private fun screenshot(name: String) { shell("screencap -p /data/local/tmp/raticode-voice-setup-$name.png") }
    private fun shown(tag: String) = compose.onAllNodesWithTag(tag).fetchSemanticsNodes(atLeastOneRootRequired = false).isNotEmpty()
    private fun focus() = shell("dumpsys window").lines().firstOrNull { it.trim().startsWith("mCurrentFocus") }.orEmpty()
    /** Opening Raticode from its icon: a new launcher start, not a return to an open screen. */
    private fun launch() {
        app.startActivity(Intent(app, MainActivity::class.java).setAction(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK))
        compose.waitUntil(15000) { focus().contains("dev.raticode.mobile/dev.raticode.mobile.MainActivity") }
        compose.waitUntil(15000) { shown("desktopNavigation") }
    }
    /** Waits long enough for an offer to have appeared, then reports whether it did. */
    private fun offered(): Boolean {
        runCatching { compose.waitUntil(4000) { shown("voiceOffer") } }
        return shown("voiceOffer")
    }

    @Test fun launchOfferLeadsToAndroidsAssistantChoice() {
        val path = InstrumentationRegistry.getArguments().getString("voiceSetupInvitationFile")
        assumeTrue("Requires a fresh fixture with --workspace --remote --desktop", path != null)
        assumeTrue("Voice mode needs Android 12", Build.VERSION.SDK_INT >= 31)
        shell("input keyevent KEYCODE_WAKEUP"); shell("wm dismiss-keyguard")
        val previous = shell("cmd role get-role-holders android.app.role.ASSISTANT").trim()
        shell("cmd role remove-role-holder android.app.role.ASSISTANT ${app.packageName}")
        VoicePrefs.setOfferSetup(app, true)
        DeviceStore(app).reset()
        lateinit var client: LiveClient
        instrumentation.runOnMainSync { client = LiveClient(app); LiveRuntime.install(client); client.preview(File(path!!).readText()); client.pair() }
        compose.waitUntil(30000) { client.paired }
        try {
            // 1. Opening Raticode offers setup; Set up opens the phone's assistant settings.
            launch()
            assertTrue("Offered on launch", offered())
            screenshot("offer")
            compose.onNodeWithTag("voiceOfferSetUp").performClick()
            // Android's own settings, not Raticode, take the screen.
            compose.waitUntil(15000) { focus().let { it.contains("com.android.settings") || it.contains("permissioncontroller") } }
            screenshot("settings")
            // The user picks Raticode there; Android allows only them, so the test stands in with cmd role.
            shell("cmd role add-role-holder android.app.role.ASSISTANT ${app.packageName}")
            shell("input keyevent KEYCODE_BACK")
            compose.waitUntil(10000) { shown("voiceOfferSelected") }
            screenshot("selected")
            compose.onNodeWithText("Done").performClick()

            // 2. Already the assistant: no offer, and More › Voice assistant shows it.
            launch()
            assertTrue("No offer once chosen", !offered())
            compose.onNodeWithContentDescription("More").performClick()
            compose.onNodeWithText("Voice assistant").performClick()
            compose.waitUntil(5000) { shown("voicePage") }
            compose.onNodeWithTag("voiceAssistantRole").assertTextEquals("Raticode is your digital assistant.")
            screenshot("page")

            // 3. Switched back to another assistant: offered again; Not now asks next time, Don't ask again stops.
            shell("cmd role remove-role-holder android.app.role.ASSISTANT ${app.packageName}")
            previous.split(';').filter { it.isNotBlank() }.forEach { shell("cmd role add-role-holder android.app.role.ASSISTANT $it") }
            launch()
            assertTrue(offered())
            compose.onNodeWithTag("voiceOfferLater").performClick()
            launch()
            assertTrue("Not now asks again", offered())
            compose.onNodeWithTag("voiceOfferNever").performClick()
            launch()
            assertTrue("Don't ask again is respected", !offered())
            assertTrue(!VoicePrefs.offerSetup(app))
        } finally {
            VoicePrefs.setOfferSetup(app, true)
            shell("cmd role remove-role-holder android.app.role.ASSISTANT ${app.packageName}")
            previous.split(';').filter { it.isNotBlank() }.forEach { shell("cmd role add-role-holder android.app.role.ASSISTANT $it") }
            instrumentation.runOnMainSync { client.disconnect() }
        }
        assertEquals(previous, shell("cmd role get-role-holders android.app.role.ASSISTANT").trim())
    }
}
