package dev.raticode.mobile

import android.app.Application
import android.net.Uri
import android.os.SystemClock
import androidx.activity.compose.LocalActivityResultRegistryOwner
import androidx.activity.compose.setContent
import androidx.activity.result.ActivityResultRegistry
import androidx.activity.result.ActivityResultRegistryOwner
import androidx.activity.result.contract.ActivityResultContract
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.core.app.ActivityOptionsCompat
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.browser.BrowserRules
import dev.raticode.mobile.browser.BrowserRuntime
import dev.raticode.mobile.browser.GeckoBrowser
import dev.raticode.mobile.browser.GeckoImport
import dev.raticode.mobile.browser.GeckoRules
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.ui.BrowserPage
import dev.raticode.mobile.ui.RaticodeTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import java.io.File

/**
 * Importing from Firefox, with files a real Firefox wrote into a throwaway profile (never the user's) and real answers
 * from addons.mozilla.org. Pass `firefoxExportDir`, a folder on the emulator holding extensions.json, prefs.js,
 * bookmarks.html, bookmarks.json, about-support.json and a stand-in logins.json. A stand-in for Android's file picker
 * hands those files to the real import path.
 */
class GeckoFirefoxImportTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    @get:Rule val keepControls = KeepBrowserControls()
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val app get() = instrumentation.targetContext.applicationContext as Application
    private val dir get() = InstrumentationRegistry.getArguments().getString("firefoxExportDir")
    private var picked: List<Uri> = emptyList()

    private val picker = object : ActivityResultRegistryOwner {
        override val activityResultRegistry = object : ActivityResultRegistry() {
            override fun <I, O> onLaunch(requestCode: Int, contract: ActivityResultContract<I, O>, input: I, options: ActivityOptionsCompat?) {
                @Suppress("UNCHECKED_CAST") dispatchResult(requestCode, picked as O)
            }
        }
    }

    private fun waitFor(what: String, timeout: Long = 30000, condition: () -> Boolean) {
        val deadline = SystemClock.uptimeMillis() + timeout
        while (!condition()) {
            if (SystemClock.uptimeMillis() > deadline) throw AssertionError(what)
            SystemClock.sleep(150)
        }
    }
    private fun shown(tag: String) = compose.onAllNodesWithTag(tag).fetchSemanticsNodes().isNotEmpty()
    private fun file(name: String) = Uri.fromFile(File(dir!!, name))
    private fun scrollTo(tag: String) { compose.onNodeWithTag("geckoImportList").performScrollToNode(hasTestTag(tag)) }
    private fun text(words: String) = compose.onAllNodesWithText(words, substring = true).fetchSemanticsNodes().isNotEmpty()

    private fun start(): GeckoBrowser {
        assumeTrue("Pass firefoxExportDir", dir != null)
        lateinit var gecko: GeckoBrowser
        compose.runOnUiThread {
            BrowserRuntime.get(app).setEngine(BrowserRules.ENGINE_GECKO)
            gecko = GeckoBrowser.get(app)
            gecko.tabs.map { it.id }.forEach(gecko::close)
            gecko.importer.reset()
            gecko.setTracking(GeckoRules.TRACKING_STANDARD); gecko.setColors(GeckoRules.COLORS_SYSTEM); gecko.setKeepHistory(true)
            gecko.bookmarks.forEach(gecko::removeBookmark)
            gecko.addFavorites(listOf(GeckoRules.Link("https://kept.example.org/", "Already here")))
            val client = LiveClient(app)
            compose.activity.setContent { RaticodeTheme { CompositionLocalProvider(LocalActivityResultRegistryOwner provides picker) { BrowserPage(client, null) {} } } }
        }
        waitFor("the Gecko page", 10000) { shown("geckoPage") }
        return gecko
    }
    private fun openImport() {
        compose.onNodeWithTag("geckoMenu").performClick()
        waitFor("menu") { shown("geckoMenuSheet") }
        compose.onNodeWithText("Import from Firefox").performScrollTo().performClick()
        waitFor("import sheet") { shown("geckoImportOther") || shown("geckoImportFiles") }
        if (!shown("geckoImportFiles")) compose.onNodeWithTag("geckoImportOther").performClick() // When Firefox is on this phone, these are folded away.
        waitFor("computer steps") { shown("geckoImportFiles") }
    }
    private fun end(gecko: GeckoBrowser) {
        compose.runOnUiThread { gecko.extensions.forEach(gecko::uninstall); gecko.importer.reset(); gecko.setKeepHistory(true); BrowserRuntime.get(app).setEngine(BrowserRules.ENGINE_RATICODE) }
        SystemClock.sleep(1200)
    }

    @Test fun importsChosenExtensionsSettingsAndBookmarksFromFirefoxFiles() {
        val gecko = start()
        try {
            openImport()
            assertFalse("Nothing to import until something is chosen", shown("geckoImportRun"))
            picked = listOf(file("extensions.json"), file("prefs.js"), file("bookmarks.html"), file("logins.json"))
            compose.onNodeWithTag("geckoImportFiles").performClick()
            val dark = "addon@darkreader.org"
            waitFor("Firefox Add-ons answered", 45000) { gecko.importer.busy == 0 && dark in gecko.importer.listings }
            val preview = gecko.importer.preview
            assertEquals("Only the extension the user added", listOf(dark), preview.extensions.map { it.id })
            assertTrue("Firefox's own parts are left out", preview.builtIn >= 5)
            assertEquals(setOf(GeckoImport.TRACKING, GeckoImport.COLORS, GeckoImport.HISTORY), preview.settings.map { it.key }.toSet())
            assertTrue("Bookmarks: ${preview.bookmarks.map { it.url }}", preview.bookmarks.any { it.url == "https://example.com/a?x=1&y=2" })
            assertTrue("The password file is refused by name", preview.notes.any { it.what == "logins.json" && "passwords" in it.why })
            scrollTo("geckoImportExt-$dark")
            assertTrue(text("Made for Android"))

            instrumentation.uiAutomation.executeShellCommand("screencap -p /data/local/tmp/raticode-gecko-import.png").close()
            SystemClock.sleep(600)
            // Leave one setting out: only what is ticked changes.
            scrollTo("geckoImportSetting-${GeckoImport.COLORS}")
            compose.onNode(hasAnyAncestor(hasTestTag("geckoImportSetting-${GeckoImport.COLORS}")) and isToggleable()).performClick()
            assertEquals("Nothing changes before Import", GeckoRules.TRACKING_STANDARD, gecko.saved.settings.tracking)
            scrollTo("geckoImportRun")
            compose.onNodeWithTag("geckoImportRun").performClick()

            // The extension still needs the user's approval.
            waitFor("the permission dialog", 60000) { shown("geckoInstallAllow") }
            assertTrue(gecko.extensions.none { it.id == dark })
            compose.onNodeWithTag("geckoInstallAllow").performClick()
            waitFor("Dark Reader added", 30000) { gecko.extensions.any { it.id == dark && it.enabled } }

            assertEquals(GeckoRules.TRACKING_STRICT, gecko.saved.settings.tracking)
            assertEquals("The unticked setting stayed", GeckoRules.COLORS_SYSTEM, gecko.saved.settings.colors)
            assertFalse(gecko.saved.settings.keepHistory)
            assertTrue("Existing favorites stay", gecko.isBookmarked("https://kept.example.org/"))
            assertTrue(gecko.isBookmarked("https://example.com/a?x=1&y=2"))
            assertTrue(gecko.bookmarks.all { it.profile == gecko.saved.currentProfile })
        } finally { end(gecko) }
    }

    @Test fun readsTroubleshootingDataCollectionsAndJsonBackups() {
        val gecko = start()
        try {
            openImport()
            compose.runOnUiThread { gecko.importer.addText(File(dir!!, "about-support.json").readText()) }
            val dark = "addon@darkreader.org"
            waitFor("troubleshooting data", 45000) { gecko.importer.busy == 0 && dark in gecko.importer.listings }
            assertEquals(listOf(dark), gecko.importer.preview.extensions.map { it.id })
            assertTrue(gecko.importer.preview.settings.any { it.key == GeckoImport.TRACKING })

            // A public collection, pasted as a link through the sheet's own field.
            compose.onNodeWithTag("geckoImportPaste").performTextReplacement("https://addons.mozilla.org/en-US/firefox/collections/mozilla/featured-add-ons/")
            scrollTo("geckoImportPasteGo") // On a small phone the keyboard can cover it.
            compose.onNodeWithTag("geckoImportPasteGo").performClick()
            waitFor("the collection", 45000) { gecko.importer.busy == 0 && gecko.importer.preview.extensions.size > 2 }
            assertTrue("Collection extensions can be added", gecko.importer.preview.extensions.count { gecko.importer.addable(it.id) } >= 2)

            compose.onNodeWithTag("geckoImportPaste").performTextReplacement("https://evil.example/collections/a/b/")
            scrollTo("geckoImportPasteGo")
            compose.onNodeWithTag("geckoImportPasteGo").performClick()
            waitFor("refused link", 5000) { shown("geckoImportError") }

            picked = listOf(file("bookmarks.json"))
            scrollTo("geckoImportFiles")
            compose.onNodeWithTag("geckoImportFiles").performClick()
            waitFor("JSON backup", 10000) { gecko.importer.preview.bookmarks.any { it.url == "https://www.mozilla.org/" } }
            assertTrue("Nothing was installed by reading", gecko.extensions.isEmpty())
        } finally { end(gecko) }
    }
}
