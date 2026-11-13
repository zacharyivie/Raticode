package dev.raticode.mobile.share

import android.app.Application
import android.app.KeyguardManager
import android.content.ContentResolver
import android.content.Intent
import android.net.Uri
import android.os.CancellationSignal
import android.provider.OpenableColumns
import android.webkit.MimeTypeMap
import androidx.annotation.RequiresApi
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.core.content.IntentCompat
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.live.LiveRuntime
import dev.raticode.mobile.live.RemHome
import dev.raticode.mobile.voice.DeskReadiness
import dev.raticode.mobile.voice.VoiceLogic
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

/**
 * One share from another app, from review to Rem's chat. Nothing leaves the phone until you tap Send: the files are
 * read while this screen holds Android's temporary read grant, uploaded over the paired, encrypted connection, then
 * sent as one Rem message. "Sent" is shown only once the desktop's authenticated chat contains that message.
 */
@RequiresApi(29)
class ShareSession(app: Application) : AndroidViewModel(app) {
    val client: LiveClient = LiveRuntime.client(app)

    data class Item(val key: Int, val uri: Uri, val name: String, val size: Long?, val type: String,
                    val problem: String? = null, val fileId: String? = null)

    enum class Phase { LOADING, REVIEW, SENDING, QUEUED, SENT }

    var phase by mutableStateOf(Phase.LOADING); private set
    var text by mutableStateOf("")
    var shortened by mutableStateOf(false); private set
    var notices by mutableStateOf(listOf<String>()); private set
    var items by mutableStateOf(listOf<Item>()); private set
    /** Why the last Send stopped, shown above the buttons. The review stays as it was. */
    var error by mutableStateOf<String?>(null); private set
    /** What the send is waiting on right now. */
    var step by mutableStateOf(""); private set
    /** Waiting for the desktop to say it has the queued message. */
    var watching by mutableStateOf(false); private set
    private var job: Job? = null
    /** Shared items beyond [ShareIntake.MAX_DESCRIBED], never looked up. */
    private var notDescribed = 0
    private var surfaces = 0

    /** The share screen is visible. Connects without marking any thread read. */
    fun attach() { if (surfaces++ == 0) client.shareStart() }
    /** Left the screen. A send in progress keeps the connection until it is queued or fails. */
    fun detach() { if (surfaces > 0 && --surfaces == 0 && job?.isActive != true) client.shareStop() }

    override fun onCleared() { job?.cancel(); if (surfaces > 0 || job != null) client.shareStop() }

    /** Reads only the share's names, sizes and types. A provider that stalls is given up after a few seconds. */
    @OptIn(kotlinx.coroutines.ExperimentalCoroutinesApi::class)
    fun load(intent: Intent, ownPackage: String) {
        if (phase != Phase.LOADING || job != null) return
        val action = intent.action
        val uris = when (action) {
            Intent.ACTION_SEND -> listOfNotNull(IntentCompat.getParcelableExtra(intent, Intent.EXTRA_STREAM, Uri::class.java))
            Intent.ACTION_SEND_MULTIPLE -> IntentCompat.getParcelableArrayListExtra(intent, Intent.EXTRA_STREAM, Uri::class.java).orEmpty().filterNotNull()
            else -> emptyList()
        }.ifEmpty { if (action == Intent.ACTION_SEND || action == Intent.ACTION_SEND_MULTIPLE) clipUris(intent) else emptyList() }.distinct()
            .let { all -> all.take(ShareIntake.MAX_DESCRIBED) to all.size - minOf(all.size, ShareIntake.MAX_DESCRIBED) }
            .let { (kept, skipped) -> notDescribed = skipped; kept }
        val subject = if (action == null) null else intent.getCharSequenceExtra(Intent.EXTRA_SUBJECT)
        val body = if (action == null) null else intent.getCharSequenceExtra(Intent.EXTRA_TEXT)
        val resolver = getApplication<Application>().contentResolver
        val pm = getApplication<Application>().packageManager
        val intentType = intent.type
        job = viewModelScope.launch {
            // Provider calls can block without end, so each runs on its own thread and the screen stops waiting.
            val pending = uris.map { uri ->
                CompletableDeferred<Pair<ShareIntake.Stream, Meta>>().also { out ->
                    Thread({ out.complete(runCatching { describe(resolver, pm, ownPackage, uri, intentType) }.getOrElse { unreadable(uri) }) },
                        "share-describe").apply { isDaemon = true; start() }
                }
            }
            withTimeoutOrNull(META_MS) { pending.forEach { it.await() } }
            val described = pending.mapIndexed { i, out -> if (out.isCompleted) out.getCompleted() else unreadable(uris[i]) }
            val plan = ShareIntake.plan(subject, body, described.map { it.first }, notDescribed)
            text = plan.text; shortened = plan.shortened
            items = plan.accepted.map { index ->
                val (_, meta) = described[index]
                val extension = MimeTypeMap.getSingleton().getExtensionFromMimeType(meta.type)
                val name = ShareIntake.name(meta.name, extension, index, meta.type) { MimeTypeMap.getSingleton().getMimeTypeFromExtension(it) }
                Item(index, uris[index], name, meta.size, meta.type, ShareIntake.sizeProblem(meta.size))
            }
            notices = plan.refused + listOfNotNull(if (action !in setOf(Intent.ACTION_SEND, Intent.ACTION_SEND_MULTIPLE)) NOTHING else null)
            if (text.isEmpty() && items.isEmpty() && notices.isEmpty()) notices = listOf(NOTHING)
            phase = Phase.REVIEW; job = null
        }
    }

    private fun clipUris(intent: Intent): List<Uri> {
        val clip = intent.clipData ?: return emptyList()
        return (0 until clip.itemCount).mapNotNull { clip.getItemAt(it).uri }
    }

    private class Meta(val name: String?, val size: Long?, val type: String)
    private fun unreadable(uri: Uri) = ShareIntake.Stream(uri.scheme, false, null, unreadable = true) to Meta(null, null, "")

    private fun describe(resolver: ContentResolver, pm: android.content.pm.PackageManager, ownPackage: String, uri: Uri, intentType: String?):
            Pair<ShareIntake.Stream, Meta> {
        // A URI this app serves would let another app make Raticode send its own files, so it is never read.
        val own = ShareIntake.ownAuthority(uri.authority, ownPackage) { pm.resolveContentProvider(it, 0)?.packageName }
        if (uri.scheme != ContentResolver.SCHEME_CONTENT || own) return ShareIntake.Stream(uri.scheme, own, null) to Meta(null, null, "")
        // A provider that doesn't report a type keeps the one the sharing app declared, such as image/*.
        val type = runCatching { resolver.getType(uri) }.getOrNull() ?: intentType.orEmpty()
        var name: String? = null
        var size: Long? = null
        val signal = CancellationSignal()
        runCatching {
            resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null, signal)?.use { cursor ->
                if (cursor.moveToFirst()) {
                    name = if (cursor.isNull(0)) null else cursor.getString(0)
                    size = if (cursor.isNull(1)) null else cursor.getLong(1).takeIf { it >= 0 }
                }
            }
        }
        return ShareIntake.Stream(uri.scheme, false, type) to Meta(name, size, type)
    }

    fun remove(key: Int) { if (phase == Phase.REVIEW) items = items.filterNot { it.key == key } }

    /** A problem that keeps Send off, or null. Connection problems are checked when you tap Send. */
    val blocker: String? get() = when {
        phase != Phase.REVIEW -> null
        client.loaded && !client.paired -> NOT_PAIRED
        client.remReady != null && !client.desktopControl -> CONTROL_OFF
        text.isBlank() && items.isEmpty() -> "Nothing to send. Add a note or go back."
        items.any { it.problem != null } -> "Remove the files Rem can't take, then send."
        else -> ShareIntake.totalProblem(items.map { it.size })
    }

    fun send() {
        if (phase != Phase.REVIEW || blocker != null || job?.isActive == true) return
        if (getApplication<Application>().getSystemService(KeyguardManager::class.java).isDeviceLocked) { error = LOCKED; return }
        error = null; phase = Phase.SENDING
        val note = text.trim().take(ShareIntake.MAX_TEXT)
        job = viewModelScope.launch {
            val failure = deliver(note)
            if (failure != null) { error = failure; phase = Phase.REVIEW }
            job = null
            if (surfaces == 0) client.shareStop()
        }
    }

    /** Stops a send that hasn't been queued yet. Files already uploaded expire on the desktop unused. */
    fun cancel() { if (phase == Phase.SENDING) { job?.cancel(); job = null; phase = Phase.REVIEW; error = "Stopped. Nothing was sent to Rem." } }

    private fun home() = client.desktopViews["rem.home"] as? RemHome

    /** Returns why the send stopped, or null once the message reached Rem or waits in the encrypted outbox. */
    private suspend fun deliver(note: String): String? {
        step = "Connecting to your desktop…"
        val ready = withTimeoutOrNull(CONNECT_MS) {
            snapshotFlow { VoiceLogic.readiness(client.loaded, client.paired, client.remReady != null, client.desktopControl) }
                .first { it != DeskReadiness.LOADING && it != DeskReadiness.CONNECTING }
        }
        when (ready) {
            null -> return UNREACHABLE
            DeskReadiness.NOT_PAIRED -> return NOT_PAIRED
            DeskReadiness.CONTROL_OFF -> return CONTROL_OFF
            else -> Unit
        }
        if (items.isNotEmpty()) {
            if (!client.connected) return UNREACHABLE
            step = "Checking Rem's chat…"
            val home = withTimeoutOrNull(CONNECT_MS) { snapshotFlow { home() }.first { it != null } } ?: return UNREACHABLE
            if (home.filesThread == null) return FILES_OLD
        }
        val ids = mutableListOf<String>()
        for (item in items) {
            val id = item.fileId?.takeIf { id -> client.files.any { it.id == id && it.state in LIVE } } ?: run {
                step = "Reading ${item.name}…"
                val result = CompletableDeferred<Result<String>>()
                client.importRemFile(item.uri, item.name, onFailed = { result.complete(Result.failure(IllegalStateException(it))) }) { result.complete(Result.success(it)) }
                val outcome = withTimeoutOrNull(READ_MS) { result.await() } ?: Result.failure(IllegalStateException("Reading this file took too long."))
                outcome.getOrElse { reason -> markProblem(item.key, reason.message ?: "Couldn't read this file."); return "Couldn't read ${item.name}: ${reason.message}" }
                    .also { id -> items = items.map { if (it.key == item.key) it.copy(fileId = id) else it } }
            }
            ids.add(id)
        }
        if (ids.isNotEmpty()) {
            step = "Uploading to your desktop…"
            val files = snapshotFlow { client.files.filter { it.id in ids } }
                .first { found -> found.size < ids.size || found.any { it.state !in LIVE } || found.all { it.state == "available" } }
            if (files.size < ids.size || files.any { it.state != "available" }) {
                forgetUploads()
                return "An upload didn't finish. Try again."
            }
        }
        if (home()?.turnState == "replying" || "rem.home" in client.desktopBusy) {
            step = "Waiting for Rem to finish replying…"
            snapshotFlow { home()?.turnState != "replying" && "rem.home" !in client.desktopBusy }.first { it }
        }
        step = "Sending to Rem…"
        val queued = CompletableDeferred<String>()
        client.sendRemMessage(note, ids, onRequest = { queued.complete(it) })
        val request = withTimeoutOrNull(QUEUE_MS) { queued.await() } ?: return client.requestError ?: NOT_QUEUED
        phase = Phase.QUEUED; watching = true
        step = "Queued on this phone. It goes to Rem as soon as your desktop answers."
        // Stop watching after a while so a share left in the background doesn't keep the phone polling. The message
        // stays in the encrypted outbox, and Rem's chat shows whether the desktop took it.
        val (_, refusal) = withTimeoutOrNull(ACCEPT_MS) {
            snapshotFlow { home() to client.remRefusal }.first { (home, refusal) -> refusal?.first == request || VoiceLogic.accepted(home, request) }
        } ?: run { watching = false; step = "Queued on this phone. It goes to Rem when your desktop is reachable; Rem's chat shows when it arrives."; return null }
        watching = false
        if (refusal?.first == request) {
            client.dismissRemUnsent()
            forgetUploads()
            return "Rem didn't take it: ${refusal.second}"
        }
        phase = Phase.SENT
        return null
    }

    /** The desktop claims uploads when a message uses them, so a retry uploads the files again. */
    private fun forgetUploads() { items = items.map { it.copy(fileId = null) } }

    private fun markProblem(key: Int, reason: String) { items = items.map { if (it.key == key) it.copy(problem = reason) else it } }

    /** Upload progress for [item], from 0 to 100, or null before it starts. */
    fun progress(item: Item): Int? {
        val file = client.files.firstOrNull { it.id == item.fileId } ?: return null
        return if (file.state == "available") 100 else if (file.size > 0) (file.bytes * 100 / file.size).toInt() else 0
    }

    companion object {
        private val LIVE = setOf("offered", "receiving", "available")
        private const val META_MS = 5_000L
        private const val CONNECT_MS = 20_000L
        private const val READ_MS = 45_000L
        private const val QUEUE_MS = 10_000L
        private const val ACCEPT_MS = 120_000L
        const val NOTHING = "The other app didn't share anything Rem can use."
        const val LOCKED = "Unlock your phone to send this to Rem."
        const val UNREACHABLE = "Couldn't reach your desktop, so nothing was sent. Make sure both are on the same Wi-Fi, then try again."
        const val FILES_OLD = "Update Raticode on your computer to send files to Rem. You can remove the files and send the text."
        const val NOT_PAIRED = "Pair this phone with Raticode on your computer first. Open Raticode and scan the desktop's QR code."
        const val CONTROL_OFF = "Rem isn't allowed from this phone yet. On your computer, open Paired devices and turn on Use Raticode from this phone."
        const val NOT_QUEUED = "The phone couldn't queue this message. Try again, or open Raticode to check its storage."
    }
}
