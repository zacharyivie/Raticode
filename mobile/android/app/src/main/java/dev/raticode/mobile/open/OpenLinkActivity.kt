package dev.raticode.mobile.open

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import dev.raticode.mobile.MainActivity
import dev.raticode.mobile.applock.AppLockRuntime
import dev.raticode.mobile.childlock.ChildLockRuntime
import dev.raticode.mobile.childlock.NotificationTap
import dev.raticode.mobile.live.LiveNotifications

/**
 * Raticode as the phone's web browser. Android sends web links here once the user makes Raticode the default browser
 * (Settings › Open with Raticode) or picks it when asked. This screen has no window: it hands the link to the main
 * screen, which opens it as a new tab in Raticode (chromium) or Raticode (gecko), whichever the user chose for links from
 * other apps. Like a notification tap, it leaves the child lock and app lock in place: while the child lock is on the
 * link isn't opened, and app lock opens it once the phone's owner unlocks Raticode.
 */
class OpenLinkActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val url = OpenTypes.webLink(intent?.takeIf { it.action == Intent.ACTION_VIEW }?.dataString)
        if (url == null) {
            Toast.makeText(this, "Raticode can only open web links (http and https).", Toast.LENGTH_LONG).show()
            finish()
            return
        }
        when (NotificationTap.of(ChildLockRuntime.locked(this), ChildLockRuntime.lockScreens)) {
            NotificationTap.KEEP_LOCK -> Toast.makeText(this, "Raticode is locked, so the link didn't open.", Toast.LENGTH_LONG).show()
            NotificationTap.OPEN_LOCKED -> {
                Toast.makeText(this, "Raticode is locked, so the link didn't open.", Toast.LENGTH_LONG).show()
                startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or LiveNotifications.OPEN_FLAGS))
            }
            NotificationTap.OPEN -> {
                val open = openIntent(url)
                // Starting the main screen would clear an app lock already showing. Its unlock opens the link instead.
                if (!AppLockRuntime.holdUntilUnlocked(this, open)) startActivity(open)
            }
        }
        finish()
    }

    private fun openIntent(url: String) = Intent(this, MainActivity::class.java)
        .setAction(ACTION_OPEN_LINK)
        .putExtra(EXTRA_URL, url)
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or LiveNotifications.OPEN_FLAGS)

    companion object {
        const val ACTION_OPEN_LINK = "dev.raticode.mobile.action.OPEN_LINK"
        const val EXTRA_URL = "dev.raticode.mobile.extra.LINK_URL"

        /** The link a main-screen intent carries from this screen, checked again, or null. */
        fun linkFrom(intent: Intent?): String? =
            intent?.takeIf { it.action == ACTION_OPEN_LINK }?.getStringExtra(EXTRA_URL)?.let(OpenTypes::webLink)
    }
}
