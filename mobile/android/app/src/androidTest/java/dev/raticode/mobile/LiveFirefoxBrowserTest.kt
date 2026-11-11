package dev.raticode.mobile

import android.accessibilityservice.AccessibilityService
import android.app.Application
import androidx.activity.compose.setContent
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.lifecycle.ViewModelStore
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.browser.BrowserRules
import dev.raticode.mobile.browser.BrowserRuntime
import dev.raticode.mobile.browser.FirefoxApps
import dev.raticode.mobile.live.*
import dev.raticode.mobile.ui.LiveScreen
import dev.raticode.mobile.ui.RaticodeTheme
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import java.io.File

/**
 * Firefox as the phone's browser, against `live-device-fixture.py --workspace --remote --desktop --browser` and a real
 * Firefox for Android installed on the emulator (Mozilla's own package, which Raticode checks is Mozilla-signed). Pass
 * `firefoxInvitationFile`. Checks: the choice in Browser settings; the Firefox page with its version; the computer's tabs
 * labelled as its Firefox; an address, Sign in and Add-ons each open in Firefox itself; a Firefox turned off in Android
 * says so and offers its app settings; Use Raticode switches back; and More › Desktop settings switches the computer to
 * Firefox (the fixture's `raticode-test-desktop.json` then holds `browser.engine: firefox`). Screenshots go to
 * `/data/local/tmp/raticode-firefox-*.png`.
 */
class LiveFirefoxBrowserTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    @get:Rule val keepControls = KeepBrowserControls()
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()

    private fun shell(command: String) = instrumentation.uiAutomation.executeShellCommand(command).use {
        android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes().decodeToString()
    }
    private fun screenshot(name: String) { shell("screencap -p /data/local/tmp/raticode-firefox-$name") }
    private fun foreground(): String? = instrumentation.uiAutomation.rootInActiveWindow?.packageName?.toString()
    private fun has(text: String, substring: Boolean = false) = compose.onAllNodesWithText(text, substring = substring).fetchSemanticsNodes().isNotEmpty()

    private fun waitFor(what: String, client: LiveClient, timeout: Long = 20000, condition: () -> Boolean) {
        try { compose.waitUntil(timeout, condition) }
        catch (failure: ComposeTimeoutException) {
            throw AssertionError("$what: errors=${client.desktopErrors}; request=${client.requestError}; status=${client.status}; front=${foreground()}", failure)
        }
    }

    private fun pause(ms: Long) { runCatching { compose.waitUntil(ms) { false } } }

    /**
     * The first node in the active window whose text or description matches. Walks the tree, because Compose apps such
     * as Firefox don't answer Android's find-by-text query.
     */
    private fun findText(text: String, exact: Boolean = false, root: android.view.accessibility.AccessibilityNodeInfo? =
        instrumentation.uiAutomation.rootInActiveWindow): android.view.accessibility.AccessibilityNodeInfo? {
        if (root == null) return null
        val label = (root.text ?: root.contentDescription)?.toString()
        if (label != null && (if (exact) label == text else text in label)) return root
        for (i in 0 until root.childCount) findText(text, exact, root.getChild(i) ?: continue)?.let { return it }
        return null
    }

    /**
     * Taps through a fresh Firefox's first-run screens, which a user's own Firefox has long finished: Welcome › Continue,
     * Android's "Set Firefox as your default browser?" › Cancel (the test changes no default), then Not now. Firefox's
     * Compose screens fill their accessibility tree a moment after appearing, so each step polls for its button, and the
     * welcome is over once no first-run button shows for a few seconds.
     */
    private fun finishFirefoxWelcome() {
        var idle = 0
        while (idle < 6) {
            val root = instrumentation.uiAutomation.rootInActiveWindow
            val pkg = root?.packageName?.toString().orEmpty()
            val choices = when {
                pkg == "org.mozilla.firefox" -> listOf("Not now", "Skip", "Maybe later", "Continue", "Start browsing", "Got it")
                pkg.endsWith("permissioncontroller") -> listOf("Cancel")
                else -> emptyList()
            }
            val button = choices.firstNotNullOfOrNull { text -> findText(text, exact = true, root = root) }
            if (button == null) { idle++; pause(500); continue }
            idle = 0
            // Firefox's buttons are Compose views whose text node takes no click action; tap where it is drawn.
            val bounds = android.graphics.Rect().also(button::getBoundsInScreen)
            shell("input tap ${bounds.centerX()} ${bounds.centerY()}")
            pause(2000)
        }
    }

    /** Firefox takes the screen and shows [expect] (polled, since its tree fills late), then Back returns to Raticode. */
    private fun inFirefoxThenBack(client: LiveClient, name: String, expect: String? = null, welcome: Boolean = false) {
        waitFor("Firefox in front", client, 20000) { foreground() == "org.mozilla.firefox" }
        pause(1500)
        if (welcome) finishFirefoxWelcome()
        if (expect != null) waitFor("Firefox shows \"$expect\"", client, 15000) { findText(expect) != null }
        screenshot(name)
        repeat(6) { if (foreground() != app.packageName) { instrumentation.uiAutomation.performGlobalAction(AccessibilityService.GLOBAL_ACTION_BACK); pause(1200) } }
        waitFor("Raticode in front", client, 10000) { foreground() == app.packageName }
    }

    private val app get() = instrumentation.targetContext.applicationContext as Application

    @Test fun firefoxAsThePhonesBrowser() {
        val path = InstrumentationRegistry.getArguments().getString("firefoxInvitationFile")
        assumeTrue("Requires disposable fixture with --workspace --remote --desktop --browser", path != null)
        assumeTrue("Requires Firefox for Android on the emulator", FirefoxApps.find(app)?.usable == true)
        compose.runOnUiThread { compose.activity.requestedOrientation = android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT }
        DeviceStore(app).reset()
        lateinit var browser: BrowserRuntime
        compose.runOnUiThread { browser = BrowserRuntime.get(app); browser.setEngine(BrowserRules.ENGINE_RATICODE); browser.setFirefoxMode(FirefoxApps.MODE_TAB); browser.notice = null }
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app)
            owners.put("firefox", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client, startPage = "Rem") } }
            client.preview(File(path!!).readText()); client.pair()
        }
        try {
            waitFor("desktop browser", client, 30000) { client.connected && client.desktopControl && client.desktopBrowser == true }
            compose.onNodeWithContentDescription("More").performClick()
            compose.onNodeWithText("Browser", substring = false).performScrollTo().performClick()

            // Browser settings: choose Firefox. The page becomes Firefox's.
            compose.onNodeWithTag("browserMenu").performClick()
            compose.onNodeWithText("Browser settings and privacy").performScrollTo().performClick()
            compose.onNodeWithTag("engine-firefox").performScrollTo().performClick()
            instrumentation.uiAutomation.performGlobalAction(AccessibilityService.GLOBAL_ACTION_BACK)
            compose.waitUntil(8000) { compose.onAllNodesWithTag("firefoxPage").fetchSemanticsNodes().isNotEmpty() }
            assertEquals(BrowserRules.ENGINE_FIREFOX, browser.saved.engine)
            compose.onNodeWithText("Version", substring = true).assertIsDisplayed()
            waitFor("computer tabs", client) { has("Fixture docs") }
            screenshot("page.png")

            // Open Firefox once and finish its first-run screens, as a user's own Firefox already has.
            compose.onNodeWithTag("firefoxReturn").performScrollTo().performClick()
            inFirefoxThenBack(client, "welcome.png", welcome = true)
            // An address opens in Firefox, over Raticode.
            compose.onNodeWithTag("firefoxAddress").performTextReplacement("example.com")
            compose.onNodeWithTag("firefoxAddress").performImeAction()
            inFirefoxThenBack(client, "page-in-firefox.png", expect = "example.com")
            // Firefox's own sign-in and add-ons screens.
            compose.onNodeWithTag("firefoxSignIn").performScrollTo().performClick()
            inFirefoxThenBack(client, "sign-in.png", expect = "Sign in")
            compose.onNodeWithText("Add-ons").performScrollTo().performClick()
            inFirefoxThenBack(client, "add-ons.png", expect = "Extensions")

            // Turned off in Android: the page says so and sends nothing.
            shell("pm disable-user --user 0 org.mozilla.firefox")
            try {
                // Leaving and coming back is when the page looks for Firefox again.
                shell("am start -a android.settings.SETTINGS")
                pause(2000)
                instrumentation.uiAutomation.performGlobalAction(AccessibilityService.GLOBAL_ACTION_BACK)
                waitFor("turned off", client) { has(FirefoxApps.TURNED_OFF) }
                assertTrue(has("Open Firefox's app settings"))
                screenshot("turned-off.png")
            } finally { shell("pm enable --user 0 org.mozilla.firefox") }

            // Back to Raticode's own browser.
            compose.onNodeWithTag("useRaticodeBrowser").performClick()
            compose.waitUntil(5000) { compose.onAllNodesWithTag("browserAddress").fetchSemanticsNodes().isNotEmpty() }
            assertEquals(BrowserRules.ENGINE_RATICODE, browser.saved.engine)

            // The computer's own browser, from More › Desktop settings.
            compose.onNodeWithTag("moreBack").performClick()
            compose.onNodeWithText("Desktop settings", substring = false).performScrollTo().performClick()
            waitFor("desktop settings", client) { compose.onAllNodesWithContentDescription("Default browser for new tabs").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithContentDescription("Default browser for new tabs").performScrollTo()
            screenshot("desktop-setting-before.png")
            compose.onNodeWithContentDescription("Default browser for new tabs").performClick()
            compose.onNodeWithText("Firefox").performClick()
            waitFor("computer switched", client, 20000) { has("Firefox") && compose.onAllNodesWithContentDescription("Default browser for new tabs").fetchSemanticsNodes().isNotEmpty() &&
                (client.desktopViews["settings.get"] as? DesktopSettings)?.values?.get("browser.engine") == "firefox" }
            screenshot("desktop-setting.png")
        } catch (failure: Throwable) {
            runCatching { screenshot("failure.png") }
            throw failure
        } finally {
            compose.runOnUiThread { owners.clear(); BrowserRuntime.get(app).setEngine(BrowserRules.ENGINE_RATICODE) }
        }
    }
}
