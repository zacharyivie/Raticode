package dev.raticode.mobile.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.annotation.RequiresApi
import androidx.compose.foundation.ExperimentalFoundationApi
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
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import dev.raticode.mobile.browser.BrowserControlsMotion
import dev.raticode.mobile.browser.BrowserMediaRules
import dev.raticode.mobile.browser.BrowserRules
import dev.raticode.mobile.browser.BrowserRuntime
import dev.raticode.mobile.browser.BrowserTabs
import dev.raticode.mobile.browser.GeckoBrowser
import dev.raticode.mobile.browser.GeckoImport
import dev.raticode.mobile.browser.GeckoRules
import dev.raticode.mobile.live.DesktopBrowser
import dev.raticode.mobile.live.LiveClient
import kotlinx.coroutines.launch
import org.mozilla.geckoview.GeckoView

/**
 * Raticode (gecko): Mozilla's engine drawing inside this page, so Raticode's bottom bar stays below it. Favorites, history,
 * profiles, settings and Firefox extensions live here, separate from Raticode (chromium); the tab list shows both browsers'
 * tabs (see [AllTabsSheet]).
 */
@OptIn(ExperimentalMaterial3Api::class)
@RequiresApi(29)
@Composable
internal fun GeckoPage(client: LiveClient, chromium: BrowserRuntime, onBack: (() -> Unit)?, revealControls: Int = 0, onAskRem: (String) -> Unit) {
    val context = LocalContext.current
    val gecko = remember { GeckoBrowser.get(context) }
    val page = gecko.page
    val focus = LocalFocusManager.current
    var editing by remember { mutableStateOf(false) }
    var address by remember { mutableStateOf("") }
    var sheet by remember { mutableStateOf<String?>(null) }
    var finding by remember { mutableStateOf(false) }
    // Android can end Raticode while Firefox has the screen. The time the import's handover began comes back with this
    // screen so it can carry on; nothing from the clipboard is kept, and a dismissed sheet keeps nothing.
    var savedHandover by rememberSaveable { mutableStateOf(0L) }
    LaunchedEffect(gecko) {
        if (savedHandover != 0L && gecko.importer.restoreHandover(savedHandover)) sheet = "import"
        snapshotFlow { if (sheet == "import") gecko.importer.handoverStartedAt else 0L }.collect { savedHandover = it }
    }
    // The computer's tabs are read only while the tab list shows them, as in Raticode (chromium).
    val showsComputer = client.paired && sheet == "tabs"
    DisposableEffect(showsComputer, client.desktopBrowser) {
        if (showsComputer && client.desktopBrowser == true) client.openDesktop(DesktopBrowser.TABS)
        onDispose { if (showsComputer && client.desktopBrowser == true) client.openDesktop(null) }
    }
    val computer = client.desktopViews[DesktopBrowser.TABS] as? DesktopBrowser.View
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    LaunchedEffect(gecko) {
        snapshotFlow { gecko.notice }.collect { text ->
            if (text == null) return@collect
            gecko.notice = null
            scope.launch { snackbar.currentSnackbarData?.dismiss(); snackbar.showSnackbar(text, withDismissAction = true) }
        }
    }
    fun submit(text: String) { editing = false; focus.clearFocus(); gecko.go(text) }
    fun startEditing() { address = if (page.url == BrowserRules.HOME) "" else page.url; editing = true }
    BackHandler(enabled = editing || finding || page.canBack || gecko.fullscreen) {
        when {
            editing -> { editing = false; focus.clearFocus() }
            finding -> { finding = false; gecko.endFind() }
            gecko.fullscreen -> gecko.exitFullscreen()
            else -> gecko.back()
        }
    }

    val controls = remember { BrowserControlsState() }
    DisposableEffect(gecko) {
        gecko.scrollListener = controls::scrolledTo
        onDispose { gecko.scrollListener = null }
    }
    val controlsOpen = browserControlsOpen(controls, revealRequests = revealControls, tab = gecko.active, loading = page.loading,
        held = editing || finding || sheet != null || gecko.menu != null || gecko.popup != null || gecko.asks.isNotEmpty() || page.url == BrowserRules.HOME)
    val forwardFits = BrowserControlsMotion.forwardFits(LocalConfiguration.current.screenWidthDp, onBack != null)

    Box(Modifier.fillMaxSize().testTag("geckoPage")) {
        Column(Modifier.fillMaxSize()) {
            if (!gecko.fullscreen) {
                BrowserToolbar(controls, controlsOpen, "gecko", page.url, gecko.saved.settings.desktopSites, page.loading, page.progress, page.canBack, page.canForward,
                    canReload = page.url != BrowserRules.HOME, tabCount = allTabs(chromium, gecko, BrowserTabs.GECKO).size, showForward = forwardFits, onPageBack = onBack, onEdit = ::startEditing,
                    onBack = { gecko.back() }, onForward = { gecko.forward() }, onStop = { gecko.stop() }, onReload = { gecko.reload() }, onTabs = { sheet = "tabs" }, onMenu = { sheet = "menu" },
                    privateTab = gecko.activeIsPrivate,
                    editor = if (!editing) null else ({
                        val requester = remember { FocusRequester() }
                        LaunchedEffect(Unit) { requester.requestFocus() }
                        OutlinedTextField(address, { address = it }, singleLine = true, placeholder = { Text("Search or type an address") },
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Go, autoCorrectEnabled = false),
                            keyboardActions = KeyboardActions(onGo = { submit(address) }),
                            trailingIcon = { IconButton(onClick = { if (address.isEmpty()) { editing = false; focus.clearFocus() } else address = "" }) { Icon(Icons.Filled.Close, "Clear address") } },
                            modifier = Modifier.weight(1f).padding(vertical = 4.dp).focusRequester(requester).testTag("geckoAddressField"))
                    }))
                if (finding) FindRow(gecko) { finding = false; gecko.endFind() }
            }
            Box(Modifier.weight(1f).fillMaxWidth().testTag("geckoContent")) {
                when {
                    editing -> Suggestions(gecko, address) { submit(it) }
                    page.url == BrowserRules.HOME -> GeckoStart(gecko) { sheet = it }
                    else -> {
                        val active = gecko.active
                        active?.let { ShowingMediaOf(BrowserMediaRules.Key(BrowserMediaRules.Engine.GECKO, it)) }
                        AndroidView(factory = { GeckoView(it) }, modifier = Modifier.fillMaxSize().testTag("geckoView"), update = { view ->
                            // Reading these redraws when the tab changes or a blank tab starts loading.
                            active.hashCode(); page.url.hashCode()
                            val session = gecko.activeSession()
                            if (view.session !== session) { view.releaseSession(); if (session != null) view.setSession(session) }
                        }, onRelease = { it.releaseSession() })
                    }
                }
            }
        }
        SnackbarHost(snackbar, Modifier.align(Alignment.BottomCenter).padding(bottom = 8.dp))
    }

    when (sheet) {
        "tabs" -> AllTabsSheet(chromium, gecko, client, computer, BrowserTabs.GECKO) { sheet = null }
        "menu" -> MenuSheet(gecko, page, !forwardFits && page.canForward, onAskRem, onFind = { finding = true }, onOpen = { sheet = it }) { sheet = null }
        "extensions" -> ExtensionsSheet(gecko) { sheet = null }
        "favorites" -> LinksSheet("Favorites", gecko.bookmarks, gecko, onRemove = { gecko.removeBookmark(it) }) { sheet = null }
        "history" -> LinksSheet("History", gecko.history, gecko, onRemove = null) { sheet = null }
        "profiles" -> ProfilesSheet(gecko) { sheet = null }
        "settings" -> GeckoSettingsSheet(gecko, chromium, onImport = { sheet = "import" }) { sheet = null }
        "import" -> GeckoImportSheet(gecko) { gecko.importer.stopWaiting(); sheet = null }
    }
    gecko.popup?.let { popup -> PopupSheet(gecko, popup) }
    gecko.menu?.let { menu -> LinkMenu(gecko, menu, context) }
    gecko.asks.firstOrNull()?.let { ask -> AskDialog(gecko, ask) }
}

@Composable
private fun FindRow(gecko: GeckoBrowser, onClose: () -> Unit) {
    var query by remember { mutableStateOf("") }
    Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        OutlinedTextField(query, { query = it; gecko.find(it) }, singleLine = true, placeholder = { Text("Find in page") }, modifier = Modifier.weight(1f).testTag("geckoFind"))
        gecko.found?.let { Text("${it.current}/${it.total}", Modifier.padding(horizontal = 6.dp), style = MaterialTheme.typography.labelMedium) }
        IconButton(onClick = { gecko.find(query, backwards = true) }) { Icon(ThreadIcons.ChevronUp, "Previous") }
        IconButton(onClick = { gecko.find(query) }) { Icon(ThreadIcons.ChevronDown, "Next") }
        IconButton(onClick = onClose) { Icon(Icons.Filled.Close, "Close find") }
    }
}

@Composable
private fun Suggestions(gecko: GeckoBrowser, text: String, onPick: (String) -> Unit) {
    val input = text.trim().lowercase()
    val matches = (gecko.bookmarks + gecko.history).filter { input.isEmpty() || input in it.url.lowercase() || input in it.title.lowercase() }.distinctBy { it.url }.take(10)
    LazyColumn(Modifier.fillMaxSize()) { items(matches, key = { it.url }) { link -> MenuRow(link.title.ifBlank { link.url }, detail = BrowserRules.host(link.url)) { onPick(link.url) } } }
}

@Composable
private fun GeckoStart(gecko: GeckoBrowser, open: (String) -> Unit) {
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(vertical = 12.dp)) {
        if (gecko.activeIsPrivate) PrivateNote("Extensions don't run in it.")
        Text("${BrowserTabs.GECKO_NAME} · ${gecko.profile.name}", Modifier.padding(horizontal = 16.dp).semantics { heading() }, style = MaterialTheme.typography.titleMedium)
        Text("Mozilla's engine, inside Raticode. Add Firefox extensions from the menu.", Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        if (gecko.bookmarks.isNotEmpty()) SectionTitle("Favorites")
        gecko.bookmarks.take(12).forEach { link -> MenuRow(link.title.ifBlank { link.url }, icon = ThreadIcons.Star, detail = BrowserRules.host(link.url)) { gecko.go(link.url) } }
        if (gecko.history.isNotEmpty()) SectionTitle("Recent")
        gecko.history.take(6).forEach { link -> MenuRow(link.title.ifBlank { link.url }, detail = BrowserRules.host(link.url)) { gecko.go(link.url) } }
        if (gecko.bookmarks.isEmpty() && gecko.history.isEmpty()) TextButton(onClick = { open("extensions") }, Modifier.padding(horizontal = 8.dp)) { Text("Find extensions") }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun MenuSheet(gecko: GeckoBrowser, page: GeckoBrowser.Page, showForward: Boolean, onAskRem: (String) -> Unit, onFind: () -> Unit, onOpen: (String) -> Unit, onDismiss: () -> Unit) {
    val context = LocalContext.current
    val web = page.url != BrowserRules.HOME
    ModalBottomSheet(onDismissRequest = onDismiss, modifier = Modifier.testTag("geckoMenuSheet")) {
        Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(bottom = 16.dp)) {
            if (showForward) MenuRow("Forward", icon = ThreadIcons.PageForward) { gecko.forward(); onDismiss() }
            MenuRow("New tab") { gecko.newTab(); onDismiss() }
            MenuRow("New private tab", detail = "Not saved, and extensions don't run in it.") { gecko.newTab(private = true); onDismiss() }
            if (gecko.canReopen) MenuRow("Reopen closed tab") { gecko.reopenClosed(); onDismiss() }
            MenuRow("Extensions", icon = ThreadIcons.Extensions) { onOpen("extensions") }
            MenuRow(if (gecko.isBookmarked(page.url)) "Remove from favorites" else "Add to favorites", icon = ThreadIcons.Star, enabled = web) { gecko.toggleBookmark(); onDismiss() }
            MenuRow("Favorites") { onOpen("favorites") }
            MenuRow("History") { onOpen("history") }
            MenuRow("Find in page", enabled = web) { onFind(); onDismiss() }
            if (!gecko.activeIsPrivate) MenuRow("Ask Rem about this page", icon = ThreadIcons.Rem, enabled = web) { onAskRem(("About this page: ${page.title}\n${BrowserRules.storable(page.url)}\n\n").take(4000)); onDismiss() }
            MenuRow("Share", enabled = web) { context.startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, page.url), null)); onDismiss() }
            MenuRow("Copy address", enabled = web) { copy(context, page.url); gecko.notice = "Copied."; onDismiss() }
            MenuRow("Save as PDF", enabled = web) { gecko.saveAsPdf(); onDismiss() }
            Row(Modifier.fillMaxWidth().heightIn(min = 56.dp).padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                Text("Text size", Modifier.weight(1f))
                OutlinedButton(onClick = { gecko.textZoom(-10) }, enabled = gecko.saved.settings.textZoom > BrowserRules.MIN_TEXT) { Text("A−") }
                TextButton(onClick = { gecko.textZoom(0) }) { Text("${gecko.saved.settings.textZoom}%") }
                OutlinedButton(onClick = { gecko.textZoom(10) }, enabled = gecko.saved.settings.textZoom < BrowserRules.MAX_TEXT) { Text("A+") }
            }
            MenuRow("Import from Firefox", detail = "Extensions, settings and bookmarks") { onOpen("import") }
            MenuRow("Profiles", detail = gecko.profile.name) { onOpen("profiles") }
            MenuRow("Settings", icon = ThreadIcons.Settings) { onOpen("settings") }
        }
    }
}

private fun copy(context: Context, text: String) {
    context.getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("Address", text))
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun LinksSheet(title: String, links: List<GeckoRules.Link>, gecko: GeckoBrowser, onRemove: ((GeckoRules.Link) -> Unit)?, onDismiss: () -> Unit) {
    ModalBottomSheet(onDismissRequest = onDismiss) {
        LazyColumn(Modifier.fillMaxWidth().padding(bottom = 16.dp)) {
            item { Text(title, Modifier.padding(horizontal = 16.dp, vertical = 8.dp).semantics { heading() }, style = MaterialTheme.typography.titleMedium) }
            if (links.isEmpty()) item { Text("Nothing here yet.", Modifier.padding(16.dp), color = MaterialTheme.colorScheme.onSurfaceVariant) }
            items(links, key = { it.url + it.profile }) { link ->
                Row(Modifier.fillMaxWidth().heightIn(min = 56.dp).combinedClickableRow { gecko.go(link.url); onDismiss() }.padding(start = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(link.title.ifBlank { link.url }, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Text(BrowserRules.host(link.url), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1)
                    }
                    if (onRemove != null) IconButton(onClick = { onRemove(link) }) { Icon(Icons.Filled.Close, "Remove") }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ExtensionsSheet(gecko: GeckoBrowser, onDismiss: () -> Unit) {
    var query by remember { mutableStateOf("") }
    var results by remember { mutableStateOf<List<GeckoRules.Listing>?>(null) }
    var searching by remember { mutableStateOf(false) }
    LaunchedEffect(gecko) { gecko.refreshExtensions() }
    fun search() { if (query.isBlank()) return; searching = true; gecko.search(query) { results = it; searching = false; if (it == null) gecko.notice = "Couldn't reach Firefox Add-ons." } }
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true), modifier = Modifier.testTag("geckoExtensionsSheet")) {
        LazyColumn(Modifier.fillMaxWidth().padding(bottom = 16.dp)) {
            item {
                Text("Extensions", Modifier.padding(horizontal = 16.dp).semantics { heading() }, style = MaterialTheme.typography.titleMedium)
                OutlinedTextField(query, { query = it }, singleLine = true, label = { Text("Search Firefox Add-ons") },
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search), keyboardActions = KeyboardActions(onSearch = { search() }),
                    trailingIcon = { TextButton(onClick = ::search, enabled = query.isNotBlank() && !searching) { Text(if (searching) "…" else "Search") } },
                    modifier = Modifier.padding(16.dp).fillMaxWidth().testTag("geckoExtensionSearch"))
            }
            results?.let { list ->
                item { SectionTitle("From Firefox Add-ons") }
                if (list.isEmpty()) item { Text("No extensions found.", Modifier.padding(16.dp), color = MaterialTheme.colorScheme.onSurfaceVariant) }
                items(list, key = { "add-" + it.url }) { item -> ListingRow(gecko, item) }
            }
            item { SectionTitle("Installed") }
            if (gecko.extensions.isEmpty()) item { Text("None yet. Search above, or tap Add to Firefox on addons.mozilla.org in this browser.", Modifier.padding(16.dp), color = MaterialTheme.colorScheme.onSurfaceVariant) }
            items(gecko.extensions, key = { it.id }) { item -> InstalledRow(gecko, item, onDismiss) }
            if (gecko.extensions.isNotEmpty()) item { TextButton(onClick = { gecko.updateExtensions() }, Modifier.padding(horizontal = 8.dp)) { Text("Check for updates") } }
        }
    }
}

@Composable
private fun ListingRow(gecko: GeckoBrowser, item: GeckoRules.Listing) {
    val added = gecko.extensions.any { it.id == item.id }
    Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(item.name, fontWeight = FontWeight.Medium, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(item.summary, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2, overflow = TextOverflow.Ellipsis)
            Text(if (item.android) "Made for Android · ${item.users} users" else "Desktop extension, may only partly work · ${item.users} users", style = MaterialTheme.typography.labelSmall)
        }
        FilledTonalButton(onClick = { gecko.install(item.url) }, enabled = !added && item.url !in gecko.installing, modifier = Modifier.padding(start = 8.dp).testTag("geckoInstall-${item.id}")) {
            Text(when { added -> "Added"; item.url in gecko.installing -> "Adding…"; else -> "Add" })
        }
    }
}

@Composable
private fun InstalledRow(gecko: GeckoBrowser, item: GeckoBrowser.Extension, onDismiss: () -> Unit) {
    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp).testTag("geckoExtension-${item.id}")) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(item.name, fontWeight = FontWeight.Medium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(item.version + (item.action?.badgeText?.takeIf { it.isNotBlank() }?.let { " · $it" }.orEmpty()), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Switch(item.enabled, { gecko.setEnabled(item, it) }, Modifier.semantics { contentDescription = "${item.name} on" })
        }
        Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            if (item.action != null && item.enabled) TextButton(onClick = { item.action.click() }, Modifier.testTag("geckoOpen-${item.id}")) { Text("Open") }
            item.options?.takeIf { item.enabled }?.let { url -> TextButton(onClick = { gecko.openOptions(url); onDismiss() }) { Text("Settings") } }
            TextButton(onClick = { gecko.uninstall(item) }) { Text("Remove") }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun PopupSheet(gecko: GeckoBrowser, popup: GeckoBrowser.Popup) {
    ModalBottomSheet(onDismissRequest = { gecko.closePopup() }, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true), modifier = Modifier.testTag("geckoPopup")) {
        AndroidView(factory = { GeckoView(it).apply { setSession(popup.session) } }, modifier = Modifier.fillMaxWidth().height(480.dp), onRelease = { it.releaseSession() })
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ProfilesSheet(gecko: GeckoBrowser, onDismiss: () -> Unit) {
    var name by remember { mutableStateOf("") }
    ModalBottomSheet(onDismissRequest = onDismiss, modifier = Modifier.testTag("geckoProfilesSheet")) {
        Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(bottom = 16.dp)) {
            Text("Profiles", Modifier.padding(horizontal = 16.dp).semantics { heading() }, style = MaterialTheme.typography.titleMedium)
            Text("Each has its own tabs, favorites, history, sign-ins and site data. Extensions are shared.", Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            gecko.saved.profiles.forEach { profile ->
                Row(Modifier.fillMaxWidth().heightIn(min = 52.dp).combinedClickableRow { gecko.switchProfile(profile.id); onDismiss() }.padding(start = 8.dp).testTag("geckoProfile-${profile.name}"), verticalAlignment = Alignment.CenterVertically) {
                    RadioButton(profile.id == gecko.saved.currentProfile, { gecko.switchProfile(profile.id); onDismiss() })
                    Text(profile.name, Modifier.weight(1f))
                    if (profile.id != GeckoRules.DEFAULT_PROFILE) TextButton(onClick = { gecko.deleteProfile(profile.id) }) { Text("Delete") }
                }
            }
            Row(Modifier.padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(name, { name = it }, singleLine = true, label = { Text("New profile") }, modifier = Modifier.weight(1f).testTag("geckoProfileName"))
                TextButton(onClick = { if (gecko.addProfile(name)) onDismiss() else gecko.notice = "Name it, or delete a profile first." }, enabled = name.isNotBlank(), modifier = Modifier.testTag("geckoProfileAdd")) { Text("Add") }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun GeckoSettingsSheet(gecko: GeckoBrowser, chromium: BrowserRuntime, onImport: () -> Unit, onDismiss: () -> Unit) {
    val settings = gecko.saved.settings
    ModalBottomSheet(onDismissRequest = onDismiss, modifier = Modifier.testTag("geckoSettingsSheet")) {
        Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(bottom = 16.dp)) {
            Text("${BrowserTabs.GECKO_NAME} settings", Modifier.padding(horizontal = 16.dp).semantics { heading() }, style = MaterialTheme.typography.titleMedium)
            SectionTitle("Tracking protection")
            ChoiceRow("Standard", settings.tracking == GeckoRules.TRACKING_STANDARD) { gecko.setTracking(GeckoRules.TRACKING_STANDARD) }
            ChoiceRow("Strict", settings.tracking == GeckoRules.TRACKING_STRICT) { gecko.setTracking(GeckoRules.TRACKING_STRICT) }
            SectionTitle("Website colors")
            listOf(GeckoRules.COLORS_SYSTEM to "Follow the phone", GeckoRules.COLORS_LIGHT to "Light", GeckoRules.COLORS_DARK to "Dark").forEach { (value, label) -> ChoiceRow(label, settings.colors == value) { gecko.setColors(value) } }
            SectionTitle("Search")
            GeckoImport.SEARCH_ENGINES.forEach { (label, template) -> ChoiceRow(label, settings.search == template) { gecko.setSearch(template) } }
            SectionTitle("Pages")
            SwitchRow("Desktop sites", settings.desktopSites) { gecko.setDesktopSites(it) }
            SwitchRow("Save browsing history", settings.keepHistory) { gecko.setKeepHistory(it) }
            Box(Modifier.padding(horizontal = 16.dp)) { LockScreenTitlesRow() }
            MenuRow("Clear this profile's cookies, site data and history") { gecko.clearData(history = true); onDismiss() }
            MenuRow("Import from Firefox", detail = "Extensions, settings and bookmarks") { onImport() }
            SectionTitle("Default browser")
            Column(Modifier.padding(horizontal = 16.dp)) {
                EngineChoice(chromium)
                EdgeEngineChoice(chromium)
            }
            Text("${BrowserTabs.GECKO_NAME} has its own sign-ins, separate from ${BrowserTabs.CHROMIUM_NAME}'s. Android keeps the Firefox app's data private to Firefox, so its passwords and account can't be read from here.",
                Modifier.padding(horizontal = 16.dp, vertical = 8.dp), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun ChoiceRow(label: String, selected: Boolean, onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).combinedClickableRow(onClick).padding(start = 8.dp), verticalAlignment = Alignment.CenterVertically) { RadioButton(selected, onClick); Text(label) }
}

@Composable
private fun SwitchRow(label: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) { Text(label, Modifier.weight(1f)); Switch(checked, onChange) }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun LinkMenu(gecko: GeckoBrowser, menu: GeckoBrowser.LinkMenu, context: Context) {
    val close = { gecko.menu = null }
    ModalBottomSheet(onDismissRequest = close) {
        Column(Modifier.fillMaxWidth().padding(bottom = 16.dp)) {
            menu.link?.let { link ->
                MenuRow("Open in new tab") { gecko.openFrom(menu.tab, link); close() }
                MenuRow("Open in background tab") { gecko.openFrom(menu.tab, link, background = true); close() }
                MenuRow("Copy link") { copy(context, link); gecko.notice = "Copied."; close() }
            }
            menu.image?.let { image ->
                MenuRow("Open image in new tab") { gecko.openFrom(menu.tab, image); close() }
                MenuRow("Copy image address") { copy(context, image); gecko.notice = "Copied."; close() }
            }
        }
    }
}

@OptIn(ExperimentalComposeUiApi::class)
@Composable
private fun AskDialog(gecko: GeckoBrowser, ask: GeckoBrowser.Ask) {
    when (ask) {
        is GeckoBrowser.Ask.Confirm -> AlertDialog(onDismissRequest = { }, title = { Text(ask.title) }, text = { Text(ask.message) },
            confirmButton = { TextButton(onClick = { gecko.answered(ask); ask.answer(true) }, Modifier.testTag("geckoAskYes")) { Text(ask.yes) } },
            dismissButton = ask.no?.let { label -> { TextButton(onClick = { gecko.answered(ask); ask.answer(false) }) { Text(label) } } })
        is GeckoBrowser.Ask.Media -> PageMediaDialog(ask.site, ask.camera, ask.microphone) { allowed -> gecko.answered(ask); ask.answer(allowed) }
        is GeckoBrowser.Ask.AppPermissions -> {
            val launcher = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { results -> gecko.answered(ask); ask.answer(results.values.all { it }) }
            LaunchedEffect(ask) { launcher.launch(ask.permissions.toTypedArray()) }
        }
        is GeckoBrowser.Ask.Text -> {
            var value by remember(ask) { mutableStateOf(ask.value) }
            AlertDialog(onDismissRequest = { }, title = { Text(ask.title) }, text = { Column { Text(ask.message); OutlinedTextField(value, { value = it }, singleLine = true) } },
                confirmButton = { TextButton(onClick = { gecko.answered(ask); ask.answer(value) }) { Text("OK") } },
                dismissButton = { TextButton(onClick = { gecko.answered(ask); ask.answer(null) }) { Text("Cancel") } })
        }
        is GeckoBrowser.Ask.Login -> {
            var user by remember(ask) { mutableStateOf("") }
            var password by remember(ask) { mutableStateOf("") }
            AlertDialog(onDismissRequest = { }, title = { Text("Sign in to ${ask.site}") }, text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (ask.message.isNotBlank()) Text(ask.message)
                    if (ask.username) OutlinedTextField(user, { user = it }, singleLine = true, label = { Text("Username") })
                    OutlinedTextField(password, { password = it }, singleLine = true, label = { Text("Password") }, visualTransformation = PasswordVisualTransformation())
                }
            }, confirmButton = { TextButton(onClick = { gecko.answered(ask); ask.answer(user, password) }) { Text("Sign in") } },
                dismissButton = { TextButton(onClick = { gecko.answered(ask); ask.answer(null, null) }) { Text("Cancel") } })
        }
        is GeckoBrowser.Ask.Choose -> {
            var picked by remember(ask) { mutableStateOf(emptySet<String>()) }
            AlertDialog(onDismissRequest = { gecko.answered(ask); ask.answer(null) }, title = { Text(ask.title) }, text = {
                LazyColumn(Modifier.heightIn(max = 360.dp)) { items(ask.items, key = { it.first }) { (id, label) ->
                    Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).combinedClickableRow {
                        if (ask.multiple) picked = if (id in picked) picked - id else picked + id else { gecko.answered(ask); ask.answer(listOf(id)) }
                    }, verticalAlignment = Alignment.CenterVertically) {
                        if (ask.multiple) Checkbox(id in picked, null)
                        Text(label)
                    }
                } }
            }, confirmButton = { if (ask.multiple) TextButton(onClick = { gecko.answered(ask); ask.answer(picked.toList()) }) { Text("Done") } },
                dismissButton = { TextButton(onClick = { gecko.answered(ask); ask.answer(null) }) { Text("Cancel") } })
        }
        is GeckoBrowser.Ask.Files -> {
            val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
                gecko.answered(ask); ask.answer(if (ask.multiple) uris else uris.take(1))
            }
            LaunchedEffect(ask) { picker.launch(ask.mime.ifEmpty { listOf("*/*") }.toTypedArray()) }
        }
        is GeckoBrowser.Ask.Install -> AlertDialog(onDismissRequest = { }, modifier = Modifier.testTag("geckoInstallPrompt"), title = { Text("Add ${ask.name}?") }, text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(if (ask.permissions.isEmpty() && ask.origins.isEmpty()) "It asks for no special access." else "It will be able to:")
                ask.permissions.forEach { Text("• $it", style = MaterialTheme.typography.bodySmall) }
                if (ask.origins.isNotEmpty()) Text(if (ask.origins.any { "<all_urls>" in it || it.startsWith("*://*/") }) "• Read and change your data on all websites" else "• Read and change your data on ${ask.origins.joinToString(", ").take(400)}", style = MaterialTheme.typography.bodySmall)
                if (ask.data.isNotEmpty()) Text("The developer says it collects: ${ask.data.joinToString(", ")}", style = MaterialTheme.typography.bodySmall)
            }
        }, confirmButton = { TextButton(onClick = { gecko.answered(ask); ask.answer(true) }, Modifier.testTag("geckoInstallAllow")) { Text("Add") } },
            dismissButton = { TextButton(onClick = { gecko.answered(ask); ask.answer(false) }) { Text("Cancel") } })
    }
}
