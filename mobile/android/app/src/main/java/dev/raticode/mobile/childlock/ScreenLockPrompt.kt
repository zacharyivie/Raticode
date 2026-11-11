package dev.raticode.mobile.childlock

import android.app.Activity
import android.app.KeyguardManager
import android.content.Intent
import android.hardware.biometrics.BiometricManager
import android.hardware.biometrics.BiometricPrompt
import android.os.Build
import android.os.CancellationSignal
import androidx.annotation.RequiresApi

/** What Android may accept when a lock asks who is holding the phone. */
internal enum class ScreenLockProof {
    /** The pattern, PIN or password only: a fingerprint or face could belong to someone else who is enrolled. */
    SCREEN_LOCK_ONLY,
    /** Fingerprint, face or the screen lock, whichever the phone offers. */
    ANY_PHONE_UNLOCK,
}

/**
 * Asks Android to confirm the phone's owner, for Child lock and App lock. Android collects the pattern, PIN, password,
 * fingerprint or face; Raticode only gets the result. Android 10 can't limit [BiometricPrompt] to the screen lock, so it
 * uses Android's older confirmation screen, which may also offer an enrolled fingerprint or face. That screen is another
 * activity, so its result comes back through [startConfirmation] and [confirmationFinished].
 */
@RequiresApi(29)
internal class ScreenLockPrompt(private val activity: Activity, var startConfirmation: (Intent) -> Unit = {}) {
    private var signal: CancellationSignal? = null
    private var onResult: ((UnlockOutcome) -> Unit)? = null

    fun ask(title: String, subtitle: String, proof: ScreenLockProof, onResult: (UnlockOutcome) -> Unit) {
        this.onResult = onResult
        try {
            if (Build.VERSION.SDK_INT >= 30) askWithPrompt(title, subtitle, proof) else askWithConfirmation(title, subtitle)
        } catch (_: RuntimeException) {
            // Includes a phone with no confirmation screen. Any failure to ask leaves the lock on.
            finished(UnlockOutcome.UNAVAILABLE)
        }
    }

    /** The result of the Android 10 confirmation screen. */
    fun confirmationFinished(resultCode: Int) = finished(if (resultCode == Activity.RESULT_OK) UnlockOutcome.CONFIRMED else UnlockOutcome.CANCELLED)

    /** Closes a prompt still on screen; it reports back as cancelled. */
    fun cancel() { signal?.cancel(); signal = null }

    @RequiresApi(30)
    private fun askWithPrompt(title: String, subtitle: String, proof: ScreenLockProof) {
        val cancel = CancellationSignal().also { signal = it }
        BiometricPrompt.Builder(activity)
            .setTitle(title)
            .setSubtitle(subtitle)
            .setAllowedAuthenticators(when (proof) {
                ScreenLockProof.SCREEN_LOCK_ONLY -> BiometricManager.Authenticators.DEVICE_CREDENTIAL
                // WEAK, not STRONG: much face unlock is class 2, and no key is released by this result.
                ScreenLockProof.ANY_PHONE_UNLOCK -> BiometricManager.Authenticators.BIOMETRIC_WEAK or BiometricManager.Authenticators.DEVICE_CREDENTIAL
            })
            .build()
            .authenticate(cancel, activity.mainExecutor, object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) = finished(when (proof) {
                    ScreenLockProof.SCREEN_LOCK_ONLY -> UnlockOutcome.fromSuccess(result.authenticationType)
                    ScreenLockProof.ANY_PHONE_UNLOCK -> UnlockOutcome.fromAnyUnlock(result.authenticationType)
                })
                override fun onAuthenticationError(errorCode: Int, errString: CharSequence?) = finished(UnlockOutcome.fromError(errorCode))
                // onAuthenticationFailed is a failed try Android lets the user repeat: the prompt stays up and the lock stays on.
            })
    }

    @Suppress("DEPRECATION")
    private fun askWithConfirmation(title: String, subtitle: String) {
        val intent = activity.getSystemService(KeyguardManager::class.java).createConfirmDeviceCredentialIntent(title, subtitle)
        if (intent == null) finished(UnlockOutcome.NO_SCREEN_LOCK) else startConfirmation(intent)
    }

    private fun finished(outcome: UnlockOutcome) {
        signal = null
        val done = onResult ?: return
        onResult = null
        done(outcome)
    }
}
