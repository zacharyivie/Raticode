package dev.raticode.mobile.ui

import androidx.activity.compose.BackHandler
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.foundation.interaction.collectIsDraggedAsState
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.saveable.rememberSaveableStateHolder
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.unit.dp
import androidx.compose.ui.platform.LocalContext
import dev.raticode.mobile.live.LiveRuntime
import dev.raticode.mobile.live.DeviceSyncService
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import kotlinx.coroutines.launch
import dev.raticode.mobile.live.VoiceTidy
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.live.Kanban
import dev.raticode.mobile.live.SelfWorkView
import androidx.core.content.FileProvider

/**
 * Bottom-bar pages. The user picks up to [MAX] destinations, like choosing visible plugins on desktop; More always
 * follows them and lists every destination left out, so none becomes unreachable. The app opens to the first one;
 * notification and share links still open their own page.
 */
/** Where Source control keeps its place while Ask Rem visits Rem's chat. */
private const val SOURCE_CONTROL_STATE = "source-control"

internal object LiveNavigation {
    const val HOME = "Rem"
    const val MORE = "More"
    /** The page that chooses the bar, reached from More. */
    const val NAV_BAR = "NavBar"
    /** People Rem's call and text cards can reach, reached from More. */
    const val PEOPLE = "People"
    const val APP_LOCK = "AppLock"
    /** Rem's self-work settings, reached from Desktop settings, the board's Self-work and Rem's report. */
    const val SELF_WORK = "SelfWork"
    const val MAX = 4
    /** A page the bar can show: its page name, its short bar label, and its full name in More and settings. */
    data class Destination(val page: String, val label: String, val title: String)
    /** In the order More lists them; the pages More always held keep their old order, and newer pages follow. */
    val destinations = listOf(
        Destination("Rem", "Rem", "Rem"),
        Destination("Threads", "Threads", "Threads"),
        Destination("Organizations", "Orgs", "Organizations"),
        Destination("Swarms", "Swarms", "Swarms"),
        Destination("Voice", "Voice", "Voice assistant"),
        Destination("Workflows", "Workflows", "Workflows"),
        Destination("Approvals", "Approvals", "Approvals"),
        Destination("Settings", "Settings", "Desktop settings"),
        Destination("Usage", "Usage", "Usage"),
        Destination("SourceControl", "Source", "Source control"),
        Destination("Explorer", "Files", "Files"),
        Destination("Developer", "Log", "Developer log"),
        Destination("Browser", "Browser", "Browser"),
        Destination("Kanban", "Board", "Global board"),
    )
    private val byPage = destinations.associateBy { it.page }
    /** The bar before the user changes it, and after a saved choice turns out unreadable. */
    val DEFAULT = listOf("Rem", "Threads", "Organizations", "Swarms")

    fun destination(page: String) = byPage[page]
    /** Page name to bar label, left to right, ending with More. */
    fun tabs(bar: List<String> = DEFAULT) = bar.mapNotNull(byPage::get).map { it.page to it.label } + (MORE to MORE)
    fun topLevel(bar: List<String> = DEFAULT) = bar.toSet() + MORE
    /** Destinations More lists: everything not on the bar, in the usual order. */
    fun omitted(bar: List<String>) = destinations.filter { it.page !in bar }

    /** A saved bar: known pages, each once, at most [MAX]. Anything else falls back to [DEFAULT]. */
    fun parse(saved: String?): List<String> {
        val pages = saved.orEmpty().split(',').map { it.trim() }.filter { it in byPage }.distinct().take(MAX)
        return pages.ifEmpty { DEFAULT }
    }
    fun encode(bar: List<String>) = bar.joinToString(",")
    /** Adds [page] at the end if there is room, or removes it while another page stays. */
    fun toggle(bar: List<String>, page: String): List<String> = when {
        page in bar -> if (bar.size > 1) bar - page else bar
        page in byPage && bar.size < MAX -> bar + page
        else -> bar
    }
    /** Moves [page] one place left ([delta] -1) or right (+1). */
    fun move(bar: List<String>, page: String, delta: Int): List<String> {
        val from = bar.indexOf(page)
        val to = from + delta
        if (from < 0 || to !in bar.indices) return bar
        return bar.toMutableList().apply { add(to, removeAt(from)) }
    }

    fun initialPage(initialThread: String?, home: String = HOME) =
        when (initialThread) { null -> home; "approvals" -> "Approvals"; "rem" -> "Rem"; else -> "Chat" }
    /** Where system back goes from [page], or null when back leaves the app. A page opened from More goes back to More. */
    fun back(page: String, lastTop: String, home: String = HOME, bar: List<String> = DEFAULT) = when (page) {
        home -> null
        "Files" -> "Chat"
        "Chat" -> "Threads"
        in topLevel(bar) -> home
        else -> lastTop.takeIf { it != page } ?: home
    }
}

@OptIn(ExperimentalLayoutApi::class)
@androidx.annotation.RequiresApi(29)
@Composable
fun LiveScreen(onBack: () -> Unit = {}, initialThread: String? = null, liveClient: LiveClient? = null, consumeInitialThread: () -> Unit = {}, scanOnOpen: Boolean = false, consumeScan: () -> Unit = {},
               offerVoiceSetup: Boolean = false, consumeVoiceOffer: () -> Unit = {}, startPage: String? = null) {
    val context = LocalContext.current
    // The user's bottom bar; the app opens to its first destination unless a caller names one.
    var bar by remember { mutableStateOf(NavBarPrefs.load(context)) }
    val home = startPage ?: bar.first()
    val client = liveClient ?: remember { LiveRuntime.client(context.applicationContext as android.app.Application) }
    val shortScreen = androidx.compose.ui.platform.LocalConfiguration.current.screenHeightDp < 480
    val typing = WindowInsets.isImeVisible
    val compactTyping = typing &&
        androidx.compose.ui.platform.LocalConfiguration.current.orientation == android.content.res.Configuration.ORIENTATION_LANDSCAPE
    var code by remember { mutableStateOf("") }
    var attachments by rememberSaveable { mutableStateOf(listOf<String>()) }
    var now by remember { mutableStateOf(System.currentTimeMillis()/1000) }
    LaunchedEffect(Unit) { while (true) { kotlinx.coroutines.delay(2000); now = System.currentTimeMillis()/1000 } }
    var draft by remember { mutableStateOf("") }
    var selected by rememberSaveable { mutableStateOf(initialThread?.takeIf { it != "approvals" && it != "rem" }) }
    val homeState = rememberSaveableStateHolder()
    // Files on their way to Rem's message box from another page, such as Ask Rem in the Developer log.
    var remHandoff by remember { mutableStateOf<RemHandoff?>(null) }
    var page by rememberSaveable { mutableStateOf(LiveNavigation.initialPage(initialThread, home)) }
    /** Words for Rem's message box from another page, such as Ask Rem in the browser, file viewer or Source control. Nothing sends. */
    fun askRem(words: String) { remHandoff = RemHandoff(System.nanoTime(), emptyList(), words); page = "Rem" }
    // Source control starts over when you leave it, except when Ask Rem took you to Rem's chat: coming back finds the same diff.
    var keepSourceControl by remember { mutableStateOf(false) }
    var previousPage by remember { mutableStateOf(page) }
    LaunchedEffect(page) {
        if (previousPage == "SourceControl" && page != "SourceControl" && !keepSourceControl) homeState.removeState(SOURCE_CONTROL_STATE)
        keepSourceControl = false
        previousPage = page
    }
    val topLevel = LiveNavigation.topLevel(bar)
    var browserReveal by remember { mutableIntStateOf(0) }
    // The global board's section, kept across pages so Rem's report can open it.
    var kanbanTab by rememberSaveable { mutableStateOf(KanbanTabs.BOARD) }
    // An organization the board asked to open, taken up by the Organizations page.
    var organizationToOpen by remember { mutableStateOf<String?>(null) }
    // A ticket a pill in Rem's chat asked to open, taken up by the global board.
    var ticketToOpen by remember { mutableStateOf<String?>(null) }
    // Where Rem self-work goes back to: the page that opened it.
    var selfWorkBack by rememberSaveable { mutableStateOf("Settings") }
    fun openSelfWork(from: String) { selfWorkBack = from; page = LiveNavigation.SELF_WORK }
    var lastTop by rememberSaveable { mutableStateOf(home) }
    LaunchedEffect(page, bar) { if (page in topLevel) lastTop = page }
    // Reads the desktop's wake setting afresh, so turning it on or off shows without reconnecting.
    LaunchedEffect(page, client.connected) { if (page == "Desktop" && client.connected) client.checkDesktopFeatures() }
    BackHandler(enabled = client.paired && LiveNavigation.back(page, lastTop, home, bar) != null) {
        LiveNavigation.back(page, lastTop, home, bar)?.let { page = it }
    }
    BackHandler(enabled = client.paired && page == LiveNavigation.SELF_WORK) { page = selfWorkBack }
    /** A destination on the bar has no back arrow; opened from More, it has one that goes back there. */
    fun backFrom(target: String): (() -> Unit)? = if (target in bar) null else ({ page = LiveNavigation.back(target, lastTop, home, bar) ?: home })
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    DisposableEffect(client, lifecycle) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_START) client.foreground()
            if (event == Lifecycle.Event.ON_STOP) client.background()
        }
        lifecycle.addObserver(observer)
        if (liveClient == null && lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)) client.foreground()
        onDispose { lifecycle.removeObserver(observer) }
    }
    // Passing App lock is when the user first sees this page, so reading it and clearing its alerts wait for that.
    val appLocked = remember { dev.raticode.mobile.applock.AppLockRuntime.get(context) }.locked
    var wasAppLocked by remember { mutableStateOf(appLocked) }
    LaunchedEffect(appLocked) {
        if (wasAppLocked && !appLocked && lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)) client.foreground()
        wasAppLocked = appLocked
    }
    LaunchedEffect(client.threads) {
        if (selected != null && client.loaded && client.threads.none { it.first == selected }) {
            selected = null
            if (page in setOf("Chat", "Files")) page = "Threads"
        }
    }
    var draftEdited by remember(selected) { mutableStateOf(false) }
    LaunchedEffect(selected, client.drafts) { if (!draftEdited) draft = client.drafts[selected].orEmpty() }
    LaunchedEffect(selected) { attachments = emptyList(); client.openThread(selected) }
    // Only a chat on screen counts as read, matching desktop's "unread until opened" rule.
    LaunchedEffect(selected, page) { client.viewThread(selected?.takeIf { page == "Chat" }) }
    LaunchedEffect(initialThread) {
        if (initialThread == "approvals") { page = "Approvals"; consumeInitialThread() }
        else if (initialThread == "rem") { page = "Rem"; consumeInitialThread() }
        else if (initialThread != null) { selected = initialThread; page = "Chat"; consumeInitialThread() }
    }
    var exportId by rememberSaveable { mutableStateOf<String?>(null) }
    var importThread by rememberSaveable { mutableStateOf<String?>(null) }
    val importLauncher = rememberLauncherForActivityResult(androidx.activity.result.contract.ActivityResultContracts.OpenDocument()) { uri ->
        val thread = importThread
        importThread = null
        if (uri != null && thread != null) client.importFile(thread, uri) { id -> if (selected == thread) attachments = (attachments + id).take(8) }
    }
    val exportLauncher = rememberLauncherForActivityResult(androidx.activity.result.contract.ActivityResultContracts.CreateDocument("application/octet-stream")) { uri ->
        if (uri != null && exportId != null) client.exportFile(exportId!!, uri)
        exportId = null
    }
    val openFile = rememberFileOpener(client) { id, name -> exportId = id; exportLauncher.launch(name) }
    var listening by remember { mutableStateOf(false) }
    var speechError by remember { mutableStateOf<String?>(null) }
    var dictation by remember { mutableStateOf<OnDeviceDictation?>(null) }
    var tidyingDictation by remember { mutableStateOf(false) }
    var tidyNotice by remember { mutableStateOf<String?>(null) }
    val dictationScope = rememberCoroutineScope()
    val latestDraft by rememberUpdatedState(draft)
    val latestSelected by rememberUpdatedState(selected)
    fun addDictated(target: String, words: String) {
        fun join(base: String) = listOf(base.trimEnd(), words).filter { it.isNotBlank() }.joinToString(" ").take(4000)
        if (latestSelected == target) {
            draftEdited = true
            draft = join(latestDraft)
            client.draft(target, draft)
        } else client.draft(target, join(client.drafts[target].orEmpty())) // Tidied after leaving the chat: kept in its draft.
    }
    fun startDictation() {
        val target = selected ?: return
        speechError = null; tidyNotice = null
        dictation?.close()
        dictation = OnDeviceDictation(context, result = { words ->
            // With voice tidying on, the words show once the desktop has tidied them, or as heard if it couldn't.
            if (client.voiceTidy) {
                tidyingDictation = true
                dictationScope.launch {
                    val tidied = client.tidyVoice(words)
                    tidyingDictation = false
                    addDictated(target, tidied.text)
                    tidyNotice = VoiceTidy.notice(tidied).ifBlank { null }
                }
            } else addDictated(target, words)
        }, ended = { error -> listening = false; speechError = error })
        listening = dictation?.start() == true
        if (!listening) speechError = "On-device speech is unavailable. Install an offline speech language in Android settings, or use your keyboard's microphone."
    }
    val microphone = rememberLauncherForActivityResult(androidx.activity.result.contract.ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) startDictation() else speechError = "Microphone access is off. You can still type a message."
    }
    DisposableEffect(selected, lifecycle) {
        val observer = LifecycleEventObserver { _, event -> if (event == Lifecycle.Event.ON_STOP) { dictation?.close(); dictation = null; listening = false } }
        lifecycle.addObserver(observer)
        onDispose { lifecycle.removeObserver(observer); dictation?.close(); dictation = null; listening = false }
    }
    val selectableProviders = client.providers.keys
    var modelPicker by remember { mutableStateOf(false) }
    LaunchedEffect(selected) { modelPicker = false }
    var providerChoice by remember { mutableStateOf("") }
    var modelChoice by remember { mutableStateOf("") }
    var contextRevision by remember { mutableStateOf<Long?>(null) }
    var permissionChoice by remember { mutableStateOf("") }
    var effortChoice by remember { mutableStateOf("") }
    var projectChoice by remember { mutableStateOf<String?>(null) }
    var resourceChoice by remember { mutableStateOf(setOf<String>()) }
    LaunchedEffect(modelPicker) {
        if (modelPicker) resourceChoice = client.threadResources[selected].orEmpty().filter { it.enabled }.map { it.id }.toSet()
    }
    var searchTarget by remember { mutableStateOf<String?>(null) }
    var attachAfterCreation by rememberSaveable { mutableStateOf(false) }
    LaunchedEffect(client.createdThread) {
        client.createdThread?.let {
            selected = it; page = "Chat"; client.consumeCreatedThread()
            if (attachAfterCreation) {
                attachAfterCreation = false; importThread = it; importLauncher.launch(arrayOf("*/*"))
            }
        }
    }
    LaunchedEffect(client.requestError) { if (client.requestError != null) attachAfterCreation = false }
    var reset by remember { mutableStateOf(false) }
    var clearHistory by remember { mutableStateOf<String?>(null) }
    var removeFile by remember { mutableStateOf<String?>(null) }
    val scanner = rememberLauncherForActivityResult(ScanContract()) { result -> result.contents?.let(client::preview) }
    val scan = {
        scanner.launch(ScanOptions().setCaptureActivity(SecureCaptureActivity::class.java)
            .setDesiredBarcodeFormats(ScanOptions.QR_CODE).setPrompt("Scan the invitation in desktop Device settings")
            .setBeepEnabled(false).setOrientationLocked(false))
    }
    LaunchedEffect(scanOnOpen) { if (scanOnOpen) { consumeScan(); scan() } }
    val imagesEnabled = client.mediaProxy && client.connected
    val imageSource = remember(client, imagesEnabled) { ChatImageSource(imagesEnabled, { client.images[it] }, client::requestImage) }
    // Web links in chats open in Raticode's browser unless the user chose the phone's own browser there.
    val links = rememberChatLinks(client) { url ->
        val browser = dev.raticode.mobile.browser.BrowserRuntime.get(context)
        val firefox = if (browser.saved.engine == dev.raticode.mobile.browser.BrowserRules.ENGINE_FIREFOX)
            dev.raticode.mobile.browser.FirefoxApps.find(context) else null
        val other = dev.raticode.mobile.browser.BrowserApps.byEngine(browser.saved.engine)?.find(context)
        when {
            !browser.saved.linksInApp -> false
            browser.saved.engine == dev.raticode.mobile.browser.BrowserRules.ENGINE_GECKO ->
                dev.raticode.mobile.browser.GeckoBrowser.get(context).newTab(url) != null &&
                    run { browser.show(dev.raticode.mobile.browser.BrowserRules.ENGINE_GECKO); page = "Browser"; true }
            // Firefox chosen and on the phone: the page opens there. Without it, Raticode's browser takes the link.
            firefox != null && dev.raticode.mobile.browser.FirefoxApps.open(context, firefox, url, browser.saved.firefoxMode) -> true
            // Edge chosen and on the phone, likewise.
            browser.saved.engine == dev.raticode.mobile.browser.BrowserRules.ENGINE_EDGE &&
                dev.raticode.mobile.browser.EdgeApps.find(context)?.let { dev.raticode.mobile.browser.EdgeApps.open(context, it, url, browser.saved.edgeMode) } == true -> true
            // The same for Chrome or another of BrowserApps, when it is installed and its maker signed it.
            other != null && other.app.open(context, other, url, dev.raticode.mobile.browser.BrowserRules.appMode(browser.saved, other.app.engine)) -> true
            // The Browser page shows the new tab even while it was showing a Raticode (gecko) tab.
            else -> browser.newTab(url) != null && run { browser.show(dev.raticode.mobile.browser.BrowserRules.ENGINE_RATICODE); page = "Browser"; true }
        }
    }
    val attachmentHost = remember(client, openFile) { AttachmentHost.of(client, openFile) }
    CompositionLocalProvider(LocalChatImages provides imageSource, LocalChatLinks provides links, LocalAttachments provides attachmentHost) {
    Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
        Column(Modifier.fillMaxSize().safeDrawingPadding().imePadding()) {
            // Above every page while a tab plays, except over the tab that plays: the Browser page already shows it.
            // Full-screen video and a keyboard on a short screen need the room more.
            val mediaTrack = dev.raticode.mobile.browser.BrowserMedia.bar
            val fullscreenVideo = dev.raticode.mobile.browser.BrowserMedia.fullscreen && page == "Browser"
            if (mediaTrack != null && !fullscreenVideo && !compactTyping && !(typing && shortScreen))
                BrowserMediaBar(mediaTrack, onOpen = { dev.raticode.mobile.browser.BrowserMedia.reveal(context, it); page = "Browser" })
            if (page == "Desktop" || page == "Files" || !client.paired) StudioHeader(if (client.paired) { if (client.connected) "Encrypted ${client.route}" else "Offline" } else "Mobile companion") {
                if (client.paired) TextButton(onClick = { page = if (page == "Desktop") "Threads" else "Desktop" },
                    colors = ButtonDefaults.textButtonColors(contentColor = LocalStudioColors.current.onNavigation)) {
                    Text(if (page == "Desktop") "Threads" else "Desktop")
                }
            }
            if (page == "Desktop" || !client.paired) ConnectionStatus(client.connected, client.status, Modifier.fillMaxWidth())
            if (dev.raticode.mobile.live.WakeOnLan.showsBanner(client.paired, client.cantConnect, client.wakeAvailable, typing, page, topLevel)) WakeBanner(client)
            Column(Modifier.weight(1f).fillMaxWidth()) {
                if (!client.loaded) {
                    LinearProgressIndicator(Modifier.fillMaxWidth())
                } else if (!client.paired) {
                    Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        Text("Connect to your desktop", style = MaterialTheme.typography.headlineMedium)
                        Text("Continue your desktop conversations with Rem from your phone. Both devices must use the same Wi-Fi network, or your own Tailscale network if you turn it on below.", color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Spacer(Modifier.height(12.dp))
                        SettingsGroup("Pair your desktop") {
                        Text("On your computer, open Paired devices and choose Pair a device. Scan the QR, then confirm this phone.")
                        if (client.pendingPair) {
                            Text("Confirm this phone on desktop, then recover the connection.")
                            LanRecovery(client)
                            Button(onClick = client::connect) { Text("Recover pending pairing") }
                            TextButton(onClick = { reset = true }) { Text("Forget pending pairing") }
                        }
                        Button(onClick = scan, modifier = Modifier.fillMaxWidth()) { Text("Scan desktop QR") }
                        }
                        SettingsGroup("Use an invitation link") {
                        OutlinedTextField(code, { code = it.take(4096) }, label = { Text("Paste invitation") }, modifier = Modifier.fillMaxWidth().testTag("invitationField"), maxLines = 4)
                        OutlinedButton(onClick = { client.preview(code); code = "" }, enabled = code.isNotBlank(), modifier = Modifier.fillMaxWidth()) { Text("Review invitation") }
                        }
                        SettingsGroup("Tailscale (optional)") { TailscaleSettings(client) }
                    }
                } else if (page == "Threads" || (page == "Chat" && selected == null)) {
                    backFrom("Threads")?.let { MoreBackRow(it) }
                    homeState.SaveableStateProvider("thread-home") {
                    ThreadHome(client, onOpen = { id, target -> searchTarget = target; selected = id; page = "Chat" },
                        onDesktop = { page = "Desktop" }, onAttachAfterCreation = { attachAfterCreation = true },
                        onApprovals = { page = "Approvals" })
                    }
                } else if (page == "Approvals") {
                    ApprovalsPage(client, backFrom("Approvals"))
                } else if (page == "Rem") {
                    backFrom("Rem")?.let { MoreBackRow(it) }
                    // Rem's draft, reply target and attachments stay while another page is open.
                    homeState.SaveableStateProvider("rem") { RemPage(client, remHandoff, onHandoffTaken = { remHandoff = null }, onSelfWork = {
                        // With the board off on the computer, the report is with self-work's settings.
                        if ((client.desktopViews[Kanban.SELF_WORK] as? SelfWorkView)?.state?.boardEnabled == false) openSelfWork("Rem")
                        else { kanbanTab = KanbanTabs.SELF; page = "Kanban" }
                    }, onTicket = { ticket -> ticketToOpen = ticket.uid; kanbanTab = KanbanTabs.BOARD; page = "Kanban" }) }
                } else if (page == "Developer") {
                    DeveloperLogPage(client, backFrom("Developer")) { file, name ->
                        remHandoff = RemHandoff(System.nanoTime(), listOf(Picked(FileProvider.getUriForFile(context,
                            context.packageName + dev.raticode.mobile.data.OpenedFiles.AUTHORITY_SUFFIX, file), name, file)))
                        page = "Rem"
                    }
                } else if (page == "Organizations") {
                    OrganizationsPage(client, backFrom("Organizations"), organizationToOpen) { organizationToOpen = null }
                } else if (page == "Kanban") {
                    KanbanPage(client, kanbanTab, { kanbanTab = it }, backFrom("Kanban"), openTicket = ticketToOpen, onTicketOpened = { ticketToOpen = null },
                        onSelfWorkSettings = { openSelfWork("Kanban") }) { organizationToOpen = it; page = "Organizations" }
                } else if (page == LiveNavigation.SELF_WORK) {
                    SelfWorkPage(client, { page = selfWorkBack }, onProjects = { kanbanTab = KanbanTabs.PEOPLE; page = "Kanban" }) { uid ->
                        ticketToOpen = uid; kanbanTab = KanbanTabs.BOARD; page = "Kanban" }
                } else if (page == LiveNavigation.MORE) {
                    MorePage(client, bar) { page = it }
                } else if (page == LiveNavigation.APP_LOCK) {
                    AppLockPage { page = LiveNavigation.MORE }
                } else if (page == LiveNavigation.PEOPLE) {
                    PeoplePage { page = LiveNavigation.MORE }
                } else if (page == LiveNavigation.NAV_BAR) {
                    NavBarSettingsPage(bar, onChange = { bar = it; NavBarPrefs.save(context, it) }) { page = LiveNavigation.MORE }
                } else if (page == "Settings") {
                    DesktopSettingsPage(client, backFrom("Settings")) { openSelfWork("Settings") }
                } else if (page == "Usage") {
                    UsagePage(client, backFrom("Usage"))
                } else if (page == "Voice") {
                    VoicePage(client, backFrom("Voice"))
                } else if (page == "Workflows") {
                    WorkflowsPage(client, backFrom("Workflows"))
                } else if (page == "SourceControl") {
                    homeState.SaveableStateProvider(SOURCE_CONTROL_STATE) {
                        SourceControlPage(client, backFrom("SourceControl")) { words -> keepSourceControl = true; askRem(words) }
                    }
                } else if (page == "Explorer") {
                    homeState.SaveableStateProvider("explorer") { FileExplorerPage(client, backFrom("Explorer"), ::askRem) }
                } else if (page == "Browser") {
                    BrowserPage(client, backFrom("Browser"), browserReveal, ::askRem)
                } else if (page == "Swarms") {
                    SwarmsPage(client, onBack = backFrom("Swarms"), onApprovals = { page = "Approvals" })
                } else if (page == "Chat" && selected != null && client.threads.any { it.first == selected }) {
                    val current = selected!!
                    val currentInfo = client.threadContexts[current]
                    if (!compactTyping) Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                        IconButton(onClick = { page = "Threads" }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Threads") }
                        Column(Modifier.weight(1f)) {
                            Text(client.threads.first { it.first == current }.second, style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            if (shortScreen) Text((client.projects[currentInfo?.project] ?: "Global") + if (currentInfo?.running == true) " · Running" else "", style = MaterialTheme.typography.labelSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        }
                        if (shortScreen) TextButton(modifier = Modifier.widthIn(max = 150.dp).semantics { contentDescription = "Thread settings" }, onClick = {
                            providerChoice = currentInfo?.provider.orEmpty(); modelChoice = currentInfo?.model.orEmpty(); effortChoice = currentInfo?.effort.orEmpty(); projectChoice = currentInfo?.project; contextRevision = currentInfo?.revision; permissionChoice = currentInfo?.permission.orEmpty(); modelPicker = true
                        }) { Text(listOfNotNull(currentInfo?.model?.takeIf { it.isNotBlank() }, currentInfo?.effort?.takeIf { it.isNotBlank() }).joinToString(" · ").ifBlank { "Thread settings" }, maxLines = 1, overflow = TextOverflow.Ellipsis) }
                        TextButton(onClick = { page = "Files" }) { Text("Files") }
                    }
                    if (!typing && !shortScreen) Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                        val info = client.threadContexts[current]
                        Surface(onClick = { providerChoice = info?.provider.orEmpty(); modelChoice = info?.model.orEmpty(); effortChoice = info?.effort.orEmpty(); projectChoice = info?.project; contextRevision = info?.revision; permissionChoice = info?.permission.orEmpty(); modelPicker = true }, color = MaterialTheme.colorScheme.background, modifier = Modifier.weight(1f)) {
                            Column(Modifier.padding(vertical = 6.dp)) {
                                ChoiceMenu("Scoped to ${client.projects[info?.project] ?: "Global"}", "Project scope",
                                    listOf("" to "Global") + client.projects.map { it.key to it.value }, info?.project.orEmpty()) { scope ->
                                    if (info != null) client.updateContext(current, info.provider, info.model, scope.takeIf(String::isNotBlank), info.effort, info.revision, info.permission)
                                }
                                Text(if (!client.connected) "Offline" else if (info?.running == true) "Rem is running" else "Encrypted ${client.route}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            }
                        }
                        TextButton(modifier = Modifier.widthIn(max = 160.dp).semantics { contentDescription = "Thread settings" }, onClick = { providerChoice = info?.provider.orEmpty(); modelChoice = info?.model.orEmpty(); effortChoice = info?.effort.orEmpty(); projectChoice = info?.project; contextRevision = info?.revision; permissionChoice = info?.permission.orEmpty(); modelPicker = true }) {
                            Text(listOfNotNull(info?.model?.takeIf { it.isNotBlank() }, info?.effort?.takeIf { it.isNotBlank() }).joinToString(" · ").ifBlank { "Thread settings" }, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        }
                    }
                    if (client.connected && client.remReady == false) Text("Desktop Rem is not ready. Open Paired devices on your computer to check its status.", Modifier.padding(horizontal = 16.dp), style = MaterialTheme.typography.bodySmall)
                    val messages = client.messages.filter { it.thread == current }
                    val chatItems = remember(messages) { buildChatItems(messages) }
                    var highlightedMessage by remember(current) { mutableStateOf<String?>(null) }
                    val history = rememberLazyListState()
                    var followLatest by remember(current) { mutableStateOf(searchTarget == null) }
                    LaunchedEffect(current, messages, searchTarget) {
                        val index = chatItems.indexOfFirst { searchTarget in it.anchors }
                        if (index >= 0) { highlightedMessage = searchTarget; followLatest = false; history.scrollToItem(index + 1); searchTarget = null }
                    }
                    val dragging by history.interactionSource.collectIsDraggedAsState()
                    val scrollScope = rememberCoroutineScope()
                    LaunchedEffect(history, current) {
                        snapshotFlow { history.firstVisibleItemIndex to history.firstVisibleItemScrollOffset }.collect {
                            if (dragging || !history.canScrollForward) followLatest = !history.canScrollForward
                        }
                    }
                    LaunchedEffect(history, current) {
                        // Re-anchor after every viewport resize, including the keyboard animation.
                        snapshotFlow { history.layoutInfo.viewportSize.height to history.layoutInfo.totalItemsCount }.collect { (_, count) ->
                            if (count > 1 && followLatest) history.scrollToItem(count - 1)
                        }
                    }
                    LaunchedEffect(current, chatItems) {
                        if (messages.isNotEmpty() && followLatest) history.scrollToItem(chatItems.size)
                    }
                    if (!followLatest) TextButton(onClick = {
                        followLatest = true
                        scrollScope.launch { history.animateScrollToItem(chatItems.size) }
                    }) { Text("Latest messages") }
                    Surface(Modifier.weight(1f).fillMaxWidth(), color = LocalStudioColors.current.chat, contentColor = LocalStudioColors.current.onChat) {
                        LazyColumn(state = history, contentPadding = PaddingValues(horizontal = 14.dp, vertical = 16.dp), verticalArrangement = Arrangement.spacedBy(16.dp), modifier = Modifier.testTag("liveMessages")) {
                            item {
                                if ((client.historyBefore[current] ?: 0) > 0) TextButton(onClick = { client.loadEarlier(current) }, enabled = client.connected) { Text("Load earlier messages") }
                                if (messages.isEmpty()) Column(Modifier.padding(top = 40.dp, bottom = 24.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                    Text("What are we working on?", style = MaterialTheme.typography.headlineSmall)
                                    Text("Ask Rem a question or continue work on your desktop project.", color = MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                            }
                            val newestReply = newestReplyId(chatItems)
                            items(chatItems, key = { it.id }) { item ->
                                ChatHistoryItem(item, highlightedMessage, newest = item.id == newestReply)
                            }
                        }
                    }
                    Surface(color = MaterialTheme.colorScheme.surface, shadowElevation = 2.dp) {
                    Column(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = if (compactTyping) 4.dp else 8.dp)) {
                    val info = client.threadContexts[current]
                    if (!compactTyping && info != null) ModelControls(client, info.provider, info.model, info.effort, !info.deletePending) { p, m, e ->
                        client.updateContext(current, p, m, info.project, e, info.revision,
                            if (p == info.provider) info.permission else client.defaultPermissions[p])
                    }
                    if (!compactTyping && info != null) ChoiceMenu(permissionLabel(info.permission), "Permissions",
                        client.permissionModes[info.provider].orEmpty().map { it to permissionLabel(it) }, info.permission,
                        Modifier.widthIn(max = 220.dp), !info.deletePending) {
                        client.updateContext(current, info.provider, info.model, info.project, info.effort, info.revision, it)
                    }
                    if (speechError != null) Text(speechError!!, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
                    if (listening) Text("Listening on this phone…", style = MaterialTheme.typography.bodySmall)
                    if (tidyingDictation) Text("Tidying your words…", style = MaterialTheme.typography.bodySmall, modifier = Modifier.testTag("dictationTidying"))
                    else tidyNotice?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.testTag("dictationTidyNotice")) }
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        attachments.forEach { id ->
                            val file = client.files.firstOrNull { it.id == id }
                            AttachmentTile(id, file?.name ?: "Attachment", file?.size ?: 0, onRemove = {
                                attachments = attachments - id
                                // An unfinished upload stops too, so it doesn't hold up the next one.
                                if (file?.direction == "upload" && file.state in setOf("offered", "receiving")) client.cancelFile(id)
                            })
                        }
                    }
                    var composerMenu by remember(current) { mutableStateOf(false) }
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(4.dp), verticalAlignment = Alignment.Bottom) {
                        Box {
                            IconButton(onClick = { composerMenu = true }, modifier = Modifier.size(48.dp)) { Icon(Icons.Default.Add, "Message options") }
                            DropdownMenu(expanded = composerMenu, onDismissRequest = { composerMenu = false }) {
                                DropdownMenuItem(text = { Text("Attach file") }, enabled = attachments.size < 8, onClick = {
                                    composerMenu = false; importThread = current; importLauncher.launch(arrayOf("*/*"))
                                })
                                DropdownMenuItem(text = { Text(if (listening) "Stop dictation" else "Dictate") }, onClick = {
                                    composerMenu = false
                                    if (listening) { dictation?.close(); dictation = null; listening = false }
                                    else microphone.launch(android.Manifest.permission.RECORD_AUDIO)
                                })
                                val running = messages.lastOrNull { it.state in setOf("running", "Accepted by desktop", "accepted", "awaiting_approval") }
                                if (running != null) DropdownMenuItem(text = { Text("Stop Rem") }, onClick = { composerMenu = false; client.stopRequest(current, running.id) })
                            }
                        }
                    OutlinedTextField(draft, { draftEdited = true; tidyNotice = null; draft = it.take(4000); client.draft(current, draft) }, placeholder = { Text("Message Rem") }, shape = MaterialTheme.shapes.large, modifier = Modifier.weight(1f).testTag("messageComposer").semantics { contentDescription = "Message Rem" }, minLines = 1, maxLines = if (compactTyping) 2 else 4)
                    FilledIconButton(shape = MaterialTheme.shapes.medium, modifier = Modifier.size(48.dp).semantics {
                        contentDescription = if (client.connected) "Send message" else "Queue message"
                    }, onClick = {
                        val submitted = draft
                        val submittedAttachments = attachments
                        client.sendAttachments(current, submitted, submittedAttachments) {
                            if (selected == current && draft == submitted) { draft = ""; draftEdited = false }
                            if (selected == current && attachments == submittedAttachments) attachments = emptyList()
                        }
                    }, enabled = !client.sending && (draft.isNotBlank() || attachments.isNotEmpty()) && attachments.all { id -> client.files.any { it.id == id && it.state == "available" } }) { if (client.sending) CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp) else Icon(ThreadIcons.Send, null, Modifier.size(20.dp)) }
                    }
                    }
                    }
                } else if (page == "Files") {
                    Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                        IconButton(onClick = { page = "Chat" }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back to thread") }
                        Text("Thread files", style = MaterialTheme.typography.titleLarge)
                    }
                    Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(20.dp), verticalArrangement = Arrangement.spacedBy(20.dp)) {
                        val current = selected
                        if (current != null) {
                            Text(client.threads.firstOrNull { it.first == current }?.second.orEmpty(), color = MaterialTheme.colorScheme.onSurfaceVariant)
                            val files = client.files.filter { it.thread == current }
                            if (files.isEmpty()) SettingsGroup("No files yet") {
                                Text("Attach a file to give Rem more context. Files returned by your desktop will appear here.", color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                            files.forEach { file -> SettingsGroup(file.name) {
                                Text("${file.state} • ${file.bytes}/${file.size} bytes", style = MaterialTheme.typography.bodySmall)
                                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                    if (file.direction == "upload" && file.state == "available") TextButton(onClick = { attachments = (attachments + file.id).distinct().take(8); page = "Chat" }) { Text("Attach to message") }
                                    if (file.direction == "download" && file.state == "offered") TextButton(onClick = { client.requestFile(file.id) }) { Text("Download") }
                                    if (file.direction == "download" && file.state == "available") TextButton(onClick = { exportId = file.id; exportLauncher.launch(file.name) }) { Text("Export file") }
                                    if (file.direction == "download" && file.state == "available") TextButton(onClick = { openFile(file.id, file.name) }) { Text("Open file") }
                                    if (file.state in setOf("available", "failed", "cancelled")) TextButton(onClick = { removeFile = file.id }) { Text("Remove local file") }
                                    else TextButton(onClick = { client.cancelFile(file.id) }) { Text("Cancel transfer") }
                                }
                            } }
                            Button(onClick = { importThread = current; importLauncher.launch(arrayOf("*/*")) }, modifier = Modifier.fillMaxWidth()) { Text("Send file, up to 10 MiB") }
                            Text(if (client.tailscaleEnabled) "Files transfer end-to-end encrypted over Wi-Fi or Tailscale." else "Files transfer end-to-end encrypted over Wi-Fi.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            SettingsGroup("Local history") {
                                Text("Manage completed messages saved on this phone.", style = MaterialTheme.typography.bodyMedium)
                                TextButton(onClick = { clearHistory = current }) { Text("Clear completed local history") }
                            }
                        }
                    }
                } else {
                    Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(20.dp), verticalArrangement = Arrangement.spacedBy(24.dp)) {
                        Text("Your desktop", style = MaterialTheme.typography.headlineMedium)
                        SettingsGroup("Connection") {
                            Text(if (client.connected) "Connected over ${client.route}" else "Ready to reconnect", style = MaterialTheme.typography.titleMedium)
                            Text(if (client.connected) "End-to-end encrypted" else "Your pairing and queued messages are saved.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                if (!client.connected) Button(onClick = client::connect) { Text("Connect") }
                                else OutlinedButton(onClick = client::disconnect) { Text("Disconnect") }
                                TextButton(onClick = client::refresh, enabled = client.connected) { Text("Refresh") }
                            }
                            WakeComputer(client, showLimits = true)
                        }
                        SettingsGroup("Wi-Fi connection") { LanRecovery(client) }
                        SettingsGroup("Tailscale") { TailscaleSettings(client) }
                        SettingsGroup("Voice assistant") { VoiceAssistantSettings(client) }
                        SettingsGroup("Background & notifications") {
                            Text("Keep receiving responses when you leave the app.", color = MaterialTheme.colorScheme.onSurfaceVariant)
                            TextButton(onClick = {
                                val intent = android.content.Intent(context, DeviceSyncService::class.java)
                                try {
                                    if (client.backgroundEnabled) context.stopService(intent) else androidx.core.content.ContextCompat.startForegroundService(context, intent)
                                } catch (_: Exception) { client.inform("Android could not start background sync. Keep the app open and try again later.") }
                            }) { Text(if (client.backgroundEnabled) "Stop background sync" else "Enable background sync") }
                            TaskProgressSettings(client.backgroundEnabled)
                            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                            LiveNotificationSettings()
                            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                            RemNotificationSettings(client.remAlerts, client.desktopControl, client.remAlertFilter)
                        }
                        SettingsGroup("Other desktops") {
                            TextButton(onClick = client::refreshFleet, enabled = client.connected) { Text("Refresh fleet status") }
                            val peers = runCatching { org.json.JSONObject(client.fleet).getJSONArray("devices") }.getOrNull()
                            if (peers == null || peers.length() == 0) Text("No desktop status to show.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            if (peers != null) for (i in 0 until peers.length()) {
                                val peer = peers.getJSONObject(i)
                                Text("${peer.optString("name")}: ${if (client.connected && peer.optLong("fresh_until") > now) peer.optString("status") else "stale / unknown"}")
                            }
                        }
                        TextButton(onClick = { reset = true }, colors = ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.error)) { Text("Forget pairing on this phone") }
                        Text("Experimental device sync. Independent security review is pending.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
            if (client.paired && page in topLevel && !typing) RaticodeBottomBar(Modifier.testTag("desktopNavigation")) {
                // Waiting approvals show on Approvals when it is on the bar, otherwise on More.
                val badged = if ("Approvals" in bar) "Approvals" else LiveNavigation.MORE
                LiveNavigation.tabs(bar).forEach { (target, label) ->
                    RaticodeBottomItem(selected = page == target, onClick = { if (page == target && target == "Browser") browserReveal++ else page = target }, label = label,
                        description = LiveNavigation.destination(target)?.title ?: target, icon = destinationIcon(target),
                        badge = client.approvals.size.takeIf { target == badged && it > 0 }?.toString())
                }
            }
        }
    }
    }
    // Opened from the launcher: offer to make Rem the phone's assistant (Android 12+; voice mode needs on-device speech).
    if (offerVoiceSetup && client.loaded && android.os.Build.VERSION.SDK_INT >= 31)
        VoiceSetupOffer(client.paired, onDone = consumeVoiceOffer, onOpenVoicePage = { page = "Voice" })
    client.requestError?.let { error -> AlertDialog(onDismissRequest = client::dismissRequestError, title = { Text("Desktop request") }, text = { Text(error) }, confirmButton = { TextButton(onClick = { client.dismissRequestError(); client.refresh() }) { Text("OK") } }) }
    if (modelPicker) AlertDialog(onDismissRequest = { modelPicker = false }, title = { Text("Thread settings") },
        text = { Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Scope", style = MaterialTheme.typography.titleSmall)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                FilterChip(projectChoice == null, { projectChoice = null }, label = { Text("Global") })
                client.projects.forEach { (id, name) -> FilterChip(projectChoice == id, { projectChoice = id }, label = { Text(name) }) }
            }
            if (projectChoice == null) Text("Rem will choose an open project for your request.", style = MaterialTheme.typography.bodySmall)
            Text("Provider and model", style = MaterialTheme.typography.titleSmall)
            Text("Uses this thread's desktop permissions and resources.", style = MaterialTheme.typography.bodySmall)
            if (client.providers.isEmpty()) Text("Connect and refresh to load desktop providers.")
            client.providers.forEach { (provider, availableModels) ->
                val currentModel = client.threadContexts[selected]?.takeIf { it.provider == provider }?.model
                val models = (availableModels + listOfNotNull(currentModel)).distinct()
                FilterChip(providerChoice == provider, { providerChoice = provider; modelChoice = models.firstOrNull().orEmpty(); effortChoice = ""; permissionChoice = client.defaultPermissions[provider].orEmpty() }, enabled = provider in selectableProviders, label = { Text(provider) })
                if (providerChoice == provider) models.forEach { model ->
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        RadioButton(modelChoice == model, { modelChoice = model; effortChoice = "" })
                        TextButton(onClick = { modelChoice = model; effortChoice = "" }) { Text(model) }
                    }
                }
            }
            if (client.permissionModes[providerChoice].orEmpty().isNotEmpty()) {
                Text("Permissions", style = MaterialTheme.typography.titleSmall)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    client.permissionModes[providerChoice].orEmpty().forEach { mode ->
                        FilterChip(permissionChoice == mode, { permissionChoice = mode }, label = { Text(when(mode) {
                            "danger-full-access" -> "Full access"; "workspace-write" -> "Workspace write"; "read-only" -> "Read only"; "cli-managed" -> "CLI managed"; "bypassPermissions" -> "Bypass permissions"; else -> mode
                        }) })
                    }
                }
            }
            Text("Effort", style = MaterialTheme.typography.titleSmall)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                FilterChip(effortChoice.isBlank(), { effortChoice = "" }, label = { Text("Default") })
                client.efforts[providerChoice]?.get(modelChoice).orEmpty().forEach { effort ->
                    FilterChip(effortChoice == effort, { effortChoice = effort }, label = { Text(effort) })
                }
            }
            val resources = client.threadResources[selected].orEmpty()
            if (resources.isNotEmpty()) {
                Text("Resources", style = MaterialTheme.typography.titleSmall)
                Text("Turn this thread's desktop tools, skills and MCP servers on or off.", style = MaterialTheme.typography.bodySmall)
                resources.forEach { resource ->
                    val label = when (resource.kind) { "skill" -> "Skill: ${resource.name}"; "mcp" -> "MCP: ${resource.name}"; else -> resource.name }
                    val on = resource.id in resourceChoice
                    Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).toggleable(on, role = androidx.compose.ui.semantics.Role.Checkbox) {
                        resourceChoice = if (it) resourceChoice + resource.id else resourceChoice - resource.id
                    }, verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(on, null)
                        Text(label, style = MaterialTheme.typography.bodyMedium)
                    }
                }
            }
        } }, confirmButton = { TextButton(enabled = selected != null && providerChoice in selectableProviders && (modelChoice in client.providers[providerChoice].orEmpty() || client.threadContexts[selected]?.let { it.provider == providerChoice && it.model == modelChoice } == true), onClick = {
            val enabledNow = client.threadResources[selected].orEmpty().filter { it.enabled }.map { it.id }.toSet()
            client.updateContext(selected!!, providerChoice, modelChoice, projectChoice, effortChoice, contextRevision, permissionChoice.takeIf { it.isNotBlank() },
                resources = resourceChoice.toList().takeIf { resourceChoice != enabledNow }); modelPicker = false
        }) { Text("Apply") } }, dismissButton = { TextButton(onClick = { modelPicker = false }) { Text("Cancel") } })
    clearHistory?.let { thread ->
        AlertDialog(onDismissRequest = { clearHistory = null }, title = { Text("Clear completed history on this phone?") },
            text = { Text("Removes completed messages for this thread from this phone. Queued and unresolved requests, drafts, desktop history and replay protection are retained.") },
            confirmButton = { TextButton(shape = MaterialTheme.shapes.small, onClick = { client.clearHistory(thread); clearHistory = null }) { Text("Clear completed history") } },
            dismissButton = { TextButton(shape = MaterialTheme.shapes.small, onClick = { clearHistory = null }) { Text("Keep history") } })
    }
    removeFile?.let { id ->
        AlertDialog(onDismissRequest = { removeFile = null }, title = { Text("Remove local file?") },
            text = { Text("Removes the encrypted copy on this phone. Exported copies and desktop files are unchanged. Files needed by pending messages cannot be removed.") },
            confirmButton = { TextButton(shape = MaterialTheme.shapes.small, onClick = { client.removeFile(id); attachments = attachments - id; removeFile = null }) { Text("Remove local copy") } },
            dismissButton = { TextButton(shape = MaterialTheme.shapes.small, onClick = { removeFile = null }) { Text("Keep file") } })
    }
    client.preview?.let { invitation ->
        AlertDialog(onDismissRequest = client::clearPreview, title = { Text("Pair with ${invitation.name}?") },
            text = { Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text("Compare this desktop fingerprint with the desktop's invitation:\n${invitation.pin.chunked(8).joinToString(" ")}\n\nThe desktop must also confirm your phone. This grants communication only.")
                val endpoint = invitation.json.getJSONObject("lan_endpoint")
                val tailnet = dev.raticode.mobile.live.DeviceRoutes.endpoint(endpoint)?.path == dev.raticode.mobile.live.DevicePath.TAILSCALE
                Text(if (!tailnet) "Connects over Wi-Fi to ${endpoint.getString("host")}, port ${endpoint.getInt("port")}. This phone must use the same Wi-Fi network as the desktop."
                    else if (client.tailscaleEnabled) "Connects through Tailscale to ${endpoint.getString("host")}, port ${endpoint.getInt("port")}. Tailscale must be on for this phone and the desktop."
                    else "This desktop invitation uses a Tailscale address (${endpoint.getString("host")}). Turn on Connect through Tailscale before pairing.")
            } },
            confirmButton = { TextButton(shape = MaterialTheme.shapes.small, onClick = client::pair) { Text("Pair") } },
            dismissButton = { TextButton(shape = MaterialTheme.shapes.small, onClick = client::clearPreview) { Text("Cancel") } })
    }
    if (reset) AlertDialog(onDismissRequest = { reset = false }, title = { Text("Forget this desktop?") }, text = { Text("Deletes local pairing keys, queued live messages and live history. Unpair this phone on desktop too. Use Revoke there if you want to block its old identity.") },
        confirmButton = { TextButton(shape = MaterialTheme.shapes.small, onClick = { reset = false; selected = null; draft = ""; draftEdited = false; attachments = emptyList(); exportId = null; importThread = null; client.reset() }) { Text("Forget") } }, dismissButton = { TextButton(shape = MaterialTheme.shapes.small, onClick = { reset = false }) { Text("Keep") } })
}

@androidx.annotation.RequiresApi(29)
@Composable
private fun LanRecovery(client: LiveClient) {
    var editing by rememberSaveable { mutableStateOf(false) }
    var host by rememberSaveable { mutableStateOf("") }
    var port by rememberSaveable { mutableStateOf("") }
    Text(if (client.lanAvailable) "Desktop address: ${client.lanHost}, port ${client.lanPort}"
        else "No desktop address was saved. Find it in Paired devices on your computer.",
        style = MaterialTheme.typography.bodySmall)
    Text(if (client.tailscaleEnabled) "On the same Wi-Fi, Raticode connects directly. Elsewhere it uses only your Tailscale network, never plain mobile data or an internet relay."
        else "Raticode connects only over Wi-Fi. It never uses mobile data or an internet relay.",
        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    TextButton(onClick = { host = client.lanHost; port = client.lanPort; editing = true }) { Text("Set desktop address") }
    if (editing) {
        val valid = runCatching {
            dev.raticode.mobile.live.DeviceInvitation.validateLanEndpoint(
                org.json.JSONObject().put("host", host.trim()).put("port", port.trim().toInt()))
        }.isSuccess
        AlertDialog(onDismissRequest = { editing = false }, title = { Text("Desktop Wi-Fi address") },
            text = {
                Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text("Copy the IP address and port from desktop Paired devices. Both devices must use the same Wi-Fi network. The saved desktop identity is still verified.")
                    OutlinedTextField(host, { host = it }, label = { Text("Desktop IP address") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                    OutlinedTextField(port, { port = it }, label = { Text("Port") }, singleLine = true, modifier = Modifier.fillMaxWidth(),
                        keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = androidx.compose.ui.text.input.KeyboardType.Number))
                    if (host.isNotBlank() && port.isNotBlank() && !valid) Text("Use a private IP address and a port from 1 to 65535.", style = MaterialTheme.typography.bodySmall)
                }
            },
            confirmButton = { TextButton(enabled = valid, onClick = { editing = false; client.useLanAddress(host, port) }) { Text("Connect") } },
            dismissButton = { TextButton(onClick = { editing = false }) { Text("Cancel") } })
    }
}

/** Opt-in route through the user's own Tailscale network. Sign-in happens in the Tailscale app, not here. */
@androidx.annotation.RequiresApi(29)
@Composable
private fun TailscaleSettings(client: LiveClient) {
    var editing by rememberSaveable { mutableStateOf(false) }
    var host by rememberSaveable { mutableStateOf("") }
    var port by rememberSaveable { mutableStateOf("") }
    Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).testTag("tailscaleToggle")
        .toggleable(client.tailscaleEnabled, role = androidx.compose.ui.semantics.Role.Switch) { client.setTailscale(it) },
        verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text("Connect through Tailscale", style = MaterialTheme.typography.bodyLarge)
            Text("Reach your desktop on mobile data or other Wi-Fi through your own Tailscale network. Off by default.",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Switch(client.tailscaleEnabled, null)
    }
    if (!client.tailscaleEnabled) return
    Text(if (client.tailscaleHost.isBlank()) "No Tailscale address saved. Enter the desktop's Tailscale IP address and the device port from desktop Paired devices."
        else "Desktop Tailscale address: ${client.tailscaleHost}, port ${client.tailscalePort}" + if (client.tailscaleChosen) "" else " (reported by desktop)",
        style = MaterialTheme.typography.bodySmall)
    Text("Install the Tailscale app on this phone and sign in to the same Tailscale account as your desktop. Raticode still verifies the paired desktop identity on every connection. While Tailscale is off, Raticode does not use mobile data.",
        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        TextButton(onClick = { host = client.tailscaleHost; port = client.tailscalePort.ifBlank { client.lanPort }; editing = true }) { Text("Set Tailscale address") }
        if (client.tailscaleChosen) TextButton(onClick = client::clearTailscaleAddress) { Text("Remove saved address") }
    }
    if (editing) {
        val valid = runCatching {
            dev.raticode.mobile.live.DeviceInvitation.validateTailnetEndpoint(
                org.json.JSONObject().put("host", host.trim()).put("port", port.trim().toInt()))
        }.isSuccess
        AlertDialog(onDismissRequest = { editing = false }, title = { Text("Desktop Tailscale address") },
            text = {
                Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text("On the desktop, run tailscale ip or open the Tailscale admin console's Machines page. Use the same port as the Wi-Fi address. The saved desktop identity is still verified.")
                    OutlinedTextField(host, { host = it }, label = { Text("Desktop Tailscale IP address") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                    OutlinedTextField(port, { port = it }, label = { Text("Port") }, singleLine = true, modifier = Modifier.fillMaxWidth(),
                        keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = androidx.compose.ui.text.input.KeyboardType.Number))
                    if (host.isNotBlank() && port.isNotBlank() && !valid) Text("Use a Tailscale IP address (100.x.y.z or fd7a:115c:a1e0:…) and a port from 1 to 65535. Names are not accepted.", style = MaterialTheme.typography.bodySmall)
                }
            },
            confirmButton = { TextButton(enabled = valid, onClick = { editing = false; client.useTailscaleAddress(host, port) }) { Text("Save") } },
            dismissButton = { TextButton(onClick = { editing = false }) { Text("Cancel") } })
    }
}
