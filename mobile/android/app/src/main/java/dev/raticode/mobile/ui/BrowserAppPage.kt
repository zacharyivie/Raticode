package dev.raticode.mobile.ui

import android.content.Context
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import dev.raticode.mobile.browser.BrowserApp
import dev.raticode.mobile.browser.BrowserApps
import dev.raticode.mobile.browser.BrowserRules
import dev.raticode.mobile.browser.BrowserRuntime
import dev.raticode.mobile.browser.BrowserTabs
import dev.raticode.mobile.live.DesktopBrowser
import dev.raticode.mobile.live.LiveClient
import kotlinx.coroutines.launch

/**
 * The Browser page with Chrome (or another of [BrowserApps]) chosen. Android can't show another app inside Raticode,
 * so this page hands addresses, searches and the computer's tabs to the app itself, and offers what the app really
 * allows another app to do: open it, open its maker's sign-in page in it, its Android app settings and its Google
 * Play page. Chrome has no links to its own settings and no extensions; the page says so rather than pretending.
 * The app keeps its data in its own profile and asks for any password itself; Raticode sees none of it.
 * Test tags start with the app's engine ("chromePage", "chromeAddress", "chromeSignIn", ...).
 */
@RequiresApi(29)
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun BrowserAppPage(client: LiveClient, browser: BrowserRuntime, app: BrowserApp, onBack: (() -> Unit)?) {
    val context = LocalContext.current
    val focus = LocalFocusManager.current
    val tag = app.engine
    var found by remember(app) { mutableStateOf(app.find(context)) }
    // Installing, turning on or removing the app happens in other apps; look again whenever Raticode comes back.
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    DisposableEffect(lifecycle, app) {
        val observer = LifecycleEventObserver { _, event -> if (event == Lifecycle.Event.ON_RESUME) found = app.find(context) }
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
    val mode = BrowserRules.appMode(saved, app.engine)
    // A missing, turned-off or look-alike app gets its own reason; only an Android refusal is "didn't open".
    fun openUrl(url: String) {
        val current = found
        app.problem(current)?.let { return say(it) }
        if (!app.open(context, current!!, url, mode)) say("${current.edition.name} didn't open the page.")
    }
    var address by rememberSaveable { mutableStateOf("") }
    fun go() {
        // The app opens http pages too, under its own rules; scripts and local files are still refused here.
        when (val target = BrowserRules.resolve(address, BrowserRules.searchTemplate(saved))) {
            null -> return
            is BrowserRules.Target.Load -> openUrl(target.url)
            is BrowserRules.Target.Insecure -> openUrl(target.url)
            is BrowserRules.Target.App -> return say("Type a web address or words to search.")
            is BrowserRules.Target.Blocked -> return say(target.reason)
        }
        address = ""; focus.clearFocus()
    }
    val name = found?.edition?.name ?: app.name

    Box(Modifier.fillMaxSize().testTag("${tag}Page")) {
        Column(Modifier.fillMaxSize()) {
            Row(Modifier.fillMaxWidth().heightIn(min = 56.dp).padding(horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                if (onBack != null) IconButton(onClick = onBack, modifier = Modifier.testTag("moreBack")) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back to More") }
                else Spacer(Modifier.width(12.dp))
                Icon(ThreadIcons.Browser, null, Modifier.size(22.dp))
                Spacer(Modifier.width(10.dp))
                Column(Modifier.weight(1f)) {
                    Text(name, style = MaterialTheme.typography.titleMedium, modifier = Modifier.semantics { heading() })
                    Text(when {
                        found == null -> "Not installed on this phone"
                        found?.genuine == false -> "Not signed by ${app.maker}"
                        found?.enabled == false -> "Turned off in Android settings"
                        else -> "Version ${found!!.version.ifBlank { "unknown" }} · opens Raticode's web pages"
                    }, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                TextButton(onClick = { browser.setEngine(BrowserRules.ENGINE_RATICODE) }, Modifier.testTag("useRaticodeBrowser")) { Text(BrowserTabs.CHROMIUM_NAME) }
            }
            HorizontalDivider()
            Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                val current = found
                if (current == null || !current.usable) {
                    Column(Modifier.padding(horizontal = 16.dp).testTag("${tag}Missing"), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        Icon(Icons.Filled.Warning, null, tint = MaterialTheme.colorScheme.error)
                        Text(app.problem(current).orEmpty(), style = MaterialTheme.typography.titleMedium)
                        Text(when {
                            current == null -> "Install ${app.name} from Google Play to browse with it here. Raticode finds it as soon as you come back."
                            !current.genuine -> "Remove this app in Android's app settings and install ${app.name} from Google Play. Raticode finds it as soon as you come back."
                            else -> "Turn ${app.name} back on in Android's app settings, then come back."
                        }, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        if (current == null || !current.genuine) Button(onClick = { if (!app.getApp(context)) say("This phone has no app store or browser to get ${app.name} with.") },
                            Modifier.fillMaxWidth()) { Text("Get ${app.name}") }
                        if (current != null) {
                            val label = "Open ${current.edition.name}'s app settings"
                            if (current.genuine) Button(onClick = { app.appSettings(context, current) }, Modifier.fillMaxWidth()) { Text(label) }
                            else OutlinedButton(onClick = { app.appSettings(context, current) }, Modifier.fillMaxWidth()) { Text(label) }
                        }
                        OutlinedButton(onClick = { browser.setEngine(BrowserRules.ENGINE_RATICODE) }, Modifier.fillMaxWidth()) { Text("Use ${BrowserTabs.CHROMIUM_NAME} instead") }
                    }
                } else {
                    OutlinedTextField(address, { address = it.take(BrowserRules.MAX_TITLE * 4) }, singleLine = true,
                        placeholder = { Text("Search or type an address") }, label = { Text("Open in $name") },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Go, autoCorrectEnabled = false),
                        keyboardActions = KeyboardActions(onGo = { go() }),
                        trailingIcon = { TextButton(onClick = ::go, enabled = address.isNotBlank()) { Text("Go") } },
                        modifier = Modifier.padding(horizontal = 16.dp).fillMaxWidth().testTag("${tag}Address"))
                    Row(Modifier.padding(horizontal = 16.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        // The launcher entry brings back the app's own task, with its tabs, where the user left it.
                        Button(onClick = { if (!app.launch(context, current)) say("$name didn't open.") }, Modifier.weight(1f).heightIn(min = 48.dp)
                            .testTag("${tag}Open")) { Text("Open ${app.name}") }
                        app.signIn?.let { signIn ->
                            FilledTonalButton(onClick = { openUrl(signIn.url) }, Modifier.weight(1f).heightIn(min = 48.dp).testTag("${tag}SignIn")) { Text("Sign in") }
                        }
                    }
                    Text("Pages open", Modifier.padding(start = 16.dp, top = 4.dp), style = MaterialTheme.typography.labelLarge)
                    SingleChoiceSegmentedButtonRow(Modifier.padding(horizontal = 16.dp).fillMaxWidth()) {
                        listOf(BrowserApps.MODE_APP to "In the ${app.name} app", BrowserApps.MODE_TAB to "Quick look").forEachIndexed { index, (choice, label) ->
                            SegmentedButton(mode == choice, { browser.setAppMode(app.engine, choice) }, SegmentedButtonDefaults.itemShape(index, 2),
                                Modifier.testTag("${tag}Opens-$choice")) { Text(label) }
                        }
                    }
                    Text(if (mode == BrowserApps.MODE_TAB)
                            "Quick look: ${app.name} draws the page on top of Raticode, hiding Raticode's bar, until you close it with ✕ or Back, which also stops anything it was playing."
                        else "Pages open as tabs in ${app.name}, which keeps them, and their sound, while you switch back to Raticode with Android's Recents.",
                        Modifier.padding(horizontal = 16.dp), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    InsideRaticode(app.name, { browser.setEngine(BrowserRules.ENGINE_GECKO) }) { browser.setEngine(BrowserRules.ENGINE_RATICODE) }
                    SectionTitle("Your ${app.name}")
                    app.signIn?.let { signIn -> MenuRow(signIn.label, detail = signIn.detail, icon = ThreadIcons.Lock) { openUrl(signIn.url) } }
                    MenuRow("${app.name}'s app settings", detail = "Android's settings for ${current.edition.name}: notifications, permissions, storage and default browser") {
                        if (!app.appSettings(context, current)) say("Android didn't open ${current.edition.name}'s app settings.")
                    }
                    MenuRow("${app.name} on Google Play", detail = "Updates and the store page") {
                        if (!app.getApp(context)) say("This phone has no app store or browser to open Google Play with.")
                    }
                }
                if (client.paired) Box(Modifier.padding(horizontal = 16.dp)) {
                    SwitchRow("Open chat links in ${app.name}", "Web links in Rem's chat and threads open in ${app.name} instead of your phone's default browser.",
                        saved.linksInApp, browser::setLinksInApp)
                }
                if (client.paired) {
                    SectionTitle(computerTitle(computer))
                    ComputerTabs(browser, client, computer, limit = 8, open = { url -> openUrl(url) })
                }
                Text(buildString {
                    append("${app.name} keeps its own tabs, sign-ins, passwords, history and settings on this phone, and syncs them with your ${app.maker} account if you sign in. ")
                    append("Raticode doesn't see what you do there and never asks for your ${app.maker} password. ")
                    append(app.limits).append(' ')
                    append("To ask Rem about a page, use Share › Rem in ${app.name}. ")
                    if (computer?.engine == app.engine) append("${app.name} on your computer is a separate profile that Raticode runs there; signing in here doesn't sign it in.")
                }, Modifier.padding(horizontal = 16.dp, vertical = 8.dp).testTag("${tag}About"), style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        SnackbarHost(snackbar, Modifier.align(Alignment.BottomCenter).padding(bottom = 12.dp))
    }
}

/** Browser settings' rows for each of [BrowserApps], as ((engine, label), detail), in the shape [EngineChoice] lists. */
internal fun appEngineChoices(context: Context): List<Pair<Pair<String, String>, String>> = BrowserApps.ALL.map { app ->
    val found = app.find(context)
    app.engine to app.name to when {
        found == null -> "${app.name} for Android isn't installed yet; choosing it offers to get it"
        !found.genuine -> "The ${app.name} on this phone isn't signed by ${app.maker}; choosing it explains"
        else -> "${found.edition.name} ${found.version}, with its own sign-in, passwords and sync"
    }
}

/**
 * Says plainly what "inside Raticode" can mean with [name] chosen, and offers it. Android shows another app's screens only
 * in that app's own window (a Custom Tab or the app itself), never inside Raticode's, so Raticode's bar and tab list can't
 * appear over [name]'s pages. Raticode (chromium) and Raticode (gecko) are the browsers that run inside Raticode.
 */
@Composable
internal fun InsideRaticode(name: String, useGecko: () -> Unit, useRaticode: () -> Unit) {
    OutlinedCard(Modifier.padding(horizontal = 16.dp).fillMaxWidth().testTag("insideRaticode")) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Browsing inside Raticode", style = MaterialTheme.typography.titleSmall)
            Text("Android doesn't let $name draw inside another app, so Raticode's bar and tabs can't show on $name's pages. " +
                "${BrowserTabs.CHROMIUM_NAME} and ${BrowserTabs.GECKO_NAME} open pages inside Raticode, in one tab list with up to ${BrowserRules.MAX_TABS} tabs each, " +
                "with Raticode's bar in reach, and a page's music keeps playing while you switch tabs or go to Rem or Threads. " +
                "${BrowserTabs.GECKO_NAME} is Mozilla's engine and runs Firefox extensions. Both have their own sign-ins, separate from $name's.",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Button(onClick = useGecko, Modifier.fillMaxWidth().heightIn(min = 48.dp).testTag("browseInGecko")) { Text("Use ${BrowserTabs.GECKO_NAME}, with Firefox extensions") }
            OutlinedButton(onClick = useRaticode, Modifier.fillMaxWidth().heightIn(min = 48.dp).testTag("browseInRaticode")) { Text("Use ${BrowserTabs.CHROMIUM_NAME}") }
        }
    }
}
