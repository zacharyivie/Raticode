package dev.raticode.mobile.live

import android.Manifest
import android.app.*
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import dev.raticode.mobile.R

object LiveNotifications {
    const val THREAD_EXTRA = "local_notification_thread"
    /** Reuses Raticode's main screen instead of stacking a second one, which would stop the first and end its full-screen video. */
    const val OPEN_FLAGS = Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
    fun enabled(context: Context): Boolean = context.getSharedPreferences("live-notifications", Context.MODE_PRIVATE)
        .getBoolean("enabled", false)

    fun setEnabled(context: Context, enabled: Boolean) {
        context.getSharedPreferences("live-notifications", Context.MODE_PRIVATE).edit().putBoolean("enabled", enabled).apply()
        if (!enabled) {
            val manager = context.getSystemService(NotificationManager::class.java)
            manager.activeNotifications.filter { it.id == 2003 }.forEach { manager.cancel(it.tag, it.id) }
        }
    }

    fun allowed(context: Context): Boolean {
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return false
        val manager = context.getSystemService(NotificationManager::class.java)
        return manager.areNotificationsEnabled() && manager.getNotificationChannel("desktop-messages")?.importance != NotificationManager.IMPORTANCE_NONE
    }

    /** Generic approval alert. Its text never names the workflow, swarm or request. */
    fun showApproval(context: Context) {
        if (!enabled(context) || !allowed(context)) return
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel("desktop-messages", "Desktop messages", NotificationManager.IMPORTANCE_DEFAULT).apply {
            lockscreenVisibility = Notification.VISIBILITY_SECRET
        })
        val intent = dev.raticode.mobile.childlock.NotificationOpenActivity.intent(context).putExtra(THREAD_EXTRA, "live:approvals")
            .setData(android.net.Uri.parse("raticode-live://approvals"))
        val pending = PendingIntent.getActivity(context, 1, intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        manager.notify("approvals", 2003, NotificationCompat.Builder(context, "desktop-messages").setSmallIcon(R.drawable.ic_launcher)
            .setContentTitle("Raticode").setContentText("A desktop approval is waiting.")
            .setVisibility(NotificationCompat.VISIBILITY_SECRET).setAutoCancel(true).setContentIntent(pending).build())
    }

    fun show(context: Context, thread: String) {
        if (!enabled(context) || !allowed(context)) return
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel("desktop-messages", "Desktop messages", NotificationManager.IMPORTANCE_DEFAULT).apply {
            lockscreenVisibility = Notification.VISIBILITY_SECRET
        })
        val intent = dev.raticode.mobile.childlock.NotificationOpenActivity.intent(context).putExtra(LiveNotifications.THREAD_EXTRA, "live:$thread")
            .setData(android.net.Uri.parse("raticode-live://thread/$thread"))
        val pending = PendingIntent.getActivity(context, 0, intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        manager.notify(thread, 2003, NotificationCompat.Builder(context, "desktop-messages").setSmallIcon(R.drawable.ic_launcher)
            .setContentTitle("Raticode").setContentText("A desktop response is ready.")
            .setVisibility(NotificationCompat.VISIBILITY_SECRET).setAutoCancel(true).setContentIntent(pending).build())
    }
}
