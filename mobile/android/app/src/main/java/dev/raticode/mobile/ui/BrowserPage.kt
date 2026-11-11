package dev.raticode.mobile.ui

import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.webkit.WebChromeClient
import android.widget.FrameLayout
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.annotation.RequiresApi
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import dev.raticode.mobile.browser.BrowserApps
import dev.raticode.mobile.browser.BrowserControlsMotion
import dev.raticode.mobile.browser.BrowserMedia
import dev.raticode.mobile.browser.BrowserMediaRules
import dev.raticode.mobile.browser.BrowserRules
import dev.raticode.mobile.browser.BrowserRuntime
import dev.raticode.mobile.browser.BrowserTabs
import dev.raticode.mobile.browser.GeckoBrowser
import dev.raticode.mobile.live.BrowserAddress
import dev.raticode.mobile.live.DesktopBrowser
import dev.raticode.mobile.live.LiveClient
import kotlinx.coroutines.launch
import org.json.JSONObject

/**
 * Raticode (chromium), the phone's built-in WebView browser: one compact row at the top (see [BrowserToolbar]) that folds to a strip while the
 * user reads. Pages run in Android's WebView on this phone's own internet connection.
 * The paired computer adds its open browser tabs to the tab switcher and takes pages sent to it; Rem gets a page only
 * when the user asks, as a draft in Rem's message box.
 */
@RequiresApi(29)
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
internal fun BrowserPage(client: LiveClient, onBack: (() -> Unit)?, revealControls: Int = 0, onAskRem: (String) -> Unit) {
    val context = LocalContext.current
    val browser = remember { BrowserRuntime.get(context) }
    // The default browser, unless the user picked a tab of the other built-in browser in the shared tab list.
    val engine = BrowserRules.pageEngine(browser.saved)
    // With Firefox chosen, the page hands pages and Firefox's own screens to Firefox for Android.
    val app = BrowserApps.byEngine(engine)
    if (engine == BrowserRules.ENGINE_GECKO) GeckoPage(client, browser, onBack, revealControls, onAskRem)
    else if (engine == BrowserRules.ENGINE_FIREFOX) FirefoxPage(client, browser, onBack)
    // With Edge chosen, Microsoft Edge for Android takes pages the same way.
    else if (engine == BrowserRules.ENGINE_EDGE) EdgePage(client, browser, onBack)
    // With Chrome (one of BrowserApps) chosen, Chrome for Android takes pages the same way.
    else if (app != null) BrowserAppPage(client, browser, app, onBack)
    else RaticodeBrowser(client, browser, onBack, revealControls, onAskRem)
}

@RequiresApi(29)
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
private fun RaticodeBrowser(client: LiveClient, browser: BrowserRuntime, onBack: (() -> Unit)?, revealControls: Int, onAskRem: (String) -> Unit) {
    val context = LocalContext.current
    // Raticode (gecko)'s tabs share the tab list; its pages, and anything they play, run on while this browser shows.
    val gecko = remember { GeckoBrowser.get(context) }
    val page = browser.page
    val activeId = browser.active
    val focus = LocalFocusManager.current
    var editing by remember { mutableStateOf(false) }
    var address by remember { mutableStateOf(TextFieldValue("")) }
    var sheet by remember { mutableStateOf<String?>(null) }
    var historyOf by remember { mutableStateOf<Int?>(null) }
    val snackbar = remember { SnackbarHostState() }

    // Pages run while Raticode is in front, including on Raticode's other pages, so music in a tab keeps playing while the
    // user is in Rem or Threads. When Raticode leaves the screen (here, and in MainActivity.onStop) they stop, except a
    // page playing sound, which goes on with a notification.
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    DisposableEffect(lifecycle) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_START) browser.visible(true)
            if (event == Lifecycle.Event.ON_STOP) browser.appInFront(false)
        }
        lifecycle.addObserver(observer)
        if (lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)) browser.visible(true)
        onDispose { lifecycle.removeObserver(observer); browser.exitFullscreen() }
    }
    // One collector for the page's life: clearing the notice must not cancel the snackbar it started.
    val noticeScope = rememberCoroutineScope()
    LaunchedEffect(browser) {
        snapshotFlow { browser.notice }.collect { text ->
            if (text == null) return@collect
            browser.notice = null
            noticeScope.launch { snackbar.currentSnackbarData?.dismiss(); snackbar.showSnackbar(text, withDismissAction = true) }
        }
    }
    // The computer's tabs are read only while the switcher or a blank tab shows them.
    val showsComputer = client.paired && (sheet == "tabs" || (page.url == BrowserRules.HOME && page.problem == null && !editing))
    DisposableEffect(showsComputer, client.desktopBrowser) {
        if (showsComputer && client.desktopBrowser == true) client.openDesktop(DesktopBrowser.TABS)
        onDispose { if (showsComputer && client.desktopBrowser == true) client.openDesktop(null) }
    }
    val computer = client.desktopViews[DesktopBrowser.TABS] as? DesktopBrowser.View
    LaunchedEffect(computer?.searchUrl) { computer?.searchUrl?.let(browser::computerSearch) }
    val sent = client.desktopViews[DesktopBrowser.OPEN] as? DesktopBrowser.Sent
    LaunchedEffect(sent) { if (sent != null && browser.lastSent !== sent) { browser.lastSent = sent; snackbar.showSnackbar(sent.message, withDismissAction = true) } }

    fun startEditing() {
        val text = if (page.url == BrowserRules.HOME) "" else page.url
        address = TextFieldValue(text, TextRange(0, text.length))
        editing = true
    }
    fun submit(text: String) { editing = false; focus.clearFocus(); browser.go(text) }
    fun askRem(selection: String) {
        val title = BrowserRules.title(page.title, page.url)
        val quote = selection.takeIf { it.isNotBlank() }?.let { "\n\n> " + it.replace("\n", "\n> ") }.orEmpty()
        onAskRem(("About this page: $title\n${page.url}$quote\n\n").take(4000))
    }

    BackHandler(enabled = editing || browser.find != null || page.canBack || page.problem?.let { page.canBack } == true) {
        when {
            editing -> { editing = false; focus.clearFocus() }
            browser.find != null -> browser.closeFind()
            else -> browser.back()
        }
    }

    val controls = remember { BrowserControlsState() }
    DisposableEffect(browser) {
        browser.scrollListener = controls::scrolledTo
        onDispose { browser.scrollListener = null }
    }
    val controlsOpen = browserControlsOpen(controls, revealRequests = revealControls, tab = activeId, loading = page.loading,
        held = editing || browser.find != null || sheet != null || historyOf != null || browser.menu != null || browser.ask != null || page.problem != null || page.url == BrowserRules.HOME)
    val forwardFits = BrowserControlsMotion.forwardFits(LocalConfiguration.current.screenWidthDp, onBack != null)

    Box(Modifier.fillMaxSize()) {
    Column(Modifier.fillMaxSize()) {
        BrowserToolbar(controls, controlsOpen, "browser", page.url, page.desktop, page.loading, page.progress, page.canBack, page.canForward,
            canReload = page.url != BrowserRules.HOME || page.problem != null, tabCount = allTabs(browser, gecko, BrowserTabs.CHROMIUM).size, showForward = forwardFits, onPageBack = onBack, onEdit = ::startEditing,
            onBack = { browser.back() }, onForward = { browser.forward() }, onStop = { browser.stop() }, onReload = { browser.reload() }, onTabs = { sheet = "tabs" }, onMenu = { sheet = "menu" },
            onBackLong = { historyOf = -1 }, onForwardLong = { historyOf = 1 }, privateTab = browser.activeIsPrivate, editor = if (!editing) null else ({
                val requester = remember { FocusRequester() }
                // Compose reports "unfocused" once before the field takes focus; only a real blur closes it.
                var focused by remember { mutableStateOf(false) }
                LaunchedEffect(Unit) { requester.requestFocus() }
                OutlinedTextField(address, { address = it }, singleLine = true, placeholder = { Text("Search or type an address") },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Go, autoCorrectEnabled = false),
                    keyboardActions = KeyboardActions(onGo = { submit(address.text) }),
                    trailingIcon = { IconButton(onClick = { if (address.text.isEmpty()) { editing = false; focus.clearFocus() } else address = TextFieldValue("") }) {
                        Icon(Icons.Filled.Close, if (address.text.isEmpty()) "Cancel" else "Clear address") } },
                    modifier = Modifier.weight(1f).padding(vertical = 4.dp).focusRequester(requester).testTag("browserAddressField")
                        .onFocusChanged { if (it.isFocused) focused = true else if (focused && editing && address.text.isEmpty()) editing = false })
            }))
        browser.find?.let { find -> FindBar(find, browser) }

        Box(Modifier.weight(1f).fillMaxWidth().testTag("browserContent")) {
            val problem = page.problem
            when {
                editing -> Suggestions(address.text, browser, onPick = { submit(it) })
                problem != null -> ProblemPanel(problem, page.canBack, browser, context)
                page.url == BrowserRules.HOME -> StartPage(browser, client, computer, onEdit = ::startEditing, onAllTabs = { sheet = "tabs" })
                activeId != null -> {
                    val activity = context as? Activity
                    ShowingMediaOf(BrowserMediaRules.Key(BrowserMediaRules.Engine.CHROMIUM, activeId))
                    AndroidView(factory = { FrameLayout(it) }, modifier = Modifier.fillMaxSize().testTag("browserPage"),
                        update = { container ->
                            // Reading the address here redraws when a blank tab starts loading.
                            if (page.url != BrowserRules.HOME) browser.view(activeId)?.let { browser.attach(container, it, activity) }
                                ?: browser.detach(container)
                        }, onRelease = { browser.detach(it) })
                }
            }
        }
    }
    SnackbarHost(snackbar, Modifier.align(Alignment.BottomCenter).padding(bottom = 8.dp))
    }

    when (sheet) {
        "tabs" -> AllTabsSheet(browser, gecko, client, computer, BrowserTabs.CHROMIUM, onDismiss = { sheet = null })
        "menu" -> MenuSheet(browser, client, page, showForward = !forwardFits && page.canForward, onDismiss = { sheet = null }, onSheet = { sheet = it },
            onAskRem = { browser.selection { askRem(it) }; sheet = null }, context = context)
        "history" -> HistorySheet(browser, onDismiss = { sheet = null })
        "bookmarks" -> BookmarksSheet(browser, onDismiss = { sheet = null })
        "settings" -> BrowserSettingsSheet(browser, client, onDismiss = { sheet = null })
        "site" -> SiteSheet(browser, page, onDismiss = { sheet = null })
    }
    historyOf?.let { direction -> BackForwardSheet(browser, direction) { historyOf = null } }
    browser.menu?.let { menu -> LinkMenuSheet(menu, browser, client, context) { browser.menu = null } }
    browser.ask?.let { ask -> AskDialog(ask, browser, context) }
    browser.fullscreen?.let { full ->
        Dialog(onDismissRequest = { browser.exitFullscreen() }, properties = DialogProperties(usePlatformDefaultWidth = false, decorFitsSystemWindows = false)) {
            Box(Modifier.fillMaxSize()) {
                AndroidView(factory = { FrameLayout(it).apply { setBackgroundColor(android.graphics.Color.BLACK); (full.view.parent as? android.view.ViewGroup)?.removeView(full.view); addView(full.view) } },
                    modifier = Modifier.fillMaxSize().semantics { contentDescription = "Full-screen video. Back to exit" })
                ChildLockControl(Modifier.align(Alignment.CenterEnd))
            }
        }
    }
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        browser.chooserResult(WebChromeClient.FileChooserParams.parseResult(result.resultCode, result.data))
    }
    browser.chooser?.let { chooser ->
        LaunchedEffect(chooser) {
            try { picker.launch(chooser.intent) } catch (_: ActivityNotFoundException) { browser.chooserResult(null); browser.notice = "No app on this phone can pick files." }
        }
    }
}

@Composable
private fun FindBar(find: BrowserRuntime.Find, browser: BrowserRuntime) {
    val requester = remember { FocusRequester() }
    LaunchedEffect(Unit) { requester.requestFocus() }
    Surface(color = MaterialTheme.colorScheme.surfaceVariant) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(find.query, browser::findText, singleLine = true, placeholder = { Text("Find in page") },
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search), keyboardActions = KeyboardActions(onSearch = { browser.findNext(true) }),
                modifier = Modifier.weight(1f).padding(vertical = 4.dp).focusRequester(requester).testTag("browserFind"))
            Text(if (find.query.isEmpty()) "" else "${find.current}/${find.total}", Modifier.padding(horizontal = 6.dp).semantics {
                contentDescription = if (find.total == 0) "No matches" else "Match ${find.current} of ${find.total}" }, style = MaterialTheme.typography.labelMedium)
            IconButton(onClick = { browser.findNext(false) }, enabled = find.total > 0) { Icon(Icons.Filled.KeyboardArrowUp, "Previous match") }
            IconButton(onClick = { browser.findNext(true) }, enabled = find.total > 0) { Icon(Icons.Filled.KeyboardArrowDown, "Next match") }
            IconButton(onClick = browser::closeFind) { Icon(Icons.Filled.Close, "Close find") }
        }
    }
}

@Composable
private fun Suggestions(input: String, browser: BrowserRuntime, onPick: (String) -> Unit) {
    val saved = browser.saved
    val list = remember(input, saved) {
        BrowserRules.suggest(input, saved.bookmarks, browser.tabs.filterNot { it.private }.map { it.url to it.title }.filter { it.first != BrowserRules.HOME }, saved.history)
    }
    LazyColumn(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background).testTag("browserSuggestions")) {
        if (input.isNotBlank()) item {
            val target = BrowserRules.resolve(input, BrowserRules.searchTemplate(saved))
            val label = when (target) {
                is BrowserRules.Target.Load -> if (target.url.startsWith("https://${input.trim()}")) "Go to ${input.trim()}" else "Search for “${input.trim()}”"
                is BrowserRules.Target.Insecure -> "Go to ${input.trim()} (not secure)"
                is BrowserRules.Target.App -> "Open in another app"
                else -> "Go"
            }
            PageRow(label, null, null) { onPick(input) }
        }
        items(list, key = { it.kind + it.url }) { s -> PageRow(BrowserRules.title(s.title, s.url), s.url, s.kind) { onPick(s.url) } }
    }
}

/** One page in a list: title, address and an optional label, at least 56 dp tall. */
@OptIn(ExperimentalFoundationApi::class)
@Composable
internal fun PageRow(title: String, url: String?, label: String?, trailing: (@Composable () -> Unit)? = null, onLong: (() -> Unit)? = null, onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().heightIn(min = 56.dp).combinedClickable(onClick = onClick, onLongClick = onLong).padding(horizontal = 16.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, maxLines = 1, overflow = TextOverflow.Ellipsis)
            val second = listOfNotNull(label, url?.let { BrowserRules.host(it).ifBlank { it } }).joinToString(" · ")
            if (second.isNotEmpty()) Text(second, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        trailing?.invoke()
    }
}

@Composable
private fun ProblemPanel(problem: BrowserRuntime.Problem, canBack: Boolean, browser: BrowserRuntime, context: Context) {
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(24.dp).testTag("browserProblem"), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Icon(Icons.Filled.Warning, null, Modifier.size(32.dp), tint = MaterialTheme.colorScheme.error)
        Text(problem.title, style = MaterialTheme.typography.headlineSmall, modifier = Modifier.semantics { heading() })
        Text(problem.detail, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(problem.url, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 3, overflow = TextOverflow.Ellipsis)
        val kind = problem.kind
        if (kind == BrowserRuntime.Problem.Kind.INSECURE) BrowserRules.secureVersion(problem.url)?.let { secure ->
            Button(onClick = { browser.go(secure) }, Modifier.fillMaxWidth()) { Text("Try the secure address") }
        } else if (kind != BrowserRuntime.Problem.Kind.CERTIFICATE) Button(onClick = browser::reload, Modifier.fillMaxWidth()) {
            Text(if (kind == BrowserRuntime.Problem.Kind.CRASHED) "Reload" else "Try again")
        }
        if (canBack || kind == BrowserRuntime.Problem.Kind.CERTIFICATE || kind == BrowserRuntime.Problem.Kind.INSECURE)
            OutlinedButton(onClick = { if (canBack) browser.back() else browser.go(BrowserRules.HOME) }, Modifier.fillMaxWidth()) {
                Text(if (kind == BrowserRuntime.Problem.Kind.CERTIFICATE) "Back to safety" else "Go back")
            }
        if (kind != BrowserRuntime.Problem.Kind.CERTIFICATE) TextButton(onClick = { if (!openInBrowser(context, problem.url)) browser.notice = "This phone has no other browser." }) {
            Text("Open in your phone's browser")
        }
    }
}

@RequiresApi(29)
@Composable
private fun StartPage(browser: BrowserRuntime, client: LiveClient, computer: DesktopBrowser.View?, onEdit: () -> Unit, onAllTabs: () -> Unit) {
    val saved = browser.saved
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(vertical = 12.dp).testTag("browserStart"), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        if (browser.activeIsPrivate) PrivateNote("")
        Surface(onClick = onEdit, shape = RoundedCornerShape(28.dp), color = MaterialTheme.colorScheme.surfaceVariant,
            modifier = Modifier.padding(horizontal = 16.dp).fillMaxWidth().heightIn(min = 56.dp).semantics { contentDescription = "Search or type an address" }) {
            Row(Modifier.padding(horizontal = 18.dp), verticalAlignment = Alignment.CenterVertically) {
                Icon(ThreadIcons.Search, null, Modifier.size(20.dp)); Spacer(Modifier.width(12.dp))
                Text("Search or type an address", color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        if (client.paired) {
            SectionTitle(computerTitle(computer))
            ComputerTabs(browser, client, computer, limit = 4)
            if ((computer?.tabs?.size ?: 0) > 4) TextButton(onClick = onAllTabs, Modifier.padding(horizontal = 8.dp)) { Text("All ${computer!!.tabs.size} computer tabs") }
        }
        if (saved.bookmarks.isNotEmpty()) {
            SectionTitle("Bookmarks")
            saved.bookmarks.take(8).forEach { b -> PageRow(BrowserRules.title(b.title, b.url), b.url, null) { browser.go(b.url) } }
        }
        if (saved.history.isNotEmpty()) {
            SectionTitle("Recent")
            saved.history.distinctBy { it.url }.take(6).forEach { v -> PageRow(BrowserRules.title(v.title, v.url), v.url, null) { browser.go(v.url) } }
        }
        Text("${BrowserTabs.CHROMIUM_NAME}: pages load over this phone's own internet connection, not through your computer. Only secure (https) pages open here.",
            Modifier.padding(horizontal = 16.dp, vertical = 8.dp), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
internal fun SectionTitle(text: String) = Text(text, Modifier.padding(start = 16.dp, top = 12.dp, end = 16.dp).semantics { heading() },
    style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary)

/**
 * The computer's tabs run in Raticode's own engine or, when chosen there, in another browser it runs: Firefox, Microsoft Edge
 * or Chrome. A computer that names each tab's browser can mix them, and each tab's badge says which.
 */
internal fun computerTitle(computer: DesktopBrowser.View?) =
    if (computer?.perTabEngines == true) "On your computer" else DesktopBrowser.NAMES[computer?.engine]?.let { "$it on your computer" } ?: "On your computer"

/** The computer's browser tabs, or why they can't show. A tap opens the page in a new tab on this phone. */
@RequiresApi(29)
@Composable
internal fun ComputerTabs(browser: BrowserRuntime, client: LiveClient, computer: DesktopBrowser.View?, limit: Int = 40, onOpened: () -> Unit = {},
                          open: (String) -> Unit = { browser.newTab(it) }) {
    val reason = when {
        !client.desktopControl -> "Turn on Use Raticode from this phone in Paired devices on your computer to see its tabs here."
        client.desktopBrowser == false -> DesktopBrowser.UPDATE_DESKTOP
        computer == null && !client.connected -> "Not connected to your computer."
        computer == null -> "Checking your computer…"
        !computer.available -> computer.detail.ifBlank { "Open Raticode on your computer to see its tabs." }
        computer.tabs.isEmpty() -> "No web pages are open in Raticode on your computer."
        else -> null
    }
    if (reason != null) { Text(reason, Modifier.padding(horizontal = 16.dp), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant); return }
    if (computer!!.stale || !client.connected) Text(if (!client.connected) "Not connected; this list may be out of date." else "Raticode on your computer stopped updating this list.",
        Modifier.padding(horizontal = 16.dp), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    computer.tabs.take(limit).forEach { tab ->
        // Each tab names the browser it runs in on the computer; an older computer's tabs all take its one browser.
        val label = listOfNotNull(DesktopBrowser.engineName(tab), if (tab.rem) "${tab.label.ifBlank { "Rem" }}'s tab" else if (tab.active) "Showing on computer" else null).joinToString(" · ")
        PageRow(BrowserRules.title(tab.title, tab.url), tab.url, label,
            trailing = { Icon(ThreadIcons.Computer, null, Modifier.size(18.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant) }) {
            open(tab.url); onOpened()
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@RequiresApi(29)
@Composable
private fun MenuSheet(browser: BrowserRuntime, client: LiveClient, page: BrowserRuntime.Page, showForward: Boolean, onDismiss: () -> Unit, onSheet: (String) -> Unit,
                      onAskRem: () -> Unit, context: Context) {
    val web = page.url != BrowserRules.HOME && page.problem?.kind != BrowserRuntime.Problem.Kind.INSECURE
    val bookmarked = browser.saved.bookmarks.any { it.url == page.url }
    // The computer takes addresses up to the link's limit; longer ones, such as sign-in hand-offs, stay on the phone.
    val sendable = BrowserAddress.isWeb(page.url)
    val canSend = client.paired && client.desktopControl && client.connected && client.desktopBrowser == true && sendable
    val sending = DesktopBrowser.OPEN in client.desktopBusy
    ModalBottomSheet(onDismissRequest = onDismiss, modifier = Modifier.testTag("browserMenuSheet")) {
        Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(bottom = 16.dp)) {
            if (showForward) MenuRow("Forward", icon = ThreadIcons.PageForward) { browser.forward(); onDismiss() }
            MenuRow("New tab") { browser.newTab(); onDismiss() }
            MenuRow("New private tab", detail = "Not saved. Its cookies and data go when you close your last private tab.") { browser.newPrivateTab(); onDismiss() }
            if (web) MenuRow("Site information", icon = ThreadIcons.Lock) { onSheet("site") }
            if (web) MenuRow(if (bookmarked) "Remove bookmark" else "Bookmark this page", icon = ThreadIcons.Star) { browser.toggleBookmark(); onDismiss() }
            if (web && client.paired && !browser.activeIsPrivate) MenuRow("Ask Rem about this page", icon = ThreadIcons.Rem,
                detail = "Adds the address, and any text you selected, to Rem's message box. Nothing sends until you do.", onClick = onAskRem)
            if (web && client.paired && !browser.activeIsPrivate) MenuRow(if (sending) "Sending to your computer…" else "Open on your computer", icon = ThreadIcons.Computer, enabled = canSend && !sending,
                detail = when {
                    !client.desktopControl -> "Turn on Use Raticode from this phone in Paired devices on your computer."
                    client.desktopBrowser == false -> DesktopBrowser.UPDATE_DESKTOP
                    !client.connected -> "Connect to your computer first."
                    !sendable -> "This page's address is too long to send to your computer."
                    else -> "Opens in a new tab in Raticode's browser there, without switching to it."
                }) { client.desktopAction(DesktopBrowser.OPEN, JSONObject().put("url", page.url)); onDismiss() }
            if (web) MenuRow("Find in page") { browser.openFind(); onDismiss() }
            if (web) MenuRow("Share") {
                val send = Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, page.url).putExtra(Intent.EXTRA_SUBJECT, page.title)
                try { context.startActivity(Intent.createChooser(send, "Share page")) } catch (_: ActivityNotFoundException) {}
                onDismiss()
            }
            if (web) MenuRow("Copy address") { copy(context, page.url); browser.notice = "Address copied."; onDismiss() }
            if (web) MenuRow("Print or save as PDF") { browser.print(context); onDismiss() }
            if (web) MenuRow("Open in your phone's browser") { if (!openInBrowser(context, page.url)) browser.notice = "This phone has no other browser."; onDismiss() }
            if (web) Row(Modifier.fillMaxWidth().heightIn(min = 56.dp).padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                Text("Desktop site", Modifier.weight(1f))
                Switch(page.desktop, { browser.toggleDesktopSite() }, Modifier.semantics { contentDescription = "Desktop site" })
            }
            Row(Modifier.fillMaxWidth().heightIn(min = 56.dp).padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                Text("Text size", Modifier.weight(1f))
                OutlinedButton(onClick = { browser.textZoom(-10) }, enabled = browser.saved.textZoom > BrowserRules.MIN_TEXT) { Text("A−") }
                TextButton(onClick = { browser.textZoom(0) }) { Text("${browser.saved.textZoom}%") }
                OutlinedButton(onClick = { browser.textZoom(10) }, enabled = browser.saved.textZoom < BrowserRules.MAX_TEXT) { Text("A+") }
            }
            HorizontalDivider()
            MenuRow("Bookmarks") { onSheet("bookmarks") }
            MenuRow("History") { onSheet("history") }
            if (browser.saved.closed.isNotEmpty()) MenuRow("Reopen closed tab") { browser.reopenClosed(); onDismiss() }
            MenuRow("Browser settings and privacy") { onSheet("settings") }
        }
    }
}

@Composable
internal fun MenuRow(label: String, icon: ImageVector? = null, detail: String? = null, enabled: Boolean = true, onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().heightIn(min = 52.dp).let { if (enabled) it.combinedClickableRow(onClick) else it }.padding(horizontal = 16.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically) {
        if (icon != null) { Icon(icon, null, Modifier.size(20.dp)); Spacer(Modifier.width(16.dp)) }
        Column(Modifier.weight(1f)) {
            Text(label, color = if (enabled) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurface.copy(alpha = 0.5f))
            if (detail != null) Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
internal fun Modifier.combinedClickableRow(onClick: () -> Unit) = this.combinedClickable(role = Role.Button, onClick = onClick)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun HistorySheet(browser: BrowserRuntime, onDismiss: () -> Unit) {
    var clearing by remember { mutableStateOf(false) }
    ModalBottomSheet(onDismissRequest = onDismiss) {
        LazyColumn(Modifier.fillMaxWidth().padding(bottom = 16.dp)) {
            item {
                Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text("History", Modifier.weight(1f).semantics { heading() }, style = MaterialTheme.typography.titleMedium)
                    TextButton(onClick = { clearing = true }, enabled = browser.saved.history.isNotEmpty()) { Text("Clear…") }
                }
                if (!browser.saved.keepHistory) Text("Saving history is off in Browser settings.", Modifier.padding(horizontal = 16.dp), style = MaterialTheme.typography.bodySmall)
                else if (browser.saved.history.isEmpty()) Text("Pages you visit appear here. History stays on this phone.", Modifier.padding(horizontal = 16.dp), style = MaterialTheme.typography.bodySmall)
            }
            items(browser.saved.history.take(300), key = { it.at.toString() + it.url }) { v ->
                PageRow(BrowserRules.title(v.title, v.url), v.url, android.text.format.DateUtils.getRelativeTimeSpanString(v.at).toString(), trailing = {
                    IconButton(onClick = { browser.removeVisit(v) }) { Icon(Icons.Filled.Close, "Remove from history") }
                }) { browser.go(v.url); onDismiss() }
            }
        }
    }
    if (clearing) ClearDialog(browser) { clearing = false }
}

@Composable
private fun ClearDialog(browser: BrowserRuntime, onDone: () -> Unit) {
    var range by remember { mutableIntStateOf(BrowserRules.CLEAR_RANGES.lastIndex) }
    var history by remember { mutableStateOf(true) }
    var siteData by remember { mutableStateOf(false) }
    var cache by remember { mutableStateOf(false) }
    AlertDialog(onDismissRequest = onDone, title = { Text("Clear browsing data") }, text = {
        Column(Modifier.verticalScroll(rememberScrollState())) {
            Text("History from", style = MaterialTheme.typography.titleSmall)
            BrowserRules.CLEAR_RANGES.forEachIndexed { index, (label, _) ->
                Row(Modifier.fillMaxWidth().heightIn(min = 44.dp).combinedClickableRow { range = index }, verticalAlignment = Alignment.CenterVertically) {
                    RadioButton(range == index, { range = index }); Text(label)
                }
            }
            CheckRow("Browsing history", history) { history = it }
            CheckRow("Cookies and site data (all time; signs you out of sites)", siteData) { siteData = it }
            CheckRow("Cached files", cache) { cache = it }
        }
    }, confirmButton = {
        TextButton(onClick = { browser.clear(BrowserRules.CLEAR_RANGES[range].second, history, siteData, cache); onDone() },
            enabled = history || siteData || cache) { Text("Clear") }
    }, dismissButton = { TextButton(onClick = onDone) { Text("Cancel") } })
}

@Composable
private fun CheckRow(label: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth().heightIn(min = 44.dp).combinedClickableRow { onChange(!checked) }, verticalAlignment = Alignment.CenterVertically) {
        Checkbox(checked, onChange); Text(label)
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun BookmarksSheet(browser: BrowserRuntime, onDismiss: () -> Unit) {
    ModalBottomSheet(onDismissRequest = onDismiss) {
        LazyColumn(Modifier.fillMaxWidth().padding(bottom = 16.dp)) {
            item {
                Text("Bookmarks", Modifier.padding(horizontal = 16.dp).semantics { heading() }, style = MaterialTheme.typography.titleMedium)
                if (browser.saved.bookmarks.isEmpty()) Text("Bookmark a page from the browser menu. Bookmarks stay on this phone.",
                    Modifier.padding(16.dp), style = MaterialTheme.typography.bodySmall)
            }
            items(browser.saved.bookmarks, key = { it.url }) { b ->
                PageRow(BrowserRules.title(b.title, b.url), b.url, null, trailing = {
                    IconButton(onClick = { browser.removeBookmark(b.url) }) { Icon(Icons.Filled.Close, "Remove bookmark") }
                }) { browser.go(b.url); onDismiss() }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@RequiresApi(29)
@Composable
private fun BrowserSettingsSheet(browser: BrowserRuntime, client: LiveClient, onDismiss: () -> Unit) {
    var clearing by remember { mutableStateOf(false) }
    val saved = browser.saved
    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp).padding(bottom = 24.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Browser settings and privacy", Modifier.semantics { heading() }, style = MaterialTheme.typography.titleMedium)
            EngineChoice(browser)
            EdgeEngineChoice(browser)
            Text("Search with", style = MaterialTheme.typography.titleSmall)
            val choices = listOf("computer" to (if (saved.computerSearch.isNotBlank()) "Same as Raticode on your computer" else "Same as your computer (Google until it's known)")) +
                BrowserRules.SEARCH_ENGINES.map { (label, template) -> template to label }
            choices.forEach { (value, label) ->
                Row(Modifier.fillMaxWidth().heightIn(min = 44.dp).combinedClickableRow { browser.setSearch(value) }, verticalAlignment = Alignment.CenterVertically) {
                    RadioButton(saved.search == value, { browser.setSearch(value) }); Text(label)
                }
            }
            SwitchRow("Save browsing history", "History stays on this phone, encrypted. Turning this off deletes it.", saved.keepHistory, browser::setKeepHistory)
            LockScreenTitlesRow()
            if (client.paired) SwitchRow("Open chat links here", "Web links in Rem's chat and threads open in the browser chosen above instead of your phone's default browser.",
                saved.linksInApp, browser::setLinksInApp)
            OutlinedButton(onClick = { clearing = true }, Modifier.fillMaxWidth()) { Text("Clear browsing data…") }
            Text("How ${BrowserTabs.CHROMIUM_NAME} works", style = MaterialTheme.typography.titleSmall)
            Text("Pages run in Android's system WebView on this phone's internet connection, never through your computer or Rem. " +
                "Only secure (https) pages open. Google Safe Browsing checks pages for phishing and malware. Third-party cookies are off. " +
                "Sites can't use your camera, microphone or location here, and pop-ups need a tap. Tabs, history and bookmarks stay on this phone, encrypted. " +
                "Raticode never reads pages, except a selection you send with Ask Rem.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
    if (clearing) ClearDialog(browser) { clearing = false }
}

/** Shared by both browsers: whether the lock screen's media controls name what's playing. */
@Composable
internal fun LockScreenTitlesRow() {
    val context = LocalContext.current
    SwitchRow("Show what's playing on the lock screen", "The title and picture in the lock screen's media controls. Private tabs only ever say \"Private tab\", " +
        "and App lock hides titles on the lock screen too.", BrowserMedia.lockScreenTitles) { BrowserMedia.setLockScreenTitles(context, it) }
}

@Composable
internal fun SwitchRow(title: String, detail: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth().heightIn(min = 56.dp).combinedClickableRow { onChange(!checked) }, verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title); Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Switch(checked, onChange, Modifier.semantics { contentDescription = title })
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SiteSheet(browser: BrowserRuntime, page: BrowserRuntime.Page, onDismiss: () -> Unit) {
    val certificate = remember(page.url) { browser.active?.let { browser.certificate(it) } }
    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp).padding(bottom = 24.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(BrowserRules.host(page.url).ifBlank { page.url }, Modifier.semantics { heading() }, style = MaterialTheme.typography.titleMedium)
            Text(if (BrowserRules.security(page.url) == BrowserRules.Security.SECURE) "Connection is secure. Information you send to this site is encrypted." else "This isn't a secure page.")
            certificate?.let { (to, by, until) ->
                Text("Certificate", style = MaterialTheme.typography.titleSmall)
                Text("Issued to $to\nIssued by $by\nValid until $until", style = MaterialTheme.typography.bodySmall)
            }
            Text("Sites can't use your camera, microphone or location in ${BrowserTabs.CHROMIUM_NAME}. To clear a site's cookies, use Clear browsing data, which clears every site.",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun BackForwardSheet(browser: BrowserRuntime, direction: Int, onDismiss: () -> Unit) {
    val (items, current) = remember { browser.historyList() }
    val shown = if (direction < 0) (current - 1 downTo 0).toList() else (current + 1 until items.size).toList()
    ModalBottomSheet(onDismissRequest = onDismiss) {
        LazyColumn(Modifier.fillMaxWidth().padding(bottom = 16.dp)) {
            item { Text(if (direction < 0) "Go back to" else "Go forward to", Modifier.padding(horizontal = 16.dp).semantics { heading() }, style = MaterialTheme.typography.titleMedium) }
            items(shown) { index -> val (url, title) = items[index]; PageRow(title, url, null) { browser.goBy(index - current); onDismiss() } }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@RequiresApi(29)
@Composable
private fun LinkMenuSheet(menu: BrowserRuntime.LinkMenu, browser: BrowserRuntime, client: LiveClient, context: Context, onDismiss: () -> Unit) {
    val link = menu.link?.takeIf { it.startsWith("https://") || it.startsWith("http://") }
    val image = menu.image?.takeIf { it.startsWith("https://") }
    if (link == null && image == null) { LaunchedEffect(menu) { onDismiss() }; return }
    val canSend = client.paired && client.desktopControl && client.connected && client.desktopBrowser == true && link != null && BrowserAddress.isWeb(link) && !browser.isPrivate(menu.tab)
    ModalBottomSheet(onDismissRequest = onDismiss, modifier = Modifier.testTag("browserLinkMenu")) {
        Column(Modifier.fillMaxWidth().padding(bottom = 16.dp)) {
            Text(link ?: image!!, Modifier.padding(horizontal = 16.dp), style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
            if (link != null) {
                MenuRow("Open in new tab") { browser.openFrom(menu.tab, link); onDismiss() }
                MenuRow("Open in background tab") { browser.openFrom(menu.tab, link, background = true)?.let { browser.notice = "Opened in a background tab." }; onDismiss() }
                MenuRow("Copy link address") { copy(context, link); onDismiss() }
                MenuRow("Share link") {
                    try { context.startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, link), "Share link")) }
                    catch (_: ActivityNotFoundException) {}
                    onDismiss()
                }
                if (canSend) MenuRow("Open link on your computer", icon = ThreadIcons.Computer) { client.desktopAction(DesktopBrowser.OPEN, JSONObject().put("url", link)); onDismiss() }
                MenuRow("Open in your phone's browser") { openInBrowser(context, link); onDismiss() }
            }
            if (image != null) {
                MenuRow("Open image in new tab") { browser.openFrom(menu.tab, image); onDismiss() }
                MenuRow("Copy image address") { copy(context, image); onDismiss() }
            }
        }
    }
}

@Composable
private fun AskDialog(ask: BrowserRuntime.Ask, browser: BrowserRuntime, context: Context) {
    val properties = DialogProperties(dismissOnClickOutside = false)
    when (ask) {
        is BrowserRuntime.Ask.Alert -> AlertDialog(onDismissRequest = browser::answerNo, properties = properties, title = { Text("${ask.host.ifBlank { "This page" }} says") },
            text = { Text(ask.message) }, confirmButton = { TextButton(onClick = { browser.answerYes() }) { Text("OK") } })
        is BrowserRuntime.Ask.Confirm -> AlertDialog(onDismissRequest = browser::answerNo, properties = properties, title = { Text("${ask.host.ifBlank { "This page" }} asks") },
            text = { Text(ask.message) }, confirmButton = { TextButton(onClick = { browser.answerYes() }) { Text("OK") } },
            dismissButton = { TextButton(onClick = browser::answerNo) { Text("Cancel") } })
        is BrowserRuntime.Ask.Prompt -> {
            var value by remember(ask) { mutableStateOf(ask.value) }
            AlertDialog(onDismissRequest = browser::answerNo, properties = properties, title = { Text("${ask.host.ifBlank { "This page" }} asks") },
                text = { Column { Text(ask.message); OutlinedTextField(value, { value = it.take(2000) }, singleLine = true) } },
                confirmButton = { TextButton(onClick = { browser.answerYes(value) }) { Text("OK") } },
                dismissButton = { TextButton(onClick = browser::answerNo) { Text("Cancel") } })
        }
        is BrowserRuntime.Ask.Leave -> AlertDialog(onDismissRequest = browser::answerNo, properties = properties, title = { Text("Leave this page?") },
            text = { Text("${ask.host.ifBlank { "This page" }} may not save changes you made.") },
            confirmButton = { TextButton(onClick = { browser.answerYes() }) { Text("Leave") } },
            dismissButton = { TextButton(onClick = browser::answerNo) { Text("Stay") } })
        is BrowserRuntime.Ask.SignIn -> {
            var user by remember(ask) { mutableStateOf("") }
            var password by remember(ask) { mutableStateOf("") }
            AlertDialog(onDismissRequest = browser::answerNo, properties = properties, title = { Text("Sign in to ${ask.host}") },
                text = { Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (ask.realm.isNotBlank()) Text("The site says: ${ask.realm.take(200)}", style = MaterialTheme.typography.bodySmall)
                    OutlinedTextField(user, { user = it.take(200) }, label = { Text("Username") }, singleLine = true)
                    OutlinedTextField(password, { password = it.take(200) }, label = { Text("Password") }, singleLine = true,
                        visualTransformation = PasswordVisualTransformation(), keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password))
                    Text("Raticode never saves these.", style = MaterialTheme.typography.bodySmall)
                } },
                confirmButton = { TextButton(onClick = { browser.answerYes(user = user, password = password) }) { Text("Sign in") } },
                dismissButton = { TextButton(onClick = browser::answerNo) { Text("Cancel") } })
        }
        is BrowserRuntime.Ask.Media -> PageMediaDialog(ask.host, ask.camera, ask.microphone) { allowed ->
            if (allowed) browser.answerYes() else { browser.answerNo(); browser.notice = "The page wasn't given your ${BrowserRules.mediaAsk(ask.camera, ask.microphone)}." } }
        is BrowserRuntime.Ask.OpenApp -> AlertDialog(onDismissRequest = browser::answerNo, title = { Text("Open another app?") },
            text = { Text("This link leaves Raticode's browser and opens in another app on this phone:\n${ask.url.take(300)}") },
            confirmButton = { TextButton(onClick = { browser.openApp(context, ask.url) }) { Text("Open") } },
            dismissButton = { TextButton(onClick = browser::answerNo) { Text("Cancel") } })
        is BrowserRuntime.Ask.Download -> AlertDialog(onDismissRequest = browser::answerNo, title = { Text("Download file?") },
            icon = if (ask.risky) ({ Icon(Icons.Filled.Warning, null, tint = MaterialTheme.colorScheme.error) }) else null,
            text = { Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(ask.name, style = MaterialTheme.typography.titleSmall)
                Text("${BrowserRules.size(ask.size)} from ${ask.host}")
                if (ask.risky) Text("This kind of file can install or run software. Download it only if you trust ${ask.host}.", color = MaterialTheme.colorScheme.error)
                Text("It goes to Downloads on this phone, where other apps can see it.", style = MaterialTheme.typography.bodySmall)
            } },
            confirmButton = { TextButton(onClick = { browser.download(context) }) { Text(if (ask.risky) "Download anyway" else "Download") } },
            dismissButton = { TextButton(onClick = browser::answerNo) { Text("Cancel") } })
    }
}

private fun copy(context: Context, text: String) {
    (context.getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager)?.setPrimaryClip(ClipData.newPlainText("Link", text))
}
