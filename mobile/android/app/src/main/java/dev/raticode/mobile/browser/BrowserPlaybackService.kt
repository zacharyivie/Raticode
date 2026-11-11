package dev.raticode.mobile.browser

import android.app.KeyguardManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.graphics.drawable.Icon
import android.media.MediaMetadata
import android.media.session.MediaSession
import android.media.session.PlaybackState
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import dev.raticode.mobile.R
import dev.raticode.mobile.applock.AppLockRuntime
import dev.raticode.mobile.browser.BrowserMediaRules.Action
import dev.raticode.mobile.childlock.ChildLockRuntime
import dev.raticode.mobile.childlock.NotificationOpenActivity

/**
 * Keeps a page in Raticode's browser playing after Raticode leaves the screen, as a music app does, and puts the media
 * bar's controls on the lock screen and in the notification shade: an Android media session and a media notification
 * with the title, picture, Previous, Play/Pause, Next and Stop. It shows exactly what [BrowserMedia.current] holds, so it
 * comes and goes with the in-app bar: it starts when a tab plays while Raticode is in front (Android doesn't let an app in
 * the background start one), stays through the bar's short hold after a pause so Play still works from the lock screen,
 * and ends when the bar does or the tab closes. The pages keep playing in [BrowserRuntime] and [GeckoBrowser]; every
 * button goes to the tab through [BrowserMedia.perform], and only buttons the page offers right now are shown or obeyed.
 *
 * A media notification needs no notification permission on Android 13 and newer, so the controls appear even when
 * Raticode's other notifications are blocked. A private tab shows "Private tab" and no picture everywhere, and while the
 * phone is locked the title and picture stay hidden when the user turned them off or App lock is on.
 */
class BrowserPlaybackService : Service() {
    private var session: MediaSession? = null
    private var foreground = false
    /** finish() ran: nothing more is shown unless a new start arrives. */
    private var ended = false
    /** The screen is off or locked, as far as Android has said. Starts from what Android says now. */
    private var phoneLocked = false
    private val main = Handler(Looper.getMainLooper())

    private val screen = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            phoneLocked = when (intent.action) {
                // The keyguard may lock a few seconds after the screen goes off; hide at once rather than guess.
                Intent.ACTION_SCREEN_OFF -> true
                Intent.ACTION_USER_PRESENT -> false
                else -> keyguardLocked()
            }
            render()
        }
    }

    private val controls = object : MediaSession.Callback() {
        override fun onPlay() = press(Action.PLAY)
        override fun onPause() = press(Action.PAUSE)
        override fun onSkipToNext() = press(Action.NEXT)
        override fun onSkipToPrevious() = press(Action.PREVIOUS)
        override fun onStop() = stopAll()
        override fun onCustomAction(action: String, extras: android.os.Bundle?) { if (action == ACTION_STOP) stopAll() }
    }

    override fun onCreate() {
        super.onCreate()
        running = this
        starting = false
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL, "Browser sound", NotificationManager.IMPORTANCE_LOW).apply { setShowBadge(false) })
        session = MediaSession(this, "Raticode browser").apply {
            setCallback(controls, main)
            setSessionActivity(openApp())
        }
        phoneLocked = keyguardLocked() || !getSystemService(PowerManager::class.java).isInteractive
        // Exported because Android's lock screen (System UI, an app of its own) sends USER_PRESENT, and a not-exported
        // receiver never hears it. All three are protected broadcasts that only the system can send.
        ContextCompat.registerReceiver(this, screen, IntentFilter().apply {
            addAction(Intent.ACTION_SCREEN_OFF); addAction(Intent.ACTION_SCREEN_ON); addAction(Intent.ACTION_USER_PRESENT)
        }, ContextCompat.RECEIVER_EXPORTED)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> stopAll()
            ACTION_PLAY -> press(Action.PLAY)
            ACTION_PAUSE -> press(Action.PAUSE)
            ACTION_NEXT -> press(Action.NEXT)
            ACTION_PREVIOUS -> press(Action.PREVIOUS)
            // Started by sync() with startForegroundService, which Android requires to end in startForeground, even when
            // the music stopped in the meantime.
            else -> {
                starting = false; ended = false; foreground = false
                render()
                if (!foreground) { startForeground(placeholder()); finish() }
            }
        }
        // A notification button pressed after the service ended has nothing to control.
        if (!foreground) finish()
        return START_NOT_STICKY
    }

    /** A button from the lock screen, the notification, a headset or a car. Only what the bar would offer now is done. */
    private fun press(action: Action) {
        val track = BrowserMedia.current ?: return
        if (ChildLockRuntime.locked(this)) { ChildLockRuntime.toastBlocked(this); return }
        if (!BrowserMediaRules.allowed(track, action)) { render(); return }
        BrowserMedia.perform(track, action)
    }

    /** Stop: every page's sound stops in both browsers, and the controls go with it. */
    private fun stopAll() {
        if (ChildLockRuntime.locked(this)) { ChildLockRuntime.toastBlocked(this); return }
        BrowserRuntime.peek()?.stopSound()
        GeckoBrowser.peek()?.stopSound()
        finish()
    }

    /** Shows [BrowserMedia.current], or ends the service when there is none. */
    private fun render() {
        if (ended) return
        val track = BrowserMedia.current
        val session = session
        if (track == null || session == null) { finish(); return }
        val hidden = BrowserMediaRules.hideOnLockScreen(phoneLocked, BrowserMedia.lockScreenTitles, AppLockRuntime.get(this).enabled)
        val shown = BrowserMediaRules.shown(track, hidden)
        val picture = BrowserMedia.bitmap(shown.artwork)
        val buttons = BrowserMediaRules.buttons(track)

        session.setMetadata(MediaMetadata.Builder()
            .putString(MediaMetadata.METADATA_KEY_TITLE, shown.title)
            .putString(MediaMetadata.METADATA_KEY_DISPLAY_TITLE, shown.title)
            .putString(MediaMetadata.METADATA_KEY_ARTIST, shown.subtitle)
            .putString(MediaMetadata.METADATA_KEY_DISPLAY_SUBTITLE, shown.subtitle)
            .apply { picture?.let { putBitmap(MediaMetadata.METADATA_KEY_ALBUM_ART, it); putBitmap(MediaMetadata.METADATA_KEY_DISPLAY_ICON, it) } }
            .build())
        // Android 13 and newer draw the lock screen and shade controls from these actions, not the notification's buttons.
        val actions = buttons.fold(PlaybackState.ACTION_STOP) { all, button -> all or playbackAction(button) }
        session.setPlaybackState(PlaybackState.Builder()
            .setActions(actions)
            .setState(if (track.playing) PlaybackState.STATE_PLAYING else PlaybackState.STATE_PAUSED, PlaybackState.PLAYBACK_POSITION_UNKNOWN, if (track.playing) 1f else 0f)
            .addCustomAction(PlaybackState.CustomAction.Builder(ACTION_STOP, "Stop", android.R.drawable.ic_menu_close_clear_cancel).build())
            .build())
        session.isActive = true

        val all = buttons.map { it to label(it) } + (null to "Stop")
        val builder = Notification.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_launcher)
            .setContentTitle(shown.title)
            .setContentText(shown.subtitle)
            .setLargeIcon(picture)
            .setCategory(Notification.CATEGORY_TRANSPORT)
            .setOngoing(true).setOnlyAlertOnce(true).setShowWhen(false)
            .setContentIntent(openApp())
            .setStyle(Notification.MediaStyle().setMediaSession(session.sessionToken)
                .setShowActionsInCompactView(*buttons.indices.take(3).toList().toIntArray()))
        all.forEachIndexed { index, (button, label) ->
            builder.addAction(Notification.Action.Builder(Icon.createWithResource(this, icon(button)), label,
                PendingIntent.getService(this, 10 + index, Intent(this, BrowserPlaybackService::class.java).setAction(intentAction(button)), PendingIntent.FLAG_IMMUTABLE)).build())
        }
        // Belt and braces for the notification card: with titles hidden on the lock screen, its locked version is generic
        // too, even before Android reports the screen turning off.
        if (track.private || !BrowserMedia.lockScreenTitles || AppLockRuntime.get(this).enabled) {
            val generic = BrowserMediaRules.shown(track, hidden = true)
            builder.setVisibility(Notification.VISIBILITY_PRIVATE).setPublicVersion(Notification.Builder(this, CHANNEL)
                .setSmallIcon(R.drawable.ic_launcher).setContentTitle(generic.title).setContentText(generic.subtitle).build())
        } else builder.setVisibility(Notification.VISIBILITY_PUBLIC)
        val notification = builder.build()
        if (foreground) getSystemService(NotificationManager::class.java).notify(NOTIFICATION, notification)
        else startForeground(notification)
    }

    private fun startForeground(notification: Notification) {
        try {
            ServiceCompat.startForeground(this, NOTIFICATION, notification,
                if (Build.VERSION.SDK_INT >= 29) ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK else 0)
            foreground = true
        } catch (_: RuntimeException) {
            // Android refused (ForegroundServiceStartNotAllowedException): the page plays on without the controls.
            foreground = false
        }
    }

    private fun placeholder() = Notification.Builder(this, CHANNEL).setSmallIcon(R.drawable.ic_launcher)
        .setContentTitle(BrowserMediaRules.HIDDEN_TITLE).setVisibility(Notification.VISIBILITY_PUBLIC).build()

    private fun openApp() = PendingIntent.getActivity(this, 0, NotificationOpenActivity.intent(this), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

    private fun keyguardLocked() = getSystemService(KeyguardManager::class.java).isKeyguardLocked

    private fun finish() {
        ended = true
        session?.isActive = false
        if (foreground) ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        foreground = false
        stopSelf()
    }

    override fun onTimeout(startId: Int, fgsType: Int) { finish() }

    override fun onDestroy() {
        if (running === this) { running = null; starting = false }
        runCatching { unregisterReceiver(screen) }
        session?.run { isActive = false; release() }
        session = null
        if (foreground) ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        foreground = false
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    companion object {
        const val TITLE = BrowserMediaRules.HIDDEN_TITLE
        private const val CHANNEL = "raticode-browser-sound"
        private const val NOTIFICATION = 2003
        private const val ACTION_STOP = "stop"
        private const val ACTION_PLAY = "play"
        private const val ACTION_PAUSE = "pause"
        private const val ACTION_NEXT = "next"
        private const val ACTION_PREVIOUS = "previous"

        /** The service while it runs. Main thread only. */
        private var running: BrowserPlaybackService? = null
        /** startForegroundService was called and the service hasn't been created yet. */
        private var starting = false

        /**
         * Brings the service in line with [BrowserMedia.current]: starts it when a tab plays, updates it, or ends it.
         * Starting can fail while Raticode is in the background, as Android requires; the page plays on either way.
         */
        fun sync(context: Context) {
            val service = running?.takeUnless { it.ended }
            val track = BrowserMedia.current
            when {
                service != null -> service.render()
                track?.playing == true && !starting -> starting = try {
                    ContextCompat.startForegroundService(context, Intent(context, BrowserPlaybackService::class.java))
                    true
                } catch (_: IllegalStateException) { false } catch (_: SecurityException) { false }
            }
        }

        private fun playbackAction(action: Action) = when (action) {
            Action.PLAY -> PlaybackState.ACTION_PLAY or PlaybackState.ACTION_PLAY_PAUSE
            Action.PAUSE -> PlaybackState.ACTION_PAUSE or PlaybackState.ACTION_PLAY_PAUSE
            Action.NEXT -> PlaybackState.ACTION_SKIP_TO_NEXT
            Action.PREVIOUS -> PlaybackState.ACTION_SKIP_TO_PREVIOUS
        }

        private fun label(action: Action) = when (action) {
            Action.PLAY -> "Play"
            Action.PAUSE -> "Pause"
            Action.NEXT -> "Next"
            Action.PREVIOUS -> "Previous"
        }

        private fun icon(action: Action?) = when (action) {
            Action.PLAY -> android.R.drawable.ic_media_play
            Action.PAUSE -> android.R.drawable.ic_media_pause
            Action.NEXT -> android.R.drawable.ic_media_next
            Action.PREVIOUS -> android.R.drawable.ic_media_previous
            null -> android.R.drawable.ic_menu_close_clear_cancel
        }

        private fun intentAction(action: Action?) = when (action) {
            Action.PLAY -> ACTION_PLAY
            Action.PAUSE -> ACTION_PAUSE
            Action.NEXT -> ACTION_NEXT
            Action.PREVIOUS -> ACTION_PREVIOUS
            null -> ACTION_STOP
        }
    }
}
