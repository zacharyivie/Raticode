package dev.raticode.mobile.update

import android.Manifest
import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.ActivityNotFoundException
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInfo
import android.content.pm.PackageInstaller
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.annotation.RequiresApi
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import dev.raticode.mobile.R
import dev.raticode.mobile.applock.AppLockRuntime
import dev.raticode.mobile.childlock.ChildLockRuntime
import dev.raticode.mobile.update.AppUpdateRules.Version
import java.io.File
import java.net.URL
import java.security.MessageDigest
import java.time.LocalDate
import javax.net.ssl.HttpsURLConnection
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Raticode Mobile's own updates. While the app is on screen it asks GitHub for a newer phone release when it starts
 * and every 10 minutes, one request at a time and backing off after failures. It has no background service: a
 * phone that never opens Raticode isn't checked or reminded. "Update Raticode" downloads the signed APK into private
 * storage, verifies it, and hands it to Android's installer, which asks the user to confirm. A copy installed by
 * Google Play, or a build without the install permission, points to Play or the website instead.
 */
@RequiresApi(29)
object AppUpdates {
    const val CHANNEL = "raticode-updates"
    const val NOTIFICATION_ID = 2010
    private const val PREFS = "raticode-updates"
    private const val PLAY = "com.android.vending"
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)

    enum class Source { DIRECT, PLAY, NONE }

    /** The newest phone release newer than this copy, or null when this copy is the newest. */
    var available by mutableStateOf<AppUpdateRules.Release?>(null); private set
    var checking by mutableStateOf(false); private set
    var checkedAt by mutableStateOf(0L); private set
    /** Why the last check failed; cleared by the next one that works. */
    var checkError by mutableStateOf<String?>(null); private set
    var downloading by mutableStateOf(false); private set
    var downloaded by mutableStateOf(0L); private set
    var downloadSize by mutableStateOf(0L); private set
    var verifying by mutableStateOf(false); private set
    var installError by mutableStateOf<String?>(null); private set
    var installNotice by mutableStateOf<String?>(null); private set
    /** Android needs "Install unknown apps" for Raticode first. */
    var needsInstallPermission by mutableStateOf(false); private set
    /** The download is ~175 MB and the phone is on mobile data or a metered network. */
    var meteredPrompt by mutableStateOf(false); private set
    /** The installer's confirmation, waiting for the screen to show it. */
    var confirmation by mutableStateOf<Intent?>(null); private set
    /** The daily reminder banner. */
    var reminder by mutableStateOf<String?>(null); private set
    var source by mutableStateOf(Source.NONE); private set

    private var loop: Job? = null
    private var checkJob: Job? = null
    private var downloadJob: Job? = null
    @Volatile private var cancelDownload = false
    private var appStarted = false
    /** The user went to "Install unknown apps" from the update; coming back continues it. */
    private var resumeAfterPermission = false

    /** Raticode came on screen. The first time per process counts as the app starting. */
    fun foreground(context: Context) {
        val app = context.applicationContext
        source = installSource(app)
        val starting = !appStarted
        appStarted = true
        if (resumeAfterPermission) {
            resumeAfterPermission = false
            if (app.packageManager.canRequestPackageInstalls()) update(context, onMobileData = true)
            else installError = "Raticode still isn't allowed to install apps, so the update didn't start."
        }
        loop?.cancel()
        loop = scope.launch {
            var start = starting
            while (true) {
                val prefs = prefs(app)
                val now = System.currentTimeMillis()
                if (AppUpdateRules.checkDue(now, prefs.getLong("checked_at", 0), prefs.getLong("next_check_at", 0), prefs.getLong("retry_at", 0), start)) {
                    check(app, manual = false)
                } else if (available == null && checkError == null) {
                    restoreCached(app)
                }
                start = false
                remind(app)
                val p = prefs(app)
                val wait = maxOf(p.getLong("next_check_at", 0), p.getLong("retry_at", 0)) - System.currentTimeMillis()
                // Wakes at least once a minute to notice a new day for the reminder; it doesn't contact GitHub then.
                delay(wait.coerceIn(1_000L, 60_000L))
            }
        }
    }

    /** Raticode left the screen: no checks until it comes back. A download keeps going while Android lets it. */
    fun background() { loop?.cancel(); loop = null }

    /** The Check now button. Shares a check already running. */
    fun checkNow(context: Context) { scope.launch { check(context.applicationContext, manual = true) } }

    private suspend fun check(app: Context, manual: Boolean) {
        checkJob?.takeIf { it.isActive }?.let { it.join(); return }
        val job = scope.launch { runCheck(app, manual) }
        checkJob = job
        job.join()
    }

    private suspend fun runCheck(app: Context, manual: Boolean) {
        if (downloading || verifying) return
        val prefs = prefs(app)
        val now = System.currentTimeMillis()
        checking = true
        prefs.edit().putLong("checked_at", now).apply()
        try {
            if (!online(app)) throw UpdateFailure("You're offline, so Raticode couldn't check for updates. It tries again when you're back online.")
            val installed = installedVersion(app) ?: throw UpdateFailure("Raticode couldn't read its own version.")
            val found = withContext(Dispatchers.IO) { latestRelease(app, installed) }
            available = found
            checkError = null
            checkedAt = System.currentTimeMillis()
            prefs.edit().putInt("failures", 0).putLong("retry_at", 0)
                .putLong("next_check_at", checkedAt + AppUpdateRules.CHECK_INTERVAL_MS)
                .putString("available", found?.version?.toString()).apply()
            if (found == null) { clearReminder(app); removeDownloads(app, keep = null) }
        } catch (error: CancellationException) {
            throw error
        } catch (error: Exception) {
            val failures = prefs.getInt("failures", 0) + 1
            val retryAt = (error as? UpdateFailure)?.retryAt ?: 0
            checkError = (error as? UpdateFailure)?.message ?: "Raticode couldn't check for updates: ${error.message ?: error.javaClass.simpleName}"
            val after = System.currentTimeMillis()
            prefs.edit().putInt("failures", failures).putLong("retry_at", retryAt)
                .putLong("next_check_at", after + AppUpdateRules.nextCheckDelay(failures, retryAt, after)).apply()
        } finally {
            checking = false
        }
    }

    /** The cached answer of the last check, so a quick return to the app shows it without asking GitHub. */
    private fun restoreCached(app: Context) {
        val prefs = prefs(app)
        val version = Version.parse(prefs.getString("available", null)) ?: return
        val installed = installedVersion(app) ?: return
        if (version <= installed) return
        val json = prefs.getString("release_json:$version", null) ?: return
        available = runCatching { AppUpdateRules.release(json, version) }.getOrNull()
        checkedAt = prefs.getLong("checked_at", 0)
    }

    private fun latestRelease(app: Context, installed: Version): AppUpdateRules.Release? {
        val tags = cachedGet(app, AppUpdateRules.TAGS_URL, "tags") ?: return null
        for (version in AppUpdateRules.newerTags(tags, installed).take(AppUpdateRules.MAX_RELEASE_LOOKUPS)) {
            // A tag whose release is still a draft answers 404: it isn't out yet.
            val json = cachedGet(app, AppUpdateRules.releaseUrl(version), "release_json:$version") ?: continue
            AppUpdateRules.release(json, version)?.let { return it }
        }
        return null
    }

    /**
     * GET with GitHub's ETag: an unchanged answer comes back as 304 with no body. Without an account GitHub still
     * counts it against the 60 requests an hour per address, so the backoff and retryAt handle a rate limit.
     * Returns null for 404.
     */
    private fun cachedGet(app: Context, url: String, key: String): String? {
        val prefs = prefs(app)
        val connection = open(url, app)
        try {
            prefs.getString("etag:$key", null)?.takeIf { prefs.contains(key) }?.let { connection.setRequestProperty("If-None-Match", it) }
            connection.setRequestProperty("Accept", "application/vnd.github+json")
            connection.setRequestProperty("X-GitHub-Api-Version", "2022-11-28")
            val status = connection.responseCode
            return when (status) {
                304 -> prefs.getString(key, null)
                200 -> {
                    val body = connection.inputStream.use { it.readBytes().toString(Charsets.UTF_8) }
                    prefs.edit().putString(key, body).putString("etag:$key", connection.getHeaderField("ETag")).apply()
                    body
                }
                404 -> null
                else -> {
                    val now = System.currentTimeMillis()
                    val retryAt = AppUpdateRules.retryAt(status, connection.getHeaderField("X-RateLimit-Remaining"),
                        connection.getHeaderField("X-RateLimit-Reset"), connection.getHeaderField("Retry-After"), now)
                    throw UpdateFailure(AppUpdateRules.httpProblem(status, retryAt, now), retryAt)
                }
            }
        } catch (error: java.io.IOException) {
            throw UpdateFailure("Couldn't reach GitHub to check for updates. Check your internet connection.")
        } finally {
            connection.disconnect()
        }
    }

    private fun open(url: String, app: Context, from: Long = 0): HttpsURLConnection {
        val connection = URL(url).openConnection() as? HttpsURLConnection ?: throw UpdateFailure("Raticode only checks for updates over HTTPS.")
        connection.connectTimeout = 15_000
        connection.readTimeout = 30_000
        connection.instanceFollowRedirects = true
        connection.useCaches = false
        connection.setRequestProperty("User-Agent", "RaticodeMobile/${installedVersion(app) ?: "unknown"}")
        if (from > 0) connection.setRequestProperty("Range", "bytes=$from-")
        return connection
    }

    // ---- Update Raticode ----

    /** The Update Raticode button. [onMobileData] true means the user already agreed to use mobile data. */
    fun update(context: Context, onMobileData: Boolean = false) {
        val app = context.applicationContext
        val release = available ?: return
        installError = null
        installNotice = null
        when (installSource(app)) {
            Source.PLAY -> { openPlay(context); return }
            Source.NONE -> { openPage(context, release.page); return }
            Source.DIRECT -> Unit
        }
        if (downloading || verifying) return
        if (!online(app)) { installError = "You're offline. Connect to the internet and try again."; return }
        if (!onMobileData && metered(app)) { meteredPrompt = true; return }
        meteredPrompt = false
        if (!app.packageManager.canRequestPackageInstalls()) { needsInstallPermission = true; return }
        needsInstallPermission = false
        cancelDownload = false
        downloadJob = scope.launch {
            downloading = true
            downloaded = 0
            downloadSize = release.apkSize
            try {
                val apk = withContext(Dispatchers.IO) { download(app, release) }
                downloading = false
                verifying = true
                withContext(Dispatchers.IO) { install(app, apk.first, apk.second) }
                installNotice = "Android is asking you to confirm the update."
            } catch (error: ApkDownload.Cancelled) {
                installNotice = "Download paused. Tap Update Raticode to continue where it stopped."
            } catch (error: CancellationException) {
                throw error
            } catch (error: UpdateFailure) {
                installError = error.message
            } catch (error: Exception) {
                installError = "The update failed: ${error.message ?: error.javaClass.simpleName}. Nothing was installed."
            } finally {
                downloading = false
                verifying = false
            }
        }
    }

    fun cancel() { cancelDownload = true }

    fun dismissMeteredPrompt() { meteredPrompt = false }
    fun dismissInstallPermission() { needsInstallPermission = false }

    /** Android's "Install unknown apps" page for Raticode. Raticode resumes the update when the user comes back. */
    fun openInstallSettings(context: Context) {
        needsInstallPermission = false
        resumeAfterPermission = true
        try {
            context.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${context.packageName}")))
        } catch (_: ActivityNotFoundException) {
            installError = "This phone has no setting for installing apps from Raticode. Download the update from raticode.dev instead."
        }
    }

    private fun download(app: Context, release: AppUpdateRules.Release): Pair<File, AppUpdateRules.ApkPromise> {
        val promise = AppUpdateRules.apkPromise(smallGet(app, release.checksUrl), release)
        val installed = installedCertificates(app)
        // Checked before 175 MB is spent: a development build or another key can't be updated by this APK.
        if (!AppUpdateRules.sameKey(installed, promise))
            throw UpdateFailure("This copy of Raticode Mobile isn't signed with Raticode's release key, such as a development build, so Android can't update it. Install the release from raticode.dev by hand.")
        val folder = File(app.noBackupFilesDir, "updates").apply { mkdirs() }
        removeDownloads(app, keep = AppUpdateRules.apkName(release.version))
        val target = File(folder, AppUpdateRules.apkName(release.version))
        val partial = File(target.path + ".part").takeIf { it.isFile }?.length() ?: 0
        // Room for the rest of the download and Android's own copy while installing.
        if (folder.usableSpace < (release.apkSize - partial) + release.apkSize + (32L shl 20))
            throw UpdateFailure("There isn't enough free storage for the update (about ${release.apkSize * 2 / (1 shl 20)} MB). Free some space and try again.")
        val downloader = ApkDownload { url, from ->
            val connection = open(url, app, from)
            val status = connection.responseCode
            val body = if (status in 200..299) connection.inputStream else (connection.errorStream ?: java.io.ByteArrayInputStream(ByteArray(0)))
            ApkDownload.Response(status, body, connection.url.toString(), connection.getHeaderField("Content-Range"))
        }
        var lastShown = 0L
        val apk = downloader.fetch(release.apkUrl, target, promise.size, promise.sha256, { cancelDownload }) { done, total ->
            if (done - lastShown >= (1 shl 20) || done == total) { lastShown = done; scope.launch { downloaded = done; downloadSize = total } }
        }
        val problem = AppUpdateRules.apkProblem(archiveFacts(app, apk), app.packageName, installedCode(app), installed, promise)
        if (problem != null) { apk.delete(); throw UpdateFailure(problem) }
        return apk to promise
    }

    private fun smallGet(app: Context, url: String): String {
        val connection = open(url, app)
        try {
            val status = connection.responseCode
            if (status != 200) throw UpdateFailure("GitHub returned HTTP $status for the release checks. Try again later.")
            if (!connection.url.toString().startsWith("https://")) throw UpdateFailure("GitHub sent the release checks somewhere other than HTTPS.")
            return connection.inputStream.use { input ->
                val out = java.io.ByteArrayOutputStream()
                val buffer = ByteArray(8 * 1024)
                while (true) {
                    val read = input.read(buffer)
                    if (read < 0) break
                    out.write(buffer, 0, read)
                    if (out.size() > 256 * 1024) throw UpdateFailure("The release checks are larger than expected, so Raticode stopped.")
                }
                out.toString(Charsets.UTF_8.name())
            }
        } catch (error: java.io.IOException) {
            throw UpdateFailure("Couldn't reach GitHub to download the update. Check your internet connection.")
        } finally {
            connection.disconnect()
        }
    }

    /** Streams the verified file into an installer session, hashing it again on the way so what Android gets is what was checked. */
    private fun install(app: Context, apk: File, promise: AppUpdateRules.ApkPromise) {
        val installer = app.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(app.packageName)
            setSize(apk.length())
        }
        val id = installer.createSession(params)
        try {
            installer.openSession(id).use { session ->
                val digest = MessageDigest.getInstance("SHA-256")
                apk.inputStream().use { input ->
                    session.openWrite("raticode-mobile.apk", 0, apk.length()).use { out ->
                        val buffer = ByteArray(64 * 1024)
                        while (true) { val read = input.read(buffer); if (read < 0) break; digest.update(buffer, 0, read); out.write(buffer, 0, read) }
                        session.fsync(out)
                    }
                }
                val written = digest.digest().joinToString("") { "%02x".format(it) }
                if (written != promise.sha256) throw UpdateFailure("The update changed after it was verified, so Raticode stopped. Nothing was installed.")
                val intent = Intent(app, UpdateInstallReceiver::class.java).setPackage(app.packageName)
                // Mutable so the installer can add its status; explicit, so only this receiver gets it.
                val sender = PendingIntent.getBroadcast(app, id, intent, PendingIntent.FLAG_MUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
                session.commit(sender.intentSender)
            }
        } catch (error: Exception) {
            runCatching { installer.abandonSession(id) }
            throw error
        }
    }

    /** The installer's answer, from [UpdateInstallReceiver]. */
    fun onInstallStatus(context: Context, intent: Intent) {
        val app = context.applicationContext
        when (val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                confirmation = if (Build.VERSION.SDK_INT >= 33) intent.getParcelableExtra(Intent.EXTRA_INTENT, Intent::class.java)
                    else @Suppress("DEPRECATION") intent.getParcelableExtra(Intent.EXTRA_INTENT)
            }
            PackageInstaller.STATUS_SUCCESS -> {
                installNotice = "Raticode Mobile is updated."
                removeDownloads(app, keep = null)
                clearReminder(app)
            }
            else -> {
                installNotice = null
                installError = AppUpdateRules.installProblem(status, intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE))
            }
        }
    }

    /**
     * Shows the installer's confirmation from the screen, never over Child lock or App lock: the person holding the
     * phone must be the one who can unlock it. Returns false while a lock is on; it is shown once the lock is gone.
     */
    fun showConfirmation(context: Context): Boolean {
        val intent = confirmation ?: return true
        if (ChildLockRuntime.locked(context) || AppLockRuntime.locked(context)) return false
        confirmation = null
        return try { context.startActivity(intent); true } catch (_: ActivityNotFoundException) {
            installError = "Android's installer didn't open. Nothing was installed."; true
        }
    }

    // ---- The daily reminder ----

    /** Shows the reminder banner and notification once a day, the first time the user is in Raticode that day. */
    fun remind(context: Context) {
        val app = context.applicationContext
        val release = available ?: return
        val prefs = prefs(app)
        val last = AppUpdateRules.Reminder(prefs.getString("reminder_day", "").orEmpty(), prefs.getString("reminder_version", "").orEmpty())
        // App lock first: the user isn't in the app until they pass it.
        val next = AppUpdateRules.remind(release.version, !AppLockRuntime.locked(app), LocalDate.now().toString(), last) ?: return
        prefs.edit().putString("reminder_day", next.day).putString("reminder_version", next.version).apply()
        reminder = reminderText(app, release)
        postNotification(app, release)
    }

    /** Not now: the banner and the notification go until tomorrow. */
    fun dismissReminder(context: Context) {
        reminder = null
        context.getSystemService(NotificationManager::class.java).cancel(NOTIFICATION_ID)
    }

    private fun clearReminder(app: Context) = dismissReminder(app)

    private fun reminderText(app: Context, release: AppUpdateRules.Release) =
        "Raticode Mobile ${release.version} is available. You have ${installedVersion(app) ?: "an older version"}."

    private fun postNotification(app: Context, release: AppUpdateRules.Release) {
        if (ContextCompat.checkSelfPermission(app, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED && Build.VERSION.SDK_INT >= 33) return
        val manager = app.getSystemService(NotificationManager::class.java)
        if (!manager.areNotificationsEnabled()) return
        manager.createNotificationChannel(NotificationChannel(CHANNEL, "Raticode updates", NotificationManager.IMPORTANCE_LOW).apply {
            description = "At most one reminder a day when a newer Raticode Mobile is available"
            setShowBadge(false)
        })
        manager.notify(NOTIFICATION_ID, notification(app, release))
    }

    internal fun notification(app: Context, release: AppUpdateRules.Release): Notification {
        // Opens Raticode through the screen every notification uses, so Child lock and App lock stay in place.
        val open = PendingIntent.getActivity(app, 10, dev.raticode.mobile.childlock.NotificationOpenActivity.intent(app)
            .setAction(Intent.ACTION_VIEW).setData(Uri.parse("raticode-live://update")), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val action = if (installSource(app) == Source.PLAY) "Open Raticode to update from Google Play." else "Open Raticode and tap Update Raticode."
        return NotificationCompat.Builder(app, CHANNEL).setSmallIcon(R.drawable.ic_launcher)
            .setContentTitle("Update Raticode Mobile")
            .setContentText("Version ${release.version} is available. $action")
            .setStyle(NotificationCompat.BigTextStyle().bigText("${reminderText(app, release)} $action"))
            .setCategory(NotificationCompat.CATEGORY_RECOMMENDATION)
            .setOnlyAlertOnce(true).setAutoCancel(true).setContentIntent(open)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .build()
    }

    // ---- This copy of the app ----

    fun installedVersion(context: Context): Version? = Version.installed(packageInfo(context, 0)?.versionName)

    private fun installedCode(context: Context): Long = packageInfo(context, 0)?.longVersionCode ?: Long.MAX_VALUE

    /** Google Play installed this copy, or this build lacks the install permission (Play's copy leaves it out). */
    fun installSource(context: Context): Source {
        val installer = runCatching {
            if (Build.VERSION.SDK_INT >= 30) context.packageManager.getInstallSourceInfo(context.packageName).installingPackageName
            else @Suppress("DEPRECATION") context.packageManager.getInstallerPackageName(context.packageName)
        }.getOrNull()
        if (installer == PLAY) return Source.PLAY
        val declared = packageInfo(context, PackageManager.GET_PERMISSIONS)?.requestedPermissions.orEmpty()
        return if (Manifest.permission.REQUEST_INSTALL_PACKAGES in declared) Source.DIRECT else Source.NONE
    }

    private fun installedCertificates(context: Context): Set<String> = certificates(packageInfo(context, PackageManager.GET_SIGNING_CERTIFICATES))

    private fun archiveFacts(context: Context, apk: File): AppUpdateRules.ApkFacts? {
        val flags = AppUpdateRules.archiveSignerFlags(Build.VERSION.SDK_INT)
        val info = context.packageManager.getPackageArchiveInfo(apk.path, flags) ?: return null
        return AppUpdateRules.ApkFacts(info.packageName, info.longVersionCode, certificates(info))
    }

    private fun certificates(info: PackageInfo?): Set<String> {
        val signing = info?.signingInfo ?: return emptySet()
        val signers = if (signing.hasMultipleSigners()) signing.apkContentsSigners else signing.signingCertificateHistory
        return signers.orEmpty().map { signature ->
            MessageDigest.getInstance("SHA-256").digest(signature.toByteArray()).joinToString("") { "%02x".format(it) }
        }.toSet()
    }

    @SuppressLint("WrongConstant")
    private fun packageInfo(context: Context, flags: Int): PackageInfo? = runCatching {
        if (Build.VERSION.SDK_INT >= 33) context.packageManager.getPackageInfo(context.packageName, PackageManager.PackageInfoFlags.of(flags.toLong()))
        else @Suppress("DEPRECATION") context.packageManager.getPackageInfo(context.packageName, flags)
    }.getOrNull()

    private fun openPlay(context: Context) {
        val market = Intent(Intent.ACTION_VIEW, Uri.parse("market://details?id=${context.packageName}")).setPackage(PLAY)
        try { context.startActivity(market) } catch (_: ActivityNotFoundException) {
            openPage(context, "https://play.google.com/store/apps/details?id=${context.packageName}")
        }
    }

    private fun openPage(context: Context, url: String) {
        try { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) } catch (_: ActivityNotFoundException) {
            installError = "No app on this phone can open $url."
        }
    }

    private fun removeDownloads(app: Context, keep: String?) {
        File(app.noBackupFilesDir, "updates").listFiles()?.forEach { file ->
            if (keep == null || (file.name != keep && file.name != "$keep.part")) file.delete()
        }
    }

    private fun online(context: Context): Boolean {
        val connectivity = context.getSystemService(ConnectivityManager::class.java) ?: return true
        val capabilities = connectivity.getNetworkCapabilities(connectivity.activeNetwork) ?: return false
        return capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
    }

    private fun metered(context: Context) = context.getSystemService(ConnectivityManager::class.java)?.isActiveNetworkMetered ?: false

    private fun prefs(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
}

/** The installer's status for an update. Not exported: only the update's own PendingIntent reaches it. */
class UpdateInstallReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (Build.VERSION.SDK_INT >= 29) AppUpdates.onInstallStatus(context, intent)
    }
}
