package dev.raticode.mobile.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class OpenedFilesTest {
    @get:Rule val temporary = TemporaryFolder()

    @Test fun extensionsAreLowercaseAndBounded() {
        assertEquals("pdf", OpenedFiles.extension("Report.PDF"))
        assertEquals("gz", OpenedFiles.extension("logs.tar.gz"))
        assertNull(OpenedFiles.extension("README"))
        assertNull(OpenedFiles.extension("trailing."))
        assertNull(OpenedFiles.extension("odd.p d f"))
    }

    @Test fun installerPackagesAreRecognized() {
        for (name in listOf("raticode.apk", "BUNDLE.APKS", "app.xapk", "app.apkm", "release.aab")) assertTrue(name, OpenedFiles.isInstaller(name))
        for (name in listOf("notes.txt", "apk", "apk.pdf")) assertFalse(name, OpenedFiles.isInstaller(name))
    }

    @Test fun copiesGoInFreshFoldersUnderOpenedWithSafeNames() {
        val cache = temporary.root
        val first = OpenedFiles.write(cache, "../../escape.txt", byteArrayOf(1, 2, 3))
        val second = OpenedFiles.write(cache, "../../escape.txt", byteArrayOf(4))
        assertEquals("escape.txt", first.name)
        assertEquals(OpenedFiles.directory(cache), first.parentFile!!.parentFile)
        assertNotEquals(first.parentFile, second.parentFile)
        assertEquals(listOf<Byte>(1, 2, 3), first.readBytes().toList())
        assertEquals(listOf<Byte>(4), second.readBytes().toList())
    }

    @Test fun cleanupRemovesOnlyOldCopiesUnlessAskedToRemoveAll() {
        val cache = temporary.root
        val old = OpenedFiles.write(cache, "old.txt", byteArrayOf(1))
        old.parentFile!!.setLastModified(1_000)
        val recent = OpenedFiles.write(cache, "recent.txt", byteArrayOf(2), now = 1_000 + OpenedFiles.KEEP_MILLIS)
        assertFalse(old.exists())
        assertTrue(recent.exists())
        OpenedFiles.cleanup(cache, keepMillis = 0)
        assertEquals(0, OpenedFiles.directory(cache).listFiles()!!.size)
    }

    @Test fun openHandsTheCopyToTheDefaultAppAndRaticodesOwnViewerAcceptsIt() {
        val given = OpenedFiles.HandedOut { 0L }
        val uri = "content://dev.raticode.mobile.opened/opened/3f1c/report.html"
        val tried = mutableListOf<String>()
        assertTrue(OpenedFiles.offer(uri, listOf("text/html"), given) { type -> tried += type; true })
        assertEquals(listOf("text/html"), tried)
        // Raticode is the default app for HTML, so OpenFileActivity gets this address and must show it.
        assertTrue(given.contains(uri))
        assertTrue(dev.raticode.mobile.open.OpenTypes.readableFrom("content", "dev.raticode.mobile.opened", "dev.raticode.mobile",
            handedOut = given.contains(uri)))
        // Any other address of Raticode's own stays refused.
        val other = "content://dev.raticode.mobile.opened/handoff/log.txt"
        assertFalse(dev.raticode.mobile.open.OpenTypes.readableFrom("content", "dev.raticode.mobile.opened", "dev.raticode.mobile",
            handedOut = given.contains(other)))
        assertFalse(given.contains(null))
    }

    @Test fun openTriesEachTypeAndReportsFalseWhenNoAppTakesTheFile() {
        val given = OpenedFiles.HandedOut { 0L }
        val uri = "content://dev.raticode.mobile.opened/opened/9a2b/notes.md"
        val tried = mutableListOf<String>()
        assertFalse(OpenedFiles.offer(uri, listOf("text/markdown", "text/plain"), given) { type -> tried += type; false })
        assertEquals(listOf("text/markdown", "text/plain"), tried)
        assertFalse(given.contains(uri))
        // The plain-text fallback is used when only it has an app.
        assertTrue(OpenedFiles.offer(uri, listOf("text/markdown", "text/plain"), given) { it == "text/plain" })
        assertTrue(given.contains(uri))
    }

    @Test fun handedOutCopiesExpireWithTheCopyAndClearOnReset() {
        var now = 1_000L
        val given = OpenedFiles.HandedOut { now }
        given.add("content://x.opened/opened/a/r.pdf")
        now += OpenedFiles.KEEP_MILLIS - 1
        assertTrue(given.contains("content://x.opened/opened/a/r.pdf"))
        now += 1
        assertFalse(given.contains("content://x.opened/opened/a/r.pdf"))
        given.add("content://x.opened/opened/b/r.pdf")
        given.clear()
        assertFalse(given.contains("content://x.opened/opened/b/r.pdf"))
    }
}
