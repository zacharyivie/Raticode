package dev.raticode.mobile.applock

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import dev.raticode.mobile.childlock.UnlockOutcome

/** How long Raticode may be away from the screen before opening it asks again. */
internal enum class AppLockDelay(val minutes: Int, val label: String) {
    IMMEDIATELY(0, "Immediately"),
    ONE_MINUTE(1, "After 1 minute"),
    FIVE_MINUTES(5, "After 5 minutes"),
    THIRTY_MINUTES(30, "After 30 minutes");

    val millis get() = minutes * 60_000L

    companion object {
        val DEFAULT = ONE_MINUTE
        /** A saved choice. Anything unreadable becomes the strictest choice, not the loosest. */
        fun of(minutes: Int) = entries.firstOrNull { it.minutes == minutes } ?: IMMEDIATELY
    }
}

/** When Raticode left the screen: time since the phone started, and which start that was. */
internal data class AppLockLeftAt(val elapsed: Long, val boot: Int)

/** The phone's clock. Time since boot can't be changed from Android's settings, unlike the wall clock. */
internal interface AppLockClock {
    fun now(): Long
    /** How many times the phone has started, or a negative number when Android won't say. */
    fun boot(): Int
}

/** Where the setting and the moment Raticode left the screen are kept. Writes return false when they fail. */
internal interface AppLockStore {
    val enabled: Boolean
    val minutes: Int
    val leftAt: AppLockLeftAt?
    fun saveSetting(enabled: Boolean, minutes: Int): Boolean
    fun saveLeftAt(at: AppLockLeftAt?): Boolean
}

/**
 * Whether Raticode has to ask who is holding the phone. It holds no secret and doesn't know the screen lock: Android asks
 * and reports the result. Lifecycle calls come from every Raticode screen except voice mode, which keeps its own unlock
 * rule. Every doubt locks: a crash, a restart of the phone, an unreadable record or a clock going backwards.
 */
internal class AppLock(private val store: AppLockStore, private val clock: AppLockClock) {
    var enabled by mutableStateOf(store.enabled); private set
    var delay by mutableStateOf(AppLockDelay.of(store.minutes)); private set
    /** Raticode must stay covered. Only [finishUnlock] ends this. */
    var locked by mutableStateOf(store.enabled && due()); private set
    /** Lock screens alive in this process. It stays on the main thread, like the activities that count themselves. */
    var lockScreens = 0

    private var inside = 0
    private var rotating = 0
    private var promptSince: Long? = null

    /** A Raticode screen became visible. The first one after being away decides whether to ask. */
    fun activityStarted() {
        if (rotating > 0) { rotating--; return }
        if (inside++ > 0 || !enabled) return
        if (!promptIsOpen() && due()) locked = true
        // Cleared on the way in, so a crash leaves no record and the next launch asks.
        if (store.leftAt != null) store.saveLeftAt(null)
    }

    /** A Raticode screen stopped being visible. A rotation is the same screen coming straight back. */
    fun activityStopped(changingConfigurations: Boolean) {
        if (changingConfigurations) { rotating++; return }
        if (inside > 0) inside--
        if (inside == 0 && enabled) store.saveLeftAt(AppLockLeftAt(clock.now(), clock.boot()))
    }

    /** Android's confirmation is on screen. Coming back from it, for a short while, isn't opening the app. */
    fun beginPrompt() { promptSince = clock.now() }
    fun endPrompt() { promptSince = null }

    /** Applies a prompt's result and returns what to show, or null once unlocked. Nothing else unlocks. */
    fun finishUnlock(outcome: UnlockOutcome): UnlockOutcome? {
        if (outcome != UnlockOutcome.CONFIRMED) return outcome
        locked = false
        return null
    }

    /** Turning the lock on or off, or giving it longer, asks first. Making it stricter doesn't. */
    fun needsProof(on: Boolean, newDelay: AppLockDelay) = on != enabled || (enabled && newDelay.millis > delay.millis)

    /** Saves first, so a setting that couldn't survive a relaunch doesn't look applied. */
    fun change(on: Boolean, newDelay: AppLockDelay): Boolean {
        if (!store.saveSetting(on, newDelay.minutes)) return false
        enabled = on
        delay = newDelay
        if (!on) store.saveLeftAt(null)
        return true
    }

    private fun promptIsOpen() = promptSince?.let { clock.now() - it in 0..PROMPT_GRACE_MS } == true

    private fun due(): Boolean {
        val left = store.leftAt ?: return true
        val boot = clock.boot()
        if (boot < 0 || left.boot != boot) return true
        val idle = clock.now() - left.elapsed
        return idle < 0 || idle >= AppLockDelay.of(store.minutes).millis
    }

    private companion object { const val PROMPT_GRACE_MS = 120_000L }
}

/** What the lock screen says after a prompt that didn't unlock. */
internal fun UnlockOutcome.appLockText(): String? = when (this) {
    UnlockOutcome.CONFIRMED -> null
    UnlockOutcome.CANCELLED -> "Not unlocked. Tap Unlock to try again."
    UnlockOutcome.TOO_MANY_TRIES -> "Too many wrong tries. Wait a while, then try again."
    UnlockOutcome.NO_SCREEN_LOCK -> "This phone has no screen lock, so Raticode can't confirm it's you. Set one in Android settings, then unlock."
    UnlockOutcome.UNAVAILABLE -> "Android couldn't ask for your fingerprint, face or screen lock. Try again."
    UnlockOutcome.NOT_SAVED -> "Raticode couldn't save that. Free up some storage and try again."
}

/** What the App lock settings page says after a change that didn't go through. */
internal fun UnlockOutcome.appLockSettingsText(): String? = when (this) {
    UnlockOutcome.CONFIRMED -> null
    UnlockOutcome.CANCELLED -> "Not confirmed, so nothing changed."
    UnlockOutcome.TOO_MANY_TRIES -> "Too many wrong tries, so nothing changed. Wait a while, then try again."
    UnlockOutcome.NO_SCREEN_LOCK -> "App lock needs a fingerprint, face or screen lock. This phone has no screen lock yet, so nothing changed."
    UnlockOutcome.UNAVAILABLE -> "Android couldn't ask for your fingerprint, face or screen lock, so nothing changed. Try again."
    UnlockOutcome.NOT_SAVED -> "Raticode couldn't save that, so nothing changed. Free up some storage and try again."
}
