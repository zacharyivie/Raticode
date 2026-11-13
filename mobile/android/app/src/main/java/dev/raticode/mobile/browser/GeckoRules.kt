package dev.raticode.mobile.browser

import dev.raticode.mobile.live.BrowserAddress
import org.json.JSONArray
import org.json.JSONObject
import java.net.URI
import java.net.URLEncoder
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.YearMonth

/**
 * The rules of Raticode's Gecko browser (`GeckoBrowser`): what it saves, and what it may install. Android-free, so they
 * can be tested without an engine. Its data is its own: nothing here touches the Chromium browser's tabs or history.
 */
internal object GeckoRules {
    const val DEFAULT_PROFILE = "default"
    const val MAX_PROFILES = 8
    const val MAX_HISTORY = 1000
    const val MAX_BOOKMARKS = 500
    const val MAX_CLOSED = 25
    private const val MAX_NAME = 40
    /** Questions (dialogs, sign-ins, saves) that may wait at once; more are dismissed as if the user said no. */
    const val MAX_WAITING_ASKS = 3

    /** A page may ask only from the tab on screen, and only while few questions wait. */
    fun mayAsk(activeTab: Boolean, waiting: Int): Boolean = activeTab && waiting < MAX_WAITING_ASKS

    /** [private] tabs live only in memory: [encode] leaves them out of everything written to storage. */
    data class Tab(val id: String, val url: String = BrowserRules.HOME, val title: String = "", val profile: String = DEFAULT_PROFILE, val private: Boolean = false)
    data class Profile(val id: String, val name: String)
    data class Link(val url: String, val title: String, val profile: String = DEFAULT_PROFILE)
    data class Settings(
        /** Tracking protection: "standard" or "strict". */
        val tracking: String = TRACKING_STANDARD,
        /** How pages are told to look: "system", "light" or "dark". */
        val colors: String = COLORS_SYSTEM,
        val keepHistory: Boolean = true,
        /** Address bar searches; the same choice of engines as Raticode's own browser. */
        val search: String = BrowserRules.DEFAULT_SEARCH,
        val desktopSites: Boolean = false,
        val textZoom: Int = 100,
    )
    data class Saved(
        val tabs: List<Tab> = emptyList(),
        val active: String? = null,
        val profiles: List<Profile> = listOf(Profile(DEFAULT_PROFILE, "Personal")),
        val currentProfile: String = DEFAULT_PROFILE,
        val bookmarks: List<Link> = emptyList(),
        val history: List<Link> = emptyList(),
        val settings: Settings = Settings(),
        val closed: List<Tab> = emptyList(),
    )

    const val TRACKING_STANDARD = "standard"
    const val TRACKING_STRICT = "strict"
    const val COLORS_SYSTEM = "system"
    const val COLORS_LIGHT = "light"
    const val COLORS_DARK = "dark"

    fun encode(saved: Saved): String = JSONObject()
        .put("tabs", JSONArray(saved.tabs.filterNot { it.private }.take(BrowserRules.MAX_TABS).map(::tabJson)))
        .put("closed", JSONArray(saved.closed.take(MAX_CLOSED).map(::tabJson)))
        .put("active", saved.active?.takeIf { id -> saved.tabs.none { it.id == id && it.private } } ?: saved.tabs.lastOrNull { !it.private }?.id ?: JSONObject.NULL)
        .put("profiles", JSONArray(saved.profiles.map { JSONObject().put("id", it.id).put("name", it.name) }))
        .put("current_profile", saved.currentProfile)
        .put("bookmarks", JSONArray(saved.bookmarks.take(MAX_BOOKMARKS).map(::linkJson)))
        .put("history", JSONArray(saved.history.take(MAX_HISTORY).map(::linkJson)))
        .put("settings", JSONObject().put("tracking", saved.settings.tracking).put("colors", saved.settings.colors)
            .put("keep_history", saved.settings.keepHistory).put("search", saved.settings.search).put("desktop_sites", saved.settings.desktopSites).put("text_zoom", saved.settings.textZoom))
        .toString()

    private fun tabJson(tab: Tab) = JSONObject().put("id", tab.id).put("url", tab.url).put("title", tab.title).put("profile", tab.profile)

    private fun linkJson(link: Link) = JSONObject().put("url", link.url).put("title", link.title).put("profile", link.profile)

    /** Reads saved state. Anything unreadable falls back to empty, and only web addresses come back. */
    fun decode(text: String): Saved = runCatching {
        val json = JSONObject(text)
        val profiles = json.optJSONArray("profiles").objects().mapNotNull { item ->
            val id = item.optString("id").takeIf { it.isNotBlank() } ?: return@mapNotNull null
            Profile(id, item.optString("name").take(MAX_NAME).ifBlank { "Profile" })
        }.distinctBy { it.id }.take(MAX_PROFILES).let { list -> if (list.none { it.id == DEFAULT_PROFILE }) listOf(Profile(DEFAULT_PROFILE, "Personal")) + list else list }
        val known = profiles.map { it.id }.toSet()
        val profileOf = { value: String -> value.takeIf { it in known } ?: DEFAULT_PROFILE }
        fun tabs(key: String) = json.optJSONArray(key).objects().mapNotNull { item ->
            val id = item.optString("id").takeIf { it.isNotBlank() } ?: return@mapNotNull null
            val url = item.optString("url").takeIf { it == BrowserRules.HOME || BrowserAddress.isWeb(it) } ?: BrowserRules.HOME
            Tab(id, url, item.optString("title").take(BrowserRules.MAX_TITLE), profileOf(item.optString("profile")))
        }.distinctBy { it.id }
        val tabs = tabs("tabs").take(BrowserRules.MAX_TABS)
        val settings = json.optJSONObject("settings") ?: JSONObject()
        Saved(tabs, json.optString("active").takeIf { active -> tabs.any { it.id == active } }, profiles, profileOf(json.optString("current_profile")),
            links(json.optJSONArray("bookmarks"), known).take(MAX_BOOKMARKS), links(json.optJSONArray("history"), known).take(MAX_HISTORY),
            Settings(if (settings.optString("tracking") == TRACKING_STRICT) TRACKING_STRICT else TRACKING_STANDARD,
                settings.optString("colors").takeIf { it == COLORS_LIGHT || it == COLORS_DARK } ?: COLORS_SYSTEM,
                settings.optBoolean("keep_history", true),
                settings.optString("search").takeIf { "{query}" in it && BrowserAddress.isWeb(it.replace("{query}", "q")) } ?: BrowserRules.DEFAULT_SEARCH,
                settings.optBoolean("desktop_sites", false),
                settings.optInt("text_zoom", 100).coerceIn(BrowserRules.MIN_TEXT, BrowserRules.MAX_TEXT)),
            tabs("closed").take(MAX_CLOSED))
    }.getOrDefault(Saved())

    private fun links(array: JSONArray?, known: Set<String>) = array.objects().mapNotNull { item ->
        val url = item.optString("url").takeIf { BrowserAddress.isWeb(it) } ?: return@mapNotNull null
        Link(url, item.optString("title").take(BrowserRules.MAX_TITLE), item.optString("profile").takeIf { it in known } ?: DEFAULT_PROFILE)
    }

    private fun JSONArray?.objects(): List<JSONObject> = if (this == null) emptyList() else (0 until length()).mapNotNull { optJSONObject(it) }

    fun cleanProfileName(name: String): String = name.trim().replace(Regex("\\s+"), " ").take(MAX_NAME)

    /** A profile's cookie jar. Every profile has its own, so clearing one never touches another. */
    fun contextId(profile: String): String = "raticode-$profile"

    /** Adds a visit, newest first, without repeating the address just before it. */
    fun remember(history: List<Link>, url: String, title: String, profile: String): List<Link> {
        if (!BrowserAddress.isWeb(url)) return history
        if (history.firstOrNull()?.let { it.url == url && it.profile == profile } == true)
            return listOf(Link(url, title.take(BrowserRules.MAX_TITLE), profile)) + history.drop(1)
        return (listOf(Link(url, title.take(BrowserRules.MAX_TITLE), profile)) + history).take(MAX_HISTORY)
    }

    // Form pickers ----------------------------------------------------------------------------------------------------

    const val FORM_DATE = "date"
    const val FORM_MONTH = "month"
    const val FORM_WEEK = "week"
    const val FORM_TIME = "time"
    const val FORM_DATETIME = "date and time"
    const val FORM_COLOR = "color"

    fun formHint(kind: String) = when (kind) {
        FORM_DATE -> "Type a date like 2026-11-08."
        FORM_MONTH -> "Type a month like 2026-11."
        FORM_WEEK -> "Type a week like 2026-W45."
        FORM_TIME -> "Type a 24-hour time like 14:30."
        FORM_DATETIME -> "Type a date and 24-hour time like 2026-11-08 14:30."
        else -> "Type a color like #ff8800."
    }

    /** What to give the page for [input], as HTML writes that kind of value; empty clears the field; null when it isn't valid or is outside [min]..[max]. */
    fun formValue(kind: String, input: String, min: String? = null, max: String? = null): String? {
        val text = input.trim().let { if (kind == FORM_DATETIME) it.replace(' ', 'T') else it }
        if (text.isEmpty()) return ""
        val value = runCatching {
            when (kind) {
                FORM_DATE -> LocalDate.parse(text).toString()
                FORM_MONTH -> YearMonth.parse(text).toString()
                FORM_WEEK -> text.takeIf { Regex("\\d{4}-W(0[1-9]|[1-4]\\d|5[0-3])").matches(it) }
                FORM_TIME -> { LocalTime.parse(text); text }
                FORM_DATETIME -> { LocalDateTime.parse(text); text }
                FORM_COLOR -> text.lowercase().takeIf { Regex("#[0-9a-f]{6}").matches(it) }
                else -> null
            }
        }.getOrNull() ?: return null
        if (kind != FORM_COLOR && (!min.isNullOrEmpty() && value < min || !max.isNullOrEmpty() && value > max)) return null
        return value
    }

    // Extensions ------------------------------------------------------------------------------------------------------

    private const val AMO = "addons.mozilla.org"

    /** Extension files come only from Mozilla's add-ons site. Gecko separately refuses any file Mozilla hasn't signed. */
    fun installable(url: String): Boolean = runCatching {
        val uri = URI(url)
        uri.scheme == "https" && uri.host == AMO && uri.userInfo == null && uri.port == -1 && uri.path.endsWith(".xpi")
    }.getOrDefault(false)

    /**
     * Whether a tab's saved back/forward list can reopen it: its current page is a web page. A page process that ended
     * can leave a list that is empty or stops at about:blank, which would reopen the tab blank (see TabRecovery).
     */
    fun restorable(currentUri: String?): Boolean = currentUri != null && BrowserAddress.isWeb(currentUri)

    /** Whether an address is an extension file offered by a page, wherever it came from. */
    fun looksLikeExtension(url: String): Boolean = runCatching { URI(url).path.orEmpty().endsWith(".xpi", ignoreCase = true) }.getOrDefault(false)

    fun searchUrl(query: String): String = "https://$AMO/api/v5/addons/search/?app=firefox&type=extension&sort=users&page_size=25&lang=en-US&q=" +
        URLEncoder.encode(query.take(200), "UTF-8").replace("+", "%20")

    /** One extension from Mozilla's catalogue. [android] means its author lists Firefox for Android. */
    data class Listing(val id: String, val name: String, val summary: String, val users: Int, val rating: Double, val url: String, val android: Boolean, val icon: String)

    fun parseSearch(text: String): List<Listing> = runCatching {
        JSONObject(text).optJSONArray("results").objects().mapNotNull(::listing)
    }.getOrDefault(emptyList())

    /** One extension from an answer of Mozilla's site, or null when it has no file Raticode may install. */
    fun listing(item: JSONObject): Listing? {
        val version = item.optJSONObject("current_version") ?: return null
        val url = version.optJSONObject("file")?.optString("url").orEmpty().takeIf(::installable) ?: return null
        return Listing(item.optString("guid"), localized(item.opt("name")).take(80).ifBlank { item.optString("slug") }, localized(item.opt("summary")).take(280),
            item.optInt("average_daily_users"), item.optJSONObject("ratings")?.optDouble("average", 0.0) ?: 0.0, url,
            version.optJSONObject("compatibility")?.has("android") == true, item.optString("icon_url").takeIf { it.startsWith("https://") }.orEmpty())
    }

    private fun localized(value: Any?): String = when (value) {
        is String -> value
        is JSONObject -> value.optString("en-US").ifBlank { value.keys().asSequence().firstOrNull()?.let(value::optString).orEmpty() }
        else -> ""
    }

    /** Plain words for one of Gecko's install errors (`WebExtension.InstallException.ErrorCodes`). */
    fun installError(code: Int): String = when (code) {
        -1 -> "The extension couldn't be downloaded. Check the connection and try again."
        -2, -3 -> "The extension file is damaged."
        -5 -> "Mozilla hasn't signed this extension, so it can't be installed."
        -10, -14 -> "Mozilla has blocked this extension."
        -11 -> "This extension doesn't work with this version of the browser engine."
        -100 -> "Installation cancelled."
        else -> "The extension couldn't be installed (error $code)."
    }

    /** What tapping Open on an installed extension does. */
    sealed interface ExtensionOpen {
        /** Press its toolbar button: Gecko shows the button's popup, or tells the extension it was clicked. */
        data object Button : ExtensionOpen
        /** Its settings page, in a new tab. */
        data class Options(val url: String) : ExtensionOpen
        /** Nothing opens; [message] says why, in plain words. */
        data class Refused(val message: String) : ExtensionOpen
    }

    /**
     * What Open does for an extension. [button] is whether it has a toolbar button, and [buttonOn] whether the button is
     * on for the page in view (null when the extension didn't say). [privateTab] is the tab in view: extensions don't run
     * in private tabs, so a button press there is refused. Its settings page has no page to act on, so it still opens, in a
     * regular tab, as the Settings button does.
     */
    fun extensionOpen(name: String, enabled: Boolean, button: Boolean, buttonOn: Boolean?, options: String?, privateTab: Boolean): ExtensionOpen = when {
        !enabled -> ExtensionOpen.Refused("Turn $name on to open it.")
        button && privateTab -> ExtensionOpen.Refused("Extensions don't run in private tabs. Switch to a regular tab to open $name.")
        button && buttonOn != false -> ExtensionOpen.Button
        options != null -> ExtensionOpen.Options(options)
        button -> ExtensionOpen.Refused("$name's button is off on this page. Try it on another page.")
        else -> ExtensionOpen.Refused("$name has nothing to open. It works on the pages you visit by itself.")
    }

    /** Shown when a button press opened no popup or tab: the extension handles the press itself, on the page in view. */
    fun extensionActedOnPage(name: String) = "$name has no window to open. Its button works on the page you're viewing."

    /**
     * The tab Gecko's extensions should treat as the one in view, or null for none. Gecko keeps no idea of its own of which
     * tab that is: without being told, a toolbar button press finds no tab and quietly does nothing. A private tab is never
     * named, so extensions, which don't run in private tabs, learn nothing about it.
     */
    fun extensionTab(active: String?, private: Boolean, live: Boolean): String? = active?.takeIf { live && !private }

    /** Turns a Firefox bookmarks HTML export into links; only web addresses are kept. */
    fun parseBookmarks(html: String, profile: String): List<Link> =
        Regex("<a\\s[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL)).findAll(html).mapNotNull { match ->
            val url = unescape(match.groupValues[1]).takeIf { BrowserAddress.isWeb(it) } ?: return@mapNotNull null
            Link(url, unescape(match.groupValues[2].replace(Regex("<[^>]*>"), "")).trim().take(BrowserRules.MAX_TITLE), profile)
        }.distinctBy { it.url }.take(MAX_BOOKMARKS).toList()

    private fun unescape(text: String) = text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", "\"").replace("&#39;", "'").replace("&amp;", "&")
}
