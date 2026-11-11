package dev.raticode.mobile

import android.app.Application
import android.os.Build
import android.os.ParcelFileDescriptor
import androidx.activity.ComponentActivity
import androidx.compose.ui.test.assertIsOff
import androidx.compose.ui.test.assertIsOn
import androidx.compose.ui.test.assertIsSelected
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.applock.AppLock
import dev.raticode.mobile.applock.AppLockDelay
import dev.raticode.mobile.applock.AppLockRuntime
import dev.raticode.mobile.ui.AppLockPage
import dev.raticode.mobile.ui.RaticodeTheme
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test

/** More › App lock, with Android's real prompt and a test PIN. Run only on a disposable emulator. */
class AppLockSettingsTest {
    @get:Rule val compose = createAndroidComposeRule<ComponentActivity>()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val app get() = instrumentation.targetContext.applicationContext as Application
    private val lock: AppLock get() = AppLockRuntime.get(app)

    private fun shell(command: String) = instrumentation.uiAutomation.executeShellCommand(command).use {
        ParcelFileDescriptor.AutoCloseInputStream(it).readBytes().decodeToString()
    }
    private fun focus() = shell("dumpsys window").lines().firstOrNull { it.trim().startsWith("mCurrentFocus") }?.trim().orEmpty()
    /** Types the PIN once Android's pad is ready. The pad ignores keys for a moment as it opens, so allow one more try. */
    private fun enterPin(done: () -> Boolean) {
        compose.waitUntil(20_000) { focus().contains("systemui", ignoreCase = true) || focus().contains("BiometricPrompt") }
        val settle = System.currentTimeMillis()
        compose.waitUntil(5_000) { System.currentTimeMillis() - settle > 1_500 }
        repeat(2) {
            if (done()) return
            shell("input text 1111")
            shell("input keyevent KEYCODE_ENTER")
            runCatching { compose.waitUntil(6_000, done) }
        }
    }
    private fun choose(delay: AppLockDelay) = compose.onNodeWithTag("appLockDelay${delay.minutes}").performScrollTo().performClick()

    @Before fun setUp() {
        assumeTrue(Build.VERSION.SDK_INT >= 30)
        shell("locksettings set-pin 1111")
        instrumentation.runOnMainSync { lock.change(false, AppLockDelay.DEFAULT) }
        compose.setContent { RaticodeTheme { AppLockPage { } } }
    }

    @After fun tearDown() {
        instrumentation.runOnMainSync { lock.change(false, AppLockDelay.DEFAULT) }
        shell("locksettings clear --old 1111")
    }

    @Test fun offByDefaultAndChangesAskFirstOnlyWhenTheyLoosenTheLock() {
        assertFalse(lock.enabled)
        compose.onNodeWithTag("appLockSwitch").assertIsOff()
        assertEquals(AppLockDelay.DEFAULT, lock.delay)

        // A choice while off asks nothing.
        choose(AppLockDelay.FIVE_MINUTES)
        compose.waitUntil(5_000) { lock.delay == AppLockDelay.FIVE_MINUTES }
        compose.onNodeWithTag("appLockDelay5").assertIsSelected()

        // Turning on goes through Android's prompt.
        compose.onNodeWithTag("appLockSwitch").performClick()
        enterPin { lock.enabled }
        compose.waitUntil(20_000) { lock.enabled }
        compose.onNodeWithTag("appLockSwitch").assertIsOn()
        assertFalse(lock.locked)

        // Shorter is immediate; longer asks.
        choose(AppLockDelay.IMMEDIATELY)
        compose.waitUntil(5_000) { lock.delay == AppLockDelay.IMMEDIATELY }
        choose(AppLockDelay.THIRTY_MINUTES)
        enterPin { lock.delay == AppLockDelay.THIRTY_MINUTES }
        compose.waitUntil(20_000) { lock.delay == AppLockDelay.THIRTY_MINUTES }

        // Turning off asks too.
        compose.onNodeWithTag("appLockSwitch").performClick()
        enterPin { !lock.enabled }
        compose.waitUntil(20_000) { !lock.enabled }
        assertTrue(lock.delay == AppLockDelay.THIRTY_MINUTES)
    }
}
