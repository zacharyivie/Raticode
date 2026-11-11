package dev.raticode.mobile.browser

import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import androidx.core.net.toUri
import dev.raticode.mobile.live.BrowserAddress

/**
 * Firefox as the phone's web browser. Android can't put Firefox inside another app (GeckoView would be a separate
 * engine without Firefox's account, sync or settings), so Raticode hands pages to Firefox for Android itself: as a
 * Firefox tab over Raticode (Android Custom Tabs) or in the Firefox app. Firefox's own screens (settings, the Mozilla
 * account sign-in, add-ons, privacy, passwords, bookmarks and history) open through the deep links Firefox exports
 * for other apps (`fenix://settings`, `fenix://turn_on_sync` and so on, registered in Fenix's manifest). Raticode never
 * sees what the user does there, never handles their Firefox password, and reads nothing from Firefox's profile.
 */
object FirefoxApps {
    /** A Firefox release type: its package, the deep-link scheme it registers, and its name. */
    data class Edition(val pkg: String, val scheme: String, val name: String)

    /**
     * In order of preference. Scheme values are Fenix's `deepLinkScheme` for each release type. Nightly isn't offered:
     * its signing key wasn't checked, and Raticode only hands pages to an app Mozilla signed.
     */
    val BUILDS = listOf(
        Edition("org.mozilla.firefox", "fenix", "Firefox"),
        Edition("org.mozilla.firefox_beta", "fenix-beta", "Firefox Beta"),
    )

    /**
     * SHA-256 of the certificate Mozilla signs Firefox and Firefox Beta for Android with ("Release Engineering, Mozilla
     * Corporation"), read from Mozilla's own 159.0.1 and 160.0b5 packages. Another app can't take Firefox's package name
     * while Firefox is installed, but a look-alike installed in its place could; it gets no pages or links.
     */
    const val MOZILLA_CERT_SHA256 = "a78b62a5165b4494b2fead9e76a280d22d937fee6251aece599446b2ea319b04"
    const val NOT_MOZILLA = "The app named Firefox on this phone isn't signed by Mozilla, so Raticode won't send it pages."

    fun certBytes(hex: String = MOZILLA_CERT_SHA256): ByteArray = ByteArray(hex.length / 2) { hex.substring(it * 2, it * 2 + 2).toInt(16).toByte() }

    /** Firefox screens Raticode offers, as (deep-link host, label, what it does). Only these hosts are ever sent. */
    data class Screen(val host: String, val label: String, val detail: String)
    val SIGN_IN = Screen("turn_on_sync", "Sign in to Firefox",
        "Your Mozilla account: sync bookmarks, passwords, open tabs and add-ons with Firefox on your other devices")
    val SCREENS = listOf(
        SIGN_IN,
        Screen("settings", "Firefox settings", "Everything Firefox lets you change, in Firefox"),
        Screen("settings_addon_manager", "Add-ons", "Find, install and manage Firefox extensions"),
        Screen("settings_tracking_protection", "Tracking protection", "Standard, strict or custom blocking"),
        Screen("settings_search_engine", "Search engine", "Firefox's default search and suggestions"),
        Screen("settings_logins", "Passwords", "Saved logins, protected by Firefox"),
        Screen("settings_delete_browsing_data", "Delete browsing data", "Firefox's tabs, history, cookies and cache"),
        Screen("urls_bookmarks", "Bookmarks", "Firefox's bookmarks, synced when you're signed in"),
        Screen("urls_history", "History", "Firefox's history"),
        Screen("enable_private_browsing", "Private browsing", "Firefox's private tabs"),
    )
    private val HOSTS = SCREENS.map { it.host }.toSet() + "home"

    /** The deep link for [host] in [build], or null for a host Raticode doesn't offer. */
    fun deepLink(build: Edition, host: String): String? = if (host in HOSTS) "${build.scheme}://$host" else null

    /** The preferred installed build: release first, then Beta. */
    fun preferred(installed: Collection<String>): Edition? = BUILDS.firstOrNull { it.pkg in installed }

    /** Pages Firefox opens for Raticode: web addresses only. Firefox applies its own rules, including HTTPS-only mode. */
    fun openable(url: String): Boolean = BrowserAddress.isWeb(url)

    const val MISSING = "Firefox isn't installed on this phone."
    const val TURNED_OFF = "Firefox is turned off in Android settings."
    const val PLAY_PAGE = "https://play.google.com/store/apps/details?id=org.mozilla.firefox"

    // Android ---------------------------------------------------------------------------------------------------------

    /** Why [found] can't take pages or screens, in the page's words, or null when it can. */
    fun problem(found: Found?): String? = when {
        found == null -> MISSING
        !found.genuine -> NOT_MOZILLA
        !found.enabled -> TURNED_OFF
        else -> null
    }

    /** An installed Firefox. [genuine] means Mozilla signed it; nothing is sent to one that isn't. */
    data class Found(val build: Edition, val version: String, val enabled: Boolean, val genuine: Boolean = true) {
        val usable get() = enabled && genuine
    }

    /** The installed Firefox, if any. Only Firefox's own packages are visible to Raticode (manifest `<queries>`). */
    fun find(context: Context): Found? {
        val pm = context.packageManager
        val found = BUILDS.mapNotNull { build ->
            val info = try {
                if (android.os.Build.VERSION.SDK_INT >= 33) pm.getPackageInfo(build.pkg, PackageManager.PackageInfoFlags.of(0))
                else @Suppress("DEPRECATION") pm.getPackageInfo(build.pkg, 0)
            } catch (_: PackageManager.NameNotFoundException) { null } ?: return@mapNotNull null
            val genuine = android.os.Build.VERSION.SDK_INT >= 28 &&
                pm.hasSigningCertificate(build.pkg, certBytes(), PackageManager.CERT_INPUT_SHA256)
            Found(build, info.versionName.orEmpty(), info.applicationInfo?.enabled != false, genuine)
        }
        return found.firstOrNull { it.usable } ?: found.firstOrNull()
    }

    /**
     * How Firefox shows a page. "app" (the default) opens it as a tab in the Firefox app, in Firefox's own task: Raticode
     * stays a separate app with its bottom bar, and switching between the two (Android's Recents, or Return to Firefox)
     * leaves Firefox's tabs and anything playing alone. "tab" is an Android Custom Tab: Firefox draws the page on top of
     * Raticode's own screens, so Raticode's bar can't show while it is open, and its ✕ closes the page and its sound.
     */
    const val MODE_TAB = "tab"
    const val MODE_APP = "app"

    /** Opens [url] in [found]. Returns false when Firefox didn't take it. */
    fun open(context: Context, found: Found, url: String, mode: String): Boolean {
        if (!openable(url) || !found.usable) return false
        val intent = Intent(Intent.ACTION_VIEW, url.toUri()).setPackage(found.build.pkg).addCategory(Intent.CATEGORY_BROWSABLE)
        // Firefox's own task, never Raticode's: going back to Raticode can't close the page.
        if (mode != MODE_TAB) intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (mode == MODE_TAB) {
            // The Custom Tabs protocol, written out so no extra library is needed: a session extra (here empty) marks it.
            intent.putExtras(Bundle().apply { putBinder("android.support.customtabs.extra.SESSION", null) })
            intent.putExtra("android.support.customtabs.extra.TITLE_VISIBILITY", 1)
            intent.putExtra("androidx.browser.customtabs.extra.SHARE_STATE", 1)
        }
        return start(context, intent)
    }

    /** Opens one of Firefox's own screens. An older Firefox without that screen opens on its home page instead. */
    fun openScreen(context: Context, found: Found, host: String): Boolean {
        if (!found.usable) return false
        val link = deepLink(found.build, host) ?: return false
        if (start(context, Intent(Intent.ACTION_VIEW, link.toUri()).setPackage(found.build.pkg))) return true
        return launch(context, found)
    }

    /**
     * Brings Firefox back where the user left it: its launcher entry resumes Firefox's existing task with the open tab,
     * rather than opening a new one.
     */
    fun launch(context: Context, found: Found): Boolean {
        if (!found.usable) return false
        val intent = context.packageManager.getLaunchIntentForPackage(found.build.pkg) ?: return false
        return start(context, intent)
    }

    /**
     * Firefox in the other half of a split screen, beside Raticode and its bottom bar. Android places it there only
     * while Raticode is itself in split screen; otherwise Firefox opens full screen, as [launch] does.
     */
    fun launchBeside(context: Context, found: Found): Boolean {
        if (!found.usable) return false
        val intent = context.packageManager.getLaunchIntentForPackage(found.build.pkg) ?: return false
        return start(context, intent.addFlags(Intent.FLAG_ACTIVITY_LAUNCH_ADJACENT or Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    /** Opens Firefox's page in Google Play, or on the web when the phone has no store. */
    fun getFirefox(context: Context): Boolean {
        val store = Intent(Intent.ACTION_VIEW, "market://details?id=org.mozilla.firefox".toUri())
        return start(context, store) || start(context, Intent(Intent.ACTION_VIEW, PLAY_PAGE.toUri()).addCategory(Intent.CATEGORY_BROWSABLE))
    }

    /** Android's app settings for Firefox, where it can be turned back on. */
    fun appSettings(context: Context, found: Found): Boolean =
        start(context, Intent(android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS, "package:${found.build.pkg}".toUri()))

    private fun start(context: Context, intent: Intent): Boolean {
        if (context !is Activity) intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return try { context.startActivity(intent); true } catch (_: ActivityNotFoundException) { false } catch (_: SecurityException) { false }
    }

}
