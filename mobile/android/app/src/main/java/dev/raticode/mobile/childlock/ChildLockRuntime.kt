package dev.raticode.mobile.childlock

import android.app.Activity
import android.app.KeyguardManager
import android.content.Context
import android.widget.Toast
import androidx.annotation.RequiresApi

/** The one child lock for this app, read from storage the first time anything asks. */
internal object ChildLockRuntime {
    @Volatile private var instance: ChildLock? = null
    /** Lock screens alive in this process. It stays on the main thread, like the activities that count themselves. */
    var lockScreens = 0

    fun get(context: Context): ChildLock = instance ?: synchronized(this) {
        instance ?: ChildLock(PreferenceStore(context.applicationContext)).also { instance = it }
    }

    fun locked(context: Context) = get(context).locked

    fun hasScreenLock(context: Context) = context.getSystemService(KeyguardManager::class.java).isDeviceSecure

    fun toastBlocked(context: Context) { Toast.makeText(context, ChildLock.BLOCKED_NOTE, Toast.LENGTH_SHORT).show() }

    /** Turns the lock on and covers Raticode, or records why it can't. [context] must be an activity. */
    @RequiresApi(29)
    fun engage(context: Context) {
        if (get(context).turnOn(hasScreenLock(context)) != ChildLock.TurnOn.LOCKED) return
        // The lock screen's handle comes out with an open padlock and closes it, so the user sees the same lock change.
        EdgeHandle.justLocked = true
        EdgeHandle.show()
        ChildLockActivity.show(context)
    }

    private class PreferenceStore(context: Context) : ChildLockStore {
        private val prefs = context.getSharedPreferences("child_lock", Context.MODE_PRIVATE)
        override fun read() = prefs.getBoolean(KEY, false)
        // commit, not apply: the lock must be on disk before the screen says it is on.
        override fun write(locked: Boolean) = prefs.edit().putBoolean(KEY, locked).commit()
        private companion object { const val KEY = "locked" }
    }
}

/**
 * For a screen that opens outside Raticode's main window, such as the share sheet or voice mode. While the lock is on
 * it says so and closes the screen, returning true; the caller then stops.
 */
internal fun Activity.closedByChildLock(): Boolean {
    if (!ChildLockRuntime.locked(this)) return false
    ChildLockRuntime.toastBlocked(this)
    finish()
    return true
}
