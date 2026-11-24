package dev.raticode.mobile.update

import dev.raticode.mobile.update.AppUpdateRules.Version
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class AppUpdateRulesTest {
    private fun v(text: String) = Version.parse(text)!!
    private val sha = "2eb9ea9169693c086bfb879e17c758bcd060865ec56203c16460803c93af6f60"
    private val cert = "9713ffff09a430e175b4b768392b52a4b6ba9656bbeccee735930863e6fae7bc"

    @Test fun versionsCompareBySemver() {
        assertTrue(v("0.35.0") > v("0.34.9"))
        assertTrue(v("0.34.10") > v("0.34.9"))
        assertTrue(v("1.0.0") > v("1.0.0-rc.2"))
        assertTrue(v("1.0.0-rc.10") > v("1.0.0-rc.2"))
        assertTrue(v("1.0.0-rc.1") > v("1.0.0-beta"))
        assertEquals(0, v("1.2.3+build.5").compareTo(v("1.2.3")))
        assertNull(Version.parse("v1.2.3"))
        assertNull(Version.parse("1.2"))
        assertNull(Version.parse("01.2.3"))
        // A debug build's suffix marks the build, not an older prerelease.
        assertEquals(v("0.34.0"), Version.installed("0.34.0-debug"))
        assertEquals(v("1.0.0-rc.1"), Version.installed("1.0.0-rc.1"))
    }

    private fun tags(vararg names: String) = JSONArray(names.map { JSONObject().put("ref", "refs/tags/$it") }).toString()

    @Test fun onlyNewerStablePhoneTagsCountNewestFirst() {
        val json = tags("mobile-v0.33.0", "mobile-v0.34.0", "mobile-v0.36.0", "mobile-v0.35.0", "mobile-v0.37.0-rc.1", "v0.9.9", "mobile-vbroken", "mobile-v0.35.0")
        assertEquals(listOf(v("0.36.0"), v("0.35.0")), AppUpdateRules.newerTags(json, v("0.34.0")))
        assertEquals(emptyList<Version>(), AppUpdateRules.newerTags(json, v("0.36.0")))
        // Someone on a release candidate gets the release.
        assertEquals(listOf(v("1.0.0")), AppUpdateRules.newerTags(tags("mobile-v1.0.0"), v("1.0.0-rc.1")))
        assertThrows(UpdateFailure::class.java) { AppUpdateRules.newerTags("{\"message\":\"Not Found\"}", v("0.34.0")) }
    }

    private fun asset(name: String, version: String, size: Long = 175_033_343, digest: String? = "sha256:$sha", url: String? = null) =
        JSONObject().put("name", name).put("size", size).put("state", "uploaded")
            .put("browser_download_url", url ?: "https://github.com/zacharyivie/Raticode/releases/download/mobile-v$version/$name")
            .apply { if (digest != null) put("digest", digest) }

    private fun release(version: String = "0.35.0", draft: Boolean = false, prerelease: Boolean = false, assets: List<JSONObject>? = null) =
        JSONObject().put("tag_name", "mobile-v$version").put("draft", draft).put("prerelease", prerelease).put("published_at", "2026-11-14T00:00:00Z")
            .put("assets", JSONArray(assets ?: listOf(asset("raticode-mobile-$version.apk", version), asset("apk-checks.json", version, size = 1926, digest = null)))).toString()

    @Test fun aPublishedReleaseGivesItsApkAndChecks() {
        val found = AppUpdateRules.release(release(), v("0.35.0"))!!
        assertEquals("https://github.com/zacharyivie/Raticode/releases/download/mobile-v0.35.0/raticode-mobile-0.35.0.apk", found.apkUrl)
        assertEquals("https://github.com/zacharyivie/Raticode/releases/download/mobile-v0.35.0/apk-checks.json", found.checksUrl)
        assertEquals(sha, found.apkDigest)
        assertEquals("https://github.com/zacharyivie/Raticode/releases/tag/mobile-v0.35.0", found.page)
        assertNull(AppUpdateRules.release(release(draft = true), v("0.35.0")))
        assertNull(AppUpdateRules.release(release(prerelease = true), v("0.35.0")))
        assertNull(AppUpdateRules.release(release(), v("0.36.0")))
    }

    @Test fun aReleaseWithAMissingOrMisplacedApkIsAFailureNotUpToDate() {
        val checks = asset("apk-checks.json", "0.35.0", size = 1926, digest = null)
        val broken = mapOf(
            "no apk" to listOf(checks),
            "wrong name" to listOf(asset("raticode-mobile.apk", "0.35.0"), checks),
            "other host" to listOf(asset("raticode-mobile-0.35.0.apk", "0.35.0", url = "https://example.com/raticode-mobile-0.35.0.apk"), checks),
            "http" to listOf(asset("raticode-mobile-0.35.0.apk", "0.35.0", url = "http://github.com/zacharyivie/Raticode/releases/download/mobile-v0.35.0/raticode-mobile-0.35.0.apk"), checks),
            "tiny" to listOf(asset("raticode-mobile-0.35.0.apk", "0.35.0", size = 10), checks),
            "bad digest" to listOf(asset("raticode-mobile-0.35.0.apk", "0.35.0", digest = "md5:abc"), checks),
            "no checks" to listOf(asset("raticode-mobile-0.35.0.apk", "0.35.0")),
        )
        for ((name, assets) in broken) {
            assertThrows(name, UpdateFailure::class.java) { AppUpdateRules.release(release(assets = assets), v("0.35.0")) }
        }
        assertNull(AppUpdateRules.release(release(assets = listOf(asset("raticode-mobile-0.35.0.apk", "0.35.0", digest = null), checks)), v("0.35.0"))!!.apkDigest)
    }

    private fun checksJson(version: String = "0.35.0", status: String = "passed", signed: Boolean = true, apkSha: String = sha, size: Long = 175_033_343, code: Long = 51, certificate: String = cert) =
        JSONObject().put("status", status).put("signed", signed).put("version", version).put("apk_sha256", apkSha)
            .put("apk_size", size).put("version_code", code).put("certificate_sha256", certificate).toString()

    @Test fun releaseChecksMustDescribeTheSignedApk() {
        val found = AppUpdateRules.release(release(), v("0.35.0"))!!
        val promise = AppUpdateRules.apkPromise(checksJson(), found)
        assertEquals(AppUpdateRules.ApkPromise(sha, 175_033_343, 51, cert), promise)
        // keytool's colon form is accepted.
        assertEquals(cert, AppUpdateRules.apkPromise(checksJson(certificate = cert.chunked(2).joinToString(":").uppercase()), found).certificateSha256)
        for (bad in listOf(checksJson(status = "failed"), checksJson(signed = false), checksJson(version = "0.34.0"), checksJson(apkSha = "abc"),
                checksJson(size = 5), checksJson(code = 0), checksJson(certificate = ""), checksJson(apkSha = "f".repeat(64)), "not json")) {
            assertThrows(bad, UpdateFailure::class.java) { AppUpdateRules.apkPromise(bad, found) }
        }
    }

    @Test fun theDownloadedApkMustBeThisAppNewerAndSignedWithTheReleaseKey() {
        val promise = AppUpdateRules.ApkPromise(sha, 1, 51, cert)
        val good = AppUpdateRules.ApkFacts("dev.raticode.mobile", 51, setOf(cert))
        fun problem(apk: AppUpdateRules.ApkFacts?, installedCode: Long = 50, installedCerts: Set<String> = setOf(cert)) =
            AppUpdateRules.apkProblem(apk, "dev.raticode.mobile", installedCode, installedCerts, promise)
        assertNull(problem(good))
        assertNotNull(problem(null))
        assertNotNull(problem(good.copy(packageName = "com.example.evil")))
        assertNotNull(problem(good.copy(versionCode = 52)))
        assertNotNull(problem(good, installedCode = 51))
        assertNotNull(problem(good.copy(certificates = setOf("e".repeat(64)))))
        val debug = problem(good, installedCerts = setOf("d".repeat(64)))!!
        assertTrue(debug, debug.contains("development build"))
        assertTrue(AppUpdateRules.sameKey(setOf(cert), promise))
        assertFalse(AppUpdateRules.sameKey(setOf("d".repeat(64)), promise))
    }

    @Test fun checksRunAtStartAndEveryTenMinutesAndBackOff() {
        val ten = AppUpdateRules.CHECK_INTERVAL_MS
        val now = 10_000_000_000L
        // Starting the app checks unless a check ran in the last minute.
        assertTrue(AppUpdateRules.checkDue(now, lastCheckAt = now - 2 * 60_000, nextCheckAt = now + ten, retryAt = 0, appStart = true))
        assertFalse(AppUpdateRules.checkDue(now, lastCheckAt = now - 30_000, nextCheckAt = now + ten, retryAt = 0, appStart = true))
        // A clock moved backwards doesn't block checks.
        assertTrue(AppUpdateRules.checkDue(now, lastCheckAt = now + 99_000, nextCheckAt = now + ten, retryAt = 0, appStart = true))
        // While open, the schedule decides.
        assertFalse(AppUpdateRules.checkDue(now, lastCheckAt = now - 9 * 60_000, nextCheckAt = now + 60_000, retryAt = 0, appStart = false))
        assertTrue(AppUpdateRules.checkDue(now, lastCheckAt = now - ten, nextCheckAt = now, retryAt = 0, appStart = false))
        // GitHub's rate limit wins, even on start.
        assertFalse(AppUpdateRules.checkDue(now, lastCheckAt = 0, nextCheckAt = 0, retryAt = now + 1, appStart = true))
        assertEquals(ten, AppUpdateRules.nextCheckDelay(0, now = now))
        assertEquals(ten, AppUpdateRules.nextCheckDelay(1, now = now))
        assertEquals(4 * ten, AppUpdateRules.nextCheckDelay(3, now = now))
        assertEquals(AppUpdateRules.MAX_BACKOFF_MS, AppUpdateRules.nextCheckDelay(40, now = now))
        assertEquals(45 * 60_000L, AppUpdateRules.nextCheckDelay(1, retryAt = now + 45 * 60_000, now = now))
    }

    @Test fun aRateLimitNeverHoldsAutomaticChecksBackForMoreThanTwelveHours() {
        val cap = AppUpdateRules.MAX_RETRY_WAIT_MS
        assertEquals(12 * 60 * 60 * 1000L, cap)
        // A phone clock a day behind GitHub's: the reset looks a day away, but the wait stops at the cap.
        val now = 1_900_000_000_000L - 24 * 60 * 60 * 1000L
        assertEquals(now + cap, AppUpdateRules.retryAt(403, "0", "1900000000", null, now))
        assertEquals(now + cap, AppUpdateRules.retryAt(429, null, null, "999999999", now))
        assertEquals(now + cap, AppUpdateRules.retryAt(403, "0", Long.MAX_VALUE.toString(), null, now))
        // A longer wait saved by an older version no longer blocks checks; one within the cap still does.
        assertTrue(AppUpdateRules.checkDue(now, lastCheckAt = now - 60 * 60_000, nextCheckAt = 0, retryAt = now + 3 * cap, appStart = true))
        assertFalse(AppUpdateRules.checkDue(now, lastCheckAt = now - 60 * 60_000, nextCheckAt = 0, retryAt = now + cap, appStart = true))
    }

    @Test fun rateLimitHeadersBecomeARetryTimeAndAnHonestMessage() {
        val now = 1_000_000L
        val soon = 1_899_999_000_000L
        assertEquals(1_900_000_000_000L, AppUpdateRules.retryAt(403, "0", "1900000000", null, soon))
        assertEquals(now + 120_000, AppUpdateRules.retryAt(429, "12", null, "120", now))
        assertEquals(0L, AppUpdateRules.retryAt(500, "0", "1900000000", "120", now))
        assertTrue(AppUpdateRules.httpProblem(403, now + 30 * 60_000, now).contains("tries again in 30 min"))
        assertTrue(AppUpdateRules.httpProblem(403, 0, now).contains("refused"))
        assertTrue(AppUpdateRules.httpProblem(502, 0, now).contains("HTTP 502"))
    }

    @Test fun oneReminderADayAndOneAfterDaysAway() {
        val none = AppUpdateRules.Reminder()
        assertNull(AppUpdateRules.remind(null, true, "2026-11-13", none))
        assertNull(AppUpdateRules.remind(v("0.35.0"), false, "2026-11-13", none))
        val first = AppUpdateRules.remind(v("0.35.0"), true, "2026-11-13", none)!!
        assertEquals(AppUpdateRules.Reminder("2026-11-13", "0.35.0"), first)
        // Same day: never again, even for a newer release.
        assertNull(AppUpdateRules.remind(v("0.35.0"), true, "2026-11-13", first))
        assertNull(AppUpdateRules.remind(v("0.36.0"), true, "2026-11-13", first))
        // Back after a week with three releases out: one reminder, for the newest.
        val back = AppUpdateRules.remind(v("0.38.0"), true, "2026-11-20", first)!!
        assertEquals(AppUpdateRules.Reminder("2026-11-20", "0.38.0"), back)
        assertNull(AppUpdateRules.remind(v("0.38.0"), true, "2026-11-20", back))
    }

    @Test fun installerStatusesBecomeWords() {
        assertTrue(AppUpdateRules.installProblem(3, null).contains("cancelled"))
        assertTrue(AppUpdateRules.installProblem(5, null).contains("signing key"))
        assertTrue(AppUpdateRules.installProblem(6, null).contains("storage"))
        assertTrue(AppUpdateRules.installProblem(1, "INSTALL_FAILED_X").contains("INSTALL_FAILED_X"))
        assertEquals("Android couldn't install the update.", AppUpdateRules.installProblem(1, null))
    }

    @Suppress("DEPRECATION")
    @Test fun androidTenReadsTheDownloadedApksSignerWithGetSignatures() {
        // Android 10's getPackageArchiveInfo leaves signingInfo null unless GET_SIGNATURES is set.
        val both = android.content.pm.PackageManager.GET_SIGNING_CERTIFICATES or android.content.pm.PackageManager.GET_SIGNATURES
        assertEquals(both, AppUpdateRules.archiveSignerFlags(29))
        assertEquals(android.content.pm.PackageManager.GET_SIGNING_CERTIFICATES, AppUpdateRules.archiveSignerFlags(30))
        assertEquals(android.content.pm.PackageManager.GET_SIGNING_CERTIFICATES, AppUpdateRules.archiveSignerFlags(36))
    }
}
