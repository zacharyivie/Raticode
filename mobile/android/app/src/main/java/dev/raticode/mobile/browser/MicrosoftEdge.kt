package dev.raticode.mobile.browser

/**
 * Microsoft Edge as the phone's web browser, one of [BrowserApps]. Android can't put Edge inside another app, so Raticode
 * hands pages to Edge for Android itself: as a tab in the Edge app, or as an Android Custom Tab that Edge draws over
 * Raticode. Edge for Android documents no links that other apps may use to open its settings, account sign-in or
 * extensions, so the Browser page lists those as [BrowserApp.places]: each opens Edge and says where to tap in Edge's
 * menu (the "…" button on its toolbar). Raticode never sees what the user does in Edge, never handles their Microsoft
 * account password, and reads nothing from Edge's profile.
 */
object MicrosoftEdge {
    const val ENGINE = "edge"

    /**
     * SHA-256 of the certificates Microsoft signs Edge for Android with. Microsoft's own Digital Asset Links file lists
     * these two, and only these, for `com.microsoft.emmx`, `.beta`, `.dev` and `.canary`:
     * https://login.microsoftonline.com/.well-known/assetlinks.json (the same entries are served by
     * https://login.microsoft.com/.well-known/assetlinks.json and https://login.windows.net/.well-known/assetlinks.json,
     * read November 7, 2026). `com.microsoft.emmx.local`, listed there with a different key, is a developer build and
     * isn't offered. Edge is genuine when Android confirms it is signed with either one (the app's current certificate or
     * one in its rotation history), as Android's own link verification accepts either. A look-alike installed under
     * Edge's name gets no pages or links.
     */
    val MICROSOFT_CERTS = listOf(
        "01e1999710a82c2749b4d50c445dc85d670b6136089d0a766a73827c82a1eac9",
        "f6a17139956be7c7dd0e537710025dd21690ae805b20b619eed9d6678ac46b28",
    )

    /** In order of preference: release first, then Beta, Dev and Canary, each signed with either of [MICROSOFT_CERTS]. */
    val EDITIONS = listOf(
        BrowserApp.Edition("com.microsoft.emmx", "Microsoft Edge", MICROSOFT_CERTS),
        BrowserApp.Edition("com.microsoft.emmx.beta", "Microsoft Edge Beta", MICROSOFT_CERTS),
        BrowserApp.Edition("com.microsoft.emmx.dev", "Microsoft Edge Dev", MICROSOFT_CERTS),
        BrowserApp.Edition("com.microsoft.emmx.canary", "Microsoft Edge Canary", MICROSOFT_CERTS),
    )

    /** Edge's own screens, first the account sign-in. Edge has no documented links for them. */
    val PLACES = listOf(
        BrowserApp.Place("Sign in to Edge", "Your Microsoft account: sync favorites, passwords, history and open tabs with Edge on your other devices",
            "In Edge, tap your profile picture, or the menu (…) › Settings › Accounts, then sign in with your Microsoft account."),
        BrowserApp.Place("Edge settings", "Everything Edge lets you change, including passwords, privacy, search and clearing browsing data",
            "In Edge, tap the menu (…) › Settings."),
        BrowserApp.Place("Extensions", "Edge's extensions for Android, where your Edge version offers them",
            "In Edge, tap the menu (…) › Extensions."),
        BrowserApp.Place("Favorites", "Edge's bookmarks, synced when you're signed in", "In Edge, tap the menu (…) › Favorites."),
        BrowserApp.Place("History", "Edge's history", "In Edge, tap the menu (…) › History."),
        BrowserApp.Place("InPrivate browsing", "Edge's private tabs", "In Edge, open the tab list and choose InPrivate."),
    )

    val APP = BrowserApp(
        engine = ENGINE, name = "Edge", maker = "Microsoft", editions = EDITIONS, store = "com.microsoft.emmx",
        signIn = null,
        limits = "Edge for Android doesn't let other apps open its settings, sign-in or extensions, so change those in Edge: menu (…) › Settings.",
        places = PLACES,
        beside = true,
    )
}
