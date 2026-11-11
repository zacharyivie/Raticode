package dev.raticode.mobile.childlock

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.os.Bundle
import dev.raticode.mobile.MainActivity
import dev.raticode.mobile.applock.AppLockRuntime
import dev.raticode.mobile.live.LiveNotifications

/**
 * Every Raticode notification and shortcut opens this private screen, which has no window and closes at once. A launch
 * aimed straight at the main screen has to clear what is above it, and that would take the child lock away for a moment
 * while a dialog below it is still touchable. From here, a tap while locked brings Raticode forward with the lock untouched.
 */
class NotificationOpenActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        when (NotificationTap.of(ChildLockRuntime.locked(this), ChildLockRuntime.lockScreens)) {
            NotificationTap.KEEP_LOCK -> Unit
            // Android ended Raticode: the main screen opens without the notification's destination and locks itself.
            NotificationTap.OPEN_LOCKED -> startActivity(Intent(this, MainActivity::class.java).addFlags(LiveNotifications.OPEN_FLAGS))
            NotificationTap.OPEN -> {
                val open = Intent(this, MainActivity::class.java).setAction(intent.action).setData(intent.data)
                    .apply { intent.getStringExtra(LiveNotifications.THREAD_EXTRA)?.let { putExtra(LiveNotifications.THREAD_EXTRA, it) } }
                    .addFlags(LiveNotifications.OPEN_FLAGS)
                // Starting the main screen would clear an app lock already showing. Its unlock opens this instead.
                if (!AppLockRuntime.holdUntilUnlocked(this, open)) startActivity(open)
            }
        }
        finish()
    }

    companion object {
        fun intent(context: Context) = Intent(context, NotificationOpenActivity::class.java)
    }
}
