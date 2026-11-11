package dev.raticode.mobile.ui

import android.Manifest
import android.content.Intent
import android.os.Build
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import android.app.TimePickerDialog
import android.text.format.DateFormat
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.selection.toggleable
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import androidx.core.app.ActivityCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import dev.raticode.mobile.live.LiveNotifications
import dev.raticode.mobile.live.RemAlertRules
import dev.raticode.mobile.live.RemNotifications
import dev.raticode.mobile.live.TaskNotification
import java.time.LocalTime
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle

@Composable
fun LiveNotificationSettings() {
    val context = LocalContext.current
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    var enabled by remember { mutableStateOf(LiveNotifications.enabled(context)) }
    var allowed by remember { mutableStateOf(LiveNotifications.allowed(context)) }
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
        allowed = LiveNotifications.allowed(context)
    }
    DisposableEffect(lifecycle, context) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) allowed = LiveNotifications.allowed(context)
        }
        lifecycle.addObserver(observer)
        onDispose { lifecycle.removeObserver(observer) }
    }
    Column {
        Text("Desktop response notifications", style = MaterialTheme.typography.titleSmall)
        Text("Show a generic alert when a response arrives. Message text and file names stay hidden.", style = MaterialTheme.typography.bodySmall)
        TextButton(shape = MaterialTheme.shapes.small, onClick = {
            enabled = !enabled
            LiveNotifications.setEnabled(context, enabled)
            if (enabled && !allowed && Build.VERSION.SDK_INT >= 33) permission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }) { Text(if (enabled) "Turn off response notifications" else "Enable response notifications") }
        if (enabled && !allowed) {
            Text("Android has blocked notifications. Messages still sync while the connection is active. Allow notifications in Android settings to see alerts.", style = MaterialTheme.typography.bodySmall)
            TextButton(shape = MaterialTheme.shapes.small, onClick = {
                context.startActivity(Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName))
            }) { Text("Open notification settings") }
        }
    }
}

/** What background sync shows for tasks started from this phone, and how to fix it when Android blocks that. */
@Composable
fun TaskProgressSettings(backgroundOn: Boolean) {
    val context = LocalContext.current
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    var allowed by remember { mutableStateOf(TaskNotification.allowed(context)) }
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { allowed = TaskNotification.allowed(context) }
    DisposableEffect(lifecycle, context) {
        val observer = LifecycleEventObserver { _, event -> if (event == Lifecycle.Event.ON_RESUME) allowed = TaskNotification.allowed(context) }
        lifecycle.addObserver(observer)
        onDispose { lifecycle.removeObserver(observer) }
    }
    Column {
        Text("While background sync is on, a task you start from this phone shows Plan, Work, Check and Report and the time so far in one notification. Hide it and it stays away for that task. Tasks Rem starts on its own never appear.",
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        if (backgroundOn && !allowed) {
            Text("Android has blocked notifications, so task progress can't appear.", style = MaterialTheme.typography.bodySmall)
            TextButton(shape = MaterialTheme.shapes.small, onClick = {
                if (Build.VERSION.SDK_INT >= 33 && ActivityCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != android.content.pm.PackageManager.PERMISSION_GRANTED) {
                    permission.launch(Manifest.permission.POST_NOTIFICATIONS)
                } else context.startActivity(Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName))
            }) { Text("Allow notifications") }
        }
    }
}

/**
 * Rem's chat alerts: off by default, and generic unless the user also chooses to show Rem's words.
 * [supported] is null until the desktop says whether it sends them; [filterSupported] the same for alerts about work
 * this phone didn't start.
 */
@Composable
fun RemNotificationSettings(supported: Boolean?, desktopControl: Boolean, filterSupported: Boolean?) {
    val context = LocalContext.current
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    var enabled by remember { mutableStateOf(RemNotifications.enabled(context)) }
    var showText by remember { mutableStateOf(RemNotifications.showText(context)) }
    var state by remember { mutableStateOf(RemNotifications.state(context, desktopControl, supported)) }
    LaunchedEffect(desktopControl, supported) { state = RemNotifications.state(context, desktopControl, supported) }
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
        state = RemNotifications.state(context, desktopControl, supported)
    }
    DisposableEffect(lifecycle, context) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) state = RemNotifications.state(context, desktopControl, supported)
        }
        lifecycle.addObserver(observer)
        onDispose { lifecycle.removeObserver(observer) }
    }
    Column {
        Text("Rem chat notifications", style = MaterialTheme.typography.titleSmall)
        Text("Rem's replies and task updates for requests you sent from this phone, with a Reply button. Alerts arrive only while Raticode is open or background sync is on. Replying needs an unlocked phone.",
            style = MaterialTheme.typography.bodySmall)
        TextButton(shape = MaterialTheme.shapes.small, onClick = {
            enabled = !enabled
            RemNotifications.setEnabled(context, enabled)
            state = RemNotifications.state(context, desktopControl, supported)
            if (enabled && state.fix == RemAlertRules.Fix.ASK_PERMISSION && Build.VERSION.SDK_INT >= 33) permission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }) { Text(if (enabled) "Turn off Rem chat notifications" else "Enable Rem chat notifications") }
        if (enabled) {
            state.problem?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
            if (!state.androidAllows) {
                Text("Rem's messages still appear in Rem's chat. Allow notifications to get alerts.", style = MaterialTheme.typography.bodySmall)
                if (state.fix == RemAlertRules.Fix.ASK_PERMISSION && Build.VERSION.SDK_INT >= 33) TextButton(shape = MaterialTheme.shapes.small, onClick = {
                    permission.launch(Manifest.permission.POST_NOTIFICATIONS)
                }) { Text("Allow notifications") }
                // Android stops asking after repeated denials; its settings page always works.
                TextButton(shape = MaterialTheme.shapes.small, onClick = {
                    val intent = if (state.fix == RemAlertRules.Fix.CHANNEL_SETTINGS)
                        Intent(Settings.ACTION_CHANNEL_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_CHANNEL_ID, RemNotifications.CHANNEL)
                    else Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                    context.startActivity(intent.putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName))
                }) { Text("Open notification settings") }
            }
            Text("Show Rem's words", style = MaterialTheme.typography.titleSmall)
            Text(if (showText) "Alerts show Rem's replies and your answers. Anyone who sees your unlocked phone, a paired watch, or an app you allowed to read notifications can read them. The lock screen shows only \"New message from Rem\"."
                else "Alerts stay generic, like \"New message\" or \"A task needs you\". Open Rem's chat to read them.",
                style = MaterialTheme.typography.bodySmall)
            TextButton(shape = MaterialTheme.shapes.small, onClick = {
                showText = !showText
                RemNotifications.setShowText(context, showText)
            }) { Text(if (showText) "Hide Rem's words in alerts" else "Show Rem's words in alerts") }
            RemAlertChoices(filterSupported)
        }
    }
}

/** The alerts beyond this phone's own requests, and quiet hours. Saved on the phone; the desktop decides what counts under each. */
@Composable
private fun RemAlertChoices(filterSupported: Boolean?) {
    val context = LocalContext.current
    var include by remember { mutableStateOf(RemNotifications.include(context).toSet()) }
    var quiet by remember { mutableStateOf(RemNotifications.quietHours(context)) }
    fun filter(name: String, on: Boolean) {
        RemNotifications.setFilter(context, name, on)
        include = RemNotifications.include(context).toSet()
    }
    fun save(next: RemAlertRules.QuietHours) {
        RemNotifications.setQuietHours(context, next)
        quiet = next
    }
    fun pick(minute: Int, chosen: (Int) -> Unit) = TimePickerDialog(context, { _, hour, min -> chosen(hour * 60 + min) },
        minute / 60, minute % 60, DateFormat.is24HourFormat(context)).show()
    val format = remember { DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT) }
    fun label(minute: Int) = LocalTime.of(minute / 60, minute % 60).format(format)

    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text("Also alert me about", style = MaterialTheme.typography.titleSmall)
        if (filterSupported == false) Text("Update Raticode on your computer to get these alerts.", style = MaterialTheme.typography.bodySmall)
        AlertSwitch("A task needs you", "From any source: tasks started on the desktop, self-work and organizations.",
            RemAlertRules.NEEDS_YOU in include, "remAlertNeedsYou") { filter(RemAlertRules.NEEDS_YOU, it) }
        AlertSwitch("A task finishes", "Any task, wherever it started.",
            RemAlertRules.FINISHED in include, "remAlertFinished") { filter(RemAlertRules.FINISHED, it) }
        AlertSwitch("Proposals are waiting", "One alert for each self-work round that leaves proposals for you.",
            RemAlertRules.PROPOSALS in include, "remAlertProposals") { filter(RemAlertRules.PROPOSALS, it) }
        Text("Your desktop decides what counts as each of these.", style = MaterialTheme.typography.bodySmall)
        AlertSwitch("Quiet hours", "Alerts still arrive during these hours, without sound or a pop-up.", quiet.on, "remQuietHours") { save(quiet.copy(on = it)) }
        if (quiet.on) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                TextButton(shape = MaterialTheme.shapes.small, onClick = { pick(quiet.start) { save(quiet.copy(start = it)) } }) { Text("From ${label(quiet.start)}") }
                TextButton(shape = MaterialTheme.shapes.small, onClick = { pick(quiet.end) { save(quiet.copy(end = it)) } }) { Text("Until ${label(quiet.end)}") }
            }
            if (quiet.empty) Text("Start and end are the same, so quiet hours have no effect.", style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
private fun AlertSwitch(title: String, detail: String, checked: Boolean, tag: String, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).testTag(tag).toggleable(checked, role = Role.Switch, onValueChange = onChange),
        verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.bodyLarge)
            Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Switch(checked, null)
    }
}
