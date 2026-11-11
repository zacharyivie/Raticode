package dev.raticode.mobile.live

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.media.MediaDataSource
import android.media.MediaMetadataRetriever
import android.os.Build
import androidx.core.graphics.scale
import dev.raticode.mobile.data.OpenedFiles

/** What a file Rem sent previews as on this phone. */
enum class MediaKind { IMAGE, VIDEO }

/** The inline preview of a picture or video Rem sent. */
sealed interface MediaPreview {
    data object Loading : MediaPreview
    /** [poster] is the picture, or the video's first frame (null when it has none); [durationMs] is 0 for pictures. */
    data class Ready(val poster: Bitmap?, val durationMs: Long = 0) : MediaPreview
    data object Failed : MediaPreview
}

/**
 * Pictures and videos Rem sends arrive through the normal file offer, so they stay inside end-to-end TLS and
 * the desktop's reply-file checks. The phone makes previews from the verified download in memory; nothing is
 * written out in plaintext to show or play them.
 */
object ChatMedia {
    const val CACHE = 24
    const val THUMB_SIDE = 720
    const val FULL_SIDE = 2048
    private val IMAGES = setOf("jpg", "jpeg", "png", "gif", "webp", "bmp", "heic", "heif")
    private val VIDEOS = setOf("mp4", "m4v", "webm", "3gp", "mkv", "mov")

    /** Decided from the file name; the desktop offers every file as application/octet-stream. */
    fun kind(name: String): MediaKind? = when (OpenedFiles.extension(name)) {
        in IMAGES -> MediaKind.IMAGE
        in VIDEOS -> MediaKind.VIDEO
        else -> null
    }

    /** Decodes at most [side] pixels on the longer side so one picture cannot exhaust memory. */
    fun decodeImage(data: ByteArray, side: Int): Bitmap {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(data, 0, data.size, bounds)
        require(bounds.outWidth in 1..20000 && bounds.outHeight in 1..20000)
        var sample = 1
        while (maxOf(bounds.outWidth, bounds.outHeight) / sample > side) sample *= 2
        return requireNotNull(BitmapFactory.decodeByteArray(data, 0, data.size, BitmapFactory.Options().apply { inSampleSize = sample }))
    }

    /** The video's first frame, fitted inside [side], and its length. Fails when the phone can't read the video. */
    fun videoPoster(data: ByteArray, side: Int): MediaPreview.Ready {
        val retriever = MediaMetadataRetriever()
        try {
            retriever.setDataSource(BytesSource(data))
            val duration = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull() ?: 0
            val hasVideo = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_HAS_VIDEO) == "yes"
            require(hasVideo || duration > 0)
            val frame = if (!hasVideo) null else if (Build.VERSION.SDK_INT >= 27)
                retriever.getScaledFrameAtTime(0, MediaMetadataRetriever.OPTION_CLOSEST_SYNC, side, side)
            else retriever.getFrameAtTime(0)?.let { fit(it, side) }
            return MediaPreview.Ready(frame, duration)
        } finally { retriever.release() }
    }

    fun preview(kind: MediaKind, data: ByteArray): MediaPreview.Ready = when (kind) {
        MediaKind.IMAGE -> MediaPreview.Ready(decodeImage(data, THUMB_SIDE))
        MediaKind.VIDEO -> videoPoster(data, THUMB_SIDE)
    }

    private fun fit(frame: Bitmap, side: Int): Bitmap {
        val scale = side.toFloat() / maxOf(frame.width, frame.height)
        if (scale >= 1f) return frame
        return frame.scale((frame.width * scale).toInt().coerceAtLeast(1), (frame.height * scale).toInt().coerceAtLeast(1))
            .also { if (it !== frame) frame.recycle() }
    }

    /** 1:05 or 1:02:03, as the player and the preview badge show it. */
    fun duration(ms: Long): String {
        val total = (ms.coerceAtLeast(0) + 500) / 1000
        val h = total / 3600; val m = total % 3600 / 60; val s = total % 60
        return if (h > 0) "$h:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}" else "$m:${s.toString().padStart(2, '0')}"
    }
}

/** Plays or reads verified bytes from memory, so a video never needs a plaintext copy on disk. */
class BytesSource(private val data: ByteArray) : MediaDataSource() {
    override fun readAt(position: Long, buffer: ByteArray, offset: Int, size: Int): Int {
        if (position < 0 || position >= data.size) return -1
        val count = minOf(size.toLong(), data.size - position).toInt()
        System.arraycopy(data, position.toInt(), buffer, offset, count)
        return count
    }
    override fun getSize(): Long = data.size.toLong()
    override fun close() {}
}
