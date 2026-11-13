package dev.raticode.mobile.ui

import android.app.KeyguardManager
import androidx.annotation.RequiresApi
import androidx.compose.animation.animateContentSize
import androidx.compose.animation.animateColorAsState
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.addPathNodes
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.data.LongText
import dev.raticode.mobile.live.PhoneAction
import dev.raticode.mobile.live.RemHome
import dev.raticode.mobile.live.FileKinds
import dev.raticode.mobile.live.UploadQueue
import dev.raticode.mobile.voice.DeskReadiness
import dev.raticode.mobile.voice.ScreenShare
import dev.raticode.mobile.voice.ScreenText
import dev.raticode.mobile.voice.VoiceController
import dev.raticode.mobile.voice.CommandStep
import dev.raticode.mobile.voice.CommandPlan
import dev.raticode.mobile.live.PhonePeople
import dev.raticode.mobile.voice.VoiceLogic
import dev.raticode.mobile.voice.VoicePrefs
import dev.raticode.mobile.voice.VoiceState

/**
 * What only the hosting surface can do: unlock, ask for the microphone, open the app or a tapped phone action,
 * pick files to attach, close.
 */
interface VoiceActions {
    fun unlock()
    fun allowMic()
    fun openRem()
    fun openPhoneAction(action: PhoneAction, report: (String?) -> Unit)
    /** Opens the contact picker for a call or text card naming a person; [remember] saves the pick for its words. */
    fun pickContact(action: PhoneAction, remember: Boolean)
    /** Asks Android for a permission a phone command needs, such as calling or texting. */
    fun requestPermission(permission: String)
    /** Opens another app for a phone command, such as the clock or maps; [report] gets a problem sentence. */
    fun launch(spec: dev.raticode.mobile.live.PhoneIntentSpec, report: (String?) -> Unit)
    fun attachFiles()
    fun close()
}

internal object VoiceIcons {
    private fun lucide(name: String, vararg paths: String) = ImageVector.Builder(name, 24.dp, 24.dp, 24f, 24f).apply {
        paths.forEach { addPath(addPathNodes(it), fill = null, stroke = SolidColor(Color.Black), strokeLineWidth = 1.75f,
            strokeLineCap = StrokeCap.Round, strokeLineJoin = StrokeJoin.Round) }
    }.build()
    val Mic = lucide("Mic", "M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z", "M19 10v2a7 7 0 0 1-14 0v-2", "M12 19v3")
    val Lock = lucide("Lock", "M5 11h14a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2Z", "M7 11V7a5 5 0 0 1 10 0v4")
    val Close = lucide("Close", "M18 6 6 18", "M6 6l12 12")
    val Check = lucide("Check", "M20 6 9 17l-5-5")
    val Keyboard = lucide("Keyboard", "M4 5h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2Z",
        "M6 9h.01", "M10 9h.01", "M14 9h.01", "M18 9h.01", "M7 13h.01", "M11 13h.01", "M15 13h.01", "M8 16h8")
    val Send = lucide("Send", "M12 19V5", "m5 12 7-7 7 7")
    val Chat = lucide("Chat", "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z")
    val Stop = lucide("Stop", "M7 7h10v10H7Z")
    val Retry = lucide("Retry", "M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8", "M3 3v5h5")
    val Speaker = lucide("Speaker", "M11 5 6 9H2v6h4l5 4V5Z", "M15.54 8.46a5 5 0 0 1 0 7.07", "M19.07 4.93a10 10 0 0 1 0 14.14")
    val SpeakerOff = lucide("SpeakerOff", "M11 5 6 9H2v6h4l5 4V5Z", "m22 9-6 6", "m16 9 6 6")
    val Attach = lucide("Attach", "m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48")
    val Screen = lucide("Screen", "M7 2h10a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2Z", "M12 18h.01")
}

/**
 * Voice mode's own look in light and dark themes alike: a night-indigo card with a thin neon edge, Raticode's
 * indigo running into magenta for the main action, and the dotted line's cyan-to-magenta glow.
 */
private object Look {
    val Card = Color(0xFF100D1C)
    val Raised = Color(0xFF1D1930)
    val Ink = Color(0xFFF4F1FF)
    val Muted = Color(0xFFA9A3C8)
    val Accent = Color(0xFF5B4CF0)
    val AccentHot = Color(0xFFA52FC4)
    val AccentLight = Color(0xFFB9B4FF)
    val Error = Color(0xFFFF9A8F)
    val Edge = Brush.linearGradient(listOf(DotColors.Cyan.copy(alpha = .45f), DotColors.Lilac.copy(alpha = .25f), DotColors.Magenta.copy(alpha = .45f)))
}

/**
 * Rem's voice panel, shown by the assistant overlay and the voice screen alike. It reads state from [controller]
 * and never talks to the desktop itself. Words on screen are the user's and Rem's; controls are icons with spoken
 * labels, and the state is announced to screen readers rather than printed.
 */
@RequiresApi(29)
@Composable
fun VoicePanel(controller: VoiceController, actions: VoiceActions) {
    val state = controller.state
    val quiet = remember { MutableInteractionSource() }
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.BottomCenter) {
        // Tapping outside closes, like other assistants. Screen readers use the close button instead.
        Spacer(Modifier.matchParentSize().background(Color.Black.copy(alpha = .3f))
            .clickable(interactionSource = quiet, indication = null) { actions.close() }.clearAndSetSemantics { })
        // The card takes its own taps, so they don't reach the background behind it.
        Surface(Modifier.windowInsetsPadding(WindowInsets.safeDrawing).padding(horizontal = 10.dp, vertical = 10.dp)
            .widthIn(max = 520.dp).fillMaxWidth().pointerInput(Unit) { detectTapGestures { } }.testTag("voicePanel"),
            shape = RoundedCornerShape(28.dp), color = Look.Card, contentColor = Look.Ink, shadowElevation = 12.dp,
            border = BorderStroke(1.dp, Look.Edge)) {
            Box {
                // Announced, not shown: the animation and the controls say it on screen.
                Box(Modifier.size(1.dp).testTag("voiceStatus").semantics {
                    contentDescription = status(controller, state); liveRegion = LiveRegionMode.Polite
                })
                Column(Modifier.animateContentSize().heightIn(max = 560.dp).verticalScroll(rememberScrollState())
                    .padding(horizontal = 14.dp, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Body(controller, state, actions)
                }
            }
        }
    }
}

@RequiresApi(29)
internal fun status(controller: VoiceController, state: VoiceState): String = when (state) {
    VoiceState.Idle -> "Starting"
    is VoiceState.Listening -> if (state.paused) "Paused" else "Listening"
    is VoiceState.Tidying -> "Tidying your words"
    is VoiceState.Review -> "Check your message"
    is VoiceState.NeedsMic -> "Microphone needed"
    is VoiceState.NeedsUnlock -> "Unlock to send"
    is VoiceState.Sending -> when (controller.readiness()) {
        DeskReadiness.LOADING, DeskReadiness.CONNECTING -> "Connecting to your desktop"
        else -> "Sending"
    }
    is VoiceState.Waiting -> {
        val home = controller.client.desktopViews["rem.home"] as? RemHome
        when {
            !VoiceLogic.accepted(home, state.request) && !controller.client.connected -> "Queued on this phone"
            home?.turnState == "replying" -> home.turnLabel.ifBlank { "Rem is thinking" }
            else -> "Waiting for Rem"
        }
    }
    is VoiceState.Replied -> "Rem replied"
    is VoiceState.Command -> when (val step = state.step) {
        is CommandStep.Countdown -> (if (step.call) "Calling " else "Texting ") + step.who
        is CommandStep.Choose -> "Which one?"
        is CommandStep.Pick -> "Choose a contact"
        is CommandStep.NeedsPermission -> if (step.call) "Calling permission needed" else "Texting permission needed"
        is CommandStep.Launch -> step.doing
        is CommandStep.Working -> step.text
        is CommandStep.Done -> step.text
        is CommandStep.Problem -> "Couldn't do that"
    }
    is VoiceState.Failed -> if (state.text.isNotBlank()) "Not sent" else if (state.problem == VoiceController.NO_SPEECH) "Didn't catch that" else "Voice mode can't listen"
}

@RequiresApi(29)
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun Body(controller: VoiceController, state: VoiceState, actions: VoiceActions) {
    val context = LocalContext.current
    when (state) {
        VoiceState.Idle -> {
            Context(controller)
            Line(listening = false, level = { 0f }, label = "Talk to Rem", onClick = { controller.listen() })
            Controls(actions, extra = { AttachTool(controller, actions); ShareScreen(controller); TalkBack(controller) })
        }
        is VoiceState.Listening -> {
            // With tidying on, new words stay hidden until they are tidied; words already in the box still show, and
            // at each pause the words tidied so far replace them. The moving line says it is listening, so no note
            // explains the hiding.
            val shown = if (state.hidden) state.preview.ifBlank { state.kept } else state.heard
            if (shown.isNotBlank()) Text(shown, style = MaterialTheme.typography.bodyLarge, color = Look.Ink,
                modifier = Modifier.fillMaxWidth().padding(horizontal = 4.dp).testTag("voiceHeard"))
            Context(controller)
            Line(listening = !state.paused, level = { controller.level },
                label = if (state.paused) "Keep talking" else "Pause listening", onClick = controller::toggleListening)
            Controls(actions, extra = {
                // The keyboard: stop listening and edit the words (tidied first, when tidying is on) before sending.
                Tool(VoiceIcons.Keyboard, if (state.heard.isBlank()) "Type instead" else "Edit with the keyboard", "voiceType") { controller.type(state.heard) }
                AttachTool(controller, actions)
                ShareScreen(controller)
                TalkBack(controller, optional = true)
            },
                // No Done step: Send stays bottom right, as in the box below. It finishes the last words and sends.
                // It waits until something has been said and while a tidy round is out, so it never sends nothing or
                // words that are halfway through tidying.
                end = { Primary(VoiceIcons.Send, "Send", "voiceSend",
                    enabled = VoiceLogic.canSendSpoken(state.heard, controller.tidyPending, controller.importing.waiting.isEmpty()),
                    onClick = controller::sendSpoken) })
        }
        is VoiceState.Review -> {
            if (state.notice.isNotBlank()) Note(state.notice, error = true, tag = "voiceNotice")
            var text by remember(state.text) { mutableStateOf(state.text) }
            val focus = remember { FocusRequester() }
            // Typing from the start, or the keyboard button: put the cursor in the box.
            LaunchedEffect(Unit) { if (state.text.isBlank() || state.editing) runCatching { focus.requestFocus() } }
            Surface(color = Look.Raised, shape = RoundedCornerShape(20.dp), modifier = Modifier.fillMaxWidth()) {
                Box(Modifier.padding(horizontal = 14.dp, vertical = 12.dp)) {
                    if (text.isEmpty()) Text("Message Rem", style = MaterialTheme.typography.bodyLarge, color = Look.Muted)
                    BasicTextField(text, { text = it.take(LongText.MAX_CHARS) },
                        Modifier.fillMaxWidth().heightIn(min = 24.dp).focusRequester(focus).testTag("voiceInput")
                            .semantics { contentDescription = "Message to Rem" },
                        textStyle = MaterialTheme.typography.bodyLarge.merge(TextStyle(color = Look.Ink)), cursorBrush = SolidColor(Look.AccentLight), maxLines = 8)
                }
            }
            VoiceLogic.lengthNote(text.length)?.let { Note(it, tag = "voiceLength") }
            Context(controller)
            Controls(actions, extra = {
                // Keeps what is in the box and adds the new words after it.
                Tool(VoiceIcons.Mic, if (text.isBlank()) "Talk instead" else "Keep talking", "voiceKeepTalking") { controller.listen(keep = text) }
                AttachTool(controller, actions)
                ShareScreen(controller)
                TalkBack(controller, optional = true)
            }, end = { Primary(VoiceIcons.Send, "Send", "voiceSend",
                enabled = VoiceLogic.sendable(text, controller.attachments) && controller.importing.waiting.isEmpty()) { controller.send(text) } })
        }
        is VoiceState.Tidying -> {
            state.preview.ifBlank { state.kept }.takeIf { it.isNotBlank() }?.let { Said(it) }
            Context(controller)
            // Just the sweeping line; screen readers still hear "Tidying your words" from the status.
            Thinking()
            Controls(actions, extra = { TalkBack(controller) })
        }
        is VoiceState.NeedsMic -> {
            Note(if (state.denied) "Microphone is off for Raticode in Android settings." else "Raticode needs the microphone to hear you.")
            Controls(actions, extra = { Tool(VoiceIcons.Keyboard, "Type instead", "voiceType") { controller.type() } },
                end = { Pill("Allow", "voiceAllowMic", onClick = actions::allowMic) })
        }
        is VoiceState.NeedsUnlock -> {
            Said(state.text)
            Context(controller)
            Controls(actions, extra = { Icon(VoiceIcons.Lock, null, Modifier.padding(12.dp).size(22.dp), tint = Look.Muted) },
                end = { Pill("Unlock to send", "voiceUnlock", onClick = actions::unlock) })
        }
        is VoiceState.Sending -> {
            Said(state.text, controller.sentAsFile)
            Context(controller)
            Thinking()
            if (controller.readiness().let { it == DeskReadiness.LOADING || it == DeskReadiness.CONNECTING }) Note("Connecting…")
        }
        is VoiceState.Waiting -> {
            Said(state.text, controller.sentAsFile)
            val draft = controller.client.remDraft
            val locked = context.getSystemService(KeyguardManager::class.java).isDeviceLocked
            if (draft.isNotBlank() && VoiceLogic.showReply(locked, VoicePrefs.whileLocked(context))) Text(draft, style = MaterialTheme.typography.bodyLarge)
            else Thinking()
            val queued = !VoiceLogic.accepted(controller.client.desktopViews["rem.home"] as? RemHome, state.request) && !controller.client.connected
            if (queued) Note("Queued · sends when your desktop is reachable")
            Controls(actions, extra = { Tool(VoiceIcons.Chat, "Open Rem's chat", "voiceOpenChat", onClick = actions::openRem) })
        }
        is VoiceState.Replied -> {
            Said(state.text, controller.sentAsFile)
            if (state.hidden) {
                Controls(actions, extra = { Icon(VoiceIcons.Lock, null, Modifier.padding(12.dp).size(22.dp), tint = Look.Muted) },
                    end = { Pill("Unlock to see", "voiceUnlockReply", onClick = actions::unlock) })
            } else {
                Text(state.reply, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.testTag("voiceReply"))
                val speaker = controller.speaker
                VoiceLogic.replyNote(controller.talkBack, speaker?.ready)?.let { Note(it, tag = "voiceNotReadAloud") }
                if (state.suggestions.isNotEmpty()) FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    state.suggestions.forEach { suggestion ->
                        Surface(onClick = { controller.sendSuggestion(suggestion) }, shape = RoundedCornerShape(16.dp), color = Look.Raised) {
                            Text(suggestion, Modifier.padding(horizontal = 12.dp, vertical = 8.dp), style = MaterialTheme.typography.bodyMedium)
                        }
                    }
                }
                // Only shown with the reply, so a locked phone shows none unless the user allows replies there; opening or
                // picking still asks for an unlock, and saved people's names stay hidden until then.
                val keyguard = context.getSystemService(KeyguardManager::class.java)
                var locked by remember { mutableStateOf(keyguard.isDeviceLocked) }
                LaunchedEffect(locked) { while (locked) { kotlinx.coroutines.delay(1000); locked = keyguard.isDeviceLocked } }
                PhoneActionCards(state.actions, Look.Muted, actions::openPhoneAction, actions::pickContact, hideNames = locked)
                // Rem's turn on the line while the reply is read aloud.
                if (speaker?.speaking == true) VoiceDots(DotMood.SPEAKING, { 0f }, height = 44.dp)
                Controls(actions, extra = {
                    Tool(VoiceIcons.Chat, "Open Rem's chat", "voiceOpenChat", onClick = actions::openRem)
                    TalkBack(controller)
                    if (speaker?.speaking == true) Tool(VoiceIcons.Stop, "Stop reading", "voiceStopReading", onClick = controller::stopSpeaking)
                }, end = { Primary(VoiceIcons.Mic, "Talk again", "voiceTalkAgain") { controller.listen() } })
            }
        }
        is VoiceState.Command -> CommandBody(controller, state, actions)
        is VoiceState.Failed -> {
            Note(state.problem, error = true, tag = "voiceProblem")
            if (state.text.isNotBlank()) Said(state.text)
            Context(controller)
            Controls(actions, extra = {
                Tool(VoiceIcons.Keyboard, "Type instead", "voiceType") { controller.type(state.text) }
                if (state.canRetry && state.text.isNotBlank()) Tool(VoiceIcons.Retry, "Try again", "voiceRetry") { controller.send(state.text) }
            }, end = { Primary(VoiceIcons.Mic, if (state.text.isBlank()) "Talk again" else "Keep talking", "voiceTalkAgain") { controller.listen(keep = state.text) } })
        }
    }
}

/**
 * A phone command carried out on this phone: the call or text counting down with Cancel, a choice between people, a
 * contact to pick, a permission to grant, another app opening, or what happened. Nothing here reaches the desktop.
 */
@RequiresApi(29)
@Composable
private fun CommandBody(controller: VoiceController, state: VoiceState.Command, actions: VoiceActions) {
    Said(state.said)
    val talkAgain: @Composable () -> Unit = { Primary(VoiceIcons.Mic, "Talk again", "voiceTalkAgain") { controller.listen() } }
    when (val step = state.step) {
        is CommandStep.Countdown -> {
            Text((if (step.call) "Calling " else "Texting ") + step.who, style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.testTag("voiceCommandWho").semantics { liveRegion = LiveRegionMode.Polite })
            if (!step.call) Text(step.body, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.testTag("voiceCommandBody"))
            // The bar runs down to the moment it goes; Cancel stops it, and closing voice mode does too.
            var left by remember(step) { mutableFloatStateOf(1f) }
            LaunchedEffect(step) {
                while (left > 0f) { kotlinx.coroutines.delay(50); left = ((step.endsAt - android.os.SystemClock.elapsedRealtime()).toFloat() / step.total).coerceIn(0f, 1f) }
            }
            LinearProgressIndicator(progress = { left }, modifier = Modifier.fillMaxWidth().height(3.dp), color = Look.Accent, trackColor = Look.Raised)
            Controls(actions, extra = {
                Tool(VoiceIcons.Stop, "Cancel", "voiceCommandCancel") { controller.cancel() }
                if (!step.call) Tool(VoiceIcons.Keyboard, "Edit the message", "voiceCommandEdit") { controller.editCommand() }
            }, end = { Pill(if (step.call) "Call now" else "Send now", "voiceCommandNow", onClick = controller::now) })
        }
        is CommandStep.Choose -> {
            Text("Which " + step.words + "?", style = MaterialTheme.typography.titleMedium)
            step.people.forEachIndexed { index, person ->
                Surface(onClick = { controller.choose(person) }, shape = RoundedCornerShape(16.dp), color = Look.Raised,
                    modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp).testTag("voiceCommandChoice$index")) {
                    Text(PhonePeople.describe(person) + " · ending " + PhonePeople.ending(person.number),
                        Modifier.padding(horizontal = 14.dp, vertical = 12.dp), style = MaterialTheme.typography.bodyLarge)
                }
            }
            Controls(actions, extra = { Tool(VoiceIcons.Chat, "Ask Rem instead", "voiceCommandAskRem") { controller.askRem(state.said) } },
                end = { Pill("Someone else", "voiceCommandPick") { actions.pickContact(step.let { CommandPlan.card(it.call, it.words, it.body) }, false) } })
        }
        is CommandStep.Pick -> {
            Text("No one is saved as “" + step.words + "” yet.", style = MaterialTheme.typography.titleMedium)
            Note("Choose them in your contacts. Raticode remembers them as “" + step.words + "”, and the number stays on this phone.")
            Controls(actions, extra = { Tool(VoiceIcons.Chat, "Ask Rem instead", "voiceCommandAskRem") { controller.askRem(state.said) } },
                end = { Pill("Choose contact", "voiceCommandPick") { actions.pickContact(step.card, true) } })
        }
        is CommandStep.NeedsPermission -> {
            Text(if (step.call) "Let Raticode place calls" else "Let Raticode send texts", style = MaterialTheme.typography.titleMedium)
            Note(if (step.call) "Then “call " + step.who.substringBefore(" · ") + "” rings straight away, after a moment to cancel. Without it, the dialer opens."
                else "Then texts you dictate go straight away, after a moment to cancel. Without it, a draft opens instead.")
            Controls(actions, extra = {
                Tool(VoiceIcons.Keyboard, if (step.call) "Open the dialer instead" else "Open a draft instead", "voiceCommandDraft") { controller.permissionAnswered(false) }
            }, end = { Pill("Allow", "voiceCommandAllow") {
                actions.requestPermission(if (step.call) android.Manifest.permission.CALL_PHONE else android.Manifest.permission.SEND_SMS)
            } })
        }
        is CommandStep.Launch -> {
            Text(step.doing, style = MaterialTheme.typography.titleMedium, modifier = Modifier.testTag("voiceCommandDoing"))
            // Opens once for this step; the voice screen closes when the other app is up.
            LaunchedEffect(step) { actions.launch(step.spec) { controller.launched(it) } }
            Controls(actions)
        }
        is CommandStep.Working -> { Text(step.text, style = MaterialTheme.typography.titleMedium); Thinking(); Controls(actions) }
        is CommandStep.Done -> {
            Text(step.text, style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.testTag("voiceCommandDone").semantics { liveRegion = LiveRegionMode.Polite })
            // Words such as "pause the music" may have been meant for Rem; one tap sends them there instead.
            Controls(actions, extra = {
                if (step.askRem) Tool(VoiceIcons.Chat, "Ask Rem instead", "voiceCommandAskRem") { controller.askRem(state.said) }
                TalkBack(controller)
            }, end = talkAgain)
        }
        is CommandStep.Problem -> {
            Note(step.text, error = true, tag = "voiceCommandProblem")
            Controls(actions, extra = {
                if (step.askRem) Tool(VoiceIcons.Chat, "Ask Rem instead", "voiceCommandAskRem") { controller.askRem(state.said) }
                step.fallback?.let { spec -> Tool(VoiceIcons.Retry, "Open it in the app instead", "voiceCommandFallback") {
                    controller.openInstead(spec) } }
            }, end = talkAgain)
        }
    }
}

/**
 * Turns reading replies aloud on or off from voice mode itself. Works with or without an installed voice. An
 * [optional] one gives way on narrow screens; settings and the other voice screens still have it.
 */
@RequiresApi(29)
@Composable
private fun TalkBack(controller: VoiceController, optional: Boolean = false) {
    if (optional && !LocalRoomy.current) return
    val on = controller.talkBack
    IconToggleButton(checked = on, onCheckedChange = controller::talkBack,
        modifier = Modifier.testTag("voiceTalkBack").semantics { contentDescription = "Talk back to me"; stateDescription = if (on) "On" else "Off" }) {
        Icon(if (on) VoiceIcons.Speaker else VoiceIcons.SpeakerOff, null, Modifier.size(22.dp), tint = Look.Muted)
    }
}

/** Opens the same attach menu as Rem's chat. The phone must be unlocked to pick files. */
@RequiresApi(29)
@Composable
private fun AttachTool(controller: VoiceController, actions: VoiceActions) {
    IconButton(onClick = { controller.attachNotice = null; actions.attachFiles() }, enabled = controller.attachRoom > 0,
        modifier = Modifier.testTag("voiceAttach").semantics { contentDescription = "Attach files" }) {
        Icon(VoiceIcons.Attach, null, Modifier.size(22.dp), tint = if (controller.attachRoom > 0) Look.Muted else Look.Muted.copy(alpha = .4f))
    }
}

/**
 * Share the screen Rem was opened over with this message. Off every time Rem opens; lit while on. Shown once screen
 * sharing is allowed in Voice assistant settings, which is where Android is told it may hand the screen over.
 */
@RequiresApi(29)
@Composable
private fun ShareScreen(controller: VoiceController) {
    if (!VoicePrefs.screen(LocalContext.current)) return
    val on = controller.shareScreen
    val app = (controller.screen as? ScreenShare.Ready)?.screen?.app
    IconToggleButton(checked = on, onCheckedChange = { controller.toggleShareScreen() },
        modifier = Modifier.testTag("voiceShareScreen").semantics {
            contentDescription = "Share screen"
            stateDescription = if (on) "Sharing ${app ?: "the screen"}" else "Not shared"
        }) {
        Box(Modifier.size(36.dp).background(if (on) Brush.linearGradient(listOf(Look.Accent, Look.AccentHot)) else SolidColor(Color.Transparent), CircleShape),
            contentAlignment = Alignment.Center) {
            Icon(VoiceIcons.Screen, null, Modifier.size(20.dp), tint = if (on) Color.White else Look.Muted)
        }
    }
}

/**
 * What goes with the message besides the words: attached files, files still being read, and the shared screen's
 * app. Notes appear only after the user did something: a refused file, or a screen that can't be shared.
 */
@RequiresApi(29)
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun Context(controller: VoiceController) {
    val files = controller.client.files
    if (controller.attachments.isNotEmpty()) FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp),
        modifier = Modifier.testTag("voiceAttachments")) {
        controller.attachments.forEach { id ->
            val file = files.firstOrNull { it.id == id }
            val sending = file?.state in setOf("offered", "receiving")
            AttachmentTileView(file?.name ?: "Attachment", file?.size ?: 0, null, FileKinds.previewMode(file?.name ?: ""),
                sending = sending || file == null, missing = false, onOpen = {}, onRemove = { controller.removeAttachment(id) },
                progress = file?.takeIf { sending }?.let { UploadQueue.progress(it.state, it.bytes, it.size) },
                failed = file?.state in setOf("failed", "cancelled"), offline = sending && !controller.client.connected)
        }
    }
    val reading = controller.importing.waiting.size
    if (reading > 0) Note(if (reading == 1) "Reading 1 file…" else "Reading $reading files…", tag = "voiceReading")
    controller.attachNotice?.let { Note(it.text, tag = "voiceAttachNotice") }
    val app = (controller.screen as? ScreenShare.Ready)?.screen?.app
    if (controller.shareScreen && app != null) Note("Sharing the screen from $app", tag = "voiceScreenShared")
    controller.screenNote?.let { Note(it, tag = "voiceScreenNote") }
}

/** Whether the bottom row has room for optional tools and a labelled main button. */
private val LocalRoomy = compositionLocalOf { true }

/** The bottom row: close and any tools on the left, the main action on the right. */
@Composable
private fun Controls(actions: VoiceActions, extra: @Composable RowScope.() -> Unit = {}, end: @Composable () -> Unit = {}) {
    // Small phones (about 320 dp wide) fit close, four tools and a round main button; wider ones get labels too.
    BoxWithConstraints(Modifier.fillMaxWidth()) {
        CompositionLocalProvider(LocalRoomy provides (maxWidth >= 340.dp)) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Tool(VoiceIcons.Close, "Close voice mode", "voiceClose", onClick = actions::close)
                extra()
                Spacer(Modifier.weight(1f))
                end()
            }
        }
    }
}

@Composable
private fun Tool(icon: ImageVector, label: String, tag: String, onClick: () -> Unit) {
    IconButton(onClick = onClick, modifier = Modifier.testTag(tag).semantics { contentDescription = label }) {
        Icon(icon, null, Modifier.size(22.dp), tint = Look.Muted)
    }
}

/** The main action: an indigo-to-magenta circle, or a slim pill when it carries a word. */
@Composable
private fun Primary(icon: ImageVector, description: String, tag: String, label: String? = null, enabled: Boolean = true, onClick: () -> Unit) {
    val from by animateColorAsState(if (enabled) Look.Accent else Look.Raised, label = "primaryFrom")
    val to by animateColorAsState(if (enabled) Look.AccentHot else Look.Raised, label = "primaryTo")
    Surface(onClick = onClick, enabled = enabled, shape = CircleShape, color = Color.Transparent, contentColor = Color.White,
        modifier = Modifier.heightIn(min = 48.dp).widthIn(min = 48.dp).testTag(tag).semantics { contentDescription = description }) {
        Row(Modifier.background(Brush.linearGradient(listOf(from, to)), CircleShape).heightIn(min = 48.dp)
            .padding(horizontal = if (label == null || !LocalRoomy.current) 12.dp else 18.dp), verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.Center) {
            Icon(icon, null, Modifier.size(22.dp), tint = if (enabled) Color.White else Look.Muted)
            if (label != null && LocalRoomy.current) { Spacer(Modifier.width(6.dp)); Text(label, style = MaterialTheme.typography.labelLarge) }
        }
    }
}

@Composable
private fun Pill(label: String, tag: String, onClick: () -> Unit) {
    Surface(onClick = onClick, shape = CircleShape, color = Color.Transparent, contentColor = Color.White,
        modifier = Modifier.heightIn(min = 48.dp).testTag(tag)) {
        Box(Modifier.background(Brush.linearGradient(listOf(Look.Accent, Look.AccentHot)), CircleShape).heightIn(min = 48.dp)
            .padding(horizontal = 20.dp), contentAlignment = Alignment.Center) { Text(label, style = MaterialTheme.typography.labelLarge) }
    }
}

@Composable
private fun Note(text: String, error: Boolean = false, tag: String = "") {
    Text(text, style = MaterialTheme.typography.bodySmall, color = if (error) Look.Error else Look.Muted,
        modifier = if (tag.isEmpty()) Modifier else Modifier.testTag(tag))
}

/** The user's own words, as a bubble on the right. */
@Composable
private fun Said(text: String, asFile: Boolean = false) {
    if (text.isBlank()) return
    // Long words go to Rem as a text file; their first words stand for them here, as in the message box.
    val long = VoiceLogic.asFile(text)
    Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) {
        Surface(color = Look.Raised, shape = RoundedCornerShape(18.dp, 18.dp, 4.dp, 18.dp)) {
            Column(Modifier.padding(horizontal = 14.dp, vertical = 10.dp)) {
                Text(if (long) "“${LongText.preview(text)}”" else text, Modifier.testTag("voiceSaid"), style = MaterialTheme.typography.bodyLarge)
                if (long && asFile) Text("${LongText.characters(text.trim().length)}, attached as a text file", Modifier.testTag("voiceSaidFile"),
                    style = MaterialTheme.typography.labelSmall, color = Look.Muted)
            }
        }
    }
}

/**
 * The dotted line as the listening control: it rises with your voice while listening, and rests dim around a
 * microphone when paused. Tapping it pauses or resumes listening; words already heard stay either way.
 */
@Composable
private fun Line(listening: Boolean, level: () -> Float, label: String, onClick: () -> Unit) {
    VoiceDots(if (listening) DotMood.LISTENING else DotMood.PAUSED, level,
        // No ripple: the line itself answers the tap by settling around the microphone or rising again.
        Modifier.clickable(remember { MutableInteractionSource() }, indication = null, onClickLabel = label, role = Role.Button, onClick = onClick)
            .testTag("voiceMic")
            .semantics { contentDescription = label; stateDescription = if (listening) "Listening" else "Paused" })
}

/** While the desktop and Rem work, a bright dot sweeps along the line. */
@Composable
private fun Thinking() = VoiceDots(DotMood.THINKING, { 0f }, height = 36.dp)
