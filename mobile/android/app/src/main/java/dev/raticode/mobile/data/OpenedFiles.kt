package dev.raticode.mobile.data

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.webkit.MimeTypeMap
import androidx.core.content.FileProvider
import java.io.File
import java.util.UUID

/**
 * Short-lived plaintext copies of verified files the user chose to open in another app.
 * Copies live only in this app's cache, are shared read-only through a non-exported
 * provider for the one intent, and are deleted after [KEEP_MILLIS] or on reset.
 */
object OpenedFiles {
    const val AUTHORITY_SUFFIX = ".opened"
    const val KEEP_MILLIS = 30 * 60 * 1000L
    private const val DIRECTORY = "opened"
    // Installing apps needs a permission this app deliberately does not request.
    private val installers = setOf("apk", "apks", "apkm", "xapk", "aab")

    fun extension(name: String): String? =
        name.substringAfterLast('.', "").lowercase().takeIf { it.isNotEmpty() && it.length <= 16 && it.all(Char::isLetterOrDigit) }

    fun isInstaller(name: String) = extension(name) in installers

    fun directory(cache: File) = File(cache, DIRECTORY)

    /** Deletes copies older than [keepMillis]. Zero deletes every copy. */
    fun cleanup(cache: File, now: Long = System.currentTimeMillis(), keepMillis: Long = KEEP_MILLIS) {
        directory(cache).listFiles().orEmpty().forEach { entry ->
            if (keepMillis == 0L || now - entry.lastModified() >= keepMillis) entry.deleteRecursively()
        }
    }

    /** Writes [bytes] under a fresh random folder so earlier copies keep their own names. */
    fun write(cache: File, name: String, bytes: ByteArray, now: Long = System.currentTimeMillis()): File {
        cleanup(cache, now)
        val folder = File(directory(cache), UUID.randomUUID().toString())
        check(folder.mkdirs()) { "Could not prepare the file" }
        val file = File(folder, FileMetadata.safeName(name))
        check(file.parentFile == folder)
        file.outputStream().use { it.write(bytes) }
        return file
    }

    fun mimeType(name: String): String =
        extension(name)?.let { MimeTypeMap.getSingleton().getMimeTypeFromExtension(it) } ?: "application/octet-stream"

    /** Types to try in order: text such as Markdown or code falls back to a plain-text viewer. */
    fun viewTypes(name: String): List<String> = mimeType(name).let { if (it.startsWith("text/") && it != "text/plain") listOf(it, "text/plain") else listOf(it) }

    /** Starts the user's viewer for [file]. Returns false when no app on the phone can open it. */
    fun view(context: Context, file: File, name: String): Boolean {
        val uri = FileProvider.getUriForFile(context, context.packageName + AUTHORITY_SUFFIX, file)
        for (type in viewTypes(name)) {
            val intent = Intent(Intent.ACTION_VIEW).setDataAndType(uri, type)
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            if (context !is android.app.Activity) intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            try { context.startActivity(intent); return true } catch (_: ActivityNotFoundException) {}
        }
        return false
    }
}
