package dev.raticode.mobile

import android.content.ActivityNotFoundException
import android.content.ComponentName
import android.content.ContextWrapper
import android.content.Intent
import androidx.core.content.FileProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.data.OpenedFiles
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/** Viewers are stood in for by a context wrapper, so no other app is launched. */
@RunWith(AndroidJUnit4::class)
class OpenedFilesProviderTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @After fun clean() = OpenedFiles.cleanup(context.cacheDir, keepMillis = 0)

    @Test fun providerIsPrivateAndGrantsOnlyPerIntent() {
        val info = context.packageManager.getProviderInfo(ComponentName(context, FileProvider::class.java), 0)
        assertFalse(info.exported)
        assertTrue(info.grantUriPermissions)
        assertEquals(context.packageName + OpenedFiles.AUTHORITY_SUFFIX, info.authority)
    }

    @Test fun viewSendsAReadOnlyGrantForTheCopyWithItsType() {
        val file = OpenedFiles.write(context.cacheDir, "report.pdf", "%PDF-1.7".toByteArray())
        var sent: Intent? = null
        val viewer = object : ContextWrapper(context) { override fun startActivity(intent: Intent) { sent = intent } }
        assertTrue(OpenedFiles.view(viewer, file, "report.pdf"))
        val intent = sent!!
        assertEquals(Intent.ACTION_VIEW, intent.action)
        assertEquals("application/pdf", intent.type)
        assertEquals(context.packageName + OpenedFiles.AUTHORITY_SUFFIX, intent.data!!.authority)
        assertTrue(intent.flags and Intent.FLAG_GRANT_READ_URI_PERMISSION != 0)
        assertTrue(intent.flags and Intent.FLAG_GRANT_WRITE_URI_PERMISSION == 0)
        context.contentResolver.openInputStream(intent.data!!)!!.use { assertArrayEquals("%PDF-1.7".toByteArray(), it.readBytes()) }
    }

    @Test fun missingViewerIsReportedInsteadOfCrashing() {
        val file = OpenedFiles.write(context.cacheDir, "data.unknownkind", byteArrayOf(1))
        val nobody = object : ContextWrapper(context) { override fun startActivity(intent: Intent) { throw ActivityNotFoundException() } }
        assertFalse(OpenedFiles.view(nobody, file, "data.unknownkind"))
        assertEquals("application/octet-stream", OpenedFiles.mimeType("data.unknownkind"))
    }

    @Test fun textWithoutItsOwnViewerOpensAsPlainText() {
        val file = OpenedFiles.write(context.cacheDir, "notes.md", "# Notes".toByteArray())
        val sent = mutableListOf<Intent>()
        val plainOnly = object : ContextWrapper(context) {
            override fun startActivity(intent: Intent) { sent += intent; if (intent.type != "text/plain") throw ActivityNotFoundException() }
        }
        assertTrue(OpenedFiles.view(plainOnly, file, "notes.md"))
        assertEquals(listOf(OpenedFiles.mimeType("notes.md"), "text/plain"), sent.map { it.type })
        assertTrue(sent.all { it.flags and Intent.FLAG_GRANT_READ_URI_PERMISSION != 0 && it.flags and Intent.FLAG_GRANT_WRITE_URI_PERMISSION == 0 })
        // Other kinds get no stand-in type.
        assertEquals(listOf("application/pdf"), OpenedFiles.viewTypes("report.pdf"))
        assertEquals(listOf("text/plain"), OpenedFiles.viewTypes("notes.txt"))
    }

    @Test fun filesOutsideTheOpenedFolderCannotBeShared() {
        val outside = java.io.File(context.cacheDir, "not-opened.txt").apply { writeText("x") }
        try {
            val result = runCatching { FileProvider.getUriForFile(context, context.packageName + OpenedFiles.AUTHORITY_SUFFIX, outside) }
            assertTrue(result.isFailure)
        } finally { outside.delete() }
    }
}
