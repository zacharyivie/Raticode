package dev.raticode.mobile.open

import dev.raticode.mobile.browser.BrowserRules
import dev.raticode.mobile.live.FileViewer
import dev.raticode.mobile.ui.BrowserRoleStep
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.w3c.dom.Element
import java.io.File
import javax.xml.parsers.DocumentBuilderFactory

class OpenTypesTest {
    private val android = "http://schemas.android.com/apk/res/android"
    private val manifestFile = listOf("src/main/AndroidManifest.xml", "app/src/main/AndroidManifest.xml").map(::File).first { it.exists() }
    private val manifest = DocumentBuilderFactory.newInstance().apply { isNamespaceAware = true }.newDocumentBuilder().parse(manifestFile)

    private fun Element.children(tag: String): List<Element> {
        val nodes = getElementsByTagName(tag)
        return (0 until nodes.length).map { nodes.item(it) as Element }
    }
    private fun Element.attr(name: String) = getAttributeNS(android, name)
    private fun activity(name: String) = manifest.documentElement.children("activity").single { it.attr("name") == name }
    private fun filterValues(filter: Element, tag: String, attr: String) = filter.children(tag).map { it.attr(attr) }.filter { it.isNotEmpty() }

    @Test fun theFileViewersFilterListsExactlyTheTypesRaticodeShowsAndOnlyContentAddresses() {
        val viewer = activity(".open.OpenFileActivity")
        assertEquals("true", viewer.attr("exported"))
        assertEquals("@bool/open_with_raticode", viewer.attr("enabled"))
        val filter = viewer.children("intent-filter").single()
        assertEquals(listOf("android.intent.action.VIEW"), filterValues(filter, "action", "name"))
        assertEquals(listOf("android.intent.category.DEFAULT"), filterValues(filter, "category", "name"))
        assertEquals(listOf("content"), filterValues(filter, "data", "scheme"))
        assertEquals(OpenTypes.mimeTypes, filterValues(filter, "data", "mimeType"))
        // No wildcards: Raticode claims only what it can show.
        assertTrue(OpenTypes.mimeTypes.none { it.contains('*') })
        assertEquals(OpenTypes.mimeTypes.size, OpenTypes.mimeTypes.toSet().size)
    }

    @Test fun theLinkScreenMakesRaticodeABrowserAndonlyTakesWebLinks() {
        val link = activity(".open.OpenLinkActivity")
        assertEquals("true", link.attr("exported"))
        assertEquals("@android:style/Theme.NoDisplay", link.attr("theme"))
        val filters = link.children("intent-filter")
        val view = filters.single { filterValues(it, "action", "name") == listOf("android.intent.action.VIEW") }
        assertEquals(setOf("android.intent.category.DEFAULT", "android.intent.category.BROWSABLE"), filterValues(view, "category", "name").toSet())
        // Any host: Android's browser role wants a filter for every http and https link.
        assertEquals(listOf("http", "https"), filterValues(view, "data", "scheme"))
        assertTrue(filterValues(view, "data", "host").isEmpty())
        assertTrue(filters.any { "android.intent.category.APP_BROWSER" in filterValues(it, "category", "name") })
    }

    @Test fun openingFilesNeedsNoStoragePermission() {
        val permissions = manifest.documentElement.children("uses-permission").map { it.attr("name") }
        for (storage in listOf("READ_EXTERNAL_STORAGE", "WRITE_EXTERNAL_STORAGE", "MANAGE_EXTERNAL_STORAGE", "READ_MEDIA_IMAGES", "READ_MEDIA_VIDEO", "READ_MEDIA_AUDIO")) {
            assertFalse(storage, permissions.any { it.endsWith(storage) })
        }
        assertTrue(OpenTypes.readableScheme("content"))
        assertTrue(OpenTypes.readableScheme("CONTENT"))
        assertFalse(OpenTypes.readableScheme("file"))
        assertFalse(OpenTypes.readableScheme(null))
        assertTrue(OpenTypes.readableFrom("content", "com.android.providers.downloads.documents", "dev.raticode.mobile"))
        assertFalse(OpenTypes.readableFrom("content", "dev.raticode.mobile.opened", "dev.raticode.mobile"))
        assertFalse(OpenTypes.readableFrom("content", "dev.raticode.mobile.debug.opened", "dev.raticode.mobile"))
        assertFalse(OpenTypes.readableFrom("file", "", "dev.raticode.mobile"))
        // Android drops a user prefix before opening, so `0@` must not slip Raticode's own cache past the check.
        assertFalse(OpenTypes.readableFrom("content", "0@dev.raticode.mobile.opened", "dev.raticode.mobile"))
        assertFalse(OpenTypes.readableFrom("content", "a@b@dev.raticode.mobile.opened", "dev.raticode.mobile"))
        assertTrue(OpenTypes.readableFrom("content", "0@com.android.providers.downloads.documents", "dev.raticode.mobile"))
    }

    @Test fun aCopyRaticodeHandedOutIsRefusedWhileALockIsOn() {
        val own = "dev.raticode.mobile.opened"
        assertTrue(OpenTypes.readableFrom("content", own, "dev.raticode.mobile", handedOut = true, locked = false))
        // App lock or child lock on: Raticode's own report must not show on this screen.
        assertFalse(OpenTypes.readableFrom("content", own, "dev.raticode.mobile", handedOut = true, locked = true))
        assertFalse(OpenTypes.readableFrom("content", "0@$own", "dev.raticode.mobile", handedOut = true, locked = true))
        // Not handed out stays refused, locked or not, and a handed-out flag never lets a file:// path through.
        assertFalse(OpenTypes.readableFrom("content", own, "dev.raticode.mobile", handedOut = false, locked = false))
        assertFalse(OpenTypes.readableFrom("file", own, "dev.raticode.mobile", handedOut = true, locked = false))
        // Another app's file is the user's to show, as before, whether or not Raticode is locked.
        assertTrue(OpenTypes.readableFrom("content", "com.android.providers.downloads.documents", "dev.raticode.mobile", locked = true))
    }

    @Test fun releasePolicyListsBothNewExportedScreens() {
        val policy = listOf("scripts/release-policy.json", "../scripts/release-policy.json").map(::File).first { it.exists() }.readText()
        assertTrue(policy.contains("\"dev.raticode.mobile.open.OpenFileActivity\": null"))
        assertTrue(policy.contains("\"dev.raticode.mobile.open.OpenLinkActivity\": null"))
    }

    @Test fun theNamedTypeChoosesTheViewerAndTheNameHelpsOnlyWhereTheTypeSaysLittle() {
        assertEquals(FileViewer.HTML, OpenTypes.viewer("text/html; charset=utf-8", "page.html"))
        assertEquals(FileViewer.HTML, OpenTypes.viewer("application/xhtml+xml", null))
        assertEquals(FileViewer.PDF, OpenTypes.viewer("application/pdf", "report"))
        assertEquals(FileViewer.IMAGE, OpenTypes.viewer("IMAGE/PNG", "a.png"))
        assertEquals(FileViewer.SVG, OpenTypes.viewer("image/svg+xml", "logo.svg"))
        assertEquals(FileViewer.VIDEO, OpenTypes.viewer("video/mp4", "clip.mp4"))
        assertEquals(FileViewer.AUDIO, OpenTypes.viewer("audio/mpeg", "song.mp3"))
        // A Markdown file sent as plain text shows as Markdown.
        assertEquals(FileViewer.MARKDOWN, OpenTypes.viewer("text/plain", "README.md"))
        assertEquals(FileViewer.TEXT, OpenTypes.viewer("text/plain", "notes.txt"))
        // An HTML file sent as plain text stays text: the type decides.
        assertEquals(FileViewer.TEXT, OpenTypes.viewer("text/plain", "page.html"))
        assertEquals(FileViewer.TEXT, OpenTypes.viewer("text/x-rust", "main.rs"))
        assertEquals(FileViewer.PDF, OpenTypes.viewer("application/octet-stream", "scan.pdf"))
        assertEquals(FileViewer.PDF, OpenTypes.viewer(null, "scan.pdf"))
        assertNull(OpenTypes.viewer("application/octet-stream", "archive.zip"))
        assertNull(OpenTypes.viewer("application/zip", "archive.zip"))
        assertNull(OpenTypes.viewer(null, null))
        // Every listed type has a real viewer.
        for (kind in OpenTypes.kinds) for (type in kind.mimeTypes) assertEquals(type, kind.viewer, OpenTypes.viewer(type, null))
    }

    @Test fun onlyHttpAndHttpsLinksWithAHostAreTaken() {
        assertEquals("https://example.com/a?b=1#c", OpenTypes.webLink("https://example.com/a?b=1#c"))
        assertEquals("http://example.com", OpenTypes.webLink(" http://example.com "))
        assertEquals("HTTPS://Example.com", OpenTypes.webLink("HTTPS://Example.com"))
        for (bad in listOf(null, "", "javascript:alert(1)", "file:///sdcard/a.html", "content://x/y", "intent://x#Intent;end", "https://",
            "https:///path", "https://:443/", "https://exa mple.com", "https://example.com/\nmore", "ftp://example.com", "https://" + "a".repeat(8200))) {
            assertNull(bad, OpenTypes.webLink(bad))
        }
    }

    @Test fun linksFromOtherAppsOpenInChromiumUnlessGeckoIsChosenAndTheChoiceIsSaved() {
        assertEquals(BrowserRules.ENGINE_RATICODE, OpenTypes.linkEngine(null))
        assertEquals(BrowserRules.ENGINE_RATICODE, OpenTypes.linkEngine(dev.raticode.mobile.browser.Firefox.ENGINE))
        assertEquals(BrowserRules.ENGINE_GECKO, OpenTypes.linkEngine(BrowserRules.ENGINE_GECKO))
        assertEquals(BrowserRules.ENGINE_RATICODE, BrowserRules.Saved().externalEngine)
        val saved = BrowserRules.Saved(externalEngine = BrowserRules.ENGINE_GECKO)
        assertEquals(BrowserRules.ENGINE_GECKO, BrowserRules.decode(BrowserRules.encode(saved)).externalEngine)
        assertEquals(BrowserRules.ENGINE_RATICODE, BrowserRules.decode("""{"v":1,"external_engine":"chrome"}""").externalEngine)
        // Saves from before this setting read as Raticode (chromium).
        assertEquals(BrowserRules.ENGINE_RATICODE, BrowserRules.decode("""{"v":1,"engine":"gecko"}""").externalEngine)
    }

    @Test fun theDefaultBrowserButtonAsksAndroidOrOpensItsSettings() {
        assertEquals(BrowserRoleStep.HELD, BrowserRoleStep.of(available = true, held = true))
        assertEquals(BrowserRoleStep.ASK, BrowserRoleStep.of(available = true, held = false))
        assertEquals(BrowserRoleStep.SETTINGS, BrowserRoleStep.of(available = false, held = false))
    }

    @Test fun aFileIsReadUpToTheLimitPlusOneByteSoTooLargeIsNoticed() {
        val small = ByteArray(10) { it.toByte() }
        assertArrayEquals(small, readAtMost(small.inputStream(), 10))
        assertTrue(readAtMost(ByteArray(200_000).inputStream(), 10).size > 10)
        assertTrue(readAtMost(ByteArray(200_000).inputStream(), 100_000).size <= 100_000 + 64 * 1024)
    }
}
