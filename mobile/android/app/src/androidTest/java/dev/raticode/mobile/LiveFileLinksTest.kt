package dev.raticode.mobile

import android.app.Application
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

/**
 * Tapping file links against `live-device-fixture.py --workspace --remote --desktop --links`, which runs
 * the desktop's real device application and `files.link` checks. No provider or model runs: the desktop
 * finds the link in the message, checks the file and offers it, and the phone downloads it over pinned TLS.
 */
class LiveFileLinksTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()

    private fun waitFor(what: String, client: LiveClient, timeout: Long = 20000, condition: () -> Boolean) {
        try { compose.waitUntil(timeout, condition) }
        catch (failure: ComposeTimeoutException) {
            throw AssertionError("$what: link=${client.linkFetch}; files=${client.files}; request=${client.requestError}; status=${client.status}", failure)
        }
    }

    private fun navigate(target: String) {
        compose.waitUntil(5000) { compose.onAllNodesWithContentDescription(target).fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithContentDescription(target).performClick()
    }

    private fun screenshot(name: String) {
        compose.waitForIdle()
        InstrumentationRegistry.getInstrumentation().uiAutomation.executeShellCommand("screencap -p /data/local/tmp/raticode-$name").use {
            android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
        }
    }

    private fun pair(path: String, owners: ViewModelStore): LiveClient {
        compose.runOnUiThread { compose.activity.requestedOrientation = android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT }
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        DeviceStore(app).reset()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app)
            owners.put("links", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client, startPage = "Threads") } }
            client.preview(File(path).readText()); client.pair()
        }
        return client
    }

    /** A desktop without `file_links` never receives `files.link`; the phone explains instead, and the session stays up. */
    @Test fun olderDesktopsAreNotSentFileLinks() {
        val path = InstrumentationRegistry.getArguments().getString("olderLinksInvitationFile")
        assumeTrue("Requires a fixture from a desktop without files.link, with --workspace --remote --desktop --links", path != null)
        val owners = ViewModelStore()
        val client = pair(path!!, owners)
        try {
            waitFor("desktop control", client, 30000) { client.connected && client.desktopControl }
            navigate("Rem")
            waitFor("Rem reply with links", client) { (client.desktopViews["rem.home"] as? RemHome)?.messages?.any { it.id == "links" } == true }
            assertFalse(client.fileLinks)
            compose.onNodeWithText("the fixture report").performClick()
            compose.onNodeWithText(FILE_LINKS_OLD).assertIsDisplayed()
            compose.onNodeWithText("Close").performClick()
            compose.onNodeWithTag("remComposer").performTextInput("Still there?")
            compose.onNodeWithContentDescription("Send").performClick()
            waitFor("Rem reply", client) { (client.desktopViews["rem.home"] as? RemHome)?.messages?.any { it.body == "Fixture Rem reply" } == true }
            assertTrue(client.files.isEmpty())
        } finally {
            compose.runOnUiThread { owners.clear() }
        }
    }

    private fun foreground(): String? = InstrumentationRegistry.getInstrumentation().uiAutomation.rootInActiveWindow?.packageName?.toString()

    /** The default app for [type], asked through the shell because package visibility hides it from this app. */
    private fun defaultViewer(type: String): String? = InstrumentationRegistry.getInstrumentation().uiAutomation
        .executeShellCommand("cmd package resolve-activity --brief -a android.intent.action.VIEW -t $type -d content://x/y")
        .let { android.os.ParcelFileDescriptor.AutoCloseInputStream(it).bufferedReader().readLines() }.lastOrNull { "/" in it }?.substringBefore('/')

    /**
     * Waits for another app (a viewer, or Android's chooser when several could open it) to come to the
     * front, then goes back to Raticode. [expected] names the app when the device has a default.
     */
    private fun leftForViewer(expected: String? = null, screenshot: String? = null) {
        val own = InstrumentationRegistry.getInstrumentation().targetContext.packageName
        // waitUntil also runs the test's frame clock, which the phone's auto-open waits on.
        try { compose.waitUntil(20000) { foreground().let { it != null && it != own && (expected == null || it == expected) } } }
        catch (_: ComposeTimeoutException) {}
        val shown = foreground()
        assertNotEquals("A viewer should open without another prompt in Raticode", own, shown)
        assertNotNull(shown)
        if (expected != null) assertEquals(expected, shown)
        screenshot?.let { name ->
            Thread.sleep(1500)
            InstrumentationRegistry.getInstrumentation().uiAutomation.executeShellCommand("screencap -p /data/local/tmp/raticode-$name").use {
                android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
            }
        }
        repeat(5) {
            if (foreground() == own) return@repeat
            InstrumentationRegistry.getInstrumentation().uiAutomation.performGlobalAction(android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_BACK)
            Thread.sleep(800)
        }
        compose.waitUntil(10000) { foreground() == own }
    }

    @Test fun fileLinksDownloadWithoutRemAndRefuseOtherFolders() {
        val path = InstrumentationRegistry.getArguments().getString("linksInvitationFile")
        assumeTrue("Requires disposable fixture with --workspace --remote --desktop --links", path != null)
        val owners = ViewModelStore()
        val client = pair(path!!, owners)
        try {
            waitFor("desktop control", client, 30000) { client.connected && client.desktopControl && client.fileLinks }

            // Rem's chat: the tap asks the desktop for the file, which downloads and opens with no further prompt.
            navigate("Rem")
            waitFor("Rem reply with links", client) { (client.desktopViews["rem.home"] as? RemHome)?.messages?.any { it.id == "links" } == true }
            screenshot("link-rendering.png")
            compose.onNodeWithText("the fixture report").performClick()
            waitFor("downloaded report", client) { client.files.any { it.name == "Fixture report.md" && it.state == "available" } }
            // Markdown has no viewer here, so it opens as plain text (in Android's own app chooser when several apps can).
            leftForViewer(screenshot = "link-file-opened-report.png")
            assertNull(client.linkFetch)
            assertEquals("# Fixture report\n\nNo failed runs.\n".length.toLong(), client.files.single { it.name == "Fixture report.md" }.size)
            // Tapping it again opens the same download instead of asking for another copy.
            compose.onNodeWithText("the fixture report").performClick()
            leftForViewer()
            assertEquals(1, client.files.count { it.name == "Fixture report.md" })

            // A picture opens in the phone's default photo viewer.
            compose.onNodeWithText("the chart").performClick()
            waitFor("downloaded chart", client) { client.files.any { it.name == "fixture chart.png" && it.state == "available" } }
            leftForViewer(defaultViewer("image/png"), "link-file-opened-chart.png")

            // Full paths Rem wrote as plain text, with spaces, or as code: the desktop lists them and they open the same way.
            val paths = (client.desktopViews["rem.home"] as RemHome).messages.single { it.id == "paths" }
            assertEquals(2, paths.fileMentions.size)
            val plain = paths.fileMentions.single { it.endsWith("/paused screen.png") }
            val code = paths.fileMentions.single { it.endsWith("/review screen.png") }
            compose.onNodeWithTag("remMessages").performScrollToNode(hasText(code))
            screenshot("link-paths-rendering.png")
            compose.onNodeWithText(plain).performClick()
            waitFor("downloaded plain path", client) { client.files.any { it.name == "paused screen.png" && it.state == "available" } }
            leftForViewer(defaultViewer("image/png"), "link-path-opened.png")
            compose.onNodeWithTag("remMessages").performScrollToNode(hasText(code))
            compose.onNodeWithText(code).performClick()
            waitFor("downloaded code path", client) { client.files.any { it.name == "review screen.png" && it.state == "available" } }
            leftForViewer(defaultViewer("image/png"))

            // Nothing opens the raw data, so the phone says so and offers Save instead.
            compose.onNodeWithTag("remMessages").performScrollToNode(hasText("the raw data"))
            compose.onNodeWithText("the raw data").performClick()
            waitFor("no viewer", client) { compose.onAllNodesWithText("Can't open fixture data.tar").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithText("Save instead").assertIsDisplayed()
            screenshot("link-file-no-viewer.png")
            compose.onNodeWithText("Close").performClick()

            // A secret-looking file stays on the desktop even though Rem linked it.
            compose.onNodeWithTag("remMessages").performScrollToNode(hasText("a secret file"))
            compose.onNodeWithText("a secret file").performClick()
            waitFor("refused link", client) { client.linkFetch?.error != null }
            assertTrue(client.linkFetch!!.error!!, "passwords, keys or sign-ins" in client.linkFetch!!.error!!)
            assertTrue(client.files.none { it.name == "api-token.txt" })
            screenshot("link-file-refused.png")
            compose.onNodeWithText("Close").performClick()

            // A shared thread: the relative link resolves in the thread's project and arrives on that thread.
            navigate("Threads")
            compose.waitUntil(15000) { client.threads.isNotEmpty() }
            compose.onNodeWithText("Desktop project chat").performClick()
            waitFor("thread history", client) { client.messages.any { it.text.contains("the thread notes") } }
            compose.onNodeWithText("the thread notes").performClick()
            waitFor("downloaded thread notes", client) { client.files.any { it.name == "thread notes.md" && it.state == "available" } }
            assertEquals(client.threads.single().first, client.files.single { it.name == "thread notes.md" }.thread)
            leftForViewer(screenshot = "link-file-thread.png")
        } finally {
            compose.runOnUiThread { owners.clear() }
        }
    }
}
