package dev.raticode.mobile.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.live.WakeOnLan

@Composable
internal fun ConnectionStatus(connected: Boolean, status: String, modifier: Modifier = Modifier) {
    var details by remember { mutableStateOf(false) }
    Surface(onClick = { details = true }, color = MaterialTheme.colorScheme.background) {
    Row(modifier.padding(horizontal = 20.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Box(Modifier.size(6.dp).background(if (connected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outline, CircleShape))
        Text(status, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
    }
    }
    if (details) AlertDialog(onDismissRequest = { details = false }, title = { Text("Connection status") },
        text = { Text(status) }, confirmButton = { TextButton(onClick = { details = false }) { Text("Close") } })
}

/** Wake-on-LAN sends no answer, so the words here only ever say the signal went out. */
@androidx.annotation.RequiresApi(29)
@Composable
internal fun WakeComputer(client: LiveClient, showLimits: Boolean = false) {
    if (!client.wakeAvailable) return
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        OutlinedButton(onClick = client::wakeComputer, enabled = !client.wakeBusy, modifier = Modifier.heightIn(min = 48.dp).testTag("wakeComputer")) { Text(if (client.wakeNotice == WakeOnLan.SENT) "Wake signal sent" else "Wake computer") }
        client.wakeNotice?.let { Text(it, style = MaterialTheme.typography.bodySmall, modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite }) }
        if (showLimits) Text(WakeOnLan.LIMITS, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/** The offer shown on other pages after a connection attempt has failed. */
@androidx.annotation.RequiresApi(29)
@Composable
internal fun WakeBanner(client: LiveClient) {
    Surface(color = MaterialTheme.colorScheme.surfaceVariant, modifier = Modifier.fillMaxWidth().testTag("wakeBanner")) {
        Column(Modifier.padding(horizontal = 20.dp, vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text("Can't reach your desktop. It may be asleep.", style = MaterialTheme.typography.bodyMedium)
            WakeComputer(client)
        }
    }
}

@Composable
internal fun SettingsGroup(title: String, content: @Composable ColumnScope.() -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(title, style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(start = 4.dp))
        Surface(shape = MaterialTheme.shapes.large, color = MaterialTheme.colorScheme.surface) {
            Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp), content = content)
        }
    }
}

@Composable
internal fun ConversationRow(title: String, project: String, preview: String, onClick: () -> Unit) {
    Surface(onClick = onClick, color = MaterialTheme.colorScheme.background) {
        Row(Modifier.fillMaxWidth().padding(vertical = 18.dp), verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                if (project.isNotBlank()) Text(project, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.primary, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(title, style = MaterialTheme.typography.titleMedium, maxLines = 2, overflow = TextOverflow.Ellipsis)
                Text(preview, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
            Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, contentDescription = null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
    HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
}
