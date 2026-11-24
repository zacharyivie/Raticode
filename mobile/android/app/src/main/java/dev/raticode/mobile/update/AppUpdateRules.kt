package dev.raticode.mobile.update

import org.json.JSONArray
import org.json.JSONObject

/**
 * The rules behind Raticode Mobile's update check, kept free of Android so they can be tested on their own: which
 * GitHub release counts, what its APK must look like, when to check again, and when to remind.
 *
 * Desktop and phone releases share one GitHub repository, so GitHub's "latest release" is usually the desktop's. The
 * phone looks at its own `mobile-vX.Y.Z` tags instead and then reads only the release of a tag newer than itself.
 */
object AppUpdateRules {
    const val REPOSITORY = "zacharyivie/Raticode"
    const val TAG_PREFIX = "mobile-v"
    const val TAGS_URL = "https://api.github.com/repos/$REPOSITORY/git/matching-refs/tags/$TAG_PREFIX"
    const val RELEASES_PAGE = "https://github.com/$REPOSITORY/releases"
    const val CHECK_INTERVAL_MS = 10 * 60 * 1000L
    const val MAX_BACKOFF_MS = 2 * 60 * 60 * 1000L
    /** A start of the app within this long of the last check reuses it instead of asking GitHub again. */
    const val STARTUP_REUSE_MS = 60 * 1000L
    /** The most releases one check reads, when several tags newer than this copy aren't public yet. */
    const val MAX_RELEASE_LOOKUPS = 3
    private const val MIN_APK_BYTES = 1L shl 20
    private const val MAX_APK_BYTES = 1L shl 30
    private val SHA256 = Regex("^[0-9a-f]{64}$")

    fun releaseUrl(version: Version) = "https://api.github.com/repos/$REPOSITORY/releases/tags/$TAG_PREFIX$version"
    fun releasePage(version: Version) = "$RELEASES_PAGE/tag/$TAG_PREFIX$version"
    fun apkName(version: Version) = "raticode-mobile-$version.apk"
    fun assetUrl(version: Version, name: String) = "$RELEASES_PAGE/download/$TAG_PREFIX$version/$name"

    /** PackageManager.GET_SIGNATURES and PackageManager.GET_SIGNING_CERTIFICATES. */
    private const val GET_SIGNATURES = 0x40
    private const val GET_SIGNING_CERTIFICATES = 0x08000000

    /**
     * The flags for reading a downloaded APK's signer with getPackageArchiveInfo. Before Android 11 it collects an
     * archive's certificates only when GET_SIGNATURES is set, so GET_SIGNING_CERTIFICATES alone leaves signingInfo
     * null and every update on Android 10 would look unsigned.
     */
    fun archiveSignerFlags(sdk: Int): Int =
        if (sdk < 30) GET_SIGNING_CERTIFICATES or GET_SIGNATURES else GET_SIGNING_CERTIFICATES

    /** A SemVer 2.0.0 version, the form version.properties uses. Build metadata is ignored. */
    data class Version(val major: Int, val minor: Int, val patch: Int, val prerelease: List<String> = emptyList()) : Comparable<Version> {
        val stable get() = prerelease.isEmpty()
        override fun toString() = "$major.$minor.$patch" + if (prerelease.isEmpty()) "" else "-" + prerelease.joinToString(".")
        override fun compareTo(other: Version): Int {
            compareValuesBy(this, other, Version::major, Version::minor, Version::patch).takeIf { it != 0 }?.let { return it }
            // A release outranks its own prereleases.
            if (prerelease.isEmpty() || other.prerelease.isEmpty()) return other.prerelease.size.coerceAtMost(1) - prerelease.size.coerceAtMost(1)
            for (i in 0 until minOf(prerelease.size, other.prerelease.size)) {
                val a = prerelease[i]; val b = other.prerelease[i]
                val an = a.toIntOrNull(); val bn = b.toIntOrNull()
                val order = when {
                    an != null && bn != null -> an.compareTo(bn)
                    an != null -> -1
                    bn != null -> 1
                    else -> a.compareTo(b)
                }
                if (order != 0) return order
            }
            return prerelease.size.compareTo(other.prerelease.size)
        }
        companion object {
            private val PATTERN = Regex("^(0|[1-9]\\d{0,8})\\.(0|[1-9]\\d{0,8})\\.(0|[1-9]\\d{0,8})(?:-([0-9A-Za-z-]+(?:\\.[0-9A-Za-z-]+)*))?(?:\\+[0-9A-Za-z.-]+)?$")
            fun parse(text: String?): Version? {
                val match = PATTERN.matchEntire(text?.trim().orEmpty()) ?: return null
                val (major, minor, patch, pre) = match.destructured
                return Version(major.toInt(), minor.toInt(), patch.toInt(), if (pre.isEmpty()) emptyList() else pre.split('.'))
            }
            /** This app's versionName. A debug build adds "-debug", which marks the build rather than a prerelease. */
            fun installed(versionName: String?) = parse(versionName?.removeSuffix("-debug"))
        }
    }

    /**
     * Stable phone versions newer than [installed], newest first, from GitHub's list of `mobile-v` tags. A tag can exist
     * before its release is public, so each still needs its release checked. Prereleases are left to people who
     * install them by hand.
     */
    fun newerTags(tagsJson: String, installed: Version): List<Version> {
        val refs = runCatching { JSONArray(tagsJson) }.getOrNull() ?: throw UpdateFailure("GitHub sent a list of releases Raticode couldn't read.")
        return (0 until refs.length()).mapNotNull { refs.optJSONObject(it)?.optString("ref") }
            .mapNotNull { ref -> ref.removePrefix("refs/tags/").takeIf { it.startsWith(TAG_PREFIX) }?.let { Version.parse(it.removePrefix(TAG_PREFIX)) } }
            .filter { it.stable && it > installed }
            .distinct().sortedDescending()
    }

    /** A published phone release and the files the in-app update uses. */
    data class Release(
        val version: Version,
        val page: String,
        val apkUrl: String,
        val apkSize: Long,
        /** GitHub's own SHA-256 of the uploaded APK, when it reports one. */
        val apkDigest: String?,
        val checksUrl: String,
        val publishedAt: String,
    )

    /**
     * The release for [version], or null when GitHub has it only as a draft or prerelease. Throws when it is public but
     * lacks a usable APK: that is a broken release, not "up to date".
     */
    fun release(json: String, version: Version): Release? {
        val release = runCatching { JSONObject(json) }.getOrNull() ?: throw UpdateFailure("GitHub sent release details Raticode couldn't read.")
        if (release.optString("tag_name") != "$TAG_PREFIX$version" || release.optBoolean("draft") || release.optBoolean("prerelease")) return null
        val assets = release.optJSONArray("assets") ?: JSONArray()
        fun asset(name: String) = (0 until assets.length()).mapNotNull { assets.optJSONObject(it) }.firstOrNull { it.optString("name") == name }
        val apk = asset(apkName(version)) ?: throw UpdateFailure("Raticode Mobile $version is published without its APK. Try again later or download it from the website.")
        val apkUrl = assetUrl(version, apkName(version))
        if (apk.optString("browser_download_url") != apkUrl) throw UpdateFailure("Raticode Mobile $version's APK isn't where Raticode publishes it, so it won't be downloaded.")
        val state = apk.optString("state", "uploaded")
        if (state != "uploaded") throw UpdateFailure("GitHub is still uploading Raticode Mobile $version. Try again in a few minutes.")
        val size = apk.optLong("size", -1)
        if (size !in MIN_APK_BYTES..MAX_APK_BYTES) throw UpdateFailure("Raticode Mobile $version's APK has an unexpected size, so it won't be downloaded.")
        val checks = asset(CHECKS_NAME) ?: throw UpdateFailure("Raticode Mobile $version is published without its release checks, so Raticode can't verify the download.")
        if (checks.optString("browser_download_url") != assetUrl(version, CHECKS_NAME)) throw UpdateFailure("Raticode Mobile $version's release checks aren't where Raticode publishes them.")
        val digest = apk.optString("digest").takeIf { it.isNotEmpty() && it != "null" }?.let {
            it.removePrefix("sha256:").lowercase().takeIf { hex -> it.startsWith("sha256:") && SHA256.matches(hex) }
                ?: throw UpdateFailure("GitHub reported a checksum for Raticode Mobile $version that Raticode couldn't read.")
        }
        return Release(version, releasePage(version), apkUrl, size, digest, assetUrl(version, CHECKS_NAME), release.optString("published_at"))
    }

    const val CHECKS_NAME = "apk-checks.json"

    /** What the release's apk-checks.json promises about the APK: its hash, size, version code and signing certificate. */
    data class ApkPromise(val sha256: String, val size: Long, val versionCode: Long, val certificateSha256: String)

    /** Reads apk-checks.json, which the release pipeline wrote after checking the signed APK. */
    fun apkPromise(json: String, release: Release): ApkPromise {
        val checks = runCatching { JSONObject(json) }.getOrNull() ?: throw UpdateFailure("Raticode couldn't read the release checks for ${release.version}.")
        fun fail(): Nothing = throw UpdateFailure("The release checks for ${release.version} don't describe a signed, verified APK, so it won't be installed.")
        if (checks.optString("status") != "passed" || !checks.optBoolean("signed") || checks.optString("version") != release.version.toString()) fail()
        val sha = checks.optString("apk_sha256").lowercase().takeIf(SHA256::matches) ?: fail()
        val certificate = checks.optString("certificate_sha256").lowercase().replace(":", "").takeIf(SHA256::matches) ?: fail()
        val size = checks.optLong("apk_size", -1)
        val code = checks.optLong("version_code", -1)
        if (size != release.apkSize || code < 1) fail()
        if (release.apkDigest != null && release.apkDigest != sha) throw UpdateFailure("GitHub's checksum for ${release.version} disagrees with its release checks, so it won't be downloaded.")
        return ApkPromise(sha, size, code, certificate)
    }

    /** What Android reads from an APK: its package, version code and signing certificates' SHA-256. */
    data class ApkFacts(val packageName: String?, val versionCode: Long, val certificates: Set<String>)

    /**
     * Why the downloaded APK must not be handed to the installer, or null when it is the expected update: this app's
     * package, the promised version code, newer than this copy, and signed with this copy's key, which must also be the
     * published release key.
     */
    fun apkProblem(apk: ApkFacts?, packageName: String, installedCode: Long, installedCertificates: Set<String>, promise: ApkPromise): String? = when {
        apk == null || apk.packageName == null -> "Android couldn't read the downloaded APK. Nothing was installed."
        apk.packageName != packageName -> "The downloaded APK isn't Raticode Mobile. Nothing was installed."
        apk.versionCode != promise.versionCode -> "The downloaded APK isn't the version the release describes. Nothing was installed."
        apk.versionCode <= installedCode -> "The downloaded APK isn't newer than this copy. Nothing was installed."
        promise.certificateSha256 !in apk.certificates -> "The downloaded APK isn't signed with Raticode's published release key. Nothing was installed."
        installedCertificates.none { it in apk.certificates } ->
            "This copy of Raticode Mobile is signed with a different key, such as a development build, so Android can't update it. Install the release from raticode.dev by hand."
        else -> null
    }

    /** True when this copy's key is the published release key, so an update can install over it. */
    fun sameKey(installedCertificates: Set<String>, promise: ApkPromise) = promise.certificateSha256 in installedCertificates

    /** How long until the next automatic check: 10 minutes, longer after failures, and never before GitHub's reset. */
    fun nextCheckDelay(failures: Int, retryAt: Long = 0, now: Long): Long {
        val backoff = if (failures > 0) minOf(CHECK_INTERVAL_MS shl minOf(failures - 1, 10), MAX_BACKOFF_MS) else CHECK_INTERVAL_MS
        val untilRetry = if (retryAt > now) minOf(retryAt - now, 6 * MAX_BACKOFF_MS) else 0
        return maxOf(backoff, untilRetry)
    }

    /**
     * Whether an automatic check is due. Every start of the app checks, unless one ran in the last minute or GitHub
     * asked to wait; while the app stays open, [nextCheckAt] spaces them out.
     */
    fun checkDue(now: Long, lastCheckAt: Long, nextCheckAt: Long, retryAt: Long, appStart: Boolean): Boolean = when {
        // A wait saved by an older version could reach days ahead; past the cap it no longer holds checks back.
        now < retryAt && retryAt - now <= MAX_RETRY_WAIT_MS -> false
        appStart -> now - lastCheckAt >= STARTUP_REUSE_MS || lastCheckAt > now
        else -> now >= nextCheckAt
    }

    /** The longest an automatic check waits for GitHub's rate limit, however far off its reset time looks. */
    const val MAX_RETRY_WAIT_MS = 6 * MAX_BACKOFF_MS

    /**
     * When GitHub will take another request after refusing one, from its rate-limit headers. GitHub's reset is a clock
     * time, so a phone clock that runs hours or days behind would otherwise stop automatic checks for that long; the
     * wait is capped at [MAX_RETRY_WAIT_MS], as [nextCheckDelay] already caps it.
     */
    fun retryAt(status: Int, remaining: String?, reset: String?, retryAfter: String?, now: Long): Long {
        if (status != 403 && status != 429) return 0
        val resetSeconds = reset?.toLongOrNull() ?: 0
        val latest = now + MAX_RETRY_WAIT_MS
        if (remaining == "0" && resetSeconds > 0) return minOf(resetSeconds.coerceAtMost(Long.MAX_VALUE / 1000) * 1000, latest)
        val after = retryAfter?.toLongOrNull() ?: 0
        return if (after > 0) now + minOf(after, MAX_RETRY_WAIT_MS / 1000) * 1000 else 0
    }

    /** What to tell the user when GitHub answers a check with [status]. */
    fun httpProblem(status: Int, retryAt: Long, now: Long): String = when {
        (status == 403 || status == 429) && retryAt > now -> "GitHub is limiting update checks from this network. Raticode tries again in ${((retryAt - now + 59_999) / 60_000)} min."
        status == 403 || status == 429 -> "GitHub refused the update check (HTTP $status). A VPN, proxy or firewall may be blocking it."
        status >= 500 -> "GitHub returned HTTP $status while checking for updates. Raticode tries again later."
        else -> "GitHub returned HTTP $status while checking for updates."
    }

    /** A day's last reminder: the local calendar day and the version it named. */
    data class Reminder(val day: String = "", val version: String = "")

    /**
     * Whether to show the once-a-day reminder now: an update is waiting, the user is in the app, and no reminder was
     * shown today. Only the day of the last one is kept, so a week away still adds up to one reminder.
     */
    fun remind(available: Version?, userPresent: Boolean, today: String, last: Reminder): Reminder? =
        if (available == null || !userPresent || last.day == today) null else Reminder(today, available.toString())

    /** Android's PackageInstaller status codes, as words. Codes are passed in so this stays testable off a device. */
    fun installProblem(status: Int, detail: String?): String = when (status) {
        3 -> "The update was cancelled. Nothing was installed."
        2 -> "Android blocked the update. A device policy may not allow installing apps."
        4 -> "Android said the downloaded APK is invalid. Nothing was installed. Try the update again."
        5 -> "Android couldn't install the update because it conflicts with this copy, usually a different signing key. Nothing was installed."
        6 -> "There isn't enough storage to install the update. Free some space and try again."
        7 -> "This phone can't run the new version of Raticode Mobile."
        8 -> "Android took too long to install the update. Try again."
        else -> "Android couldn't install the update" + (detail?.takeIf { it.isNotBlank() }?.let { ": ${it.take(200)}" } ?: ".")
    }
}

/** A check, download or install problem in words the user can act on. */
class UpdateFailure(message: String, val retryAt: Long = 0) : Exception(message)
