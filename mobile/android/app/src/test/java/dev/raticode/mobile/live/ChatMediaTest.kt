package dev.raticode.mobile.live

import dev.raticode.mobile.ui.tileNote
import org.junit.Assert.*
import org.junit.Test

/** Which files Rem sends preview inline, and what their tiles say before the preview is ready. */
class ChatMediaTest {
    @Test fun picturesAndVideosAreKnownByName() {
        for (name in listOf("chart.png", "Photo.JPG", "a.jpeg", "anim.gif", "x.webp", "scan.heic"))
            assertEquals(name, MediaKind.IMAGE, ChatMedia.kind(name))
        for (name in listOf("demo.mp4", "clip.MOV", "a.webm", "phone.3gp", "b.mkv", "c.m4v"))
            assertEquals(name, MediaKind.VIDEO, ChatMedia.kind(name))
        // Other files, and names without a usable extension, keep their plain file row.
        for (name in listOf("notes.txt", "report.html", "song.mp3", "png", "archive.png.zip", "Makefile", "x.péng"))
            assertNull(name, ChatMedia.kind(name))
    }

    @Test fun durationsReadLikeAPlayer() {
        assertEquals("0:00", ChatMedia.duration(0))
        assertEquals("0:03", ChatMedia.duration(2_600))
        assertEquals("1:05", ChatMedia.duration(65_000))
        assertEquals("1:02:03", ChatMedia.duration(3_723_000))
        assertEquals("0:00", ChatMedia.duration(-5))
    }

    @Test fun tilesSayWhatIsHappening() {
        assertEquals("Downloading 45%", tileNote(MediaKind.VIDEO, "receiving", 45, true, null))
        assertEquals("Downloading", tileNote(MediaKind.IMAGE, "offered", 0, true, null))
        assertEquals("Connect to your desktop to load this video", tileNote(MediaKind.VIDEO, "offered", 0, false, null))
        assertEquals("Loading picture", tileNote(MediaKind.IMAGE, "available", 100, true, MediaPreview.Loading))
        assertEquals("This phone can't preview this video. Open it in another app instead.",
            tileNote(MediaKind.VIDEO, "available", 100, true, MediaPreview.Failed))
        for (state in listOf(null, "failed", "cancelled"))
            assertEquals("This picture is no longer available. Ask Rem to send it again.", tileNote(MediaKind.IMAGE, state, 0, true, null))
    }
}
