package dev.raticode.mobile.childlock

import android.hardware.biometrics.BiometricPrompt
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue

/** Where the on/off state is kept, so closing the app or a restart can't forget it. */
internal interface ChildLockStore {
    fun read(): Boolean
    /** False when the choice could not be written to storage. */
    fun write(locked: Boolean): Boolean
}

/** How one request for the phone's screen lock ended. Only [CONFIRMED] turns the child lock off. */
internal enum class UnlockOutcome(val notice: String?, val offerSettings: Boolean = false) {
    CONFIRMED(null),
    CANCELLED("Not unlocked. Child lock is still on."),
    TOO_MANY_TRIES("Too many wrong tries. Wait a while, then try again. Child lock is still on."),
    NO_SCREEN_LOCK("This phone has no screen lock, so Raticode can't confirm it's you. Set one in Android settings, then hold the lock again. Child lock is still on.", offerSettings = true),
    UNAVAILABLE("Android couldn't ask for your screen lock. Child lock is still on. Try again."),
    NOT_SAVED("Raticode couldn't save that. Child lock is still on. Try again.");

    companion object {
        /** A prompt that ended without success, by BiometricPrompt's error code. Unknown codes never unlock. */
        fun fromError(code: Int): UnlockOutcome = when (code) {
            BiometricPrompt.BIOMETRIC_ERROR_USER_CANCELED, BiometricPrompt.BIOMETRIC_ERROR_CANCELED,
            BiometricPrompt.BIOMETRIC_ERROR_TIMEOUT -> CANCELLED
            BiometricPrompt.BIOMETRIC_ERROR_LOCKOUT, BiometricPrompt.BIOMETRIC_ERROR_LOCKOUT_PERMANENT -> TOO_MANY_TRIES
            BiometricPrompt.BIOMETRIC_ERROR_NO_DEVICE_CREDENTIAL -> NO_SCREEN_LOCK
            else -> UNAVAILABLE
        }

        /**
         * A prompt that reported success. Only a result Android labels as the screen lock counts; a fingerprint, face or
         * unknown result leaves the lock on.
         */
        fun fromSuccess(authenticationType: Int): UnlockOutcome =
            if (authenticationType == BiometricPrompt.AUTHENTICATION_RESULT_TYPE_DEVICE_CREDENTIAL) CONFIRMED else UNAVAILABLE

        /** App lock's rule: a fingerprint, face or the screen lock all count. An unknown result still doesn't. */
        fun fromAnyUnlock(authenticationType: Int): UnlockOutcome = when (authenticationType) {
            BiometricPrompt.AUTHENTICATION_RESULT_TYPE_BIOMETRIC, BiometricPrompt.AUTHENTICATION_RESULT_TYPE_DEVICE_CREDENTIAL -> CONFIRMED
            else -> UNAVAILABLE
        }
    }
}

/** What a tap on a Raticode notification or shortcut does. */
internal enum class NotificationTap {
    /** Not locked: goes where the notification points. */
    OPEN,
    /** Locked with the lock screen in place: Raticode comes forward and nothing underneath changes. */
    KEEP_LOCK,
    /** Locked, but Android ended the lock screen with the app: the main screen opens and locks itself. */
    OPEN_LOCKED;

    companion object {
        fun of(locked: Boolean, lockScreens: Int) = when {
            !locked -> OPEN
            lockScreens > 0 -> KEEP_LOCK
            else -> OPEN_LOCKED
        }
    }
}

/**
 * The child lock's state. It holds only whether Raticode is locked. The phone's pattern, PIN or password never reaches
 * the app; Android asks for it and reports the result.
 */
internal class ChildLock(private val store: ChildLockStore) {
    enum class TurnOn { LOCKED, NEEDS_SCREEN_LOCK, NOT_SAVED }

    var locked by mutableStateOf(store.read()); private set
    /** Why the last request to turn the lock on was refused; shown by the app until dismissed. */
    var refusal by mutableStateOf<TurnOn?>(null); private set

    /** Saved before it takes effect, so a lock that couldn't survive a relaunch is refused rather than half on. */
    fun turnOn(hasScreenLock: Boolean): TurnOn {
        val result = when {
            locked -> TurnOn.LOCKED
            // Without a screen lock nothing could ever turn this off again.
            !hasScreenLock -> TurnOn.NEEDS_SCREEN_LOCK
            !store.write(true) -> TurnOn.NOT_SAVED
            else -> { locked = true; TurnOn.LOCKED }
        }
        refusal = result.takeIf { it != TurnOn.LOCKED }
        return result
    }

    fun dismissRefusal() { refusal = null }

    /** Applies a prompt's result and returns what to show, or null once the lock is off. Nothing else turns it off. */
    fun finishUnlock(outcome: UnlockOutcome): UnlockOutcome? {
        if (outcome != UnlockOutcome.CONFIRMED) return outcome
        if (!store.write(false)) return UnlockOutcome.NOT_SAVED
        locked = false
        return null
    }

    companion object {
        const val BLOCKED_NOTE = "Raticode's child lock is on."
    }
}
