package dev.raticode.mobile

import android.app.Application
import android.app.RemoteInput
import android.content.Intent
import android.content.pm.ActivityInfo
import android.os.Build
import android.os.Bundle
import android.os.ParcelFileDescriptor
import androidx.compose.ui.test.ComposeTimeoutException
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.runner.lifecycle.ActivityLifecycleMonitorRegistry
import androidx.test.runner.lifecycle.Stage
import dev.raticode.mobile.applock.AppLock
import dev.raticode.mobile.applock.AppLockDelay
import dev.raticode.mobile.applock.AppLockRuntime
import dev.raticode.mobile.childlock.NotificationOpenActivity
import dev.raticode.mobile.childlock.UnlockOutcome
import dev.raticode.mobile.live.DeviceStore
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.live.LiveRuntime
import dev.raticode.mobile.live.RemHome
import dev.raticode.mobile.live.RemNotifications
import dev.raticode.mobile.live.RemReplyReceiver
import org.junit.After
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import java.io.File

/**
 * App lock with Android's real prompt and a test PIN. Run only on a disposable emulator. The first test needs no desktop;
 * the second pairs with `live-device-fixture.py --workspace --remote --desktop` through the `appLockInvitationFile`
 * argument, and shows that sending and alert replies carry on while Raticode is covered.
 */
class LiveAppLockTest {
    // No host activity: the lock has to cover real Raticode screens, as LiveVoiceTest explains for voice mode.
    @get:Rule val compose = createEmptyComposeRule()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val app get() = instrumentation.targetContext.applicationContext as Application
    private val lock: AppLock get() = AppLockRuntime.get(app)
    private var scenario: ActivityScenario<MainActivity>? = null

    private fun shell(command: String) = instrumentation.uiAutomation.executeShellCommand(command).use {
        ParcelFileDescriptor.AutoCloseInputStream(it).readBytes().decodeToString()
    }
    private fun screenshot(name: String) { shell("screencap -p /data/local/tmp/raticode-applock-$name.png") }
    private fun waitFor(what: String, timeout: Long = 20_000, condition: () -> Boolean) {
        try { compose.waitUntil(timeout, condition) }
        catch (failure: ComposeTimeoutException) { screenshot("failure"); throw AssertionError("$what: locked=${lock.locked}; focus=${focus()}", failure) }
    }
    private fun focus() = shell("dumpsys window").lines().firstOrNull { it.trim().startsWith("mCurrentFocus") }?.trim().orEmpty()
    /** The real stage of the main screen. ActivityScenario stops tracking it once a notification changes its intent. */
    private fun mainStage(stage: Stage): Boolean {
        var found = false
        instrumentation.runOnMainSync { found = ActivityLifecycleMonitorRegistry.getInstance().getActivitiesInStage(stage).any { it is MainActivity } }
        return found
    }
    private fun shown(tag: String) = compose.onAllNodesWithTag(tag).fetchSemanticsNodes(atLeastOneRootRequired = false).isNotEmpty()
    private fun set(on: Boolean, delay: AppLockDelay) = instrumentation.runOnMainSync { assertTrue(lock.change(on, delay)) }
    /** Android's PIN pad is another window; waiting inside the test clock keeps Compose running behind it. */
    private fun waitForPinPad() = waitFor("Android's PIN pad", 20_000) { focus().let { it.contains("BiometricPrompt") || it.contains("systemui", ignoreCase = true) } }
    /** Types the PIN once Android's pad is ready. The pad ignores keys for a moment as it opens, so give it time and allow one more try. */
    private fun enterPin(done: () -> Boolean) {
        waitForPinPad()
        val settle = System.currentTimeMillis()
        compose.waitUntil(5_000) { System.currentTimeMillis() - settle > 1_500 }
        repeat(2) {
            if (done()) return
            shell("input text 1111")
            shell("input keyevent KEYCODE_ENTER")
            runCatching { compose.waitUntil(6_000, done) }
        }
    }
    private fun leaveAndComeBack() {
        shell("am start -W -a android.settings.SETTINGS")
        waitFor("Settings in front") { focus().contains("settings", ignoreCase = true) }
        waitFor("main screen stopped") { mainStage(Stage.STOPPED) }
        shell("input keyevent KEYCODE_BACK")
    }
    private fun waitLockedThenUnlockWithPin(screenshotName: String) {
        waitFor("lock screen") { shown("appLockScreen") }
        assertTrue(lock.locked)
        screenshot(screenshotName)
        enterPin { !lock.locked }
        waitFor("unlocked") { !lock.locked && !shown("appLockScreen") }
    }

    @Before fun setUp() {
        assumeTrue("Needs Android 11's BiometricPrompt", Build.VERSION.SDK_INT >= 30)
        shell("input keyevent KEYCODE_WAKEUP"); shell("wm dismiss-keyguard")
        shell("locksettings set-pin 1111")
        DeviceStore(app).reset()
    }

    @After fun tearDown() {
        // Passes the lock so the screen under it can close, and restores the off default.
        instrumentation.runOnMainSync { if (lock.enabled) lock.change(false, AppLockDelay.DEFAULT); lock.finishUnlock(UnlockOutcome.CONFIRMED) }
        runCatching { scenario?.close() }
        shell("locksettings clear --old 1111")
        shell("input keyevent KEYCODE_WAKEUP"); shell("wm dismiss-keyguard")
    }

    @Test fun coversRaticodeUntilThePhoneConfirmsItsOwner() {
        // Off by default: a plain launch asks nothing.
        assertFalse(lock.enabled)
        scenario = ActivityScenario.launch(MainActivity::class.java)
        waitFor("main screen in front") { mainStage(Stage.RESUMED) }
        assertFalse(shown("appLockScreen"))

        // A return after any time away asks when the choice is Immediately. The screens below stay started under the lock.
        set(true, AppLockDelay.IMMEDIATELY)
        leaveAndComeBack()
        waitFor("lock screen") { shown("appLockScreen") }
        assertTrue(lock.locked)
        assertTrue("the main screen only pauses, so the connection stays", mainStage(Stage.PAUSED))

        // Cancelling leaves it locked and explains; Unlock asks again.
        waitForPinPad()
        shell("input keyevent KEYCODE_BACK")
        waitFor("notice") { compose.onAllNodesWithTag("appLockNotice").fetchSemanticsNodes(atLeastOneRootRequired = false).isNotEmpty() && lock.locked && focus().contains("AppLock") }
        assertTrue(lock.locked)
        screenshot("cancelled")

        // A notification tapped while locked doesn't take the lock away; its destination waits for the unlock.
        app.startActivity(Intent(app, NotificationOpenActivity::class.java).putExtra("thread", "live:rem").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        waitFor("still locked after the tap") { shown("appLockScreen") }
        assertTrue(lock.locked)

        compose.onNodeWithTag("appLockUnlock").performClick()
        enterPin { !lock.locked }
        waitFor("unlocked") { !lock.locked && !shown("appLockScreen") }
        waitFor("main screen back in front") { mainStage(Stage.RESUMED) }
        screenshot("unlocked")

        // Rotating is not leaving, even with Immediately.
        scenario!!.onActivity { it.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE }
        waitFor("landscape") { mainStage(Stage.RESUMED) && app.resources.configuration.orientation == android.content.res.Configuration.ORIENTATION_LANDSCAPE }
        scenario!!.onActivity { it.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_PORTRAIT }
        waitFor("portrait") { mainStage(Stage.RESUMED) && app.resources.configuration.orientation == android.content.res.Configuration.ORIENTATION_PORTRAIT }
        scenario!!.onActivity { it.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED }
        assertFalse("a rotation must not lock", lock.locked)

        // Within the chosen time it doesn't ask.
        set(true, AppLockDelay.THIRTY_MINUTES)
        leaveAndComeBack()
        waitFor("back in the app") { mainStage(Stage.RESUMED) }
        assertFalse(shown("appLockScreen"))
        assertFalse(lock.locked)
    }

    @Test fun sendingAndAlertRepliesCarryOnUnderTheLock() {
        val path = InstrumentationRegistry.getArguments().getString("appLockInvitationFile")
        assumeTrue("Requires a fresh fixture with --workspace --remote --desktop", path != null)
        lateinit var client: LiveClient
        instrumentation.runOnMainSync {
            client = LiveClient(app); LiveRuntime.install(client)
            client.preview(File(path!!).readText()); client.pair()
        }
        RemNotifications.setEnabled(app, true)
        scenario = ActivityScenario.launch(MainActivity::class.java)
        waitFor("desktop control", 30_000) { client.connected && client.desktopControl }
        waitFor("Rem's home", 30_000) { client.desktopViews["rem.home"] is RemHome }
        set(true, AppLockDelay.IMMEDIATELY)
        leaveAndComeBack()
        waitFor("lock screen") { shown("appLockScreen") }
        assertTrue(lock.locked)
        assertTrue("the main screen only pauses, so the connection stays", mainStage(Stage.PAUSED))
        screenshot("sending-locked")

        // A message queued from the outbox goes through while Raticode is covered, and the connection stays.
        fun messages(text: String) = (client.desktopViews["rem.home"] as? RemHome)?.messages?.count { it.role == "user" && it.body == text } ?: 0
        instrumentation.runOnMainSync { client.sendRemMessage("While locked", emptyList()) }
        waitFor("message delivered under the lock", 30_000) { messages("While locked") == 1 }
        assertTrue("still connected", client.connected)
        assertTrue("still locked", lock.locked)

        // A reply typed on Rem's alert goes through the same way, without needing the lock passed.
        val reply = Intent(app, RemReplyReceiver::class.java).setPackage(app.packageName)
        RemoteInput.addResultsToIntent(arrayOf(RemoteInput.Builder(RemNotifications.REPLY_KEY).build()), reply, Bundle().apply { putCharSequence(RemNotifications.REPLY_KEY, "Reply while locked") })
        app.sendBroadcast(reply)
        waitFor("alert reply delivered under the lock", 30_000) { messages("Reply while locked") == 1 }
        assertTrue("still locked", lock.locked)

        enterPin { !lock.locked }
        waitFor("unlocked") { !lock.locked && !shown("appLockScreen") }
    }
}
