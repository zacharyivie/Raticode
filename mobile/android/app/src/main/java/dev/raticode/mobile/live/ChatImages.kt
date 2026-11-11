package dev.raticode.mobile.live

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import org.json.JSONObject

/** A message image's state on this phone. */
sealed interface ChatImage {
    data object Loading : ChatImage
    data class Ready(val bitmap: Bitmap) : ChatImage
    data object Failed : ChatImage
}

/**
 * Web images in Rem's replies. The phone asks the desktop for them with the `media.image`
 * read, so image addresses stay inside end-to-end TLS and traffic stays on Wi-Fi.
 */
object ChatImages {
    const val KEY = "media.image:"
    const val CACHE = 40
    const val MAX_BYTES = 768 * 1024
    private const val MAX_SIDE = 1600

    /** Only absolute web addresses; the desktop also limits fetches to public hosts. */
    fun fetchable(url: String): Boolean {
        if (url.length > 2048) return false
        val uri = runCatching { java.net.URI(url) }.getOrNull() ?: return false
        return uri.scheme?.lowercase() in setOf("https", "http") && !uri.host.isNullOrBlank() && uri.userInfo == null
    }

    fun bytes(body: JSONObject): ByteArray {
        val data = java.util.Base64.getDecoder().decode(body.getString("data"))
        require(data.isNotEmpty() && data.size <= MAX_BYTES)
        require(body.optString("mime") in setOf("image/jpeg", "image/png", "image/gif", "image/webp"))
        return data
    }

    /** Decodes at most [MAX_SIDE] pixels on the longer side so one image cannot exhaust memory. */
    fun decode(body: JSONObject): Bitmap {
        val data = bytes(body)
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(data, 0, data.size, bounds)
        require(bounds.outWidth in 1..20000 && bounds.outHeight in 1..20000)
        var sample = 1
        while (maxOf(bounds.outWidth, bounds.outHeight) / sample > MAX_SIDE) sample *= 2
        return requireNotNull(BitmapFactory.decodeByteArray(data, 0, data.size, BitmapFactory.Options().apply { inSampleSize = sample }))
    }
}
