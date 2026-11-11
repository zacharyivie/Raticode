package dev.raticode.mobile.ui

import org.junit.Assert.*
import org.junit.Test

class UploadMetaTest {
    @Test fun aTileSaysHowFarItsUploadHasGotOrThatItStopped() {
        assertEquals("Uploading · 0%", uploadMeta(0f, failed = false, offline = false))
        assertEquals("Uploading · 45%", uploadMeta(.4567f, failed = false, offline = false))
        assertEquals("Not connected · 45%", uploadMeta(.45f, failed = false, offline = true))
        assertEquals("Didn't upload", uploadMeta(.45f, failed = true, offline = false))
        // Not uploading, or not known yet: the tile shows its type and size instead.
        assertNull(uploadMeta(null, failed = false, offline = false))
        // 99.9% isn't shown as done until the desktop confirms the last byte.
        assertEquals("99%", percent(.999f))
        assertEquals("100%", percent(1f))
        assertEquals("0%", percent(-1f))
    }
}
