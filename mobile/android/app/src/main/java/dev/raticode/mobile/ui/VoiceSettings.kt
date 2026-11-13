package dev.raticode.mobile.ui

import android.app.StatusBarManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.graphics.drawable.Icon
import android.os.Build
import android.provider.Settings
import android.speech.RecognitionSupport
import android.speech.RecognitionSupportCallback
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import androidx.annotation.RequiresApi
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.selection.toggleable
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLocale
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import dev.raticode.mobile.R
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.voice.AssistantSupport
import dev.raticode.mobile.voice.RemSpeaker
import dev.raticode.mobile.voice.RemVoiceInteractionService
import dev.raticode.mobile.voice.VoiceLogic
import dev.raticode.mobile.voice.RemVoiceTile
import dev.raticode.mobile.voice.VoiceActivity
import dev.raticode.mobile.voice.VoiceController
import dev.raticode.mobile.voice.VoicePrefs
import dev.raticode.mobile.voice.VoiceSetup
import java.util.Locale

/** Setup for using Rem as the phone's digital assistant: role, power button, speech, voice and entry points. */
@RequiresApi(29)
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun VoiceAssistantSettings(client: LiveClient) {
    val context = LocalContext.current
    val muted = MaterialTheme.colorScheme.onSurfaceVariant
    if (Build.VERSION.SDK_INT < 31) {
        Text("Voice mode needs Android 12 or newer, for speech recognition that stays on the phone.", style = MaterialTheme.typography.bodyMedium)
        return
    }
    var support by remember { mutableStateOf(VoiceSetup.support(context)) }
    var settingsMissing by remember { mutableStateOf(false) }
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    DisposableEffect(lifecycle) {
        val observer = LifecycleEventObserver { _, event -> if (event == Lifecycle.Event.ON_RESUME) support = VoiceSetup.support(context) }
        lifecycle.addObserver(observer)
        onDispose { lifecycle.removeObserver(observer) }
    }
    val assistant = support == AssistantSupport.SELECTED
    val steps = remember { VoiceSetup.steps() }
    Text("Talk to Rem with the power button, the assist gesture, a Quick Settings tile or the app shortcut, even when Raticode is closed. Speech is recognized and read aloud on this phone; only the words go to your desktop, end-to-end encrypted.",
        style = MaterialTheme.typography.bodyMedium)
    if (client.remReady != null && !client.desktopControl) Text(VoiceController.CONTROL_OFF, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)

    Text(when (support) {
        AssistantSupport.SELECTED -> "Raticode is your digital assistant."
        AssistantSupport.ROLE_UNAVAILABLE -> "This phone doesn't let apps be the digital assistant here."
        else -> "Raticode isn't your digital assistant yet."
    }, style = MaterialTheme.typography.titleSmall, modifier = Modifier.testTag("voiceAssistantRole"))
    Text(when (support) {
        AssistantSupport.SELECTED -> "Holding the power button or using the assist gesture opens Rem, where your phone allows it."
        AssistantSupport.ROLE_UNAVAILABLE -> "Android doesn't offer the digital assistant choice for this profile, such as a work profile. Use the Talk to Rem tile or the shortcut on Raticode's icon."
        else -> "Android lets only you choose this. ${steps.choose} Choosing it replaces Google's assistant for the power button and assist gesture; you can switch back the same way."
    }, style = MaterialTheme.typography.bodySmall, color = muted)
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        if (support != AssistantSupport.ROLE_UNAVAILABLE) Button(onClick = { settingsMissing = !VoiceSetup.openAssistantSettings(context) },
            modifier = Modifier.testTag("voiceChooseAssistant")) { Text(if (assistant) "Digital assistant settings" else "Choose digital assistant") }
        OutlinedButton(onClick = { context.startActivity(VoiceActivity.intent(context).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) },
            modifier = Modifier.testTag("voiceTry")) { Text("Try voice mode") }
    }
    if (settingsMissing) Text("This phone didn't open its assistant settings. ${steps.choose}", style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.error, modifier = Modifier.testTag("voiceSettingsMissing"))
    Text("Power button", style = MaterialTheme.typography.titleSmall)
    Text(steps.button, style = MaterialTheme.typography.bodySmall, color = muted, modifier = Modifier.testTag("voiceButtonSteps"))
    if (support == AssistantSupport.NOT_SELECTED) {
        var offer by remember { mutableStateOf(VoicePrefs.offerSetup(context)) }
        VoiceSwitch("Offer setup when Raticode opens", "Asks each time you open Raticode until Raticode is your assistant.", offer, "voiceOfferSetup") {
            offer = it; VoicePrefs.setOfferSetup(context, it)
        }
    }

    HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
    SpeechStatus()
    HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)

    var speak by remember { mutableStateOf(VoicePrefs.speak(context)) }
    var whileLocked by remember { mutableStateOf(VoicePrefs.whileLocked(context)) }
    // Always switchable, voice or not; with it off nothing is said about voices.
    VoiceSwitch("Talk back to me", "Rem reads its replies aloud with an offline voice on this phone. Voices that need the internet are never used.", speak, "voiceSpeak") {
        speak = it; VoicePrefs.setSpeak(context, it)
    }
    if (speak) VoiceOutputStatus()
    Column(Modifier.testTag("voiceTidyStatus")) {
        Text("Tidy voice messages", style = MaterialTheme.typography.bodyLarge)
        Text(if (client.voiceTidy) "On. Whenever you pause, your desktop tidies what you've said so far with the model chosen in its Settings, and the words appear while you keep talking. If it can't, they appear as heard."
            else "Off. Turn on Tidy voice messages in desktop Settings, under Rem, to fix punctuation and misheard names before your words appear.",
            style = MaterialTheme.typography.bodySmall, color = muted)
    }
    ScreenSetting(assistant)
    VoiceSwitch("Show replies on the lock screen", "Off: Rem's replies stay hidden and silent until you unlock. Sending always needs an unlock.", whileLocked, "voiceWhileLocked") {
        whileLocked = it; VoicePrefs.setWhileLocked(context, it)
    }

    HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
    Text("Other ways to start", style = MaterialTheme.typography.titleSmall)
    Text("Long-press the Raticode icon and choose Talk to Rem, or tap Talk to Rem on the background sync notification.", style = MaterialTheme.typography.bodySmall, color = muted)
    if (Build.VERSION.SDK_INT >= 33) TextButton(onClick = { addTile(context) }) { Text("Add Talk to Rem tile") }
    else Text("Edit your Quick Settings and drag in the Talk to Rem tile.", style = MaterialTheme.typography.bodySmall, color = muted)
}

@Composable
private fun VoiceSwitch(title: String, detail: String, checked: Boolean, tag: String, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).testTag(tag).toggleable(checked, role = Role.Switch, onValueChange = onChange),
        verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.bodyLarge)
            Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Switch(checked, null)
    }
}

/** Whether this phone can recognize the user's language offline, with a download when Android offers one. */
@RequiresApi(31)
@Composable
private fun SpeechStatus() {
    val context = LocalContext.current
    // Compose's locale, so the status follows a change of the phone's language.
    val language = LocalLocale.current.platformLocale
    val available = remember { SpeechRecognizer.isOnDeviceRecognitionAvailable(context) }
    var support by remember { mutableStateOf<String?>(null) }
    val recognizer = remember { if (available && Build.VERSION.SDK_INT >= 33) SpeechRecognizer.createOnDeviceSpeechRecognizer(context) else null }
    DisposableEffect(recognizer) { onDispose { recognizer?.destroy() } }
    val request = remember(language) { Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).putExtra(RecognizerIntent.EXTRA_LANGUAGE, language.toLanguageTag()) }
    LaunchedEffect(recognizer, request) {
        if (recognizer != null && Build.VERSION.SDK_INT >= 33) runCatching {
            recognizer.checkRecognitionSupport(request, context.mainExecutor, object : RecognitionSupportCallback {
                override fun onSupportResult(result: RecognitionSupport) { support = speechSupport(result, language) }
                override fun onError(error: Int) { support = "unknown" }
            })
        }.onFailure { support = "unknown" }
    }
    Text("Speech recognition", style = MaterialTheme.typography.titleSmall)
    Text(when {
        !available -> "This phone has no on-device speech recognition, so voice mode can only take typed requests. Raticode doesn't use online speech services."
        support == "installed" -> "Offline speech for ${language.displayName} is installed."
        support == "pending" -> "Offline speech for ${language.displayName} is downloading."
        support == "download" -> "Offline speech for ${language.displayName} can be downloaded."
        support == "unsupported" -> "This phone can't recognize ${language.displayName} offline. Change the phone's language or type to Rem."
        else -> "Recognized on this phone. If voice mode can't hear you, install an offline speech language in Android settings."
    }, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.testTag("voiceSpeechStatus"))
    if (recognizer != null && support == "download" && Build.VERSION.SDK_INT >= 33)
        TextButton(onClick = { runCatching { recognizer.triggerModelDownload(request) }; support = "pending" }) { Text("Download offline speech") }
}

@RequiresApi(33)
private fun speechSupport(result: RecognitionSupport, language: Locale): String {
    fun has(tags: List<String>) = tags.any { Locale.forLanguageTag(it).language == language.language }
    return when {
        has(result.installedOnDeviceLanguages) -> "installed"
        has(result.pendingOnDeviceLanguages) -> "pending"
        has(result.supportedOnDeviceLanguages) -> "download"
        else -> "unsupported"
    }
}

/**
 * "Let Rem read your screen": off by default, and turning it on first says what is read, where it goes and what is
 * never shared. Android's own "Use text from screen" switch for the digital assistant must also be on.
 */
@RequiresApi(31)
@Composable
private fun ScreenSetting(assistant: Boolean) {
    val context = LocalContext.current
    var on by remember { mutableStateOf(VoicePrefs.screen(context)) }
    var asking by remember { mutableStateOf(false) }
    fun set(value: Boolean) { on = value; VoicePrefs.setScreen(context, value); RemVoiceInteractionService.screenSettingChanged() }
    VoiceSwitch("Let Rem read your screen", "Adds a share-screen button to voice mode, so you can ask about what you're looking at. Off unless you turn it on.", on, "voiceScreenSetting") {
        if (it) asking = true else set(false)
    }
    if (on) Text(if (assistant) "Hold the power button or use the assist gesture over an app, tap the screen button at the bottom, then ask, like “what am I looking at?”. The screen is shared only with that message. If Rem can't see it, turn on “Use text from screen” in the digital assistant settings."
        else "Works once Raticode is your digital assistant.",
        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.testTag("voiceScreenHelp"))
    if (asking) AlertDialog(onDismissRequest = { asking = false },
        title = { Text("Let Rem read your screen?") },
        text = { Text(SCREEN_DISCLOSURE, modifier = Modifier.testTag("voiceScreenDisclosure")) },
        confirmButton = { TextButton(onClick = { asking = false; set(true) }, modifier = Modifier.testTag("voiceScreenAllow")) { Text("Turn on") } },
        dismissButton = { TextButton(onClick = { asking = false }) { Text("Not now") } })
}

internal const val SCREEN_DISCLOSURE = "When you open Rem with the power button or assist gesture, Android gives Raticode the text of the app you're looking at. It stays in memory on this phone unless you tap the screen button in voice mode; then Raticode sends it, end-to-end encrypted, only with that message, and the phone keeps it, encrypted, only until your desktop receives it. Your desktop passes it to the AI provider Rem uses, which reads it to answer; that provider's own records may keep it. Neither device saves the screen text; Rem's answer is kept in its chat like any reply. Work only happens when your own words ask for it, never because the screen says so. Images, password fields, the lock screen and apps that keep their screen private are never shared."

@Composable
private fun VoiceOutputStatus() {
    val context = LocalContext.current
    val speaker = remember { RemSpeaker(context) }
    DisposableEffect(speaker) { onDispose { speaker.shutdown() } }
    val status = VoiceLogic.voiceStatus(talkBack = true, ready = speaker.ready) ?: return
    when (speaker.ready) {
        null -> Text(status, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        true -> Text(status, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.testTag("voiceOutputStatus"))
        false -> {
            Text(status, style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.testTag("voiceOutputStatus"))
            TextButton(onClick = {
                val install = Intent(TextToSpeech.Engine.ACTION_INSTALL_TTS_DATA).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                runCatching { context.startActivity(install) }.onFailure { context.startActivity(Intent(Settings.ACTION_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
            }) { Text("Install a voice") }
        }
    }
}


@RequiresApi(33)
private fun addTile(context: Context) {
    runCatching {
        context.getSystemService(StatusBarManager::class.java).requestAddTileService(ComponentName(context, RemVoiceTile::class.java),
            "Talk to Rem", Icon.createWithResource(context, R.drawable.ic_mic), context.mainExecutor) { }
    }
}
