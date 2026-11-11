package dev.raticode.mobile.browser

/**
 * One tab list for Raticode's two built-in browsers, Raticode (chromium) ([BrowserRuntime], Android's WebView) and
 * Raticode (gecko) ([GeckoBrowser], Mozilla's GeckoView). Each browser keeps its own tabs, private tabs, history, downloads,
 * sign-ins and rules; this only lists them together and names them. Android-free, so plain JUnit can test it.
 *
 * Firefox, Chrome and Edge are separate apps on Android. Raticode can hand them a page, but can't hold or list their tabs,
 * so they never appear here.
 */
object BrowserTabs {
    const val CHROMIUM = BrowserRules.ENGINE_RATICODE
    const val GECKO = BrowserRules.ENGINE_GECKO
    const val CHROMIUM_NAME = "Raticode (chromium)"
    const val GECKO_NAME = "Raticode (gecko)"

    /** The name people see for a browser value, as on the desktop: "Raticode (chromium)", "Raticode (gecko)", "Firefox"... */
    fun name(engine: String?): String = when (engine) {
        GECKO -> GECKO_NAME
        BrowserRules.ENGINE_FIREFOX -> "Firefox"
        BrowserRules.ENGINE_EDGE -> "Edge"
        else -> engine?.let(BrowserApps::byEngine)?.name ?: CHROMIUM_NAME
    }

    /** The short badge on a tab in the list: "chromium" or "gecko" for the built-in browsers, as on the desktop's tab bar. */
    fun badge(engine: String?): String = when (engine) {
        CHROMIUM -> "chromium"
        GECKO -> "gecko"
        else -> name(engine)
    }

    /** One tab as its browser reports it. [playing]: its page plays sound now. */
    data class Tab(val engine: String, val id: String, val url: String, val title: String, val private: Boolean = false, val playing: Boolean = false)

    /** A row of the shared list. [current]: the tab on screen, in the browser the Browser page shows. */
    data class Entry(val tab: Tab, val current: Boolean) {
        val key get() = tab.engine + ":" + tab.id
    }

    /**
     * The shared list: Raticode (chromium)'s tabs, then Raticode (gecko)'s, each in the order they were opened, the way a
     * tab bar reads, so a tab keeps its place while the user switches between them.
     *
     * A blank, non-private tab of the browser that isn't on screen is left out: each browser always keeps one tab, so the
     * one the user never used shouldn't fill the list. A blank tab on screen, and a private one, always show; a hidden
     * private tab would keep private data alive with no way to close it.
     */
    fun list(chromium: List<Tab>, gecko: List<Tab>, showing: String, chromiumActive: String?, geckoActive: String?): List<Entry> {
        fun of(engine: String, tabs: List<Tab>, active: String?) = tabs.filter { it.engine == engine }.mapNotNull { tab ->
            val current = engine == showing && tab.id == active
            if (engine != showing && tab.url == BrowserRules.HOME && !tab.private && !tab.playing) null else Entry(tab, current)
        }
        return of(CHROMIUM, chromium, chromiumActive) + of(GECKO, gecko, geckoActive)
    }

    /** Where the list opens: at the tab on screen, or the top. */
    fun currentIndex(entries: List<Entry>): Int = entries.indexOfFirst { it.current }.coerceAtLeast(0)

    /** The second line of a row: private and playing, then the site. The badge names the browser. */
    fun detail(entry: Entry): String? = listOfNotNull("Private".takeIf { entry.tab.private }, "Playing".takeIf { entry.tab.playing },
        "Current".takeIf { entry.current }, BrowserRules.host(entry.tab.url).ifBlank { null }).joinToString(" · ").ifBlank { null }

    /** A row's title: the page's title, or its site, or "New tab". */
    fun title(tab: Tab): String = if (tab.url == BrowserRules.HOME) "New tab" else BrowserRules.title(tab.title, tab.url)
}
