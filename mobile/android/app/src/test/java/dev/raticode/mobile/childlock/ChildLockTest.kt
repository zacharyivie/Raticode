package dev.raticode.mobile.childlock

import android.hardware.biometrics.BiometricPrompt
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ChildLockTest {
    private class MemoryStore(var value: Boolean = false, var writable: Boolean = true) : ChildLockStore {
        override fun read() = value
        override fun write(locked: Boolean): Boolean { if (writable) value = locked; return writable }
    }

    private fun lockedWith(store: MemoryStore = MemoryStore()) = ChildLock(store).also { assertEquals(ChildLock.TurnOn.LOCKED, it.turnOn(true)) }

    @Test fun startsOffAndTurnsOnWithOneCall() {
        val store = MemoryStore()
        val lock = ChildLock(store)
        assertFalse(lock.locked)
        assertEquals(ChildLock.TurnOn.LOCKED, lock.turnOn(true))
        assertTrue(lock.locked)
        assertTrue(store.value)
    }

    @Test fun aNewProcessReadsTheSavedLock() {
        val store = MemoryStore()
        lockedWith(store)
        assertTrue("relaunch after the app was closed", ChildLock(store).locked)
    }

    @Test fun onlyAConfirmedScreenLockTurnsItOff() {
        UnlockOutcome.entries.filter { it != UnlockOutcome.CONFIRMED }.forEach { outcome ->
            val store = MemoryStore()
            val lock = lockedWith(store)
            assertEquals(outcome, lock.finishUnlock(outcome))
            assertTrue("$outcome must leave it locked", lock.locked)
            assertTrue("$outcome must leave it saved", store.value)
            assertNotNull("$outcome must explain itself", outcome.notice)
        }
        val store = MemoryStore()
        val lock = lockedWith(store)
        assertNull(lock.finishUnlock(UnlockOutcome.CONFIRMED))
        assertFalse(lock.locked)
        assertFalse(ChildLock(store).locked)
    }

    @Test fun everyPromptErrorKeepsTheLockOn() {
        assertEquals(UnlockOutcome.CANCELLED, UnlockOutcome.fromError(BiometricPrompt.BIOMETRIC_ERROR_USER_CANCELED))
        assertEquals(UnlockOutcome.CANCELLED, UnlockOutcome.fromError(BiometricPrompt.BIOMETRIC_ERROR_CANCELED))
        assertEquals(UnlockOutcome.CANCELLED, UnlockOutcome.fromError(BiometricPrompt.BIOMETRIC_ERROR_TIMEOUT))
        assertEquals(UnlockOutcome.TOO_MANY_TRIES, UnlockOutcome.fromError(BiometricPrompt.BIOMETRIC_ERROR_LOCKOUT))
        assertEquals(UnlockOutcome.TOO_MANY_TRIES, UnlockOutcome.fromError(BiometricPrompt.BIOMETRIC_ERROR_LOCKOUT_PERMANENT))
        assertEquals(UnlockOutcome.NO_SCREEN_LOCK, UnlockOutcome.fromError(BiometricPrompt.BIOMETRIC_ERROR_NO_DEVICE_CREDENTIAL))
        (-5..64).forEach { assertTrue("code $it", UnlockOutcome.fromError(it) != UnlockOutcome.CONFIRMED) }
    }

    @Test fun onlyAScreenLockResultCounts() {
        assertEquals(UnlockOutcome.CONFIRMED, UnlockOutcome.fromSuccess(BiometricPrompt.AUTHENTICATION_RESULT_TYPE_DEVICE_CREDENTIAL))
        assertEquals(UnlockOutcome.UNAVAILABLE, UnlockOutcome.fromSuccess(BiometricPrompt.AUTHENTICATION_RESULT_TYPE_BIOMETRIC))
    }

    @Test fun anUnknownResultTypeDoesNotCount() {
        listOf(-1, 0, 3, 99).forEach { assertEquals("type $it", UnlockOutcome.UNAVAILABLE, UnlockOutcome.fromSuccess(it)) }
    }

    @Test fun aPhoneWithoutAScreenLockCannotTurnItOn() {
        val store = MemoryStore()
        val lock = ChildLock(store)
        assertEquals(ChildLock.TurnOn.NEEDS_SCREEN_LOCK, lock.turnOn(false))
        assertFalse(lock.locked)
        assertFalse(store.value)
        assertEquals(ChildLock.TurnOn.NEEDS_SCREEN_LOCK, lock.refusal)
        lock.dismissRefusal()
        assertNull(lock.refusal)
    }

    @Test fun aScreenLockRemovedLaterLeavesItOnWithAWayForward() {
        val lock = lockedWith()
        assertEquals(UnlockOutcome.NO_SCREEN_LOCK, lock.finishUnlock(UnlockOutcome.NO_SCREEN_LOCK))
        assertTrue(lock.locked)
        assertTrue(UnlockOutcome.NO_SCREEN_LOCK.offerSettings)
    }

    @Test fun aLockThatCannotBeSavedIsNotTurnedOn() {
        val lock = ChildLock(MemoryStore(writable = false))
        assertEquals(ChildLock.TurnOn.NOT_SAVED, lock.turnOn(true))
        assertFalse(lock.locked)
    }

    @Test fun anUnlockThatCannotBeSavedStaysLocked() {
        val store = MemoryStore()
        val lock = lockedWith(store)
        store.writable = false
        assertEquals(UnlockOutcome.NOT_SAVED, lock.finishUnlock(UnlockOutcome.CONFIRMED))
        assertTrue(lock.locked)
        assertTrue(store.value)
    }

    @Test fun turningOnTwiceIsHarmless() {
        val lock = lockedWith()
        assertEquals(ChildLock.TurnOn.LOCKED, lock.turnOn(false))
        assertTrue(lock.locked)
        assertNull(lock.refusal)
    }

    @Test fun aNotificationTapLeavesAnExistingLockScreenAlone() {
        assertEquals(NotificationTap.KEEP_LOCK, NotificationTap.of(locked = true, lockScreens = 1))
        assertEquals(NotificationTap.KEEP_LOCK, NotificationTap.of(locked = true, lockScreens = 2))
    }

    @Test fun aNotificationTapAfterAndroidEndedTheLockScreenLocksAgain() {
        assertEquals(NotificationTap.OPEN_LOCKED, NotificationTap.of(locked = true, lockScreens = 0))
    }

    @Test fun aNotificationTapOpensItsDestinationOnlyWhenUnlocked() {
        assertEquals(NotificationTap.OPEN, NotificationTap.of(locked = false, lockScreens = 0))
        assertEquals(NotificationTap.OPEN, NotificationTap.of(locked = false, lockScreens = 1))
    }
}
