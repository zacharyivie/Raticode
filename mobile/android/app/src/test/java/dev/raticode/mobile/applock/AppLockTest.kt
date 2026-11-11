package dev.raticode.mobile.applock

import android.hardware.biometrics.BiometricPrompt
import dev.raticode.mobile.childlock.ChildLock
import dev.raticode.mobile.childlock.ChildLockStore
import dev.raticode.mobile.childlock.NotificationTap
import dev.raticode.mobile.childlock.UnlockOutcome
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class AppLockTest {
    private class Memory(override var enabled: Boolean = false, override var minutes: Int = AppLockDelay.DEFAULT.minutes) : AppLockStore {
        override var leftAt: AppLockLeftAt? = null
        var writable = true
        override fun saveSetting(enabled: Boolean, minutes: Int): Boolean { if (writable) { this.enabled = enabled; this.minutes = minutes }; return writable }
        override fun saveLeftAt(at: AppLockLeftAt?): Boolean { if (writable) leftAt = at; return writable }
    }

    private class Clock(var at: Long = 1_000_000, var bootCount: Int = 7) : AppLockClock {
        override fun now() = at
        override fun boot() = bootCount
        fun advance(minutes: Int, extraMillis: Long = 0) { at += minutes * 60_000L + extraMillis }
    }

    private val store = Memory()
    private val clock = Clock()

    /** A running lock that is on, with the app open and already past the first unlock. */
    private fun openWith(delay: AppLockDelay): AppLock = AppLock(store, clock).also {
        assertTrue(it.change(true, delay))
        it.activityStarted()
        it.finishUnlock(UnlockOutcome.CONFIRMED)
        assertFalse(it.locked)
    }

    private fun AppLock.goAway() = activityStopped(changingConfigurations = false)
    private fun AppLock.comeBack() = activityStarted()

    @Test fun offUntilTurnedOnAndNeverAsksWhileOff() {
        val lock = AppLock(store, clock)
        assertFalse(lock.enabled)
        lock.activityStarted()
        assertFalse(lock.locked)
        lock.goAway()
        clock.advance(120)
        lock.comeBack()
        assertFalse(lock.locked)
        assertNull("nothing is recorded while off", store.leftAt)
    }

    @Test fun theFourChoicesAreTheOnesOffered() {
        assertEquals(listOf(0, 1, 5, 30), AppLockDelay.entries.map { it.minutes })
        assertEquals(listOf(0L, 60_000L, 300_000L, 1_800_000L), AppLockDelay.entries.map { it.millis })
    }

    @Test fun anUnreadableChoiceBecomesTheStrictest() {
        assertEquals(AppLockDelay.IMMEDIATELY, AppLockDelay.of(-1))
        assertEquals(AppLockDelay.IMMEDIATELY, AppLockDelay.of(7))
    }

    @Test fun immediatelyAsksOnEveryReturn() {
        val lock = openWith(AppLockDelay.IMMEDIATELY)
        lock.goAway()
        lock.comeBack()
        assertTrue(lock.locked)
    }

    @Test fun eachDelayAsksOnlyOnceItHasPassed() {
        listOf(AppLockDelay.ONE_MINUTE, AppLockDelay.FIVE_MINUTES, AppLockDelay.THIRTY_MINUTES).forEach { delay ->
            val lock = openWith(delay)
            lock.goAway()
            clock.at += delay.millis - 1
            lock.comeBack()
            assertFalse("$delay just before", lock.locked)
            lock.goAway()
            clock.at += delay.millis
            lock.comeBack()
            assertTrue("$delay at the limit", lock.locked)
            assertNull(lock.finishUnlock(UnlockOutcome.CONFIRMED))
        }
    }

    @Test fun movingBetweenRaticodeScreensIsNotLeaving() {
        val lock = openWith(AppLockDelay.IMMEDIATELY)
        lock.activityStarted() // the scanner opens before the main screen stops
        lock.goAway()
        assertFalse(lock.locked)
        assertNull(store.leftAt)
    }

    @Test fun rotatingIsNotLeaving() {
        val lock = openWith(AppLockDelay.IMMEDIATELY)
        lock.activityStopped(changingConfigurations = true)
        lock.activityStarted()
        assertFalse(lock.locked)
        assertNull(store.leftAt)
    }

    @Test fun onlyAConfirmedPromptUnlocks() {
        UnlockOutcome.entries.filter { it != UnlockOutcome.CONFIRMED }.forEach { outcome ->
            val lock = openWith(AppLockDelay.IMMEDIATELY).also { it.goAway(); it.comeBack() }
            assertEquals(outcome, lock.finishUnlock(outcome))
            assertTrue("$outcome must leave it locked", lock.locked)
            assertNotNull("$outcome must explain itself", outcome.appLockText())
            assertNotNull("$outcome must explain itself in settings", outcome.appLockSettingsText())
            assertNull(lock.finishUnlock(UnlockOutcome.CONFIRMED))
        }
    }

    @Test fun fingerprintFaceAndScreenLockAllCount() {
        assertEquals(UnlockOutcome.CONFIRMED, UnlockOutcome.fromAnyUnlock(BiometricPrompt.AUTHENTICATION_RESULT_TYPE_BIOMETRIC))
        assertEquals(UnlockOutcome.CONFIRMED, UnlockOutcome.fromAnyUnlock(BiometricPrompt.AUTHENTICATION_RESULT_TYPE_DEVICE_CREDENTIAL))
        listOf(-1, 0, 3, 99).forEach { assertEquals("type $it", UnlockOutcome.UNAVAILABLE, UnlockOutcome.fromAnyUnlock(it)) }
    }

    @Test fun leavingTheLockScreenWithoutUnlockingStaysLocked() {
        val lock = openWith(AppLockDelay.THIRTY_MINUTES)
        lock.goAway()
        clock.advance(31)
        lock.comeBack()
        lock.goAway()
        lock.comeBack()
        assertTrue(lock.locked)
    }

    @Test fun aNewProcessRespectsTheDelay() {
        val first = openWith(AppLockDelay.FIVE_MINUTES)
        first.goAway()
        clock.advance(2)
        val second = AppLock(store, clock)
        assertFalse("Android ended the app but the delay hasn't passed", second.locked)
        second.activityStarted()
        assertFalse(second.locked)
        second.goAway()
        clock.advance(5)
        val third = AppLock(store, clock)
        assertTrue(third.locked)
    }

    @Test fun aCrashAsksAtTheNextLaunch() {
        openWith(AppLockDelay.THIRTY_MINUTES)
        // No record of leaving, because the app never left.
        assertTrue(AppLock(store, clock).locked)
        val after = AppLock(store, clock)
        after.activityStarted()
        assertTrue(after.locked)
    }

    @Test fun aRestartOfThePhoneAsks() {
        val lock = openWith(AppLockDelay.THIRTY_MINUTES)
        lock.goAway()
        clock.bootCount = 8
        clock.at = 2_000_000 // the time since boot is small again but that says nothing
        assertTrue(AppLock(store, clock).locked)
    }

    @Test fun aClockThatRanBackwardsAsks() {
        val lock = openWith(AppLockDelay.THIRTY_MINUTES)
        lock.goAway()
        clock.at -= 1
        assertTrue(AppLock(store, clock).locked)
    }

    @Test fun aPhoneThatWontSayItsBootCountAsks() {
        val lock = openWith(AppLockDelay.THIRTY_MINUTES)
        lock.goAway()
        clock.bootCount = -1
        assertTrue(AppLock(store, clock).locked)
    }

    @Test fun confirmingAChangeDoesNotLockOnTheWayBack() {
        val lock = openWith(AppLockDelay.IMMEDIATELY)
        lock.beginPrompt()
        lock.goAway()
        lock.comeBack()
        assertFalse("Android 10's confirmation screen is not leaving the app", lock.locked)
        lock.endPrompt()
    }

    @Test fun aPromptLeftOpenForLongStillAsks() {
        val lock = openWith(AppLockDelay.IMMEDIATELY)
        lock.beginPrompt()
        lock.goAway()
        clock.advance(10)
        lock.comeBack()
        assertTrue(lock.locked)
    }

    @Test fun aPromptEndedBeforeLeavingDoesNotExcuseLeaving() {
        val lock = openWith(AppLockDelay.IMMEDIATELY)
        lock.beginPrompt()
        lock.endPrompt()
        lock.goAway()
        lock.comeBack()
        assertTrue(lock.locked)
    }

    @Test fun turningOnOrOffAndLooseningAsksFirst() {
        val lock = AppLock(store, clock)
        assertTrue("turning on proves the prompt works", lock.needsProof(true, AppLockDelay.ONE_MINUTE))
        assertFalse("choosing a delay while off changes nothing", lock.needsProof(false, AppLockDelay.THIRTY_MINUTES))
        lock.change(true, AppLockDelay.FIVE_MINUTES)
        assertTrue("turning off", lock.needsProof(false, AppLockDelay.FIVE_MINUTES))
        assertTrue("longer", lock.needsProof(true, AppLockDelay.THIRTY_MINUTES))
        assertFalse("shorter", lock.needsProof(true, AppLockDelay.ONE_MINUTE))
    }

    @Test fun aChangeThatCannotBeSavedChangesNothing() {
        val lock = AppLock(store, clock)
        store.writable = false
        assertFalse(lock.change(true, AppLockDelay.THIRTY_MINUTES))
        assertFalse(lock.enabled)
        assertEquals(AppLockDelay.DEFAULT, lock.delay)
    }

    @Test fun settingsSurviveARelaunch() {
        AppLock(store, clock).change(true, AppLockDelay.THIRTY_MINUTES)
        val next = AppLock(store, clock)
        assertTrue(next.enabled)
        assertEquals(AppLockDelay.THIRTY_MINUTES, next.delay)
    }

    @Test fun turningOffForgetsWhenTheAppLeft() {
        val lock = openWith(AppLockDelay.FIVE_MINUTES)
        lock.goAway()
        assertNotNull(store.leftAt)
        lock.change(false, AppLockDelay.FIVE_MINUTES)
        assertNull(store.leftAt)
        assertFalse(AppLock(store, clock).locked)
    }

    @Test fun anUnlockLeavesChildLockAlone() {
        val saved = object : ChildLockStore {
            var value = false
            override fun read() = value
            override fun write(locked: Boolean): Boolean { value = locked; return true }
        }
        val child = ChildLock(saved).also { it.turnOn(true) }
        val lock = openWith(AppLockDelay.IMMEDIATELY)
        lock.goAway()
        lock.comeBack()
        lock.finishUnlock(UnlockOutcome.CONFIRMED)
        assertFalse(lock.locked)
        assertTrue("App lock passing must not turn off Child lock", child.locked)
        assertEquals(NotificationTap.KEEP_LOCK, NotificationTap.of(child.locked, lockScreens = 1))
    }
}
