package dev.raticode.mobile.live

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.os.IBinder
import androidx.annotation.RequiresApi
import androidx.core.app.NotificationCompat
import dev.raticode.mobile.R

/** User-started, visible data-sync service. Android may still stop it or enforce a time limit. */
@RequiresApi(29)
class DeviceSyncService : Service() {
    override fun onCreate() {
        super.onCreate()
        val notifications = getSystemService(NotificationManager::class.java)
        notifications.createNotificationChannel(NotificationChannel(CHANNEL, "Desktop connection", NotificationManager.IMPORTANCE_LOW))
        val open = PendingIntent.getActivity(this, 0, dev.raticode.mobile.childlock.NotificationOpenActivity.intent(this), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val stop = PendingIntent.getService(this, 1, Intent(this, DeviceSyncService::class.java).setAction("stop"), PendingIntent.FLAG_IMMUTABLE)
        val notification = NotificationCompat.Builder(this, CHANNEL).setSmallIcon(R.drawable.ic_launcher)
            .setContentTitle("Raticode device sync")
            .setContentText("Desktop sync is enabled. Open the app for connection status.")
            .setVisibility(NotificationCompat.VISIBILITY_SECRET).setOngoing(true)
            .setContentIntent(open).addAction(0, "Stop sync", stop)
        if (dev.raticode.mobile.voice.VoiceActivity.supported) notification.addAction(R.drawable.ic_mic, "Talk to Rem",
            PendingIntent.getActivity(this, 2, dev.raticode.mobile.voice.VoiceActivity.intent(this).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK), PendingIntent.FLAG_IMMUTABLE))
        startForeground(2002, notification.build())
        LiveRuntime.client(application).backgroundEnabled = true
    }
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == "stop") {
            if (dev.raticode.mobile.childlock.ChildLockRuntime.locked(this)) {
                dev.raticode.mobile.childlock.ChildLockRuntime.toastBlocked(this)
                return START_STICKY
            }
            stopSelf()
            return START_NOT_STICKY
        }
        LiveRuntime.client(application).connect()
        return START_STICKY
    }
    override fun onTimeout(startId: Int, fgsType: Int) { stopSelf() }
    override fun onDestroy() {
        LiveRuntime.client(application).apply { backgroundEnabled = false; disconnect() }
        TaskNotification.clear(this)
        stopForeground(STOP_FOREGROUND_REMOVE)
        super.onDestroy()
    }
    override fun onBind(intent: Intent?): IBinder? = null
    companion object { private const val CHANNEL = "raticode-device-sync" }
}

@RequiresApi(29)
object LiveRuntime {
    private var instance: LiveClient? = null
    @Synchronized fun client(app: Application): LiveClient = instance ?: LiveClient(app).also { instance = it }
    /** Tests supply a client paired with their own fixture; voice mode then uses it. */
    @androidx.annotation.VisibleForTesting @Synchronized internal fun install(client: LiveClient) { instance = client }
}
