package dev.raticode.mobile

import android.app.Application
import android.app.KeyguardManager
import android.os.Build
import android.os.ParcelFileDescriptor
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.DeviceStore
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.live.LiveRuntime
import dev.raticode.mobile.live.RemHome
import dev.raticode.mobile.voice.VoiceActivity
import dev.raticode.mobile.voice.VoicePrefs
import dev.raticode.mobile.voice.VoiceRuntime
import dev.raticode.mobile.voice.VoiceState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import java.io.File

/**
 * Voice mode against `live-device-fixture.py --workspace --remote --desktop`, whose in-memory Rem answers every
 * message with "Fixture Rem reply". Covers a typed turn on the voice screen, a suggested follow-up, Raticode as the
 * emulator's digital assistant opened by the assist key and by holding Power, and the lock-screen gate. Each turn is
 * typed with Type instead, since the emulator has no microphone input; on-device listening needs a physical phone.
 * Restores Google as the assistant and removes the test PIN afterwards.
 */
class LiveVoiceTest {
    // No host activity: locking and unlocking would stop it under the rule. Voice mode brings its own windows.
    @get:Rule val compose = createEmptyComposeRule()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val app get() = instrumentation.targetContext.applicationContext as Application
    private lateinit var client: LiveClient

    private fun shell(command: String) = instrumentation.uiAutomation.executeShellCommand(command).use {
        ParcelFileDescriptor.AutoCloseInputStream(it).readBytes().decodeToString()
    }
    private fun screenshot(name: String) { shell("screencap -p /data/local/tmp/raticode-voice-$name.png") }
    private fun home() = client.desktopViews["rem.home"] as? RemHome
    private fun userMessages(body: String) = home()?.messages?.count { it.role == "user" && it.body == body } ?: 0
    private fun waitFor(what: String, timeout: Long = 20000, condition: () -> Boolean) {
        try { compose.waitUntil(timeout, condition) }
        catch (failure: ComposeTimeoutException) {
            val roots = runCatching { compose.onAllNodes(isRoot()).fetchSemanticsNodes(atLeastOneRootRequired = false).size }.getOrDefault(-1)
            screenshot("failure")
            throw AssertionError("$what: voice=${VoiceRuntime.controller(app).state}; roots=$roots; status=${client.status}; errors=${client.desktopErrors}", failure)
        }
    }
    private fun shown(tag: String) = compose.onAllNodesWithTag(tag).fetchSemanticsNodes(atLeastOneRootRequired = false).isNotEmpty()
    private fun typeAndSend(text: String) {
        waitFor("type instead") { compose.onAllNodesWithContentDescription("Type instead").fetchSemanticsNodes(atLeastOneRootRequired = false).isNotEmpty() }
        compose.onNodeWithContentDescription("Type instead").performClick()
        compose.onNodeWithTag("voiceInput").performTextInput(text)
        compose.onNodeWithTag("voiceSend").performClick()
    }
    private fun waitForReply(text: String) {
        waitFor("reply to $text", 30000) { shown("voiceReply") && (VoiceRuntime.controller(app).state as? VoiceState.Replied)?.text == text }
        compose.onNodeWithTag("voiceReply").assertTextEquals("Fixture Rem reply")
    }
    private fun closePanel() {
        compose.onNodeWithContentDescription("Close voice mode").performClick()
        waitFor("panel closed", 10000) { !shown("voicePanel") }
    }

    @Test fun voiceTurnsReachRemFromEveryEntryPoint() {
        val path = InstrumentationRegistry.getArguments().getString("voiceInvitationFile")
        assumeTrue("Requires a fresh fixture with --workspace --remote --desktop", path != null)
        assumeTrue("Voice mode needs Android 12", Build.VERSION.SDK_INT >= 31)
        shell("input keyevent KEYCODE_WAKEUP"); shell("wm dismiss-keyguard")
        DeviceStore(app).reset()
        instrumentation.runOnMainSync {
            client = LiveClient(app); LiveRuntime.install(client)
            client.preview(File(path!!).readText()); client.pair()
        }
        VoicePrefs.setWhileLocked(app, false); VoicePrefs.setSpeak(app, true)
        waitFor("desktop control", 30000) { client.connected && client.desktopControl }
        // Like a closed app: nothing keeps the connection open except voice mode.
        instrumentation.runOnMainSync { client.background() }
        val previous = shell("cmd role get-role-holders android.app.role.ASSISTANT").trim()
        try {
            // 1. The voice screen, as the tile, shortcut and notification open it.
            app.startActivity(VoiceActivity.intent(app).addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK))
            waitFor("voice screen") { shown("voicePanel") }
            screenshot("start")
            typeAndSend("What is running?")
            waitForReply("What is running?")
            assertEquals(1, userMessages("What is running?"))
            compose.onNodeWithTag("voiceSaid").assertTextEquals("What is running?")
            screenshot("reply")
            // A suggestion under the reply sends at once, exactly once.
            compose.onNodeWithText("Anything else running?").performClick()
            waitForReply("Anything else running?")
            assertEquals(1, userMessages("Anything else running?"))
            closePanel()

            // 2. Raticode as the digital assistant: the assist key with the app's screens closed.
            shell("cmd role add-role-holder android.app.role.ASSISTANT ${app.packageName}")
            waitFor("assistant role", 15000) { shell("settings get secure voice_interaction_service").contains(app.packageName) }
            shell("input keyevent KEYCODE_ASSIST")
            waitFor("assistant overlay", 15000) { shown("voicePanel") }
            screenshot("assistant")
            typeAndSend("Summarize my tasks")
            waitForReply("Summarize my tasks")
            closePanel()

            // 2b. The assist key over Raticode's own Rem chat: once sent, the overlay steps aside, since the chat
            // behind it shows the message and Rem's reply. The message still reaches the desktop exactly once.
            app.startActivity(android.content.Intent(app, MainActivity::class.java).addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK))
            waitFor("Rem's chat on screen", 20000) { client.remChatOnScreen }
            shell("input keyevent KEYCODE_ASSIST")
            waitFor("overlay over Rem's chat", 15000) { shown("voicePanel") }
            assertTrue("Rem's chat stays on screen under the overlay", client.remChatOnScreen)
            typeAndSend("Sent over the chat")
            waitFor("overlay closed after sending", 15000) { !shown("voicePanel") }
            assertTrue(VoiceRuntime.controller(app).state !is VoiceState.Failed)
            waitFor("message reached Rem") { userMessages("Sent over the chat") == 1 }
            waitFor("reply in Rem's chat", 30000) {
                home()?.messages?.let { all -> all.indexOfFirst { it.body == "Sent over the chat" }.let { at -> at >= 0 && all.drop(at + 1).any { it.role != "user" && it.body == "Fixture Rem reply" } } } == true
            }
            assertTrue("The chat is the window in front", shell("dumpsys window").lines().any { it.trim().startsWith("mCurrentFocus") && it.contains("MainActivity") })
            screenshot("over-rem-chat")
            Thread.sleep(2000)
            assertEquals("Sent once", 1, userMessages("Sent over the chat"))
            shell("input keyevent KEYCODE_HOME")
            waitFor("app closed", 10000) { !client.remChatOnScreen }

            // 3. Holding the power button, where the phone sends it to the assistant (this emulator does).
            if (shell("dumpsys window policy").contains("mLongPressOnPowerBehavior=LONG_PRESS_POWER_ASSISTANT")) {
                shell("input keyevent --duration 1500 KEYCODE_POWER")
                waitFor("overlay from the power button", 15000) { shown("voicePanel") }
                screenshot("power-button")
                closePanel()
            }

            // 4. A locked phone: listening and typing work, but nothing is sent until it is unlocked.
            shell("locksettings set-pin 1111")
            shell("input keyevent KEYCODE_SLEEP"); Thread.sleep(1500); shell("input keyevent KEYCODE_WAKEUP")
            val keyguard = app.getSystemService(KeyguardManager::class.java)
            waitFor("lock screen", 10000) { keyguard.isDeviceLocked }
            shell("input keyevent KEYCODE_ASSIST")
            waitFor("overlay on the lock screen", 15000) { shown("voicePanel") }
            typeAndSend("Sent after unlocking")
            waitFor("unlock step") { shown("voiceUnlock") }
            screenshot("locked")
            Thread.sleep(3000)
            assertEquals("Nothing reaches the desktop while locked", 0, userMessages("Sent after unlocking"))
            compose.onNodeWithTag("voiceUnlock").performClick()
            // The overlay hands over to the voice screen, which asks for the PIN.
            waitFor("PIN prompt", 15000) { shell("dumpsys window").lines().any { it.trim().startsWith("mCurrentFocus") && it.contains("NotificationShade") } }
            shell("input text 1111"); shell("input keyevent KEYCODE_ENTER")
            waitFor("unlocked", 15000) { !keyguard.isDeviceLocked }
            waitForReply("Sent after unlocking")
            assertEquals(1, userMessages("Sent after unlocking"))
            screenshot("unlocked")
            closePanel()
        } finally {
            shell("locksettings clear --old 1111")
            shell("input keyevent KEYCODE_WAKEUP"); shell("wm dismiss-keyguard")
            shell("cmd role remove-role-holder android.app.role.ASSISTANT ${app.packageName}")
            previous.split(';').filter { it.isNotBlank() }.forEach { shell("cmd role add-role-holder android.app.role.ASSISTANT $it") }
            instrumentation.runOnMainSync { client.disconnect() }
        }
        assertTrue(shell("cmd role get-role-holders android.app.role.ASSISTANT").trim() == previous)
    }
}
