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
import dev.raticode.mobile.browser.BrowserApps
import dev.raticode.mobile.browser.BrowserRules
import dev.raticode.mobile.browser.BrowserRuntime
import dev.raticode.mobile.browser.BrowserTabs
import dev.raticode.mobile.browser.FirefoxApps
import dev.raticode.mobile.live.DesktopBrowser
import dev.raticode.mobile.live.LiveClient
import kotlinx.coroutines.launch

/**
 * The Browser page with Firefox chosen. Android has no way to show Firefox inside another app, so this page hands
 * everything to Firefox for Android: addresses and searches, the computer's tabs, and Firefox's own screens (settings,
 * sign-in to a Mozilla account, add-ons, privacy, passwords, bookmarks and history). Firefox draws them, keeps their data
 * in its own profile and asks for any password itself; Raticode sees none of it.
 */
@RequiresApi(29)
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun FirefoxPage(client: LiveClient, browser: BrowserRuntime, onBack: (() -> Unit)?) {
    val context = LocalContext.current
    val focus = LocalFocusManager.current
    var firefox by remember { mutableStateOf(FirefoxApps.find(context)) }
    // Installing, turning on or removing Firefox happens in other apps; look again whenever Raticode comes back.
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    DisposableEffect(lifecycle) {
        val observer = LifecycleEventObserver { _, event -> if (event == Lifecycle.Event.ON_RESUME) firefox = FirefoxApps.find(context) }
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
    // A missing, turned-off or look-alike Firefox gets its own reason; only an Android refusal is "didn't open".
    fun openUrl(url: String) {
        val found = firefox
        FirefoxApps.problem(found)?.let { return say(it) }
        if (!FirefoxApps.open(context, found!!, url, saved.firefoxMode)) say("${found.build.name} didn't open the page. Try opening Firefox first.")
    }
    fun screen(host: String) {
        val found = firefox
        FirefoxApps.problem(found)?.let { return say(it) }
        if (!FirefoxApps.openScreen(context, found!!, host)) say("${found.build.name} didn't open that screen.")
    }
    var address by rememberSaveable { mutableStateOf("") }
    fun go() {
        // Firefox opens http pages too, under its own HTTPS-only setting; scripts and local files are still refused here.
        when (val target = BrowserRules.resolve(address, BrowserRules.searchTemplate(saved))) {
            null -> return
            is BrowserRules.Target.Load -> openUrl(target.url)
            is BrowserRules.Target.Insecure -> openUrl(target.url)
            is BrowserRules.Target.App -> return say("Type a web address or words to search.")
            is BrowserRules.Target.Blocked -> return say(target.reason)
        }
        address = ""; focus.clearFocus()
    }

    Box(Modifier.fillMaxSize().testTag("firefoxPage")) {
        Column(Modifier.fillMaxSize()) {
            Row(Modifier.fillMaxWidth().heightIn(min = 56.dp).padding(horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                if (onBack != null) IconButton(onClick = onBack, modifier = Modifier.testTag("moreBack")) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back to More") }
                else Spacer(Modifier.width(12.dp))
                Icon(ThreadIcons.Browser, null, Modifier.size(22.dp))
                Spacer(Modifier.width(10.dp))
                Column(Modifier.weight(1f)) {
                    Text(firefox?.build?.name ?: "Firefox", style = MaterialTheme.typography.titleMedium, modifier = Modifier.semantics { heading() })
                    Text(when {
                        firefox == null -> "Not installed on this phone"
                        firefox?.genuine == false -> "Not signed by Mozilla"
                        firefox?.enabled == false -> "Turned off in Android settings"
                        else -> "Version ${firefox!!.version.ifBlank { "unknown" }} · opens Raticode's web pages"
                    }, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                TextButton(onClick = { browser.setEngine(BrowserRules.ENGINE_RATICODE) }, Modifier.testTag("useRaticodeBrowser")) { Text(BrowserTabs.CHROMIUM_NAME) }
            }
            HorizontalDivider()
            Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                val found = firefox
                if (found == null || !found.usable) {
                    Column(Modifier.padding(horizontal = 16.dp).testTag("firefoxMissing"), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        Icon(Icons.Filled.Warning, null, tint = MaterialTheme.colorScheme.error)
                        Text(when { found == null -> FirefoxApps.MISSING; !found.genuine -> FirefoxApps.NOT_MOZILLA; else -> FirefoxApps.TURNED_OFF },
                            style = MaterialTheme.typography.titleMedium)
                        Text(when {
                            found == null || !found.genuine -> "Install Firefox for Android from Google Play to browse with Firefox here, sign in to your Mozilla account and use Firefox's settings and add-ons. Raticode finds it as soon as you come back."
                            else -> "Turn Firefox back on in Android's app settings, then come back."
                        }, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        if (found == null || !found.genuine) Button(onClick = { if (!FirefoxApps.getFirefox(context)) say("This phone has no app store or browser to get Firefox with.") }, Modifier.fillMaxWidth()) { Text("Get Firefox") }
                        else Button(onClick = { FirefoxApps.appSettings(context, found) }, Modifier.fillMaxWidth()) { Text("Open Firefox's app settings") }
                        OutlinedButton(onClick = { browser.setEngine(BrowserRules.ENGINE_RATICODE) }, Modifier.fillMaxWidth()) { Text("Use ${BrowserTabs.CHROMIUM_NAME} instead") }
                    }
                } else {
                    OutlinedTextField(address, { address = it.take(BrowserRules.MAX_TITLE * 4) }, singleLine = true,
                        placeholder = { Text("Search or type an address") }, label = { Text("Open in ${found.build.name}") },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Go, autoCorrectEnabled = false),
                        keyboardActions = KeyboardActions(onGo = { go() }),
                        trailingIcon = { TextButton(onClick = ::go, enabled = address.isNotBlank()) { Text("Go") } },
                        modifier = Modifier.padding(horizontal = 16.dp).fillMaxWidth().testTag("firefoxAddress"))
                    // Firefox keeps running in its own task; this brings it back with its tabs, and anything playing, untouched.
                    Button(onClick = { if (!FirefoxApps.launch(context, found)) say("${found.build.name} didn't open.") },
                        Modifier.padding(horizontal = 16.dp).fillMaxWidth().heightIn(min = 56.dp).testTag("firefoxReturn")) { Text("Return to ${found.build.name}") }
                    // In split screen, Firefox can take the other half while Raticode's bar stays on this one.
                    val split = remember(LocalConfiguration.current) { (context as? android.app.Activity)?.isInMultiWindowMode == true }
                    Row(Modifier.padding(horizontal = 16.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        if (split) OutlinedButton(onClick = { if (!FirefoxApps.launchBeside(context, found)) say("${found.build.name} didn't open.") },
                            Modifier.weight(1f).heightIn(min = 48.dp).testTag("firefoxBeside")) { Text("${found.build.name} beside Raticode") }
                        FilledTonalButton(onClick = { screen(FirefoxApps.SIGN_IN.host) }, Modifier.weight(1f).heightIn(min = 48.dp).testTag("firefoxSignIn")) { Text("Sign in") }
                    }
                    Text(if (split) "Raticode and Firefox share the screen, so the bar below stays in reach."
                        else "Firefox stays open with its tabs and music while you use Raticode. To come back here from Firefox, use Android's " +
                            "Recents: tap the square button twice, or swipe along the bottom edge. For both on one screen, open Recents, tap " +
                            "Raticode's icon and choose split screen.",
                        Modifier.padding(horizontal = 16.dp).testTag("firefoxSwitchHelp"), style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text("Pages open", Modifier.padding(start = 16.dp, top = 4.dp), style = MaterialTheme.typography.labelLarge)
                    SingleChoiceSegmentedButtonRow(Modifier.padding(horizontal = 16.dp).fillMaxWidth()) {
                        listOf(FirefoxApps.MODE_APP to "In the Firefox app", FirefoxApps.MODE_TAB to "Quick look").forEachIndexed { index, (mode, label) ->
                            SegmentedButton(saved.firefoxMode == mode, { browser.setFirefoxMode(mode) }, SegmentedButtonDefaults.itemShape(index, 2),
                                Modifier.testTag("firefoxOpens-$mode")) { Text(label) }
                        }
                    }
                    Text(if (saved.firefoxMode == FirefoxApps.MODE_TAB)
                            "Quick look draws the page over Raticode for a moment: Raticode's bar is hidden until you close the page with ✕, which also stops anything it was playing."
                        else "Pages open as tabs in Firefox, which keeps them, and their sound, while you switch to Raticode and back.",
                        Modifier.padding(horizontal = 16.dp), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    InsideRaticode(found.build.name, { browser.setEngine(BrowserRules.ENGINE_GECKO) }) { browser.setEngine(BrowserRules.ENGINE_RATICODE) }
                    SectionTitle("Your Firefox")
                    FirefoxApps.SCREENS.forEach { item ->
                        MenuRow(item.label, detail = item.detail, icon = if (item == FirefoxApps.SIGN_IN) ThreadIcons.Lock else null) { screen(item.host) }
                    }
                }
                if (client.paired) Box(Modifier.padding(horizontal = 16.dp)) {
                    SwitchRow("Open chat links in Firefox", "Web links in Rem's chat and threads open in Firefox instead of your phone's default browser.",
                        saved.linksInApp, browser::setLinksInApp)
                }
                if (client.paired) {
                    SectionTitle(computerTitle(computer))
                    ComputerTabs(browser, client, computer, limit = 8, open = { url -> openUrl(url) })
                }
                Text(buildString {
                    append("Firefox keeps its own tabs, sign-ins, passwords, history, add-ons and settings on this phone, and syncs them with your Mozilla account if you sign in. ")
                    append("Raticode doesn't see what you do there and never asks for your Firefox password. ")
                    append("To ask Rem about a page, use Share › Rem in Firefox. ")
                    if (computer?.firefox == true) append("Firefox on your computer is a separate profile that Raticode runs there; signing in here doesn't sign it in.")
                }, Modifier.padding(horizontal = 16.dp, vertical = 8.dp), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        SnackbarHost(snackbar, Modifier.align(Alignment.BottomCenter).padding(bottom = 12.dp))
    }
}

/**
 * The default browser for new tabs, chat links and the Browser page: Raticode (chromium), Raticode (gecko), Firefox or one
 * of [BrowserApps] such as Chrome. Microsoft Edge's row ([EdgeEngineChoice]) follows. The stored values ("raticode",
 * "gecko", ...) never change; only the names shown do.
 */
@Composable
internal fun EngineChoice(browser: BrowserRuntime) {
    val context = LocalContext.current
    val firefox = remember { FirefoxApps.find(context) }
    Text("Default browser", style = MaterialTheme.typography.titleSmall)
    Text("New tabs and chat links open here. Tabs of ${BrowserTabs.CHROMIUM_NAME} and ${BrowserTabs.GECKO_NAME} share one tab list.",
        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    (listOf(BrowserRules.ENGINE_RATICODE to BrowserTabs.CHROMIUM_NAME to "Built into this app, on Android's WebView (Chromium)",
        BrowserRules.ENGINE_GECKO to BrowserTabs.GECKO_NAME to "Built into this app, on Mozilla's Gecko engine, with Firefox extensions",
        BrowserRules.ENGINE_FIREFOX to "Firefox" to (firefox?.let { "${it.build.name} ${it.version}, with its settings, add-ons and Mozilla account" }
            ?: "Firefox for Android isn't installed yet; choosing it offers to get it")) + remember { appEngineChoices(context) }).forEach { (choice, detail) ->
        val (value, label) = choice
        Row(Modifier.fillMaxWidth().heightIn(min = 52.dp).combinedClickableRow { browser.setEngine(value) }.testTag("engine-$value"),
            verticalAlignment = Alignment.CenterVertically) {
            RadioButton(browser.saved.engine == value, { browser.setEngine(value) })
            Column { Text(label, fontWeight = FontWeight.Medium); Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
        }
    }
}
