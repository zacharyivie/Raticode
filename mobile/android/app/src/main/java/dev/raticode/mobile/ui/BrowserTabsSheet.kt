package dev.raticode.mobile.ui

import android.content.Context
import androidx.annotation.RequiresApi
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.browser.BrowserApps
import dev.raticode.mobile.browser.BrowserRules
import dev.raticode.mobile.browser.BrowserRuntime
import dev.raticode.mobile.browser.BrowserTabs
import dev.raticode.mobile.browser.EdgeApps
import dev.raticode.mobile.browser.FirefoxApps
import dev.raticode.mobile.browser.GeckoBrowser
import dev.raticode.mobile.live.BrowserAddress
import dev.raticode.mobile.live.DesktopBrowser
import dev.raticode.mobile.live.LiveClient

/** Both built-in browsers' tabs, as the shared list shows them. Reading them here redraws the list when either changes. */
internal fun allTabs(chromium: BrowserRuntime, gecko: GeckoBrowser, showing: String): List<BrowserTabs.Entry> {
    val chromiumTabs = chromium.tabs.map { tab ->
        val page = chromium.pages[tab.id]
        BrowserTabs.Tab(BrowserTabs.CHROMIUM, tab.id, page?.url ?: tab.url, page?.title ?: tab.title, tab.private, tab.id in chromium.playing)
    }
    val geckoTabs = gecko.tabs.map { tab ->
        val page = gecko.pages[tab.id]
        BrowserTabs.Tab(BrowserTabs.GECKO, tab.id, page?.url ?: tab.url, page?.title ?: tab.title, tab.private, tab.id in gecko.playing)
    }
    return BrowserTabs.list(chromiumTabs, geckoTabs, showing, chromium.active, gecko.active)
}

/** The short name of the built-in browser a tab runs in, as a small label. TalkBack reads the full name with the row. */
@Composable
internal fun EngineBadge(engine: String, modifier: Modifier = Modifier) {
    val gecko = engine == BrowserTabs.GECKO
    Surface(shape = RoundedCornerShape(6.dp), modifier = modifier.clearAndSetSemantics { contentDescription = BrowserTabs.name(engine) },
        color = if (gecko) MaterialTheme.colorScheme.tertiaryContainer else MaterialTheme.colorScheme.secondaryContainer,
        contentColor = if (gecko) MaterialTheme.colorScheme.onTertiaryContainer else MaterialTheme.colorScheme.onSecondaryContainer) {
        Text(BrowserTabs.badge(engine), Modifier.padding(horizontal = 6.dp, vertical = 2.dp), style = MaterialTheme.typography.labelSmall, maxLines = 1)
    }
}

/** A separate browser app Raticode can hand the page on screen to: its name, and how to open a page in it. */
private class OtherBrowser(val engine: String, val name: String, val open: (Context, String) -> Boolean)

/** Firefox, Chrome and Edge as installed, signed by their makers and turned on, each opening pages the way the user chose for it. */
private fun otherBrowsers(context: Context, saved: BrowserRules.Saved): List<OtherBrowser> = buildList {
    FirefoxApps.find(context)?.takeIf { it.usable }?.let { found ->
        add(OtherBrowser(BrowserRules.ENGINE_FIREFOX, found.build.name) { c, url -> FirefoxApps.open(c, found, url, saved.firefoxMode) })
    }
    BrowserApps.ALL.forEach { app ->
        app.find(context)?.takeIf { it.usable }?.let { found ->
            add(OtherBrowser(app.engine, app.name) { c, url -> app.open(c, found, url, BrowserRules.appMode(saved, app.engine)) })
        }
    }
    EdgeApps.find(context)?.takeIf { it.usable }?.let { found ->
        add(OtherBrowser(BrowserRules.ENGINE_EDGE, "Edge") { c, url -> EdgeApps.open(c, found, url, saved.edgeMode) })
    }
}

/**
 * The tab list of the Browser page, shared by Raticode (chromium) and Raticode (gecko): every tab of both, each with a badge
 * naming its browser. Picking a tab of the other browser switches the page to that browser; the tabs left behind stay
 * open, and their music keeps playing. New tabs open in the browser chosen at the top, which starts at the default
 * browser. Firefox, Chrome and Edge are separate apps whose tabs Raticode can't hold, so they can only take the page on
 * screen. [showing] is the browser on screen; the computer's tabs show when the phone is paired.
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@RequiresApi(29)
@Composable
internal fun AllTabsSheet(chromium: BrowserRuntime, gecko: GeckoBrowser, client: LiveClient, computer: DesktopBrowser.View?, showing: String, onDismiss: () -> Unit) {
    val context = LocalContext.current
    val entries = allTabs(chromium, gecko, showing)
    var newIn by remember { mutableStateOf(BrowserRules.newTabEngine(chromium.saved)) }
    val others = remember { otherBrowsers(context, chromium.saved) }
    val geckoIn = newIn == BrowserTabs.GECKO
    // Notices go to the browser on screen, which shows them; the other one's would wait until it was shown.
    fun say(text: String?) { if (text == null) return; if (showing == BrowserTabs.GECKO) gecko.notice = text else chromium.notice = text }
    fun opened(engine: String, id: String?): Boolean {
        if (id == null) {
            if (engine != showing) { if (engine == BrowserTabs.GECKO) { say(gecko.notice); gecko.notice = null } else { say(chromium.notice); chromium.notice = null } }
            return false
        }
        chromium.show(engine)
        return true
    }
    fun openNew(private: Boolean, url: String? = null): Boolean =
        if (geckoIn) opened(BrowserTabs.GECKO, gecko.newTab(url, private = private))
        else opened(BrowserTabs.CHROMIUM, if (private) chromium.newPrivateTab() else chromium.newTab(url))
    fun select(entry: BrowserTabs.Entry) {
        if (entry.tab.engine == BrowserTabs.GECKO) gecko.select(entry.tab.id) else chromium.select(entry.tab.id)
        chromium.show(entry.tab.engine)
        onDismiss()
    }
    fun close(entry: BrowserTabs.Entry) { if (entry.tab.engine == BrowserTabs.GECKO) gecko.close(entry.tab.id) else chromium.close(entry.tab.id) }
    val onScreen = entries.firstOrNull { it.current }?.tab
    val sendable = onScreen?.takeIf { !it.private && BrowserAddress.isWeb(it.url) }?.url
    val list = rememberLazyListState(initialFirstVisibleItemIndex = BrowserTabs.currentIndex(entries))

    ModalBottomSheet(onDismissRequest = onDismiss, modifier = Modifier.testTag(if (showing == BrowserTabs.GECKO) "geckoTabsSheet" else "browserTabsSheet")) {
        Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp)) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text("Tabs on this phone", Modifier.weight(1f).semantics { heading() }, style = MaterialTheme.typography.titleMedium)
                TextButton(onClick = { if (openNew(private = true)) onDismiss() }, Modifier.testTag(if (geckoIn) "geckoNewPrivateTab" else "browserNewPrivateTab")) { Text("Private") }
                TextButton(onClick = { if (openNew(private = false)) onDismiss() }, Modifier.testTag(if (geckoIn) "geckoNewTab" else "browserNewTab")) { Text("New tab") }
            }
            Text("New tabs open in", style = MaterialTheme.typography.labelLarge)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf(BrowserTabs.CHROMIUM, BrowserTabs.GECKO).forEach { engine ->
                    FilterChip(newIn == engine, { newIn = engine }, label = { Text(BrowserTabs.name(engine)) }, modifier = Modifier.testTag("newTabIn-$engine"))
                }
            }
            if (sendable != null && others.isNotEmpty()) {
                Text("Open this page in", style = MaterialTheme.typography.labelLarge)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    others.forEach { other ->
                        AssistChip(onClick = { if (other.open(context, sendable)) onDismiss() else say("${other.name} didn't open the page.") },
                            label = { Text(other.name) }, modifier = Modifier.testTag("sendTabTo-${other.engine}"))
                    }
                }
                Text("Firefox, Chrome and Edge are separate apps on Android, so their tabs stay in those apps.",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            if (gecko.saved.profiles.size > 1) Text("${BrowserTabs.GECKO_NAME} tabs are from the ${gecko.profile.name} profile.",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        LazyColumn(Modifier.fillMaxWidth().padding(bottom = 16.dp).testTag("allTabsList"), state = list) {
            items(entries, key = { it.key }) { entry -> TabRow(entry, chromium, gecko, onSelect = { select(entry) }, onClose = { close(entry) }) }
            item {
                val canReopen = if (geckoIn) gecko.canReopen else chromium.saved.closed.isNotEmpty()
                if (canReopen) TextButton(onClick = {
                    if (geckoIn) { if (gecko.reopenClosed()) chromium.show(BrowserTabs.GECKO) } else { if (chromium.reopenClosed()) chromium.show(BrowserTabs.CHROMIUM) }
                    onDismiss()
                }, Modifier.padding(horizontal = 8.dp).testTag(if (geckoIn) "geckoReopenClosed" else "browserReopenClosed")) {
                    Text("Reopen closed ${BrowserTabs.name(newIn)} tab")
                }
            }
            if (client.paired) {
                item { SectionTitle(computerTitle(computer)) }
                item { ComputerTabs(chromium, client, computer, onOpened = onDismiss, open = { url -> openNew(private = false, url = url) }) }
            }
        }
    }
}

@Composable
private fun TabRow(entry: BrowserTabs.Entry, chromium: BrowserRuntime, gecko: GeckoBrowser, onSelect: () -> Unit, onClose: () -> Unit) {
    val tab = entry.tab
    val title = BrowserTabs.title(tab)
    // The tags existing tests know: each browser's tabs by their place in that browser's own list.
    val tag = if (tab.engine == BrowserTabs.GECKO) "geckoTab-${gecko.tabs.indexOfFirst { it.id == tab.id }}" else "browserTab-${chromium.tabs.indexOfFirst { it.id == tab.id }}"
    Surface(color = if (entry.current) MaterialTheme.colorScheme.secondaryContainer.copy(alpha = 0.5f) else MaterialTheme.colorScheme.surface) {
        Row(Modifier.fillMaxWidth().heightIn(min = 56.dp).combinedClickableRow(onSelect).padding(start = 16.dp).testTag(tag)
            .semantics { stateDescription = if (entry.current) "Current tab" else "" }, verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(title, maxLines = 1, overflow = TextOverflow.Ellipsis, fontWeight = if (entry.current) FontWeight.SemiBold else FontWeight.Normal)
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    EngineBadge(tab.engine)
                    BrowserTabs.detail(entry)?.let {
                        Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                }
            }
            if (tab.playing) Icon(ThreadIcons.Music, "Playing", Modifier.size(18.dp))
            IconButton(onClick = onClose) { Icon(Icons.Filled.Close, "Close tab $title") }
        }
    }
}
