package dev.raticode.mobile

import android.accessibilityservice.AccessibilityService
import android.app.Application
import android.view.accessibility.AccessibilityNodeInfo
import androidx.activity.compose.setContent
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.browser.BrowserApps
import dev.raticode.mobile.browser.BrowserRules
import dev.raticode.mobile.browser.BrowserRuntime
import dev.raticode.mobile.browser.ChromeApps
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.ui.BrowserPage
import dev.raticode.mobile.ui.RaticodeTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test

/**
 * Chrome as the phone's browser, with Google's own Chrome for Android on the emulator (the Google APIs system image
 * ships it; Raticode checks Google signed it). No paired computer is needed. Checks: the choice in Browser settings; the
 * Chrome page with its version; an address opens in the Chrome app, in Chrome's own task; "Over Raticode" opens it as a
 * Chrome Custom Tab in Raticode's task; Sign in opens Google's sign-in page in Chrome; a Chrome turned off in Android
 * says so and offers its app settings; and Use Raticode switches back. Pages need the emulator's internet; without it
 * the test still checks Chrome took them. Screenshots go to `/data/local/tmp/raticode-chrome-*.png`.
 */
class ChromeBrowserTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    @get:Rule val keepControls = KeepBrowserControls()
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val app get() = instrumentation.targetContext.applicationContext as Application

    private fun shell(command: String) = instrumentation.uiAutomation.executeShellCommand(command).use {
        android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes().decodeToString()
    }
    private fun screenshot(name: String) { shell("screencap -p /data/local/tmp/raticode-chrome-$name") }
    private fun foreground(): String? = instrumentation.uiAutomation.rootInActiveWindow?.packageName?.toString()
    private fun pause(ms: Long) { runCatching { compose.waitUntil(ms) { false } } }
    private fun has(text: String) = compose.onAllNodesWithText(text, substring = true).fetchSemanticsNodes().isNotEmpty()

    /** The resumed activity and its task, from `dumpsys activity`: "com.android.chrome/...CustomTabActivity t12". */
    private fun resumed(): String = Regex("topResumedActivity=ActivityRecord\\{\\S+ u0 (\\S+) (t\\d+)").find(shell("dumpsys activity activities"))
        ?.let { "${it.groupValues[1]} ${it.groupValues[2]}" }.orEmpty()
    private fun raticodeTask(): String = Regex("u0 ${app.packageName}/\\.MainActivity (t\\d+)").find(shell("dumpsys activity activities"))?.groupValues?.get(1).orEmpty()

    private fun findText(text: String, root: AccessibilityNodeInfo? = instrumentation.uiAutomation.rootInActiveWindow): AccessibilityNodeInfo? {
        if (root == null) return null
        val label = (root.text ?: root.contentDescription)?.toString()
        if (label != null && label.equals(text, ignoreCase = true)) return root
        for (i in 0 until root.childCount) findText(text, root.getChild(i) ?: continue)?.let { return it }
        return null
    }

    /** Any text in Chrome's window containing [part], such as the address in its toolbar. */
    private fun chromeShows(part: String, root: AccessibilityNodeInfo? = instrumentation.uiAutomation.rootInActiveWindow): Boolean {
        if (root == null) return false
        if ((root.text ?: root.contentDescription)?.toString()?.contains(part) == true) return true
        return (0 until root.childCount).any { i -> root.getChild(i)?.let { chromeShows(part, it) } == true }
    }

    /**
     * Taps through a fresh Chrome's first-run screens, which a user's own Chrome has long finished: continuing without
     * an account, declining sync and notifications, and cancelling Android's default-browser question.
     */
    private fun finishChromeWelcome() {
        var idle = 0
        while (idle < 6) {
            val root = instrumentation.uiAutomation.rootInActiveWindow
            val pkg = root?.packageName?.toString().orEmpty()
            val choices = when {
                pkg == "com.android.chrome" -> listOf("Use without an account", "Accept & continue", "Continue", "No thanks", "No, thanks", "Not now", "Got it", "Skip")
                pkg.endsWith("permissioncontroller") || pkg == "android" -> listOf("Cancel", "Don't allow")
                else -> emptyList()
            }
            val button = choices.firstNotNullOfOrNull { findText(it, root) }
            if (button == null) { idle++; pause(500); continue }
            idle = 0
            val bounds = android.graphics.Rect().also(button::getBoundsInScreen)
            shell("input tap ${bounds.centerX()} ${bounds.centerY()}")
            pause(1500)
        }
    }

    private fun backToRaticode() {
        repeat(6) { if (foreground() != app.packageName) { instrumentation.uiAutomation.performGlobalAction(AccessibilityService.GLOBAL_ACTION_BACK); pause(1200) } }
        if (foreground() != app.packageName) shell("am start -n ${app.packageName}/.MainActivity")
        compose.waitUntil(10000) { foreground() == app.packageName }
    }

    private fun waitForChrome(what: String) {
        try { compose.waitUntil(20000) { foreground() == "com.android.chrome" } }
        catch (failure: ComposeTimeoutException) { throw AssertionError("$what: Chrome didn't come to the front; resumed=${resumed()}", failure) }
    }

    @Test fun chromeAsThePhonesBrowser() {
        val chrome = ChromeApps.APP.find(app)
        assumeTrue("Requires Google's Chrome for Android on the emulator", chrome?.usable == true)
        assertEquals("com.android.chrome", chrome!!.edition.pkg)
        compose.runOnUiThread { compose.activity.requestedOrientation = android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT }
        lateinit var browser: BrowserRuntime
        compose.runOnUiThread {
            browser = BrowserRuntime.get(app)
            browser.setEngine(BrowserRules.ENGINE_RATICODE); browser.setAppMode(ChromeApps.ENGINE, BrowserApps.MODE_APP); browser.notice = null
            val client = LiveClient(app)
            compose.activity.setContent { RaticodeTheme { BrowserPage(client, null) {} } }
        }
        try {
            // Browser settings: choose Chrome. The page becomes Chrome's.
            compose.onNodeWithTag("browserMenu").performClick()
            compose.onNodeWithText("Browser settings and privacy").performScrollTo().performClick()
            compose.onNodeWithTag("engine-chrome").performScrollTo()
            assertTrue(has("Chrome ${chrome.version}"))
            compose.onNodeWithTag("engine-chrome").performClick()
            instrumentation.uiAutomation.performGlobalAction(AccessibilityService.GLOBAL_ACTION_BACK)
            compose.waitUntil(8000) { compose.onAllNodesWithTag("chromePage").fetchSemanticsNodes().isNotEmpty() }
            assertEquals(ChromeApps.ENGINE, browser.saved.engine)
            compose.onNodeWithText("Version ${chrome.version}", substring = true).assertIsDisplayed()
            compose.onNodeWithTag("chromeAbout").performScrollTo()
            assertTrue(has("no extensions"))
            screenshot("page.png")
            // Inside Raticode: the page says Chrome can't draw inside Raticode, calls the Custom Tab a Quick look, and its
            // button switches to Raticode's own browser, which keeps Raticode's bar.
            assertTrue(has("Quick look")); assertTrue(has("Browsing inside Raticode"))
            compose.onNodeWithTag("browseInRaticode").performScrollTo().performClick()
            compose.waitUntil(5000) { compose.onAllNodesWithTag("browserAddress").fetchSemanticsNodes().isNotEmpty() }
            assertEquals(BrowserRules.ENGINE_RATICODE, browser.saved.engine)
            compose.runOnUiThread { browser.setEngine(ChromeApps.ENGINE) }
            compose.waitUntil(8000) { compose.onAllNodesWithTag("chromePage").fetchSemanticsNodes().isNotEmpty() }

            // Open Chrome once and finish its first-run screens, as a user's own Chrome already has.
            compose.onNodeWithTag("chromeOpen").performScrollTo().performClick()
            waitForChrome("Open Chrome")
            pause(1500); finishChromeWelcome()
            screenshot("welcome.png")
            backToRaticode()

            // An address opens in the Chrome app, in Chrome's own task.
            val ours = raticodeTask()
            compose.onNodeWithTag("chromeAddress").performScrollTo().performTextReplacement("example.com")
            compose.onNodeWithTag("chromeAddress").performImeAction()
            waitForChrome("address in the app")
            pause(1500); finishChromeWelcome()
            val inApp = resumed()
            assertTrue(inApp, inApp.startsWith("com.android.chrome/") && !inApp.endsWith(" $ours") && "CustomTab" !in inApp)
            val online = runCatching { compose.waitUntil(15000) { chromeShows("example.com") } }.isSuccess
            screenshot("page-in-chrome.png")
            backToRaticode()

            // Over Raticode: a Chrome Custom Tab, in Raticode's own task.
            compose.onNodeWithTag("chromeOpens-tab").performScrollTo().performClick()
            assertEquals(BrowserApps.MODE_TAB, BrowserRules.appMode(browser.saved, ChromeApps.ENGINE))
            compose.onNodeWithTag("chromeAddress").performScrollTo().performTextReplacement("https://example.org/")
            compose.onNodeWithTag("chromeAddress").performImeAction()
            waitForChrome("address over Raticode")
            pause(1500)
            val tab = resumed()
            assertTrue(tab, tab.startsWith("com.android.chrome/") && "CustomTab" in tab && tab.endsWith(" $ours"))
            if (online) compose.waitUntil(15000) { chromeShows("example.org") }
            screenshot("custom-tab.png")
            backToRaticode()
            compose.onNodeWithTag("chromeOpens-app").performScrollTo().performClick()

            // Sign in opens Google's sign-in page in Chrome; Chrome and Google handle the account from there.
            compose.onNodeWithTag("chromeSignIn").performScrollTo().performClick()
            waitForChrome("Sign in")
            if (online) compose.waitUntil(20000) { chromeShows("accounts.google.com") || chromeShows("Sign in") }
            pause(3000)
            screenshot("sign-in.png")
            backToRaticode()

            // Turned off in Android: the page says so and sends nothing.
            shell("pm disable-user --user 0 com.android.chrome")
            try {
                // Leaving and coming back is when the page looks for Chrome again.
                shell("am start -a android.settings.SETTINGS")
                pause(2000)
                backToRaticode()
                compose.waitUntil(10000) { has(ChromeApps.APP.turnedOff) }
                assertTrue(has("Open Chrome's app settings"))
                screenshot("turned-off.png")
            } finally { shell("pm enable --user 0 com.android.chrome") }

            // Back to Raticode's own browser.
            compose.waitUntil(10000) { compose.onAllNodesWithTag("useRaticodeBrowser").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithTag("useRaticodeBrowser").performClick()
            compose.waitUntil(5000) { compose.onAllNodesWithTag("browserAddress").fetchSemanticsNodes().isNotEmpty() }
            assertEquals(BrowserRules.ENGINE_RATICODE, browser.saved.engine)
        } catch (failure: Throwable) {
            runCatching { screenshot("failure.png") }
            throw failure
        } finally {
            shell("pm enable --user 0 com.android.chrome")
            compose.runOnUiThread { BrowserRuntime.get(app).setEngine(BrowserRules.ENGINE_RATICODE) }
        }
    }
}
