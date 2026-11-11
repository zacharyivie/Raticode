package dev.raticode.mobile.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Tapping the name of a file Rem sent: open it, fetch it first, or wait for the download already running. */
class SentFileTapTest {
    @Test fun eachDownloadStateHasOneAction() {
        assertEquals(SentFileTap.OPEN, SentFileTap.of("available"))
        // Offline too: the request waits in the outbox until the desktop reconnects.
        assertEquals(SentFileTap.DOWNLOAD, SentFileTap.of("offered"))
        assertEquals(SentFileTap.WAIT, SentFileTap.of("receiving"))
        // A file that expired, failed or was cancelled: the name is not a button.
        for (state in listOf(null, "failed", "cancelled", "expired")) assertNull(SentFileTap.of(state))
    }
}
