package dev.raticode.mobile

import android.app.Application
import androidx.activity.compose.setContent
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.browser.BrowserRules
import dev.raticode.mobile.browser.BrowserRuntime
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.ui.BrowserPage
import dev.raticode.mobile.ui.RaticodeTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test

/**
 * A page that moves itself to a very long https address, as sign-in pages that carry tokens can, stays in Raticode's
 * browser. Before the fix the 2,048-character limit for the computer link refused it with "Raticode's browser can't
 * open this kind of address", the likeliest cause of a refusal seen after Google's sign-in on a phone (the refused
 * address wasn't measured). Needs the emulator's internet (example.com); skips without it.
 */
class BrowserLongAddressTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()

    @Test fun aPageCanMoveToALongAddress() {
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        lateinit var browser: BrowserRuntime
        compose.runOnUiThread {
            browser = BrowserRuntime.get(app)
            browser.setEngine(BrowserRules.ENGINE_RATICODE)
            browser.tabs.map { it.id }.forEach(browser::close)
            browser.notice = null
            val client = LiveClient(app)
            compose.activity.setContent { RaticodeTheme { BrowserPage(client, null) {} } }
            browser.go("https://example.com/")
        }
        val online = runCatching { compose.waitUntil(20000) { browser.page.title.contains("Example", ignoreCase = true) } }.isSuccess
        assumeTrue("Needs the emulator's internet connection", online)

        val long = "https://example.com/?ssdc=1&sidt=" + "A".repeat(2600) + "&continue=https%3A%2F%2Fmyaccount.google.com%2F"
        compose.runOnUiThread { browser.view(browser.active!!)!!.evaluateJavascript("location.href = '$long'", null) }
        // The page turns a refusal into a snackbar at once, so look for its words on screen.
        fun refused() = compose.onAllNodesWithText("Raticode's browser", substring = true).fetchSemanticsNodes()
            .flatMap { node -> node.config.getOrElseNullable(androidx.compose.ui.semantics.SemanticsProperties.Text) { null }.orEmpty() }
            .map { it.text }.firstOrNull()
        var notice: String? = null
        compose.waitUntil(20000) {
            notice = refused()
            notice != null || (browser.page.url == long && !browser.page.loading)
        }
        assertNull("The page was refused: $notice", notice)
        assertEquals(long, browser.page.url)
        // The saved tab keeps a short address without the token-bearing query.
        assertEquals("https://example.com/", browser.tabs.first { it.id == browser.active }.url)
    }
}
