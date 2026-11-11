package dev.raticode.mobile

import android.Manifest
import android.app.Application
import android.app.Notification
import android.app.NotificationManager
import android.os.Build
import androidx.activity.compose.setContent
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.lifecycle.ViewModelStore
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.*
import dev.raticode.mobile.ui.LiveScreen
import dev.raticode.mobile.ui.RaticodeTheme
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import java.io.File
import java.time.LocalTime

/**
 * Rem's alerts about work this phone didn't start, against `live-device-fixture.py --workspace --remote --desktop --alert-filter`
 * (a scripted stand-in for the desktop's filter) or `--alert-filter-old` (an older desktop).
 */
class LiveRemAlertsTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val app get() = instrumentation.targetContext.applicationContext as Application
    private val manager get() = app.getSystemService(NotificationManager::class.java)

    private fun alert() = manager.activeNotifications.firstOrNull { it.id == 2004 && it.tag == "rem" }?.notification

    private fun lines(notification: Notification?): List<String> = @Suppress("DEPRECATION") notification?.extras?.getParcelableArray(Notification.EXTRA_MESSAGES)
        ?.let { Notification.MessagingStyle.Message.getMessagesFromBundleArray(it) }?.map { it.text.toString() }.orEmpty()

    private fun screenshot(name: String) {
        compose.waitForIdle()
        instrumentation.uiAutomation.executeShellCommand("screencap -p /data/local/tmp/raticode-$name").use {
            android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
        }
    }

    private fun connect(path: String): LiveClient {
        compose.runOnUiThread { compose.activity.requestedOrientation = android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT }
        DeviceStore(app).reset()
        if (Build.VERSION.SDK_INT >= 33) instrumentation.uiAutomation.grantRuntimePermission(app.packageName, Manifest.permission.POST_NOTIFICATIONS)
        RemNotifications.setEnabled(app, false)
        RemNotifications.setShowText(app, false)
        RemAlertRules.FILTERS.forEach { RemNotifications.setFilter(app, it, false) }
        RemNotifications.setQuietHours(app, RemAlertRules.QuietHours())
        RemNotifications.setEnabled(app, true)
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app)
            owners.put("alerts", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client, startPage = "Desktop") } }
            client.preview(File(path).readText()); client.pair()
        }
        compose.waitUntil(30000) { client.connected && client.desktopControl }
        return client
    }

    /** Scrolls the switch into view and turns it on, as a person would. */
    private fun turnOn(tag: String) {
        compose.onNodeWithTag(tag).performScrollTo().assertIsOff().performClick()
        compose.onNodeWithTag(tag).assertIsOn()
    }

    @Test fun alertsFromAFilteringDesktopFollowTheSwitches() {
        val path = InstrumentationRegistry.getArguments().getString("remAlertsInvitationFile")
        assumeTrue("Requires a disposable fixture with --alert-filter", path != null)
        val client = connect(path!!)
        try {
            compose.waitUntil(20000) { client.remAlertFilter == true }
            // Let the first plain read set the position, so the next ones have something to be older than.
            val settled = android.os.SystemClock.elapsedRealtime() + 4000
            compose.waitUntil(8000) { android.os.SystemClock.elapsedRealtime() > settled }
            compose.onNodeWithText("Also alert me about").performScrollTo().assertIsDisplayed()
            turnOn("remAlertNeedsYou")
            turnOn("remAlertFinished")
            screenshot("rem-alert-switches.png")
            turnOn("remAlertProposals")
            compose.onNodeWithTag("remQuietHours").performScrollTo().assertIsOff()

            compose.waitUntil(45000) { lines(alert()).contains("A task finished") }
            val first = alert()!!
            val shown = lines(first)
            assertEquals("needs you: $shown", 1, shown.count { it == "A task needs you" })
            assertEquals("finished: $shown", 1, shown.count { it == "A task finished" })
            // The older round found as the switch went on stays quiet, even when named again.
            assertEquals("proposals: $shown", 1, shown.count { it == "Proposals are waiting" })
            assertTrue("words leaked: $shown", shown.none { "Fixture" in it })
            assertEquals("New message from Rem", first.publicVersion.extras.getString(Notification.EXTRA_TEXT))
            assertNotEquals("not quiet yet", "silent", first.group)

            // The next read names round 1 again, and the one after posts inside quiet hours.
            val minute = LocalTime.now().let { it.hour * 60 + it.minute }
            RemNotifications.setQuietHours(app, RemAlertRules.QuietHours(true, (minute + 1430) % 1440, (minute + 10) % 1440))
            compose.waitUntil(45000) { lines(alert()).count { it == "A task finished" } == 2 }
            val quiet = alert()!!
            val during = lines(quiet)
            assertEquals("round named again: $during", 1, during.count { it == "Proposals are waiting" })
            val record = instrumentation.uiAutomation.executeShellCommand("dumpsys notification --noredact").use {
                android.os.ParcelFileDescriptor.AutoCloseInputStream(it).bufferedReader().readText()
            }
            assertEquals("rem-quiet", quiet.group)
            assertTrue("Android's record has no quiet group: ${record.lines().filter { "groupKey" in it && "2004" in it }}", record.lines().any { "groupKey" in it && "2004" in it && "rem-quiet" in it })
            assertEquals(Notification.GROUP_ALERT_SUMMARY, quiet.groupAlertBehavior)

            RemNotifications.setQuietHours(app, RemAlertRules.QuietHours())
            RemNotifications.setShowText(app, true)
            compose.waitUntil(45000) { lines(alert()).contains("Fixture words shown") }
            assertTrue(lines(alert()).none { "Fixture secret" in it })
            assertEquals("New message from Rem", alert()!!.publicVersion.extras.getString(Notification.EXTRA_TEXT))
        } finally {
            RemNotifications.setEnabled(app, false)
            RemNotifications.setShowText(app, false)
            RemAlertRules.FILTERS.forEach { RemNotifications.setFilter(app, it, false) }
            RemNotifications.setQuietHours(app, RemAlertRules.QuietHours())
        }
    }

    @Test fun anOlderDesktopIsNeverSentTheFilter() {
        val path = InstrumentationRegistry.getArguments().getString("remAlertsOldInvitationFile")
        assumeTrue("Requires a disposable fixture with --alert-filter-old", path != null)
        val client = connect(path!!)
        try {
            compose.waitUntil(20000) { client.remAlertFilter == false }
            compose.onNodeWithText("Update Raticode on your computer to get these alerts.").performScrollTo().assertIsDisplayed()
            turnOn("remAlertNeedsYou")
            turnOn("remAlertFinished")
            turnOn("remAlertProposals")
            screenshot("rem-alert-older-desktop.png")
            // Long enough for two more reads; the fixture only answers with an alert if a read carries the filter.
            val until = android.os.SystemClock.elapsedRealtime() + 25000
            compose.waitUntil(30000) { android.os.SystemClock.elapsedRealtime() > until }
            assertNull("an older desktop was sent the filter", alert())
            assertTrue(client.connected)
        } finally {
            RemNotifications.setEnabled(app, false)
            RemAlertRules.FILTERS.forEach { RemNotifications.setFilter(app, it, false) }
        }
    }
}
