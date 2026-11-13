package dev.raticode.mobile.browser

import dev.raticode.mobile.live.BrowserAddress
import org.json.JSONArray
import org.json.JSONObject
import java.net.URLEncoder

/**
 * The phone browser's decisions, kept apart from Android so plain JUnit can test them: what the address bar turns
 * input into, which navigations stay in the page, what history and bookmarks keep, and which downloads need a warning.
 *
 * The phone browser loads only secure (https) pages. The app forbids plain-text network traffic as a whole
 * (`usesCleartextTraffic="false"`), which also protects the encrypted desktop link, so `http://` pages get an
 * explanation with a secure retry and the phone's own browser instead of an engine error.
 */
object BrowserRules {
    const val HOME = "about:blank"
    const val MAX_TABS = 20
    const val MAX_HISTORY = 2000
    const val MAX_BOOKMARKS = 500
    const val MAX_TITLE = 300
    const val DEFAULT_SEARCH = "https://www.google.com/search?q={query}"

    /** Search choices besides the computer's own setting, as (label, template). */
    val SEARCH_ENGINES = listOf(
        "Google" to DEFAULT_SEARCH,
        "DuckDuckGo" to "https://duckduckgo.com/?q={query}",
        "Bing" to "https://www.bing.com/search?q={query}",
        "Brave Search" to "https://search.brave.com/search?q={query}",
    )

    sealed interface Target {
        /** Load this https (or about:blank) address in the tab. */
        data class Load(val url: String) : Target
        /** A plain http page: explain, and offer the https address and the phone's browser. */
        data class Insecure(val url: String) : Target
        /** Another app handles it (mail, phone, maps, store, intent links). Opens only after the user agrees. */
        data class App(val url: String) : Target
        /** Never opened: scripts, local files, content providers and browser-internal pages. */
        data class Blocked(val url: String, val reason: String) : Target
    }

    private val SCHEME = Regex("^([A-Za-z][A-Za-z0-9+.-]*):")
    private val APP_SCHEMES = setOf("mailto", "tel", "sms", "smsto", "mms", "geo", "market", "intent", "whatsapp", "tg", "zoommtg", "spotify")
    private val KNOWN_SCHEMES = APP_SCHEMES + setOf("http", "https", "about", "javascript", "file", "content", "data", "blob", "chrome",
        "view-source", "filesystem", "android-app", "android.resource", "ws", "wss")
    private val HOST_LIKE = Regex("^(localhost|(\\d{1,3}\\.){3}\\d{1,3}|\\[[0-9a-fA-F:.]+]|([A-Za-z0-9-]+\\.)+[A-Za-z][A-Za-z0-9-]{1,62})(:\\d{1,5})?([/?#].*)?$")

    const val BLOCKED_SCRIPT = "Raticode's browser doesn't run addresses that are scripts."
    const val BLOCKED_LOCAL = "Raticode's browser opens web pages only, not files or other apps' content on this phone."
    const val BLOCKED_OTHER = "Raticode's browser can't open this kind of address."
    const val BLOCKED_MALFORMED = "This web address isn't valid, so Raticode's browser didn't open it."
    const val BLOCKED_TOO_LONG = "This web address is longer than any browser opens, so Raticode's browser didn't open it."

    /** A refused address names its kind (never the address itself), so a screenshot of the notice says why. */
    fun blockedKind(scheme: String) = "Raticode's browser can't open ${scheme.take(20)}: addresses."

    /**
     * The longest address a page may navigate to: Chromium's own limit (`kMaxURLChars`, 2 MiB), which Android WebView
     * enforces. Sign-in pages pass tokens and nested return addresses between sites in their addresses, which can run past
     * the 2,048 characters used for the computer link and saved lists. Holding pages to 2,048 is the likeliest reason a
     * page after Google's sign-in was refused on a phone (November 2026), though the refused address wasn't measured.
     */
    const val MAX_PAGE_ADDRESS = 2 * 1024 * 1024

    /** A web address a page may load: [BrowserAddress.isWeb] with the page limit. */
    fun pageAddress(url: String) = BrowserAddress.isWeb(url, MAX_PAGE_ADDRESS)

    /** What the address bar does with [input]: a page, a search, or a refusal. Null for blank input. */
    fun resolve(input: String, searchTemplate: String): Target? {
        val text = input.trim().replace(Regex("[\\r\\n\\t]"), "")
        if (text.isEmpty()) return null
        val scheme = SCHEME.find(text)?.groupValues?.get(1)?.lowercase()
        // "localhost:3000" and "example.com:8443/x" look like a scheme; "note: milk" is a search.
        val known = scheme != null && (scheme in KNOWN_SCHEMES || text.startsWith("$scheme://", ignoreCase = true))
        if (known && !(HOST_LIKE.matches(text) && "://" !in text)) return classify(text, userTyped = true)
        if (' ' !in text && HOST_LIKE.matches(text)) return Target.Load("https://$text")
        return Target.Load(search(text, searchTemplate))
    }

    fun search(query: String, template: String): String {
        val usable = template.takeIf { "{query}" in it && BrowserAddress.isWeb(it.replace("{query}", "q")) } ?: DEFAULT_SEARCH
        return usable.replace("{query}", URLEncoder.encode(query.take(1000), "UTF-8").replace("+", "%20"))
    }

    /**
     * Where a navigation to [url] goes. Pages may move between https pages; anything else is decided here, and an app
     * link from a page needs a user gesture before it is even offered.
     */
    fun classify(url: String, userTyped: Boolean = false): Target {
        val scheme = SCHEME.find(url)?.groupValues?.get(1)?.lowercase() ?: return Target.Blocked(url, BLOCKED_MALFORMED)
        return when {
            scheme == "https" || scheme == "http" -> when {
                pageAddress(url) -> if (scheme == "https") Target.Load(url) else Target.Insecure(url)
                url.length > MAX_PAGE_ADDRESS -> Target.Blocked(url, BLOCKED_TOO_LONG)
                else -> Target.Blocked(url, BLOCKED_MALFORMED)
            }
            // A blank page, with or without a fragment or query, as pages use to reset frames and pop-ups.
            BLANK.matches(url) -> Target.Load(url.replaceFirst(Regex("^about:blank", RegexOption.IGNORE_CASE), HOME))
            url.startsWith("about:srcdoc") -> Target.Load(url)
            scheme == "javascript" -> Target.Blocked(url, BLOCKED_SCRIPT)
            scheme in setOf("file", "content", "android-app", "android.resource") -> Target.Blocked(url, BLOCKED_LOCAL)
            scheme in setOf("about", "chrome", "data", "blob", "view-source", "filesystem", "ws", "wss") ->
                Target.Blocked(url, blockedKind(scheme))
            scheme in APP_SCHEMES || (!userTyped && scheme.length >= 2) -> Target.App(url)
            else -> Target.Blocked(url, BLOCKED_OTHER)
        }
    }

    private val BLANK = Regex("^about:blank([?#].*)?$", RegexOption.IGNORE_CASE)

    /**
     * The address saved for a tab: short enough for the saved list, and without the query of a long one, which on
     * sign-in pages holds one-time tokens. Reopening such a tab after a restart loads the page without them.
     */
    fun storable(url: String): String = when {
        url == HOME || BrowserAddress.isWeb(url) -> url
        !pageAddress(url) -> HOME
        else -> url.substringBefore('#').substringBefore('?').takeIf { BrowserAddress.isWeb(it) }
            ?: url.substringBefore("://") + "://" + url.substringAfter("://").substringBefore('/').substringBefore('?').substringBefore('#') + "/"
    }

    /** The https version of an http address, offered on the insecure-page notice. */
    fun secureVersion(url: String): String? =
        if (url.startsWith("http://", ignoreCase = true)) "https://" + url.substring(7) else null

    enum class Security { SECURE, BLANK, OTHER }

    fun security(url: String): Security = when {
        url.isBlank() || url == HOME -> Security.BLANK
        url.startsWith("https://", ignoreCase = true) -> Security.SECURE
        else -> Security.OTHER
    }

    /** The address bar's short form: the host without `www.`, or nothing for a blank tab. */
    fun host(url: String): String {
        if (!pageAddress(url)) return ""
        val authority = url.substringAfter("://").substringBefore('/').substringBefore('?').substringBefore('#')
        return authority.substringBefore(':').removePrefix("www.").lowercase().ifBlank { authority }
    }

    fun title(title: String?, url: String): String {
        val clean = title.orEmpty().replace(Regex("\\s+"), " ").trim().take(MAX_TITLE)
        return clean.takeIf { it.isNotEmpty() && it != url && !it.startsWith("data:") } ?: host(url).ifBlank { "New tab" }
    }

    // History -------------------------------------------------------------------

    data class Visit(val url: String, val title: String, val at: Long)

    /** Adds a visit, newest first. A reload or a title change of the newest page updates it instead of adding another. */
    fun record(history: List<Visit>, url: String, title: String?, at: Long): List<Visit> {
        if (security(url) != Security.SECURE || url.length > BrowserAddress.MAX) return history
        val newest = history.firstOrNull()
        val visit = Visit(url, title(title, url), at)
        if (newest?.url == url) return listOf(visit.copy(title = if (title.isNullOrBlank()) newest.title else visit.title)) + history.drop(1)
        return (listOf(visit) + history).take(MAX_HISTORY)
    }

    /** History is cleared for the last [seconds], or all of it when null, as on the desktop. */
    fun clearHistory(history: List<Visit>, now: Long, seconds: Long?): List<Visit> =
        if (seconds == null) emptyList() else history.filter { it.at < now - seconds * 1000 }

    val CLEAR_RANGES = listOf<Pair<String, Long?>>("Last hour" to 3600L, "Last day" to 86_400L, "Last week" to 604_800L,
        "Last 4 weeks" to 2_419_200L, "All time" to null)

    // Bookmarks -----------------------------------------------------------------

    data class Bookmark(val url: String, val title: String, val at: Long)

    fun toggleBookmark(bookmarks: List<Bookmark>, url: String, title: String?, at: Long): List<Bookmark> = when {
        bookmarks.any { it.url == url } -> bookmarks.filterNot { it.url == url }
        security(url) != Security.SECURE || !BrowserAddress.isWeb(url) -> bookmarks
        else -> (listOf(Bookmark(url, title(title, url), at)) + bookmarks).take(MAX_BOOKMARKS)
    }

    // Suggestions ---------------------------------------------------------------

    data class Suggestion(val url: String, val title: String, val kind: String)

    /** Bookmarks, then open tabs, then history, each matching every word of [input] in its address or title. */
    fun suggest(input: String, bookmarks: List<Bookmark>, tabs: List<Pair<String, String>>, history: List<Visit>, limit: Int = 6): List<Suggestion> {
        val words = input.trim().lowercase().split(Regex("\\s+")).filter { it.isNotEmpty() }
        if (words.isEmpty()) return emptyList()
        fun matches(url: String, title: String) = (url.lowercase() + " " + title.lowercase()).let { text -> words.all { it in text } }
        val all = bookmarks.filter { matches(it.url, it.title) }.map { Suggestion(it.url, it.title, "Bookmark") } +
            tabs.filter { (url, title) -> matches(url, title) }.map { (url, title) -> Suggestion(url, title, "Open tab") } +
            history.filter { matches(it.url, it.title) }.map { Suggestion(it.url, it.title, "History") }
        return all.distinctBy { it.url }.take(limit)
    }

    // Page view -----------------------------------------------------------------

    /**
     * The user agent for "Desktop site": the phone's own engine version with a desktop Linux platform and without the
     * Mobile token, so sites send their full layout. The engine version stays truthful.
     */
    fun desktopUserAgent(mobile: String): String =
        mobile.replace(Regex("\\(Linux; Android[^)]*\\)"), "(X11; Linux x86_64)").replace(" Mobile", "").replace(Regex("\\s+"), " ").trim()

    /** What a page asked for, in words for the question put to the user. */
    fun mediaAsk(camera: Boolean, microphone: Boolean) = when {
        camera && microphone -> "camera and microphone"
        camera -> "camera"
        else -> "microphone"
    }

    const val MIN_TEXT = 50
    const val MAX_TEXT = 200
    fun textZoom(current: Int, step: Int) = (current + step).coerceIn(MIN_TEXT, MAX_TEXT)

    // Downloads -----------------------------------------------------------------

    private val RISKY = setOf("apk", "apks", "xapk", "aab", "exe", "msi", "bat", "cmd", "com", "scr", "ps1", "sh", "bash", "jar",
        "js", "vbs", "wsf", "dmg", "pkg", "deb", "rpm", "appimage", "run", "bin", "dll", "so", "lnk", "reg", "hta", "py")

    /** Apps, installers and scripts could harm the phone or a computer, so their download asks twice. */
    fun risky(name: String, mime: String?): Boolean =
        name.substringAfterLast('.', "").lowercase() in RISKY ||
            mime.orEmpty().lowercase() in setOf("application/vnd.android.package-archive", "application/x-msdownload", "application/x-sh")

    /**
     * A name safe to save in Downloads: no folders, controls or hidden-file dots, at most 120 characters. Invisible
     * format characters go too, so a right-to-left mark can't make `invoice\u202Efdp.apk` read as `invoicekpa.pdf`.
     */
    fun safeName(name: String): String {
        val base = name.substringAfterLast('/').substringAfterLast('\\').filter { it >= ' ' && it != '\u007f' && it !in "<>:\"|?*" && visible(it) }
            .trim().trimStart('.')
        if (base.isEmpty()) return "download"
        if (base.length <= 120) return base
        val ext = base.substringAfterLast('.', "").take(10)
        return if (ext.isEmpty()) base.take(120) else base.take(119 - ext.length) + "." + ext
    }

    private fun visible(c: Char) = Character.getType(c).toByte().let {
        it != Character.FORMAT && it != Character.LINE_SEPARATOR && it != Character.PARAGRAPH_SEPARATOR
    }

    /**
     * The question a tab may show next. A page can't swap a new question, such as a download or another app, in under
     * the user's finger while one is open: [offered] waits its turn and the page is told to try again.
     */
    fun <T : Any> nextQuestion(open: T?, offered: T): T? = if (open == null) offered else null

    fun size(bytes: Long): String = when {
        bytes <= 0 -> "Unknown size"
        bytes < 1024 -> "$bytes B"
        bytes < 1024 * 1024 -> "${(bytes + 512) / 1024} KB"
        else -> String.format(java.util.Locale.US, "%.1f MB", bytes / 1_048_576.0)
    }

    // Saved state ---------------------------------------------------------------

    /** [private] tabs live only in memory: [encode] leaves them out of everything written to storage. */
    data class SavedTab(val id: String, val url: String, val title: String, val private: Boolean = false)

    data class Saved(
        val tabs: List<SavedTab> = emptyList(),
        val active: String? = null,
        val history: List<Visit> = emptyList(),
        val bookmarks: List<Bookmark> = emptyList(),
        val closed: List<SavedTab> = emptyList(),
        val keepHistory: Boolean = true,
        val linksInApp: Boolean = true,
        /** "computer" follows the desktop's search setting; otherwise one of [SEARCH_ENGINES]' templates. */
        val search: String = "computer",
        /** The desktop's search template as last reported, used while the desktop is out of reach. */
        val computerSearch: String = "",
        val textZoom: Int = 100,
        /** How each of [BrowserApps] shows pages, by engine: "app" (the default) or "tab". */
        val appModes: Map<String, String> = emptyMap(),
        /**
         * The default browser: where new tabs, chat links and the Browser page go. "raticode" (Raticode (chromium), this
         * app's WebView browser), "gecko" (Raticode (gecko)), "firefox" (Firefox for Android), "edge" (Microsoft Edge) or a
         * [BrowserApps] engine such as "chrome". The stored values never change; only the names people see do.
         */
        val engine: String = ENGINE_RATICODE,
        /**
         * The built-in browser whose tab the Browser page shows after the user picked one of its tabs in the shared tab
         * list, when that isn't [engine]; null follows [engine]. See [pageEngine].
         */
        val showing: String? = null,
        /** How Firefox shows pages: as a Firefox tab over Raticode ("tab") or in the Firefox app ("app"). */
        val firefoxMode: String = FirefoxApps.MODE_APP,
        /** How Edge shows pages: in the Edge app ("app", the default, like Firefox) or as an Edge tab over Raticode ("tab"). */
        val edgeMode: String = EdgeApps.MODE_APP,
    )

    const val ENGINE_RATICODE = "raticode"
    const val ENGINE_FIREFOX = "firefox"
    const val ENGINE_EDGE = "edge"
    /** Raticode's second browser, on Mozilla's Gecko engine, inside this app (`GeckoBrowser`). */
    const val ENGINE_GECKO = "gecko"

    /** A saved or chosen browser value this phone knows, or "raticode" for anything else. */
    fun engine(value: String?): String =
        value?.takeIf { it == ENGINE_RATICODE || it == ENGINE_FIREFOX || it == ENGINE_EDGE || it == ENGINE_GECKO || BrowserApps.byEngine(it) != null } ?: ENGINE_RATICODE

    /** Raticode (chromium) and Raticode (gecko) run inside this app and share one tab list; the others are separate apps. */
    fun inApp(engine: String?) = engine == ENGINE_RATICODE || engine == ENGINE_GECKO

    /** The browser the Browser page shows: the built-in one whose tab the user picked last, or the default. */
    fun pageEngine(saved: Saved): String = saved.showing?.takeIf(::inApp) ?: saved.engine

    /**
     * The built-in browser a new tab opens in by default: the default browser when it's built in, otherwise the built-in one
     * on screen, otherwise Raticode (chromium). Firefox, Chrome and Edge can't hold tabs inside Raticode.
     */
    fun newTabEngine(saved: Saved): String = saved.engine.takeIf(::inApp) ?: saved.showing?.takeIf(::inApp) ?: ENGINE_RATICODE

    /** [engine] becomes the default browser, and the Browser page shows it. */
    fun withEngine(saved: Saved, engine: String): Saved = saved.copy(engine = engine(engine), showing = null)

    /**
     * The Browser page shows built-in browser [engine], as when the user picks one of its tabs, without changing the default.
     * Showing the default is stored as null, so a later change of default carries the page with it.
     */
    fun showing(saved: Saved, engine: String): Saved =
        if (!inApp(engine)) saved else saved.copy(showing = engine.takeIf { it != saved.engine })

    fun encode(saved: Saved): String = JSONObject()
        .put("v", 1)
        .put("tabs", JSONArray(saved.tabs.filterNot { it.private }.take(MAX_TABS).map(::tabJson)))
        .put("active", saved.active?.takeIf { id -> saved.tabs.none { it.id == id && it.private } } ?: saved.tabs.lastOrNull { !it.private }?.id ?: JSONObject.NULL)
        .put("history", JSONArray(saved.history.take(MAX_HISTORY).map { JSONObject().put("u", it.url).put("t", it.title).put("a", it.at) }))
        .put("bookmarks", JSONArray(saved.bookmarks.take(MAX_BOOKMARKS).map { JSONObject().put("u", it.url).put("t", it.title).put("a", it.at) }))
        .put("closed", JSONArray(saved.closed.take(25).map(::tabJson)))
        .put("app_opens", JSONObject(saved.appModes.filterKeys { BrowserApps.byEngine(it) != null }.mapValues { BrowserApps.mode(it.value) }))
        .put("keep_history", saved.keepHistory).put("links_in_app", saved.linksInApp)
        .put("search", saved.search).put("computer_search", saved.computerSearch).put("text_zoom", saved.textZoom)
        .put("engine", saved.engine).put("firefox_opens", saved.firefoxMode)
        .put("edge_opens", saved.edgeMode)
        .also { json -> saved.showing?.takeIf { inApp(it) && it != saved.engine }?.let { json.put("showing", it) } }
        .toString()

    /** Reads saved state. Anything unreadable falls back to empty, and only web addresses come back. */
    fun decode(text: String): Saved {
        val json = runCatching { JSONObject(text) }.getOrNull() ?: return Saved()
        fun tabs(key: String, max: Int) = json.optJSONArray(key).objects().mapNotNull { t ->
            val url = t.optString("u")
            SavedTab(t.optString("i").ifBlank { return@mapNotNull null }.take(64), url.takeIf { it == HOME || BrowserAddress.isWeb(it) } ?: HOME,
                t.optString("t").take(MAX_TITLE))
        }.distinctBy { it.id }.take(max)
        val tabs = tabs("tabs", MAX_TABS)
        val search = json.optString("search", "computer").takeIf { it == "computer" || SEARCH_ENGINES.any { (_, t) -> t == it } } ?: "computer"
        // Saves from before Chrome hold "raticode", "firefox" or "edge" and read as before; anything unknown means Raticode (chromium).
        val chosen = engine(json.optString("engine"))
        return Saved(
            tabs = tabs,
            active = json.optString("active").takeIf { id -> tabs.any { it.id == id } },
            history = json.optJSONArray("history").objects().mapNotNull { v ->
                v.optString("u").takeIf(BrowserAddress::isWeb)?.let { Visit(it, v.optString("t").take(MAX_TITLE), v.optLong("a")) }
            }.take(MAX_HISTORY),
            bookmarks = json.optJSONArray("bookmarks").objects().mapNotNull { b ->
                b.optString("u").takeIf(BrowserAddress::isWeb)?.let { Bookmark(it, b.optString("t").take(MAX_TITLE), b.optLong("a")) }
            }.distinctBy { it.url }.take(MAX_BOOKMARKS),
            closed = tabs("closed", 25),
            keepHistory = json.optBoolean("keep_history", true),
            linksInApp = json.optBoolean("links_in_app", true),
            search = search,
            computerSearch = json.optString("computer_search").takeIf { "{query}" in it && BrowserAddress.isWeb(it.replace("{query}", "q")) }.orEmpty(),
            textZoom = json.optInt("text_zoom", 100).coerceIn(MIN_TEXT, MAX_TEXT),
            appModes = json.optJSONObject("app_opens")?.let { modes ->
                BrowserApps.ALL.mapNotNull { app -> modes.optString(app.engine).takeIf { it.isNotEmpty() }?.let { app.engine to BrowserApps.mode(it) } }.toMap()
            }.orEmpty(),
            engine = chosen,
            // Saves from before the shared tab list have no "showing": the page follows the default, as it always did.
            showing = json.optString("showing").takeIf { inApp(it) && it != chosen },
            // "firefox_opens" replaced "firefox_mode" on November 7, 2026, when the Firefox app became the default: the
            // earlier default closed the page (and its music) when the user went back to Raticode. A choice made since
            // is kept; the old field is read as the new default.
            firefoxMode = if (json.optString("firefox_opens") == FirefoxApps.MODE_TAB) FirefoxApps.MODE_TAB else FirefoxApps.MODE_APP,
            // Edge opens in its own app unless the user chose a tab over Raticode, for the same reason as Firefox.
            edgeMode = if (json.optString("edge_opens") == EdgeApps.MODE_TAB) EdgeApps.MODE_TAB else EdgeApps.MODE_APP,
        )
    }

    /** How [engine]'s app shows pages, from [Saved.appModes]. */
    fun appMode(saved: Saved, engine: String): String = BrowserApps.mode(saved.appModes[engine])

    /** The search template in effect: the user's pick, or the computer's when known, or Google. */
    fun searchTemplate(saved: Saved): String = if (saved.search == "computer") saved.computerSearch.ifBlank { DEFAULT_SEARCH } else saved.search

    private fun tabJson(tab: SavedTab) = JSONObject().put("i", tab.id).put("u", tab.url).put("t", tab.title)
    private fun JSONArray?.objects(): List<JSONObject> = if (this == null) emptyList() else (0 until length()).mapNotNull { optJSONObject(it) }
}
