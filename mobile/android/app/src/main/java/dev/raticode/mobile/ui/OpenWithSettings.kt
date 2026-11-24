package dev.raticode.mobile.ui

import android.app.role.RoleManager
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.annotation.RequiresApi
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import dev.raticode.mobile.browser.BrowserRules
import dev.raticode.mobile.browser.BrowserRuntime
import dev.raticode.mobile.browser.BrowserTabs
import dev.raticode.mobile.open.OpenTypes

/** What the default-browser button does: Android's own request dialog, or its default-apps settings. */
internal enum class BrowserRoleStep {
    /** Raticode is already the default browser. */
    HELD,
    /** Android asks the user in its own dialog (RoleManager). */
    ASK,
    /** The role can't be requested here; the user picks in Android's default-apps settings. */
    SETTINGS;

    companion object {
        fun of(available: Boolean, held: Boolean) = when {
            held -> HELD
            available -> ASK
            else -> SETTINGS
        }
    }
}

@RequiresApi(29)
private fun browserRole(context: Context): BrowserRoleStep {
    val roles = context.getSystemService(RoleManager::class.java) ?: return BrowserRoleStep.SETTINGS
    val available = runCatching { roles.isRoleAvailable(RoleManager.ROLE_BROWSER) }.getOrDefault(false)
    val held = available && runCatching { roles.isRoleHeld(RoleManager.ROLE_BROWSER) }.getOrDefault(false)
    return BrowserRoleStep.of(available, held)
}

private fun openSettings(context: Context, vararg intents: Intent): Boolean {
    for (intent in intents) {
        try { context.startActivity(intent); return true } catch (_: ActivityNotFoundException) { } catch (_: SecurityException) { }
    }
    return false
}

private fun defaultAppsSettings(context: Context) = openSettings(context, Intent(Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS),
    Intent(Settings.ACTION_SETTINGS))

/** Raticode's own "Open by default" page (Android 12+), or its app info. */
private fun openByDefaultSettings(context: Context) {
    val app = Uri.fromParts("package", context.packageName, null)
    val intents = buildList {
        if (Build.VERSION.SDK_INT >= 31) add(Intent(Settings.ACTION_APP_OPEN_BY_DEFAULT_SETTINGS, app))
        add(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, app))
    }
    openSettings(context, *intents.toTypedArray())
}

/**
 * More › Open with Raticode: Raticode as the phone's default browser, which of Raticode's browsers takes links from other
 * apps, and the files other apps can open with Raticode. Android makes every one of these changes in its own dialog or
 * settings; Raticode never changes a default by itself.
 */
@RequiresApi(29)
@Composable
internal fun OpenWithSettingsPage(onBack: () -> Unit) {
    val context = LocalContext.current
    val browser = remember { BrowserRuntime.get(context) }
    var role by remember { mutableStateOf(browserRole(context)) }
    var checks by remember { mutableIntStateOf(0) }
    var problem by remember { mutableStateOf<String?>(null) }
    // Coming back from Android's dialog or settings shows what the user chose there.
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) { checks++ }
    LaunchedEffect(checks) { role = browserRole(context) }
    val request = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { role = browserRole(context) }
    Column(Modifier.fillMaxSize()) {
        Header("Open with Raticode", "Default browser and files", onBack)
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 8.dp).testTag("openWithPage"),
            verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("Web links", Modifier.semantics { heading() }, style = MaterialTheme.typography.titleMedium)
            Text(if (role == BrowserRoleStep.HELD) "Raticode is this phone's default browser. Links you tap in other apps open here."
                else "Links you tap in other apps open in your default browser. Make Raticode the default to open them here.",
                Modifier.testTag("browserRoleStatus"), style = MaterialTheme.typography.bodyMedium)
            if (role != BrowserRoleStep.HELD) Button(onClick = {
                problem = null
                val asked = role == BrowserRoleStep.ASK && runCatching {
                    request.launch(context.getSystemService(RoleManager::class.java).createRequestRoleIntent(RoleManager.ROLE_BROWSER)); true
                }.getOrDefault(false)
                if (!asked && !defaultAppsSettings(context)) problem = "This phone has no default-apps settings Raticode can open."
            }, Modifier.fillMaxWidth().testTag("makeDefaultBrowser")) { Text("Make Raticode the default browser") }
            Text("Android asks you to confirm in its own dialog or settings. You can switch back there at any time.",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)

            Text("Links from other apps open in", style = MaterialTheme.typography.titleSmall)
            Column(Modifier.selectableGroup()) {
                listOf(BrowserRules.ENGINE_RATICODE to BrowserTabs.CHROMIUM_NAME, BrowserRules.ENGINE_GECKO to BrowserTabs.GECKO_NAME).forEach { (engine, label) ->
                    val chosen = OpenTypes.linkEngine(browser.saved.externalEngine) == engine
                    Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).testTag("linkEngine_$engine")
                        .selectable(chosen, role = Role.RadioButton) { browser.setExternalEngine(engine) },
                        verticalAlignment = Alignment.CenterVertically) {
                        RadioButton(chosen, null)
                        Text(label, Modifier.padding(start = 12.dp), style = MaterialTheme.typography.bodyLarge)
                    }
                }
            }
            Text("Each link opens in a new tab, like one you opened yourself. Rem sees a page only when you send it with Ask Rem.",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)

            Text("Files", Modifier.semantics { heading() }, style = MaterialTheme.typography.titleMedium)
            Text("Raticode can open these when another app, such as Files or Downloads, opens one. The first time, Android asks which " +
                "app to use: choose Raticode, then Always to make it the default for that kind of file.", style = MaterialTheme.typography.bodyMedium)
            OpenTypes.kinds.forEach { kind ->
                Column(Modifier.fillMaxWidth().testTag("openKind_${kind.viewer.name}")) {
                    Text(kind.label, style = MaterialTheme.typography.bodyLarge)
                    Text(kind.detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            Note("Files open in a viewer of their own, read only and up to ${OpenTypes.MAX_BYTES / (1024 * 1024)} MB. Web pages and SVG drawings " +
                "show without running scripts, loading anything from the internet or reading other files. Nothing you open this way " +
                "goes to Rem, a thread or your computer, and Raticode needs no permission to your phone's storage.")
            OutlinedButton(onClick = { if (!defaultAppsSettings(context)) problem = "This phone has no default-apps settings Raticode can open." },
                Modifier.fillMaxWidth().testTag("defaultAppsSettings")) { Text("Android default apps settings") }
            OutlinedButton(onClick = { openByDefaultSettings(context) }, Modifier.fillMaxWidth().testTag("openByDefaultSettings")) {
                Text("Raticode's Open by default settings")
            }
            Text("To stop a file kind opening in Raticode, clear Raticode's defaults on its Open by default page.",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            problem?.let { Text(it, Modifier.testTag("openWithProblem"), color = MaterialTheme.colorScheme.error) }
        }
    }
}
