package dev.raticode.mobile.live

import dev.raticode.mobile.ui.missingNote
import org.junit.Assert.*
import org.junit.Test

/** Attachment tiles: which type each file shows as, how tapping previews it, and when the phone has a copy. */
class FileKindsTest {
    @Test fun filesAreGroupedLikeTheDesktop() {
        assertEquals(FileKind.IMAGE, FileKinds.kind("Screen Shot.PNG"))
        assertEquals(FileKind.VIDEO, FileKinds.kind("clip.mov"))
        assertEquals(FileKind.PDF, FileKinds.kind("report.pdf"))
        assertEquals(FileKind.SHEET, FileKinds.kind("data.xlsx"))
        assertEquals(FileKind.CODE, FileKinds.kind("app.tsx"))
        assertEquals(FileKind.ARCHIVE, FileKinds.kind("backup.tar.gz"))
        assertEquals(FileKind.DOCUMENT, FileKinds.kind("deck.pptx"))
        assertEquals(FileKind.FILE, FileKinds.kind("Makefile"))
        assertEquals("PNG", FileKinds.label("Screen Shot.PNG"))
        assertEquals("GZ", FileKinds.label("backup.tar.gz"))
        assertEquals("FILE", FileKinds.label("Makefile"))
        assertEquals("FILE", FileKinds.label("archive.verylongext"))
        assertEquals("FILE", FileKinds.label(".env"))
    }

    @Test fun tappingPreviewsPicturesVideosAndTextAndOpensTheRest() {
        assertEquals(PreviewMode.IMAGE, FileKinds.previewMode("cat.jpg"))
        assertEquals(PreviewMode.VIDEO, FileKinds.previewMode("demo.mp4"))
        for (name in listOf("notes.md", "rows.csv", "config.yaml", "main.kt", "log.txt"))
            assertEquals(name, PreviewMode.TEXT, FileKinds.previewMode(name))
        // Formats the phone can't draw itself open in another app.
        for (name in listOf("report.pdf", "rows.xlsx", "song.mp3", "archive.zip", "icon.svg", "Makefile"))
            assertEquals(name, PreviewMode.OTHER, FileKinds.previewMode(name))
    }

    @Test fun textPreviewsStopAtTheLimitAndRefuseBinary() {
        assertEquals(TextPreview("hello", false), FileKinds.previewText("hello".toByteArray()))
        assertEquals(TextPreview("abc", true), FileKinds.previewText("abcdef".toByteArray(), 3))
        assertNull(FileKinds.previewText(byteArrayOf(104, 0, 105)))
        // A character cut at the limit is replaced rather than failing.
        assertEquals("a�", FileKinds.previewText("aé".toByteArray(), 2)!!.text)
    }

    @Test fun thePhoneHoldsFinishedDownloadsAndItsOwnUploads() {
        assertTrue(FileTransfers.holds("download", "available"))
        for (state in listOf("offered", "receiving", "failed", "cancelled")) assertFalse(state, FileTransfers.holds("download", state))
        for (state in listOf("offered", "receiving", "available")) assertTrue(state, FileTransfers.holds("upload", state))
        for (state in listOf("failed", "cancelled")) assertFalse(state, FileTransfers.holds("upload", state))
    }

    @Test fun filesThePhoneDoesNotHaveSayWhy() {
        assertTrue(missingNote("cat.png", null).contains("Files attached on your computer stay there"))
        assertTrue(missingNote("cat.png", "failed").contains("didn't finish sending"))
    }
}
