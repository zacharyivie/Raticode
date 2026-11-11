package dev.raticode.mobile.ui

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import androidx.core.net.toUri
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import dev.raticode.mobile.MainActivity
import dev.raticode.mobile.R

object LocalNotifications {
    const val THREAD_EXTRA = "local_notification_thread"
    fun show(context: Context, threadId: String): Boolean {
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return false
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel("local-demo", "Local demo updates", NotificationManager.IMPORTANCE_DEFAULT).apply {
            description = "Explicit sample notifications. No desktop push connection."
            lockscreenVisibility = android.app.Notification.VISIBILITY_SECRET
        })
        if (!NotificationManagerCompat.from(context).areNotificationsEnabled() || manager.getNotificationChannel("local-demo").importance == NotificationManager.IMPORTANCE_NONE) return false
        val intent = Intent(context, MainActivity::class.java).apply {
            data = "raticode-local://thread/$threadId".toUri()
            putExtra(THREAD_EXTRA, threadId)
            flags = Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
        }
        val pending = PendingIntent.getActivity(context, 0, intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        manager.notify(threadId, 1, NotificationCompat.Builder(context, "local-demo")
            .setSmallIcon(R.drawable.ic_launcher).setContentTitle("Raticode local demo")
            .setContentText("A sample update is ready. Open Raticode to view it.")
            .setVisibility(NotificationCompat.VISIBILITY_SECRET).setAutoCancel(true).setContentIntent(pending).build())
        return true
    }
    fun clear(context: Context) = context.getSystemService(NotificationManager::class.java).cancelAll()
}
