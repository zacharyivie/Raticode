package dev.raticode.mobile.browser

import android.app.Application
import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.core.content.edit
import dev.raticode.mobile.browser.BrowserMediaRules.Action
import dev.raticode.mobile.browser.BrowserMediaRules.Engine
import dev.raticode.mobile.browser.BrowserMediaRules.Key
import dev.raticode.mobile.browser.BrowserMediaRules.Report
import dev.raticode.mobile.browser.BrowserMediaRules.Track
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.net.URL
import java.util.concurrent.Executors
import javax.net.ssl.HttpsURLConnection

/**
 * What the media bar at the top of Raticode shows, gathered from the two browsers built into the app. [BrowserRuntime] and
 * [GeckoBrowser] report their tabs here and carry out the bar's buttons; everything happens on the main thread and lasts
 * only while the app runs, so a restored tab never claims to be playing. The Firefox, Chrome and Edge apps are separate
 * apps whose playback Raticode can't see. [BrowserPlaybackService] puts the same track on the lock screen and in the
 * notification shade, and goes when the bar does.
 */
internal object BrowserMedia {
    /** The tab the bar shows, or null when no integrated browser tab is playing. */
    var current by mutableStateOf<Track?>(null); private set
    /** What the bar at the top shows: [current], unless that's the tab on the Browser page, then another tab or nothing. */
    var bar by mutableStateOf<Track?>(null); private set
    /** The tab the Browser page is showing, set by the page while it's on screen. */
    var inView: Key? = null; private set
    /** Thumbnails by [Track.artwork]. */
    val images = mutableStateMapOf<String, ImageBitmap>()
    /** The lock screen's media controls show the title and picture. Private tabs never do, and App lock hides them too. */
    var lockScreenTitles by mutableStateOf(true); private set

    private const val PREFS = "browser_media"
    private const val LOCK_SCREEN_TITLES = "lock_screen_titles"
    /** The application, never an activity, so nothing is kept alive by holding it. */
    private var app: Application? = null

    private const val MAX_IMAGES = 8
    private const val MAX_IMAGE_BYTES = 2 * 1024 * 1024
    private val main = Handler(Looper.getMainLooper())
    private val tracks = HashMap<Key, Track>()
    private val fetching = HashSet<String>()
    private val failed = LinkedHashSet<String>()
    private val io = Executors.newSingleThreadExecutor { Thread(it, "browser-media-art").apply { isDaemon = true } }
    private val expire = Runnable { refresh() }

    /** Called by each browser as it starts, so the media controls can reach Android. */
    fun attach(context: Context) {
        if (app != null) return
        val application = context.applicationContext as Application
        app = application
        lockScreenTitles = application.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean(LOCK_SCREEN_TITLES, true)
    }

    fun setLockScreenTitles(context: Context, on: Boolean) {
        attach(context)
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit { putBoolean(LOCK_SCREEN_TITLES, on) }
        lockScreenTitles = on
        app?.let(BrowserPlaybackService::sync)
    }

    /** The picture for [track] as Android's controls need it, once fetched. */
    fun bitmap(id: String?): Bitmap? = id?.let { images[it] }?.asAndroidBitmap()

    fun report(key: Key, report: Report) {
        val next = BrowserMediaRules.merge(tracks[key], key, report, now())
        if (next == null) tracks.remove(key) else tracks[key] = next
        refresh()
    }

    /** The tab closed, crashed or left the page: it goes at once, with no grace period. */
    fun forget(key: Key) { if (tracks.remove(key) != null) refresh() }

    fun forget(engine: Engine) { if (tracks.keys.removeAll { it.engine == engine }) refresh() }

    /** The Browser page shows [key]'s page now. Only the bar changes; the lock-screen controls keep showing [current]. */
    fun showing(key: Key) { if (inView != key) { inView = key; refresh() } }

    /** The Browser page stopped showing [key]: another tab, another page of Raticode, or the start page. */
    fun hidden(key: Key) { if (inView == key) { inView = null; refresh() } }

    /** One of the bar's buttons, carried out by the browser the tab lives in. */
    fun perform(track: Track, action: Action) {
        val handled = when (track.key.engine) {
            Engine.CHROMIUM -> BrowserRuntime.peek()?.mediaAction(track.key.tab, action)
            Engine.GECKO -> GeckoBrowser.peek()?.mediaAction(track.key.tab, action)
        }
        if (handled != true) forget(track.key)
    }

    /**
     * Shows the tab playing [track] on the Browser page, switching to the built-in browser (or Gecko profile) it lives in.
     * The default browser stays as the user chose it, and the other browser's tabs keep playing.
     */
    fun reveal(context: Context, track: Track) {
        val chromium = BrowserRuntime.get(context)
        when (track.key.engine) {
            Engine.CHROMIUM -> { chromium.show(BrowserRules.ENGINE_RATICODE); chromium.select(track.key.tab) }
            Engine.GECKO -> { chromium.show(BrowserRules.ENGINE_GECKO); GeckoBrowser.get(context).reveal(track.key.tab) }
        }
    }

    fun putImage(id: String, bitmap: Bitmap) {
        while (images.size >= MAX_IMAGES) images.remove(images.keys.first())
        images[id] = bitmap.asImageBitmap()
        if (current?.artwork == id) app?.let(BrowserPlaybackService::sync)
    }

    /** Fetches [url] for the bar once. It sends no cookies, so a site sees no more than an anonymous request for a picture. */
    fun fetchImage(url: String) {
        if (url in images || url in failed || !fetching.add(url)) return
        io.execute {
            val bitmap = download(url)
            main.post {
                fetching.remove(url)
                if (bitmap != null) putImage(url, bitmap)
                else { failed.add(url); if (failed.size > 32) failed.remove(failed.first()) }
            }
        }
    }

    /** Redirects are followed by hand, so every address is checked before anything is sent to it. */
    private fun download(start: String): Bitmap? = runCatching<Bitmap?> {
        var url = start
        for (hop in 0..BrowserMediaRules.MAX_ARTWORK_REDIRECTS) {
            val connection = URL(url).openConnection() as HttpsURLConnection
            connection.instanceFollowRedirects = false
            connection.connectTimeout = 8000; connection.readTimeout = 8000; connection.useCaches = false
            connection.setRequestProperty("Accept", "image/*")
            try {
                val code = connection.responseCode
                if (code in 300..399) { url = BrowserMediaRules.redirectTarget(url, connection.getHeaderField("Location")) ?: return null; continue }
                if (code != 200 || connection.contentType?.startsWith("image/") != true) return null
                val bytes = connection.inputStream.use(::readCapped) ?: return null
                val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
                val sample = BrowserMediaRules.sampleSize(bounds.outWidth, bounds.outHeight)
                if (sample == 0) return null
                return BitmapFactory.decodeByteArray(bytes, 0, bytes.size, BitmapFactory.Options().apply { inSampleSize = sample })
            } finally { connection.disconnect() }
        }
        null
    }.getOrNull()

    private fun readCapped(input: InputStream): ByteArray? {
        val out = ByteArrayOutputStream()
        val buffer = ByteArray(16 * 1024)
        while (true) {
            val count = input.read(buffer)
            if (count < 0) return out.toByteArray()
            out.write(buffer, 0, count)
            if (out.size() > MAX_IMAGE_BYTES) return null
        }
    }

    private fun now() = SystemClock.elapsedRealtime()

    private fun refresh() {
        val time = now()
        tracks.values.removeAll { !BrowserMediaRules.visible(it, time) }
        val shown = BrowserMediaRules.current(tracks.values, time)
        if (shown != current) { current = shown; app?.let(BrowserPlaybackService::sync) }
        bar = BrowserMediaRules.forBar(tracks.values, time, inView)
        (bar ?: shown)?.artwork?.takeIf { it.startsWith("https://") }?.let(::fetchImage)
        main.removeCallbacks(expire)
        BrowserMediaRules.nextExpiry(tracks.values, time)?.let { main.postDelayed(expire, it - time) }
    }
}
