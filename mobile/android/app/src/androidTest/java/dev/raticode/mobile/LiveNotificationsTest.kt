package dev.raticode.mobile

import android.Manifest
import android.app.Notification
import android.app.NotificationManager
import android.os.Build
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.LiveNotifications
import org.junit.Assert.*
import org.junit.Test

class LiveNotificationsTest {
    @Test fun optInIsSeparateAndNotificationsContainNoConversationContent() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val manager = context.getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= 33) instrumentation.uiAutomation.grantRuntimePermission(context.packageName, Manifest.permission.POST_NOTIFICATIONS)
        val thread = java.util.UUID.randomUUID().toString()
        try {
            LiveNotifications.setEnabled(context, false)
            LiveNotifications.show(context, thread)
            assertFalse(manager.activeNotifications.any { it.tag == thread })
            LiveNotifications.setEnabled(context, true)
            LiveNotifications.show(context, thread)
            val deadline = android.os.SystemClock.elapsedRealtime() + 5000
            while (manager.activeNotifications.none { it.tag == thread } && android.os.SystemClock.elapsedRealtime() < deadline) Thread.sleep(20)
            val notification = manager.activeNotifications.single { it.tag == thread }.notification
            assertEquals("A desktop response is ready.", notification.extras.getString(Notification.EXTRA_TEXT))
            assertEquals(Notification.VISIBILITY_SECRET, notification.visibility)
            LiveNotifications.setEnabled(context, false)
            val cancelDeadline = android.os.SystemClock.elapsedRealtime() + 5000
            while (manager.activeNotifications.any { it.tag == thread } && android.os.SystemClock.elapsedRealtime() < cancelDeadline) Thread.sleep(20)
            assertFalse(manager.activeNotifications.any { it.tag == thread })
        } finally { LiveNotifications.setEnabled(context, false); manager.cancel(thread, 2003) }
    }
}
