package dev.raticode.mobile.ui

import android.content.Context
import androidx.activity.compose.BackHandler
import androidx.annotation.RequiresApi
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.core.content.edit
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.live.SourceCommit
import dev.raticode.mobile.live.SourceControl
import dev.raticode.mobile.live.SourceFile
import dev.raticode.mobile.live.SourceProject
import dev.raticode.mobile.live.SourceProjectList
import dev.raticode.mobile.live.SourceWorktree
import org.json.JSONObject

/** The user's bottom-bar choice. Only page names are stored, never anything from the desktop. */
internal object NavBarPrefs {
    private fun prefs(context: Context) = context.getSharedPreferences("navigation", Context.MODE_PRIVATE)
    fun load(context: Context) = LiveNavigation.parse(prefs(context).getString("bar", null))
    fun save(context: Context, bar: List<String>) = prefs(context).edit { putString("bar", LiveNavigation.encode(bar)) }
}

internal fun destinationIcon(page: String): ImageVector = when (page) {
    "Rem" -> ThreadIcons.Rem; "Threads" -> ThreadIcons.Threads; "Organizations" -> ThreadIcons.Organizations
    "Swarms" -> ThreadIcons.Swarms; "SourceControl" -> ThreadIcons.SourceControl; "Explorer" -> ThreadIcons.Folder; "Workflows" -> ThreadIcons.Workflows
    "Approvals" -> ThreadIcons.Approvals; "Usage" -> ThreadIcons.Usage; "Voice" -> ThreadIcons.Mic
    "Settings" -> ThreadIcons.Settings; "Developer" -> ThreadIcons.Log; "Browser" -> ThreadIcons.Browser; "Kanban" -> ThreadIcons.Kanban; else -> ThreadIcons.More
}

/** A back arrow for Rem and Threads, whose own headers have none, when they were opened from More rather than the bar. */
@Composable
internal fun MoreBackRow(onBack: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(start = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        IconButton(onClick = onBack, modifier = Modifier.testTag("moreBack")) {
            Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back")
        }
    }
}

/** Chooses the bottom bar's destinations and their order. More stays last and lists the rest. */
@Composable
internal fun NavBarSettingsPage(bar: List<String>, onChange: (List<String>) -> Unit, onBack: () -> Unit) {
    val full = bar.size >= LiveNavigation.MAX
    Column(Modifier.fillMaxSize()) {
        Header("Bottom bar", "Choose up to ${LiveNavigation.MAX} destinations", onBack)
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 8.dp).testTag("navBarSettings"),
            verticalArrangement = Arrangement.spacedBy(20.dp)) {
            SettingsGroup("On the bar") {
                bar.forEachIndexed { index, page ->
                    val destination = LiveNavigation.destination(page) ?: return@forEachIndexed
                    Row(Modifier.fillMaxWidth().heightIn(min = 48.dp), verticalAlignment = Alignment.CenterVertically) {
                        Row(Modifier.weight(1f).heightIn(min = 48.dp).toggleable(true, enabled = bar.size > 1, role = Role.Checkbox) { onChange(LiveNavigation.toggle(bar, page)) },
                            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            Checkbox(true, null, enabled = bar.size > 1)
                            Icon(destinationIcon(page), null, Modifier.size(20.dp))
                            Text(destination.title, style = MaterialTheme.typography.bodyLarge, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        }
                        IconButton(onClick = { onChange(LiveNavigation.move(bar, page, -1)) }, enabled = index > 0) {
                            Icon(Icons.Filled.KeyboardArrowUp, "Move ${destination.title} up, further left on the bar")
                        }
                        IconButton(onClick = { onChange(LiveNavigation.move(bar, page, 1)) }, enabled = index < bar.lastIndex) {
                            Icon(Icons.Filled.KeyboardArrowDown, "Move ${destination.title} down, further right on the bar")
                        }
                    }
                }
                Note("The top of this list is the left end of the bar, and More always comes last. The app opens to the first destination.")
            }
            SettingsGroup("In More") {
                if (full) Note("The bar is full. Remove a destination to add another.")
                LiveNavigation.omitted(bar).forEach { destination ->
                    Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).toggleable(false, enabled = !full, role = Role.Checkbox) { onChange(LiveNavigation.toggle(bar, destination.page)) },
                        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        Checkbox(false, null, enabled = !full)
                        Icon(destinationIcon(destination.page), null, Modifier.size(20.dp))
                        Text(destination.title, style = MaterialTheme.typography.bodyLarge)
                    }
                }
            }
            TextButton(onClick = { onChange(LiveNavigation.DEFAULT) }, enabled = bar != LiveNavigation.DEFAULT) { Text("Restore the default bar") }
        }
    }
}
