package dev.raticode.mobile.browser

import dev.raticode.mobile.live.BrowserAddress
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.distinctUntilChanged
import org.json.JSONArray
import org.json.JSONObject
import java.net.URI
import java.net.URLEncoder

/**
 * Reads what the user brings over from Firefox: its extension lists, settings and bookmarks. Android-free, and nothing
 * here changes anything; it only builds a preview for the user to choose from. Only the handful of settings named in
 * [SETTINGS] and [NOT_IMPORTED] are ever looked at, because the same files also hold account and sync details.
 */
internal object GeckoImport {
    const val MAX_FILE = 16 * 1024 * 1024

    data class FoundExtension(val id: String, val name: String, val version: String, val active: Boolean)
    data class Note(val what: String, val why: String)
    /** [key] is one of the settings Gecko's page has; [value] is the saved form of the new choice. */
    data class SettingChange(val key: String, val label: String, val from: String, val to: String, val value: String)
    data class Preview(
        val sources: List<String> = emptyList(),
        val extensions: List<FoundExtension> = emptyList(),
        /** Parts of Firefox that show up as extensions and don't apply here. */
        val builtIn: Int = 0,
        val themes: Int = 0,
        val settings: List<SettingChange> = emptyList(),
        val bookmarks: List<GeckoRules.Link> = emptyList(),
        val notes: List<Note> = emptyList(),
    )

    const val TRACKING = "tracking"
    const val COLORS = "colors"
    const val SEARCH = "search"
    const val HISTORY = "history"

    val SEARCH_ENGINES = listOf("Google" to BrowserRules.DEFAULT_SEARCH, "DuckDuckGo" to "https://duckduckgo.com/?q={query}",
        "Bing" to "https://www.bing.com/search?q={query}", "Brave Search" to "https://search.brave.com/search?q={query}")

    private const val PREF_TRACKING = "browser.contentblocking.category"
    private const val PREF_COLORS = "layout.css.prefers-color-scheme.content-override"
    private const val PREF_SEARCH = "browser.urlbar.placeholderName"
    private const val PREF_HISTORY = "places.history.enabled"
    private val SETTINGS = setOf(PREF_TRACKING, PREF_COLORS, PREF_SEARCH, PREF_HISTORY)

    /** Firefox settings this browser has no matching control for: named so the user knows they weren't carried over. */
    private val NOT_IMPORTED = mapOf(
        "browser.privatebrowsing.autostart" to ("Always use private browsing" to "Private tabs here are opened one at a time, from the menu."),
        "network.trr.mode" to ("DNS over HTTPS" to "This browser has no setting for it yet."),
        "network.cookie.cookieBehavior" to ("Cookie rules" to "This browser always isolates third-party cookies."),
        "browser.startup.homepage" to ("Home page" to "New tabs open Raticode's start page."),
        "signon.rememberSignons" to ("Saving passwords" to "This browser doesn't save passwords."),
        "privacy.globalprivacycontrol.enabled" to ("Global Privacy Control" to "This browser has no setting for it yet."),
        "dom.security.https_only_mode" to ("HTTPS-Only Mode" to "It is always on here."),
    )

    /** Never sent here on purpose: what can't be carried over from Firefox whatever file is chosen. */
    val ALWAYS_UNAVAILABLE = listOf(
        Note("Passwords and sign-ins", "Raticode doesn't read Firefox's password or cookie files. Sign in again on each site."),
        Note("Your Mozilla account and Firefox Sync", "Firefox Sync is only open to services Mozilla has registered, and Raticode isn't one, so it can't read your synced data."),
        Note("Each extension's own settings", "Firefox keeps them in its private storage. Use the extension's own Export or Backup, if it has one, then restore it here."),
    )

    /** What Firefox on this phone can't hand over, even through its troubleshooting data. */
    val PHONE_UNAVAILABLE = listOf(
        Note("Bookmarks from Firefox on this phone", "Firefox for Android doesn't give them to other apps. Export them from Firefox on a computer if you use one, under From a computer."),
        Note("Most Firefox settings on this phone", "Firefox for Android keeps them in its private storage. Only the few it lists in its troubleshooting data can come over."),
    )

    // Firefox installed on this phone ---------------------------------------------------------------------------------

    /**
     * Firefox's own page listing its extensions. On Firefox 159.0.1, opening it from outside with a VIEW link showed
     * "Cannot Complete Request", and shared text, search intents and `fenix://open` didn't reach it either, so the user
     * pastes it into Firefox's address bar.
     */
    const val SUPPORT_PAGE = "about:support"
    const val PHONE_SOURCE = "Firefox on this phone"

    /** Long enough to do this in Firefox; after it the clipboard isn't looked at. */
    const val HANDOVER_MS = 10 * 60 * 1000L

    /** Whether a handover begun at [startedAt] may still read the clipboard at [now], both on the phone's uptime clock. */
    fun handoverOpen(startedAt: Long, now: Long) = startedAt > 0 && now >= startedAt && now - startedAt <= HANDOVER_MS

    /** How long focus must stay before the clipboard is read, because Android can take a moment to hand it over. */
    const val FOCUS_SETTLE_MS = 250L

    /** Calls [check] each time [focus] comes back and stays for [settleMs]. Losing it again during that wait drops the check. */
    suspend fun onFocusBack(focus: Flow<Boolean>, settleMs: Long = FOCUS_SETTLE_MS, check: () -> Unit) {
        focus.distinctUntilChanged().collectLatest { focused -> if (focused) { delay(settleMs); check() } }
    }

    /** Whether [text] is Firefox's troubleshooting data. The clipboard is only ever used for this. */
    fun isTroubleshootingData(text: String): Boolean = text.length <= MAX_FILE && text.trimStart().startsWith("{") &&
        runCatching { JSONObject(text).let { it.has("application") && it.optJSONArray("addons") != null } }.getOrDefault(false)

    /** Whether the phone's Firefox had nothing for this browser to take. */
    fun isEmpty(preview: Preview) = preview.extensions.isEmpty() && preview.settings.isEmpty() && preview.bookmarks.isEmpty()

    /** Why a file is refused by its name alone, before it is opened; null when it may be read. */
    fun refused(name: String): String? {
        val file = name.substringAfterLast('/').lowercase()
        return when {
            file == "places.sqlite" -> "Raticode doesn't open Firefox's history database. Export your bookmarks to HTML in Firefox instead."
            file.startsWith("key3") || file.startsWith("key4") || file.startsWith("logins") || file.startsWith("cert9") || file.startsWith("signedinuser") ->
                "This file holds passwords or account keys, so Raticode won't open it."
            file.endsWith(".sqlite") || file.endsWith(".sqlite-wal") || file.endsWith(".sqlite-shm") || file.endsWith(".db") || file.endsWith(".mozlz4") || file.endsWith(".jsonlz4") ->
                "Raticode doesn't open this kind of Firefox file. Use extensions.json, prefs.js or a bookmarks export."
            else -> null
        }
    }

    /** What [text] holds, whichever Firefox file it came from. [name] only labels the result. */
    fun read(name: String, text: String, current: GeckoRules.Settings, profile: String): Preview {
        val body = text.trimStart(Char(0xFEFF), ' ', '\n', '\r', '\t')
        val preview = when {
            body.startsWith("{") -> runCatching { readJson(JSONObject(body), current, profile) }.getOrNull()
            Regex("(?m)^\\s*user_pref\\(").containsMatchIn(body) -> settings(prefs(body), current)
            body.contains("NETSCAPE-Bookmark-file", ignoreCase = true) || Regex("<a\\s[^>]*href=", RegexOption.IGNORE_CASE).containsMatchIn(body) ->
                Preview(bookmarks = GeckoRules.parseBookmarks(body, profile))
            else -> null
        } ?: return Preview(notes = listOf(Note(name, "Raticode couldn't tell what this is. Use extensions.json, prefs.js or a bookmarks export.")))
        if (preview.extensions.isEmpty() && preview.settings.isEmpty() && preview.bookmarks.isEmpty() && preview.notes.isEmpty() && preview.builtIn == 0 && preview.themes == 0)
            return Preview(notes = listOf(Note(name, "Nothing to bring over was found in it.")))
        return preview.copy(sources = listOf(name))
    }

    private fun readJson(json: JSONObject, current: GeckoRules.Settings, profile: String): Preview? = when {
        // about:support's "Copy raw data": extensions and the settings it reports.
        json.has("application") && json.has("addons") -> {
            val modified = json.optJSONObject("modifiedPreferences")
            val found = modified?.let { map -> (SETTINGS + NOT_IMPORTED.keys).filter(map::has).associateWith { map.get(it) } }.orEmpty()
            addons(json.optJSONArray("addons")).let { list -> merge(list, settings(found, current)) }
        }
        // A Firefox profile's extensions.json or addons.json.
        json.optJSONArray("addons") != null -> addons(json.optJSONArray("addons"))
        // Firefox's bookmarks backup in JSON.
        json.optString("type") == "text/x-moz-place-container" || json.optString("root").isNotEmpty() -> Preview(bookmarks = bookmarks(json, profile))
        else -> null
    }

    private fun JSONArray?.objects(): List<JSONObject> = if (this == null) emptyList() else (0 until length()).mapNotNull { optJSONObject(it) }

    private fun addons(array: JSONArray?): Preview {
        var builtIn = 0; var themes = 0
        val found = ArrayList<FoundExtension>()
        for (item in array.objects()) {
            val id = item.optString("id").takeIf { it.isNotBlank() } ?: continue
            val location = item.optString("location").ifBlank { item.optString("locationName") }
            val type = item.optString("type", "extension")
            when {
                type == "theme" -> themes++
                type != "extension" -> Unit
                location.startsWith("app-builtin") || location.startsWith("app-system") || location == "app-global" ||
                    (location.isEmpty() && (id.endsWith("@mozilla.org") || id.endsWith("@mozilla.com") || id.endsWith("@mozac.org"))) -> builtIn++
                else -> found.add(FoundExtension(id,
                    (item.optJSONObject("defaultLocale")?.optString("name")?.ifBlank { null } ?: item.optString("name")).ifBlank { id }.take(80),
                    item.optString("version").take(40), item.optBoolean("active", item.optBoolean("isActive", true))))
            }
        }
        return Preview(extensions = found, builtIn = builtIn, themes = themes,
            notes = if (themes > 0) listOf(Note("Themes", "This browser doesn't use Firefox themes.")) else emptyList())
    }

    private fun bookmarks(node: JSONObject, profile: String): List<GeckoRules.Link> {
        val out = ArrayList<GeckoRules.Link>()
        fun walk(item: JSONObject, depth: Int) {
            if (depth > 64 || out.size >= GeckoRules.MAX_BOOKMARKS) return
            item.optString("uri").takeIf { BrowserAddress.isWeb(it) }?.let { out.add(GeckoRules.Link(it, item.optString("title").take(BrowserRules.MAX_TITLE), profile)) }
            item.optJSONArray("children").objects().forEach { walk(it, depth + 1) }
        }
        walk(node, 0)
        return out.distinctBy { it.url }
    }

    /** The settings in a prefs.js that this import knows about. Every other line, sync and account details included, is skipped. */
    private fun prefs(text: String): Map<String, Any> {
        val wanted = SETTINGS + NOT_IMPORTED.keys
        val out = HashMap<String, Any>()
        Regex("(?m)^\\s*user_pref\\(\\s*\"([^\"]+)\"\\s*,\\s*(true|false|-?\\d+|\"((?:[^\"\\\\]|\\\\.)*)\")\\s*\\)\\s*;").findAll(text).forEach { match ->
            val name = match.groupValues[1]
            if (name !in wanted) return@forEach
            val raw = match.groupValues[2]
            out[name] = when {
                raw == "true" -> true
                raw == "false" -> false
                raw.startsWith("\"") -> match.groupValues[3].replace("\\\"", "\"").replace("\\\\", "\\")
                else -> raw.toIntOrNull() ?: return@forEach
            }
        }
        return out
    }

    private fun settings(prefs: Map<String, Any>, current: GeckoRules.Settings): Preview {
        val changes = ArrayList<SettingChange>()
        val notes = ArrayList<Note>()
        when (prefs[PREF_TRACKING]) {
            "strict" -> if (current.tracking != GeckoRules.TRACKING_STRICT) changes.add(SettingChange(TRACKING, "Tracking protection", "Standard", "Strict", GeckoRules.TRACKING_STRICT))
            "standard" -> if (current.tracking != GeckoRules.TRACKING_STANDARD) changes.add(SettingChange(TRACKING, "Tracking protection", "Strict", "Standard", GeckoRules.TRACKING_STANDARD))
            "custom" -> notes.add(Note("Custom tracking protection", "This browser offers Standard or Strict."))
        }
        val color = when (prefs[PREF_COLORS]) { 0 -> GeckoRules.COLORS_DARK; 1 -> GeckoRules.COLORS_LIGHT; 2 -> GeckoRules.COLORS_SYSTEM; else -> null }
        if (color != null && color != current.colors) changes.add(SettingChange(COLORS, "Website colors", colorName(current.colors), colorName(color), color))
        (prefs[PREF_SEARCH] as? String)?.let { engine ->
            val match = SEARCH_ENGINES.firstOrNull { it.first.equals(engine, ignoreCase = true) || it.first.substringBefore(' ').equals(engine, ignoreCase = true) }
            when {
                match == null -> notes.add(Note("Search engine: ${engine.take(40)}", "This browser offers ${SEARCH_ENGINES.joinToString(", ") { it.first }}."))
                match.second != current.search -> changes.add(SettingChange(SEARCH, "Search engine", SEARCH_ENGINES.firstOrNull { it.second == current.search }?.first ?: "Custom", match.first, match.second))
            }
        }
        if (prefs[PREF_HISTORY] == false && current.keepHistory) changes.add(SettingChange(HISTORY, "Save browsing history", "On", "Off", "off"))
        NOT_IMPORTED.forEach { (name, label) -> if (name in prefs && meaningful(name, prefs[name])) notes.add(Note(label.first, label.second)) }
        return Preview(settings = changes, notes = notes)
    }

    private fun meaningful(name: String, value: Any?) = when (name) {
        "network.trr.mode" -> value == 2 || value == 3
        "browser.privatebrowsing.autostart", "dom.security.https_only_mode", "privacy.globalprivacycontrol.enabled" -> value == true
        "signon.rememberSignons" -> value == true
        else -> true
    }

    private fun colorName(value: String) = when (value) { GeckoRules.COLORS_DARK -> "Dark"; GeckoRules.COLORS_LIGHT -> "Light"; else -> "Follow the phone" }

    fun apply(settings: GeckoRules.Settings, change: SettingChange): GeckoRules.Settings = when (change.key) {
        TRACKING -> settings.copy(tracking = change.value)
        COLORS -> settings.copy(colors = change.value)
        SEARCH -> settings.copy(search = change.value)
        HISTORY -> settings.copy(keepHistory = false)
        else -> settings
    }

    fun merge(a: Preview, b: Preview) = Preview((a.sources + b.sources).distinct(), (a.extensions + b.extensions).distinctBy { it.id }, a.builtIn + b.builtIn, a.themes + b.themes,
        (b.settings + a.settings).distinctBy { it.key }, (a.bookmarks + b.bookmarks).distinctBy { it.url }.take(GeckoRules.MAX_BOOKMARKS), (a.notes + b.notes).distinct())

    /**
     * Firefox updates some of its own parts into the profile, where they look like add-ons (New Tab does). Mozilla's real
     * extensions use the same address style, so only an ID the site has answered "not listed" for is treated as a part.
     */
    fun withoutFirefoxParts(preview: Preview, listed: Set<String>, checked: Set<String>): Preview {
        val parts = preview.extensions.filter { (it.id.endsWith("@mozilla.org") || it.id.endsWith("@mozilla.com")) && it.id in checked && it.id !in listed }
        return if (parts.isEmpty()) preview else preview.copy(extensions = preview.extensions - parts.toSet(), builtIn = preview.builtIn + parts.size)
    }

    // Mozilla's add-ons site ----------------------------------------------------------------------------------------

    private const val AMO = "addons.mozilla.org"

    /** Search addresses that look up [ids] on Mozilla's site, 25 at a time. Only the IDs leave the phone. */
    fun lookupUrls(ids: List<String>): List<String> = ids.distinct().chunked(25).map { batch ->
        "https://$AMO/api/v5/addons/search/?app=firefox&type=extension&page_size=50&lang=en-US&guid=" + batch.joinToString(",") { URLEncoder.encode(it, "UTF-8") }
    }

    /** The API address of a public collection's page on Mozilla's site, or null when [link] isn't one. */
    fun collectionApi(link: String, page: Int = 1): String? = runCatching {
        val uri = URI(link.trim())
        if (uri.scheme != "https" || uri.host != AMO || uri.port != -1 || uri.userInfo != null) return null
        val match = Regex("^/(?:[A-Za-z-]+/)?(?:firefox/|android/)?collections/([^/]+)/([^/]+)/?$").find(uri.path) ?: return null
        val (user, slug) = match.destructured
        "https://$AMO/api/v5/accounts/account/${URLEncoder.encode(user, "UTF-8")}/collections/${URLEncoder.encode(slug, "UTF-8")}/addons/?page_size=50&lang=en-US&page=$page"
    }.getOrNull()

    fun parseCollection(text: String): List<GeckoRules.Listing> = runCatching {
        JSONObject(text).optJSONArray("results").objects().mapNotNull { it.optJSONObject("addon")?.let(GeckoRules::listing) }
    }.getOrDefault(emptyList())

    fun hasNextPage(text: String): Boolean = runCatching { !JSONObject(text).isNull("next") }.getOrDefault(false)
}
