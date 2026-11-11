package dev.raticode.mobile

import android.net.Uri
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.data.*
import org.junit.Assert.*
import org.junit.Before
import org.junit.After
import org.junit.Test
import java.io.File

class LocalVaultTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val vault = LocalVault(context)
    private fun importFile(uri: Uri, state: Workspace): LocalFile {
        val latch = java.util.concurrent.CountDownLatch(1)
        var result: Result<DocumentImport.Prepared>? = null
        InstrumentationRegistry.getInstrumentation().runOnMainSync {
            check(DocumentImport(context.contentResolver).start(uri) { result = it; latch.countDown() })
        }
        check(latch.await(10, java.util.concurrent.TimeUnit.SECONDS))
        return vault.storeImport(result!!.getOrThrow(), state)
    }
    @Before fun clear() { vault.reset() }
    @After fun cleanup() { vault.reset() }
    @Test fun snapshotIsEncryptedAndSurvivesNewRepositoryInstance() {
        val state = LocalSession.start(Workspace()).let { s -> s.updateThread(s.activeId!!) { it.copy(draft = "private-draft-749") } }
        vault.save(state)
        assertEquals(state, LocalVault(context).load())
        val encrypted = File(context.noBackupFilesDir, "workspace/state.bin").readBytes()
        assertFalse(String(encrypted).contains("private-draft-749"))
        assertFalse(String(encrypted).contains("threads"))
    }
    @Test fun tamperedSnapshotFailsWithoutReplacingIt() {
        vault.save(LocalSession.start(Workspace()))
        val path = File(context.noBackupFilesDir, "workspace/state.bin")
        val changed = path.readBytes().also { it[it.lastIndex] = (it.last().toInt() xor 1).toByte() }
        path.writeBytes(changed)
        assertThrows(Exception::class.java) { vault.load() }
        assertArrayEquals(changed, path.readBytes())
    }
    @Test fun fileImportEncryptsAndExportVerifiesBytes() {
        val source = File(context.cacheDir, "vault-test-source.txt").apply { writeText("private-file-328") }
        val target = File(context.cacheDir, "vault-test-output.txt")
        try {
            val file = importFile(Uri.fromFile(source), Workspace())
            assertEquals(source.length(), file.size)
            val blob = File(context.noBackupFilesDir, "workspace/${file.id}.bin")
            assertFalse(String(blob.readBytes()).contains("private-file-328"))
            vault.exportFile(file, Uri.fromFile(target))
            assertArrayEquals(source.readBytes(), target.readBytes())
            blob.writeBytes(blob.readBytes().also { it[it.lastIndex] = (it.last().toInt() xor 1).toByte() })
            target.delete()
            assertThrows(Exception::class.java) { vault.exportFile(file, Uri.fromFile(target)) }
            assertFalse(target.exists())
        } finally { source.delete(); target.delete() }
    }
    @Test fun oversizedImportIsRejectedWithoutRetainingBlob() {
        val source = File(context.cacheDir, "vault-too-large").apply { java.io.RandomAccessFile(this, "rw").use { it.setLength(LocalVault.MAX_FILE + 1) } }
        try {
            assertThrows(IllegalArgumentException::class.java) { importFile(Uri.fromFile(source), Workspace()) }
            assertEquals(0, File(context.noBackupFilesDir, "workspace").listFiles()!!.size)
        } finally { source.delete() }
    }
    @Test fun missingKeyDoesNotSilentlyReplaceSavedWorkspace() {
        vault.save(LocalSession.start(Workspace()))
        val path = File(context.noBackupFilesDir, "workspace/state.bin")
        val original = path.readBytes()
        java.security.KeyStore.getInstance("AndroidKeyStore").apply { load(null); deleteEntry("raticode.local.v1") }
        assertThrows(Exception::class.java) { vault.load() }
        assertArrayEquals(original, path.readBytes())
    }
    @Test fun resetDeletesMessagesAndAttachments() {
        val state = LocalSession.start(Workspace()); vault.save(state); vault.reset()
        assertEquals(Workspace(), vault.load())
    }
}
