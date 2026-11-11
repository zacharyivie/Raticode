package dev.raticode.mobile.browser

/**
 * Google Chrome as the phone's web browser. Android can't put Chrome inside another app, so Raticode hands pages to
 * Chrome for Android itself: as a Chrome tab in the Chrome app, or as an Android Custom Tab that Chrome draws over
 * Raticode. Chrome exports no links to its own screens: its settings and sign-in activities aren't exported (checked in
 * Chrome 133's manifest), and its only other scheme, `googlechrome://`, opens a page. Chrome for Android has no
 * extensions. So Raticode offers what Android and Chrome really allow: pages, opening Chrome, Google's sign-in page in
 * Chrome, Chrome's Android app settings and its Google Play page, and says where Chrome's own sign-in and sync are
 * (Settings › Sign in, at the top of Chrome's settings in Chrome 133). Raticode never sees what the user does in Chrome, never handles their Google password, and reads
 * nothing from Chrome's profile.
 */
object ChromeApps {
    const val ENGINE = "chrome"

    /**
     * SHA-256 of the certificate Google signs Chrome (`com.android.chrome`) with, "CN=Android, OU=Android, O=Google Inc.":
     * read with `apksigner` from Chrome 133.0.6943.137 on the emulator's Google APIs image, and the same certificate
     * Google lists for Chrome in its Credential Manager browser list (gstatic.com/gpm-passkeys-privileged-apps/apps.json)
     * and AppAuth-Android lists as Chrome's signature.
     */
    const val GOOGLE_CHROME_CERT = "f0fd6c5b410f25cb25c3b53346c8972fae30f8ee7411df910480ad6b2d60db83"

    /**
     * SHA-256 of the certificate Google signs Chrome Beta (`com.chrome.beta`) with, "CN=chrome_beta, O=Google Inc.":
     * read with `apksigner` from Chrome Beta 158.0.8127.22, and listed for Chrome Beta in Google's Credential Manager list.
     */
    const val GOOGLE_CHROME_BETA_CERT = "da633d34b69e63ae2103b49d53ce052fc5f7f3c53aab94fdc2a208bdfd14249c"

    /** Chrome Dev and Canary aren't offered: their certificates weren't read from a package. */
    val EDITIONS = listOf(
        BrowserApp.Edition("com.android.chrome", "Chrome", listOf(GOOGLE_CHROME_CERT)),
        BrowserApp.Edition("com.chrome.beta", "Chrome Beta", listOf(GOOGLE_CHROME_BETA_CERT)),
    )

    /** Google's sign-in page, opened in Chrome like any other page. Google and Chrome handle the account from there. */
    const val SIGN_IN_PAGE = "https://accounts.google.com/"

    val APP = BrowserApp(
        engine = ENGINE, name = "Chrome", maker = "Google", editions = EDITIONS, store = "com.android.chrome",
        signIn = BrowserApp.SignIn(SIGN_IN_PAGE, "Sign in to Google",
            "Opens Google's sign-in page in Chrome. Chrome's own sign-in and sync are under Sign in at the top of Chrome's Settings (menu ⋮)"),
        limits = "Chrome for Android has no extensions, and it doesn't let other apps open its settings or sign-in screens, so change those in Chrome: menu ⋮ › Settings.",
    )
}
