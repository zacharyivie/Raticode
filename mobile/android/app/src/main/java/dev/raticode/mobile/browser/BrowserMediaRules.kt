package dev.raticode.mobile.browser

import dev.raticode.mobile.live.BrowserAddress
import org.json.JSONArray
import org.json.JSONObject
import org.json.JSONTokener

/**
 * The media bar's decisions, kept apart from Android so plain JUnit can test them: which tab the bar shows, how long a
 * stopped tab stays, and what a page is allowed to put in the bar. Titles, artists and artwork addresses come from web
 * pages, so they are treated as untrusted text: cleaned, capped, and always shown beside the site that supplied them.
 */
object BrowserMediaRules {
    /** A paused tab that can resume stays this long, so Play can bring the music back. The desktop's bar waits the same. */
    const val PAUSE_GRACE_MS = 30_000L
    /** A tab that finished and can't resume stays this long, so the bar doesn't blink between two tracks. */
    const val STOP_GRACE_MS = 5_000L
    const val MAX_TEXT = 200
    /** Pixels on the long side of a thumbnail: 40 dp at the densest screens. */
    const val ARTWORK_PX = 192
    private const val MAX_REPORT = 64 * 1024
    private const val MAX_ARTWORK_ADDRESS = 2048
    /** How many times a thumbnail's address may send Raticode elsewhere. */
    const val MAX_ARTWORK_REDIRECTS = 3
    private const val SMALLEST_SHARP_ARTWORK = 96
    const val UNTITLED = "Untitled media"
    const val NO_SITE = "Browser tab"

    enum class Engine { CHROMIUM, GECKO }
    enum class Action { PLAY, PAUSE, PREVIOUS, NEXT }
    data class Key(val engine: Engine, val tab: String)

    /** What an engine says about one tab right now. */
    data class Report(val playing: Boolean, val title: String = "", val artist: String = "", val site: String = "",
                      val canToggle: Boolean = false, val canPrevious: Boolean = false, val canNext: Boolean = false, val artwork: String? = null,
                      val private: Boolean = false)

    /** A tab the bar knows about. */
    data class Track(val key: Key, val title: String, val artist: String, val site: String, val playing: Boolean,
                     val canToggle: Boolean, val canPrevious: Boolean, val canNext: Boolean, val artwork: String?,
                     val startedAt: Long, val stoppedAt: Long, val private: Boolean = false)

    /** The new record for a tab after [report], or null when the tab has never played and isn't playing now. */
    fun merge(previous: Track?, key: Key, report: Report, now: Long): Track? {
        if (previous == null && !report.playing) return null
        val restarted = report.playing && previous?.playing != true
        return Track(key, report.title, report.artist, report.site, report.playing, report.canToggle, report.canPrevious, report.canNext, report.artwork,
            private = report.private,
            startedAt = if (restarted || previous == null) now else previous.startedAt,
            stoppedAt = when { report.playing -> 0L; previous?.playing == true -> now; else -> previous?.stoppedAt ?: now })
    }

    /** The moment [track] leaves the bar if nothing changes. Null while it plays. */
    fun expiresAt(track: Track): Long? = when {
        track.playing -> null
        track.canToggle -> track.stoppedAt + PAUSE_GRACE_MS
        else -> track.stoppedAt + STOP_GRACE_MS
    }

    fun visible(track: Track, now: Long): Boolean = expiresAt(track)?.let { now < it } ?: true

    /** The tab the bar shows: something playing beats something paused, then the one that started last. */
    fun current(tracks: Collection<Track>, now: Long): Track? =
        tracks.filter { visible(it, now) }.sortedWith(compareByDescending<Track> { it.playing }.thenByDescending { it.startedAt }.thenBy { it.key.tab }).firstOrNull()

    /**
     * The tab the bar at the top of Raticode shows. The tab on the Browser page ([inView]) already shows its own player,
     * so the bar speaks for another tab, playing ones first, or stays away. The lock screen keeps following [current].
     */
    fun forBar(tracks: Collection<Track>, now: Long, inView: Key?): Track? = current(tracks.filter { it.key != inView }, now)

    /** When the bar next needs another look, or null when nothing is waiting to expire. */
    fun nextExpiry(tracks: Collection<Track>, now: Long): Long? = tracks.mapNotNull(::expiresAt).filter { it > now }.minOrNull()

    /**
     * Text from a page, on one line and short. Control characters and the invisible characters that reorder text are
     * removed, so a page can't make its title read differently from what it is.
     */
    fun clean(text: String?): String = text.orEmpty()
        .replace(Regex("[\\p{Cc}\\u202A-\\u202E\\u2066-\\u2069\\u200E\\u200F]"), " ")
        .replace(Regex("\\s+"), " ").trim().take(MAX_TEXT).trim()

    /** The bar's title: the page's media title, else the page title, else the site. Never empty. */
    fun title(metadata: String?, pageTitle: String?, url: String): String {
        val page = clean(pageTitle).takeIf { it.isNotEmpty() && it != url && !it.startsWith("data:") }
        return clean(metadata).ifEmpty { page ?: BrowserRules.host(url).ifBlank { UNTITLED } }
    }

    fun site(url: String): String = BrowserRules.host(url).ifBlank { NO_SITE }

    // Android's media controls --------------------------------------------------------------------------------------

    /** The title Android's controls show for a private tab. Nothing from the page leaves Raticode. */
    const val PRIVATE_TITLE = "Private tab"
    /** The title when what's playing stays hidden. */
    const val HIDDEN_TITLE = "Playing in Raticode's browser"
    const val APP_SUBTITLE = "Raticode browser"

    /** What Android's media controls show for a track: a title, one line under it, and the picture to use, if any. */
    data class Shown(val title: String, val subtitle: String, val artwork: String?)

    /**
     * What the notification, the lock screen and anything else reading Android's media session (a car, a watch) see. A
     * private tab never shows its page there. [hidden] keeps any other tab's title, site and picture back too.
     */
    fun shown(track: Track, hidden: Boolean): Shown = when {
        track.private -> Shown(PRIVATE_TITLE, HIDDEN_TITLE, null)
        hidden -> Shown(HIDDEN_TITLE, APP_SUBTITLE, null)
        else -> Shown(track.title, listOf(track.artist, track.site).filter { it.isNotBlank() }.joinToString(" · "), track.artwork)
    }

    /**
     * Whether the controls hide what's playing right now: only while the phone is locked, and then when the user turned
     * titles off for the lock screen or Raticode's App lock is on.
     */
    fun hideOnLockScreen(phoneLocked: Boolean, showTitles: Boolean, appLock: Boolean): Boolean = phoneLocked && (!showTitles || appLock)

    /** The buttons Android's controls offer, in order, matching the media bar: only what the page can do now. */
    fun buttons(track: Track): List<Action> = buildList {
        if (track.canPrevious) add(Action.PREVIOUS)
        if (track.canToggle) add(if (track.playing) Action.PAUSE else Action.PLAY)
        if (track.canNext) add(Action.NEXT)
    }

    /** Whether a press from outside the app (lock screen, notification, headset) may do [action] to [track] now. */
    fun allowed(track: Track, action: Action): Boolean = action in buttons(track)

    /** What TalkBack reads for the title area of the bar. */
    fun spoken(track: Track): String = listOf(track.title, track.artist, "from ${track.site}").filter { it.isNotBlank() }.joinToString(", ")

    /** Reads [raw], the answer from the page's status script, or null if it isn't one. Everything in it came from the page. */
    fun parse(raw: String?): Report? {
        if (raw == null || raw.length > MAX_REPORT) return null
        return runCatching {
            val text = JSONTokener(raw).nextValue() as? String ?: return null
            if (text.isEmpty() || text.length > MAX_REPORT) return null
            val json = JSONObject(text)
            Report(playing = json.optBoolean("playing"), title = clean(json.optString("title")), artist = clean(json.optString("artist")),
                canToggle = json.optBoolean("canToggle"), canPrevious = json.optBoolean("previous"), canNext = json.optBoolean("next"),
                artwork = artwork(artworkList(json.optJSONArray("artwork")), json.optString("poster")))
        }.getOrNull()
    }

    private fun artworkList(array: JSONArray?): List<Pair<String, String>> {
        if (array == null) return emptyList()
        return (0 until minOf(array.length(), 8)).mapNotNull { index ->
            array.optJSONArray(index)?.let { it.optString(0) to it.optString(1) }
        }
    }

    /** The picture to fetch: the smallest listed one that is still sharp, else the largest listed, else the video's poster. */
    fun artwork(listed: List<Pair<String, String>>, poster: String?): String? {
        val usable = listed.filter { artworkAddress(it.first) }
        val sized = usable.map { it.first to largestSide(it.second) }
        return sized.filter { it.second >= SMALLEST_SHARP_ARTWORK }.minByOrNull { it.second }?.first
            ?: sized.filter { it.second > 0 }.maxByOrNull { it.second }?.first
            ?: usable.firstOrNull()?.first
            ?: poster?.takeIf(::artworkAddress)
    }

    private fun largestSide(sizes: String): Int = sizes.split(' ').mapNotNull { size ->
        size.lowercase().split('x').takeIf { it.size == 2 }?.mapNotNull { it.toIntOrNull() }?.takeIf { it.size == 2 }?.max()
    }.maxOrNull() ?: 0

    private val LOCAL_SUFFIXES = listOf(".local", ".localhost", ".internal", ".lan", ".home", ".intranet", ".home.arpa")
    private val HOST_NAME = Regex("[a-z0-9-]+(\\.[a-z0-9-]+)+")
    /** The last label of a host that browsers read as an IPv4 address in decimal, octal or hex: `127.1`, `0x7f.1`, `2130706433`. */
    private val NUMERIC_LABEL = Regex("0x[0-9a-f]*|[0-9]+")

    /**
     * Whether Raticode may fetch a thumbnail from [url]: secure, and named on the public internet. Goes by the address as
     * written, with no name lookup: IP addresses in any form, `localhost`, local names and their trailing-dot spellings are refused.
     */
    fun artworkAddress(url: String): Boolean {
        if (url.length > MAX_ARTWORK_ADDRESS || !url.startsWith("https://", ignoreCase = true) || !BrowserAddress.isWeb(url)) return false
        val authority = url.substring("https://".length).substringBefore('/').substringBefore('?').substringBefore('#').lowercase()
        val name = authority.substringBefore(':').trimEnd('.')
        return HOST_NAME.matches(name) && LOCAL_SUFFIXES.none(name::endsWith) && !NUMERIC_LABEL.matches(name.substringAfterLast('.'))
    }

    /** Where a redirect from [from] leads, or null when it has no target or the target isn't one Raticode may fetch. */
    fun redirectTarget(from: String, location: String?): String? {
        if (location.isNullOrBlank()) return null
        val target = runCatching { java.net.URL(java.net.URL(from), location.trim()).toString() }.getOrNull() ?: return null
        return target.takeIf(::artworkAddress)
    }

    /** The inSampleSize that brings a [width] by [height] picture down to about [ARTWORK_PX], or 0 for a size not worth decoding. */
    fun sampleSize(width: Int, height: Int): Int {
        if (width <= 0 || height <= 0 || width > 8192 || height > 8192) return 0
        var sample = 1
        while (maxOf(width, height) / (sample * 2) >= ARTWORK_PX) sample *= 2
        return sample
    }
}

/**
 * Tells a new document from a same-page address change in Gecko, which reports both the same way. A real navigation starts
 * a load and then changes the address when the new page replaces the old one. A load that ends with no address change, such
 * as a download, left the old page playing. Only the first address change after a start counts.
 */
class GeckoDocuments {
    private val loading = HashSet<String>()
    fun loadStarted(tab: String) { loading.add(tab) }
    fun loadStopped(tab: String) { loading.remove(tab) }
    /** True when this address change put a new document in [tab]. */
    fun addressChanged(tab: String): Boolean = loading.remove(tab)
}

/**
 * The scripts that read and control media in Raticode's WebView tabs. They run in the page with `evaluateJavascript`; the
 * page gets no way to call the app, so the only thing that crosses back is the answer [BrowserMediaRules.parse] checks.
 *
 * [HOOK] notes the track buttons a page registers and players a script keeps outside the document, neither of which a page
 * otherwise exposes. Android's WebView has left out `navigator.mediaSession`, since it has no media controls of its own, so
 * the hook gives pages a stand-in with the same shape when it is missing; sites then describe their tracks as they do in
 * any phone browser. A page that registers its buttons before the hook lands shows them as unavailable.
 */
internal object BrowserMediaScript {
    private const val PRELUDE = "var K=Symbol.for('raticode.media'),S=window[K];if(!S){S={a:{},m:[]};Object.defineProperty(window,K,{value:S});" +
        "if(!navigator.mediaSession){Object.defineProperty(navigator,'mediaSession',{configurable:true,value:{metadata:null,playbackState:'none',setActionHandler:function(){},setPositionState:function(){},setCameraActive:function(){},setMicrophoneActive:function(){}}});" +
        "if(!window.MediaMetadata)window.MediaMetadata=function(o){o=o||{};this.title=o.title||'';this.artist=o.artist||'';this.album=o.album||'';this.artwork=(o.artwork||[]).map(function(a){var u=String(a.src);try{u=new URL(u,location.href).href}catch(e){}return {src:u,sizes:a.sizes||'',type:a.type||''}})}}" +
        "var M=navigator.mediaSession;if(M&&M.setActionHandler){var O=M.setActionHandler;M.setActionHandler=function(n,h){if(h)S.a[n]=h;else delete S.a[n];return O.apply(M,arguments)}}" +
        "var P=HTMLMediaElement.prototype,Q=P.play;P.play=function(){if(S.m.indexOf(this)<0){S.m.push(this);if(S.m.length>8)S.m.shift()}return Q.apply(this,arguments)}}"

    /** A: every known player. L: those audibly playing, with their volume up. S.l: the one that played last. */
    private const val ELEMENTS = "var D=[].slice.call(document.querySelectorAll('audio,video')),A=D.concat(S.m.filter(function(x){return D.indexOf(x)<0}))," +
        "L=A.filter(function(m){return !m.paused&&!m.ended&&!m.muted&&m.volume>0&&(m.currentSrc||m.srcObject)});if(L.length)S.l=L[0];"

    private const val ANSWER = "var T=S.l,H=S.a,I=navigator.mediaSession,C=I&&I.metadata,R=[];if(C)for(var i=0;i<C.artwork.length&&i<8;i++)R.push([C.artwork[i].src,C.artwork[i].sizes||'']);" +
        "return JSON.stringify({playing:L.length>0,canToggle:L.length>0||!!H.play||!!(T&&!T.ended),previous:!!H.previoustrack,next:!!H.nexttrack," +
        "title:String(C&&C.title||'').slice(0,500),artist:String(C&&C.artist||'').slice(0,500),artwork:R,poster:String(T&&T.poster||'')})"

    const val HOOK = "(function(){try{$PRELUDE}catch(e){}return true})()"
    const val STATE = "(function(){try{$PRELUDE$ELEMENTS$ANSWER}catch(e){return ''}})()"
    const val PAUSE_ALL = "(function(){try{$PRELUDE$ELEMENTS A.forEach(function(m){m.pause()})}catch(e){}return true})()"

    private const val PAUSE = "if(S.a.pause)S.a.pause({action:'pause'});else L.forEach(function(m){m.pause()});"
    private const val PLAY = "if(S.a.play)S.a.play({action:'play'});else if(S.l){var U=S.l.play();if(U&&U.catch)U.catch(function(){})}"
    private const val NEXT = "if(S.a.nexttrack)S.a.nexttrack({action:'nexttrack'});"
    private const val PREVIOUS = "if(S.a.previoustrack)S.a.previoustrack({action:'previoustrack'});"

    /** Does [action] in the page, then answers like [STATE]. A page's own button is used when it registered one. */
    fun control(action: BrowserMediaRules.Action): String {
        val body = when (action) {
            BrowserMediaRules.Action.PAUSE -> PAUSE
            BrowserMediaRules.Action.PLAY -> PLAY
            BrowserMediaRules.Action.NEXT -> NEXT
            BrowserMediaRules.Action.PREVIOUS -> PREVIOUS
        }
        return "(function(){try{$PRELUDE$ELEMENTS$body$ELEMENTS$ANSWER}catch(e){return ''}})()"
    }
}
