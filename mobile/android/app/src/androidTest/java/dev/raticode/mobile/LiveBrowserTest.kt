package dev.raticode.mobile

import android.app.Application
import androidx.activity.compose.setContent
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.lifecycle.ViewModelStore
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.browser.BrowserRules
import dev.raticode.mobile.browser.BrowserRuntime
import dev.raticode.mobile.live.*
import dev.raticode.mobile.ui.LiveScreen
import dev.raticode.mobile.ui.RaticodeTheme
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import java.io.File

/**
 * The phone's browser against `live-device-fixture.py --workspace --remote --desktop --browser`, whose stand-in desktop
 * window publishes two web tabs (one of them Rem's) and a local preview through the desktop's real `browser_exchange`.
 * Checks: More opens the browser; the start page lists the computer's web tabs but not the preview; http is explained
 * instead of loaded; scripts are refused; a computer tab opens on the phone; Open on your computer reaches the desktop;
 * Ask Rem fills Rem's message box without sending. Pass `browserInvitationFile`. Afterwards the fixture's
 * `raticode-test-browser.json` lists the page the window opened. Pages that need the internet are checked only when the
 * emulator has it.
 */
class LiveBrowserTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    @get:Rule val keepControls = KeepBrowserControls()

    private fun screenshot(name: String) {
        compose.waitForIdle()
        InstrumentationRegistry.getInstrumentation().uiAutomation.executeShellCommand("screencap -p /data/local/tmp/raticode-$name").use {
            android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
        }
    }

    private fun waitFor(what: String, client: LiveClient, timeout: Long = 20000, condition: () -> Boolean) {
        try { compose.waitUntil(timeout, condition) }
        catch (failure: ComposeTimeoutException) {
            throw AssertionError("$what: errors=${client.desktopErrors}; busy=${client.desktopBusy}; request=${client.requestError}; status=${client.status}", failure)
        }
    }

    private fun has(text: String, substring: Boolean = false) = compose.onAllNodesWithText(text, substring = substring).fetchSemanticsNodes().isNotEmpty()

    private fun typeAddress(text: String) {
        compose.onNodeWithTag("browserAddress").performClick()
        compose.onNodeWithTag("browserAddressField").performTextReplacement(text)
        compose.onNodeWithTag("browserAddressField").performImeAction()
        compose.waitForIdle()
    }

    @Test fun browserWithComputerTabs() {
        val path = InstrumentationRegistry.getArguments().getString("browserInvitationFile")
        assumeTrue("Requires disposable fixture with --workspace --remote --desktop --browser", path != null)
        compose.runOnUiThread { compose.activity.requestedOrientation = android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT }
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        DeviceStore(app).reset()
        // Start from one blank tab, as a fresh install does.
        lateinit var browser: BrowserRuntime
        compose.runOnUiThread {
            browser = BrowserRuntime.get(app)
            browser.clear(null, history = true, siteData = true, cache = false)
            browser.tabs.map { it.id }.forEach(browser::close)
            browser.setLinksInApp(true)
            browser.notice = null
        }
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app)
            owners.put("browser", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client, startPage = "Rem") } }
            client.preview(File(path!!).readText()); client.pair()
        }
        try {
            waitFor("desktop browser", client, 30000) { client.connected && client.desktopControl && client.desktopBrowser == true }

            // More lists the browser; it opens on a blank tab with the computer's web tabs.
            compose.onNodeWithContentDescription("More").performClick()
            compose.onNodeWithText("Browser", substring = false).performScrollTo().performClick()
            compose.onNodeWithTag("browserStart").assertIsDisplayed()
            waitFor("computer tabs", client) { has("Fixture docs") }
            assertTrue(has("Rem's tab", substring = true))
            assertFalse("Previews stay on the computer", has("Local preview"))
            compose.onNodeWithContentDescription("Tabs, 1 open").assertIsDisplayed()
            screenshot("browser-start.png")

            // Plain http is explained, not loaded, and offers the secure address.
            typeAddress("http://example.com/")
            compose.onNodeWithText("This page isn't secure").assertIsDisplayed()
            compose.onNodeWithText("Try the secure address").assertIsDisplayed()
            screenshot("browser-insecure.png")
            compose.onNodeWithText("Go back").performClick()
            compose.onNodeWithTag("browserStart").assertIsDisplayed()

            // A script address is refused with a sentence.
            typeAddress("javascript:alert(1)")
            compose.waitUntil(5000) { has(BrowserRules.BLOCKED_SCRIPT) }

            // The tab switcher lists this phone's tabs and the computer's; a computer tab opens here in a new tab.
            compose.onNodeWithTag("browserTabs").performClick()
            compose.onNodeWithText("Tabs on this phone").assertIsDisplayed()
            compose.onNode(hasText("Fixture docs") and hasAnyAncestor(hasTestTag("browserTabsSheet"))).performScrollTo().performClick()
            compose.waitUntil(5000) { browser.tabs.size == 2 && browser.page.url == "https://example.com/" }
            compose.onNodeWithContentDescription("Tabs, 2 open").assertIsDisplayed()
            compose.onNodeWithTag("browserAddress").assertTextContains("example.com", substring = true)
            val online = runCatching { compose.waitUntil(20000) { browser.page.title.contains("Example", ignoreCase = true) } }.isSuccess
            if (online) screenshot("browser-page.png")

            // Open on your computer reaches the desktop window, which opens it in a background tab.
            compose.onNodeWithTag("browserMenu").performClick()
            compose.onNodeWithText("Open on your computer").performScrollTo().performClick()
            waitFor("sent to computer", client) { client.desktopViews[DesktopBrowser.OPEN] is DesktopBrowser.Sent }
            compose.waitUntil(5000) { has("Opens in a new tab", substring = true) }
            screenshot("browser-sent.png")

            // Ask Rem puts the page in Rem's message box and sends nothing.
            compose.onNodeWithTag("browserMenu").performClick()
            compose.onNodeWithText("Ask Rem about this page").performScrollTo().performClick()
            compose.waitUntil(10000) { compose.onAllNodesWithTag("remComposer").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithTag("remComposer").assertTextContains("https://example.com/", substring = true)
            assertTrue((client.desktopViews["rem.home"] as? RemHome)?.messages.orEmpty().none { "About this page" in it.body })
            screenshot("browser-ask-rem.png")

            // History kept the page only if it loaded; the tabs survive leaving the browser.
            assertEquals(2, browser.tabs.size)
            if (online) assertEquals("https://example.com/", browser.saved.history.first().url)
        } catch (failure: Throwable) {
            runCatching { screenshot("browser-failure.png") }
            throw failure
        } finally {
            compose.runOnUiThread { owners.clear() }
        }
    }
}
