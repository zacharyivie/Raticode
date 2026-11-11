package dev.raticode.mobile.ui

import androidx.annotation.RequiresApi
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import dev.raticode.mobile.browser.BrowserRules
import dev.raticode.mobile.browser.BrowserRuntime
import dev.raticode.mobile.browser.BrowserTabs
import dev.raticode.mobile.browser.EdgeApps
import dev.raticode.mobile.live.DesktopBrowser
import dev.raticode.mobile.live.LiveClient
import kotlinx.coroutines.launch

/**
 * The Browser page with Microsoft Edge chosen. Android has no way to show Edge inside another app, so this page hands
 * addresses, searches and the computer's tabs to Edge for Android. Edge documents no links for its settings, account
 * sign-in or extensions, so those buttons open Edge and the page says where each one is in Edge's menu. Edge draws them,
 * keeps their data in its own profile and asks for any password itself; Raticode sees none of it.
 */
@RequiresApi(29)
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun EdgePage(client: LiveClient, browser: BrowserRuntime, onBack: (() -> Unit)?) {
    val context = LocalContext.current
    val focus = LocalFocusManager.current
    var edge by remember { mutableStateOf(EdgeApps.find(context)) }
    // Installing, turning on or removing Edge happens in other apps; look again whenever Raticode comes back.
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    DisposableEffect(lifecycle) {
        val observer = LifecycleEventObserver { _, event -> if (event == Lifecycle.Event.ON_RESUME) edge = EdgeApps.find(context) }
        lifecycle.addObserver(observer)
        onDispose { lifecycle.removeObserver(observer) }
    }
    val polls = client.paired && client.desktopBrowser == true
    DisposableEffect(polls) {
        if (polls) client.openDesktop(DesktopBrowser.TABS)
        onDispose { if (polls) client.openDesktop(null) }
    }
    val computer = client.desktopViews[DesktopBrowser.TABS] as? DesktopBrowser.View
    LaunchedEffect(computer?.searchUrl) { computer?.searchUrl?.let(browser::computerSearch) }
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    fun say(text: String) { scope.launch { snackbar.currentSnackbarData?.dismiss(); snackbar.showSnackbar(text, withDismissAction = true) } }
    val saved = browser.saved
    // A missing, turned-off or look-alike Edge gets its own reason; only an Android refusal is "didn't open".
    fun openUrl(url: String) {
        val found = edge
        EdgeApps.problem(found)?.let { return say(it) }
        if (!EdgeApps.open(context, found!!, url, saved.edgeMode)) say("${found.build.name} didn't open the page.")
    }
    fun openEdge() {
        val found = edge
        EdgeApps.problem(found)?.let { return say(it) }
        if (!EdgeApps.launch(context, found!!)) say("${found.build.name} didn't open.")
    }
    var address by rememberSaveable { mutableStateOf("") }
    fun go() {
        // Edge opens http pages too, under its own rules; scripts and local files are still refused here.
        when (val target = BrowserRules.resolve(address, BrowserRules.searchTemplate(saved))) {
            null -> return
            is BrowserRules.Target.Load -> openUrl(target.url)
            is BrowserRules.Target.Insecure -> openUrl(target.url)
            is BrowserRules.Target.App -> return say("Type a web address or words to search.")
            is BrowserRules.Target.Blocked -> return say(target.reason)
        }
        address = ""; focus.clearFocus()
    }

    Box(Modifier.fillMaxSize().testTag("edgePage")) {
        Column(Modifier.fillMaxSize()) {
            Row(Modifier.fillMaxWidth().heightIn(min = 56.dp).padding(horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                if (onBack != null) IconButton(onClick = onBack, modifier = Modifier.testTag("moreBack")) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back to More") }
                else Spacer(Modifier.width(12.dp))
                Icon(ThreadIcons.Browser, null, Modifier.size(22.dp))
                Spacer(Modifier.width(10.dp))
                Column(Modifier.weight(1f)) {
                    Text(edge?.build?.name ?: "Microsoft Edge", style = MaterialTheme.typography.titleMedium, modifier = Modifier.semantics { heading() })
                    Text(when {
                        edge == null -> "Not installed on this phone"
                        edge?.genuine == false -> "Not signed by Microsoft"
                        edge?.enabled == false -> "Turned off in Android settings"
                        else -> "Version ${edge!!.version.ifBlank { "unknown" }} · opens Raticode's web pages"
                    }, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                TextButton(onClick = { browser.setEngine(BrowserRules.ENGINE_RATICODE) }, Modifier.testTag("useRaticodeBrowser")) { Text(BrowserTabs.CHROMIUM_NAME) }
            }
            HorizontalDivider()
            Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                val found = edge
                if (found == null || !found.usable) {
                    Column(Modifier.padding(horizontal = 16.dp).testTag("edgeMissing"), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        Icon(Icons.Filled.Warning, null, tint = MaterialTheme.colorScheme.error)
                        Text(EdgeApps.problem(found).orEmpty(), style = MaterialTheme.typography.titleMedium)
                        Text(when {
                            found == null || !found.genuine -> "Install Microsoft Edge from Google Play to browse with Edge here, sign in to your Microsoft account and use Edge's settings and extensions. Raticode finds it as soon as you come back."
                            else -> "Turn Edge back on in Android's app settings, then come back."
                        }, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        if (found == null || !found.genuine) Button(onClick = { if (!EdgeApps.getEdge(context)) say("This phone has no app store or browser to get Edge with.") }, Modifier.fillMaxWidth()) { Text("Get Microsoft Edge") }
                        else Button(onClick = { EdgeApps.appSettings(context, found) }, Modifier.fillMaxWidth()) { Text("Open Edge's app settings") }
                        OutlinedButton(onClick = { browser.setEngine(BrowserRules.ENGINE_RATICODE) }, Modifier.fillMaxWidth()) { Text("Use ${BrowserTabs.CHROMIUM_NAME} instead") }
                    }
                } else {
                    OutlinedTextField(address, { address = it.take(BrowserRules.MAX_TITLE * 4) }, singleLine = true,
                        placeholder = { Text("Search or type an address") }, label = { Text("Open in ${found.build.name}") },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Go, autoCorrectEnabled = false),
                        keyboardActions = KeyboardActions(onGo = { go() }),
                        trailingIcon = { TextButton(onClick = ::go, enabled = address.isNotBlank()) { Text("Go") } },
                        modifier = Modifier.padding(horizontal = 16.dp).fillMaxWidth().testTag("edgeAddress"))
                    // Edge keeps running in its own task; this brings it back with its tabs, and anything playing, untouched.
                    Button(onClick = ::openEdge, Modifier.padding(horizontal = 16.dp).fillMaxWidth().heightIn(min = 56.dp).testTag("edgeReturn")) {
                        Text("Return to ${found.build.name}")
                    }
                    // In split screen, Edge can take the other half while Raticode's bar stays on this one.
                    val split = remember(LocalConfiguration.current) { (context as? android.app.Activity)?.isInMultiWindowMode == true }
                    Row(Modifier.padding(horizontal = 16.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        if (split) OutlinedButton(onClick = { if (!EdgeApps.launchBeside(context, found)) say("${found.build.name} didn't open.") },
                            Modifier.weight(1f).heightIn(min = 48.dp).testTag("edgeBeside")) { Text("Edge beside Raticode") }
                        FilledTonalButton(onClick = ::openEdge, Modifier.weight(1f).heightIn(min = 48.dp).testTag("edgeSignIn")) { Text("Sign in") }
                    }
                    Text(EdgeApps.SIGN_IN.where, Modifier.padding(horizontal = 16.dp), style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(if (split) "Raticode and Edge share the screen, so the bar below stays in reach."
                        else "Edge stays open with its tabs and music while you use Raticode. To come back here from Edge, use Android's " +
                            "Recents: tap the square button twice, or swipe along the bottom edge. For both on one screen, open Recents, tap " +
                            "Raticode's icon and choose split screen.",
                        Modifier.padding(horizontal = 16.dp).testTag("edgeSwitchHelp"), style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text("Pages open", Modifier.padding(start = 16.dp, top = 4.dp), style = MaterialTheme.typography.labelLarge)
                    SingleChoiceSegmentedButtonRow(Modifier.padding(horizontal = 16.dp).fillMaxWidth()) {
                        listOf(EdgeApps.MODE_APP to "In the Edge app", EdgeApps.MODE_TAB to "Quick look").forEachIndexed { index, (mode, label) ->
                            SegmentedButton(saved.edgeMode == mode, { browser.setEdgeMode(mode) }, SegmentedButtonDefaults.itemShape(index, 2),
                                Modifier.testTag("edgeOpens-$mode")) { Text(label) }
                        }
                    }
                    Text(if (saved.edgeMode == EdgeApps.MODE_TAB)
                            "Quick look draws the page over Raticode for a moment: Raticode's bar is hidden until you close the page with ✕, which also stops anything it was playing."
                        else "Pages open as tabs in Edge, which keeps them, and their sound, while you switch to Raticode and back.",
                        Modifier.padding(horizontal = 16.dp), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    InsideRaticode("Edge", { browser.setEngine(BrowserRules.ENGINE_GECKO) }) { browser.setEngine(BrowserRules.ENGINE_RATICODE) }
                    SectionTitle("Your Edge")
                    Text("Edge doesn't let other apps open these screens directly, so each one opens Edge; then follow the steps shown.",
                        Modifier.padding(horizontal = 16.dp), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    EdgeApps.PLACES.forEach { item ->
                        MenuRow(item.label, detail = "${item.detail}. ${item.where}", icon = if (item == EdgeApps.SIGN_IN) ThreadIcons.Lock else null) { openEdge() }
                    }
                }
                if (client.paired) Box(Modifier.padding(horizontal = 16.dp)) {
                    SwitchRow("Open chat links in Edge", "Web links in Rem's chat and threads open in Edge instead of your phone's default browser.",
                        saved.linksInApp, browser::setLinksInApp)
                }
                if (client.paired) {
                    SectionTitle(computerTitle(computer))
                    ComputerTabs(browser, client, computer, limit = 8, open = { url -> openUrl(url) })
                }
                Text(buildString {
                    append("Edge keeps its own tabs, sign-ins, passwords, history, extensions and settings on this phone, and syncs them with your Microsoft account if you sign in. ")
                    append("Raticode doesn't see what you do there and never asks for your Microsoft account password. ")
                    append("To ask Rem about a page, use Share › Rem in Edge. ")
                    if (computer?.edge == true) append("Edge on your computer is a separate browser there; signing in here doesn't sign it in.")
                }, Modifier.padding(horizontal = 16.dp, vertical = 8.dp), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        SnackbarHost(snackbar, Modifier.align(Alignment.BottomCenter).padding(bottom = 12.dp))
    }
}

/** The Microsoft Edge row of Browser settings' Default browser choice, below the built-in browsers, Firefox and Chrome. */
@Composable
internal fun EdgeEngineChoice(browser: BrowserRuntime) {
    val context = LocalContext.current
    val edge = remember { EdgeApps.find(context) }
    val value = BrowserRules.ENGINE_EDGE
    val detail = edge?.let { "${it.build.name} ${it.version}, with its settings, extensions and Microsoft account" }
        ?: "Microsoft Edge isn't installed yet; choosing it offers to get it"
    Row(Modifier.fillMaxWidth().heightIn(min = 52.dp).combinedClickableRow { browser.setEngine(value) }.testTag("engine-$value"),
        verticalAlignment = Alignment.CenterVertically) {
        RadioButton(browser.saved.engine == value, { browser.setEngine(value) })
        Column { Text("Microsoft Edge", fontWeight = FontWeight.Medium); Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
    }
}
