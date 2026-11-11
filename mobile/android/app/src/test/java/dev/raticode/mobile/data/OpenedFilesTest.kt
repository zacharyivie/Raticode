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
}
