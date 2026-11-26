package dev.raticode.mobile

import android.app.Application
import androidx.activity.compose.setContent
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performScrollTo
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.browser.BrowserApp
import dev.raticode.mobile.browser.BrowserRules
import dev.raticode.mobile.browser.BrowserRuntime
import dev.raticode.mobile.browser.MicrosoftEdge
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.ui.BrowserAppPage
import dev.raticode.mobile.ui.RaticodeTheme
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test

/**
 * The Browser page with Edge chosen, as one of BrowserApps: with Microsoft's Edge installed (handed to the page, since the
 * emulator has no Edge), it shows Edge's places as rows (Sign in to Edge, Edge settings, Extensions) and, on Android 12L
 * and later, an Edge beside Raticode button. Nothing is tapped, so Edge is never asked to open.
 */
class EdgeBrowserPageTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    private val app get() = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application

    @Test fun edgesPageShowsItsPlacesAndTheBesideButton() {
        assumeTrue("Android 12L starts split screen for the beside button", android.os.Build.VERSION.SDK_INT >= 32)
        val edge = MicrosoftEdge.APP
        val installed = BrowserApp.Found(edge, edge.editions[0], "142.0", enabled = true, genuine = true)
        compose.runOnUiThread {
            val browser = BrowserRuntime.get(app)
            browser.setEngine(MicrosoftEdge.ENGINE)
            val client = LiveClient(app)
            compose.activity.setContent { RaticodeTheme { BrowserAppPage(client, browser, edge, null) { installed } } }
        }
        try {
            compose.onNodeWithTag("edgePage").assertIsDisplayed()
            compose.onNodeWithText("Version 142.0", substring = true).assertIsDisplayed()
            compose.onNodeWithTag("edgeBeside").assertIsDisplayed()
            compose.onNodeWithText("Edge beside Raticode").assertIsDisplayed()
            for ((index, label) in listOf("Sign in to Edge", "Edge settings", "Extensions").withIndex()) {
                compose.onNodeWithTag("edgePlace-$index").performScrollTo().assertIsDisplayed()
                compose.onNodeWithText(label).assertIsDisplayed()
            }
        } finally {
            compose.runOnUiThread { BrowserRuntime.get(app).setEngine(BrowserRules.ENGINE_RATICODE) }
        }
    }
}
