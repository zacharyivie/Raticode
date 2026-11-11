package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject

/**
 * The computer's browser as the phone's browser sees it: the web tabs open in the desktop window (`browser.tabs`) and
 * pages the user sends to a new background tab there (`browser.open`). The desktop keeps both in memory, drops previews
 * and local files, and opens a sent page only in the user's own profile. Nothing here is saved on the phone.
 */
object DesktopBrowser {
    const val TABS = "browser.tabs"
    const val OPEN = "browser.open"
    const val UPDATE_DESKTOP = "Update Raticode on your computer to see its browser tabs and send pages to it."

    /**
     * [engine] is the browser running this tab on the computer: "raticode", "gecko", "firefox", "chrome" or "edge". A
     * desktop that names each tab's browser sets [ownEngine]; an older one doesn't, and every tab takes [View.engine].
     */
    data class Tab(val id: String, val title: String, val url: String, val owner: String, val label: String, val active: Boolean,
                   val engine: String = "raticode", val ownEngine: Boolean = false) {
        val rem get() = owner == "rem"
    }

    /** Browsers the computer's web tabs can run in besides Raticode's own engine, by `engine` value, with their names. */
    val NAMES = mapOf("firefox" to "Firefox", "edge" to "Microsoft Edge", "chrome" to "Chrome")

    /** Every browser a computer tab can name, by `engine` value, with the name its badge shows (as on the desktop). */
    val TAB_ENGINES = mapOf("raticode" to "Raticode (chromium)", "gecko" to "Raticode (gecko)", "firefox" to "Firefox", "chrome" to "Chrome", "edge" to "Edge")

    /** The badge for [tab]: the name of the browser it runs in on the computer. */
    fun engineName(tab: Tab): String = TAB_ENGINES[tab.engine] ?: TAB_ENGINES.getValue("raticode")

    /**
     * [engine] is "firefox", "edge" or "chrome" when the computer's web tabs run in that browser there; an older desktop
     * says nothing, and a value this phone doesn't know reads as "raticode".
     */
    data class View(val available: Boolean, val stale: Boolean, val detail: String, val tabs: List<Tab>, val searchUrl: String, val pending: Int,
                    val engine: String = "raticode") {
        val firefox get() = engine == "firefox"
        val edge get() = engine == "edge"
        /** The computer names each tab's browser, so its tabs can come from several browsers at once. */
        val perTabEngines get() = tabs.any { it.ownEngine }
    }

    /** The desktop's answer to `browser.open`: a sentence to show. */
    data class Sent(val message: String)

    fun parse(json: JSONObject): View {
        val list = json.optJSONArray("tabs") ?: JSONArray()
        // The computer's default browser for tabs Rem and this phone open there; each tab may name its own (see Tab.engine).
        val engine = json.optString("engine").takeIf { it in NAMES } ?: "raticode"
        val tabs = (0 until minOf(list.length(), 40)).mapNotNull { list.optJSONObject(it) }.mapNotNull { t ->
            val url = t.optString("url").takeIf { BrowserAddress.isWeb(it) } ?: return@mapNotNull null
            // A value this phone doesn't know yet reads as Raticode (chromium), like the top-level one.
            val own = t.optString("engine").takeIf { it.isNotEmpty() }
            Tab(t.optString("tab_id").take(120), t.optString("title").take(200), url,
                if (t.optString("owner") == "rem") "rem" else "you", t.optString("label").take(60), t.optBoolean("active"),
                engine = if (own == null) engine else own.takeIf { it in TAB_ENGINES } ?: "raticode", ownEngine = own != null)
        }
        val search = json.optString("search_url").takeIf { "{query}" in it && BrowserAddress.isWeb(it.replace("{query}", "q")) }.orEmpty()
        return View(json.optBoolean("available"), json.optBoolean("stale"), json.optString("detail").take(300), tabs, search, json.optInt("pending"), engine)
    }

    fun parseSent(json: JSONObject) = Sent(json.optString("message").take(300).ifBlank { "Sent to your computer." })

    /** A desktop downgraded since the last connection would close the session on the unknown read. */
    fun dropQueuedReads(state: JSONObject): Boolean {
        val outbox = state.optJSONArray("outbox") ?: return false
        val kept = JSONArray()
        for (i in 0 until outbox.length()) {
            val event = JSONObject(outbox.getString(i))
            val op = event.optJSONObject("payload")?.optString("op").orEmpty()
            if (event.optString("type") != "desktop.read" || op != TABS) kept.put(outbox.getString(i))
        }
        if (kept.length() == outbox.length()) return false
        state.put("outbox", kept)
        return true
    }
}

/** Web addresses the phone's browser and the desktop's both accept: http or https with a host and no user name. */
object BrowserAddress {
    /** Addresses sent to or from the computer, saved or shown in lists. Pages may navigate to far longer ones. */
    const val MAX = 2048

    private val START = Regex("^([A-Za-z][A-Za-z0-9+.-]*)://([^/?#]*)")

    /**
     * Lenient like a browser's address bar: only the scheme and host are checked, so query characters such as `|` pass.
     * [max] is [MAX] for anything sent, saved or listed; page navigation passes Chromium's own limit instead.
     */
    fun isWeb(url: String, max: Int = MAX): Boolean {
        if (url.isBlank() || url.length > max || url.any { it <= ' ' || it == '\u007f' }) return false
        val match = START.find(url) ?: return false
        if (match.groupValues[1].lowercase() !in setOf("http", "https")) return false
        val authority = match.groupValues[2]
        if ('@' in authority || authority.isEmpty()) return false
        val (host, port) = if (authority.startsWith("[")) {
            val end = authority.indexOf(']')
            if (end < 2) return false
            authority.substring(1, end) to authority.substring(end + 1).removePrefix(":").takeIf { authority.length > end + 1 }
        } else authority.substringBefore(':') to authority.substringAfter(':', "").takeIf { ':' in authority }
        if (host.isBlank()) return false
        return port == null || (port.isNotEmpty() && port.length <= 5 && port.all(Char::isDigit) && port.toInt() in 1..65535)
    }
}
