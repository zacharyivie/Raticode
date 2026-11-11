package dev.raticode.mobile.applock

import android.app.Activity
import android.app.Application
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.os.SystemClock
import android.provider.Settings
import androidx.annotation.RequiresApi
import dev.raticode.mobile.childlock.ChildLockActivity
import dev.raticode.mobile.childlock.NotificationOpenActivity
import dev.raticode.mobile.voice.VoiceActivity

/** The one app lock for this process, and the watcher that sees Raticode's screens come and go. */
internal object AppLockRuntime {
    @Volatile private var instance: AppLock? = null
    private var heldOpen: Intent? = null

    fun get(context: Context): AppLock = instance ?: synchronized(this) {
        instance ?: AppLock(PreferenceStore(context.applicationContext), PhoneClock(context.applicationContext)).also { instance = it }
    }

    fun locked(context: Context) = get(context).locked

    /**
     * Keeps a tapped notification's destination for after the unlock, when a lock screen is already showing. Returns
     * false when there isn't one, because opening the main screen then asks by itself.
     */
    fun holdUntilUnlocked(context: Context, open: Intent): Boolean {
        val lock = get(context)
        if (!lock.locked || lock.lockScreens == 0) return false
        heldOpen = Intent(open).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return true
    }

    fun takeHeldOpen(): Intent? = heldOpen.also { heldOpen = null }

    @RequiresApi(29)
    fun install(app: Application) {
        app.registerActivityLifecycleCallbacks(object : Application.ActivityLifecycleCallbacks {
            override fun onActivityStarted(activity: Activity) {
                if (!counted(activity)) return
                val lock = get(activity)
                lock.activityStarted()
                // ChildLockActivity always has a covered screen beneath it, which starts this lock.
                if (lock.locked && activity !is AppLockActivity && activity !is ChildLockActivity) AppLockActivity.show(activity)
            }
            override fun onActivityStopped(activity: Activity) {
                if (counted(activity)) get(activity).activityStopped(activity.isChangingConfigurations)
            }
            override fun onActivityCreated(activity: Activity, savedInstanceState: Bundle?) = Unit
            override fun onActivityResumed(activity: Activity) = Unit
            override fun onActivityPaused(activity: Activity) = Unit
            override fun onActivitySaveInstanceState(activity: Activity, outState: Bundle) = Unit
            override fun onActivityDestroyed(activity: Activity) = Unit
        })
    }

    /** Voice mode keeps its own unlock rule. The notification screen has no window and closes at once. */
    private fun counted(activity: Activity) = activity !is VoiceActivity && activity !is NotificationOpenActivity

    private class PhoneClock(private val context: Context) : AppLockClock {
        override fun now() = SystemClock.elapsedRealtime()
        override fun boot() = Settings.Global.getInt(context.contentResolver, Settings.Global.BOOT_COUNT, -1)
    }

    private class PreferenceStore(context: Context) : AppLockStore {
        private val prefs = context.getSharedPreferences("app_lock", Context.MODE_PRIVATE)
        override val enabled get() = prefs.getBoolean(ENABLED, false)
        override val minutes get() = prefs.getInt(MINUTES, AppLockDelay.DEFAULT.minutes)
        override val leftAt: AppLockLeftAt? get() = if (!prefs.contains(LEFT_ELAPSED)) null else
            AppLockLeftAt(prefs.getLong(LEFT_ELAPSED, 0), prefs.getInt(LEFT_BOOT, -1))
        // commit, not apply: Android may end the app as soon as it has left the screen.
        override fun saveSetting(enabled: Boolean, minutes: Int) = prefs.edit().putBoolean(ENABLED, enabled).putInt(MINUTES, minutes).commit()
        override fun saveLeftAt(at: AppLockLeftAt?) = prefs.edit().apply {
            if (at == null) remove(LEFT_ELAPSED).remove(LEFT_BOOT) else putLong(LEFT_ELAPSED, at.elapsed).putInt(LEFT_BOOT, at.boot)
        }.commit()
        private companion object {
            const val ENABLED = "enabled"
            const val MINUTES = "minutes"
            const val LEFT_ELAPSED = "left_elapsed"
            const val LEFT_BOOT = "left_boot"
        }
    }
}
