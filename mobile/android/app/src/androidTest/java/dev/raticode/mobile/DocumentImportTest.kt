package dev.raticode.mobile

import android.app.Application
import android.content.ContentProvider
import android.content.ContentResolver
import android.content.ContentValues
import android.database.Cursor
import android.database.MatrixCursor
import android.net.Uri
import android.os.CancellationSignal
import android.os.ParcelFileDescriptor
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.data.DocumentImport
import dev.raticode.mobile.data.LocalVault
import dev.raticode.mobile.ui.RemViewModel
import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/** Disposable in-process provider via Android's real ContentResolver wrapper. */
class DocumentImportTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val uri = Uri.parse("content://test-document/fixture")
    private fun main(block: () -> Unit) = instrumentation.runOnMainSync(block)
    private fun await(condition: () -> Boolean) {
        val until = System.nanoTime() + TimeUnit.SECONDS.toNanos(10)
        while (System.nanoTime() < until) { if (condition()) return; Thread.sleep(20) }
        fail("Timed out waiting for condition")
    }
    class Provider(val queryStalls: Boolean = false, val readStalls: Boolean = false, val oversized: Boolean = false) : ContentProvider() {
        val entered = CountDownLatch(1)
        val release = CountDownLatch(1)
        val cancelled = AtomicBoolean(false)
        val finished = CountDownLatch(1)
        override fun onCreate() = true
        override fun query(uri: Uri, projection: Array<out String>?, selection: String?, args: Array<out String>?, sort: String?): Cursor =
            MatrixCursor(arrayOf("_display_name", "_size")).apply { addRow(arrayOf("test.txt", null)) }
        override fun query(uri: Uri, projection: Array<out String>?, selection: String?, args: Array<out String>?, sort: String?, signal: CancellationSignal?): Cursor {
            signal?.setOnCancelListener { cancelled.set(true) }
            if (queryStalls) { entered.countDown(); release.await(10, TimeUnit.SECONDS); finished.countDown() }
            return query(uri, projection, selection, args, sort)
        }
        override fun openAssetFile(uri: Uri, mode: String, signal: CancellationSignal?) =
            android.content.res.AssetFileDescriptor(openFile(uri, mode, signal), 0, android.content.res.AssetFileDescriptor.UNKNOWN_LENGTH)
        override fun openFile(uri: Uri, mode: String, signal: CancellationSignal?): ParcelFileDescriptor {
            signal?.setOnCancelListener { cancelled.set(true) }
            val pipe = ParcelFileDescriptor.createPipe()
            Thread {
                try {
                    ParcelFileDescriptor.AutoCloseOutputStream(pipe[1]).use { output ->
                        entered.countDown()
                        if (readStalls) release.await(10, TimeUnit.SECONDS)
                        val data = ByteArray(8192) { 42 }
                        repeat(if (oversized) 1281 else 1) { output.write(data) }
                    }
                } catch (_: Exception) { /* reader rejected/closed the fixture */ }
                finally { finished.countDown() }
            }.start()
            return pipe[0]
        }
        override fun getType(uri: Uri) = "application/octet-stream"
        override fun insert(uri: Uri, values: ContentValues?): Uri? = null
        override fun delete(uri: Uri, selection: String?, args: Array<out String>?) = 0
        override fun update(uri: Uri, values: ContentValues?, selection: String?, args: Array<out String>?) = 0
    }
    private fun cancellation(stallQuery: Boolean) {
        val provider = Provider(queryStalls = stallQuery, readStalls = !stallQuery)
        val importer = DocumentImport(ContentResolver.wrap(provider))
        val application = instrumentation.targetContext.applicationContext as Application
        val vault = LocalVault(application)
        vault.reset()
        lateinit var model: RemViewModel
        main { model = RemViewModel(application, importer) }
        await { !model.busy }
        main { model.pair() }
        await { !model.busy }
        main { model.attach(model.state.activeId!!, uri) }
        assertTrue(provider.entered.await(5, TimeUnit.SECONDS))
        main {
            assertTrue(model.importing)
            model.cancelImport()
            assertFalse(model.busy)
            repeat(100) {
                assertFalse(DocumentImport(ContentResolver.wrap(provider)).start(uri) { fail("Stalled worker must retain its slot") })
            }
            model.reset()
        }
        await { !model.busy }
        assertFalse(model.state.demo)
        await { provider.cancelled.get() }
        provider.release.countDown()
        assertTrue(provider.finished.await(5, TimeUnit.SECONDS))
        waitForWorker(importer)
        await { !model.busy }
        assertFalse(vault.load().demo)
        assertTrue(java.io.File(application.noBackupFilesDir, "workspace").listFiles().orEmpty().isEmpty())
        vault.reset()
    }
    @Test fun stalledQueryCanCancelAndResetWithoutLateCommit() = cancellation(true)
    @Test fun stalledReadCanCancelAndResetWithoutLateCommit() = cancellation(false)
    @Test fun deadlineReleasesCallerEvenWhenProviderIgnoresCancellation() {
        val provider = Provider(queryStalls = true)
        val importer = DocumentImport(ContentResolver.wrap(provider))
        val complete = CountDownLatch(1)
        main { assertTrue(importer.start(uri, 100) { assertTrue(it.isFailure); complete.countDown() }) }
        assertTrue(complete.await(5, TimeUnit.SECONDS))
        assertEquals(1, provider.release.count)
        await { provider.cancelled.get() }
        provider.release.countDown()
        assertTrue(provider.finished.await(5, TimeUnit.SECONDS))
        waitForWorker(importer)
    }
    private fun waitForWorker(importer: DocumentImport) {
        val complete = CountDownLatch(1)
        await {
            var started = false
            main { started = importer.start(uri) { assertTrue(it.isSuccess); it.getOrNull()?.close(); complete.countDown() } }
            started
        }
        assertTrue(complete.await(5, TimeUnit.SECONDS))
    }
    @Test fun actualBytesRejectOversizeWithoutTrustingMetadata() {
        val importer = DocumentImport(ContentResolver.wrap(Provider(oversized = true)))
        val complete = CountDownLatch(1)
        main { assertTrue(importer.start(uri) {
            assertTrue(it.exceptionOrNull() is IllegalArgumentException)
            assertEquals("Choose a file smaller than 10 MiB.", it.exceptionOrNull()?.message)
            complete.countDown()
        }) }
        assertTrue(complete.await(5, TimeUnit.SECONDS))
    }
}
