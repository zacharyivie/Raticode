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
 * Browser apps that the phone's Browser page can hand pages to instead of Raticode's own browser: Firefox ([Firefox]),
 * Google Chrome ([ChromeApps]), Microsoft Edge ([MicrosoftEdge]) and siblings described the same way. Each app's file
 * defines one [BrowserApp] and adds it to [ALL]; saving the choice, the Browser page, Browser settings, the tab list and
 * chat links all go through this list, so a new app needs no other change to those files.
 */
object BrowserApps {
    val ALL: List<BrowserApp> = listOf(Firefox.APP, ChromeApps.APP, MicrosoftEdge.APP)

    fun byEngine(engine: String): BrowserApp? = ALL.firstOrNull { it.engine == engine }

    /** How an app shows a page: in the app itself, in its own task ("app", the default), or over Raticode as an Android Custom Tab ("tab"). */
    const val MODE_APP = "app"
    const val MODE_TAB = "tab"

    /** A saved "how pages open" value, or the default for anything else. */
    fun mode(value: String?): String = if (value == MODE_TAB) MODE_TAB else MODE_APP

    /** The 32 bytes of a lower-case hex SHA-256 digest, as `PackageManager.hasSigningCertificate` takes them. */
    fun certBytes(hex: String): ByteArray = ByteArray(hex.length / 2) { hex.substring(it * 2, it * 2 + 2).toInt(16).toByte() }
}

/**
 * One browser app for Android. [engine] is the saved choice ("chrome"). [editions] are its packages in order of
 * preference, each with the SHA-256 digests of the certificates its maker signs that package with; Raticode sends pages
 * only to an installed edition Android confirms is signed with one of them. [store] is the package Google Play lists.
 * [signIn] starts the app's own account sign-in: a web page opened in the app like any other page, or one of the app's
 * screens. [places] are the app's own screens, each shown as a row. A place with a deep-link host opens that screen
 * ([openPlace]); a place without one opens the app and says where to tap. [linkHosts] is the fixed list of hosts the app
 * may be sent, each as `scheme://host` with its edition's [Edition.scheme]; any other host is never sent ([deepLink]).
 * [beside] offers the app in the other half of a split screen ([launchBeside]). [limits] says, in the page's words, what
 * the app doesn't let Raticode do.
 */
class BrowserApp(
    val engine: String,
    val name: String,
    val maker: String,
    val editions: List<Edition>,
    val store: String,
    val signIn: SignIn?,
    val limits: String,
    val places: List<Place> = emptyList(),
    val beside: Boolean = false,
    val linkHosts: Set<String> = emptySet(),
) {
    /** One package of the app. [scheme] is the deep-link scheme that package registers, for an app with [linkHosts]. */
    data class Edition(val pkg: String, val name: String, val certs: List<String>, val scheme: String? = null)

    /**
     * The app's own account sign-in: a web page ([url]) or one of its screens by deep-link host ([host]). Raticode only
     * opens the address or screen and never sees the password.
     */
    data class SignIn(val label: String, val detail: String, val url: String? = null, val host: String? = null) {
        init { require((url == null) != (host == null)) { "A sign-in is a web page or a screen" } }
    }

    /**
     * One of the app's own screens, as [label] and what it is ([detail]). [host] opens it by deep link; without one,
     * [where] says where it is in the app's menu.
     */
    data class Place(val label: String, val detail: String, val where: String = "", val host: String? = null)

    /** An installed edition. [genuine] means its maker signed it; nothing is sent to one that isn't. */
    data class Found(val app: BrowserApp, val edition: Edition, val version: String, val enabled: Boolean, val genuine: Boolean) {
        val usable get() = enabled && genuine
    }

    val missing get() = "$name isn't installed on this phone."
    val turnedOff get() = "$name is turned off in Android settings."
    val notGenuine get() = "The app named $name on this phone isn't signed by $maker, so Raticode won't send it pages."
    val playPage get() = "https://play.google.com/store/apps/details?id=$store"

    /** The preferred installed edition: the first of [editions] that is installed. */
    fun preferred(installed: Collection<String>): Edition? = editions.firstOrNull { it.pkg in installed }

    /** The deep link for [host] in [edition], or null for a host outside [linkHosts] or an edition with no scheme. */
    fun deepLink(edition: Edition, host: String): String? =
        if (host in linkHosts && edition.scheme != null) "${edition.scheme}://$host" else null

    /** Pages the app opens for Raticode: web addresses only. The app applies its own rules to each one. */
    fun openable(url: String): Boolean = BrowserAddress.isWeb(url)

    /** Why [found] can't take pages, in the page's words, or null when it can. */
    fun problem(found: Found?): String? = when {
        found == null -> missing
        !found.genuine -> notGenuine
        !found.enabled -> turnedOff
        else -> null
    }

    /** Whether [edition] is signed with one of its maker's certificates, asking [signedWith] about each digest. */
    fun genuine(edition: Edition, signedWith: (ByteArray) -> Boolean): Boolean =
        edition.certs.any { signedWith(BrowserApps.certBytes(it)) }

    // Android ---------------------------------------------------------------------------------------------------------

    /** The installed app, if any. Only its own packages are visible to Raticode (manifest `<queries>`). */
    fun find(context: Context): Found? {
        val pm = context.packageManager
        val found = editions.mapNotNull { edition ->
            val info = try {
                if (android.os.Build.VERSION.SDK_INT >= 33) pm.getPackageInfo(edition.pkg, PackageManager.PackageInfoFlags.of(0))
                else @Suppress("DEPRECATION") pm.getPackageInfo(edition.pkg, 0)
            } catch (_: PackageManager.NameNotFoundException) { null } ?: return@mapNotNull null
            val genuine = android.os.Build.VERSION.SDK_INT >= 28 &&
                genuine(edition) { pm.hasSigningCertificate(edition.pkg, it, PackageManager.CERT_INPUT_SHA256) }
            Found(this, edition, info.versionName.orEmpty(), info.applicationInfo?.enabled != false, genuine)
        }
        return found.firstOrNull { it.usable } ?: found.firstOrNull()
    }

    /**
     * Opens [url] in [found]. "app" opens it as a tab in the app, in the app's own task, so going back to Raticode
     * leaves the page (and anything playing) alone. "tab" uses Android Custom Tabs: the app draws the page over
     * Raticode, and closing it ends the page. Returns false when the app didn't take it.
     */
    fun open(context: Context, found: Found, url: String, mode: String): Boolean {
        if (!openable(url) || !found.usable) return false
        val intent = Intent(Intent.ACTION_VIEW, url.toUri()).setPackage(found.edition.pkg).addCategory(Intent.CATEGORY_BROWSABLE)
        if (mode == BrowserApps.MODE_TAB) {
            // The Custom Tabs protocol, written out so no extra library is needed: a session extra (here empty) marks it.
            intent.putExtras(Bundle().apply { putBinder("android.support.customtabs.extra.SESSION", null) })
            intent.putExtra("android.support.customtabs.extra.TITLE_VISIBILITY", 1)
            intent.putExtra("androidx.browser.customtabs.extra.SHARE_STATE", 1)
        } else intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return start(context, intent)
    }

    /**
     * Opens one of the app's own screens by its deep-link [host]. An older version without that screen opens where the
     * user left it instead, as [launch] does. A host outside [linkHosts] opens nothing.
     */
    fun openPlace(context: Context, found: Found, host: String): Boolean {
        if (!found.usable) return false
        val link = deepLink(found.edition, host) ?: return false
        if (start(context, Intent(Intent.ACTION_VIEW, link.toUri()).setPackage(found.edition.pkg))) return true
        return launch(context, found)
    }

    /** Opens the app through its launcher entry, which brings back its existing task where the user left it. */
    fun launch(context: Context, found: Found): Boolean {
        if (!found.usable) return false
        val intent = context.packageManager.getLaunchIntentForPackage(found.edition.pkg) ?: return false
        return start(context, intent)
    }

    /**
     * The app in the other half of a split screen, beside Raticode and its bottom bar. Android places it there only while
     * Raticode is itself in split screen; otherwise the app opens full screen, as [launch] does.
     */
    fun launchBeside(context: Context, found: Found): Boolean {
        if (!found.usable) return false
        val intent = context.packageManager.getLaunchIntentForPackage(found.edition.pkg) ?: return false
        return start(context, intent.addFlags(Intent.FLAG_ACTIVITY_LAUNCH_ADJACENT or Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    /** Opens the app's page in Google Play, or on the web when the phone has no store. */
    fun getApp(context: Context): Boolean {
        val store = Intent(Intent.ACTION_VIEW, "market://details?id=$store".toUri())
        return start(context, store) || start(context, Intent(Intent.ACTION_VIEW, playPage.toUri()).addCategory(Intent.CATEGORY_BROWSABLE))
    }

    /** Android's app settings for the app: turn it on, clear its data, or see its permissions. */
    fun appSettings(context: Context, found: Found): Boolean =
        start(context, Intent(android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS, "package:${found.edition.pkg}".toUri()))

    private fun start(context: Context, intent: Intent): Boolean {
        if (context !is Activity) intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return try { context.startActivity(intent); true } catch (_: ActivityNotFoundException) { false } catch (_: SecurityException) { false }
    }
}
