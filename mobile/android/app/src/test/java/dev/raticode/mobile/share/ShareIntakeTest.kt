package dev.raticode.mobile.share

import dev.raticode.mobile.data.FileMetadata
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ShareIntakeTest {
    private fun content(type: String?) = ShareIntake.Stream("content", own = false, type = type)

    @Test fun browserShareKeepsTitleThenLink() {
        val plan = ShareIntake.plan("Bread recipe", "https://example.com/bread", emptyList())
        assertEquals("Bread recipe\n\nhttps://example.com/bread", plan.text)
        assertFalse(plan.shortened)
        assertTrue(plan.refused.isEmpty())
    }

    @Test fun subjectAlreadyInTextIsNotRepeated() {
        assertEquals("Read Bread recipe at https://example.com", ShareIntake.text("Bread recipe", "Read Bread recipe at https://example.com").first)
        assertEquals("Only a subject", ShareIntake.text("Only a subject", null).first)
    }

    @Test fun textIsCleanedAndCappedAtRemLimit() {
        val (spoof, _) = ShareIntake.text(null, "pay\u202Eexe.txt\u0007\r\nline two")
        assertEquals("payexe.txt\nline two", spoof)
        val (long, shortened) = ShareIntake.text(null, "a".repeat(ShareIntake.MAX_TEXT + 50))
        assertEquals(ShareIntake.MAX_TEXT, long.length)
        assertTrue(shortened)
    }

    @Test fun onlySupportedContentFilesAreOffered() {
        val plan = ShareIntake.plan(null, null, listOf(
            content("image/jpeg"),
            content("application/zip"),
            ShareIntake.Stream("file", own = false, type = "image/png"),
            ShareIntake.Stream("content", own = true, type = "image/png"),
            content("application/pdf"),
            content("video/mp4"),
            content("text/plain; charset=utf-8"),
            content(null),
            ShareIntake.Stream("content", own = false, type = null, unreadable = true),
        ))
        assertEquals(listOf(0, 4, 5, 6), plan.accepted)
        assertEquals(4, plan.refused.size)
        assertTrue(plan.refused.any { it.startsWith("2 items aren't types") })
        assertTrue(plan.refused.any { it.contains("Android's file system") })
        assertTrue(plan.refused.any { it.contains("Raticode itself") })
        assertTrue(plan.refused.any { it.contains("didn't answer") })
    }

    @Test fun ownFilesAreRefusedEvenWhenTheTypeLooksFine() {
        val plan = ShareIntake.plan(null, null, listOf(ShareIntake.Stream("content", own = true, type = "application/pdf")))
        assertTrue(plan.accepted.isEmpty())
        assertEquals(listOf("One item came from Raticode itself and was left out."), plan.refused)
    }

    @Test fun ownProvidersAreRecognizedWhateverTheyAreCalled() {
        val pkg = "dev.raticode.mobile"
        val none: (String) -> String? = { null }
        assertTrue(ShareIntake.ownAuthority("dev.raticode.mobile.opened", pkg, none))
        assertTrue(ShareIntake.ownAuthority("0@dev.raticode.mobile.opened", pkg, none))
        assertTrue(ShareIntake.ownAuthority("a@b@dev.raticode.mobile", pkg, none))
        assertTrue(ShareIntake.ownAuthority("renamed.files", pkg) { if (it == "renamed.files") pkg else null })
        assertTrue(ShareIntake.ownAuthority("unknown.files", pkg) { throw SecurityException() })
        assertFalse(ShareIntake.ownAuthority("com.google.android.apps.photos.contentprovider", pkg, none))
        assertFalse(ShareIntake.ownAuthority("dev.raticode.mobilex.files", pkg, none))
        assertFalse(ShareIntake.ownAuthority(null, pkg, none))
    }

    @Test fun moreThanEightFilesAreTrimmedWithAReason() {
        val plan = ShareIntake.plan(null, null, List(11) { content("image/png") })
        assertEquals((0 until ShareIntake.MAX_FILES).toList(), plan.accepted)
        assertEquals(listOf("Rem takes 8 files at a time, so 3 more were left out."), plan.refused)
    }

    @Test fun wildcardTypesFromTheSenderStillMatchTheirFamily() {
        assertTrue(ShareIntake.supported("image/*"))
        assertFalse(ShareIntake.supported("*/*"))
        assertFalse(ShareIntake.supported("application/vnd.android.package-archive"))
        assertFalse(ShareIntake.supported(""))
    }

    @Test fun namesAreSafeAndCarryAnExtensionForTheDesktop() {
        assertEquals("IMG_1.jpg", ShareIntake.name("IMG_1", "jpg", 0))
        assertEquals("photo.png", ShareIntake.name("photo.png", "jpg", 0))
        assertEquals("shared-3.pdf", ShareIntake.name(null, "pdf", 2))
        assertEquals("passwd", ShareIntake.name("../../etc/passwd", null, 0))
        assertEquals("evil.sh", ShareIntake.name("x/evil.sh", "j/../pg", 0))
        assertEquals("shared-1", ShareIntake.name("..", null, 0))
    }

    @Test fun sizeLimitsMatchTheUploadPath() {
        assertNull(ShareIntake.sizeProblem(null))
        assertNull(ShareIntake.sizeProblem(FileMetadata.MAX_FILE))
        assertTrue(ShareIntake.sizeProblem(FileMetadata.MAX_FILE + 1)!!.contains("10 MB"))
        assertNull(ShareIntake.totalProblem(List(4) { FileMetadata.MAX_FILE }))
        assertTrue(ShareIntake.totalProblem(List(5) { FileMetadata.MAX_FILE }) != null)
    }
}
