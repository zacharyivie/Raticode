package dev.raticode.mobile

import android.app.Application
import android.content.ClipData
import android.content.ClipboardManager
import android.os.ParcelFileDescriptor
import android.view.accessibility.AccessibilityNodeInfo
import androidx.activity.compose.setContent
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.browser.BrowserRules
import dev.raticode.mobile.browser.BrowserRuntime
import dev.raticode.mobile.browser.FirefoxApps
import dev.raticode.mobile.browser.GeckoBrowser
import dev.raticode.mobile.browser.GeckoImport
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.ui.BrowserPage
import dev.raticode.mobile.ui.RaticodeTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test

/**
 * The import from Firefox on the same phone, against Mozilla's real Firefox for Android: finished with its first-run
 * screens and holding Dark Reader, added beforehand in Firefox itself. Types about:support in Firefox the way a person
 * would, taps Firefox's own Copy raw data, and comes back. Screenshots go to `/data/local/tmp/raticode-phone-firefox-*`.
 */
class GeckoPhoneFirefoxImportTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val app get() = instrumentation.targetContext.applicationContext as Application
    private val clipboard get() = app.getSystemService(ClipboardManager::class.java)
    private val dark = "addon@darkreader.org"

    private fun shell(command: String) = instrumentation.uiAutomation.executeShellCommand(command).use { ParcelFileDescriptor.AutoCloseInputStream(it).readBytes().decodeToString() }
    private fun screenshot(name: String) { shell("screencap -p /data/local/tmp/raticode-phone-firefox-$name.png") }
    private fun pause(ms: Long) { runCatching { compose.waitUntil(ms) { false } } }
    private fun foreground(): String? = instrumentation.uiAutomation.rootInActiveWindow?.packageName?.toString()
    private fun shown(tag: String) = compose.onAllNodesWithTag(tag).fetchSemanticsNodes().isNotEmpty()
    private fun clip(): String? = runCatching { clipboard.primaryClip?.getItemAt(0)?.text?.toString() }.getOrNull()

    private fun findText(text: String, root: AccessibilityNodeInfo? = instrumentation.uiAutomation.rootInActiveWindow): AccessibilityNodeInfo? {
        if (root == null) return null
        if ((root.text ?: root.contentDescription)?.toString()?.contains(text) == true) return root
        for (i in 0 until root.childCount) findText(text, root.getChild(i) ?: continue)?.let { return it }
        return null
    }

    private fun tap(node: AccessibilityNodeInfo) { val at = android.graphics.Rect().also(node::getBoundsInScreen); shell("input tap ${at.centerX()} ${at.centerY()}") }
    private fun backToRaticode() {
        shell("am start -f 0x20000 -n ${app.packageName}/dev.raticode.mobile.MainActivity")
        compose.waitUntil(15000) { foreground() == app.packageName }
    }

    private fun inFirefoxCopyRawData() {
        compose.waitUntil(25000) { foreground() == "org.mozilla.firefox" }
        pause(3000)
        val metrics = app.resources.displayMetrics
        shell("input tap ${(metrics.widthPixels * 0.39).toInt()} ${(metrics.heightPixels * 0.09).toInt()}") // The address bar at the top.
        pause(3000)
        screenshot("address-bar")
        GeckoImport.SUPPORT_PAGE.forEach { letter -> shell("input text $letter"); pause(400) }
        pause(1200)
        shell("input keyevent 66")
        compose.waitUntil(40000) { Thread.sleep(2000); findText("Copy raw data to clipboard") != null } // Spaced out: walking this big page over and over stalls an emulator.
        pause(1500)
        screenshot("about-support")
        tap(findText("Copy raw data to clipboard")!!)
        pause(2500)
        screenshot("copied")
    }

    @Test fun takesTheExtensionListFromFirefoxOnThisPhone() {
        val firefox = FirefoxApps.find(app)
        assumeTrue("Requires Firefox for Android on the emulator", firefox?.usable == true)
        lateinit var gecko: GeckoBrowser
        compose.runOnUiThread {
            BrowserRuntime.get(app).setEngine(BrowserRules.ENGINE_GECKO)
            gecko = GeckoBrowser.get(app)
            gecko.tabs.map { it.id }.forEach(gecko::close)
            gecko.importer.reset()
            val client = LiveClient(app)
            compose.activity.setContent { RaticodeTheme { BrowserPage(client, null) {} } }
        }
        compose.waitUntil(15000) { shown("geckoPage") }
        try {
            compose.onNodeWithTag("geckoMenu").performClick()
            compose.waitUntil(10000) { shown("geckoMenuSheet") }
            compose.onNodeWithText("Import from Firefox").performScrollTo().performClick()
            compose.waitUntil(10000) { shown("geckoImportFound") }
            compose.onNodeWithText("${firefox!!.build.name} ${firefox.version} is on this phone").assertExists()
            assertFalse("The computer steps are out of the way", shown("geckoImportFiles"))
            assertFalse("Nothing to import yet", shown("geckoImportRun"))
            screenshot("found")

            // Raticode copies the address, opens Firefox, and asks for nothing else until the user comes back.
            compose.onNodeWithTag("geckoImportPhone").performClick()
            compose.waitUntil(25000) { foreground() == "org.mozilla.firefox" }
            pause(2500)
            backToRaticode()
            compose.waitUntil(10000) { shown("geckoImportWaiting") }
            screenshot("waiting")
            assertEquals("Only the address is on the clipboard so far", GeckoImport.SUPPORT_PAGE, clip()?.trim())

            // Something else on the clipboard is left alone and shows nothing.
            val words = "my own words the user copied"
            compose.runOnUiThread { clipboard.setPrimaryClip(ClipData.newPlainText("mine", words)) }
            compose.onNodeWithTag("geckoImportCheck").performClick()
            compose.waitUntil(5000) { shown("geckoImportError") }
            assertEquals(words, clip())
            assertTrue(gecko.importer.preview.extensions.isEmpty())

            // Open Firefox from the sheet, take Firefox's list, and come back: it arrives without another tap.
            compose.onNodeWithTag("geckoImportReopen").performClick()
            inFirefoxCopyRawData()
            backToRaticode()
            compose.waitUntil(60000) { gecko.importer.busy == 0 && dark in gecko.importer.listings }
            assertEquals("Only what the user added in Firefox: ${gecko.importer.preview.extensions}", listOf(dark), gecko.importer.preview.extensions.map { it.id })
            assertTrue("Firefox's own parts are left out", gecko.importer.preview.builtIn >= 1)
            assertFalse("Waiting is over", shown("geckoImportWaiting"))
            assertTrue("The clipboard doesn't keep Firefox's data: ${clip()?.take(40)}", clip().isNullOrEmpty())
            assertTrue("Nothing is installed by reading", gecko.extensions.none { it.id == dark })
            screenshot("review")
            assertTrue("Bookmarks are named as not available", compose.onAllNodesWithText("Bookmarks from Firefox on this phone").fetchSemanticsNodes().isNotEmpty() ||
                runCatching { compose.onNodeWithTag("geckoImportList").performScrollToNode(hasText("Bookmarks from Firefox on this phone")); true }.getOrDefault(false))
            println("Firefox gave settings: ${gecko.importer.preview.settings}; notes: ${gecko.importer.preview.notes.map { it.what }}")

            compose.onNodeWithTag("geckoImportList").performScrollToNode(hasTestTag("geckoImportRun"))
            compose.onNodeWithTag("geckoImportRun").performClick()
            compose.waitUntil(60000) { shown("geckoInstallAllow") }
            assertTrue("It still needs the user's approval", gecko.extensions.none { it.id == dark })
            compose.onNodeWithTag("geckoInstallAllow").performClick()
            compose.waitUntil(30000) { gecko.extensions.any { it.id == dark && it.enabled } }
        } finally {
            compose.runOnUiThread { gecko.extensions.forEach(gecko::uninstall); gecko.importer.reset(); BrowserRuntime.get(app).setEngine(BrowserRules.ENGINE_RATICODE) }
            pause(1200)
        }
    }
}
