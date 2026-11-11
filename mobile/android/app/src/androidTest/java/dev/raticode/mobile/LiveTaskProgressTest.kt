package dev.raticode.mobile

import android.Manifest
import android.app.Application
import android.app.Notification
import android.app.NotificationManager
import android.os.Build
import androidx.lifecycle.ViewModelStore
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.*
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * The task progress notification, against `live-device-fixture.py --workspace --remote --desktop --task-progress`, on the desktop's real control code.
 * No screen is open, as when the phone is in a pocket: only the real sync service keeps the app going. It also avoids Compose's test rule,
 * whose Espresso crashes on Android 16.1 (`InputManager.getInstance`).
 */
class LiveTaskProgressTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val app get() = instrumentation.targetContext.applicationContext as Application
    private val manager get() = app.getSystemService(NotificationManager::class.java)

    private val sync get() = android.content.Intent(app, DeviceSyncService::class.java)
    private fun shell(command: String) = instrumentation.uiAutomation.executeShellCommand(command).close()
    private fun progress() = manager.activeNotifications.firstOrNull { it.id == TaskNotification.ID }?.notification
    private fun title(notification: Notification?) = notification?.extras?.getCharSequence(Notification.EXTRA_TITLE)?.toString()
    private fun text(notification: Notification?) = notification?.extras?.getCharSequence(Notification.EXTRA_TEXT)?.toString()
    /** Android 16.1 added Live Updates; on 16.0 the framework has no promotion methods to call. */
    private val liveUpdates get() = Build.VERSION.SDK_INT >= 36 && Build.VERSION.SDK_INT_FULL >= Build.VERSION_CODES_FULL.BAKLAVA_1
    private fun chip(notification: Notification?) = notification?.extras?.getString("android.shortCriticalText")
    private fun screenshot(name: String) = instrumentation.uiAutomation.executeShellCommand("screencap -p /data/local/tmp/raticode-$name").use {
        android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
    }
    private fun waitFor(millis: Long) = android.os.SystemClock.sleep(millis)
    private fun waitUntil(millis: Long, detail: () -> String = { "" }, condition: () -> Boolean) {
        val until = android.os.SystemClock.elapsedRealtime() + millis
        while (!condition()) {
            assertTrue("Condition still not satisfied after $millis ms ${detail()}", android.os.SystemClock.elapsedRealtime() < until)
            waitFor(200)
        }
    }

    private fun connect(path: String): LiveClient {
        DeviceStore(app).reset()
        if (Build.VERSION.SDK_INT >= 33) instrumentation.uiAutomation.grantRuntimePermission(app.packageName, Manifest.permission.POST_NOTIFICATIONS)
        RemNotifications.setEnabled(app, false)
        RemNotifications.setShowText(app, false)
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        instrumentation.runOnMainSync {
            client = LiveClient(app); LiveRuntime.install(client)
            owners.put("progress", client)
            client.preview(File(path).readText()); client.pair()
        }
        waitUntil(30000, { "status: ${client.status}" }) { client.connected && client.desktopControl }
        // The real service, as Enable background sync starts it; it uses the client installed above.
        androidx.core.content.ContextCompat.startForegroundService(app, sync)
        waitUntil(10000) { client.backgroundEnabled }
        return client
    }

    @Test fun aPhoneStartedTaskShowsItsStepsAndStaysHiddenOnceDismissed() {
        val path = InstrumentationRegistry.getArguments().getString("taskProgressInvitationFile")
        assumeTrue("Requires a disposable fixture with --task-progress", path != null)
        val client = connect(path!!)
        try {
            client.sendRemMessage("Start progress", emptyList())
            waitUntil(60000) { progress() != null }
            val first = progress()!!
            // One run: the fixture's self-work run and its desktop-started run both stay out of it.
            assertEquals("Rem is working on your task", title(first))
            val steps = text(first).orEmpty()
            listOf("Plan", "Work", "Check", "Report").forEach { assertTrue("$it in $steps", it in steps) }
            assertFalse("the task's name leaked: $steps", "Fixture" in steps)
            assertTrue("ongoing", first.flags and Notification.FLAG_ONGOING_EVENT != 0)
            assertEquals("Rem is working on a task", first.publicVersion.extras.getString(Notification.EXTRA_TEXT))
            assertTrue("elapsed time", first.extras.getBoolean(Notification.EXTRA_SHOW_CHRONOMETER))
            assertFalse("chronometer counts up", first.extras.getBoolean(Notification.EXTRA_CHRONOMETER_COUNT_DOWN))
            assertTrue("started in the past", first.`when` <= System.currentTimeMillis())
            assertNotNull("detects a swipe", first.deleteIntent)
            assertEquals(NotificationManager.IMPORTANCE_LOW, manager.getNotificationChannel(TaskNotification.CHANNEL).importance)
            if (Build.VERSION.SDK_INT >= 36) {
                assertTrue("requests promotion", first.extras.getBoolean(Notification.EXTRA_REQUEST_PROMOTED_ONGOING))
                assertEquals(Notification.ProgressStyle::class.java.name, first.extras.getString(Notification.EXTRA_TEMPLATE))
                assertTrue("chip: ${chip(first)}", chip(first) in setOf("Plan", "Work"))
                // Live Updates themselves arrived with Android 16.1; Android 16.0 ignores the request and shows an ordinary notification.
                if (liveUpdates) {
                    assertTrue("promotable", first.hasPromotableCharacteristics())
                    assertTrue("Android lets this app promote", manager.canPostPromotedNotifications())
                    assertEquals("Android promoted it", Notification.FLAG_PROMOTED_ONGOING, first.flags and Notification.FLAG_PROMOTED_ONGOING)
                }
            }

            waitUntil(60000) { text(progress())?.startsWith("✓ Plan · ▶ Work") == true }
            // The shade, as a person would see the notification.
            shell("cmd statusbar expand-notifications")
            waitFor(2500)
            screenshot("task-progress-work.png")
            shell("cmd statusbar collapse")
            // The status bar, where Android 16.1 shows the chip.
            waitFor(1500)
            screenshot("task-progress-chip.png")
            if (Build.VERSION.SDK_INT >= 36) assertEquals("Work", chip(progress()))

            RemNotifications.setEnabled(app, true)
            RemNotifications.setShowText(app, true)
            waitUntil(40000) { title(progress()) == "Fixture secret task" }
            RemNotifications.setShowText(app, false)
            assertEquals("hiding Rem's words is immediate", "Rem is working on your task", title(progress()))
            RemNotifications.setEnabled(app, false)

            waitUntil(60000) { text(progress())?.contains("▶ Check") == true }
            if (Build.VERSION.SDK_INT >= 36) assertEquals("Check", chip(progress()))
            waitUntil(90000) { progress() == null }

            client.sendRemMessage("Start progress again", emptyList())
            waitUntil(60000) { progress() != null }
            assertEquals("Rem is working on your task", title(progress()))
            progress()!!.actions.single { it.title == "Hide" }.actionIntent.send()
            waitUntil(8000) { progress() == null }
            // At least two reads while the run is still going, so it would be back by now if it could come back.
            waitFor(35000)
            assertNull("reposted after dismissal", progress())

            // A new task shows again, without the dismissed one, and stopping background sync takes it away.
            client.sendRemMessage("Start progress a third time", emptyList())
            waitUntil(60000) { progress() != null }
            assertEquals("Rem is working on your task", title(progress()))
            app.stopService(sync)
            waitUntil(8000) { progress() == null && !client.backgroundEnabled }
        } finally {
            app.stopService(sync)
            TaskNotification.clear(app)
            RemNotifications.setEnabled(app, false)
            RemNotifications.setShowText(app, false)
        }
    }

    @Test fun olderPhonesGetAnOrdinaryOngoingNotification() {
        val frame = TaskProgress.frame(listOf(TaskProgress.Run("run-1", "Name", "executing", "2026-11-09T10:00:05+00:00", false)), showWords = false)!!
        val older = TaskNotification.build(app, frame, sdk = 33)
        assertTrue("ongoing", older.flags and Notification.FLAG_ONGOING_EVENT != 0)
        assertEquals("no bar, which would replace the steps when collapsed", 0, older.extras.getInt(Notification.EXTRA_PROGRESS_MAX))
        assertEquals("✓ Plan · ▶ Work · ○ Check · ○ Report", older.extras.getCharSequence(Notification.EXTRA_TEXT).toString())
        assertFalse("not a Live Update", older.extras.getBoolean(Notification.EXTRA_REQUEST_PROMOTED_ONGOING))
        assertNull(older.extras.getString(Notification.EXTRA_TEMPLATE))
        assertTrue(older.extras.getBoolean(Notification.EXTRA_SHOW_CHRONOMETER))
        assertEquals("Hide", older.actions.single().title)
        assertEquals("kept out of Android's automatic group", TaskNotification.CHANNEL, older.group)
    }
}
