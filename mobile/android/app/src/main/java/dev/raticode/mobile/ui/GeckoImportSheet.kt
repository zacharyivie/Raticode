package dev.raticode.mobile.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalWindowInfo
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.browser.FirefoxApps
import dev.raticode.mobile.browser.GeckoBrowser
import dev.raticode.mobile.browser.GeckoImport
import dev.raticode.mobile.browser.GeckoImporter

/** Brings extensions and settings over from Firefox on this phone, or from a computer's Firefox: review, then import what is ticked. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun GeckoImportSheet(gecko: GeckoBrowser, onDismiss: () -> Unit) {
    val importer = gecko.importer
    val preview = importer.preview
    val context = LocalContext.current
    val muted = MaterialTheme.colorScheme.onSurfaceVariant
    LaunchedEffect(gecko) { gecko.refreshExtensions(); importer.findFirefox() }
    val firefox = importer.phoneFirefox
    val usable = firefox?.usable == true
    val fromPhone = GeckoImport.PHONE_SOURCE in preview.sources
    var others by rememberSaveable { mutableStateOf(false) }
    val showOthers = others || !usable // With no usable Firefox here, a computer's Firefox is the only source.
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true), modifier = Modifier.testTag("geckoImportSheet")) {
        PickUpOnReturn(importer)
        LazyColumn(Modifier.fillMaxWidth().padding(bottom = 16.dp).testTag("geckoImportList")) {
            item {
                Column(Modifier.padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("Import from Firefox", Modifier.semantics { heading() }, style = MaterialTheme.typography.titleMedium)
                    when {
                        importer.waitingForFirefox -> WaitingForFirefox(importer, context)
                        fromPhone -> Text("From ${firefox?.build?.name ?: "Firefox"} on this phone. Untick anything you don't want, then tap Import.", style = MaterialTheme.typography.bodySmall, color = muted)
                        usable -> FoundFirefox(firefox, onStart = { importer.startFromFirefox(context) })
                        else -> Text(FirefoxApps.problem(firefox) ?: FirefoxApps.MISSING, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.testTag("geckoImportNoFirefox"))
                    }
                    if (importer.busy > 0) LinearProgressIndicator(Modifier.fillMaxWidth().testTag("geckoImportBusy"))
                    importer.error?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall, modifier = Modifier.testTag("geckoImportError")) }
                }
            }
            if (preview.extensions.isNotEmpty()) {
                item { SectionTitle("Extensions") }
                items(preview.extensions, key = { "e-" + it.id }) { found ->
                    val listing = importer.listings[found.id]
                    val added = gecko.extensions.any { it.id == found.id }
                    val detail = when {
                        added -> "Already added"
                        listing != null && listing.android -> "Made for Android"
                        listing != null -> "Desktop extension, may only partly work"
                        found.id in importer.checked -> "Not on Firefox Add-ons, so it can't be added"
                        else -> "Checking Firefox Add-ons…"
                    } + if (!added && !found.active) " · Turned off in Firefox" else ""
                    val can = importer.addable(found.id)
                    Row(Modifier.fillMaxWidth().heightIn(min = 52.dp).padding(horizontal = 8.dp).testTag("geckoImportExt-${found.id}"), verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(can && found.id !in importer.unticked, { importer.toggle(found.id) }, enabled = can)
                        Column(Modifier.weight(1f)) {
                            Text(found.name, fontWeight = FontWeight.Medium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(detail, style = MaterialTheme.typography.labelSmall, color = muted)
                        }
                    }
                }
            }
            if (preview.settings.isNotEmpty()) {
                item { SectionTitle("Settings") }
                items(preview.settings, key = { "s-" + it.key }) { change ->
                    Row(Modifier.fillMaxWidth().heightIn(min = 52.dp).padding(horizontal = 8.dp).testTag("geckoImportSetting-${change.key}"), verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(change.key !in importer.unticked, { importer.toggle(change.key) })
                        Column(Modifier.weight(1f)) {
                            Text(change.label, fontWeight = FontWeight.Medium)
                            Text("${change.from} → ${change.to}", style = MaterialTheme.typography.labelSmall, color = muted)
                        }
                    }
                }
            }
            if (preview.bookmarks.isNotEmpty()) item {
                SectionTitle("Bookmarks")
                Row(Modifier.fillMaxWidth().heightIn(min = 52.dp).padding(horizontal = 8.dp).testTag("geckoImportFavorites"), verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(importer.favorites, { importer.favorites = it })
                    Column(Modifier.weight(1f)) {
                        Text("Add ${preview.bookmarks.size} to ${gecko.profile.name}'s favorites", fontWeight = FontWeight.Medium)
                        Text("Your favorites stay; folders aren't kept", style = MaterialTheme.typography.labelSmall, color = muted)
                    }
                }
            }
            if (!GeckoImport.isEmpty(preview)) item {
                Button(onClick = { importer.run(); onDismiss() }, enabled = importer.busy == 0, modifier = Modifier.padding(16.dp).fillMaxWidth().heightIn(min = 48.dp).testTag("geckoImportRun")) { Text("Import") }
            }
            item {
                Column(Modifier.padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (usable) TextButton(onClick = { others = !others }, Modifier.heightIn(min = 48.dp).testTag("geckoImportOther")) { Text(if (others) "Hide other ways" else "From a computer or a collection") }
                    if (showOthers) OtherSources(importer)
                }
            }
            if (importer.hasResults) {
                item { SectionTitle("Not brought over") }
                items((preview.notes + GeckoImport.ALWAYS_UNAVAILABLE + if (fromPhone) GeckoImport.PHONE_UNAVAILABLE else emptyList()), key = { "n-" + it.what }) { note -> NoteRow(note) }
                if (preview.builtIn > 0) item { NoteRow(GeckoImport.Note("${preview.builtIn} parts of Firefox itself", "They appear in Firefox's list but aren't extensions you added.")) }
            }
        }
    }
}

/**
 * Looks at the clipboard when the user comes back from Firefox; Android only shows an app the clipboard while it has
 * focus. Must be called inside the sheet: the sheet is its own Android window and takes the focus, so the window under
 * it never gets focus back while the sheet is open.
 */
@Composable
private fun PickUpOnReturn(importer: GeckoImporter) {
    val window = LocalWindowInfo.current
    LaunchedEffect(importer.waitingForFirefox) {
        if (importer.waitingForFirefox) GeckoImport.onFocusBack(snapshotFlow { window.isWindowFocused }) { importer.checkClipboard() }
    }
}

@Composable
private fun FoundFirefox(firefox: FirefoxApps.Found?, onStart: () -> Unit) {
    Text("${firefox?.build?.name} ${firefox?.version} is on this phone", style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium, modifier = Modifier.testTag("geckoImportFound"))
    Text("Android keeps each app's data private, so Raticode can't read Firefox on its own. In Firefox, you can paste an address and tap Copy raw data to try handing over its list of extensions.",
        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    Text("This replaces what's on your clipboard with the address ${GeckoImport.SUPPORT_PAGE}.", style = MaterialTheme.typography.bodySmall, modifier = Modifier.testTag("geckoImportClipboardNote"))
    Button(onClick = onStart, Modifier.fillMaxWidth().heightIn(min = 48.dp).testTag("geckoImportPhone")) { Text("Get my extensions from Firefox") }
}

@Composable
private fun WaitingForFirefox(importer: GeckoImporter, context: android.content.Context) {
    Text("Finish in Firefox", style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium, modifier = Modifier.testTag("geckoImportWaiting"))
    listOf(
        "Tap the address bar in Firefox, paste ${GeckoImport.SUPPORT_PAGE} (already copied) and go. Firefox didn't open this page when other apps asked in our tests.",
        "Tap Copy raw data to clipboard.",
        "Come back to Raticode. It picks the list up by itself. If it doesn't, tap Check now.",
    ).forEachIndexed { index, step ->
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("${index + 1}.", fontWeight = FontWeight.Medium)
            Text(step, style = MaterialTheme.typography.bodyMedium)
        }
    }
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        FilledTonalButton(onClick = { importer.openFirefoxAgain(context) }, Modifier.weight(1f).heightIn(min = 48.dp).testTag("geckoImportReopen")) { Text("Open Firefox") }
        OutlinedButton(onClick = { importer.checkClipboard(asked = true) }, Modifier.weight(1f).heightIn(min = 48.dp).testTag("geckoImportCheck")) { Text("Check now") }
    }
    TextButton(onClick = importer::stopWaiting, Modifier.heightIn(min = 48.dp).testTag("geckoImportStop")) { Text("Stop") }
}

@Composable
private fun OtherSources(importer: GeckoImporter) {
    var pasted by remember { mutableStateOf("") }
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { importer.addFiles(it) }
    val muted = MaterialTheme.colorScheme.onSurfaceVariant
    Text("Your files stay on this phone; only extension IDs are looked up on Firefox Add-ons.", style = MaterialTheme.typography.bodySmall, color = muted)
    Button(onClick = { picker.launch(arrayOf("*/*")) }, Modifier.fillMaxWidth().heightIn(min = 48.dp).testTag("geckoImportFiles")) { Text("Choose Firefox files") }
    Text("On your computer, open about:support in Firefox and tap Open Folder next to Profile Folder. Close Firefox, then copy extensions.json and prefs.js to this phone. For bookmarks, use Firefox's Library, Import and Backup, Export Bookmarks to HTML.",
        style = MaterialTheme.typography.bodySmall, color = muted)
    OutlinedTextField(pasted, { pasted = it }, maxLines = 3, label = { Text("Or paste a link or troubleshooting data") }, modifier = Modifier.fillMaxWidth().testTag("geckoImportPaste"),
        supportingText = { Text("A public collection on addons.mozilla.org, or Copy raw data from about:support") })
    FilledTonalButton(onClick = { importer.addText(pasted); pasted = "" }, enabled = pasted.isNotBlank(), modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp).testTag("geckoImportPasteGo")) { Text("Read this") }
}

@Composable
private fun NoteRow(note: GeckoImport.Note) {
    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp)) {
        Text(note.what, style = MaterialTheme.typography.bodyMedium)
        Text(note.why, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}
