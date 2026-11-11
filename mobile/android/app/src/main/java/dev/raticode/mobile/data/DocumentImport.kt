package dev.raticode.mobile.data

import android.content.ContentResolver
import android.net.Uri
import android.os.CancellationSignal
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.provider.OpenableColumns
import java.io.ByteArrayOutputStream
import java.util.concurrent.atomic.AtomicBoolean

/** Reads untrusted providers without granting their worker access to the vault. */
class DocumentImport(private val resolver: ContentResolver) {
    class Prepared(val name: String, val bytes: ByteArray) : AutoCloseable {
        override fun close() { bytes.fill(0) }
    }
    /** A refusal worded by this app, safe to show. Provider exceptions carry the provider's own text and are never shown. */
    class Problem(message: String) : IllegalArgumentException(message)
    private val main = Handler(Looper.getMainLooper())
    private var active: Operation? = null
    private class Operation(val deadline: Long, val signal: CancellationSignal = CancellationSignal()) {
        val cancelled = AtomicBoolean(false)
        fun check() {
            check(!cancelled.get() && SystemClock.elapsedRealtime() < deadline) { "Import cancelled or timed out." }
        }
    }
    /** [name], when given, replaces the provider's display name; it is made safe the same way. */
    fun start(uri: Uri, timeoutMillis: Long = 30_000, name: String? = null, complete: (Result<Prepared>) -> Unit): Boolean {
        check(Looper.myLooper() == Looper.getMainLooper())
        if (active != null || cancellationOccupied.get() || !workerOccupied.compareAndSet(false, true)) return false
        val operation = Operation(SystemClock.elapsedRealtime() + timeoutMillis)
        active = operation
        val timeout = Runnable {
            if (active === operation) {
                cancel()
                complete(Result.failure(Problem("Import timed out. Choose another file.")))
            }
        }
        main.postDelayed(timeout, timeoutMillis)
        Thread({
            val result = runCatching { read(uri, operation, name) }
            main.post {
                main.removeCallbacks(timeout)
                workerOccupied.set(false)
                if (active === operation) {
                    active = null
                    if (SystemClock.elapsedRealtime() >= operation.deadline) {
                        result.getOrNull()?.close()
                        complete(Result.failure(Problem("Import timed out. Choose another file.")))
                    } else complete(result)
                } else result.getOrNull()?.close()
            }
        }, "document-import").apply { isDaemon = true; start() }
        return true
    }
    fun cancel() {
        check(Looper.myLooper() == Looper.getMainLooper())
        val operation = active ?: return
        active = null
        operation.cancelled.set(true)
        // A provider's cancellation callback can itself block. Never run it on Main.
        // The occupied slot prevents accumulating stalled workers or cancellation threads.
        cancellationOccupied.set(true)
        Thread({ try { operation.signal.cancel() } finally { cancellationOccupied.set(false) } }, "document-import-cancel").apply { isDaemon = true; start() }
    }
    private fun read(uri: Uri, operation: Operation, chosen: String?): Prepared {
        operation.check()
        var name = "attachment"
        resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null, operation.signal)?.use { cursor ->
            operation.check()
            if (cursor.moveToFirst()) {
                name = cursor.getString(0) ?: name
                if (!cursor.isNull(1) && cursor.getLong(1) > FileMetadata.MAX_FILE) throw Problem("Choose a file smaller than 10 MiB.")
            }
        }
        operation.check()
        val output = object : ByteArrayOutputStream() {
            fun wipe() { buf.fill(0); reset() }
        }
        val buffer = ByteArray(8192)
        try {
            (resolver.openAssetFileDescriptor(uri, "r", operation.signal) ?: throw Problem("Cannot open this file.")).use { descriptor ->
                operation.check()
                descriptor.createInputStream().use { input ->
                    while (true) {
                        operation.check()
                        val count = input.read(buffer)
                        operation.check()
                        if (count < 0) break
                        if (output.size().toLong() + count > FileMetadata.MAX_FILE) throw Problem("Choose a file smaller than 10 MiB.")
                        output.write(buffer, 0, count)
                    }
                }
            }
            operation.check()
            return Prepared(FileMetadata.safeName(chosen ?: name), output.toByteArray())
        } finally { buffer.fill(0); output.wipe() }
    }
    companion object {
        // Process-wide bound survives activity/view-model recreation and reset.
        private val workerOccupied = AtomicBoolean(false)
        private val cancellationOccupied = AtomicBoolean(false)
    }
}
