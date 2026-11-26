package dev.raticode.mobile

import android.app.Application
import androidx.activity.compose.setContent
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performScrollTo
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.browser.BrowserApp
import dev.raticode.mobile.browser.BrowserRules
import dev.raticode.mobile.browser.BrowserRuntime
import dev.raticode.mobile.browser.Firefox
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.ui.BrowserAppPage
import dev.raticode.mobile.ui.RaticodeTheme
import org.junit.Rule
import org.junit.Test

/**
 * The Browser page with Firefox chosen, as one of BrowserApps: with Mozilla's Firefox installed (handed to the page, so
 * the test needs no Firefox on the emulator), it keeps the Firefox page's test tags and words: the address field, the
 * Sign in button, both ways pages open, and Firefox's screens as rows with no "where to tap" steps, since they open by
 * link. Nothing is tapped, so Firefox is never asked to open.
 */
class FirefoxBrowserPageTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    private val app get() = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application

    @Test fun firefoxsPageShowsItsSignInAndScreens() {
        val firefox = Firefox.APP
        val installed = BrowserApp.Found(firefox, firefox.editions[0], "159.0.1", enabled = true, genuine = true)
        compose.runOnUiThread {
            val browser = BrowserRuntime.get(app)
            browser.setEngine(Firefox.ENGINE)
            val client = LiveClient(app)
            compose.activity.setContent { RaticodeTheme { BrowserAppPage(client, browser, firefox, null) { installed } } }
        }
        try {
            compose.onNodeWithTag("firefoxPage").assertIsDisplayed()
            compose.onNodeWithText("Version 159.0.1", substring = true).assertIsDisplayed()
            compose.onNodeWithTag("firefoxAddress").assertIsDisplayed()
            compose.onNodeWithTag("firefoxSignIn").assertIsDisplayed()
            compose.onNodeWithTag("firefoxOpens-app").performScrollTo().assertIsDisplayed()
            compose.onNodeWithTag("firefoxOpens-tab").performScrollTo().assertIsDisplayed()
            compose.onNodeWithText("Sign in to Firefox").performScrollTo().assertIsDisplayed()
            for ((index, place) in firefox.places.withIndex()) {
                compose.onNodeWithTag("firefoxPlace-$index").performScrollTo().assertIsDisplayed()
                compose.onNodeWithText(place.label).assertIsDisplayed()
            }
            // Every screen opens by link, so the page doesn't say Firefox can't open them.
            check(compose.onAllNodesWithText("doesn't let other apps open these screens", substring = true).fetchSemanticsNodes().isEmpty())
        } finally {
            compose.runOnUiThread { BrowserRuntime.get(app).setEngine(BrowserRules.ENGINE_RATICODE) }
        }
    }
}
