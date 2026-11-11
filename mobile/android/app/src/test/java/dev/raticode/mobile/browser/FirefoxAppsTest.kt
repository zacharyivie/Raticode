package dev.raticode.mobile.browser

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Which Firefox Raticode uses and which of its screens it may open. */
class FirefoxAppsTest {
    /** Hosts Firefox for Android's manifest registers for its deep-link scheme (Fenix `AndroidManifest.xml`, November 2026). */
    private val fenixHosts = setOf("enable_private_browsing", "home", "home_collections", "install_search_widget", "make_default_browser", "open",
        "privacy_report", "settings", "settings_accessibility", "settings_addon_manager", "settings_ai_controls", "settings_delete_browsing_data",
        "settings_ip_protection", "settings_logins", "settings_notifications", "settings_marketing_notifications", "settings_privacy",
        "settings_search_engine", "settings_tracking_protection", "settings_app_icon", "share_sheet", "turn_on_sync", "urls_bookmarks",
        "urls_history", "settings_private_browsing", "protections_dashboard", "settings_wallpapers")

    @Test fun releaseFirefoxIsPreferredThenBetaAndNightlyIsNotUsed() {
        assertEquals("org.mozilla.firefox", FirefoxApps.preferred(listOf("org.mozilla.fenix", "org.mozilla.firefox"))?.pkg)
        assertEquals("org.mozilla.firefox_beta", FirefoxApps.preferred(listOf("org.mozilla.fenix", "org.mozilla.firefox_beta"))?.pkg)
        // Nightly's signing key wasn't checked, so it isn't used.
        assertNull(FirefoxApps.preferred(listOf("org.mozilla.fenix")))
        // Focus and other look-alikes are not Firefox: no account, sync or add-ons.
        assertNull(FirefoxApps.preferred(listOf("org.mozilla.focus", "org.mozilla.klar")))
    }

    @Test fun onlyFirefoxScreensRaticodeOffersAreLinked() {
        val release = FirefoxApps.BUILDS.first()
        assertEquals("fenix://turn_on_sync", FirefoxApps.deepLink(release, "turn_on_sync"))
        assertEquals("fenix-beta://settings", FirefoxApps.deepLink(FirefoxApps.BUILDS[1], "settings"))
        // "open" would load any address with no say from the user; share_sheet and others aren't offered either.
        for (host in listOf("open", "share_sheet", "make_default_browser", "settings?x=1", "")) assertNull(host, FirefoxApps.deepLink(release, host))
        // Every screen Raticode offers is one Firefox registers.
        assertTrue(FirefoxApps.SCREENS.all { it.host in fenixHosts })
        assertEquals("turn_on_sync", FirefoxApps.SCREENS.first().host)
    }

    @Test fun mozillasCertificateDigestIsWellFormed() {
        assertEquals(32, FirefoxApps.certBytes().size)
        assertEquals(0xa7.toByte(), FirefoxApps.certBytes()[0]); assertEquals(0x04.toByte(), FirefoxApps.certBytes()[31])
        val found = FirefoxApps.Found(FirefoxApps.BUILDS[0], "159.0.1", enabled = true, genuine = false)
        assertFalse(found.usable)
        assertFalse(found.copy(genuine = true, enabled = false).usable)
        assertTrue(found.copy(genuine = true).usable)
        // Each unusable Firefox is named for what is wrong, not "didn't open".
        assertEquals(FirefoxApps.MISSING, FirefoxApps.problem(null))
        assertEquals(FirefoxApps.NOT_MOZILLA, FirefoxApps.problem(found))
        assertEquals(FirefoxApps.NOT_MOZILLA, FirefoxApps.problem(found.copy(enabled = false)))
        assertEquals(FirefoxApps.TURNED_OFF, FirefoxApps.problem(found.copy(genuine = true, enabled = false)))
        assertNull(FirefoxApps.problem(found.copy(genuine = true)))
    }

    @Test fun firefoxOpensWebAddressesOnly() {
        assertTrue(FirefoxApps.openable("https://example.com/a"))
        assertTrue(FirefoxApps.openable("http://192.168.1.5:5173/"))
        for (bad in listOf("javascript:alert(1)", "file:///sdcard/a.html", "fenix://open?url=x", "intent://x#Intent;end", "https://u:p@e.com/"))
            assertFalse(bad, FirefoxApps.openable(bad))
    }

    @Test fun theChoiceIsSavedAndAnythingElseMeansRaticode() {
        val saved = BrowserRules.Saved(engine = BrowserRules.ENGINE_FIREFOX, firefoxMode = FirefoxApps.MODE_TAB)
        assertEquals(saved, BrowserRules.decode(BrowserRules.encode(saved)))
        assertEquals(BrowserRules.ENGINE_RATICODE, BrowserRules.Saved().engine)
        val odd = BrowserRules.decode("""{"engine":"netscape","firefox_opens":"window"}""")
        assertEquals(BrowserRules.ENGINE_RATICODE, odd.engine); assertEquals(FirefoxApps.MODE_APP, odd.firefoxMode)
    }

    @Test fun firefoxOpensPagesInItsOwnAppUnlessChosenSinceTheSwitch() {
        // The Firefox app is the default: going back to Raticode can't close the page or stop its music there.
        assertEquals(FirefoxApps.MODE_APP, BrowserRules.Saved().firefoxMode)
        // The old setting's "tab" was the old default, not a choice; it moves to the app. A choice made since stays.
        assertEquals(FirefoxApps.MODE_APP, BrowserRules.decode("""{"engine":"firefox","firefox_mode":"tab"}""").firefoxMode)
        assertEquals(FirefoxApps.MODE_TAB, BrowserRules.decode("""{"engine":"firefox","firefox_opens":"tab"}""").firefoxMode)
        assertEquals(FirefoxApps.MODE_APP, BrowserRules.decode("""{"engine":"firefox","firefox_opens":"app"}""").firefoxMode)
    }
}
