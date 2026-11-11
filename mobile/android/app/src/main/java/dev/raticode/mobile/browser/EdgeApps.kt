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
 * Microsoft Edge as the phone's web browser. Like Firefox, Edge can't be put inside another app, so Raticode hands
 * pages to Edge for Android itself: as an Edge tab over Raticode (Android Custom Tabs) or in the Edge app. Unlike
 * Firefox, Edge for Android documents no links that other apps may use to open its settings, account sign-in or
 * extensions, so Raticode only opens Edge and says where each of those is in Edge's menu. Raticode never sees what the
 * user does there, never handles their Microsoft account password, and reads nothing from Edge's profile.
 */
object EdgeApps {
    /** An Edge release type: its package and its name. */
    data class Edition(val pkg: String, val name: String)

    /** In order of preference. Each is listed with Microsoft's signing certificates in [MICROSOFT_CERTS_SHA256]'s source. */
    val BUILDS = listOf(
        Edition("com.microsoft.emmx", "Microsoft Edge"),
        Edition("com.microsoft.emmx.beta", "Microsoft Edge Beta"),
        Edition("com.microsoft.emmx.dev", "Microsoft Edge Dev"),
        Edition("com.microsoft.emmx.canary", "Microsoft Edge Canary"),
    )

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
    val MICROSOFT_CERTS_SHA256 = listOf(
        "01e1999710a82c2749b4d50c445dc85d670b6136089d0a766a73827c82a1eac9",
        "f6a17139956be7c7dd0e537710025dd21690ae805b20b619eed9d6678ac46b28",
    )
    const val NOT_MICROSOFT = "The app named Microsoft Edge on this phone isn't signed by Microsoft, so Raticode won't send it pages."

    fun certBytes(hex: String): ByteArray = ByteArray(hex.length / 2) { hex.substring(it * 2, it * 2 + 2).toInt(16).toByte() }

    /** Whether a certificate digest, as hex with or without colons in any case, is one of Microsoft's for Edge. */
    fun pinned(digest: String): Boolean = digest.replace(":", "").lowercase() in MICROSOFT_CERTS_SHA256

    /**
     * Edge's own places Raticode points to, as (label, what it is, where it is in Edge). Edge has no documented links for
     * them, so each one opens Edge and the page says where to tap. Edge's menu is the "…" button on its toolbar.
     */
    data class Place(val label: String, val detail: String, val where: String)
    val SIGN_IN = Place("Sign in to Edge", "Your Microsoft account: sync favorites, passwords, history and open tabs with Edge on your other devices",
        "In Edge, tap your profile picture, or the menu (…) › Settings › Accounts, then sign in with your Microsoft account.")
    val PLACES = listOf(
        SIGN_IN,
        Place("Edge settings", "Everything Edge lets you change, including passwords, privacy, search and clearing browsing data",
            "In Edge, tap the menu (…) › Settings."),
        Place("Extensions", "Edge's extensions for Android, where your Edge version offers them",
            "In Edge, tap the menu (…) › Extensions."),
        Place("Favorites", "Edge's bookmarks, synced when you're signed in", "In Edge, tap the menu (…) › Favorites."),
        Place("History", "Edge's history", "In Edge, tap the menu (…) › History."),
        Place("InPrivate browsing", "Edge's private tabs", "In Edge, open the tab list and choose InPrivate."),
    )

    /** The preferred installed build: release first, then Beta, Dev and Canary. */
    fun preferred(installed: Collection<String>): Edition? = BUILDS.firstOrNull { it.pkg in installed }

    /** Pages Edge opens for Raticode: web addresses only. Edge applies its own rules to every page. */
    fun openable(url: String): Boolean = BrowserAddress.isWeb(url)

    const val MISSING = "Microsoft Edge isn't installed on this phone."
    const val TURNED_OFF = "Microsoft Edge is turned off in Android settings."
    const val PLAY_PAGE = "https://play.google.com/store/apps/details?id=com.microsoft.emmx"

    // Android ---------------------------------------------------------------------------------------------------------

    /** Why [found] can't take pages, in the page's words, or null when it can. */
    fun problem(found: Found?): String? = when {
        found == null -> MISSING
        !found.genuine -> NOT_MICROSOFT
        !found.enabled -> TURNED_OFF
        else -> null
    }

    /** An installed Edge. [genuine] means Microsoft signed it; nothing is sent to one that isn't. */
    data class Found(val build: Edition, val version: String, val enabled: Boolean, val genuine: Boolean = true) {
        val usable get() = enabled && genuine
    }

    /** The installed Edge, if any. Only Edge's own packages are visible to Raticode (manifest `<queries>`). */
    fun find(context: Context): Found? {
        val pm = context.packageManager
        val found = BUILDS.mapNotNull { build ->
            val info = try {
                if (android.os.Build.VERSION.SDK_INT >= 33) pm.getPackageInfo(build.pkg, PackageManager.PackageInfoFlags.of(0))
                else @Suppress("DEPRECATION") pm.getPackageInfo(build.pkg, 0)
            } catch (_: PackageManager.NameNotFoundException) { null } ?: return@mapNotNull null
            val genuine = android.os.Build.VERSION.SDK_INT >= 28 &&
                MICROSOFT_CERTS_SHA256.any { pm.hasSigningCertificate(build.pkg, certBytes(it), PackageManager.CERT_INPUT_SHA256) }
            Found(build, info.versionName.orEmpty(), info.applicationInfo?.enabled != false, genuine)
        }
        return found.firstOrNull { it.usable } ?: found.firstOrNull()
    }

    /**
     * How Edge shows a page. "app" (the default, as for Firefox) opens it as a tab in the Edge app, in Edge's own task:
     * Raticode stays a separate app with its bottom bar, and switching between the two (Android's Recents, or Return to
     * Edge) leaves Edge's tabs and anything playing alone. "tab" is an Android Custom Tab drawn over Raticode's screens;
     * its ✕ closes the page and its sound.
     */
    const val MODE_TAB = "tab"
    const val MODE_APP = "app"

    /**
     * Opens [url] in [found]. An Edge that ignores the Custom Tabs extras of "tab" shows the page in the Edge app instead.
     * Returns false when Edge didn't take it.
     */
    fun open(context: Context, found: Found, url: String, mode: String): Boolean {
        if (!openable(url) || !found.usable) return false
        val intent = Intent(Intent.ACTION_VIEW, url.toUri()).setPackage(found.build.pkg).addCategory(Intent.CATEGORY_BROWSABLE)
        if (mode == MODE_TAB) {
            // The Custom Tabs protocol, written out so no extra library is needed: a session extra (here empty) marks it.
            intent.putExtras(Bundle().apply { putBinder("android.support.customtabs.extra.SESSION", null) })
            intent.putExtra("android.support.customtabs.extra.TITLE_VISIBILITY", 1)
            intent.putExtra("androidx.browser.customtabs.extra.SHARE_STATE", 1)
        }
        // Edge's own task, never Raticode's: going back to Raticode can't close the page.
        if (mode != MODE_TAB) intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return start(context, intent)
    }

    /** Opens Edge where the user left it; its settings, sign-in and extensions are in its menu. */
    fun launch(context: Context, found: Found): Boolean {
        if (!found.usable) return false
        val intent = context.packageManager.getLaunchIntentForPackage(found.build.pkg) ?: return false
        return start(context, intent)
    }

    /**
     * Edge in the other half of a split screen, beside Raticode and its bottom bar. Android places it there only while
     * Raticode is itself in split screen; otherwise Edge opens full screen, as [launch] does.
     */
    fun launchBeside(context: Context, found: Found): Boolean {
        if (!found.usable) return false
        val intent = context.packageManager.getLaunchIntentForPackage(found.build.pkg) ?: return false
        return start(context, intent.addFlags(Intent.FLAG_ACTIVITY_LAUNCH_ADJACENT or Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    /** Opens Edge's page in Google Play, or on the web when the phone has no store. */
    fun getEdge(context: Context): Boolean {
        val store = Intent(Intent.ACTION_VIEW, "market://details?id=com.microsoft.emmx".toUri())
        return start(context, store) || start(context, Intent(Intent.ACTION_VIEW, PLAY_PAGE.toUri()).addCategory(Intent.CATEGORY_BROWSABLE))
    }

    /** Android's app settings for Edge, where it can be turned back on. */
    fun appSettings(context: Context, found: Found): Boolean =
        start(context, Intent(android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS, "package:${found.build.pkg}".toUri()))

    private fun start(context: Context, intent: Intent): Boolean {
        if (context !is Activity) intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return try { context.startActivity(intent); true } catch (_: ActivityNotFoundException) { false } catch (_: SecurityException) { false }
    }
}
