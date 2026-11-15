package dev.raticode.mobile.ui

import androidx.annotation.RequiresApi
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.applock.AppLockRuntime
import dev.raticode.mobile.childlock.ChildLockRuntime
import dev.raticode.mobile.update.AppUpdates

/** The once-a-day reminder above every page. Not now hides it until tomorrow. */
@RequiresApi(29)
@Composable
internal fun AppUpdateBanner() {
    val context = LocalContext.current
    val text = AppUpdates.reminder ?: return
    if (AppUpdates.available == null) return
    Surface(color = MaterialTheme.colorScheme.tertiaryContainer, modifier = Modifier.fillMaxWidth().testTag("updateBanner")) {
        Row(Modifier.padding(start = 20.dp, end = 8.dp, top = 6.dp, bottom = 6.dp), verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(text, Modifier.weight(1f).semantics { liveRegion = LiveRegionMode.Polite }, style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onTertiaryContainer)
            TextButton(onClick = { AppUpdates.dismissReminder(context) }) { Text("Not now") }
            Button(onClick = { AppUpdates.dismissReminder(context); AppUpdates.update(context) }) { Text(updateLabel()) }
        }
    }
}

@RequiresApi(29)
private fun updateLabel() = when (AppUpdates.source) {
    AppUpdates.Source.PLAY -> "Open Google Play"
    AppUpdates.Source.NONE -> "Open download page"
    AppUpdates.Source.DIRECT -> "Update Raticode"
}

/** "Raticode Mobile updates" in the phone's settings: version, last check, Check now and Update Raticode. */
@OptIn(ExperimentalLayoutApi::class)
@RequiresApi(29)
@Composable
internal fun AppUpdateSettings() {
    val context = LocalContext.current
    val installed = AppUpdates.installedVersion(context)?.toString() ?: "unknown"
    val release = AppUpdates.available
    Column(Modifier.testTag("appUpdates"), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(if (release != null) "Version ${release.version} is available" else "Raticode Mobile $installed", style = MaterialTheme.typography.titleMedium)
        val status = when {
            AppUpdates.checking -> "Checking for updates…"
            AppUpdates.checkError != null -> AppUpdates.checkError!!
            release != null -> "You have $installed. The download is about ${release.apkSize / (1 shl 20)} MB."
            AppUpdates.checkedAt > 0 -> "This is the newest version. Checked ${android.text.format.DateUtils.getRelativeTimeSpanString(AppUpdates.checkedAt)}."
            else -> "Raticode checks for a new version when it opens and every 10 minutes while it's open."
        }
        Text(status, Modifier.semantics { liveRegion = LiveRegionMode.Polite }, style = MaterialTheme.typography.bodySmall,
            color = if (AppUpdates.checkError != null) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant)
        if (AppUpdates.downloading || AppUpdates.verifying) {
            val total = AppUpdates.downloadSize.coerceAtLeast(1)
            if (AppUpdates.verifying) LinearProgressIndicator(Modifier.fillMaxWidth())
            else LinearProgressIndicator(progress = { (AppUpdates.downloaded.toFloat() / total).coerceIn(0f, 1f) }, modifier = Modifier.fillMaxWidth())
            Text(if (AppUpdates.verifying) "Verifying the download…" else "Downloading ${AppUpdates.downloaded / (1 shl 20)} of ${total / (1 shl 20)} MB",
                style = MaterialTheme.typography.bodySmall)
        }
        AppUpdates.installError?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error) }
        AppUpdates.installNotice?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (release != null) {
                if (AppUpdates.downloading) OutlinedButton(onClick = AppUpdates::cancel) { Text("Pause download") }
                else Button(onClick = { AppUpdates.update(context) }, enabled = !AppUpdates.verifying) { Text(updateLabel()) }
            }
            TextButton(onClick = { AppUpdates.checkNow(context) }, enabled = !AppUpdates.checking && !AppUpdates.downloading) { Text("Check now") }
        }
        if (AppUpdates.source == AppUpdates.Source.PLAY)
            Text("Google Play installed this copy, so Google Play updates it.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/** The questions an update can ask, and Android's install confirmation. Shown above every page. */
@RequiresApi(29)
@Composable
internal fun AppUpdateDialogs() {
    val context = LocalContext.current
    val release = AppUpdates.available
    if (AppUpdates.meteredPrompt && release != null) AlertDialog(onDismissRequest = AppUpdates::dismissMeteredPrompt,
        title = { Text("Download on mobile data?") },
        text = { Text("The update is about ${release.apkSize / (1 shl 20)} MB and this phone is on mobile data or a metered network. You can wait for Wi-Fi instead.") },
        confirmButton = { TextButton(onClick = { AppUpdates.update(context, onMobileData = true) }) { Text("Download now") } },
        dismissButton = { TextButton(onClick = AppUpdates::dismissMeteredPrompt) { Text("Wait for Wi-Fi") } })
    if (AppUpdates.needsInstallPermission) AlertDialog(onDismissRequest = AppUpdates::dismissInstallPermission,
        title = { Text("Allow Raticode to install its update") },
        text = { Text("Android asks before an app installs updates itself. Turn on \"Allow from this source\" for Raticode, then come back; the update continues. Android still asks you to confirm the install.") },
        confirmButton = { TextButton(onClick = { AppUpdates.openInstallSettings(context) }) { Text("Open settings") } },
        dismissButton = { TextButton(onClick = AppUpdates::dismissInstallPermission) { Text("Cancel") } })
    // The installer's confirmation waits while Child lock or App lock covers Raticode.
    val confirmation = AppUpdates.confirmation
    val locked = ChildLockRuntime.locked(context) || AppLockRuntime.get(context).locked
    LaunchedEffect(confirmation, locked) { if (confirmation != null && !locked) AppUpdates.showConfirmation(context) }
}
