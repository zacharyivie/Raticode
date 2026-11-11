package dev.raticode.mobile.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.time.LocalDateTime

/** The paperclip keeps a message to eight files and tidies up photos taken for it. */
class AttachSourcesTest {
    @get:Rule val temporary = TemporaryFolder()

    @Test fun roomCountsChipsAndFilesStillBeingRead() {
        assertEquals(8, AttachPlan.room(0, 0))
        assertEquals(3, AttachPlan.room(4, 1))
        assertEquals(0, AttachPlan.room(8, 0))
        assertEquals(0, AttachPlan.room(7, 3))
    }

    @Test fun picksBeyondTheRoomAreDroppedWithANote() {
        assertEquals(listOf(1, 2) to null, AttachPlan.fit(listOf(1, 2), 8))
        val (kept, note) = AttachPlan.fit(listOf(1, 2, 3, 4), 3)
        assertEquals(listOf(1, 2, 3), kept)
        assertEquals("Only 3 more files fit in this message, so 1 wasn't added.", note)
        assertEquals("Only 1 more file fits in this message, so 2 weren't added.", AttachPlan.fit(listOf(1, 2, 3), 1).second)
        val (none, full) = AttachPlan.fit(listOf(1), 0)
        assertTrue(none.isEmpty())
        assertEquals("A message carries up to 8 files. Send this one, then attach more.", full)
        assertEquals(emptyList<Int>() to null, AttachPlan.fit(emptyList<Int>(), 0))
    }

    @Test fun theMenuSaysHowManyMoreFit() {
        assertEquals("Up to 8 files, 10 MB each", AttachPlan.limit(8))
        assertEquals("Up to 3 more files, 10 MB each", AttachPlan.limit(3))
        assertEquals("Up to 1 more file, 10 MB each", AttachPlan.limit(1))
    }

    @Test fun photosAreNamedByWhenTheyWereTaken() {
        assertEquals("Photo 2026-11-03 17.05.09.jpg", CameraCaptures.name(LocalDateTime.of(2026, 11, 3, 17, 5, 9)))
        assertEquals(CameraCaptures.name(LocalDateTime.of(2026, 1, 2, 3, 4, 5)), FileMetadata.safeName(CameraCaptures.name(LocalDateTime.of(2026, 1, 2, 3, 4, 5))))
    }

    @Test fun photosLiveOnlyInTheCameraFolderAndOldOnesAreRemoved() {
        val cache = temporary.root
        val now = System.currentTimeMillis()
        val old = CameraCaptures.create(cache, now)
        old.setLastModified(now - CameraCaptures.KEEP_MILLIS - 1000)
        val fresh = CameraCaptures.create(cache, now)
        assertEquals(File(cache, "camera").canonicalFile, fresh.parentFile!!.canonicalFile)
        assertEquals(0L, fresh.length())
        assertTrue(CameraCaptures.owns(cache, fresh))
        // Making the next photo drops one the camera never returned.
        assertFalse(old.exists())
        assertFalse(CameraCaptures.owns(cache, File(cache, "opened/x.jpg")))
        assertFalse(CameraCaptures.owns(cache, File(File(cache, "camera"), "../secret.jpg")))
        assertFalse(CameraCaptures.owns(cache, File(File(cache, "camera"), "notes.txt")))
        CameraCaptures.cleanup(cache, keepMillis = 0)
        assertFalse(fresh.exists())
        assertNull(CameraCaptures.directory(cache).listFiles()?.firstOrNull())
    }
}
