package dev.raticode.mobile.browser

import dev.raticode.mobile.live.DesktopBrowser
import dev.raticode.mobile.ui.computerTitle
import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/** Which Firefox, Chrome and Edge Raticode uses, how it checks their makers signed them, which Firefox screens it may open, and how the choice is saved. */
class BrowserAppsTest {
    private val chrome = ChromeApps.APP
    private val edge = MicrosoftEdge.APP
    private val firefox = Firefox.APP

    /** Hosts Firefox for Android's manifest registers for its deep-link scheme (Fenix `AndroidManifest.xml`, November 2026). */
    private val fenixHosts = setOf("enable_private_browsing", "home", "home_collections", "install_search_widget", "make_default_browser", "open",
        "privacy_report", "settings", "settings_accessibility", "settings_addon_manager", "settings_ai_controls", "settings_delete_browsing_data",
        "settings_ip_protection", "settings_logins", "settings_notifications", "settings_marketing_notifications", "settings_privacy",
        "settings_search_engine", "settings_tracking_protection", "settings_app_icon", "share_sheet", "turn_on_sync", "urls_bookmarks",
        "urls_history", "settings_private_browsing", "protections_dashboard", "settings_wallpapers")

    /**
     * `sha256_cert_fingerprints` for `com.microsoft.emmx`, `.beta`, `.dev` and `.canary`, exactly as written in
     * https://login.microsoftonline.com/.well-known/assetlinks.json (November 7, 2026).
     */
    private val microsoftAssetLinks = listOf(
        "01:E1:99:97:10:A8:2C:27:49:B4:D5:0C:44:5D:C8:5D:67:0B:61:36:08:9D:0A:76:6A:73:82:7C:82:A1:EA:C9",
        "F6:A1:71:39:95:6B:E7:C7:DD:0E:53:77:10:02:5D:D2:16:90:AE:80:5B:20:B6:19:EE:D9:D6:67:8A:C4:6B:28",
    )

    @Test fun firefoxChromeAndEdgeAreTheBrowserApps() {
        assertEquals(listOf(Firefox.ENGINE, ChromeApps.ENGINE, MicrosoftEdge.ENGINE), BrowserApps.ALL.map { it.engine })
        assertEquals(firefox, BrowserApps.byEngine("firefox"))
        assertEquals("Firefox", firefox.name); assertEquals("Mozilla", firefox.maker); assertEquals("org.mozilla.firefox", firefox.store)
        assertEquals("firefox", BrowserRules.engine("firefox")); assertEquals("Firefox", BrowserTabs.name("firefox"))
        assertEquals(edge, BrowserApps.byEngine("edge"))
        assertEquals("Edge", edge.name); assertEquals("Microsoft", edge.maker); assertEquals("com.microsoft.emmx", edge.store)
        assertEquals("https://play.google.com/store/apps/details?id=com.microsoft.emmx", edge.playPage)
        // A saved "edge" choice is still a browser this phone knows.
        assertEquals("edge", BrowserRules.engine("edge")); assertEquals("Edge", BrowserTabs.name("edge"))
    }

    @Test fun microsoftsCertificatesArePinnedExactlyOnEveryEdgeEdition() {
        val pinned = microsoftAssetLinks.map { it.replace(":", "").lowercase() }
        assertEquals(pinned, MicrosoftEdge.MICROSOFT_CERTS)
        for (edition in edge.editions) assertEquals(edition.pkg, pinned, edition.certs)
        assertEquals(0x01.toByte(), BrowserApps.certBytes(pinned[0])[0]); assertEquals(0xc9.toByte(), BrowserApps.certBytes(pinned[0])[31])
        assertEquals(0xf6.toByte(), BrowserApps.certBytes(pinned[1])[0]); assertEquals(0x28.toByte(), BrowserApps.certBytes(pinned[1])[31])
        val release = edge.editions[0]
        // Either of Microsoft's keys makes it genuine, as Android's own link verification accepts either.
        for (cert in pinned) assertTrue(edge.genuine(release) { it.contentEquals(BrowserApps.certBytes(cert)) })
        // Edge's developer build (`com.microsoft.emmx.local`), Mozilla's key, Google's and a near miss aren't Microsoft's Edge key.
        val others = listOf("32a2fc74d731105859e5a85df16d95f102d85b22099b8064c5d8915c61dad1e0", Firefox.MOZILLA_CERT_SHA256,
            ChromeApps.GOOGLE_CHROME_CERT, pinned[0].dropLast(1) + "8")
        for (other in others) assertFalse(other, edge.genuine(release) { it.contentEquals(BrowserApps.certBytes(other)) })
        assertFalse(edge.genuine(release) { false })
        // The digests asked about are exactly Microsoft's two, as bytes.
        val asked = mutableListOf<ByteArray>()
        edge.genuine(release) { asked += it; false }
        assertEquals(pinned, asked.map { bytes -> bytes.joinToString("") { "%02x".format(it) } })
    }

    @Test fun releaseEdgeIsPreferredThenBetaDevAndCanary() {
        assertEquals("com.microsoft.emmx", edge.preferred(listOf("com.microsoft.emmx.canary", "com.microsoft.emmx", "com.microsoft.emmx.beta"))?.pkg)
        assertEquals("com.microsoft.emmx.beta", edge.preferred(listOf("com.microsoft.emmx.dev", "com.microsoft.emmx.beta"))?.pkg)
        assertEquals("com.microsoft.emmx.dev", edge.preferred(listOf("com.microsoft.emmx.canary", "com.microsoft.emmx.dev"))?.pkg)
        assertEquals("com.microsoft.emmx.canary", edge.preferred(listOf("com.microsoft.emmx.canary"))?.pkg)
        // The developer build and other Microsoft apps are not offered.
        assertNull(edge.preferred(listOf("com.microsoft.emmx.local", "com.microsoft.bing", "com.microsoft.copilot")))
        assertEquals(listOf("Microsoft Edge", "Microsoft Edge Beta", "Microsoft Edge Dev", "Microsoft Edge Canary"), edge.editions.map { it.name })
    }

    @Test fun edgeOpensWebAddressesOnly() {
        assertTrue(edge.openable("https://example.com/a"))
        assertTrue(edge.openable("http://192.168.1.5:5173/"))
        for (bad in listOf("javascript:alert(1)", "file:///sdcard/a.html", "edge://settings", "microsoft-edge:https://e.com/",
            "intent://x#Intent;end", "https://u:p@e.com/"))
            assertFalse(bad, edge.openable(bad))
    }

    @Test fun eachUnusableEdgeIsNamedForWhatIsWrong() {
        val found = BrowserApp.Found(edge, edge.editions[0], "142.0", enabled = true, genuine = true)
        assertTrue(found.usable); assertNull(edge.problem(found))
        assertEquals("Edge isn't installed on this phone.", edge.problem(null))
        // A look-alike is named as one even when it is also turned off.
        assertEquals("The app named Edge on this phone isn't signed by Microsoft, so Raticode won't send it pages.",
            edge.problem(found.copy(genuine = false, enabled = false)))
        assertFalse(found.copy(genuine = false).usable)
        assertEquals("Edge is turned off in Android settings.", edge.problem(found.copy(enabled = false)))
        assertFalse(found.copy(enabled = false).usable)
    }

    @Test fun edgesPlacesAreDescribedNotLinkedAndEdgeCanSitBesideRaticode() {
        // Edge documents no links into its screens, so each place says where it is instead of naming a link.
        assertEquals(listOf("Sign in to Edge", "Edge settings", "Extensions", "Favorites", "History", "InPrivate browsing"), edge.places.map { it.label })
        assertTrue(edge.places.all { it.where.startsWith("In Edge, ") && "://" !in it.where })
        assertTrue("Microsoft account" in edge.places.first().where)
        assertNull(edge.signIn); assertTrue(edge.beside)
        // Chrome has a sign-in page instead, and no places or split-screen button.
        assertTrue(chrome.places.isEmpty()); assertFalse(chrome.beside)
    }

    @Test fun edgesOldSavedModeCarriesOverToAppModes() {
        // Saved before Edge became one of BrowserApps: its own "edge_opens" field.
        val old = BrowserRules.decode("""{"engine":"edge","edge_opens":"tab","firefox_opens":"app"}""")
        assertEquals(MicrosoftEdge.ENGINE, old.engine)
        assertEquals(BrowserApps.MODE_TAB, BrowserRules.appMode(old, "edge"))
        assertEquals(BrowserApps.MODE_APP, BrowserRules.appMode(old, ChromeApps.ENGINE))
        // The next save writes it under "app_opens" only, and it reads back the same.
        val written = JSONObject(BrowserRules.encode(old))
        assertFalse(written.has("edge_opens")); assertEquals("tab", written.getJSONObject("app_opens").getString("edge"))
        assertEquals(old, BrowserRules.decode(written.toString()))
        // A newer "app_opens" choice wins over the old field, and odd old values read as the Edge app.
        assertEquals(BrowserApps.MODE_APP, BrowserRules.appMode(BrowserRules.decode("""{"edge_opens":"tab","app_opens":{"edge":"app"}}"""), "edge"))
        assertEquals(BrowserApps.MODE_APP, BrowserRules.appMode(BrowserRules.decode("""{"edge_opens":"window"}"""), "edge"))
        assertEquals(BrowserApps.MODE_APP, BrowserRules.appMode(BrowserRules.Saved(), "edge"))
        // Edge's mode and Firefox's are kept apart.
        val both = BrowserRules.decode("""{"engine":"firefox","firefox_opens":"tab","edge_opens":"app"}""")
        assertEquals(BrowserApps.MODE_TAB, BrowserRules.appMode(both, Firefox.ENGINE)); assertEquals(BrowserApps.MODE_APP, BrowserRules.appMode(both, "edge"))
        for (odd in listOf("Edge", "msedge", "chromium", "")) {
            assertEquals(odd, BrowserRules.ENGINE_RATICODE, BrowserRules.decode("""{"engine":"$odd","edge_opens":"tab"}""").engine)
        }
    }

    @Test fun chromeIsPreferredThenBetaAndOtherChromiumAppsAreNotUsed() {
        assertEquals("com.android.chrome", chrome.preferred(listOf("com.chrome.beta", "com.android.chrome"))?.pkg)
        assertEquals("com.chrome.beta", chrome.preferred(listOf("com.chrome.dev", "com.chrome.beta"))?.pkg)
        // Dev and Canary certificates weren't read from a package; Chromium builds and look-alikes aren't Google's Chrome.
        assertNull(chrome.preferred(listOf("com.chrome.dev", "com.chrome.canary", "org.chromium.chrome", "com.google.android.apps.chrome")))
        assertEquals(chrome, BrowserApps.byEngine(ChromeApps.ENGINE))
        assertNull(BrowserApps.byEngine(BrowserRules.ENGINE_GECKO)); assertNull(BrowserApps.byEngine(BrowserRules.ENGINE_RATICODE))
    }

    @Test fun googlesCertificatesAreTheOnesReadFromChromesPackages() {
        // apksigner on Chrome 133.0.6943.137 and Chrome Beta 158.0.8127.22; the same digests Google's Credential Manager list gives.
        assertEquals(listOf("f0fd6c5b410f25cb25c3b53346c8972fae30f8ee7411df910480ad6b2d60db83"), chrome.editions[0].certs)
        assertEquals(listOf("da633d34b69e63ae2103b49d53ce052fc5f7f3c53aab94fdc2a208bdfd14249c"), chrome.editions[1].certs)
        for (edition in chrome.editions) for (cert in edition.certs) {
            assertEquals(32, BrowserApps.certBytes(cert).size)
            assertTrue(cert.all { it in "0123456789abcdef" })
        }
        assertEquals(0xf0.toByte(), BrowserApps.certBytes(ChromeApps.GOOGLE_CHROME_CERT)[0])
        assertEquals(0x83.toByte(), BrowserApps.certBytes(ChromeApps.GOOGLE_CHROME_CERT)[31])
    }

    @Test fun onlyAPackageSignedWithItsOwnGoogleCertificateIsGenuine() {
        val stable = chrome.editions[0]; val beta = chrome.editions[1]
        val google = BrowserApps.certBytes(ChromeApps.GOOGLE_CHROME_CERT)
        assertTrue(chrome.genuine(stable) { it.contentEquals(google) })
        // A look-alike under Chrome's name, signed with any other key, isn't; nor is Chrome Beta's key on Chrome's package.
        assertFalse(chrome.genuine(stable) { false })
        assertFalse(chrome.genuine(stable) { it.contentEquals(BrowserApps.certBytes(ChromeApps.GOOGLE_CHROME_BETA_CERT)) })
        assertTrue(chrome.genuine(beta) { it.contentEquals(BrowserApps.certBytes(ChromeApps.GOOGLE_CHROME_BETA_CERT)) })
        assertFalse(chrome.genuine(beta) { it.contentEquals(google) })
        // The digests asked about are exactly the listed ones, as bytes.
        val asked = mutableListOf<ByteArray>()
        chrome.genuine(stable) { asked += it; false }
        assertEquals(1, asked.size); assertArrayEquals(google, asked[0])
    }

    @Test fun eachUnusableChromeIsNamedForWhatIsWrong() {
        val found = BrowserApp.Found(chrome, chrome.editions[0], "133.0.6943.137", enabled = true, genuine = false)
        assertFalse(found.usable)
        assertFalse(found.copy(genuine = true, enabled = false).usable)
        assertTrue(found.copy(genuine = true).usable)
        assertEquals("Chrome isn't installed on this phone.", chrome.problem(null))
        assertEquals("The app named Chrome on this phone isn't signed by Google, so Raticode won't send it pages.", chrome.problem(found))
        assertEquals(chrome.notGenuine, chrome.problem(found.copy(enabled = false)))
        assertEquals("Chrome is turned off in Android settings.", chrome.problem(found.copy(genuine = true, enabled = false)))
        assertNull(chrome.problem(found.copy(genuine = true)))
        assertEquals("https://play.google.com/store/apps/details?id=com.android.chrome", chrome.playPage)
    }

    @Test fun chromeOpensWebAddressesOnly() {
        assertTrue(chrome.openable("https://example.com/a"))
        assertTrue(chrome.openable("http://192.168.1.5:5173/"))
        assertTrue(chrome.openable(ChromeApps.SIGN_IN_PAGE))
        for (bad in listOf("javascript:alert(1)", "file:///sdcard/a.html", "chrome://settings", "googlechrome://navigate?url=x",
                "intent://x#Intent;end", "content://x/y", "https://u:p@e.com/"))
            assertFalse(bad, chrome.openable(bad))
    }

    @Test fun theChoiceAndHowPagesOpenAreSaved() {
        val saved = BrowserRules.Saved(engine = ChromeApps.ENGINE, appModes = mapOf(ChromeApps.ENGINE to BrowserApps.MODE_TAB))
        assertEquals(saved, BrowserRules.decode(BrowserRules.encode(saved)))
        assertEquals(BrowserApps.MODE_TAB, BrowserRules.appMode(saved, ChromeApps.ENGINE))
        // The Chrome app is the default way pages open, and anything unknown reads as it.
        assertEquals(BrowserApps.MODE_APP, BrowserRules.appMode(BrowserRules.Saved(), ChromeApps.ENGINE))
        val odd = BrowserRules.decode("""{"engine":"chrome","app_opens":{"chrome":"window","edge-legacy":"tab"}}""")
        assertEquals(ChromeApps.ENGINE, odd.engine); assertEquals(mapOf(ChromeApps.ENGINE to BrowserApps.MODE_APP), odd.appModes)
        // Engines this phone doesn't know aren't written back either.
        val encoded = JSONObject(BrowserRules.encode(BrowserRules.Saved(appModes = mapOf("netscape" to "tab", ChromeApps.ENGINE to "tab"))))
        assertEquals("""{"chrome":"tab"}""", encoded.getJSONObject("app_opens").toString())
    }

    @Test fun savesFromBeforeChromeLoadUnchanged() {
        // A Firefox choice saved before Chrome existed, with its Firefox mode and no app_opens.
        val old = BrowserRules.decode("""{"v":1,"tabs":[],"engine":"firefox","firefox_mode":"app","keep_history":false,"text_zoom":120}""")
        assertEquals(Firefox.ENGINE, old.engine); assertEquals(BrowserApps.MODE_APP, BrowserRules.appMode(old, Firefox.ENGINE))
        assertFalse(old.keepHistory); assertEquals(120, old.textZoom); assertEquals(emptyMap<String, String>(), old.appModes)
        assertEquals(BrowserRules.ENGINE_RATICODE, BrowserRules.decode("""{"v":1,"engine":"raticode","firefox_mode":"tab"}""").engine)
        // Saves from before the engine choice have no engine at all.
        val oldest = BrowserRules.decode("""{"v":1,"tabs":[{"i":"a","u":"https://example.com/","t":"Example"}],"active":"a"}""")
        assertEquals(BrowserRules.ENGINE_RATICODE, oldest.engine); assertEquals("a", oldest.active)
        assertEquals(BrowserRules.Saved(), BrowserRules.decode("not json"))
        // An unknown engine, for example one a later version adds, reads as Raticode.
        assertEquals(BrowserRules.ENGINE_RATICODE, BrowserRules.decode("""{"engine":"netscape"}""").engine)
    }

    @Test fun theComputersChromeIsNamedOnItsTabList() {
        val view = DesktopBrowser.View(true, false, "", emptyList(), "", 0, "chrome")
        assertEquals("Chrome on your computer", computerTitle(view))
        assertEquals("Firefox on your computer", computerTitle(view.copy(engine = "firefox")))
        assertEquals("On your computer", computerTitle(view.copy(engine = "raticode")))
        assertEquals("On your computer", computerTitle(null))
    }

    @Test fun theManifestMakesOnlyTheBrowserAppsOwnPackagesVisible() {
        val manifest = listOf("src/main/AndroidManifest.xml", "app/src/main/AndroidManifest.xml").map(::File).first { it.exists() }.readText()
        val queries = manifest.substringAfter("<queries>").substringBefore("</queries>")
        for (edition in firefox.editions + chrome.editions + edge.editions) assertTrue(edition.pkg, "<package android:name=\"${edition.pkg}\" />" in queries)
        assertFalse("com.microsoft.emmx.local" in queries)
        assertFalse("com.chrome.dev" in queries); assertFalse("com.chrome.canary" in queries)
        assertFalse("org.mozilla.fenix" in queries); assertFalse("org.mozilla.focus" in queries)
    }

    // Firefox -------------------------------------------------------------------------------------------------------

    @Test fun releaseFirefoxIsPreferredThenBetaAndNightlyIsNotUsed() {
        assertEquals("org.mozilla.firefox", firefox.preferred(listOf("org.mozilla.fenix", "org.mozilla.firefox"))?.pkg)
        assertEquals("org.mozilla.firefox_beta", firefox.preferred(listOf("org.mozilla.fenix", "org.mozilla.firefox_beta"))?.pkg)
        // Nightly's signing key wasn't checked, so it isn't used.
        assertNull(firefox.preferred(listOf("org.mozilla.fenix")))
        // Focus and other look-alikes are not Firefox: no account, sync or add-ons.
        assertNull(firefox.preferred(listOf("org.mozilla.focus", "org.mozilla.klar")))
        assertEquals(listOf("Firefox", "Firefox Beta"), firefox.editions.map { it.name })
        assertEquals(listOf("fenix", "fenix-beta"), firefox.editions.map { it.scheme })
    }

    @Test fun onlyFirefoxScreensRaticodeOffersAreLinked() {
        val release = firefox.editions[0]
        assertEquals("fenix://turn_on_sync", firefox.deepLink(release, "turn_on_sync"))
        assertEquals("fenix-beta://settings", firefox.deepLink(firefox.editions[1], "settings"))
        assertEquals("fenix://home", firefox.deepLink(release, "home"))
        // "open" would load any address with no say from the user; share_sheet and others aren't offered either.
        for (host in listOf("open", "share_sheet", "make_default_browser", "settings?x=1", "settings/../open", "SETTINGS", "", " settings"))
            assertNull(host, firefox.deepLink(release, host))
        // Every screen Raticode offers, and the sign-in, is in the fixed list and is one Firefox registers.
        val offered = firefox.places.map { it.host } + firefox.signIn?.host
        assertTrue(offered.all { it != null && it in Firefox.HOSTS })
        assertTrue(Firefox.HOSTS.all { it in fenixHosts })
        assertEquals("turn_on_sync", firefox.signIn?.host); assertNull(firefox.signIn?.url)
        assertEquals("Sign in to Firefox", firefox.signIn?.label)
        assertEquals(listOf("Firefox settings", "Add-ons", "Tracking protection", "Search engine", "Passwords", "Delete browsing data",
            "Bookmarks", "History", "Private browsing"), firefox.places.map { it.label })
        // Firefox's places open by link, so none needs "where to tap".
        assertTrue(firefox.places.all { it.where.isEmpty() })
        assertTrue(firefox.beside)
    }

    @Test fun aHostOutsideTheAppsListBuildsNoDeepLink() {
        // Chrome and Edge list no hosts, so no screen of theirs is ever linked, even with a scheme.
        val withScheme = chrome.editions[0].copy(scheme = "googlechrome")
        for (host in listOf("settings", "turn_on_sync", "navigate")) {
            assertNull(chrome.deepLink(withScheme, host)); assertNull(edge.deepLink(edge.editions[0], host))
        }
        // A Firefox edition without a scheme links nothing, even for a listed host.
        assertNull(firefox.deepLink(firefox.editions[0].copy(scheme = null), "settings"))
        // A place naming a host outside the list still opens nothing.
        val odd = BrowserApp("odd", "Odd", "Maker", firefox.editions, "odd", null, "", listOf(BrowserApp.Place("Open", "x", host = "open")),
            linkHosts = setOf("settings"))
        assertNull(odd.deepLink(odd.editions[0], "open")); assertEquals("fenix://settings", odd.deepLink(odd.editions[0], "settings"))
    }

    @Test fun aSignInIsAWebPageOrAScreenNeverBoth() {
        assertEquals(ChromeApps.SIGN_IN_PAGE, chrome.signIn?.url); assertNull(chrome.signIn?.host)
        assertTrue(runCatching { BrowserApp.SignIn("x", "y") }.isFailure)
        assertTrue(runCatching { BrowserApp.SignIn("x", "y", url = "https://e.com/", host = "turn_on_sync") }.isFailure)
    }

    @Test fun mozillasCertificateIsPinnedOnBothFirefoxEditions() {
        for (edition in firefox.editions) assertEquals(edition.pkg, listOf(Firefox.MOZILLA_CERT_SHA256), edition.certs)
        val mozilla = BrowserApps.certBytes(Firefox.MOZILLA_CERT_SHA256)
        assertEquals(32, mozilla.size)
        assertEquals(0xa7.toByte(), mozilla[0]); assertEquals(0x04.toByte(), mozilla[31])
        assertTrue(firefox.genuine(firefox.editions[0]) { it.contentEquals(mozilla) })
        assertFalse(firefox.genuine(firefox.editions[0]) { it.contentEquals(BrowserApps.certBytes(ChromeApps.GOOGLE_CHROME_CERT)) })
        assertFalse(firefox.genuine(firefox.editions[1]) { false })
    }

    @Test fun eachUnusableFirefoxIsNamedForWhatIsWrong() {
        val found = BrowserApp.Found(firefox, firefox.editions[0], "159.0.1", enabled = true, genuine = false)
        assertFalse(found.usable)
        assertFalse(found.copy(genuine = true, enabled = false).usable)
        assertTrue(found.copy(genuine = true).usable)
        // The same words the Firefox page always used.
        assertEquals("Firefox isn't installed on this phone.", firefox.problem(null))
        assertEquals("The app named Firefox on this phone isn't signed by Mozilla, so Raticode won't send it pages.", firefox.problem(found))
        assertEquals(firefox.notGenuine, firefox.problem(found.copy(enabled = false)))
        assertEquals("Firefox is turned off in Android settings.", firefox.problem(found.copy(genuine = true, enabled = false)))
        assertNull(firefox.problem(found.copy(genuine = true)))
        assertEquals("https://play.google.com/store/apps/details?id=org.mozilla.firefox", firefox.playPage)
    }

    @Test fun firefoxOpensWebAddressesOnly() {
        assertTrue(firefox.openable("https://example.com/a"))
        assertTrue(firefox.openable("http://192.168.1.5:5173/"))
        for (bad in listOf("javascript:alert(1)", "file:///sdcard/a.html", "fenix://open?url=x", "intent://x#Intent;end", "https://u:p@e.com/"))
            assertFalse(bad, firefox.openable(bad))
    }

    @Test fun firefoxsOldSavedModeCarriesOverToAppModes() {
        // A save holding only Firefox's own "firefox_opens" field loads with Firefox opening pages over Raticode.
        val old = BrowserRules.decode("""{"firefox_opens":"tab"}""")
        assertEquals(BrowserApps.MODE_TAB, BrowserRules.appMode(old, Firefox.ENGINE))
        assertEquals(mapOf(Firefox.ENGINE to BrowserApps.MODE_TAB), old.appModes)
        // The next save writes it under "app_opens" only, and it reads back the same.
        val written = JSONObject(BrowserRules.encode(old))
        assertFalse(written.has("firefox_opens")); assertEquals("tab", written.getJSONObject("app_opens").getString("firefox"))
        assertEquals(old, BrowserRules.decode(written.toString()))
        // A newer "app_opens" choice wins over the old field.
        assertEquals(BrowserApps.MODE_APP, BrowserRules.appMode(BrowserRules.decode("""{"firefox_opens":"tab","app_opens":{"firefox":"app"}}"""), Firefox.ENGINE))
        assertEquals(BrowserApps.MODE_APP, BrowserRules.appMode(BrowserRules.decode("""{"engine":"firefox","firefox_opens":"app"}"""), Firefox.ENGINE))
        // The Firefox app is the default: going back to Raticode can't close the page or stop its music there.
        assertEquals(BrowserApps.MODE_APP, BrowserRules.appMode(BrowserRules.Saved(), Firefox.ENGINE))
        // The older "firefox_mode" held the old default ("tab"), not a choice; it moves to the app.
        assertEquals(BrowserApps.MODE_APP, BrowserRules.appMode(BrowserRules.decode("""{"engine":"firefox","firefox_mode":"tab"}"""), Firefox.ENGINE))
        val odd = BrowserRules.decode("""{"engine":"netscape","firefox_opens":"window"}""")
        assertEquals(BrowserRules.ENGINE_RATICODE, odd.engine); assertEquals(BrowserApps.MODE_APP, BrowserRules.appMode(odd, Firefox.ENGINE))
        val saved = BrowserRules.Saved(engine = Firefox.ENGINE, appModes = mapOf(Firefox.ENGINE to BrowserApps.MODE_TAB))
        assertEquals(saved, BrowserRules.decode(BrowserRules.encode(saved)))
    }
}
