package dev.raticode.mobile.ui

import android.content.Context
import android.content.Intent
import androidx.annotation.RequiresApi
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import dev.raticode.mobile.voice.AssistantSteps
import dev.raticode.mobile.voice.AssistantSupport
import dev.raticode.mobile.voice.VoiceActivity
import dev.raticode.mobile.voice.VoicePrefs
import dev.raticode.mobile.voice.VoiceSetup

/** What the offer asks of Android. Tests replace it to stand in for the phone's answers. */
interface VoiceSetupHost {
    fun support(): AssistantSupport
    /** False when the phone has none of the settings pages. */
    fun openSettings(): Boolean
    val steps: AssistantSteps
    fun tryVoice()
    fun askOnLaunch(): Boolean
    fun stopAsking()
}

@RequiresApi(29)
internal fun phoneSetupHost(context: Context) = object : VoiceSetupHost {
    override fun support() = VoiceSetup.support(context)
    override fun openSettings() = VoiceSetup.openAssistantSettings(context)
    override val steps = VoiceSetup.steps()
    override fun tryVoice() = context.startActivity(VoiceActivity.intent(context).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    override fun askOnLaunch() = VoicePrefs.offerSetup(context)
    override fun stopAsking() = VoicePrefs.setOfferSetup(context, false)
}

private enum class OfferStage { Deciding, Offer, InSettings, Selected, NotYet, NoSettings, Closed }

/**
 * Offered when Raticode opens on a paired phone that hasn't chosen Raticode as its assistant. Android requires the user
 * to make that choice, so Set up opens the closest settings page and, on return, says whether Raticode was chosen.
 * Not now asks again next time; Don't ask again stops the offer, which Voice assistant settings can turn back on.
 */
@RequiresApi(29)
@Composable
fun VoiceSetupOffer(paired: Boolean, onDone: () -> Unit, onOpenVoicePage: () -> Unit, host: VoiceSetupHost = phoneSetupHost(LocalContext.current)) {
    var stage by rememberSaveable { mutableStateOf(OfferStage.Deciding) }
    // Settles this launch's offer exactly once.
    fun finish() { if (stage != OfferStage.Closed) { stage = OfferStage.Closed; onDone() } }
    LaunchedEffect(paired) {
        // An unpaired phone pairs first; the offer waits for that within the same launch.
        if (stage == OfferStage.Deciding && paired) {
            if (VoiceSetup.offerOnLaunch(host.support(), paired, host.askOnLaunch())) stage = OfferStage.Offer else finish()
        }
    }
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    DisposableEffect(lifecycle) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME && stage == OfferStage.InSettings)
                stage = if (host.support() == AssistantSupport.SELECTED) OfferStage.Selected else OfferStage.NotYet
        }
        lifecycle.addObserver(observer)
        onDispose { lifecycle.removeObserver(observer) }
    }
    fun openSettings() { stage = if (host.openSettings()) OfferStage.InSettings else OfferStage.NoSettings }
    val close = { finish() }
    val body: @Composable (String) -> Unit = { text ->
        Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            text.split("\n\n").forEach { Text(it, style = MaterialTheme.typography.bodyMedium) }
        }
    }
    when (stage) {
        OfferStage.Offer -> AlertDialog(onDismissRequest = close, modifier = Modifier.testTag("voiceOffer"),
            title = { Text("Use Rem as your assistant?") },
            text = { body("Hold the power button or use the assist gesture to talk to Rem, even when Raticode is closed. Speech is recognized on this phone; only your words go to your desktop.\n\n" +
                "Android lets only you choose the assistant. ${host.steps.choose}") },
            confirmButton = { Button(onClick = ::openSettings, modifier = Modifier.testTag("voiceOfferSetUp")) { Text("Set up") } },
            dismissButton = { Row {
                TextButton(onClick = { host.stopAsking(); close() }, modifier = Modifier.testTag("voiceOfferNever")) { Text("Don't ask again") }
                TextButton(onClick = close, modifier = Modifier.testTag("voiceOfferLater")) { Text("Not now") }
            } })
        OfferStage.Selected -> AlertDialog(onDismissRequest = close, modifier = Modifier.testTag("voiceOfferSelected"),
            title = { Text("Rem is your assistant") },
            text = { body("Use the assist gesture to talk to Rem anywhere. ${host.steps.button}") },
            confirmButton = { Button(onClick = { close(); host.tryVoice() }) { Text("Try it") } },
            dismissButton = { TextButton(onClick = close) { Text("Done") } })
        OfferStage.NotYet -> AlertDialog(onDismissRequest = close, modifier = Modifier.testTag("voiceOfferNotYet"),
            title = { Text("Raticode isn't chosen yet") },
            text = { body("Your assistant hasn't changed. ${host.steps.choose}") },
            confirmButton = { Button(onClick = ::openSettings) { Text("Open settings again") } },
            dismissButton = { TextButton(onClick = close) { Text("Not now") } })
        OfferStage.NoSettings -> AlertDialog(onDismissRequest = close, modifier = Modifier.testTag("voiceOfferNoSettings"),
            title = { Text("Open Settings yourself") },
            text = { body("This phone didn't open its assistant settings. ${host.steps.choose}") },
            confirmButton = { Button(onClick = { close(); onOpenVoicePage() }) { Text("Voice assistant page") } },
            dismissButton = { TextButton(onClick = close) { Text("OK") } })
        else -> Unit
    }
}
