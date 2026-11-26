package dev.raticode.mobile.browser

/**
 * Firefox as the phone's web browser, one of [BrowserApps]. Android can't put Firefox inside another app (GeckoView would
 * be a separate engine without Firefox's account, sync or settings), so Raticode hands pages to Firefox for Android itself:
 * as a tab in the Firefox app, or as an Android Custom Tab that Firefox draws over Raticode. Firefox's own screens (the
 * Mozilla account sign-in, settings, add-ons, privacy, passwords, bookmarks and history) open through the deep links
 * Firefox exports for other apps (`fenix://turn_on_sync`, `fenix://settings` and so on, registered in Fenix's manifest).
 * Only the hosts in [HOSTS] are ever sent. Raticode never sees what the user does there, never handles their Firefox
 * password, and reads nothing from Firefox's profile.
 */
object Firefox {
    const val ENGINE = "firefox"

    /**
     * SHA-256 of the certificate Mozilla signs Firefox and Firefox Beta for Android with ("Release Engineering, Mozilla
     * Corporation"), read from Mozilla's own 159.0.1 and 160.0b5 packages. Another app can't take Firefox's package name
     * while Firefox is installed, but a look-alike installed in its place could; it gets no pages or links.
     */
    const val MOZILLA_CERT_SHA256 = "a78b62a5165b4494b2fead9e76a280d22d937fee6251aece599446b2ea319b04"

    /**
     * In order of preference, each with Fenix's `deepLinkScheme` for its release type. Nightly isn't offered: its signing
     * key wasn't checked, and Raticode only hands pages to an app Mozilla signed.
     */
    val EDITIONS = listOf(
        BrowserApp.Edition("org.mozilla.firefox", "Firefox", listOf(MOZILLA_CERT_SHA256), scheme = "fenix"),
        BrowserApp.Edition("org.mozilla.firefox_beta", "Firefox Beta", listOf(MOZILLA_CERT_SHA256), scheme = "fenix-beta"),
    )

    /** Firefox's sign-in to a Mozilla account, its own screen. */
    val SIGN_IN = BrowserApp.SignIn("Sign in to Firefox",
        "Your Mozilla account: sync bookmarks, passwords, open tabs and add-ons with Firefox on your other devices", host = "turn_on_sync")

    /** Firefox's other screens Raticode offers, each by its deep-link host. */
    val PLACES = listOf(
        BrowserApp.Place("Firefox settings", "Everything Firefox lets you change, in Firefox", host = "settings"),
        BrowserApp.Place("Add-ons", "Find, install and manage Firefox extensions", host = "settings_addon_manager"),
        BrowserApp.Place("Tracking protection", "Standard, strict or custom blocking", host = "settings_tracking_protection"),
        BrowserApp.Place("Search engine", "Firefox's default search and suggestions", host = "settings_search_engine"),
        BrowserApp.Place("Passwords", "Saved logins, protected by Firefox", host = "settings_logins"),
        BrowserApp.Place("Delete browsing data", "Firefox's tabs, history, cookies and cache", host = "settings_delete_browsing_data"),
        BrowserApp.Place("Bookmarks", "Firefox's bookmarks, synced when you're signed in", host = "urls_bookmarks"),
        BrowserApp.Place("History", "Firefox's history", host = "urls_history"),
        BrowserApp.Place("Private browsing", "Firefox's private tabs", host = "enable_private_browsing"),
    )

    /**
     * Every deep-link host Raticode may send Firefox: the screens above and Firefox's home. Written out rather than
     * derived, so a screen added above without adding its host here is never sent. Hosts such as "open" (loads any
     * address with no say from the user) and "share_sheet" are left out on purpose.
     */
    val HOSTS = setOf("turn_on_sync", "settings", "settings_addon_manager", "settings_tracking_protection", "settings_search_engine",
        "settings_logins", "settings_delete_browsing_data", "urls_bookmarks", "urls_history", "enable_private_browsing", "home")

    val APP = BrowserApp(
        engine = ENGINE, name = "Firefox", maker = "Mozilla", editions = EDITIONS, store = "org.mozilla.firefox",
        signIn = SIGN_IN,
        limits = "Firefox opens the screens listed here when Raticode asks; everything else is in Firefox's menu ⋮.",
        places = PLACES,
        beside = true,
        linkHosts = HOSTS,
    )
}
