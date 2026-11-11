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

/** Which Chrome Raticode uses, how it checks Google signed it, and how the choice is saved. */
class ChromeAppsTest {
    private val chrome = ChromeApps.APP

    @Test fun chromeIsPreferredThenBetaAndOtherChromiumAppsAreNotUsed() {
        assertEquals("com.android.chrome", chrome.preferred(listOf("com.chrome.beta", "com.android.chrome"))?.pkg)
        assertEquals("com.chrome.beta", chrome.preferred(listOf("com.chrome.dev", "com.chrome.beta"))?.pkg)
        // Dev and Canary certificates weren't read from a package; Chromium builds and look-alikes aren't Google's Chrome.
        assertNull(chrome.preferred(listOf("com.chrome.dev", "com.chrome.canary", "org.chromium.chrome", "com.google.android.apps.chrome")))
        assertEquals(chrome, BrowserApps.byEngine(ChromeApps.ENGINE))
        assertNull(BrowserApps.byEngine(BrowserRules.ENGINE_FIREFOX)); assertNull(BrowserApps.byEngine(BrowserRules.ENGINE_RATICODE))
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
        val firefox = BrowserRules.decode("""{"v":1,"tabs":[],"engine":"firefox","firefox_mode":"app","keep_history":false,"text_zoom":120}""")
        assertEquals(BrowserRules.ENGINE_FIREFOX, firefox.engine); assertEquals(FirefoxApps.MODE_APP, firefox.firefoxMode)
        assertFalse(firefox.keepHistory); assertEquals(120, firefox.textZoom); assertEquals(emptyMap<String, String>(), firefox.appModes)
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

    @Test fun theManifestMakesOnlyChromesOwnPackagesVisible() {
        val manifest = listOf("src/main/AndroidManifest.xml", "app/src/main/AndroidManifest.xml").map(::File).first { it.exists() }.readText()
        val queries = manifest.substringAfter("<queries>").substringBefore("</queries>")
        for (edition in chrome.editions) assertTrue(edition.pkg, "<package android:name=\"${edition.pkg}\" />" in queries)
        assertFalse("com.chrome.dev" in queries); assertFalse("com.chrome.canary" in queries)
    }
}
