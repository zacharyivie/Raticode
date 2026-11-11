package dev.raticode.mobile.data

import java.io.File
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale
import java.util.UUID

/** Where the paperclip can attach from. Sources Android shows in its own file browser say so in the menu. */
enum class AttachSource { MEDIA, CAMERA, RECENT, DOWNLOADS, FILES }

/** How many picked files fit in one Rem message, and what to tell the user about the rest. */
object AttachPlan {
    /** Files one Rem message carries, as on the desktop. */
    const val MAX_FILES = 8

    /** Room left for [attached] chips plus [reading] files still being read. */
    fun room(attached: Int, reading: Int) = (MAX_FILES - attached - reading).coerceIn(0, MAX_FILES)

    /** The first [room] of [picked], and a note when some didn't fit. */
    fun <T> fit(picked: List<T>, room: Int): Pair<List<T>, String?> {
        val kept = picked.take(room.coerceAtLeast(0))
        val left = picked.size - kept.size
        return kept to when {
            left == 0 -> null
            kept.isEmpty() -> "A message carries up to $MAX_FILES files. Send this one, then attach more."
            else -> "Only ${kept.size} more ${if (kept.size == 1) "file fits" else "files fit"} in this message, so $left " +
                "${if (left == 1) "wasn't" else "weren't"} added."
        }
    }

    /** The menu's limit line, such as "Up to 8 files, 10 MB each". */
    fun limit(room: Int) = "Up to $room ${if (room == 1) "more file" else if (room < MAX_FILES) "more files" else "files"}, 10 MB each"

    const val CAMERA_DENIED = "Camera access is off for Raticode. Allow it in Settings, or attach a photo from your gallery."
    const val NO_CAMERA = "No camera app on this phone can take a photo for Raticode. Attach one from your gallery instead."
    const val CAMERA_FAILED = "The camera app didn't return a photo. Try again, or attach one from your gallery."
    const val OWN_FILE = "Raticode's own files can't be attached."
    const val TOO_SLOW = "Reading a file took too long, so it wasn't attached. Try again."
}

/**
 * Photos taken for Rem's chat. The camera app writes into this app's cache through a one-intent grant, the photo is
 * read like any picked file, then deleted. It never reaches the gallery.
 */
object CameraCaptures {
    const val KEEP_MILLIS = 30 * 60 * 1000L
    private const val DIRECTORY = "camera"
    private val stamp = DateTimeFormatter.ofPattern("yyyy-MM-dd HH.mm.ss", Locale.US)

    fun directory(cache: File) = File(cache, DIRECTORY)

    /** Deletes photos older than [keepMillis], left by a camera that never returned. Zero deletes every photo. */
    fun cleanup(cache: File, now: Long = System.currentTimeMillis(), keepMillis: Long = KEEP_MILLIS) {
        directory(cache).listFiles().orEmpty().forEach { entry ->
            if (keepMillis == 0L || now - entry.lastModified() >= keepMillis) entry.deleteRecursively()
        }
    }

    /** An empty file for the camera to fill. */
    fun create(cache: File, now: Long = System.currentTimeMillis()): File {
        cleanup(cache, now)
        val folder = directory(cache)
        check(folder.isDirectory || folder.mkdirs()) { "Could not prepare the photo" }
        return File(folder, "${UUID.randomUUID()}.jpg").also { check(it.createNewFile()) }
    }

    /** True only for a file this object created, so a path from saved state can't delete anything else. */
    fun owns(cache: File, file: File) = file.parentFile?.canonicalFile == directory(cache).canonicalFile && file.name.endsWith(".jpg")

    /** The name the desktop sees, such as "Photo 2026-11-03 17.20.15.jpg". */
    fun name(time: LocalDateTime) = "Photo ${stamp.format(time)}.jpg"
}

/**
 * Text the app hands to Rem's message box as a file, such as log entries from the Developer log. The file lives in the
 * app's cache only until the message box has read it, and Android shares it with nothing else.
 */
object HandoffFiles {
    const val KEEP_MILLIS = 30 * 60 * 1000L
    private const val DIRECTORY = "handoff"

    fun directory(cache: File) = File(cache, DIRECTORY)

    /** Deletes files older than [keepMillis], left when the app closed before Rem's message box read them. */
    fun cleanup(cache: File, now: Long = System.currentTimeMillis(), keepMillis: Long = KEEP_MILLIS) {
        directory(cache).listFiles().orEmpty().forEach { entry ->
            if (keepMillis == 0L || now - entry.lastModified() >= keepMillis) entry.deleteRecursively()
        }
    }

    fun write(cache: File, text: String, now: Long = System.currentTimeMillis()): File {
        cleanup(cache, now)
        val folder = directory(cache)
        check(folder.isDirectory || folder.mkdirs()) { "Could not prepare the file" }
        return File(folder, "${UUID.randomUUID()}.txt").also { it.writeText(text) }
    }

    /** True only for a file this object wrote, so a saved path can't delete anything else. */
    fun owns(cache: File, file: File) = file.parentFile?.canonicalFile == directory(cache).canonicalFile && file.name.endsWith(".txt")
}
