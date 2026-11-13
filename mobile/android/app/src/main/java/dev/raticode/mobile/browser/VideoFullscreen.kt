package dev.raticode.mobile.browser

import android.content.pm.ActivityInfo
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import dev.raticode.mobile.browser.BrowserMediaRules.Engine

/**
 * A page full screen in one of the two browsers built into Raticode, usually a video's full-screen button. While one is,
 * and the Browser page is on screen, the video gets the whole phone: Raticode's bottom bar, the browser's controls and the
 * media bar give way, Android's status and navigation bars hide until swiped in, and the screen turns to landscape (see
 * [VideoFullscreenRules]). [BrowserRuntime] and [GeckoBrowser] report here; the screen ([dev.raticode.mobile.ui.LiveScreen])
 * reads it. Main thread only, and it lasts only while the app runs.
 */
internal object VideoFullscreen {
    var current by mutableStateOf<VideoFullscreenRules.Video?>(null); private set

    fun enter(engine: Engine, tab: String) { current = VideoFullscreenRules.entered(current, engine, tab) }
    fun size(engine: Engine, tab: String, width: Long, height: Long) { current = VideoFullscreenRules.sized(current, engine, tab, width, height) }
    fun leave(engine: Engine, tab: String) { current = VideoFullscreenRules.left(current, engine, tab) }
    fun leave(engine: Engine) { if (current?.engine == engine) current = null }
}

/** What full-screen video does to Raticode's screen, apart from Android so it can be tested on its own. */
internal object VideoFullscreenRules {
    /** The page Raticode's browsers live on (see LiveNavigation). */
    const val BROWSER_PAGE = "Browser"
    /** MainActivity names no orientation, so leaving full screen goes back to following the phone. */
    const val RESTORE_ORIENTATION = ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED

    /** The full-screen tab, with the video's size in pixels once known (0 until then). */
    data class Video(val engine: Engine, val tab: String, val width: Long = 0, val height: Long = 0)

    fun entered(current: Video?, engine: Engine, tab: String): Video? =
        if (current?.engine == engine && current.tab == tab) current else Video(engine, tab)

    fun sized(current: Video?, engine: Engine, tab: String, width: Long, height: Long): Video? =
        if (current?.engine == engine && current.tab == tab && width > 0 && height > 0) current.copy(width = width, height = height) else current

    fun left(current: Video?, engine: Engine, tab: String): Video? = if (current?.engine == engine && current.tab == tab) null else current

    /** Showing another tab of the same browser ends its full screen: only the tab in view can have it. */
    fun endsOnSelect(current: Video?, engine: Engine, tab: String): Boolean = current?.engine == engine && current.tab != tab

    /** A video taller than it is wide, such as a phone-shot clip, stays upright; everything else, or a size not known yet, turns. */
    fun upright(video: Video): Boolean = video.width > 0 && video.height > video.width

    /** The video takes the whole screen: it is full screen and the Browser page is the one showing. */
    fun covers(video: Video?, page: String): Boolean = video != null && page == BROWSER_PAGE

    /**
     * Android's bars hide and the screen turns only while the video covers the screen and nothing is over it. App lock's
     * cover is shown upright with the bars, and the video comes back full screen once the lock is passed.
     */
    fun takesWindow(video: Video?, page: String, appLocked: Boolean): Boolean = covers(video, page) && !appLocked

    /**
     * The orientation the activity asks for. The user-* values follow the phone's auto-rotate setting for which way up
     * landscape is, and keep the orientation itself whatever that setting says.
     */
    fun orientation(video: Video?, page: String, appLocked: Boolean): Int = when {
        video == null || !takesWindow(video, page, appLocked) -> RESTORE_ORIENTATION
        upright(video) -> ActivityInfo.SCREEN_ORIENTATION_USER_PORTRAIT
        else -> ActivityInfo.SCREEN_ORIENTATION_USER_LANDSCAPE
    }

    /**
     * Raticode (chromium) asks the page for the size of the video that went full screen: the full-screen element itself,
     * or the first video inside it, as players wrap theirs in their own controls. Empty when there is none or it isn't
     * loaded; a video in another site's frame can't be read and counts as landscape.
     */
    const val SIZE_SCRIPT = "(function(){try{var e=document.fullscreenElement||document.webkitFullscreenElement;if(!e)return '';" +
        "var v=e.tagName==='VIDEO'?e:e.querySelector('video');if(!v||!v.videoWidth)return '';return v.videoWidth+'x'+v.videoHeight}catch(x){return ''}})()"

    /** Reads [SIZE_SCRIPT]'s answer as WebView passes it (a JSON string), or null. */
    fun parseSize(answer: String?): Pair<Long, Long>? {
        val text = answer?.trim()?.removeSurrounding("\"") ?: return null
        val parts = text.split('x')
        if (parts.size != 2) return null
        val width = parts[0].toLongOrNull() ?: return null
        val height = parts[1].toLongOrNull() ?: return null
        return if (width in 1..MAX_SIDE && height in 1..MAX_SIDE) width to height else null
    }
    private const val MAX_SIDE = 100_000L
}
