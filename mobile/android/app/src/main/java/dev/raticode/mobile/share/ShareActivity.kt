package dev.raticode.mobile.share

import android.content.Intent
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Box
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.Modifier
import androidx.lifecycle.ViewModelProvider
import dev.raticode.mobile.MainActivity
import dev.raticode.mobile.applock.AppLockRuntime
import dev.raticode.mobile.childlock.closedByChildLock
import dev.raticode.mobile.live.LiveNotifications
import dev.raticode.mobile.ui.AppLockCover
import dev.raticode.mobile.ui.RaticodeTheme
import dev.raticode.mobile.ui.SharePanel

/**
 * Android's share sheet opens this over the app you share from. It shows what arrived for review and sends nothing
 * until you tap Send. It never shows over the lock screen, ignores every extra except the shared text and files, and
 * reads the files only while it holds the read grant Android gave it, which ends when this screen's task does.
 */
class ShareActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (closedByChildLock()) return
        enableEdgeToEdge(statusBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT))
        // Shared pictures and notes stay out of the recent apps preview.
        if (Build.VERSION.SDK_INT >= 33) setRecentsScreenshotEnabled(false)
        // The manifest turns this screen off below Android 10, which can't hold the live connection.
        if (Build.VERSION.SDK_INT < 29) { finish(); return }
        val session = ViewModelProvider(this)[ShareSession::class.java]
        session.load(intent, packageName)
        val appLock = AppLockRuntime.get(this)
        setContent { RaticodeTheme {
            // App lock opens over this screen by itself; it sends nothing until the user is past it and taps Send.
            val locked = appLock.locked
            Box(if (locked) Modifier.clearAndSetSemantics { } else Modifier) { SharePanel(session, onOpenRem = ::openRem, onClose = ::finish) }
            if (locked) AppLockCover()
        } }
    }

    // Reopened from Recents after the lock was turned on.
    override fun onResume() { super.onResume(); closedByChildLock() }
    override fun onStart() { super.onStart(); if (Build.VERSION.SDK_INT >= 29) ViewModelProvider(this)[ShareSession::class.java].attach() }
    override fun onStop() { if (Build.VERSION.SDK_INT >= 29) ViewModelProvider(this)[ShareSession::class.java].detach(); super.onStop() }

    private fun openRem() {
        startActivity(Intent(this, MainActivity::class.java).putExtra(LiveNotifications.THREAD_EXTRA, "live:rem")
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP))
        finish()
    }
}
