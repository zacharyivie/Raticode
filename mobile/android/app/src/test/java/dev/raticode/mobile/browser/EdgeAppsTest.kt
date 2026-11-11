package dev.raticode.mobile.browser

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Which Microsoft Edge Raticode uses, what it sends it and how the choice is saved. */
class EdgeAppsTest {
    /**
     * `sha256_cert_fingerprints` for `com.microsoft.emmx`, `.beta`, `.dev` and `.canary`, exactly as written in
     * https://login.microsoftonline.com/.well-known/assetlinks.json (November 7, 2026).
     */
    private val assetLinks = listOf(
        "01:E1:99:97:10:A8:2C:27:49:B4:D5:0C:44:5D:C8:5D:67:0B:61:36:08:9D:0A:76:6A:73:82:7C:82:A1:EA:C9",
        "F6:A1:71:39:95:6B:E7:C7:DD:0E:53:77:10:02:5D:D2:16:90:AE:80:5B:20:B6:19:EE:D9:D6:67:8A:C4:6B:28",
    )

    @Test fun microsoftsCertificatesArePinnedExactly() {
        assertEquals(assetLinks.map { it.replace(":", "").lowercase() }, EdgeApps.MICROSOFT_CERTS_SHA256)
        assetLinks.forEach { assertTrue(it, EdgeApps.pinned(it)) }
        for (hex in EdgeApps.MICROSOFT_CERTS_SHA256) {
            assertEquals(32, EdgeApps.certBytes(hex).size)
            assertTrue(EdgeApps.pinned(hex.uppercase()))
        }
        assertEquals(0x01.toByte(), EdgeApps.certBytes(EdgeApps.MICROSOFT_CERTS_SHA256[0])[0])
        assertEquals(0xc9.toByte(), EdgeApps.certBytes(EdgeApps.MICROSOFT_CERTS_SHA256[0])[31])
        assertEquals(0xf6.toByte(), EdgeApps.certBytes(EdgeApps.MICROSOFT_CERTS_SHA256[1])[0])
        assertEquals(0x28.toByte(), EdgeApps.certBytes(EdgeApps.MICROSOFT_CERTS_SHA256[1])[31])
        // Edge's developer build (`com.microsoft.emmx.local`), Mozilla's key and a near miss aren't Microsoft's Edge key.
        assertFalse(EdgeApps.pinned("32:A2:FC:74:D7:31:10:58:59:E5:A8:5D:F1:6D:95:F1:02:D8:5B:22:09:9B:80:64:C5:D8:91:5C:61:DA:D1:E0"))
        assertFalse(EdgeApps.pinned(FirefoxApps.MOZILLA_CERT_SHA256))
        assertFalse(EdgeApps.pinned(EdgeApps.MICROSOFT_CERTS_SHA256[0].dropLast(1) + "8"))
        assertFalse(EdgeApps.pinned(""))
    }

    @Test fun releaseEdgeIsPreferredThenBetaDevAndCanary() {
        assertEquals("com.microsoft.emmx", EdgeApps.preferred(listOf("com.microsoft.emmx.canary", "com.microsoft.emmx", "com.microsoft.emmx.beta"))?.pkg)
        assertEquals("com.microsoft.emmx.beta", EdgeApps.preferred(listOf("com.microsoft.emmx.dev", "com.microsoft.emmx.beta"))?.pkg)
        assertEquals("com.microsoft.emmx.dev", EdgeApps.preferred(listOf("com.microsoft.emmx.canary", "com.microsoft.emmx.dev"))?.pkg)
        assertEquals("com.microsoft.emmx.canary", EdgeApps.preferred(listOf("com.microsoft.emmx.canary"))?.pkg)
        // The developer build and other Microsoft apps are not offered.
        assertNull(EdgeApps.preferred(listOf("com.microsoft.emmx.local", "com.microsoft.bing", "com.microsoft.copilot")))
        assertEquals(listOf("Microsoft Edge", "Microsoft Edge Beta", "Microsoft Edge Dev", "Microsoft Edge Canary"), EdgeApps.BUILDS.map { it.name })
    }

    @Test fun edgeOpensWebAddressesOnly() {
        assertTrue(EdgeApps.openable("https://example.com/a"))
        assertTrue(EdgeApps.openable("http://192.168.1.5:5173/"))
        for (bad in listOf("javascript:alert(1)", "file:///sdcard/a.html", "edge://settings", "microsoft-edge:https://e.com/",
            "intent://x#Intent;end", "https://u:p@e.com/"))
            assertFalse(bad, EdgeApps.openable(bad))
    }

    @Test fun problemsAreNamedInOrder() {
        val found = EdgeApps.Found(EdgeApps.BUILDS[0], "142.0", enabled = true, genuine = true)
        assertTrue(found.usable); assertNull(EdgeApps.problem(found))
        assertEquals(EdgeApps.MISSING, EdgeApps.problem(null))
        // A look-alike is named as one even when it is also turned off.
        assertEquals(EdgeApps.NOT_MICROSOFT, EdgeApps.problem(found.copy(genuine = false, enabled = false)))
        assertFalse(found.copy(genuine = false).usable)
        assertEquals(EdgeApps.TURNED_OFF, EdgeApps.problem(found.copy(enabled = false)))
        assertFalse(found.copy(enabled = false).usable)
    }

    @Test fun edgesPlacesAreDescribedNotLinked() {
        // Edge documents no links into its screens, so each place says where it is instead of naming a link.
        assertEquals(EdgeApps.SIGN_IN, EdgeApps.PLACES.first())
        assertTrue(EdgeApps.PLACES.all { it.where.startsWith("In Edge, ") && "://" !in it.where })
        assertTrue("Microsoft account" in EdgeApps.SIGN_IN.where)
    }

    @Test fun theChoiceIsSavedAndAnythingElseMeansRaticode() {
        val saved = BrowserRules.Saved(engine = BrowserRules.ENGINE_EDGE, edgeMode = EdgeApps.MODE_TAB)
        assertEquals(saved, BrowserRules.decode(BrowserRules.encode(saved)))
        assertEquals("edge", BrowserRules.ENGINE_EDGE)
        // Like Firefox, Edge opens in its own app unless the user picks a tab over Raticode.
        assertEquals(EdgeApps.MODE_APP, BrowserRules.Saved().edgeMode)
        // Edge's mode and Firefox's are kept apart.
        val both = BrowserRules.Saved(engine = BrowserRules.ENGINE_FIREFOX, firefoxMode = FirefoxApps.MODE_TAB, edgeMode = EdgeApps.MODE_APP)
        assertEquals(both, BrowserRules.decode(BrowserRules.encode(both)))
        // Phone state saved before Edge existed reads as before.
        val older = BrowserRules.decode("""{"engine":"firefox","firefox_opens":"tab"}""")
        assertEquals(BrowserRules.ENGINE_FIREFOX, older.engine); assertEquals(FirefoxApps.MODE_TAB, older.firefoxMode); assertEquals(EdgeApps.MODE_APP, older.edgeMode)
        for (odd in listOf("Edge", "msedge", "chromium", "")) {
            val read = BrowserRules.decode("""{"engine":"$odd","edge_opens":"window"}""")
            assertEquals(odd, BrowserRules.ENGINE_RATICODE, read.engine); assertEquals(EdgeApps.MODE_APP, read.edgeMode)
        }
    }
}
