package dev.raticode.mobile.update

import java.io.File
import java.io.FileOutputStream
import java.io.InputStream
import java.security.MessageDigest

/**
 * Downloads the update APK into the app's private storage, picking up where a cancelled or interrupted download
 * stopped, and accepts it only when its size and SHA-256 match what the release promised. Network access is passed in,
 * so tests can drive every failure without a server.
 */
class ApkDownload(private val open: (url: String, from: Long) -> Response) {
    /** One HTTP answer: status, the body, and the HTTPS address it finally came from after redirects. */
    class Response(val status: Int, val body: InputStream, val finalUrl: String, val contentRange: String? = null) : AutoCloseable {
        override fun close() = body.close()
    }

    class Cancelled : Exception("The download was cancelled.")

    /**
     * Fills [target] with the APK at [url]. A `.part` file next to it keeps a partial download for the next try.
     * Returns [target] once it is complete and verified; on a mismatch both files are deleted.
     */
    fun fetch(url: String, target: File, size: Long, sha256: String, cancelled: () -> Boolean, progress: (Long, Long) -> Unit): File {
        if (!url.startsWith("https://")) throw UpdateFailure("Raticode only downloads updates over HTTPS.")
        if (target.isFile && target.length() == size && sha256(target) == sha256) { progress(size, size); return target }
        target.delete()
        val part = File(target.path + ".part")
        if (part.length() > size) part.delete()
        var restarted = false
        while (true) {
            val from = if (part.isFile) part.length() else 0L
            if (from < size) {
                val response = try { open(url, from) } catch (error: java.io.IOException) {
                    throw UpdateFailure("The download stopped: ${error.message ?: "the connection dropped"}. Tap Update Raticode to continue where it stopped.")
                }
                response.use { answer ->
                    if (!answer.finalUrl.startsWith("https://")) throw UpdateFailure("GitHub sent the download somewhere other than HTTPS, so Raticode stopped it.")
                    val append = when {
                        answer.status == 206 && from > 0 && answer.contentRange?.startsWith("bytes $from-") == true -> true
                        answer.status == 200 -> false
                        // The partial file no longer fits what the server has: start over once.
                        answer.status == 416 && !restarted -> { part.delete(); restarted = true; return@use null }
                        else -> throw UpdateFailure("GitHub returned HTTP ${answer.status} while downloading the update. Try again later.")
                    }
                    copy(answer.body, part, append, size, cancelled, progress)
                } ?: continue
            }
            progress(size, size)
            if (part.length() != size) { part.delete(); throw UpdateFailure("The download ended early. Tap Update Raticode to try again.") }
            if (sha256(part) != sha256) {
                part.delete()
                // A resumed download can join two different files; one fresh download settles it.
                if (from > 0 && !restarted) { restarted = true; continue }
                throw UpdateFailure("The downloaded update didn't match the checksum published with the release, so Raticode deleted it. Nothing was installed.")
            }
            if (!part.renameTo(target)) throw UpdateFailure("Raticode couldn't save the downloaded update. Free some storage and try again.")
            return target
        }
    }

    private fun copy(body: InputStream, part: File, append: Boolean, size: Long, cancelled: () -> Boolean, progress: (Long, Long) -> Unit) {
        var done = if (append) part.length() else 0L
        val buffer = ByteArray(64 * 1024)
        try {
            FileOutputStream(part, append).use { out ->
                while (true) {
                    if (cancelled()) throw Cancelled()
                    val read = body.read(buffer)
                    if (read < 0) break
                    done += read
                    if (done > size) { out.close(); part.delete(); throw UpdateFailure("The download is larger than the release says, so Raticode stopped it. Nothing was installed.") }
                    out.write(buffer, 0, read)
                    progress(done, size)
                }
            }
        } catch (error: java.io.IOException) {
            // What arrived stays in the .part file, so the next try continues from there.
            throw UpdateFailure("The download stopped: ${error.message ?: "the connection dropped"}. Tap Update Raticode to continue where it stopped.")
        }
    }

    companion object {
        fun sha256(file: File): String = file.inputStream().use { sha256(it) }
        fun sha256(input: InputStream): String {
            val digest = MessageDigest.getInstance("SHA-256")
            val buffer = ByteArray(64 * 1024)
            while (true) { val read = input.read(buffer); if (read < 0) break; digest.update(buffer, 0, read) }
            return digest.digest().joinToString("") { "%02x".format(it) }
        }
    }
}
