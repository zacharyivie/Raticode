package dev.raticode.mobile.ui

import android.content.ActivityNotFoundException
import android.content.Intent
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.annotation.RequiresApi
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.applock.AppLockDelay
import dev.raticode.mobile.applock.AppLockRuntime
import dev.raticode.mobile.applock.appLockSettingsText
import dev.raticode.mobile.childlock.ChildLockRuntime
import dev.raticode.mobile.childlock.ScreenLockPrompt
import dev.raticode.mobile.childlock.ScreenLockProof
import dev.raticode.mobile.childlock.UnlockOutcome

/** Plain cover drawn in a Raticode window for the moment before the lock screen appears over it. */
@Composable
internal fun AppLockCover() {
    Box(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background).testTag("appLockCover"))
}

/** The page shown while Raticode is locked. It shows nothing from the app. */
@Composable
internal fun AppLockScreen(notice: String?, asking: Boolean, offerSettings: Boolean, onUnlock: () -> Unit, onOpenSettings: () -> Unit) {
    Surface(Modifier.fillMaxSize().testTag("appLockScreen"), color = MaterialTheme.colorScheme.background) {
        Column(Modifier.fillMaxSize().safeDrawingPadding().verticalScroll(rememberScrollState()).padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(16.dp, Alignment.CenterVertically)) {
            Icon(Icons.Default.Lock, null, Modifier.size(48.dp), tint = MaterialTheme.colorScheme.primary)
            Text("Raticode is locked", style = MaterialTheme.typography.headlineSmall, textAlign = TextAlign.Center)
            Text(notice ?: "Use your fingerprint, face or screen lock to open it.", Modifier.widthIn(max = 360.dp).testTag("appLockNotice"),
                style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center)
            Button(onClick = onUnlock, enabled = !asking, modifier = Modifier.heightIn(min = 48.dp).testTag("appLockUnlock")) { Text("Unlock") }
            if (offerSettings) TextButton(onClick = onOpenSettings) { Text("Open Android settings") }
        }
    }
}

/** More › App lock. Off until the user turns it on. */
@RequiresApi(29)
@Composable
internal fun AppLockPage(onBack: () -> Unit) {
    val context = LocalContext.current
    val activity = remember(context) { context.findActivity() }
    val lock = remember { AppLockRuntime.get(context) }
    var asking by remember { mutableStateOf(false) }
    var problem by remember { mutableStateOf<UnlockOutcome?>(null) }
    val prompt = remember(activity) { activity?.let { ScreenLockPrompt(it) } }
    val confirmation = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { prompt?.confirmationFinished(it.resultCode) }
    SideEffect { prompt?.startConfirmation = { confirmation.launch(it) } }
    DisposableEffect(prompt) { onDispose { prompt?.cancel() } }

    fun apply(on: Boolean, delay: AppLockDelay) {
        if (asking) return
        problem = null
        val save = { if (!lock.change(on, delay)) problem = UnlockOutcome.NOT_SAVED }
        if (!lock.needsProof(on, delay)) { save(); return }
        // Asking first also proves the prompt works here, so turning this on can't shut the user out.
        if (!ChildLockRuntime.hasScreenLock(context)) { problem = UnlockOutcome.NO_SCREEN_LOCK; return }
        if (prompt == null) { problem = UnlockOutcome.UNAVAILABLE; return }
        asking = true
        lock.beginPrompt()
        prompt.ask(if (on != lock.enabled) (if (on) "Turn on app lock" else "Turn off app lock") else "Change app lock",
            "Use your fingerprint, face or screen lock", ScreenLockProof.ANY_PHONE_UNLOCK) { outcome ->
            asking = false
            lock.endPrompt()
            if (outcome == UnlockOutcome.CONFIRMED) save() else problem = outcome
        }
    }

    Column(Modifier.fillMaxSize()) {
        Header("App lock", "Ask who's holding the phone", onBack)
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 8.dp).testTag("appLockPage"),
            verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).testTag("appLockSwitch").toggleable(lock.enabled, enabled = !asking, role = Role.Switch) { apply(it, lock.delay) },
                verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f).padding(end = 12.dp)) {
                    Text("Ask when Raticode opens", style = MaterialTheme.typography.bodyLarge)
                    Text("Uses your fingerprint, face or the phone's screen lock. Off unless you turn it on.", style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Switch(lock.enabled, null, enabled = !asking)
            }
            Text("Ask again", style = MaterialTheme.typography.titleSmall)
            Column(Modifier.selectableGroup()) {
                AppLockDelay.entries.forEach { choice ->
                    Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).testTag("appLockDelay${choice.minutes}")
                        .selectable(lock.delay == choice, enabled = !asking, role = Role.RadioButton) { if (lock.delay != choice) apply(lock.enabled, choice) },
                        verticalAlignment = Alignment.CenterVertically) {
                        RadioButton(lock.delay == choice, null, enabled = !asking)
                        Text(choice.label, Modifier.padding(start = 12.dp), style = MaterialTheme.typography.bodyLarge)
                    }
                }
            }
            problem?.appLockSettingsText()?.let { text ->
                Text(text, Modifier.testTag("appLockProblem"), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.error)
                if (problem?.offerSettings == true) TextButton(onClick = {
                    try { context.startActivity(Intent(Settings.ACTION_SECURITY_SETTINGS)) } catch (_: ActivityNotFoundException) { }
                }) { Text("Open Android settings") }
            }
            Note("Immediately asks every time you come back to Raticode, including after picking a file in another app.")
            Note("Background sync, Rem's alerts and replies, and messages waiting to send keep going while Raticode is locked. Voice mode keeps its own lock-screen rule.")
        }
    }
}
