package dev.raticode.mobile.live

import android.app.Application
import dev.raticode.mobile.data.OpenedFiles
import android.os.Build
import androidx.lifecycle.AndroidViewModel
import androidx.compose.runtime.*
import dev.raticode.mobile.protocol.*
import dev.raticode.mobile.protocol.v2.MobileCapabilities
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.LinkedBlockingQueue

internal const val DESKTOP_OFF = "To use Rem, organizations and desktop settings from this phone, open Paired devices on your computer and turn on Use Raticode from this phone."
internal const val FILE_LINKS_OFF = "To get files from links, open Paired devices on your computer and turn on Use Raticode from this phone."
internal const val FILE_LINKS_OLD = "Update Raticode on your computer to get files from links on this phone."

/** Single worker owns journal and socket; UI never handles session state or private keys. */
@androidx.annotation.RequiresApi(29)
class LiveClient(app: Application,
    private val connectFactory: (DeviceStore, JSONObject, DeviceInvitation?, DeviceEndpoint, (java.net.Socket) -> Unit, (String) -> Unit) -> DeviceConnection =
        { store, state, invitation, target, bind, status -> DeviceConnection(store, state, invitation, bind, status, target = target) }
) : AndroidViewModel(app) {
    private val store = DeviceStore(app)
    private val transfers = FileTransfers(store)
    private val imports = dev.raticode.mobile.data.DocumentImport(app.contentResolver)
    private val exportOccupied = java.util.concurrent.atomic.AtomicBoolean()
    var files by mutableStateOf<List<LiveFile>>(emptyList()); private set
    data class LiveFile(val id: String, val thread: String, val name: String, val state: String, val direction: String, val bytes: Long, val size: Long)
    private val main = android.os.Handler(android.os.Looper.getMainLooper())
    private val commands = LinkedBlockingQueue<() -> Unit>(256)
    private val pendingDrafts = java.util.concurrent.ConcurrentHashMap<String, String>()
    var sending by mutableStateOf(false); private set
    private val sendOccupied = java.util.concurrent.atomic.AtomicBoolean()
    private val queuedImports = java.util.concurrent.ConcurrentHashMap.newKeySet<dev.raticode.mobile.data.DocumentImport.Prepared>()
    private val importOccupied = java.util.concurrent.atomic.AtomicBoolean()
    @Volatile private var stopped = false
    @Volatile private var connection: DeviceConnection? = null
    private var saved = JSONObject()
    // Requests queued while a received event is applied wait until its journal copy is saved.
    private val commits = CommitGate()
    @Volatile private var reconnect = false
    @Volatile private var activeCandidate: DeviceConnection? = null
    private var locked = false
    private var nextConnect = 0L
    private var failures = 0
    private var nextSync = 0L
    var remReady by mutableStateOf<Boolean?>(null); private set
    var route by mutableStateOf(""); private set
    /** The desktop gave its hardware address and this phone is on its Wi-Fi subnet. */
    var wakeAvailable by mutableStateOf(false); private set
    var wakeBusy by mutableStateOf(false); private set
    var wakeNotice by mutableStateOf<String?>(null); private set
    /** The last connection attempt failed, as opposed to still being in progress. */
    var cantConnect by mutableStateOf(false); private set
    @Volatile private var wakeTarget: WakeOnLan.Target? = null
    private val wakeSending = java.util.concurrent.atomic.AtomicBoolean()
    var lanAvailable by mutableStateOf(false); private set
    var lanHost by mutableStateOf(""); private set
    var lanPort by mutableStateOf(""); private set
    /** Opt-in Tailscale route. The chosen address is kept until the user changes it. */
    var tailscaleEnabled by mutableStateOf(false); private set
    var tailscaleHost by mutableStateOf(""); private set
    var tailscalePort by mutableStateOf(""); private set
    /** True when the address came from the user rather than an authenticated desktop report. */
    var tailscaleChosen by mutableStateOf(false); private set
    var loaded by mutableStateOf(false); private set
    var backgroundEnabled by mutableStateOf(false)
    var status by mutableStateOf("Disconnected"); private set
    var requestError by mutableStateOf<String?>(null); private set
    fun dismissRequestError() { requestError = null }
    /** The desktop locked this phone after a suspicious request ([PhoneLock]); only the computer unlocks it. */
    var lockedByDesktop by mutableStateOf(false); private set
    var pendingPair by mutableStateOf(false); private set
    var paired by mutableStateOf(false); private set
    var connected by mutableStateOf(false); private set
    var creationTemplates by mutableStateOf<List<Pair<String, String>>>(emptyList()); private set
    data class ThreadContext(val provider: String, val model: String, val revision: Long, val project: String?, val effort: String = "", val group: String = "active", val running: Boolean = false, val updatedAt: String = "", val permission: String = "", val canManage: Boolean = false, val deletePending: Boolean = false,
                             val unread: Boolean = false, val readAt: String = "")
    var threadContexts by mutableStateOf<Map<String, ThreadContext>>(emptyMap()); private set
    data class SearchResult(val thread: String, val title: String, val snippet: String, val message: String?)
    var searchResults by mutableStateOf<List<SearchResult>>(emptyList()); private set
    var searching by mutableStateOf(false); private set
    var searchHasMore by mutableStateOf(false); private set
    var searchError by mutableStateOf(""); private set
    private var searchQuery = ""
    var permissionModes by mutableStateOf<Map<String, List<String>>>(emptyMap()); private set
    var defaultPermissions by mutableStateOf<Map<String, String>>(emptyMap()); private set
    var efforts by mutableStateOf<Map<String, Map<String, List<String>>>>(emptyMap()); private set
    var providers by mutableStateOf<Map<String, List<String>>>(emptyMap()); private set
    var projects by mutableStateOf<Map<String, String>>(emptyMap()); private set
    var historyBefore by mutableStateOf<Map<String, Long>>(emptyMap()); private set
    data class Resource(val id: String, val name: String, val kind: String, val enabled: Boolean)
    var threadResources by mutableStateOf<Map<String, List<Resource>>>(emptyMap()); private set
    var remoteControl by mutableStateOf(false); private set
    @Volatile private var remoteAllowed = false
    data class Approval(val id: String, val digest: String, val source: String, val title: String, val summary: String,
                        val requestedAt: String, val instructionRequired: Boolean)
    var approvals by mutableStateOf<List<Approval>>(emptyList()); private set
    var approvalsLoaded by mutableStateOf(false); private set
    private val seenApprovals = mutableSetOf<String>()
    data class SwarmAgent(val name: String, val state: String)
    data class SwarmMessage(val sender: String, val body: String, val createdAt: String)
    data class Swarm(val id: String, val title: String, val status: String, val detail: String, val project: String,
                     val progress: Int, val agents: List<SwarmAgent>?, val messages: List<SwarmMessage>?)
    var swarms by mutableStateOf<List<Swarm>>(emptyList()); private set
    var swarmsLoaded by mutableStateOf(false); private set
    /** Null when no swarm screen is open, "" for the list, or the swarm being viewed. */
    @Volatile private var swarmScreen: String? = null
    var desktopControl by mutableStateOf(false); private set
    @Volatile private var desktopAllowed = false
    /**
     * Whether read and unread changes made here reach the desktop. Older desktops reject those
     * actions and drop the connection, so the phone sends them only after the desktop's overview
     * reports `thread_read`, which needs "Use Raticode from this phone". Without it, reading and
     * archiving still update this phone's own read marks.
     */
    var threadReadSync by mutableStateOf(false); private set
    @Volatile private var threadReadAllowed = false
    /** Latest desktop results by screen key; held in memory only. */
    var desktopViews by mutableStateOf<Map<String, Any>>(emptyMap()); private set
    /** Rem chat search: the words, and what the desktop (or this phone, offline) found for them. */
    var remSearch by mutableStateOf(RemSearchState()); private set
    /** Rem chat messages from before the newest ones rem.home sends, paged back from the desktop, oldest first. */
    var remEarlier by mutableStateOf<List<RemMessage>>(emptyList()); private set
    /** How many messages are still before [remEarlier]; null until a page arrives. */
    var remEarlierCount by mutableStateOf<Int?>(null); private set
    /** Board tickets named in [remEarlier], so its ticket keys show as pills too. */
    var remEarlierTickets by mutableStateOf<Map<String, RemTicket>>(emptyMap()); private set
    var remHistoryBusy by mutableStateOf(false); private set
    var remHistoryError by mutableStateOf(""); private set
    /** A message the chat should scroll to now that it is loaded, with a count so asking again counts. */
    var remJump by mutableStateOf<Pair<String, Int>?>(null); private set
    @Volatile private var searchRequest: String? = null
    @Volatile private var searchBefore: Long? = null
    @Volatile private var historyRequest: String? = null
    @Volatile private var historyTarget: String? = null
    private var historyHops = 0
    var desktopErrors by mutableStateOf<Map<String, String>>(emptyMap()); private set
    /** Screens with a desktop action that has not answered yet. */
    var desktopBusy by mutableStateOf<Set<String>>(emptySet()); private set
    @Volatile private var desktopScreen: Pair<String, JSONObject>? = null
    @Volatile private var desktopFast = false
    private var nextDesktop = 0L
    private val desktopRequests = java.util.concurrent.ConcurrentHashMap<String, Pair<String, Boolean>>()
    private val desktopParts = DesktopParts()
    /** Digest of each screen's latest view; the desktop answers "unchanged" when it still matches. */
    private val desktopDigests = java.util.concurrent.ConcurrentHashMap<String, String>()
    /** Web images in messages. The desktop fetches them over the encrypted link; the phone never contacts their sites. */
    var images by mutableStateOf<Map<String, ChatImage>>(emptyMap()); private set
    /** Whether this desktop fetches message images for the phone. */
    var mediaProxy by mutableStateOf(false); private set
    @Volatile private var mediaAllowed = false
    private var mediaChecked = false
    private val imageQueue = java.util.concurrent.ConcurrentLinkedQueue<String>()
    private val imageRequested = java.util.Collections.synchronizedSet(LinkedHashSet<String>())
    /** Previews of pictures and videos Rem sent, by file ID. Made on this phone from the verified download; memory only. */
    var mediaPreviews by mutableStateOf<Map<String, MediaPreview>>(emptyMap()); private set
    private val mediaRequested = java.util.Collections.synchronizedSet(LinkedHashSet<String>())
    private val mediaDownloads = java.util.Collections.synchronizedSet(HashSet<String>())
    private val mediaWorker = java.util.concurrent.Executors.newSingleThreadExecutor { Thread(it, "device-media-preview").apply { isDaemon = true } }
    /** The Rem chat's file channel on this desktop, or null when the desktop has none. */
    @Volatile private var remFiles: String? = null
    /** A file link the user tapped: waiting for the desktop, then the file it offered. */
    data class LinkFetch(val key: String, val name: String, val fileId: String? = null, val error: String? = null)
    var linkFetch by mutableStateOf<LinkFetch?>(null); private set
    /** Whether this desktop sends the file behind a tapped file link. */
    var fileLinks by mutableStateOf(false); private set
    @Volatile private var fileLinksAllowed = false
    /** Whether this desktop offers any Rem chat attachment on request (`rem.attachment`). */
    var attachmentFiles by mutableStateOf(false); private set
    @Volatile private var attachmentsAllowed = false
    /** Downloads holding Rem chat attachments fetched from the desktop, by attachment ID. */
    var attachmentCopies by mutableStateOf<Map<String, String>>(emptyMap()); private set
    /** Attachment fetches asked for or refused this session, by attachment ID. */
    var attachmentFetches by mutableStateOf<Map<String, RemAttachments.Fetch>>(emptyMap()); private set
    // Pictures and videos fetched once without a tap this session, so an evicted copy isn't fetched again and again.
    private val attachmentAuto = java.util.Collections.synchronizedSet(HashSet<String>())
    // Fetches asked for before this connection's overview said the desktop takes them; sent once it does.
    private val attachmentWaiting = java.util.concurrent.ConcurrentHashMap<String, Pair<String, RemFile>>()
    /**
     * Whether this desktop sends result cards, progress and source previews. Older desktops reject the
     * `media.preview` read and close the session, so the phone asks only after the desktop reports this.
     */
    var richResults by mutableStateOf(false); private set
    @Volatile private var richAllowed = false
    /** Voice tidying is on in desktop Settings: dictated words stay hidden until [tidyVoice] answers. */
    var voiceTidy by mutableStateOf(false); private set
    @Volatile private var tidyAllowed = false
    /** Dictations waiting for the desktop's tidied words, with the words as heard to fall back on. */
    private val tidyWaiters = java.util.concurrent.ConcurrentHashMap<String, Pair<String, kotlinx.coroutines.CompletableDeferred<TidyResult>>>()
    /** Source cards for Rem's results, fetched by the desktop; the phone never contacts the sites. */
    var previews by mutableStateOf<Map<String, ChatPreview>>(emptyMap()); private set
    private val previewQueue = java.util.concurrent.ConcurrentLinkedQueue<String>()
    private val previewRequested = java.util.Collections.synchronizedSet(LinkedHashSet<String>())
    /** Whether this desktop accepts Retry review from the phone (`rem.run.retry_review`). */
    var reviewRetry by mutableStateOf(false); private set
    @Volatile private var reviewRetryAllowed = false
    /** The desktop takes `rem.run.pause` and `rem.run.resume` (RunPause). */
    var runPause by mutableStateOf(false); private set
    @Volatile private var runPauseAllowed = false
    /**
     * Rem's reply as it is written, read as small `rem.draft` deltas while Rem replies (a desktop with `draft_deltas`).
     * Deltas are reads, not pushed events, so no fragment is stored in either side's durable outbox or journal.
     */
    var remDraft by mutableStateOf(""); private set
    @Volatile private var draftAllowed = false
    @Volatile private var homeReplying = false
    private var replyDraft = RemRich.Draft()
    private var nextDraft = 0L
    /** A Rem message the desktop refused, kept so it can be sent again or edited instead of lost. */
    data class RemUnsent(val text: String, val fileIds: List<String>, val detail: String = "", val replyTo: RemReference? = null)
    var remUnsent by mutableStateOf<RemUnsent?>(null); private set
    /** A Rem message sent while its files were uploading; it goes once they have all arrived. Kept in the journal. */
    data class RemHeld(val text: String, val fileIds: List<String>, val replyTo: RemReference? = null)
    var remHeld by mutableStateOf<RemHeld?>(null); private set
    /** The latest refused Rem message: its request ID and the desktop's reason. Voice mode watches it. */
    var remRefusal by mutableStateOf<Pair<String, String>?>(null); private set
    private val remSends = java.util.concurrent.ConcurrentHashMap<String, RemUnsent>()
    /** Whether this desktop answers `rem.notices`, the read behind Rem's chat alerts. Asked again on every connection. */
    @Volatile private var noticesAllowed = false
    /** Whether this desktop sends Rem's chat alerts: null until its overview arrives. */
    var remAlerts by mutableStateOf<Boolean?>(null); private set
    /** Whether this desktop's overview advertises the `rem.notices` filter. An older desktop must never be sent it. */
    @Volatile private var noticesFilterAllowed = false
    /** Whether this desktop can send alerts about work this phone didn't start: null until its overview arrives. */
    var remAlertFilter by mutableStateOf<Boolean?>(null); private set
    private var nextNotices = 0L
    /** The phone-started runs the last read of Rem's tasks found running. Used only on the sync thread. */
    private var progressRuns = emptyList<TaskProgress.Run>()
    /** When a read last confirmed [progressRuns]; 0 when there's nothing to confirm. */
    private var progressFresh = 0L
    private var nextProgress = 0L
    /** The just-turned-on alert switches the read now out is the first to carry. */
    private var noticeSettling = emptyList<String>()
    /** Whether a connection report this session said if the desktop lets this phone use Rem. */
    @Volatile private var desktopKnown = false
    /** Replies typed into Rem's alert that the desktop hasn't answered yet. */
    private val alertReplies = java.util.concurrent.ConcurrentHashMap.newKeySet<String>()
    /** While a reply from Rem's alert is out, the link stays up until then even with the app closed; 0 when none. */
    @Volatile private var replyUntil = 0L
    /** Whether this desktop shows workflows to the phone: null until its overview arrives this session. */
    var workflows by mutableStateOf<Boolean?>(null); private set
    @Volatile private var workflowsAllowed = false
    /** Whether this desktop shows its projects' Git status to the phone: null until its overview arrives this session. */
    var sourceControl by mutableStateOf<Boolean?>(null); private set
    @Volatile private var sourceControlAllowed = false
    /** Whether this desktop answers history, commit and diff reads; null until its overview arrives this session. */
    var sourceControlHistory by mutableStateOf<Boolean?>(null); private set
    @Volatile private var sourceHistoryAllowed = false
    /** Whether this desktop lets the phone browse its projects' files: null until its overview arrives this session. */
    var fileExplorer by mutableStateOf<Boolean?>(null); private set
    @Volatile private var explorerAllowed = false
    @Volatile private var explorerKnown = false
    /** A file opened in the file explorer: waiting for the desktop, then the download it offered, or why it can't. */
    var explorerFetch by mutableStateOf<FileExplorer.Fetch?>(null); private set
    @Volatile private var awaitingExplorer: String? = null
    // An open asked for before this connection's overview said the desktop takes it; sent once it does.
    @Volatile private var explorerWaiting: FileExplorer.Fetch? = null
    /** The desktop's answer to the latest workflow run, stop or resume, by workflow ID. */
    var workflowNotice by mutableStateOf<Pair<String, String>?>(null); private set
    /** Whether this desktop shares its application log with the phone: null until its overview arrives this session. */
    var developerLogSupported by mutableStateOf<Boolean?>(null); private set
    @Volatile private var developerLogAllowed = false
    /** The desktop shows its browser tabs to the phone's browser and opens pages sent to it; null until it says. */
    var desktopBrowser by mutableStateOf<Boolean?>(null); private set
    @Volatile private var browserAllowed = false
    /** Whether this desktop takes the five-section Organizations actions: null until its overview arrives this session. */
    var organizationsV2 by mutableStateOf<Boolean?>(null); private set
    @Volatile private var organizationsV2Allowed = false
    @Volatile private var organizationsV2Known = false
    /** Whether this desktop takes `organizations.schedule`: null until its overview arrives this session. */
    var organizationSchedule by mutableStateOf<Boolean?>(null); private set
    @Volatile private var organizationScheduleAllowed = false
    /** Whether this desktop takes `organizations.backlog_pull`: null until its overview arrives this session. */
    var organizationBacklogPull by mutableStateOf<Boolean?>(null); private set
    @Volatile private var organizationBacklogPullAllowed = false
    /** Whether this desktop takes `organizations.requests`: null until its overview arrives this session. */
    var organizationRequests by mutableStateOf<Boolean?>(null); private set
    @Volatile private var organizationRequestsAllowed = false
    /** Whether this desktop takes `organizations.auto_mode`: null until its overview arrives this session. */
    var organizationAutoMode by mutableStateOf<Boolean?>(null); private set
    @Volatile private var organizationAutoModeAllowed = false
    /** The last approve or decline answer from `organizations.requests`, by organization ID; held in memory only. */
    var organizationRequestResults by mutableStateOf<Pair<String, List<OrgRequests.Result>>?>(null); private set
    fun dismissOrganizationRequestResults() { organizationRequestResults = null }
    /** Organization actions tapped before this session's overview said whether the desktop takes them. */
    private val organizationsV2Waiting = java.util.concurrent.ConcurrentLinkedQueue<Pair<String, JSONObject>>()
    /** Whether this desktop has the global board and Rem's self-work: null until its overview arrives this session. */
    var kanban by mutableStateOf<Boolean?>(null); private set
    @Volatile private var kanbanAllowed = false
    /** Whether this desktop lists the board's people and takes their project changes: null until its overview arrives this session. */
    var kanbanParticipants by mutableStateOf<Boolean?>(null); private set
    @Volatile private var kanbanParticipantsAllowed = false
    /** Whether this desktop keeps each ticket's history and takes comments: null until its overview arrives this session. */
    var kanbanComments by mutableStateOf<Boolean?>(null); private set
    @Volatile private var kanbanCommentsAllowed = false
    @Volatile private var kanbanKnown = false
    /** Board and self-work actions tapped before this session's overview said whether the desktop takes them. */
    private val kanbanWaiting = java.util.concurrent.ConcurrentLinkedQueue<Pair<String, JSONObject>>()
    /** When Rem's chat next asks about self-work's report, so the phone can say what happened while you were away. */
    private var nextSelfWork = 0L
    /** The desktop's log as this phone holds it, oldest first, with where the next read starts. Memory only. */
    var developerLog by mutableStateOf<List<LogEntry>>(emptyList()); private set
    var developerLogInfo by mutableStateOf<LogPage?>(null); private set
    /** True after entries went missing between two reads, such as after a long time offline. */
    var developerLogSkipped by mutableStateOf(false); private set
    @Volatile private var logLevels: Set<String> = DeveloperLog.DEFAULT_LEVELS
    private var logCursor: JSONObject? = null
    private var logHeld: List<LogEntry> = emptyList()
    @Volatile private var logRequest: String? = null
    /** This phone's own connection events, kept in memory for the Developer log. */
    private val phoneLog = PhoneLog()
    var phoneLogEntries by mutableStateOf<List<LogEntry>>(emptyList()); private set
    // The link sheet still open, so only its file downloads, and files already sent per link.
    @Volatile private var awaitingLink: String? = null
    private val linkFiles = java.util.concurrent.ConcurrentHashMap<String, String>()
    private val linkDownloads = java.util.concurrent.ConcurrentHashMap.newKeySet<String>()
    private var nextApprovals = 0L
    private var nextSwarms = 0L
    private var nextHistory = 0L
    private var activeThread: String? = null
    // The open chat on screen; only it is marked read on this phone.
    private var viewingThread: String? = null
    private var nextCatalog = 0L
    var creatingThread by mutableStateOf(false); private set
    var createdThread by mutableStateOf<String?>(null); private set
    fun consumeCreatedThread() { createdThread = null }
    var threads by mutableStateOf<List<Pair<String, String>>>(emptyList()); private set
    var messages by mutableStateOf<List<LiveMessage>>(emptyList()); private set
    var preview by mutableStateOf<DeviceInvitation?>(null); private set
    var drafts by mutableStateOf<Map<String, String>>(emptyMap()); private set
    var fleet by mutableStateOf(""); private set
    data class Attachment(val id: String, val name: String, val size: Long)
    data class LiveMessage(val id: String, val thread: String, val text: String, val state: String, val detail: String = "",
        val sourceId: String = id, val offset: Long = 0, val kind: String = "", val groupId: String = "",
        val trace: Map<String, String> = emptyMap(), val attachments: List<Attachment> = emptyList(),
        val running: Boolean = false, val durationMs: Long? = null, val completedAt: String = "", val truncated: Boolean = false,
        val startedAt: Long? = null)
    private fun publishStatus(value: String, level: String = "info") {
        phoneLog.add(level, value)
        val entries = phoneLog.snapshot()
        main.post { status = value; phoneLogEntries = entries }
    }
    /** Adds to this phone's own log without changing the status line. */
    private fun notePhone(level: String, value: String) {
        phoneLog.add(level, value)
        val entries = phoneLog.snapshot()
        main.post { phoneLogEntries = entries }
    }
    // Device sockets bind to Wi-Fi, or to Tailscale when the user turns it on. Reconnect promptly
    // when either returns. A new Wi-Fi network tries the direct local route first again.
    @Volatile private var preferTailscale = false
    private val wifi = WifiRoute(app, onLinks = { enqueue { publishWake() } }, onBoundLost = { enqueue { routeLost(DevicePath.WIFI) } }) { enqueue { preferTailscale = false; if (reconnect && connection == null) { nextConnect = 0; failures = 0 } } }
    private val tailscale = TailscaleRoute(app, onBoundLost = { enqueue { routeLost(DevicePath.TAILSCALE) } }) { enqueue {
        if (reconnect && connection == null && DeviceRoutes.tailscaleEnabled(saved)) { nextConnect = 0; failures = 0 }
    } }
    init {
        Thread({
            // A notification left by a process that died has nobody to update it; the first read brings it back if the task still runs.
            TaskNotification.clear(getApplication())
            try { saved = store.load(); store.save(saved); publish() }
            catch (failure: Exception) { publishStatus(loadFailureGuidance(failure), "error"); locked = true }
            main.post { loaded = true }
            OpenedFiles.cleanup(getApplication<Application>().cacheDir)
            dev.raticode.mobile.data.CameraCaptures.cleanup(getApplication<Application>().cacheDir)
            dev.raticode.mobile.data.HandoffFiles.cleanup(getApplication<Application>().cacheDir)
            if (!locked) try { recoverImports(); transfers.cleanup(saved) }
            catch (_: Exception) { publishStatus("Some removed files could not be cleaned up. Free device storage and retry removal.") }
            while (!stopped) {
                try {
                    commands.poll()?.invoke()
                    flushDrafts()
                    if (reconnect && connection == null && System.currentTimeMillis() >= nextConnect) connectNow(null)
                    connection?.poll()?.let { event -> commits.during { apply(event) } }
                    connection?.takeIf { it.stalled() }?.let { throw ConnectionFailure(stalledGuidance(it.route)) }
                    if (connection != null) {
                        val now = android.os.SystemClock.elapsedRealtime()
                        if (now >= nextSync) requestSync()
                        pollLive(now)
                    }
                    endAlertReply()
                    expireProgress()
                    if (connection == null) Thread.sleep(100)
                } catch (_: InterruptedException) { break }
                catch (failure: LiveStorageLimit) {
                    connection?.close(); connection = null; reconnect = false
                    main.post { connected = false }
                    publishStatus(failure.message ?: "Protected storage is full. Clear completed history or remove finished files, then reconnect.", "error")
                }
                catch (failure: Exception) {
                    connection?.close(); connection = null
                    main.post { connected = false; cantConnect = true }
                    failures = minOf(failures + 1, 8)
                    // Local reconnects are cheap; regaining Wi-Fi also retries immediately.
                    nextConnect = System.currentTimeMillis() + minOf(30000L, 2000L shl failures) + (0..1000).random()
                    activeCandidate = null
                    publish()
                    val reason = (failure as? ConnectionFailure)?.guidance
                        ?: if (DeviceRoutes.tailscaleEnabled(saved)) "Connection interrupted. Check that Raticode is running on your desktop and that this phone is on its Wi-Fi or connected to Tailscale."
                        else "Connection interrupted. Check that Raticode is running on your desktop and both devices use the same Wi-Fi."
                    val recovery = if (reconnect) " Retrying after a delay. Queued messages remain on this phone."
                        else " Retry the connection or scan a fresh invitation."
                    notePhone("debug", "Connection attempt ended: ${failure.javaClass.simpleName}")
                    publishStatus(reason + recovery, "warn")
                }
            }
            transfers.release()
        }, "device-sync").apply { isDaemon = true; start() }
    }
    /** The network under the open session went away: reconnect now instead of waiting for errors. */
    private fun routeLost(path: DevicePath) {
        val current = connection ?: return
        if (current.route != path.label) return
        current.close(); connection = null
        main.post { connected = false }
        if (reconnect) { nextConnect = 0; failures = 0 }
        publish()
        publishStatus("${path.label} changed. Reconnecting to desktop; queued messages stay on this phone.")
    }
    private fun enqueue(action: () -> Unit): Boolean {
        if (stopped || !commands.offer {
            try { action() }
            catch (failure: LiveStorageLimit) { publishStatus(failure.message ?: "Protected storage is full.") }
            // A failure on this phone is not a network problem: say so and keep the connection.
            // Network and disk I/O failures still reach the sync loop, which reconnects.
            catch (failure: Exception) { if (reachesSyncLoop(failure)) throw failure; publishStatus(commandFailureGuidance(failure), "warn") }
        }) {
            publishStatus("Device commands are busy. Your draft is kept. Wait a moment and try again.")
            return false
        }
        return true
    }
    private fun recoverImports() {
        val next = JSONObject(saved.toString())
        if (transfers.recoverImports(next)) { store.save(next); saved = next }
    }
    private var draftSaveError: String? = null
    private fun flushDrafts(): Boolean {
        if (pendingDrafts.isEmpty()) return true
        if (locked) return false
        val changes = pendingDrafts.toMap()
        val next = JSONObject(saved.toString())
        val drafts = next.optJSONObject("drafts") ?: JSONObject()
        changes.forEach { (thread, text) -> drafts.put(thread, text) }
        next.put("drafts", drafts)
        try { store.save(next) }
        catch (error: Exception) {
            val message = (error as? LiveStorageLimit)?.message ?: "Draft could not be saved. Keep this screen open, free device storage and try again."
            if (draftSaveError != message) publishStatus(message)
            draftSaveError = message
            return false
        }
        saved = next; draftSaveError = null
        changes.forEach { (thread, text) -> pendingDrafts.remove(thread, text) }
        publish()
        return true
    }
    fun inform(value: String) { publishStatus(value) }
    fun preview(value: String) {
        preview = null
        try { preview = DeviceInvitation.parse(value.trim()); status = "Review the desktop identity before pairing." }
        catch (error: InvitationException) { status = error.problem.guidance }
        catch (_: Exception) { status = "This is not a valid Raticode desktop invitation. Scan the QR in desktop Device settings or paste its complete invitation link." }
    }
    fun clearPreview() { preview = null }
    fun pair() {
        val invite = preview ?: return
        preview = null
        enqueue {
            check(!saved.has("peer"))
            if (saved.optJSONObject("pending_peer")?.optString("responder_spki_sha256") != invite.pin) {
                // Addresses entered for another desktop do not carry over; the Tailscale switch does.
                val next = JSONObject(saved.toString()).apply { remove("lan_override"); optJSONObject("tailscale")?.remove("endpoint") }
                store.save(next); saved = next
            }
            connectNow(invite)
        }
    }
    fun connect() { enqueue { reconnect = true; nextConnect = 0; failures = 0 } }
    /**
     * One tap, one packet. This runs apart from the sync thread, which may be waiting on a
     * connection attempt to the sleeping desktop. Nothing confirms a wake, so the notice only
     * says the signal went out, and the usual reconnect is what finds the desktop.
     */
    fun wakeComputer() {
        val target = wakeTarget ?: return
        if (!wakeSending.compareAndSet(false, true)) return
        wakeBusy = true
        kotlin.concurrent.thread(name = "wake-computer", isDaemon = true) {
            val sent = runCatching { wifi.broadcast(target.desktop, WakeOnLan.packet(target.mac)) }.getOrDefault(false)
            main.post { wakeNotice = if (sent) WakeOnLan.SENT else WakeOnLan.NOT_SENT }
            if (sent) connect()
            main.postDelayed({ wakeNotice = null; wakeBusy = false; wakeSending.set(false) }, WakeOnLan.PAUSE_MILLIS)
        }
    }
    private fun publishWake() {
        val target = WakeOnLan.target(saved)
        wakeTarget = target
        val available = target != null && wifi.sharesSubnetWith(target.desktop)
        main.post { wakeAvailable = available }
    }
    @Volatile private var onScreen = false
    @Volatile private var remChatShown = false
    /** Rem's chat tab is the screen showing in the app. Voice mode opened over it closes once a message is queued. */
    val remChatOnScreen: Boolean get() = userSees && remChatShown
    /** The app is on screen and not hidden behind App lock, so the user can see what it shows. The connection stays either way. */
    private val userSees: Boolean get() = onScreen && !dev.raticode.mobile.applock.AppLockRuntime.locked(getApplication())
    fun showingRemChat(shown: Boolean) { remChatShown = shown }
    fun foreground() { enqueue { onScreen = true; markViewed(); if (remShown()) RemNotifications.clear(getApplication()); if (saved.has("peer")) { reconnect = true; nextConnect = 0; nextSync = 0 } } }
    fun background() { enqueue { onScreen = false }; if (!backgroundEnabled && !voiceActive && !shareActive && replyUntil == 0L) disconnect() }
    @Volatile private var voiceActive = false
    /**
     * Voice mode is open, possibly with the app closed. Connects like the app coming to the screen and reads Rem's
     * chat about once a second until [voiceStop]. It doesn't mark any thread read.
     */
    fun voiceStart() { voiceActive = true; enqueue { if (saved.has("peer")) { reconnect = true; nextConnect = 0; nextSync = 0; nextDesktop = 0 } } }
    /** Voice mode closed. Disconnects unless the app is on screen or background sync is on. */
    fun voiceStop() {
        voiceActive = false
        enqueue {
            if (onScreen || backgroundEnabled || voiceActive || shareActive || replyUntil != 0L) return@enqueue
            reconnect = false; connection?.close(); connection = null; main.post { connected = false }
            publishStatus("Disconnected. Pairing is retained.")
        }
    }
    @Volatile private var shareActive = false
    /**
     * The Share with Rem screen is open over another app. Like voice mode, it connects and reads Rem's chat without
     * marking any thread read, so the screen can tell when the desktop has accepted what you sent.
     */
    fun shareStart() { shareActive = true; enqueue { if (saved.has("peer")) { reconnect = true; nextConnect = 0; nextSync = 0; nextDesktop = 0 } } }
    /** The share screen left. Anything already queued stays in the encrypted outbox and goes out on the next connection. */
    fun shareStop() {
        shareActive = false
        enqueue {
            if (onScreen || backgroundEnabled || voiceActive || shareActive || replyUntil != 0L) return@enqueue
            reconnect = false; connection?.close(); connection = null; main.post { connected = false }
            publishStatus("Disconnected. Pairing is retained.")
        }
    }
    /** The chat currently on screen, or null on any other page. */
    fun viewThread(thread: String?) { enqueue { viewingThread = thread; markViewed() } }
    private fun markViewed() {
        val next = JSONObject(saved.toString())
        val viewed = viewingThread?.takeIf { userSees }
        val before = readMark(next, viewed)
        if (ThreadActivity.markRead(next, viewed)) { store.save(next); saved = next; publish(); sendViewedRead(viewed, before) }
    }
    private fun readMark(state: JSONObject, thread: String?) = thread?.let { state.optJSONObject("read_marks")?.optString(it) }.orEmpty()
    /** Opening an unread thread here marks it read on the desktop too, when the desktop accepts that. */
    private fun sendViewedRead(thread: String?, before: String) {
        if (thread == null || !threadReadAllowed) return
        val mark = readMark(saved, thread)
        if (mark.isEmpty() || mark == before) return
        val list = saved.optJSONArray("threads") ?: JSONArray()
        val entry = (0 until list.length()).map { list.getJSONObject(it) }.firstOrNull { it.optString("thread_id") == thread } ?: return
        if (!entry.optBoolean("can_manage") || entry.optBoolean("delete_pending")) return
        try { queue("thread.manage", thread, JSONObject().put("action", "read").put("expected_revision", entry.optLong("revision", 1))) }
        catch (_: LiveStorageLimit) { /* The phone's own mark stays; the desktop catches up on the next open. */ }
    }
    fun useLanAddress(host: String, port: String) {
        val endpoint = try {
            JSONObject().put("host", host.trim()).put("port", port.trim().toInt()).also(DeviceInvitation::validateLanEndpoint)
        } catch (_: Exception) {
            publishStatus("Enter a private IP address and a port from 1 to 65535, as shown in desktop Paired devices.")
            return
        }
        enqueue {
            check(saved.has("peer") || saved.has("pending_peer"))
            val next = JSONObject(saved.toString()).put("lan_override", endpoint)
            store.save(next); saved = next; publish()
            // Reconnect to the address the user just entered, even if another one works.
            connection?.close(); connection = null; main.post { connected = false }
            reconnect = true; nextConnect = 0; failures = 0
        }
    }
    /** Turns the opt-in Tailscale route on or off. Turning it off drops a Tailscale session at once. */
    fun setTailscale(enabled: Boolean) { enqueue {
        if (locked) return@enqueue
        val settings = JSONObject((saved.optJSONObject("tailscale") ?: JSONObject()).toString()).put("enabled", enabled)
        val next = JSONObject(saved.toString()).put("tailscale", settings)
        // Same save: a queued request for the Tailscale address must not outlive the switch.
        val dropped = !enabled && DeviceRoutes.dropTailnetRequests(next)
        store.save(next); saved = next; publish()
        if (!enabled && connection?.route == DevicePath.TAILSCALE.label) {
            connection?.close(); connection = null; main.post { connected = false }
            publishStatus("Tailscale is off. Raticode connects only over Wi-Fi.")
        }
        preferTailscale = false
        if (dropped && connection != null) requestSync()
        if (saved.has("peer") || saved.has("pending_peer")) { reconnect = true; nextConnect = 0; failures = 0 }
    } }
    /** Saves the desktop's Tailscale address. It is never replaced by a desktop Wi-Fi report. */
    fun useTailscaleAddress(host: String, port: String) {
        val endpoint = try {
            DeviceInvitation.validateTailnetEndpoint(JSONObject().put("host", host.trim()).put("port", port.trim().toInt()))
        } catch (_: Exception) {
            publishStatus("Enter the desktop's Tailscale IP address (100.64.0.0/10 or fd7a:115c:a1e0::/48) and a port from 1 to 65535.")
            return
        }
        enqueue {
            if (locked) return@enqueue
            val settings = JSONObject((saved.optJSONObject("tailscale") ?: JSONObject()).toString())
                .put("enabled", true).put("endpoint", endpoint.json())
            val next = JSONObject(saved.toString()).put("tailscale", settings)
            store.save(next); saved = next; publish()
            // Use the new address now unless a direct Wi-Fi session is already working.
            if (connection?.route != DevicePath.WIFI.label) { connection?.close(); connection = null; main.post { connected = false } }
            if (saved.has("peer") || saved.has("pending_peer")) { reconnect = true; nextConnect = 0; failures = 0 }
        }
    }
    fun clearTailscaleAddress() { enqueue {
        if (locked) return@enqueue
        val settings = saved.optJSONObject("tailscale") ?: return@enqueue
        val next = JSONObject(saved.toString()).put("tailscale", JSONObject(settings.toString()).apply { remove("endpoint") })
        store.save(next); saved = next; publish()
    } }
    private fun lanEndpoint(): JSONObject? = saved.optJSONObject("lan_override") ?:
        (saved.optJSONObject("peer") ?: saved.optJSONObject("pending_peer"))?.optJSONObject("lan_endpoint")
    private fun requestSync(offset: Long = 0) {
        nextSync = android.os.SystemClock.elapsedRealtime() + 5000
        val pending = saved.optJSONArray("outbox") ?: JSONArray()
        if ((0 until pending.length()).any { JSONObject(pending.getString(it)).optString("type") == "sync.request" }) return
        queue("sync.request", null, DeviceRoutes.requestFields(saved, JSONObject().put("after_sequence", saved.opt("cursor") ?: JSONObject.NULL)
            .put("limit", 100).put("include_connection", true).put("include_thread_details", true).put("include_activity", true).put("threads_offset", offset)))
        if (offset == 0L) {
            if (android.os.SystemClock.elapsedRealtime() >= nextCatalog || saved.optJSONObject("catalog")?.optJSONArray("providers")?.length() == 0) {
                nextCatalog = android.os.SystemClock.elapsedRealtime() + 60000
                queue("catalog.request", null, JSONObject())
            }
            activeThread?.let { requestHistory(it, null) }
        }
    }
    fun disconnect() {
        reconnect = false; activeCandidate?.close(); connection?.close()
        enqueue { reconnect = false; connection?.close(); connection = null; main.post { connected = false; cantConnect = false }; publishStatus("Disconnected. Pairing is retained.") }
    }
    fun reset() { imports.cancel(); importOccupied.set(false); reconnect = false; activeCandidate?.close(); connection?.close(); enqueue {
        reconnect = false; connection?.close(); connection = null; pendingDrafts.clear(); transfers.release(); store.reset(); saved = store.load(); store.save(saved); locked = false
        OpenedFiles.cleanup(getApplication<Application>().cacheDir, keepMillis = 0)
        dev.raticode.mobile.data.CameraCaptures.cleanup(getApplication<Application>().cacheDir, keepMillis = 0)
        linkFiles.clear(); linkDownloads.clear(); awaitingLink = null; awaitingExplorer = null; explorerWaiting = null; mediaRequested.clear(); mediaDownloads.clear(); attachmentAuto.clear(); attachmentWaiting.clear()
        main.post { attachmentFetches = emptyMap() }
        alertReplies.clear(); RemNotifications.clear(getApplication())
        progressRuns = emptyList(); progressFresh = 0; TaskNotification.clear(getApplication())
        publish(); main.post { connected = false; cantConnect = false; linkFetch = null; mediaPreviews = emptyMap() }; publishStatus("Local pairing deleted. Also revoke this phone on desktop.")
    } }
    private fun connectNow(invite: DeviceInvitation?) {
        check(!stopped && !locked)
        check(Build.VERSION.SDK_INT >= 29) { "Android 10 required for TLS 1.3" }
        // The outbox is resent below; never resend a Tailscale address request with the switch off.
        if (!DeviceRoutes.tailscaleEnabled(saved)) {
            val next = JSONObject(saved.toString())
            if (DeviceRoutes.dropTailnetRequests(next)) { store.save(next); saved = next }
        }
        // This desktop may be older than the last one; ask what it supports before polling workflows again.
        workflowsAllowed = false; noticesAllowed = false; noticesFilterAllowed = false; sourceControlAllowed = false; sourceHistoryAllowed = false; attachmentsAllowed = false
        explorerAllowed = false; explorerKnown = false
        developerLogAllowed = false; organizationsV2Allowed = false; organizationsV2Known = false; organizationScheduleAllowed = false; organizationBacklogPullAllowed = false; organizationRequestsAllowed = false; organizationAutoModeAllowed = false; logRequest = null; browserAllowed = false
        kanbanAllowed = false; kanbanParticipantsAllowed = false; kanbanCommentsAllowed = false; kanbanKnown = false; nextSelfWork = 0
        JSONObject(saved.toString()).let { next ->
            // An open still waiting for its answer is asked again once the desktop says it takes it.
            val explorerDropped = FileExplorer.dropQueued(next)
            if (explorerDropped) explorerFetch?.takeIf { it.fileId == null && it.error == null && it.key == awaitingExplorer }?.let { explorerWaiting = it }
            if (explorerDropped or Workflows.dropQueuedReads(next) or dropQueuedNoticeReads(next) or SourceControl.dropQueuedReads(next) or RemAttachments.dropQueued(next)
                or DesktopBrowser.dropQueuedReads(next)
                or dropQueuedReads(next, setOf(DeveloperLog.OP, OrganizationModel.NOTE) + Kanban.READS)) {
                store.save(next); saved = next
            }
        }
        main.post { attachmentFetches = attachmentFetches.filterValues { it != RemAttachments.Fetch.Asking } }
        fun attempt(original: DeviceInvitation?, target: DeviceEndpoint): DeviceConnection {
            publishStatus("Connecting to desktop over ${target.path.label}")
            val bind: (java.net.Socket) -> Unit = when (target.path) {
                DevicePath.WIFI -> wifi::bind
                DevicePath.TAILSCALE -> { socket -> tailscale.bind(socket, target.host) }
            }
            var current: DeviceConnection? = null
            try {
                current = connectFactory(store, saved, original, target, bind, ::publishStatus)
                activeCandidate = current
                publish()
                current.establish()
                return current
            } catch (failure: Exception) {
                current?.close(); activeCandidate = null
                if (failure is ConnectionFailure || failure is LiveStorageLimit) throw failure
                throw ConnectionFailure(connectionGuidance(
                    if (current == null) ConnectionStage.CONNECTING else ConnectionStage.AUTHENTICATION, failure, target.path))
            }
        }
        // Try each permitted route in order. Every route uses the same pinned TLS and pairing,
        // so moving on after a failure never weakens authentication.
        fun attemptRoutes(original: DeviceInvitation?): DeviceConnection {
            val peer = original?.json ?: saved.optJSONObject("peer") ?: saved.optJSONObject("pending_peer")
            var last: Exception? = null
            for (target in DeviceRoutes.plan(saved, peer, wifi.available, preferTailscale)) {
                if (stopped) break
                try {
                    return attempt(original, target).also { preferTailscale = target.path == DevicePath.TAILSCALE }
                } catch (failure: LiveStorageLimit) { throw failure }
                catch (failure: Exception) { last = failure }
            }
            throw last ?: ConnectionFailure(NO_ADDRESS_GUIDANCE)
        }
        val candidate = try { attemptRoutes(invite) }
        catch (failure: Exception) {
            // A pending identity may not have reached desktop yet. Retry the exact
            // original QR bytes while it is unexpired; a new QR is needed afterwards.
            val routeUp = wifi.available || (DeviceRoutes.tailscaleEnabled(saved) && tailscale.available)
            val pending = if (invite == null && !saved.has("peer") && !stopped && routeUp && failure !is LiveStorageLimit)
                runCatching { DeviceInvitation.parse("raticode://pair?v=2&data=" + saved.getString("pending_invitation")) }.getOrNull()
                else null
            if (pending == null) throw failure
            attemptRoutes(pending)
        }
        check(!stopped)
        activeCandidate = null
        connection = candidate; reconnect = true; failures = 0; mediaChecked = false
        main.post { connected = true; cantConnect = false; route = candidate.route; workflows = null; sourceControl = null; sourceControlHistory = null; fileExplorer = null
            developerLogSupported = null; organizationsV2 = null; organizationSchedule = null; organizationBacklogPull = null; organizationRequests = null; organizationAutoMode = null; organizationRequestResults = null; desktopBrowser = null; kanban = null; kanbanParticipants = null; kanbanComments = null }
        notePhone("info", "Connected to desktop over ${candidate.route}")
        publish()
        val pending = saved.optJSONArray("outbox") ?: JSONArray()
        for (i in 0 until pending.length()) candidate.send(JSONObject(pending.getString(i)))
        requestSync()
        pumpUpload()
    }
    private fun queue(type: String, thread: String?, payload: JSONObject, onCommitted: () -> Unit = {}) {
        check(!locked && !stopped)
        commits.afterCommit { commitQueued(type, thread, payload, onCommitted) }
    }
    private fun commitQueued(type: String, thread: String?, payload: JSONObject, onCommitted: () -> Unit) {
        val (next, event) = LiveJournal.prepare(saved, type, thread, payload)
        transfers.expire(next)
        store.save(next); saved = next; publish()
        main.post(onCommitted)
        transfers.cleanup(saved)
        connection?.send(event)
    }
    fun draft(thread: String, text: String) {
        // Keystrokes are coalesced independently of the bounded command queue.
        if (!stopped) pendingDrafts[thread] = text.take(4000)
    }
    fun send(thread: String, text: String, attachment: String? = null, onCommitted: () -> Unit = {}) =
        sendAttachments(thread, text, listOfNotNull(attachment), onCommitted)
    fun sendAttachments(thread: String, text: String, fileIds: List<String>, onCommitted: () -> Unit = {}) {
        if ((text.isBlank() && fileIds.isEmpty()) || fileIds.size > 8 || text.length > 4000 || !sendOccupied.compareAndSet(false, true)) return
        sending = true
        // Preserve even programmatically submitted text before checking outbox/history capacity.
        pendingDrafts.putIfAbsent(thread, text)
        if (!enqueue {
            try {
                if (!flushDrafts()) return@enqueue
                check(threads.any { it.first == thread })
                val attachments = JSONArray()
                for (attachment in fileIds.distinct()) {
                    val file = saved.getJSONObject("files").getJSONObject(attachment)
                    check(file.getString("thread") == thread && file.getString("direction") == "upload" && file.getString("state") == "available")
                    attachments.put(attachment)
                }
                queue("chat.submit", thread, JSONObject().put("text", text).put("attachment_ids", attachments), onCommitted)
            } finally { sendOccupied.set(false); main.post { sending = false } }
        }) { sendOccupied.set(false); sending = false }
    }
    fun stopRequest(thread: String, request: String) { enqueue {
        val (next, event) = LiveJournal.prepare(saved, "request.cancel", thread, JSONObject().put("reason", "Stopped from phone"), request)
        store.save(next); saved = next; publish(); connection?.send(event)
    } }
    fun clearHistory(thread: String) { enqueue {
        val next = LiveJournal.clearHistory(saved, thread)
        store.save(next); saved = next; publish()
        publishStatus("Completed history cleared on this phone. Pending requests and replay protection are retained.")
    } }
    fun removeFile(id: String) { enqueue {
        val next = JSONObject(saved.toString())
        transfers.remove(next, id)
        store.save(next); saved = next; publish(); transfers.cleanup(saved)
        publishStatus("File removed from this phone. Exported copies and desktop files are unchanged.")
    } }
    /**
     * Attach a file to Rem's chat. It uploads on the desktop's Rem chat file channel. [name] replaces the provider's
     * display name; [onFailed] gets a reason that is safe to show when the file wasn't queued.
     */
    fun importRemFile(uri: android.net.Uri, name: String? = null, onFailed: (String) -> Unit = {}, onImported: (String) -> Unit = {}) {
        val thread = remFiles
        if (thread == null || !desktopAllowed) {
            publishStatus("Update Raticode on your computer to attach files in Rem's chat."); main.post { onFailed("Update Raticode on your computer to send files to Rem.") }; return
        }
        importFile(thread, uri, name, onFailed, onImported)
    }
    fun importFile(thread: String, uri: android.net.Uri, name: String? = null, onFailed: (String) -> Unit = {}, onImported: (String) -> Unit = {}) {
        val failed = { reason: String -> publishStatus(reason); main.post { onFailed(reason) } }
        if (!importOccupied.compareAndSet(false, true)) { failed("A file import is still busy"); return }
        if (!imports.start(uri, name = name) { result ->
            val prepared = result.getOrNull()
            if (prepared == null) {
                importOccupied.set(false)
                failed((result.exceptionOrNull() as? dev.raticode.mobile.data.DocumentImport.Problem)?.message ?: "File import failed or timed out")
            }
            else if (stopped) { prepared.close(); importOccupied.set(false) }
            else {
                queuedImports.add(prepared)
                if (stopped && queuedImports.remove(prepared)) { prepared.close(); importOccupied.set(false); return@start }
                if (!commands.offer {
                if (!queuedImports.remove(prepared)) return@offer
                var importedId: String? = null
                try {
                    val granted = saved.optJSONArray("threads") ?: JSONArray()
                    if (thread != remFiles && (0 until granted.length()).none { granted.getJSONObject(it).getString("thread_id") == thread }) {
                        failed("Thread access changed. Refresh the shared thread list before importing a file.")
                        return@offer
                    }
                    recoverImports()
                    val id = java.util.UUID.randomUUID().toString()
                    val reservation = JSONObject(saved.toString())
                    transfers.reserveImport(reservation, id)
                    store.save(reservation); saved = reservation; importedId = id
                    val next = JSONObject(saved.toString())
                    transfers.expire(next)
                    val offer = transfers.offer(next, thread, prepared.name, prepared.bytes, id)
                    transfers.finishImport(next, id)
                    val (queued, event) = LiveJournal.prepare(next, "file.offer", thread, offer)
                    store.save(queued); saved = queued; publish(); transfers.cleanup(saved)
                    main.post { onImported(id) }
                    connection?.send(event)
                } catch (failure: LiveStorageLimit) { failed(failure.message ?: "File storage is full.") }
                finally {
                    try { if (importedId != null) recoverImports() }
                    finally { prepared.close(); importOccupied.set(false) }
                }
            }) { queuedImports.remove(prepared); prepared.close(); importOccupied.set(false); failed("Device command queue is full") }
            }
        }) { importOccupied.set(false); failed("A file provider is still busy") }
    }
    fun requestFile(id: String) { enqueue {
        val file = saved.getJSONObject("files").getJSONObject(id)
        queue("file.request", file.getString("thread"), JSONObject().put("file_id", id))
    } }
    /**
     * Cancels a transfer. An upload stops on this phone at once, in the same save that queues the desktop's
     * `file.cancel`, so no more of it is sent and later uploads don't wait behind it.
     */
    fun cancelFile(id: String) { enqueue {
        val file = saved.optJSONObject("files")?.optJSONObject(id) ?: return@enqueue
        if (file.getString("direction") == "upload" && file.getString("state") !in setOf("offered", "receiving")) return@enqueue
        val next = JSONObject(saved.toString())
        UploadQueue.cancel(next, id)
        val (queued, event) = LiveJournal.prepare(next, "file.cancel", file.getString("thread"), JSONObject().put("file_id", id))
        val advanced = advance(queued)
        store.save(advanced.state); saved = advanced.state; publish(); transfers.cleanup(saved)
        connection?.send(event); advanced.finish()
    } }
    fun exportFile(id: String, uri: android.net.Uri) { enqueue {
        if (!exportOccupied.compareAndSet(false, true)) { publishStatus("An export provider is still busy"); return@enqueue }
        val bytes = try { transfers.read(saved, id) } catch (error: Exception) { exportOccupied.set(false); throw error }
        Thread({
            try {
                getApplication<Application>().contentResolver.openOutputStream(uri, "wt")!!.use { it.write(bytes) }
                publishStatus("File exported to your chosen destination")
            } catch (_: Exception) { publishStatus("File export failed") }
            finally { bytes.fill(0); exportOccupied.set(false) }
        }, "device-file-export").apply { isDaemon = true; start() }
    } }
    /**
     * Writes an authenticated copy of a downloaded file for another app to open. The copy
     * stays in this app's cache, is shared only through the viewer's read grant and is
     * deleted after [OpenedFiles.KEEP_MILLIS] or on reset.
     */
    fun openFile(id: String, onReady: (Result<java.io.File>) -> Unit) { enqueue {
        if (!exportOccupied.compareAndSet(false, true)) { publishStatus("Another file is still being saved or opened"); return@enqueue }
        val prepared = runCatching {
            val name = saved.getJSONObject("files").getJSONObject(id).getString("name")
            name to transfers.read(saved, id)
        }
        val (name, bytes) = prepared.getOrElse { error -> exportOccupied.set(false); main.post { onReady(Result.failure(error)) }; return@enqueue }
        Thread({
            val result = try { runCatching { OpenedFiles.write(getApplication<Application>().cacheDir, name, bytes) } }
                finally { bytes.fill(0); exportOccupied.set(false) }
            main.post { onReady(result) }
        }, "device-file-open").apply { isDaemon = true; start() }
    } }
    private fun pumpUpload() {
        val before = saved.toString()
        val advanced = advance(JSONObject(before))
        if (advanced.events.isEmpty() && advanced.after.isEmpty() && advanced.state.toString() == before) return
        store.save(advanced.state); saved = advanced.state; publish(); transfers.cleanup(saved)
        advanced.finish()
    }
    /** A state that changed before its save, with what to do once it is saved. */
    private inner class Advanced(var state: JSONObject) {
        val events = mutableListOf<JSONObject>()
        val after = mutableListOf<() -> Unit>()
        fun finish() { after.forEach { it() }; events.forEach { connection?.send(it) } }
    }
    /**
     * Fills the upload window with the next chunks and sends a held Rem message whose files have all arrived, in
     * [state] before it is saved, so a desktop answer and the chunks it lets through cost one save.
     */
    private fun advance(state: JSONObject): Advanced {
        val advanced = Advanced(state)
        if (connection != null) while (UploadQueue.inFlight(advanced.state) < UploadQueue.WINDOW) {
            val chunk = transfers.nextUpload(advanced.state) ?: break
            val (queued, event) = try { LiveJournal.prepare(advanced.state, "file.chunk", chunk.thread, chunk.payload) }
                catch (_: LiveStorageLimit) { break }
            queued.getJSONObject("files").getJSONObject(chunk.id).put("sent", chunk.end)
            advanced.state = queued; advanced.events += event
        }
        releaseHeld(advanced)
        return advanced
    }
    private fun releaseHeld(advanced: Advanced) {
        val held = advanced.state.optJSONObject(HeldSends.KEY) ?: return
        val ids = HeldSends.ids(held)
        val sent = RemUnsent(held.getString("text"), ids, replyTo = held.optJSONObject("reply")?.let(::referenceOf))
        when (val decision = HeldSends.decide(advanced.state, System.currentTimeMillis()/1000)) {
            HeldSends.Check.Send -> {
                // With phone access off on the desktop it waits until it is back on.
                if (!desktopAllowed || connection == null) return
                val params = JSONObject(held.getJSONObject("params").toString()).put("attachment_ids", JSONArray(ids))
                val request = java.util.UUID.randomUUID().toString()
                val (queued, event) = try {
                    LiveJournal.prepare(advanced.state, "desktop.action", null, JSONObject().put("op", "rem.message").put("params", params), request)
                } catch (_: LiveStorageLimit) { return }
                queued.remove(HeldSends.KEY)
                TaskProgress.note(queued, "rem.message", params, request, System.currentTimeMillis())
                advanced.state = queued; advanced.events += event
                advanced.after += {
                    desktopRequests[request] = DesktopViews.key("rem.message", params) to true
                    remSends[request] = sent
                    main.post { desktopBusy = desktopBusy + "rem.home" }
                }
            }
            is HeldSends.Check.Fail -> {
                advanced.state.remove(HeldSends.KEY)
                advanced.after += { main.post { remUnsent = sent.copy(detail = HeldSends.failure(decision.name)) } }
            }
            else -> {}
        }
    }
    /**
     * Takes back a message waiting for its files, so its words, reply and files can return to the message box. The
     * files keep uploading. [onReturned] gets it on the main thread, or isn't called when it has already gone.
     */
    fun editRemHeld(onReturned: (RemHeld) -> Unit) { enqueue {
        val held = saved.optJSONObject(HeldSends.KEY) ?: return@enqueue
        val next = JSONObject(saved.toString()).apply { remove(HeldSends.KEY) }
        store.save(next); saved = next; publish()
        val shown = RemHeld(held.getString("text"), HeldSends.ids(held), held.optJSONObject("reply")?.let(::referenceOf))
        main.post { onReturned(shown) }
    } }
    private fun referenceJson(reference: RemReference) = JSONObject().put("id", reference.id).put("role", reference.role).put("body", reference.body)
        .put("created_at", reference.createdAt).put("kind", reference.kind).put("title", reference.title).put("files", reference.files)
        .put("unavailable", reference.unavailable)
    private fun referenceOf(json: JSONObject) = RemReference(json.getString("id"), json.optString("role"), json.optString("body"),
        json.optString("created_at"), json.optString("kind"), json.optString("title"), json.optInt("files"), json.optBoolean("unavailable"))
    fun createThread(template: String, title: String = "New thread", project: String? = null,
                     provider: String? = null, model: String? = null, effort: String = "",
                     permission: String? = null, firstMessage: String = "", draftText: String = "", onCommitted: () -> Unit = {}) {
        if (title.isBlank() || title.length > 160 || firstMessage.length > 4000 || draftText.length > 4000) return
        if (creatingThread) return
        creatingThread = true
        if (!enqueue {
            try {
                val list = saved.optJSONArray("threads") ?: JSONArray()
                val source = (0 until list.length()).map { list.getJSONObject(it) }.firstOrNull {
                    it.getString("thread_id") == template && it.optBoolean("can_create")
                }
                if (source == null) { main.post { requestError = "Connect to desktop and enable thread sync to create a thread." }; return@enqueue }
                val payload = JSONObject().put("title", title.trim()).put("template_thread_id", template)
                for (field in listOf("project_id", "resource_ids", "provider", "model")) payload.put(field, source.get(field))
                if (provider != null && model != null) {
                    payload.put("project_id", project ?: JSONObject.NULL).put("provider", provider).put("model", model).put("effort", effort)
                    if (!permission.isNullOrBlank()) payload.put("permission_mode", permission)
                }
                val (next, event) = LiveJournal.prepare(saved, "thread.create", null, payload)
                // Commit the first-message intent with creation. Its authenticated reply atomically
                // replaces this intent with one chat.submit, including after reconnect/restart.
                val creations = next.optJSONObject("creations") ?: JSONObject()
                creations.put(event.getString("request_id"), JSONObject().put("text", firstMessage).put("draft", draftText))
                next.put("creations", creations)
                if (firstMessage.isNotBlank() || draftText.isNotBlank()) {
                    pendingDrafts.remove("new-thread", firstMessage.ifBlank { draftText })
                    (next.optJSONObject("drafts") ?: JSONObject().also { next.put("drafts", it) }).put("new-thread", "")
                }
                store.save(next); saved = next; publish(); main.post(onCommitted)
                connection?.send(event)
            } catch (failure: LiveStorageLimit) {
                main.post { requestError = failure.message ?: "Protected storage is full. Your draft is kept." }
            } finally {
                val pending = (saved.optJSONObject("creations")?.length() ?: 0) > 0
                main.post { creatingThread = pending }
            }
        }) creatingThread = false
    }
    fun manageThread(thread: String, action: String, expectedRevision: Long) { enqueue {
        require(action in ThreadSelection.MANAGE_ACTIONS)
        manageNow(listOf(thread), action, mapOf(thread to expectedRevision))
    } }
    /** One change for several threads, such as a selection or a whole section. */
    fun manageThreads(threads: Collection<String>, action: String) { enqueue {
        require(action in ThreadSelection.MANAGE_ACTIONS)
        manageNow(threads, action, emptyMap())
    } }
    private fun manageNow(threads: Collection<String>, action: String, revisions: Map<String, Long>) {
        val list = saved.optJSONArray("threads") ?: JSONArray()
        val known = (0 until list.length()).map { list.getJSONObject(it) }.associateBy { it.optString("thread_id") }
        val chosen = threads.distinct().filter { known[it]?.let { t -> t.optBoolean("can_manage") && !t.optBoolean("delete_pending") } == true }
        // Reading, unreading and archiving show here at once; archiving marks a thread read.
        if (action in setOf("read", "unread", "archive")) {
            val next = JSONObject(saved.toString())
            if (ThreadActivity.setRead(next, chosen, read = action != "unread")) { store.save(next); saved = next; publish() }
        }
        if (action in setOf("read", "unread") && !threadReadAllowed) return
        var sent = 0
        try {
            for (thread in chosen) {
                queue("thread.manage", thread, JSONObject().put("action", action)
                    .put("expected_revision", revisions[thread] ?: known.getValue(thread).optLong("revision", 1)))
                sent++
            }
        } catch (failure: LiveStorageLimit) {
            val left = chosen.size - sent
            main.post { requestError = "$left ${if (left == 1) "thread was" else "threads were"} not changed. ${failure.message}" }
        }
        // The desktop's answer brings unread back sooner than the next scheduled sync.
        if (action == "unread") nextSync = 0
    }
    fun openThread(thread: String?) { enqueue { activeThread = thread; if (thread != null && connection != null) requestHistory(thread, null) } }
    private fun requestHistory(thread: String, before: Long?) {
        val outbox = saved.optJSONArray("outbox") ?: JSONArray()
        if ((0 until outbox.length()).any { JSONObject(outbox.getString(it)).let { e -> e.optString("type") == "thread.history.request" && e.optString("thread_id") == thread } }) return
        queue("thread.history.request", thread, JSONObject().put("before", before ?: JSONObject.NULL).put("include_presentation", true))
    }
    fun loadEarlier(thread: String) { enqueue { historyBefore[thread]?.takeIf { it > 0 }?.let { requestHistory(thread, it) } } }
    fun updateModel(thread: String, provider: String, model: String) = updateContext(thread, provider, model, threadContexts[thread]?.project, threadContexts[thread]?.effort.orEmpty())
    fun updateContext(thread: String, provider: String, model: String, project: String?, effort: String, expectedRevision: Long? = null, permission: String? = null,
                      resources: List<String>? = null) { enqueue {
        val list = saved.optJSONArray("threads") ?: JSONArray()
        val source = (0 until list.length()).map { list.getJSONObject(it) }.firstOrNull { it.optString("thread_id") == thread } ?: return@enqueue
        val payload = JSONObject().put("expected_revision", expectedRevision ?: source.optLong("revision", 1)).put("provider", provider).put("model", model)
            .put("project_id", project ?: JSONObject.NULL).put("effort", effort).put("resource_ids", source.optJSONArray("resource_ids") ?: JSONArray())
        if (permission != null) payload.put("permission_mode", permission)
        // Only an explicit choice changes resources; the desktop ignores the copied list otherwise.
        if (resources != null) payload.put("resource_ids", JSONArray(resources.distinct().take(32))).put("set_resources", true)
        queue("thread.context.update", thread, payload)
    } }
    /** Rem's thoughts and tools refresh about every second while the open thread runs. */
    private fun pollLive(now: Long) {
        if (now >= nextHistory) {
            nextHistory = now + 1000
            activeThread?.takeIf(::running)?.let { requestHistory(it, null) }
        }
        if (remoteAllowed && now >= nextApprovals && !pendingRead("approval.request")) {
            nextApprovals = now + 15000
            queue("approval.request", null, JSONObject())
        }
        // Voice mode and the share screen wait on Rem's chat whichever screen the app shows.
        val remWaiting = voiceActive || shareActive
        val screen = if (remWaiting) "rem.home" to JSONObject() else desktopScreen
        if (screen != null && desktopAllowed && now >= nextDesktop && !pendingDesktop(screen.first, screen.second)
            && (workflowsAllowed || !Workflows.isWorkflowOp(screen.first)) && (sourceControlAllowed || !SourceControl.isOp(screen.first))
            && (sourceHistoryAllowed || screen.first !in SourceControl.LATER) && (explorerAllowed || !FileExplorer.isOp(screen.first))
            && (developerLogAllowed || screen.first != DeveloperLog.OP) && (organizationsV2Allowed || screen.first != OrganizationModel.NOTE)
            && (browserAllowed || screen.first != DesktopBrowser.TABS) && (kanbanAllowed || !Kanban.isOp(screen.first))
            && (kanbanCommentsAllowed || screen.first != Kanban.TIMELINE)) {
            // Git status runs on every open project, so it refreshes less often; Refresh asks at once.
            // The log follows new entries every two seconds while it is open.
            nextDesktop = now + if (remWaiting) 1000 else if (desktopFast || screen.first == DeveloperLog.OP) 2000 else if (SourceControl.isOp(screen.first) || FileExplorer.isOp(screen.first)) 10000 else 5000
            if (screen.first == DeveloperLog.OP) {
                // Each read continues from the last one, so it never carries a digest of an older answer.
                logRequest = queueDesktop("desktop.read", DeveloperLog.OP, DeveloperLog.readParams(logCursor, logLevels))
            } else {
                val params = JSONObject(screen.second.toString())
                desktopDigests[DesktopViews.key(screen.first, params)]?.let { params.put("known", it) }
                queueDesktop("desktop.read", screen.first, params)
            }
        }
        if (desktopAllowed && !mediaChecked) { mediaChecked = true; queueDesktop("desktop.read", "overview", JSONObject()) }
        // The outbox holds the read until its answer arrives, so at most one is out.
        if (noticesAllowed && desktopAllowed && now >= nextNotices && !pendingDesktop("rem.notices", JSONObject())) {
            nextNotices = now + NOTICE_INTERVAL
            readNotices()
        }
        // Only while background sync is on and this phone has work to follow; Rem's chat already reads the same answer while it's open.
        if (backgroundEnabled && desktopAllowed && now >= nextProgress && screen?.first != "rem.home" && !pendingDesktop("rem.home", JSONObject())
            && TaskProgress.watching(TaskProgress.load(saved), System.currentTimeMillis()) && TaskNotification.allowed(getApplication())) {
            nextProgress = now + PROGRESS_INTERVAL
            val params = JSONObject()
            desktopDigests[DesktopViews.key("rem.home", params)]?.let { params.put("known", it) }
            queueDesktop("desktop.read", "rem.home", params)
        }
        // Rem's chat says what self-work did while you were away; the Self-work page reads it itself while open.
        if (kanbanAllowed && desktopAllowed && remChatShown && screen?.first == "rem.home" && now >= nextSelfWork && !pendingDesktop(Kanban.SELF_WORK, JSONObject())) {
            nextSelfWork = now + SELF_WORK_INTERVAL
            val params = JSONObject()
            desktopDigests[Kanban.SELF_WORK]?.let { params.put("known", it) }
            queueDesktop("desktop.read", Kanban.SELF_WORK, params)
        }
        if (draftAllowed && homeReplying && screen?.first == "rem.home" && now >= nextDraft && desktopRequests.values.none { it.first == "rem.draft" }) {
            nextDraft = now + 700
            queueDesktop("desktop.read", "rem.draft", RemRich.draftRead(replyDraft))
        }
        pumpImages()
        pumpPreviews()
        val swarm = swarmScreen
        if (swarm != null && now >= nextSwarms && !pendingRead("swarm.request")) {
            nextSwarms = now + 5000
            queue("swarm.request", null, JSONObject().put("swarm_id", swarm.ifBlank { null } ?: JSONObject.NULL))
        }
    }
    private fun pendingRead(type: String): Boolean {
        val outbox = saved.optJSONArray("outbox") ?: return false
        return (0 until outbox.length()).any { JSONObject(outbox.getString(it)).optString("type") == type }
    }
    private fun running(thread: String): Boolean {
        val list = saved.optJSONArray("threads") ?: JSONArray()
        if ((0 until list.length()).any { list.getJSONObject(it).let { t -> t.optString("thread_id") == thread && t.optBoolean("running") } }) return true
        val local = saved.optJSONArray("messages") ?: JSONArray()
        return (0 until local.length()).any { local.getJSONObject(it).let { m ->
            m.optString("thread") == thread && (m.optString("state") in setOf("Queued on phone", "Accepted by desktop", "accepted", "running")
                || m.optJSONObject("presentation")?.optBoolean("running") == true)
        } }
    }
    /** Polls one desktop screen while it is open; null stops polling. */
    fun openDesktop(op: String?, params: JSONObject = JSONObject()) {
        require(op == null || op in DesktopViews.READS)
        desktopScreen = op?.let { it to JSONObject(params.toString()) }
        // Rem's chat on screen shows what its alert would.
        if (op == "rem.home") RemNotifications.clear(getApplication())
        enqueue { nextDesktop = 0 }
    }
    fun refreshDesktop() { enqueue { nextDesktop = 0 } }
    /** Asks the desktop again what it supports, such as after updating it. */
    fun checkDesktopFeatures() { enqueue { if (desktopAllowed && connection != null) { mediaChecked = true; queueDesktop("desktop.read", "overview", JSONObject()) } } }
    fun dismissWorkflowNotice() { workflowNotice = null }
    fun desktopAction(op: String, params: JSONObject) {
        require(op in DesktopViews.ACTIONS)
        // Only a desktop that reports this extension receives these events.
        if (!desktopAllowed) { requestError = DESKTOP_OFF; return }
        // An older desktop would reject the unknown op and close the session.
        if (Workflows.isWorkflowOp(op) && !workflowsAllowed) { requestError = Workflows.UPDATE_DESKTOP; return }
        if (op == DesktopBrowser.OPEN && !browserAllowed) { requestError = DesktopBrowser.UPDATE_DESKTOP; return }
        if ((op == RunPause.PAUSE || op == RunPause.RESUME) && !runPauseAllowed) { requestError = RunPause.UPDATE_DESKTOP; return }
        val copy = JSONObject(params.toString())
        if (Kanban.isOp(op)) {
            // Tapped before this session's overview answered: it goes once the desktop says it takes it.
            if (!kanbanKnown) { main.post { desktopBusy = desktopBusy + DesktopViews.key(op, copy) }; kanbanWaiting.add(op to copy); return }
            if (!kanbanAllowed) { requestError = Kanban.UPDATE_DESKTOP; return }
            if (op == Kanban.PARTICIPANT && !kanbanParticipantsAllowed) { requestError = Kanban.UPDATE_PARTICIPANTS; return }
            // An older desktop would refuse the unknown action; say what's needed instead.
            if (Kanban.isComment(op, copy) && !kanbanCommentsAllowed) { requestError = Kanban.UPDATE_COMMENTS; return }
        }
        if (needsOrganizationsV2(op, copy)) {
            // Tapped before this session's overview answered: it goes once the desktop says it takes it.
            if (!organizationsV2Known) { main.post { desktopBusy = desktopBusy + DesktopViews.key(op, copy) }; organizationsV2Waiting.add(op to copy); return }
            if (!organizationsV2Allowed) { requestError = OrganizationModel.UPDATE_DESKTOP; return }
            if (op == WorkSchedule.OP && !organizationScheduleAllowed) { requestError = WorkSchedule.UPDATE_DESKTOP; return }
            if (op == BacklogPulls.OP && !organizationBacklogPullAllowed) { requestError = BacklogPulls.UPDATE_DESKTOP; return }
            if (op == OrgRequests.OP && !organizationRequestsAllowed) { requestError = OrgRequests.UPDATE_DESKTOP; return }
            if (op == AutoMode.OP && !organizationAutoModeAllowed) { requestError = AutoMode.UPDATE_DESKTOP; return }
        }
        main.post { desktopBusy = desktopBusy + DesktopViews.key(op, copy) }
        if (!enqueue { queueDesktop("desktop.action", op, copy) }) main.post { desktopBusy = desktopBusy - DesktopViews.key(op, copy) }
    }
    /**
     * Send a Rem chat message with files already uploaded on the Rem chat file channel.
     * [onRequest] receives the request ID, which is also the message's ID in Rem's chat.
     */
    /**
     * Queues a Rem message. [screen] is text from the phone's screen for this reply only; it is sent only to a desktop
     * that advertises `screen_context`, and leaves the encrypted outbox once the desktop answers. [replyTo] is the
     * earlier message this one replies to; its ID waits in the encrypted outbox with the message. With [hold], files
     * still uploading don't stop it: the message waits on this phone as [remHeld] and goes once they have all arrived.
     */
    fun sendRemMessage(text: String, fileIds: List<String>, screen: dev.raticode.mobile.voice.ScreenContext? = null,
                       replyTo: RemReference? = null, hold: Boolean = false, onCommitted: () -> Unit = {}, onRequest: (String) -> Unit = {}) {
        if ((text.isBlank() && fileIds.isEmpty()) || fileIds.size > 8 || text.length > 4000) return
        if (!desktopAllowed) { requestError = DESKTOP_OFF; return }
        val params = RemRich.messageParams(text, desktopViews["rem.home"] as? RemHome, screen?.let { it.app to it.text }, replyTo?.id)
        val thread = remFiles
        main.post { desktopBusy = desktopBusy + "rem.home" }
        if (!enqueue {
            try {
                if (hold && fileIds.isNotEmpty() && HeldSends.decide(saved.optJSONObject("files"), fileIds.distinct(), thread,
                        System.currentTimeMillis()/1000) == HeldSends.Check.Wait) {
                    val next = JSONObject(saved.toString())
                    HeldSends.hold(next, text.trim(), params, fileIds.distinct(), thread!!, replyTo?.let(::referenceJson))
                    store.save(next); saved = next
                    // The message box clears as the waiting message appears.
                    main.post { desktopBusy = desktopBusy - "rem.home"; remUnsent = null; onCommitted() }
                    publish()
                    return@enqueue
                }
                if (fileIds.isNotEmpty()) {
                    val files = saved.getJSONObject("files")
                    for (id in fileIds.distinct()) files.getJSONObject(id).let {
                        check(it.getString("thread") == remFiles && it.getString("direction") == "upload" && it.getString("state") == "available")
                    }
                    params.put("attachment_ids", JSONArray(fileIds.distinct()))
                }
                val request = queueDesktop("desktop.action", "rem.message", params)
                remSends[request] = RemUnsent(text.trim(), fileIds.distinct(), replyTo = replyTo?.takeIf { params.has("refers_to") })
                main.post { remUnsent = null; onCommitted(); onRequest(request) }
            } catch (failure: Exception) {
                main.post { desktopBusy = desktopBusy - "rem.home" }
                if (failure is LiveStorageLimit) throw failure
                publishStatus("An attachment is no longer ready. Attach it again.")
            }
        }) main.post { desktopBusy = desktopBusy - "rem.home" }
    }
    /**
     * Asks the desktop for the file behind a tapped link. The desktop resolves the link from the
     * named message and applies its file checks; no model runs. The tap is the download request,
     * so the offered file downloads while the sheet is open.
     */
    fun fetchLink(link: ChatLink.File, source: LinkSource) {
        val key = ChatLinks.KEY + link.destination
        val known = linkFiles[key]?.takeIf { id -> files.any { it.id == id && it.state in setOf("offered", "receiving", "available") } }
        linkFetch = when {
            known != null -> LinkFetch(key, link.name, known)
            !desktopAllowed -> LinkFetch(key, link.name, error = FILE_LINKS_OFF)
            !fileLinksAllowed -> LinkFetch(key, link.name, error = FILE_LINKS_OLD)
            else -> LinkFetch(key, link.name)
        }
        if (linkFetch?.error != null) return
        awaitingLink = key
        if (known != null) { if (files.firstOrNull { it.id == known }?.state == "offered") requestFile(known); return }
        val params = JSONObject().put("source", source.kind).put("message_id", source.messageId).put("href", link.destination)
        source.threadId?.let { params.put("thread_id", it) }
        if (!enqueue { queueDesktop("desktop.action", "files.link", params) }) linkFetch = LinkFetch(key, link.name, error = "The phone is busy. Try again.")
    }
    fun dismissLinkFetch() { awaitingLink = null; linkFetch = null }
    /**
     * Downloads a file the user opened in the file explorer, or reuses a copy of it as it is now, which also opens
     * offline. The desktop checks the project, the path and the file before it offers it; older explorer copies,
     * then older fetched attachments, are removed first when the phone's file space would pass its budget.
     */
    fun openExplorerFile(projectId: String, path: String, size: Long?, modified: String) {
        val fetch = FileExplorer.Fetch(projectId, path, FileExplorer.name(path))
        if (size != null && size > FileTransfers.MAX_FILE) { explorerFetch = fetch.copy(error = FileExplorer.TOO_LARGE); return }
        explorerFetch = fetch; awaitingExplorer = fetch.key
        fun failed(message: String) = main.post { if (explorerFetch?.key == fetch.key) explorerFetch = explorerFetch?.copy(error = message) }
        if (!enqueue {
            FileExplorer.copy(saved, projectId, path, size, modified)?.let { id ->
                main.post { if (explorerFetch?.key == fetch.key) explorerFetch = explorerFetch?.copy(fileId = id) }
                val file = saved.getJSONObject("files").getJSONObject(id)
                if (file.getString("state") == "offered" && connection != null) queue("file.request", file.getString("thread"), JSONObject().put("file_id", id))
                return@enqueue
            }
            when {
                connection == null -> { failed(FileExplorer.OFFLINE); return@enqueue }
                !desktopAllowed -> { failed(FileExplorer.OFF); return@enqueue }
                // Just after connecting the desktop hasn't said yet whether it takes opens.
                !explorerAllowed && !explorerKnown -> { explorerWaiting = fetch; return@enqueue }
                !explorerAllowed -> { failed(FileExplorer.UPDATE_DESKTOP); return@enqueue }
            }
            if (desktopRequests.values.any { it.first == FileExplorer.KEY + fetch.key }) return@enqueue
            val next = JSONObject(saved.toString())
            val removed = RemAttachments.evictions(next, (size ?: 0).coerceAtLeast(0), FileExplorer.files(next))
            if (removed == null) { failed(FileExplorer.NO_ROOM); return@enqueue }
            if (removed.isNotEmpty()) {
                removed.forEach { transfers.remove(next, it) }
                RemAttachments.forget(next, removed); FileExplorer.forget(next, removed)
                store.save(next); saved = next; publish(); transfers.cleanup(saved)
            }
            queueDesktop("desktop.action", FileExplorer.OPEN, FileExplorer.openParams(fetch))
        }) explorerFetch = fetch.copy(error = "The phone is busy. Try again.")
    }
    fun dismissExplorerFetch() { awaitingExplorer = null; explorerWaiting = null; explorerFetch = null }
    /** Stores which download holds an opened explorer file and starts it once its offer is here. */
    private fun receiveExplorerFile(state: JSONObject, key: String, body: JSONObject?, detail: String?) {
        val fetchKey = key.removePrefix(FileExplorer.KEY)
        val project = fetchKey.substringBefore('\n'); val path = fetchKey.substringAfter('\n')
        val id = body?.optString("file_id")?.takeIf { runCatching { java.util.UUID.fromString(it).toString() == it }.getOrDefault(false) }
        if (id != null) {
            FileExplorer.remember(state, project, path, id, body.optLong("size"), body.optString("modified").take(40))
            if (awaitingExplorer == fetchKey) linkDownloads.add(id)
        }
        main.post {
            explorerFetch?.takeIf { it.key == fetchKey }?.let { shown ->
                explorerFetch = if (id != null) shown.copy(fileId = id, error = null)
                    else shown.copy(error = detail?.ifBlank { null } ?: "Your computer couldn't send this file.")
            }
        }
    }
    /**
     * Fetches a file attached to one of the user's messages in Rem's chat from the desktop, for a message sent from
     * the desktop window or after this phone's own copy is gone. The desktop offers its stored copy on Rem's file
     * channel and the phone downloads it like any file, checked for size and SHA-256. Older fetched copies are
     * removed first when the phone's file space would pass [RemAttachments.BUDGET].
     */
    fun fetchAttachment(messageId: String, attachment: RemFile) {
        if (attachment.id.isBlank() || attachment.id.length > 128 || messageId.isBlank() || messageId.length > 200) return
        val key = RemAttachments.KEY + attachment.id
        if (!enqueue {
            if (!desktopAllowed || connection == null) return@enqueue
            // Just after connecting the desktop hasn't said yet whether it takes fetches.
            if (!attachmentsAllowed) { attachmentWaiting[attachment.id] = messageId to attachment; return@enqueue }
            // Already fetched, or offered and waiting for its download to start.
            RemAttachments.fileFor(saved, attachment.id)?.let { id -> saved.optJSONObject("files")?.optJSONObject(id) }?.let { file ->
                when (file.getString("state")) {
                    "available", "receiving" -> return@enqueue
                    "offered" -> { queue("file.request", file.getString("thread"), JSONObject().put("file_id", file.getString("file_id"))); return@enqueue }
                }
            }
            if (desktopRequests.values.any { it.first == key }) return@enqueue
            val next = JSONObject(saved.toString())
            val removed = RemAttachments.evictions(next, attachment.size.coerceAtLeast(0), FileExplorer.files(next))
            if (removed == null) { main.post { attachmentFetches = attachmentFetches + (attachment.id to RemAttachments.Fetch.NoRoom) }; return@enqueue }
            if (removed.isNotEmpty()) {
                removed.forEach { transfers.remove(next, it) }
                RemAttachments.forget(next, removed); FileExplorer.forget(next, removed)
                store.save(next); saved = next; publish(); transfers.cleanup(saved)
            }
            main.post { attachmentFetches = attachmentFetches + (attachment.id to RemAttachments.Fetch.Asking) }
            queueDesktop("desktop.action", RemAttachments.OP, JSONObject().put("message_id", messageId).put("file_id", attachment.id))
        }) main.post { attachmentFetches = attachmentFetches - attachment.id }
    }
    /** Stores which download holds an attachment and starts it once its offer is here; the offer usually comes first. */
    private fun receiveAttachment(state: JSONObject, attachment: String, body: JSONObject?, code: String, detail: String) {
        val id = body?.optString("file_id")?.takeIf { runCatching { java.util.UUID.fromString(it).toString() == it }.getOrDefault(false) }
        if (id != null) { RemAttachments.remember(state, attachment, id); linkDownloads.add(id) }
        val failed = if (id == null) RemAttachments.failure(code, detail) else null
        main.post { attachmentFetches = if (failed == null) attachmentFetches - attachment else attachmentFetches + (attachment to failed) }
    }
    /** Fetches a picture or video attachment once per session without a tap, so its preview can show. */
    fun autoFetchAttachment(messageId: String, attachment: RemFile) {
        if (attachmentAuto.add(attachment.id)) fetchAttachment(messageId, attachment)
    }
    private fun receiveLinkFile(key: String, body: JSONObject?, detail: String?) {
        val id = body?.optString("file_id")?.takeIf { runCatching { java.util.UUID.fromString(it).toString() == it }.getOrDefault(false) }
        val name = body?.optString("name").orEmpty().take(160)
        if (id != null) { linkFiles[key] = id; if (awaitingLink == key) linkDownloads.add(id) }
        main.post {
            linkFetch?.takeIf { it.key == key }?.let { shown ->
                linkFetch = if (id != null) shown.copy(fileId = id, name = name.ifBlank { shown.name }, error = null)
                    else shown.copy(error = detail?.ifBlank { null } ?: "Your desktop couldn't send this file.")
            }
        }
    }
    /** Downloads a tapped link's file once its offer is stored. The offer may arrive before or after the result. */
    private fun startLinkDownloads() {
        if (linkDownloads.isEmpty()) return
        val files = saved.optJSONObject("files") ?: return
        for (id in linkDownloads.toList()) {
            val file = files.optJSONObject(id) ?: continue
            linkDownloads.remove(id)
            if (file.getString("direction") == "download" && file.getString("state") == "offered")
                queue("file.request", file.getString("thread"), JSONObject().put("file_id", id))
        }
    }
    /** Ask the desktop for a web image shown in a message. */
    fun requestImage(url: String) {
        if (!mediaProxy || !ChatImages.fetchable(url) || !imageRequested.add(url)) return
        images = images + (url to ChatImage.Loading)
        imageQueue.add(url)
    }
    private fun pumpImages() {
        if (!mediaAllowed || !desktopAllowed) return
        while (desktopRequests.values.count { it.first.startsWith(ChatImages.KEY) } < 2) {
            val url = imageQueue.poll() ?: return
            queueDesktop("desktop.read", "media.image", JSONObject().put("url", url))
        }
    }
    private fun receiveImage(url: String, body: JSONObject?) {
        val bitmap = body?.let { runCatching { ChatImages.decode(it) }.getOrNull() }
        main.post {
            var next = images + (url to (bitmap?.let { ChatImage.Ready(it) } ?: ChatImage.Failed))
            // Keep recent images only; an evicted one is fetched again when shown.
            while (next.size > ChatImages.CACHE) next.keys.first().let { old -> next = next - old; imageRequested.remove(old) }
            images = next
        }
    }
    /** Downloads a picture or video on one of Rem's replies once, so its preview can show without a tap. */
    fun autoDownload(id: String) {
        if (files.firstOrNull { it.id == id }?.let { it.direction == "download" && it.state == "offered" } != true) return
        if (mediaDownloads.add(id)) requestFile(id)
    }
    /** Makes the inline preview of a downloaded picture or video, off the main thread and in memory only. */
    fun requestMediaPreview(id: String) {
        val file = files.firstOrNull { it.id == id && FileTransfers.holds(it.direction, it.state) } ?: return
        val kind = ChatMedia.kind(file.name) ?: return
        if (!mediaRequested.add(id)) return
        mediaPreviews = mediaPreviews + (id to MediaPreview.Loading)
        readMedia(id) { bytes ->
            mediaWorker.execute {
                val preview = try { bytes?.let { runCatching { ChatMedia.preview(kind, it) }.getOrNull() } } finally { bytes?.fill(0) }
                main.post {
                    var next = mediaPreviews + (id to (preview ?: MediaPreview.Failed))
                    // Keep recent previews only; an evicted one is made again when shown.
                    while (next.size > ChatMedia.CACHE) next.keys.first().let { old -> next = next - old; mediaRequested.remove(old) }
                    mediaPreviews = next
                }
            }
        }
    }
    /** The start of a stored text file, or null when it can't be read or isn't text. */
    fun readText(id: String, onReady: (TextPreview?) -> Unit) = readMedia(id) { bytes ->
        mediaWorker.execute {
            val text = try { bytes?.let { FileKinds.previewText(it) } } finally { bytes?.fill(0) }
            main.post { onReady(text) }
        }
    }
    /** A stored picture at viewing size, or null when it can't be read. */
    fun openImage(id: String, onReady: (android.graphics.Bitmap?) -> Unit) = readMedia(id) { bytes ->
        mediaWorker.execute {
            val bitmap = try { bytes?.let { runCatching { ChatMedia.decodeImage(it, ChatMedia.FULL_SIDE) }.getOrNull() } } finally { bytes?.fill(0) }
            main.post { onReady(bitmap) }
        }
    }
    /**
     * The verified bytes of a stored file (a finished download or this phone's own upload) for the in-app player
     * and previews, delivered on the main thread.
     * The caller zeroes them when playback ends.
     */
    fun readMedia(id: String, onReady: (ByteArray?) -> Unit) {
        if (!enqueue {
            val bytes = runCatching { transfers.read(saved, id) }.getOrNull()
            main.post { onReady(bytes) }
        }) onReady(null)
    }
    /** Rem's chat or voice mode is on screen, so an alert would repeat what the user sees. */
    private fun remShown() = voiceActive || (userSees && desktopScreen?.first == "rem.home")
    /** Updates which runs are this phone's from a full answer about Rem's tasks, saved with the received event. */
    private fun receiveProgress(state: JSONObject, home: RemHome) {
        val claims = TaskProgress.load(state)
        val next = TaskProgress.claim(claims, home, System.currentTimeMillis())
        if (next != claims) TaskProgress.save(state, next)
        progressRuns = TaskProgress.running(next, home)
        progressConfirmed()
    }
    /** A read has just confirmed [progressRuns], so the notification can show them. */
    private fun progressConfirmed() {
        progressFresh = if (progressRuns.isEmpty()) 0 else android.os.SystemClock.elapsedRealtime()
        val app = getApplication<Application>()
        val frame = if (backgroundEnabled) TaskProgress.frame(progressRuns, TaskNotification.showWords(app)) else null
        if (frame != null) TaskNotification.post(app, frame) else if (TaskNotification.showing) TaskNotification.clear(app)
    }
    /** Without the desktop the phone can't say the task is still running, so it stops claiming it. A later read brings it back. */
    private fun expireProgress() {
        if (progressFresh == 0L || android.os.SystemClock.elapsedRealtime() - progressFresh < PROGRESS_STALE) return
        progressRuns = emptyList(); progressFresh = 0
        TaskNotification.clear(getApplication())
    }
    /** The user dismissed the notification. The runs it showed stay out of it, even after a restart. */
    fun hideTaskProgress() { enqueue {
        val app = getApplication<Application>()
        val runs = TaskNotification.shownRuns
        progressRuns = progressRuns.filterNot { it.id in runs }
        TaskNotification.clear(app)
        if (runs.isEmpty() || locked) return@enqueue
        val next = JSONObject(saved.toString())
        TaskProgress.save(next, TaskProgress.hide(TaskProgress.load(next), runs))
        store.save(next); saved = next
        progressConfirmed()
    } }
    /** Asks the desktop for Rem's new messages since the saved position, while Rem's alerts are on. */
    private fun readNotices() {
        val app = getApplication<Application>()
        if (!RemNotifications.active(app)) return
        if (RemNotifications.takeBaseline(app) && (saved.has(NOTICE_CURSOR) || saved.has(NOTICE_MARK) || saved.has(NOTICE_ROUNDS))) {
            val next = JSONObject(saved.toString()).apply { remove(NOTICE_CURSOR); remove(NOTICE_MARK); remove(NOTICE_ROUNDS) }
            store.save(next); saved = next
        }
        val include = RemNotifications.include(app)
        noticeSettling = if (noticesFilterAllowed) RemNotifications.settling(app).filter { it in include } else emptyList()
        queueDesktop("desktop.read", "rem.notices", RemNoticeText.readParams(if (saved.has(NOTICE_CURSOR)) saved.getLong(NOTICE_CURSOR) else null,
            noticesFilterAllowed, include, saved.optString(NOTICE_MARK)))
    }
    /**
     * Saves the new position with the received event, then alerts. A read made before the position was
     * reset only sets it: the user just turned alerts on and shouldn't get older messages.
     */
    private fun receiveNotices(state: JSONObject, body: JSONObject) {
        val read = runCatching { RemNoticeText.parse(body) }.getOrNull() ?: return
        val known = state.has(NOTICE_CURSOR)
        state.put(NOTICE_CURSOR, read.cursor)
        // Without a mark this read carried no filter, so a switch turned on later starts from the desktop's current place.
        if (read.mark.isNotEmpty()) state.put(NOTICE_MARK, read.mark) else state.remove(NOTICE_MARK)
        if (read.more) nextNotices = 0
        val app = getApplication<Application>()
        val settling = RemNotifications.settling(app)
        // A switch stays settling through a backlog read in several parts, so nothing it finds from before alerts.
        if (!read.more && noticeSettling.isNotEmpty()) { RemNotifications.settled(app, noticeSettling); noticeSettling = emptyList() }
        if (!known) return
        val rounds = state.optJSONArray(NOTICE_ROUNDS)
        val seen = (0 until (rounds?.length() ?: 0)).map { rounds!!.getString(it) }
        val incoming = RemAlertRules.incoming(read, RemNotifications.include(app), seen, remShown(), settling)
        if (incoming.rounds != seen) state.put(NOTICE_ROUNDS, JSONArray(incoming.rounds))
        RemNotifications.post(app, incoming.notices, incoming.skipped)
    }
    /**
     * Sends a reply typed into Rem's alert, like one sent from Rem's chat. It waits in the encrypted outbox until
     * the phone reaches the desktop; with the app closed, the link stays up for [ALERT_REPLY_MILLIS] so the reply
     * and a quick answer get through. Before this session's first connection report the phone can't know whether the
     * desktop allows Rem from this phone, so it sends and the desktop decides. [onSaved] runs once the reply is in
     * the outbox or has been refused, so the receiver can let Android end the process.
     */
    fun replyToRem(text: String, onSaved: () -> Unit = {}) {
        val body = text.trim()
        if (body.isEmpty() || body.length > RemAlertRules.REPLY_LIMIT) { onSaved(); return }
        val app = getApplication<Application>()
        if (!enqueue {
            try {
                when {
                    locked || !saved.has("peer") -> RemNotifications.note(app, "Not sent. Open Raticode and pair it with your desktop.")
                    desktopKnown && !desktopAllowed -> RemNotifications.note(app, "Not sent. Turn on Use Raticode from this phone in desktop Paired devices.")
                    else -> {
                        val request = queueDesktop("desktop.action", "rem.message", JSONObject().put("body", body))
                        remSends[request] = RemUnsent(body, emptyList())
                        alertReplies.add(request)
                        replyUntil = android.os.SystemClock.elapsedRealtime() + ALERT_REPLY_MILLIS
                        if (connection == null) { reconnect = true; nextConnect = 0; failures = 0 }
                    }
                }
            } catch (failure: LiveStorageLimit) {
                RemNotifications.note(app, "Not sent. Raticode's storage on this phone is full; open Raticode to free space.")
                throw failure
            } catch (failure: Exception) {
                // Nobody sees in-app status while replying from a notification.
                if (!reachesSyncLoop(failure)) RemNotifications.note(app, "Not sent. Open Raticode to check what went wrong.")
                throw failure
            } finally { onSaved() }
        }) { RemNotifications.note(app, "Not sent. Raticode is busy; try again in a moment."); onSaved() }
    }
    /** Closes the link an alert reply opened once its time is up, unless something else still needs it. */
    private fun endAlertReply() {
        if (replyUntil == 0L || android.os.SystemClock.elapsedRealtime() < replyUntil) return
        replyUntil = 0
        if (onScreen || backgroundEnabled || voiceActive || shareActive || !reconnect) return
        reconnect = false; connection?.close(); connection = null; main.post { connected = false }
        publishStatus("Disconnected. Pairing is retained.")
    }
    fun dismissDesktopError(key: String) { desktopErrors = desktopErrors - key }
    /**
     * Tidies dictated words on the desktop with the model chosen in its Settings. Never fails: when tidying is off,
     * the desktop isn't connected, is slow or refuses, the words come back as heard with the reason. The words go
     * end-to-end encrypted and leave the phone's queue as soon as the desktop answers that it started.
     */
    suspend fun tidyVoice(text: String, wait: Long = VoiceTidy.WAIT_MS): TidyResult {
        val heard = text.trim().take(dev.raticode.mobile.data.LongText.MAX_CHARS)
        if (heard.isEmpty()) return TidyResult(heard, false, "Nothing was heard.")
        // Never cut: words past the desktop's limit would be lost. Long dictation stays as heard and goes as a file.
        VoiceTidy.tooLong(heard)?.let { return it }
        if (!tidyAllowed || !desktopAllowed) return TidyResult(heard, false, VoiceTidy.OFF)
        if (!connected) return TidyResult(heard, false, VoiceTidy.OFFLINE)
        val answer = kotlinx.coroutines.CompletableDeferred<TidyResult>()
        val queued = kotlinx.coroutines.CompletableDeferred<Boolean>()
        val request = java.util.concurrent.atomic.AtomicReference<String?>()
        if (!enqueue {
            runCatching { queueDesktop("desktop.action", VoiceTidy.KEY, JSONObject().put("text", heard)) }.getOrNull()?.let {
                request.set(it); tidyWaiters[it] = heard to answer
            }
            queued.complete(request.get() != null)
        }) return TidyResult(heard, false, VoiceTidy.BUSY)
        return try {
            kotlinx.coroutines.withTimeoutOrNull(wait) {
                if (!queued.await()) TidyResult(heard, false, VoiceTidy.BUSY) else answer.await()
            } ?: TidyResult(heard, false, VoiceTidy.SLOW)
        } finally {
            // Runs after the block above on the same queue, so the request is known. A late answer is dropped.
            enqueue { request.get()?.let { tidyWaiters.remove(it); desktopRequests.remove(it); desktopParts.drop(it) } }
        }
    }
    /** `pending` means the model is working; the tidied words follow as a second result for the same request. */
    private fun receiveTidy(request: String, payload: JSONObject) {
        if (!payload.getBoolean("ok")) {
            desktopRequests.remove(request); desktopParts.drop(request)
            tidyWaiters.remove(request)?.let { (heard, answer) ->
                answer.complete(TidyResult(heard, false, payload.optString("detail").ifBlank { "Your desktop didn't tidy this." }.take(300)))
            }
            return
        }
        val body = desktopParts.add(request, payload) ?: return
        if (body.optBoolean("pending")) return
        desktopRequests.remove(request)
        tidyWaiters.remove(request)?.let { (heard, answer) -> answer.complete(VoiceTidy.parse(heard, body)) }
    }
    private fun queueDesktop(type: String, op: String, params: JSONObject): String {
        check(!locked && !stopped)
        val request = java.util.UUID.randomUUID().toString()
        desktopRequests[request] = DesktopViews.key(op, params) to (type == "desktop.action")
        commits.afterCommit {
            val (next, event) = try { LiveJournal.prepare(saved, type, null, JSONObject().put("op", op).put("params", params), request) }
            catch (full: LiveStorageLimit) { desktopRequests.remove(request); throw full }
            TaskProgress.note(next, op, params, request, System.currentTimeMillis())
            store.save(next); saved = next; publish()
            connection?.send(event)
        }
        return request
    }
    /** Forgets a refused Rem message once it was sent again or moved back into the message box. */
    fun dismissRemUnsent() { remUnsent = null }
    /** Ops and fields an older desktop would refuse by closing the session, so they wait for its overview. */
    private fun needsOrganizationsV2(op: String, params: JSONObject) = op == OrganizationModel.KNOWLEDGE || op == OrganizationModel.WORKER || op == WorkSchedule.OP ||
        op == BacklogPulls.OP || op == OrgRequests.OP || op == AutoMode.OP ||
        (op == "organizations.task" && (params.optString("action") == "move" || params.has("status") || params.has("epic_id") || params.has("project_id")))
    /** Drops queued polling reads of [ops]; an older desktop would close the session on an op it doesn't know. */
    private fun dropQueuedReads(state: JSONObject, ops: Set<String>): Boolean {
        val outbox = state.optJSONArray("outbox") ?: return false
        val kept = JSONArray()
        for (i in 0 until outbox.length()) {
            val event = JSONObject(outbox.getString(i))
            val op = event.optJSONObject("payload")?.optString("op").orEmpty()
            if (event.optString("type") != "desktop.read" || op !in ops) kept.put(outbox.getString(i))
        }
        if (kept.length() == outbox.length()) return false
        state.put("outbox", kept)
        return true
    }
    /** Shows only [levels]; the desktop filters, so the log starts again from its newest entries. */
    fun setLogLevels(levels: Set<String>) {
        val chosen = levels.filter { it in DeveloperLog.LEVELS.map { l -> l.first } }.toSet().ifEmpty { DeveloperLog.DEFAULT_LEVELS }
        enqueue { logLevels = chosen; restartLog() }
    }
    val developerLogLevels get() = logLevels
    /** Forgets what the phone holds of the desktop's log and reads its newest entries again. */
    fun reloadLog() { enqueue { restartLog() } }
    private fun restartLog() {
        logCursor = null; logHeld = emptyList(); logRequest = null; nextDesktop = 0
        main.post { developerLog = emptyList(); developerLogSkipped = false; desktopErrors = desktopErrors - DeveloperLog.OP }
    }
    private fun receiveLog(request: String, body: JSONObject) {
        // An answer to a read made before the levels changed belongs to the old view.
        if (request != logRequest) return
        val page = DeveloperLog.parse(body)
        logHeld = DeveloperLog.merge(logHeld, page)
        logCursor = page.cursor ?: logCursor
        if (page.more) nextDesktop = 0
        val entries = logHeld
        main.post {
            developerLog = entries; developerLogInfo = page; if (page.reset) developerLogSkipped = false
            if (page.skipped) developerLogSkipped = true
            desktopErrors = desktopErrors - DeveloperLog.OP; desktopViews = desktopViews + (DeveloperLog.OP to page)
        }
    }
    fun requestPreview(url: String) {
        if (!richResults || !ChatImages.fetchable(url) || !previewRequested.add(url)) return
        previews = previews + (url to ChatPreview.Loading)
        previewQueue.add(url)
        enqueue { pumpPreviews() }
    }
    private fun pumpPreviews() {
        if (!richAllowed || !desktopAllowed) return
        while (desktopRequests.values.count { it.first.startsWith(ChatPreviews.KEY) } < 3) {
            val url = previewQueue.poll() ?: return
            queueDesktop("desktop.read", "media.preview", JSONObject().put("url", url))
        }
    }
    /** Applies a `rem.draft` delta. A gap or a new attempt starts over from the whole draft on the next read. */
    private fun applyDraft(delta: RemDraftDelta) {
        replyDraft = RemRich.applyDraft(replyDraft, delta)
        if (!delta.replying) { homeReplying = false; nextDesktop = 0 }
        val shown = replyDraft.text
        main.post { remDraft = shown }
    }
    private fun receivePreview(url: String, body: JSONObject?) {
        val preview = body?.let { runCatching { ChatPreviews.parse(it) }.getOrNull() }
        main.post {
            var next = previews + (url to (preview?.let { ChatPreview.Ready(it) } ?: ChatPreview.Failed))
            while (next.size > ChatPreviews.CACHE) next.keys.first().let { old -> next = next - old; previewRequested.remove(old) }
            previews = next
        }
    }
    private fun pendingDesktop(op: String, params: JSONObject): Boolean {
        val outbox = saved.optJSONArray("outbox") ?: return false
        val key = DesktopViews.key(op, params)
        return (0 until outbox.length()).any { JSONObject(outbox.getString(it)).let { e ->
            e.optString("type") == "desktop.read" && e.getJSONObject("payload").let { p -> DesktopViews.key(p.optString("op"), p.optJSONObject("params") ?: JSONObject()) == key }
        } }
    }
    /** Reassembles a result's parts; the body is parsed only once complete. */
    private fun receiveDesktop(state: JSONObject, request: String, payload: JSONObject) {
        val known = desktopRequests[request] ?: (state.optJSONArray("outbox") ?: JSONArray()).let { outbox ->
            // After a restart, the replayed request in the journal names its screen.
            (0 until outbox.length()).map { JSONObject(outbox.getString(it)) }.firstOrNull { it.optString("request_id") == request }
                ?.let { e -> e.getJSONObject("payload").let { p -> DesktopViews.key(p.getString("op"), p.getJSONObject("params")) to (e.getString("type") == "desktop.action") } }
                ?.also { desktopRequests[request] = it }
        } ?: return
        val (key, action) = known
        // A Rem message the desktop refused stays on the phone with Try again and Edit.
        remSends.remove(request)?.let { sent ->
            val detail = payload.optString("detail")
            if (!payload.getBoolean("ok")) main.post {
                val reason = detail.ifBlank { "Your desktop didn't take this message." }
                remUnsent = sent.copy(detail = reason); remRefusal = request to reason
            }
        }
        if (alertReplies.remove(request) && !payload.getBoolean("ok")) {
            val detail = payload.optString("detail").ifBlank { "Your desktop didn't take this reply." }
            RemNotifications.note(getApplication(), "Not sent: $detail Open Rem's chat to try again.")
        }
        if (key == "rem.notices") {
            if (!payload.getBoolean("ok")) { desktopRequests.remove(request); desktopParts.drop(request); return }
            val body = desktopParts.add(request, payload) ?: return
            desktopRequests.remove(request); receiveNotices(state, body); return
        }
        if (key == VoiceTidy.KEY) { receiveTidy(request, payload); return }
        if (key == DeveloperLog.OP && payload.getBoolean("ok")) {
            val body = desktopParts.add(request, payload) ?: return
            desktopRequests.remove(request); receiveLog(request, body); return
        }
        if (key.startsWith(ChatPreviews.KEY) && !payload.getBoolean("ok")) {
            desktopRequests.remove(request); desktopParts.drop(request); receivePreview(key.removePrefix(ChatPreviews.KEY), null); pumpPreviews(); return
        }
        if (key.startsWith(RemAttachments.KEY)) {
            val attachment = key.removePrefix(RemAttachments.KEY)
            if (!payload.getBoolean("ok")) { desktopRequests.remove(request); desktopParts.drop(request); receiveAttachment(state, attachment, null, payload.optString("code"), payload.optString("detail")); return }
            val body = desktopParts.add(request, payload) ?: return
            desktopRequests.remove(request); receiveAttachment(state, attachment, body, "", ""); return
        }
        if (key.startsWith(FileExplorer.KEY)) {
            if (!payload.getBoolean("ok")) { desktopRequests.remove(request); desktopParts.drop(request); receiveExplorerFile(state, key, null, payload.optString("detail")); return }
            val body = desktopParts.add(request, payload) ?: return
            desktopRequests.remove(request); receiveExplorerFile(state, key, body, null); return
        }
        if (key.startsWith(ChatLinks.KEY)) {
            if (!payload.getBoolean("ok")) { desktopRequests.remove(request); desktopParts.drop(request); receiveLinkFile(key, null, payload.optString("detail")); return }
            val body = desktopParts.add(request, payload) ?: return
            desktopRequests.remove(request); receiveLinkFile(key, body, null); return
        }
        if (key.startsWith(ChatImages.KEY) && !payload.getBoolean("ok")) {
            desktopRequests.remove(request); desktopParts.drop(request); receiveImage(key.removePrefix(ChatImages.KEY), null); return
        }
        if (key == RemSearch.SEARCH || key == RemSearch.HISTORY) {
            if (!payload.getBoolean("ok")) { desktopRequests.remove(request); desktopParts.drop(request); receiveRemSearch(key, request, null, payload.optString("code"), payload.optString("detail")); return }
            val body = desktopParts.add(request, payload) ?: return
            desktopRequests.remove(request); receiveRemSearch(key, request, body, "", ""); return
        }
        if (!payload.getBoolean("ok")) {
            desktopRequests.remove(request); desktopParts.drop(request)
            val detail = payload.optString("detail").ifBlank { "The desktop could not complete this request." }
            notePhone(if (action) "warn" else "info", "Desktop answered ${payload.optString("op")} with ${payload.optString("code").ifBlank { "an error" }}: $detail")
            main.post { desktopErrors = desktopErrors + (key to detail); if (action) { desktopBusy = desktopBusy - key; requestError = detail } }
            return
        }
        val body = desktopParts.add(request, payload) ?: return
        desktopRequests.remove(request)
        if (body.optBoolean("unchanged")) {
            if (key == "rem.home") progressConfirmed()
            main.post { desktopErrors = desktopErrors - key }
            return
        }
        if (key.startsWith(ChatImages.KEY)) { receiveImage(key.removePrefix(ChatImages.KEY), body); return }
        if (key.startsWith(ChatPreviews.KEY)) { receivePreview(key.removePrefix(ChatPreviews.KEY), body); pumpPreviews(); return }
        if (key == "rem.draft") { applyDraft(DesktopViews.parse(key, body) as RemDraftDelta); return }
        // A run, stop or resume answer is a short status line; the screen refreshes on the next read.
        if (action && key.startsWith(Workflows.READ + ":") && !Workflows.isView(body)) {
            nextDesktop = 0
            val notice = key.substringAfter(':') to body.optString("message").ifBlank { "Sent to your desktop." }
            main.post { desktopBusy = desktopBusy - key; desktopErrors = desktopErrors - key; workflowNotice = notice }
            return
        }
        body.optString("digest").takeIf { it.isNotBlank() }?.let { desktopDigests[key] = it }
        // An action's short answer must not replace the screen with an empty view; the next read refreshes it.
        if (action && Kanban.isShortAnswer(key, body)) {
            nextDesktop = 0
            main.post { desktopBusy = desktopBusy - key; desktopErrors = desktopErrors - key }
            return
        }
        // An approve or decline answer carries each request's result; keep it after later reads replace the screen.
        if (action && key.startsWith("organizations.read:")) OrgRequests.parseResults(body)?.let { results ->
            val organization = key.substringAfter(':')
            main.post { organizationRequestResults = organization to results }
        }
        val view = DesktopViews.parse(key, body)
        when (view) {
            is DesktopOverview -> {
                mediaAllowed = view.media; fileLinksAllowed = view.fileLinks; workflowsAllowed = view.workflows; richAllowed = view.richResults
                draftAllowed = view.draftDeltas; reviewRetryAllowed = view.reviewRetry; threadReadAllowed = view.threadRead; runPauseAllowed = view.runPause
                if (noticesFilterAllowed && !view.remNoticesFilter) dropQueuedNoticeFilters(state)
                WakeOnLan.remember(state, view.wakeMac)
                noticesAllowed = view.remNotices; noticesFilterAllowed = view.remNoticesFilter; nextNotices = 0
                tidyAllowed = view.voiceTidy; sourceControlAllowed = view.sourceControl; sourceHistoryAllowed = view.sourceControlHistory
                attachmentsAllowed = view.attachmentFiles; explorerAllowed = view.fileExplorer; explorerKnown = true
                developerLogAllowed = view.developerLog; organizationsV2Allowed = view.organizationsV2; organizationsV2Known = true
                organizationScheduleAllowed = view.organizationsV2 && view.organizationSchedule
                organizationBacklogPullAllowed = view.organizationsV2 && view.organizationBacklogPull
                organizationRequestsAllowed = view.organizationsV2 && view.organizationRequests
                organizationAutoModeAllowed = view.organizationsV2 && view.organizationAutoMode
                browserAllowed = view.browser
                kanbanAllowed = view.kanban; kanbanParticipantsAllowed = view.kanban && view.kanbanParticipants; kanbanKnown = true; nextSelfWork = 0
                kanbanCommentsAllowed = view.kanban && view.kanbanComments
                val board = view.kanban
                val boardPeople = kanbanParticipantsAllowed
                val boardComments = kanbanCommentsAllowed
                main.post { kanban = board; kanbanParticipants = boardPeople; kanbanComments = boardComments }
                generateSequence { kanbanWaiting.poll() }.toList().forEach { (op, params) ->
                    if (board && (op != Kanban.PARTICIPANT || boardPeople) && (!Kanban.isComment(op, params) || boardComments)) enqueue { queueDesktop("desktop.action", op, params) }
                    else main.post { desktopBusy = desktopBusy - DesktopViews.key(op, params)
                        requestError = if (!board) Kanban.UPDATE_DESKTOP else if (op == Kanban.PARTICIPANT) Kanban.UPDATE_PARTICIPANTS else Kanban.UPDATE_COMMENTS }
                }
                val log = view.developerLog; val organizations = view.organizationsV2; val browser = view.browser
                val schedule = organizationScheduleAllowed
                val pulls = organizationBacklogPullAllowed; val requests = organizationRequestsAllowed; val auto = organizationAutoModeAllowed
                main.post { developerLogSupported = log; organizationsV2 = organizations; organizationSchedule = schedule; organizationBacklogPull = pulls; organizationRequests = requests; organizationAutoMode = auto; desktopBrowser = browser }
                // Runs as later commands, after this event's save.
                generateSequence { organizationsV2Waiting.poll() }.toList().forEach { (op, params) ->
                    if (organizations && (op != WorkSchedule.OP || schedule) && (op != BacklogPulls.OP || pulls) && (op != OrgRequests.OP || requests) && (op != AutoMode.OP || auto)) enqueue { queueDesktop("desktop.action", op, params) }
                    else main.post { desktopBusy = desktopBusy - DesktopViews.key(op, params)
                        requestError = if (!organizations) OrganizationModel.UPDATE_DESKTOP else if (op == BacklogPulls.OP) BacklogPulls.UPDATE_DESKTOP
                            else if (op == OrgRequests.OP) OrgRequests.UPDATE_DESKTOP else if (op == AutoMode.OP) AutoMode.UPDATE_DESKTOP else WorkSchedule.UPDATE_DESKTOP }
                }
                val supported = view.workflows; val reading = view.threadRead; val alerts = view.remNotices; val alertFilter = view.remNoticesFilter; val git = view.sourceControl; val gitHistory = view.sourceControlHistory
                val files = view.fileExplorer
                main.post { workflows = supported; threadReadSync = reading; remAlerts = alerts; remAlertFilter = alertFilter; sourceControl = git; sourceControlHistory = gitHistory; fileExplorer = files }
                explorerWaiting?.let { waiting ->
                    explorerWaiting = null
                    // Runs as a later command, after this event's save.
                    if (files) enqueue { if (awaitingExplorer == waiting.key) queueDesktop("desktop.action", FileExplorer.OPEN, FileExplorer.openParams(waiting)) }
                    else main.post { if (explorerFetch?.key == waiting.key) explorerFetch = explorerFetch?.copy(error = FileExplorer.UPDATE_DESKTOP) }
                }
            }
            is RemHome -> {
                remFiles = view.filesThread; if (view.media) mediaAllowed = true; if (view.fileLinks) fileLinksAllowed = true; if (view.richResults) richAllowed = true
                if (view.draftDeltas) draftAllowed = true; if (view.reviewRetry) reviewRetryAllowed = true; if (view.runPause) runPauseAllowed = true
                tidyAllowed = view.voiceTidy; if (view.attachmentFiles) attachmentsAllowed = true
                homeReplying = view.turnState == "replying"
                if (!homeReplying && replyDraft.text.isNotEmpty()) { replyDraft = RemRich.Draft(); main.post { remDraft = "" } }
                receiveProgress(state, view)
            }
        }
        if ((view is DesktopOverview || view is RemHome) && attachmentsAllowed && attachmentWaiting.isNotEmpty()) {
            // Runs as later commands, after this event's save.
            attachmentWaiting.values.toList().also { attachmentWaiting.clear() }.forEach { (message, file) -> fetchAttachment(message, file) }
        }
        if (view is DesktopOverview || view is RemHome) {
            val allowed = mediaAllowed; val links = fileLinksAllowed; val rich = richAllowed; val retry = reviewRetryAllowed
            val tidy = tidyAllowed; val attachments = attachmentsAllowed; val pausing = runPauseAllowed
            main.post { mediaProxy = allowed; fileLinks = links; richResults = rich; reviewRetry = retry; voiceTidy = tidy; attachmentFiles = attachments; runPause = pausing }
        }
        if (key == "swarms.start") nextSwarms = 0
        // An action can change the open screen too, such as a task's runs.
        if (action) nextDesktop = 0
        if (key == desktopScreen?.let { DesktopViews.key(it.first, it.second) }) desktopFast = when (view) {
            is RemHome -> view.turnState == "replying" || view.tasks.any { it.activeRun != null }
            is RemThread -> view.running
            is Workflow -> view.activeRun != null
            is WorkflowList -> view.items.any { it.active }
            else -> false
        }
        main.post {
            if (view is RemHome) {
                // Messages that slid out of the newest window join the earlier pages, so none go missing between them.
                remEarlier = RemSearch.keepEarlier(remEarlier, desktopViews["rem.home"] as? RemHome, view)
                if (remEarlier.isEmpty()) { remEarlierCount = null; remEarlierTickets = emptyMap() }
            }
            desktopViews = desktopViews + (key to view); desktopErrors = desktopErrors - key
            if (action) desktopBusy = desktopBusy - key
        }
    }

    /**
     * Searches Rem's chat for [query]'s words. A desktop that says `search` looks through its whole saved chat;
     * offline, or with an older desktop, the phone looks through the messages it holds and says so. [more] asks for
     * the next page of the current results.
     */
    fun searchRem(query: String, more: Boolean = false) {
        val words = RemSearch.normalized(query)
        if (words.isEmpty()) { searchRequest = null; remSearch = RemSearchState(query); return }
        val home = desktopViews["rem.home"] as? RemHome
        if (!connected || !desktopControl || home?.search != true) {
            if (more) return
            searchRequest = null
            val held = RemSearch.joined(remEarlier, home?.messages.orEmpty())
            remSearch = RemSearchState(query, RemSearchState.Status.READY, RemSearch.local(held, words),
                note = if (connected && home != null) RemSearch.UPDATE_DESKTOP else RemSearch.OFFLINE)
            return
        }
        val before = if (more) remSearch.page?.next ?: return else null
        if (more && (remSearch.loadingMore || remSearch.page?.local == true)) return
        remSearch = if (more) remSearch.copy(loadingMore = true, error = "")
            else remSearch.copy(query = query, status = RemSearchState.Status.LOADING, error = "", note = "", loadingMore = false)
        enqueue {
            if (connection == null || !desktopAllowed) { main.post { if (!more) searchRem(query) }; return@enqueue }
            searchBefore = before
            searchRequest = queueDesktop("desktop.read", RemSearch.SEARCH, RemSearch.params(words, before))
        }
    }

    /**
     * Loads the page of Rem's chat before the oldest message this phone holds. With [through], pages keep loading
     * until that message is here, then [remJump] asks the chat to show it.
     */
    fun loadEarlierRem(through: String? = null) {
        val home = desktopViews["rem.home"] as? RemHome ?: return
        if (!home.search || remHistoryBusy) return
        if (!connected) { remHistoryError = "Not connected. Earlier messages load from your desktop."; return }
        val oldest = (remEarlier.firstOrNull() ?: home.messages.firstOrNull { it.state != "waiting" })?.id ?: return
        historyHops = if (through != null && through == historyTarget) historyHops + 1 else 0
        historyTarget = through
        remHistoryBusy = true; remHistoryError = ""
        enqueue {
            if (connection == null || !desktopAllowed) {
                main.post { remHistoryBusy = false; historyTarget = null; remHistoryError = "Not connected. Earlier messages load from your desktop." }
                return@enqueue
            }
            historyRequest = queueDesktop("desktop.read", RemSearch.HISTORY, RemSearch.historyParams(oldest, through))
        }
    }

    fun clearRemHistoryError() { remHistoryError = "" }

    private fun receiveRemSearch(key: String, request: String, body: JSONObject?, code: String, detail: String) {
        if (key == RemSearch.SEARCH) {
            // An answer to words since replaced is dropped.
            if (request != searchRequest) return
            searchRequest = null
            val before = searchBefore
            val page = body?.let { RemSearch.parse(it) }
            main.post {
                val current = remSearch
                remSearch = when {
                    page == null && before != null -> current.copy(loadingMore = false, error = detail.ifBlank { "Couldn't load more results." })
                    page == null -> current.copy(status = RemSearchState.Status.ERROR, loadingMore = false,
                        error = detail.ifBlank { "Your desktop couldn't search Rem's chat." })
                    before != null -> current.copy(page = page.copy(results = current.page?.results.orEmpty() + page.results), loadingMore = false, error = "")
                    else -> current.copy(status = RemSearchState.Status.READY, page = page, error = "", loadingMore = false)
                }
            }
            return
        }
        if (request != historyRequest) return
        historyRequest = null
        val target = historyTarget
        val page = body?.let { DesktopViews.parse(RemSearch.HISTORY, it) as RemHistoryPage }
        main.post {
            remHistoryBusy = false
            if (page == null) {
                historyTarget = null
                remHistoryError = detail.ifBlank { "Your desktop couldn't send earlier messages." }
                // The chat was cleared on the desktop meanwhile; start again from the newest messages.
                if (code == "not_found") { remEarlier = emptyList(); remEarlierCount = null; remEarlierTickets = emptyMap() }
                return@post
            }
            val held = remEarlier.mapTo(HashSet()) { it.id }
            remEarlier = page.messages.filter { it.id !in held } + remEarlier
            remEarlierCount = page.earlier
            remEarlierTickets = remEarlierTickets + page.tickets
            remHistoryError = ""
            if (target == null) return@post
            when {
                remEarlier.any { it.id == target } -> { historyTarget = null; remJump = target to (remJump?.second ?: 0) + 1 }
                page.messages.isNotEmpty() && page.earlier > 0 && historyHops < RemSearch.MAX_HOPS -> loadEarlierRem(target)
                else -> { historyTarget = null; remHistoryError = "That message is no longer in Rem's chat." }
            }
        }
    }
    fun refreshApprovals() { enqueue { nextApprovals = 0; if (!remoteAllowed && connection != null && !pendingRead("approval.request")) queue("approval.request", null, JSONObject()) } }
    fun resolveApproval(approval: Approval, approve: Boolean, instruction: String = "") { enqueue {
        val payload = JSONObject().put("approval_id", approval.id).put("action_digest", approval.digest)
            .put("decision", if (approve) "approve" else "deny")
        if (instruction.isNotBlank()) payload.put("instruction", instruction.trim().take(2000))
        queue("approval.resolve", null, payload)
    } }
    fun openSwarms(swarm: String? = null) { swarmScreen = swarm.orEmpty(); enqueue { nextSwarms = 0 } }
    fun closeSwarms() { swarmScreen = null }
    fun controlSwarm(swarm: String, action: String) { enqueue {
        require(action in setOf("pause", "resume", "stop"))
        queue("swarm.control", null, JSONObject().put("swarm_id", swarm).put("action", action))
    } }
    fun messageSwarm(swarm: String, body: String) {
        val text = body.trim().take(4000)
        if (text.isEmpty()) return
        enqueue { queue("swarm.message", null, JSONObject().put("swarm_id", swarm).put("body", text)) }
    }
    fun searchThreads(query: String, more: Boolean = false) {
        val trimmed = query.trim().take(300)
        searchQuery = trimmed
        if (!more) searchResults = emptyList()
        searchError = ""; searchHasMore = false
        if (trimmed.isEmpty()) { searching = false; return }
        if (!connected) { searching = false; searchError = "Connect to search saved desktop messages."; return }
        val offset = if (more) searchResults.size else 0
        searching = true
        enqueue { queue("thread.search.request", null, JSONObject().put("query", trimmed).put("offset", offset)) }
    }
    fun openSearchResult(result: SearchResult) { enqueue {
        activeThread = result.thread
        if (connection != null) queue("thread.history.request", result.thread,
            JSONObject().put("before", JSONObject.NULL).put("include_presentation", true).apply { result.message?.let { put("message_id", it) } })
    } }
    fun refreshFleet() { enqueue { queue("fleet.request", null, JSONObject()) } }
    fun refresh() { enqueue { nextCatalog = 0; requestSync() } }
    private fun apply(event: JSONObject) {
        var next = JSONObject(saved.toString())
        event.fields("version", "id", "thread_id", "request_id", "sequence", "type", "payload")
        require(event.integer("version") == 2L); val id = event.uuid("id")
        val sequence = event.integer("sequence"); val type = event.string("type", 40)
        MobileCapabilities.validateIncomingType(type)
        IncomingValidation.validate(event)
        val received = next.optJSONObject("received") ?: JSONObject()
        val canonical = event.toString()
        val hash = dev.raticode.mobile.data.FileMetadata.digest(canonical.toByteArray())
        if (type in LiveJournal.SNAPSHOT_TYPES) { if (!LiveJournal.recordRecent(next, id, hash)) return }
        else if (!LiveJournal.recordReceived(received, id, hash)) return
        require(type == "sync.snapshot" || sequence > next.optLong("cursor", -1))
        val payload = event.getJSONObject("payload")
        PhoneLock.state(type, payload)?.let { locked -> main.post { lockedByDesktop = locked } }
        var local = next.optJSONArray("messages") ?: JSONArray()
        val request = event.optString("request_id")
        when (type) {
            "file.offer", "file.chunk", "file.status" -> transfers.receive(next, event)
            "sync.snapshot" -> {
                if (payload.has("threads_offset")) {
                    val pages = if (payload.getLong("threads_offset") == 0L) JSONArray() else next.optJSONArray("thread_pages") ?: JSONArray()
                    val page = payload.getJSONArray("threads")
                    for (i in 0 until page.length()) pages.put(page.getJSONObject(i))
                    if (payload.isNull("next_threads_offset")) { next.put("threads", keepResources(next, pages)); next.remove("thread_pages") }
                    else next.put("thread_pages", pages)
                } else next.put("threads", keepResources(next, payload.getJSONArray("threads")))
                payload.optJSONObject("connection")?.let { info ->
                    DeviceRoutes.applyReport(next, info)
                    remoteAllowed = info.optBoolean("remote_control")
                    desktopAllowed = info.optBoolean("desktop_control"); desktopKnown = true
                    val allowed = remoteAllowed
                    val full = desktopAllowed
                    // Without desktop control the phone can't learn the setting changed, so it keeps nothing.
                    if (!full) WakeOnLan.remember(next, null)
                    if (!full) { kanbanAllowed = false; kanbanParticipantsAllowed = false; mediaAllowed = false; fileLinksAllowed = false; attachmentsAllowed = false; workflowsAllowed = false; sourceControlAllowed = false; sourceHistoryAllowed = false; explorerAllowed = false; richAllowed = false; mediaChecked = false; remFiles = null; imageQueue.clear(); previewQueue.clear(); browserAllowed = false
                        draftAllowed = false; reviewRetryAllowed = false; runPauseAllowed = false; homeReplying = false; replyDraft = RemRich.Draft(); threadReadAllowed = false; noticesAllowed = false; noticesFilterAllowed = false
                        tidyAllowed = false }
                    main.post {
                        remReady = info.getBoolean("rem_ready"); remoteControl = allowed; desktopControl = full
                        if (!allowed) approvals = emptyList()
                        if (!full) { desktopViews = emptyMap(); desktopBusy = emptySet(); desktopDigests.clear(); mediaProxy = false; fileLinks = false; attachmentFiles = false; workflows = null; sourceControl = null; sourceControlHistory = null; fileExplorer = null; workflowNotice = null
                            richResults = false; previews = emptyMap(); previewRequested.clear(); reviewRetry = false; runPause = false; remDraft = ""; threadReadSync = false; remAlerts = null; remAlertFilter = null; desktopBrowser = null
                            remSearch = RemSearchState(); remEarlier = emptyList(); remEarlierCount = null; remEarlierTickets = emptyMap(); remHistoryBusy = false; remHistoryError = "" }
                    }
                }
            }
            "thread.snapshot" -> {
                val thread = event.uuid("thread_id")
                val list = next.optJSONArray("threads") ?: JSONArray()
                val updated = JSONArray()
                for (i in 0 until list.length()) if (list.getJSONObject(i).getString("thread_id") != thread) updated.put(list.getJSONObject(i))
                val previous = (0 until list.length()).map { list.getJSONObject(it) }.firstOrNull { it.optString("thread_id") == thread }
                val merged = JSONObject(previous?.toString() ?: "{}")
                payload.keys().forEach { merged.put(it, payload.get(it)) }
                updated.put(merged.put("thread_id", thread).put("can_create", previous?.optBoolean("can_create") ?: false))
                next.put("threads", updated)
                ThreadActivity.keepRead(next, thread, previous?.optString("updated_at"), merged.optString("updated_at"))
            }
            "catalog.snapshot" -> next.put("catalog", payload)
            "thread.search.snapshot" -> {
                val results = payload.getJSONArray("results")
                val found = (0 until results.length()).map { results.getJSONObject(it) }.map { r ->
                    SearchResult(r.getString("thread_id"), r.getString("title"), r.getString("snippet"), r.optString("message_id").takeUnless { it == "null" || it.isBlank() })
                }
                main.post { if (payload.getString("query") == searchQuery) {
                    searchResults = if (payload.getLong("offset") == 0L) found else (searchResults + found).distinctBy { it.thread }
                    searchHasMore = payload.getBoolean("has_more"); searching = false
                } }
            }
            "thread.history.snapshot" -> {
                val thread = event.uuid("thread_id")
                val before = next.optJSONObject("history_before") ?: JSONObject()
                val previous = before.optLong(thread, Long.MAX_VALUE)
                before.put(thread, minOf(previous, payload.getLong("before")))
                next.put("history_before", before)
                if (payload.has("resources")) {
                    val list = next.optJSONArray("threads") ?: JSONArray()
                    for (i in 0 until list.length()) list.getJSONObject(i).takeIf { it.optString("thread_id") == thread }
                        ?.put("resources", payload.getJSONArray("resources"))?.put("resource_ids", payload.getJSONArray("resource_ids"))
                }
                local = LiveHistory.merge(local, thread, payload)
                next.put("messages", local); LiveHistory.makeRoom(next, thread); local = next.getJSONArray("messages")
            }
            "chat.accepted", "request.status", "error" -> {
                for (i in 0 until local.length()) if (local.getJSONObject(i).getString("id") == request)
                    local.getJSONObject(i).put("state", when(type) { "chat.accepted" -> "Accepted by desktop"; "error" -> "Desktop rejected request"; else -> payload.optString("status", "Desktop status received") }).put("detail", payload.optString("detail"))
            }
            "chat.message" -> {
                val text = payload.getString("text"); require(text.length <= 4000)
                next.put("messages", local); LiveHistory.makeRoom(next, event.uuid("thread_id"), 1); local = next.getJSONArray("messages")
                if (payload.optBoolean("final")) for (i in 0 until local.length()) {
                    val message = local.getJSONObject(i)
                    if (message.getString("id") == request) message.put("state", "Completed")
                }
                val messageId = payload.getString("message_id")
                if ((0 until local.length()).none { local.getJSONObject(it).getString("id") == messageId })
                    local.put(JSONObject().put("id", messageId).put("thread", event.uuid("thread_id")).put("text", text).put("state", "Rem")
                        .put("attachments", LiveHistory.attachmentSnapshot(next, payload.optJSONArray("attachment_ids") ?: JSONArray())))
            }
            "fleet.snapshot" -> next.put("fleet", payload.toString())
            "desktop.result" -> receiveDesktop(next, request, payload)
            "approval.snapshot" -> {
                val list = payload.getJSONArray("approvals")
                val parsed = (0 until list.length()).map { list.getJSONObject(it) }.map { a ->
                    Approval(a.getString("approval_id"), a.getString("action_digest"), a.getString("source"), a.getString("title"),
                        a.getString("summary"), a.getString("requested_at"), a.getBoolean("instruction_required"))
                }
                val fresh = parsed.any { seenApprovals.add(it.id) }
                if (fresh) LiveNotifications.showApproval(getApplication())
                main.post { approvals = parsed; approvalsLoaded = true }
            }
            "swarm.snapshot" -> {
                val list = payload.getJSONArray("swarms")
                val parsed = (0 until list.length()).map { list.getJSONObject(it) }.map { w ->
                    Swarm(w.getString("swarm_id"), w.getString("title"), w.getString("status"), w.getString("detail"),
                        w.optString("project_name"), w.optInt("progress"),
                        w.optJSONArray("agents")?.let { a -> (0 until a.length()).map { a.getJSONObject(it) }.map { SwarmAgent(it.getString("name"), it.getString("state")) } },
                        w.optJSONArray("messages")?.let { a -> (0 until a.length()).map { a.getJSONObject(it) }.map { SwarmMessage(it.getString("sender"), it.getString("body"), it.getString("created_at")) } })
                }
                main.post { swarms = parsed; swarmsLoaded = true }
            }
        }
        val pending = next.optJSONArray("outbox") ?: JSONArray()
        val remaining = JSONArray()
        for (i in 0 until pending.length()) {
            val encoded = pending.getString(i)
            val queued = JSONObject(encoded)
            val cancellationWaiting = queued.optString("type") == "request.cancel" &&
                (type == "chat.accepted" || (type == "request.status" && payload.optString("status") in setOf("accepted", "running", "awaiting_approval")))
            if (queued.optString("request_id") != request || cancellationWaiting) remaining.put(encoded)
        }
        next.put("received", received).put("messages", local).put("cursor", if (type == "sync.snapshot") maxOf(next.optLong("cursor", -1), payload.getLong("through_sequence")) else sequence).put("outbox", remaining)
        val viewed = viewingThread?.takeIf { userSees && type == "sync.snapshot" }
        val viewedBefore = readMark(next, viewed)
        if (type == "sync.snapshot") ThreadActivity.markRead(next, viewed)
        val resolved = if (type in setOf("thread.snapshot", "error")) LiveJournal.resolveCreation(next, request,
            if (type == "thread.snapshot") event.getString("thread_id") else null) else next to null
        next = resolved.first
        val firstSend = resolved.second
        // A refused offer or chunk ends that upload instead of leaving it waiting with no answer to come.
        if (type == "error") UploadQueue.failRefused(next, pending, request)
        val advanced = advance(next)
        next = advanced.state
        store.save(next); saved = next; publish(); transfers.cleanup(saved)
        firstSend?.let { connection?.send(it) }
        advanced.finish()
        sendViewedRead(viewed, viewedBefore)
        startLinkDownloads()
        if (type == "thread.snapshot" && (0 until pending.length()).any { JSONObject(pending.getString(it)).let { e -> e.optString("request_id") == request && e.optString("type") == "thread.create" } }) main.post { createdThread = event.getString("thread_id") }
        if (type == "sync.snapshot" && payload.has("next_threads_offset") && !payload.isNull("next_threads_offset")) requestSync(payload.getLong("next_threads_offset"))
        if (type == "error") {
            val original = (0 until pending.length()).map { JSONObject(pending.getString(it)) }.firstOrNull { it.optString("request_id") == request }
            original?.takeIf { it.optString("type") in setOf("desktop.read", "desktop.action") }?.let { e ->
                e.getJSONObject("payload").let { p -> desktopRequests.putIfAbsent(request, DesktopViews.key(p.getString("op"), p.optJSONObject("params") ?: JSONObject()) to (e.getString("type") == "desktop.action")) }
            }
            if (desktopRequests.containsKey(request)) {
                // A refused desktop request ends like a failed answer: its preview or image slot frees, a Rem message comes
                // back with Try again, and only an action the user asked for shows the dialog.
                receiveDesktop(saved, request, JSONObject().put("ok", false).put("detail", payload.optString("detail")))
            } else if (original?.optString("type") == "thread.search.request") main.post {
                if (original.getJSONObject("payload").optString("query") == searchQuery) { searching = false; searchError = payload.optString("detail", "Search failed. Try again.") }
            } else main.post { requestError = payload.optString("detail", "The desktop could not accept this request.") }
            publishStatus(payload.optString("detail").takeIf { it.isNotBlank() } ?: when (payload.optString("code")) {
            "unauthorized" -> "Desktop permission is required. Check this phone's thread access in Paired devices."
            "conflict" -> "The desktop rejected a conflicting request. Refresh threads before trying again."
            else -> "The desktop could not accept this request. Check Paired devices on your computer."
        })
        }
        if (type == "file.chunk" && saved.optJSONObject("files")?.optJSONObject(payload.getString("file_id")) != null) {
            val file = saved.getJSONObject("files").getJSONObject(payload.getString("file_id"))
            queue("file.status", file.getString("thread"), JSONObject().put("file_id", payload.getString("file_id"))
                .put("state", file.getString("state")).put("received_size", file.getLong("offset")))
        }
        if (type == "chat.message" && payload.optBoolean("final")) LiveNotifications.show(getApplication(), event.getString("thread_id"))
    }
    /** Sync entries omit per-thread resources; keep the last ones from history or settings replies. */
    private fun keepResources(state: JSONObject, threads: JSONArray): JSONArray {
        val old = state.optJSONArray("threads") ?: return threads
        val byId = (0 until old.length()).map { old.getJSONObject(it) }.filter { it.has("resources") }.associateBy { it.getString("thread_id") }
        for (i in 0 until threads.length()) threads.getJSONObject(i).let { t ->
            byId[t.getString("thread_id")]?.let { if (!t.has("resources")) t.put("resources", it.getJSONArray("resources")) }
        }
        return threads
    }
    private fun publish() {
        val list = saved.optJSONArray("threads") ?: JSONArray()
        val threadList = (0 until list.length()).map { list.getJSONObject(it).let { item -> item.getString("thread_id") to item.getString("title") } }
        val templates = (0 until list.length()).map { list.getJSONObject(it) }.filter { it.optBoolean("can_create") }
            .map { it.getString("thread_id") to it.getString("title") }
        val local = saved.optJSONArray("messages") ?: JSONArray()
        val messageList = (0 until local.length()).map { local.getJSONObject(it).let { m -> run {
            val p = m.optJSONObject("presentation") ?: JSONObject()
            val trace = p.optJSONObject("trace") ?: JSONObject()
            val attached = m.optJSONArray("attachments") ?: JSONArray()
            LiveMessage(m.getString("id"), m.getString("thread"), m.getString("text"), m.getString("state"), m.optString("detail"),
                m.optString("source_id", m.getString("id")), m.optLong("offset"), p.optString("kind"), p.optString("groupId"),
                trace.keys().asSequence().associateWith { trace.getString(it) },
                (0 until attached.length()).map { attached.getJSONObject(it).let { a -> Attachment(a.getString("id"), a.getString("name"), a.getLong("size")) } },
                p.optBoolean("running"), if (p.has("durationMs")) p.getLong("durationMs") else null, p.optString("completedAt"), p.optBoolean("truncated"),
                if (p.has("startedAt")) p.getLong("startedAt") else null)
        } } }
        val fileObject = saved.optJSONObject("files") ?: JSONObject()
        val fileList = fileObject.keys().asSequence().map { id -> fileObject.getJSONObject(id).let { file ->
            LiveFile(id, file.getString("thread"), file.getString("name"), file.getString("state"), file.getString("direction"), file.getLong("offset"), file.getLong("size"))
        } }.toList()
        val marks = saved.optJSONObject("read_marks")
        val contexts = (0 until list.length()).map { list.getJSONObject(it) }.associate { t -> t.getString("thread_id") to ThreadContext(t.optString("provider"), t.optString("model"), t.optLong("revision", 1), t.optString("project_id").takeUnless { it == "null" || it.isBlank() }, t.optString("effort").takeUnless { it == "null" }.orEmpty(), t.optString("group", "active"), t.optBoolean("running"), t.optString("updated_at"), t.optString("permission_mode"), t.optBoolean("can_manage"), t.optBoolean("delete_pending"), ThreadActivity.unread(t, marks), t.optString("read_at")) }
        val catalog = saved.optJSONObject("catalog")
        val available = catalog?.optJSONArray("providers") ?: JSONArray()
        val providerMap = (0 until available.length()).map { available.getJSONObject(it) }.associate { p -> p.getString("id") to p.getJSONArray("models").let { models -> (0 until models.length()).map { models.getString(it) } } }
        val modeMap = (0 until available.length()).map { available.getJSONObject(it) }.associate { p -> p.getString("id") to (p.optJSONArray("permission_modes") ?: JSONArray()).let { a -> (0 until a.length()).map { a.getString(it) } } }
        val defaults = (0 until available.length()).map { available.getJSONObject(it) }.associate { p -> p.getString("id") to p.optString("default_permission", "default") }
        val effortMap = (0 until available.length()).map { available.getJSONObject(it) }.associate { p ->
            p.getString("id") to (p.optJSONObject("efforts") ?: JSONObject()).let { e -> e.keys().asSequence().associateWith { model ->
                e.getJSONArray(model).let { values -> (0 until values.length()).map { values.getString(it) } }
            } }
        }
        val projectList = catalog?.optJSONArray("projects") ?: JSONArray()
        val projectMap = (0 until projectList.length()).map { projectList.getJSONObject(it) }.associate { it.getString("id") to it.getString("name") }
        val resourceMap = (0 until list.length()).map { list.getJSONObject(it) }.filter { it.has("resources") }.associate { t ->
            // The latest synced resource IDs win over the enabled flags from an older reply.
            val enabledIds = t.optJSONArray("resource_ids")?.let { a -> (0 until a.length()).map { a.getString(it) }.toSet() }
            t.getString("thread_id") to t.getJSONArray("resources").let { a -> (0 until a.length()).map { a.getJSONObject(it) }.map {
                Resource(it.getString("id"), it.getString("name"), it.getString("kind"), enabledIds?.contains(it.getString("id")) ?: it.getBoolean("enabled")) } }
        }
        val before = saved.optJSONObject("history_before") ?: JSONObject()
        val beforeMap = before.keys().asSequence().associateWith { before.getLong(it) }
        val hasPeer = saved.has("peer")
        val hasPending = saved.has("pending_peer")
        val draftObject = saved.optJSONObject("drafts") ?: JSONObject()
        val draftMap = draftObject.keys().asSequence().associateWith { draftObject.getString(it) } + pendingDrafts.toMap()
        val fleetSnapshot = saved.optString("fleet")
        val endpoint = lanEndpoint()
        val hasLan = endpoint != null
        val host = endpoint?.optString("host").orEmpty()
        val port = endpoint?.optInt("port")?.toString().orEmpty()
        main.post { lanHost = host; lanPort = port }
        val tailnetOn = DeviceRoutes.tailscaleEnabled(saved)
        val chosen = DeviceRoutes.endpoint(saved.optJSONObject("tailscale")?.optJSONObject("endpoint")) != null
        val tailnet = DeviceRoutes.tailnetEndpoint(saved, saved.optJSONObject("peer") ?: saved.optJSONObject("pending_peer"))
        main.post { tailscaleEnabled = tailnetOn; tailscaleChosen = chosen; tailscaleHost = tailnet?.host.orEmpty(); tailscalePort = tailnet?.port?.toString().orEmpty() }
        publishWake()
        val pendingCreation = (saved.optJSONObject("creations")?.length() ?: 0) > 0
        val held = saved.optJSONObject(HeldSends.KEY)?.let { RemHeld(it.getString("text"), HeldSends.ids(it), it.optJSONObject("reply")?.let(::referenceOf)) }
        val copies = RemAttachments.files(saved)
        main.post { attachmentCopies = copies; remHeld = held; creatingThread = pendingCreation; permissionModes = modeMap; defaultPermissions = defaults; efforts = effortMap; threadContexts = contexts; threadResources = resourceMap; providers = providerMap; projects = projectMap; historyBefore = beforeMap; creationTemplates = templates; lanAvailable = hasLan; paired = hasPeer; pendingPair = hasPending; files = fileList; drafts = draftMap; threads = threadList; messages = messageList; fleet = fleetSnapshot }
    }
    companion object {
        private const val NOTICE_CURSOR = "rem_notice_cursor"
        private const val NOTICE_MARK = "rem_notice_mark"
        private const val NOTICE_ROUNDS = "rem_notice_rounds"
        private const val NOTICE_INTERVAL = 10_000L
        private const val PROGRESS_INTERVAL = 15_000L
        private const val PROGRESS_STALE = 120_000L
        private const val SELF_WORK_INTERVAL = 30_000L
        private const val ALERT_REPLY_MILLIS = 120_000L
    }
    override fun onCleared() { stopped = true; imports.cancel(); queuedImports.toList().forEach { if (queuedImports.remove(it)) it.close() }; reconnect = false; activeCandidate?.close(); connection?.close(); wifi.close(); tailscale.close(); store.close(); super.onCleared() }
}
