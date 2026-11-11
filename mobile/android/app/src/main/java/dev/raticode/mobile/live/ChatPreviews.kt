package dev.raticode.mobile.live

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import org.json.JSONObject

/** A web page's card details, fetched by the desktop; [thumbnail] is null when the page has none. */
data class SourcePreview(val url: String, val title: String, val description: String, val siteName: String, val kind: String,
                         val thumbnail: Bitmap?)

/** A source card's state on this phone. */
sealed interface ChatPreview {
    data object Loading : ChatPreview
    data class Ready(val preview: SourcePreview) : ChatPreview
    data object Failed : ChatPreview
}

/**
 * Previews for the web pages behind Rem's results. The phone asks the desktop with the `media.preview`
 * read, only when the desktop reports `rich_results`, so page addresses stay inside end-to-end TLS and the
 * phone never contacts the sites.
 */
object ChatPreviews {
    const val KEY = "media.preview:"
    const val CACHE = 60
    private const val MAX_THUMB_BYTES = 210 * 1024
    private const val THUMB_SIDE = 240
    private val DATA_URL = Regex("^data:(image/(?:jpeg|png|gif|webp|avif));base64,([A-Za-z0-9+/=]+)$")

    fun parse(body: JSONObject): SourcePreview = SourcePreview(body.optString("url").take(2048), body.optString("title").take(200),
        body.optString("description").take(300), body.optString("site_name").take(80), body.optString("kind").take(20),
        runCatching { thumbnail(body.optString("image")) }.getOrNull())

    /** A small bitmap from the desktop's data URL, downsampled so a card thumbnail stays cheap. */
    fun thumbnail(dataUrl: String): Bitmap? {
        val match = DATA_URL.matchEntire(dataUrl) ?: return null
        val data = java.util.Base64.getDecoder().decode(match.groupValues[2])
        if (data.isEmpty() || data.size > MAX_THUMB_BYTES) return null
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(data, 0, data.size, bounds)
        if (bounds.outWidth !in 1..20000 || bounds.outHeight !in 1..20000) return null
        var sample = 1
        while (maxOf(bounds.outWidth, bounds.outHeight) / sample > THUMB_SIDE * 2) sample *= 2
        return BitmapFactory.decodeByteArray(data, 0, data.size, BitmapFactory.Options().apply { inSampleSize = sample })
    }
}
