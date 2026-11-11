package dev.raticode.mobile.live

import android.Manifest
import android.app.Application
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import dev.raticode.mobile.R

/**
 * The one notification for tasks started from this phone. Android 16 shows it as a Live Update with the current step
 * in a status-bar chip; older phones get an ordinary ongoing notification. Android counts up the elapsed time itself,
 * so nothing wakes the app between steps. What it says is decided in [TaskProgress].
 */
object TaskNotification {
    const val CHANNEL = "task-progress"
    const val ID = 2005
    private const val SDK_LIVE_UPDATES = 36
    @Volatile private var shown: TaskProgress.Frame? = null

    val showing: Boolean get() = shown != null
    val shownRuns: List<String> get() = shown?.runs.orEmpty()

    /** Rem's words count only while Rem's alerts are on, since Show Rem's words is hidden with them. */
    fun showWords(context: Context) = RemNotifications.enabled(context) && RemNotifications.showText(context)

    /** Android lets this app show the notification: its permission, the app's switch and this category's. */
    fun allowed(context: Context): Boolean {
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return false
        val manager = context.getSystemService(NotificationManager::class.java)
        return manager.areNotificationsEnabled() && manager.getNotificationChannel(CHANNEL)?.importance != NotificationManager.IMPORTANCE_NONE
    }

    /** Shows [frame], unless it is what's already there: a step changing is the only reason to touch the notification. */
    fun post(context: Context, frame: TaskProgress.Frame) {
        if (frame == shown || !allowed(context)) return
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(CHANNEL, "Task progress", NotificationManager.IMPORTANCE_LOW).apply {
            description = "Plan, Work, Check and Report for tasks you started from this phone"
            lockscreenVisibility = Notification.VISIBILITY_PRIVATE
        })
        manager.notify(ID, build(context, frame))
        shown = frame
    }

    fun clear(context: Context) {
        shown = null
        context.getSystemService(NotificationManager::class.java).cancel(ID)
    }

    /** Takes a task's name off the notification at once when the user hides Rem's words. */
    fun hideWords(context: Context) { shown?.takeIf { it.words }?.let { post(context, it.withoutWords()) } }

    /** [sdk] is a parameter so a test can check what older phones get. */
    internal fun build(context: Context, frame: TaskProgress.Frame, sdk: Int = Build.VERSION.SDK_INT, now: Long = System.currentTimeMillis()): Notification {
        val open = PendingIntent.getActivity(context, 6, dev.raticode.mobile.childlock.NotificationOpenActivity.intent(context).setAction(Intent.ACTION_VIEW)
            .putExtra(LiveNotifications.THREAD_EXTRA, "live:rem").setData(Uri.parse("raticode-live://rem")), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        // Android 13 and older don't let an ongoing notification be swiped away, so Hide is the way to dismiss it there.
        val hide = PendingIntent.getBroadcast(context, 7, Intent(context, TaskProgressReceiver::class.java).setPackage(context.packageName),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val public = NotificationCompat.Builder(context, CHANNEL).setSmallIcon(R.drawable.ic_launcher)
            .setContentTitle(TaskProgress.LOCK_TITLE).setContentText(TaskProgress.LOCK_TEXT).build()
        val builder = NotificationCompat.Builder(context, CHANNEL).setSmallIcon(R.drawable.ic_launcher)
            .setContentTitle(frame.title).setContentText(frame.text).setTicker(frame.spoken)
            .setCategory(NotificationCompat.CATEGORY_PROGRESS).setOngoing(true).setOnlyAlertOnce(true)
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE).setPublicVersion(public)
            .setContentIntent(open).setDeleteIntent(hide).addAction(0, "Hide", hide)
        // The desktop's clock can run ahead of the phone's; a start time in the future would count down.
        frame.startedAt?.let { builder.setWhen(minOf(it, now)).setShowWhen(true).setUsesChronometer(true) }
        if (sdk >= SDK_LIVE_UPDATES) {
            builder.setRequestPromotedOngoing(true).setShortCriticalText(frame.chip)
                .setStyle(NotificationCompat.ProgressStyle().setStyledByProgress(true).setProgress(frame.done)
                    .setProgressSegments(List(TaskProgress.STEPS) { NotificationCompat.ProgressStyle.Segment(1) }))
        } else {
            // Older phones show only the step line: a progress bar replaces the text when collapsed, and Android folding
            // this under the sync notification cuts it off, so it has its own group.
            builder.setGroup(CHANNEL)
        }
        return builder.build()
    }
}

/** Hide on the notification, and swiping it away where Android allows that. Not exported: only that notification's PendingIntent reaches it. */
class TaskProgressReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (Build.VERSION.SDK_INT >= 29) LiveRuntime.client(context.applicationContext as Application).hideTaskProgress()
    }
}
