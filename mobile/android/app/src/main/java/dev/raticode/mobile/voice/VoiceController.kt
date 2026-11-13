package dev.raticode.mobile.voice

import android.Manifest
import android.app.Application
import android.app.KeyguardManager
import android.content.pm.PackageManager
import android.os.Build
import androidx.annotation.MainThread
import androidx.annotation.RequiresApi
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.core.content.ContextCompat
import dev.raticode.mobile.live.LiveClient
import dev.raticode.mobile.live.PeopleStore
import dev.raticode.mobile.live.PhonePeople
import dev.raticode.mobile.live.SavedPerson
import dev.raticode.mobile.live.LiveRuntime
import dev.raticode.mobile.live.RemHome
import dev.raticode.mobile.live.PhoneAction
import dev.raticode.mobile.live.PhoneActionList
import dev.raticode.mobile.live.RemMessage
import dev.raticode.mobile.live.TidyResult
import dev.raticode.mobile.live.VoiceTidy
import dev.raticode.mobile.data.AttachPlan
import dev.raticode.mobile.data.LongText
import dev.raticode.mobile.ui.AttachNotice
import dev.raticode.mobile.ui.ImportQueue
import dev.raticode.mobile.ui.Picked
import dev.raticode.mobile.ui.pickedText
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Job
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

/** One voice turn, as the panel shows it. Text is held in memory only. */
sealed interface VoiceState {
    data object Idle : VoiceState
    /**
     * Listening until the user taps Done. Pauses between sentences don't end it. [heard] includes earlier words kept
     * from before; [paused] means the microphone is off until the user taps it. With voice tidying on, [hidden] keeps
     * new words off the screen until they are tidied; [kept], the words already in the box, show. [preview] is
     * [kept] plus the words tidied at the last pause, and replaces [kept] on screen once there is one.
     */
    data class Listening(val heard: String, val paused: Boolean = false, val hidden: Boolean = false, val kept: String = "",
                         val preview: String = "") : VoiceState
    /**
     * The desktop is tidying the new words. [kept] is what was already in the box; [heard] is everything said;
     * [preview] is what listening last showed, so the screen doesn't go blank while the rest is tidied.
     */
    data class Tidying(val heard: String, val kept: String, val preview: String = "") : VoiceState
    /**
     * What was heard, to check, edit and send with a tap; nothing sends by itself. [notice] explains why listening
     * stopped early or the words weren't tidied. [editing] means the user asked for the keyboard, so the cursor goes
     * in the box.
     */
    data class Review(val text: String, val notice: String = "", val editing: Boolean = false) : VoiceState
    data class NeedsMic(val denied: Boolean) : VoiceState
    /** The phone is locked; nothing reaches the desktop until it is unlocked. [toRem] keeps the words off phone commands. */
    data class NeedsUnlock(val text: String, val toRem: Boolean = false) : VoiceState
    /** Waiting to hear from the desktop before handing the message over. */
    data class Sending(val text: String) : VoiceState
    /** In the phone's encrypted queue or with the desktop; [request] is its ID in Rem's chat. */
    data class Waiting(val text: String, val request: String) : VoiceState
    /** [hidden] keeps a reply off a locked screen. */
    data class Replied(val text: String, val reply: String, val suggestions: List<String>, val hidden: Boolean,
                       val actions: PhoneActionList = PhoneActionList.EMPTY) : VoiceState
    data class Failed(val text: String, val problem: String, val canRetry: Boolean) : VoiceState
    /** A phone command, carried out on the phone without the desktop or a model. [said] is what the user said. */
    data class Command(val said: String, val step: CommandStep) : VoiceState
}

/**
 * Runs voice mode for every surface: the assistant overlay, the voice screen, the tile and shortcuts.
 * Listening uses only the on-device recognizer and replies are read with an offline voice; messages travel the same
 * end-to-end encrypted path as Rem's chat. Desktop Rem decides what happens with them.
 */
@MainThread
@RequiresApi(29)
class VoiceController internal constructor(private val app: Application) {
    var state by mutableStateOf<VoiceState>(VoiceState.Idle); private set
    var speaker by mutableStateOf<RemSpeaker?>(null); private set
    /** A tidy request from listening is out; Send waits for it, so it never sends half-tidied words. */
    var tidyPending by mutableStateOf(false); private set
    /** Microphone loudness from 0 to 1 while listening, for the animation. */
    var level by mutableFloatStateOf(0f); private set
    /** Read Rem's replies aloud ("Talk back to me"). Kept in [VoicePrefs]; voice mode and its settings both change it. */
    var talkBack by mutableStateOf(true); private set
    /** Text from the app on screen when Rem was opened over it, for the next message only. Never stored. */
    var screen by mutableStateOf<ScreenShare>(ScreenShare.None); private set
    /** The user chose to share [screen] with this message. Off each time Rem opens; nothing is shared unless chosen. */
    var shareScreen by mutableStateOf(false); private set
    /** Why the screen can't be shared, shown only after the user tapped share. */
    var screenNote by mutableStateOf<String?>(null); private set
    /** Files attached to the next message: their upload IDs, in the same channel as Rem's chat. */
    var attachments by mutableStateOf(listOf<String>()); private set
    /** Long spoken words going as a text file, by attachment ID, so a retry reuses the file instead of adding another. */
    private var spokenFiles = mapOf<String, String>()
    /** The words being sent went as a text file, so voice mode says so under their first words. */
    var sentAsFile by mutableStateOf(false); private set
    internal var attachNotice by mutableStateOf<AttachNotice?>(null)
    /** Picked files still being read, one at a time, as in Rem's chat. */
    internal val importing = ImportQueue<Picked> { picked, done ->
        val finish = { picked.photo?.delete(); done() }
        // The import gives up after 30 seconds, but a stopped connection answers neither way.
        val late = scope.launch { delay(IMPORT_MS); attachNotice = AttachNotice(AttachPlan.TOO_SLOW); finish() }
        client.importRemFile(picked.uri, picked.name, onFailed = { reason -> late.cancel(); attachNotice = AttachNotice(reason); finish() }) { id ->
            late.cancel()
            if (attachNotice?.text == AttachPlan.TOO_SLOW) attachNotice = null
            picked.text?.let { spokenFiles = spokenFiles + (id to it) }
            attachments = (attachments + id).distinct().take(AttachPlan.MAX_FILES); finish()
        }
    }
    /** How many more files fit in this message. */
    val attachRoom get() = AttachPlan.room(attachments.size, importing.waiting.size)
    private var resumeAfterFiles = false
    private var overRemChat = false
    /** Closes the voice surface on screen; set by the overlay or voice screen while it shows. */
    internal var closeSurface: (() -> Unit)? = null
    private var handingOff = false
    /** A card the user tapped on the assistant overlay, waiting for the voice screen to unlock and open it. */
    var tappedAction: PhoneAction? = null
    /** A call or text card waiting for the voice screen to unlock and open the contact picker, and whether to remember the pick. */
    var tappedPick: Pair<PhoneAction, Boolean>? = null
    val client: LiveClient get() = LiveRuntime.client(app)
    private val scope = MainScope()
    private var surfaces = 0
    private var session: SpeechSession? = null
    private var words = DictationText()
    private var finishing = false
    private var finishTimer: Job? = null
    private var restart: Job? = null
    private var lastWords = 0L
    private var sessionStarted = 0L
    private var fastFailures = 0
    /** One recognizer kept between the sessions of a dictation, so a pause doesn't cost a restart of Android's service. */
    private val warm = WarmRecognizer(app)
    /** Where listening comes from; tests supply a stand-in for Android's recognizer. */
    internal var speech: (SpeechEvents) -> SpeechSession = { OnDeviceSpeech(app, it, warm) }
    internal var clock: () -> Long = { android.os.SystemClock.elapsedRealtime() }
    /** Voice tidying and whether it is on; tests supply stand-ins for the desktop. */
    internal var tidyVoice: suspend (String) -> TidyResult = { client.tidyVoice(it) }
    internal var tidyOn: () -> Boolean = { client.voiceTidy }
    private var turn: Job? = null
    /** The user tapped Send while listening: send once the last words are in (and tidied, when tidying is on). */
    private var sendWhenDone = false
    /** The user tapped the keyboard while listening: open the words to edit once the last ones are in. */
    private var editWhenDone = false
    /** The microphone was paused mid-session; waiting briefly for that session's last words before it closes. */
    private var pausing = false
    private var closing: Job? = null
    private var keptAt = 0L
    private var tidying: Job? = null
    /** With tidying on: what was tidied at pauses while listening, and the request that is out. */
    private var progressive: ProgressiveTidy? = null
    private var pauseTimer: Job? = null
    /** Waits out the rest of [ProgressiveTidy.GAP_MS] once enough words are in. */
    private var gapTimer: Job? = null
    private var previewJob: Job? = null
    private var previewAnswer: CompletableDeferred<TidyResult>? = null
    /** The phone command on screen, kept so a contact pick or a permission answer can carry it on. */
    private var command: PhoneCommand? = null
    private var commandJob: Job? = null
    internal var effects: PhoneEffects = PhoneEffects(app)
    /** A permission voice mode asked for, waiting for the voice screen to ask Android. */
    var pendingPermission: String? = null

    /** A voice surface is on screen. */
    fun attach() {
        closing?.cancel(); closing = null
        talkBack = VoicePrefs.speak(app)
        if (surfaces++ == 0) {
            client.voiceStart()
            if (speaker == null) speaker = RemSpeaker(app)
        }
    }

    /** A voice surface left the screen. The microphone stops at once; the turn ends shortly after the last one. */
    fun detach() {
        if (surfaces == 0) return
        // Another surface is already up, so a hand-off between them is done.
        if (--surfaces > 0) { handingOff = false; return }
        if (handingOff) {
            // The overlay passed the turn to the voice screen; listening is already paused and the words stay.
            handingOff = false
            stopListening(); speaker?.stop()
            closing = scope.launch { delay(HANDOFF_MS); end() }
            return
        }
        // Closing voice mode stops a call or text that was about to go.
        commandJob?.cancel(); commandJob = null
        if ((state as? VoiceState.Command)?.step is CommandStep.Countdown) state = VoiceState.Idle
        // Words heard before the panel closed stay for review when it opens again.
        (state as? VoiceState.Listening)?.let { words.interrupted() }
        val listening = state as? VoiceState.Listening
        val kept = listening?.let { words.text }
        stopListening(); speaker?.stop(); sendWhenDone = false; editWhenDone = false
        if (kept != null && kept.isNotBlank() && listening.hidden) tidyThenReview(kept, listening.kept)
        else if (kept != null) state = if (kept.isBlank()) VoiceState.Idle else VoiceState.Review(kept, notice = "Listening stopped when voice mode closed.")
        // The pause lets the panel move between the assistant overlay and the unlock screen without losing the turn.
        closing = scope.launch { delay(HANDOFF_MS); end() }
    }

    private fun end() {
        closing = null
        handingOff = false
        forgetScreen()
        turn?.cancel(); turn = null
        client.voiceStop()
        speaker?.shutdown(); speaker = null
        // Unsent words wait in memory for the next time voice mode opens, for a while; Cancel clears them.
        val current = state
        if (current is VoiceState.Review && (current.text.isNotBlank() || attachments.isNotEmpty())) keptAt = clock()
        // Tidying finishes on its own and leaves the words for review.
        else if (current !is VoiceState.Tidying) { state = VoiceState.Idle; discardAttachments() }
    }

    /**
     * A new invocation: start listening, unless a request from this turn is still open. [overRemChat] means the
     * overlay opened over Rem's chat in the app, so it closes once a message is queued: the reply shows behind it.
     */
    // Another app may open the exported assist entry ([fromOtherApp]). Nothing is ever sent without a tap on Send, so
    // that needs no special handling any more.
    @Suppress("UNUSED_PARAMETER")
    fun begin(fromOtherApp: Boolean = false, overRemChat: Boolean = false) {
        this.overRemChat = overRemChat
        if (state is VoiceState.Review && keptAt != 0L && clock() - keptAt > KEEP_MS) { state = VoiceState.Idle; discardAttachments() }
        keptAt = 0L
        when (state) {
            is VoiceState.Sending, is VoiceState.Waiting, is VoiceState.NeedsUnlock, is VoiceState.Review, is VoiceState.Tidying -> Unit
            // A command still waiting for a pick, a permission or its countdown carries on; a finished one starts afresh.
            is VoiceState.Command -> if ((state as VoiceState.Command).step.let { it is CommandStep.Done || it is CommandStep.Problem }) listen()
            else -> listen()
        }
    }

    /** Starts listening. [keep] is text already said, such as before Keep talking; new words are added after it. */
    fun listen(keep: String = "") {
        stopListening(); speaker?.stop(); tidying?.cancel(); tidying = null; sendWhenDone = false; editWhenDone = false
        if (Build.VERSION.SDK_INT < 31) { state = VoiceState.Failed(keep, UNSUPPORTED, false); return }
        if (ContextCompat.checkSelfPermission(app, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            state = VoiceState.NeedsMic(denied = false); return
        }
        words = DictationText(keep)
        finishing = false; fastFailures = 0; lastWords = clock()
        endPreview()
        val hidden = tidyOn()
        progressive = if (hidden) ProgressiveTidy(words.text, clock()) else null
        state = VoiceState.Listening(words.text, hidden = hidden, kept = words.text)
        startSession()
    }

    /** Pauses or resumes the microphone without ending the dictation. */
    fun toggleListening() {
        val current = state as? VoiceState.Listening ?: return
        if (current.paused) {
            // Talking again straight after pausing: keep what the closing session had and move on.
            if (pausing) { words.interrupted(); stopListening() }
            fastFailures = 0; lastWords = clock(); state = current.copy(heard = words.text, paused = false); startSession()
            return
        }
        state = current.copy(heard = words.text, paused = true)
        val open = session
        if (open == null) { stopListening(); previewTidy(); return }
        // The microphone stops now, but the recognizer's last words for this session are still on the way: wait for
        // them, as Send does, so pausing can't cut off the end of a sentence.
        pausing = true
        restart?.cancel(); restart = null
        open.finish()
        finishTimer?.cancel()
        finishTimer = scope.launch { delay(Dictation.FINISH_MS); if (pausing) { words.interrupted(); paused() } }
    }

    /** The paused session's last words are in (or it took too long): close it and tidy what was said. */
    private fun paused() {
        pausing = false
        stopListening()
        (state as? VoiceState.Listening)?.let { state = it.copy(heard = words.text) }
        previewTidy()
    }

    /**
     * The Send button while listening. There is no Done step: this stops the microphone, waits briefly for the last
     * sentence's final words, tidies whatever the pauses haven't already (when tidying is on), and sends. Words that
     * couldn't be tidied are never sent unseen; they open in the box with the reason, for the user to check and send.
     */
    fun sendSpoken() {
        // Already collecting the last words for Send or the keyboard: one is enough.
        if (state !is VoiceState.Listening || finishing) return
        sendWhenDone = true; editWhenDone = false
        val open = session
        if (open == null) { complete(); return }
        finishing = true
        open.finish()
        finishTimer?.cancel()
        finishTimer = scope.launch { delay(Dictation.FINISH_MS); if (finishing) { words.interrupted(); complete() } }
    }

    private fun startSession() {
        restart?.cancel(); restart = null
        lateinit var mine: SpeechSession
        val events = object : SpeechEvents {
            override fun partial(words: String) {
                if (session !== mine) return
                val before = this@VoiceController.words.text
                this@VoiceController.words.partial(words)
                // Only new words count as speech; a repeated partial mustn't keep postponing the pause round.
                if (words.isNotBlank() && this@VoiceController.words.text != before) { lastWords = clock(); heardMore() }
                (state as? VoiceState.Listening)?.let { state = it.copy(heard = this@VoiceController.words.text) }
            }
            override fun result(words: String) {
                if (session !== mine) return
                this@VoiceController.words.result(words)
                if (words.isNotBlank()) { lastWords = clock(); fastFailures = 0; heardMore() }
                sessionOver(mine)
            }
            override fun segment(words: String) {
                if (session !== mine) return
                this@VoiceController.words.result(words)
                if (words.isNotBlank()) { lastWords = clock(); fastFailures = 0; heardMore() }
                (state as? VoiceState.Listening)?.let { state = it.copy(heard = this@VoiceController.words.text) }
            }
            override fun ended(error: Int) {
                if (session !== mine) return
                this@VoiceController.words.interrupted()
                if (finishing || pausing) { sessionOver(mine); return }
                if (Dictation.classify(error) == SessionEnd.STOP) { stopWith(Dictation.problem(error)); return }
                val fast = clock() - sessionStarted < Dictation.FAST_FAILURE_MS
                if (fast) fastFailures++ else fastFailures = 0
                if (fastFailures >= Dictation.FAST_FAILURES) { stopWith(Dictation.problem(error)); return }
                sessionOver(mine, failedFast = fast)
            }
            override fun level(rmsDb: Float) { if (session === mine) level = ((rmsDb + 2f) / 12f).coerceIn(0f, 1f) }
        }
        mine = speech(events)
        session = mine
        sessionStarted = clock()
        if (!mine.start()) {
            mine.close(); session = null
            if (words.text.isBlank()) state = VoiceState.Failed("", NO_RECOGNIZER, false) else stopWith(NO_RECOGNIZER)
        }
    }

    /** One recognition session ended at a pause. Listening goes on unless the user is done or it has been quiet a long time. */
    private fun sessionOver(ended: SpeechSession, failedFast: Boolean = false) {
        ended.close()
        if (session === ended) session = null
        level = 0f
        val current = state as? VoiceState.Listening
        when {
            finishing -> complete()
            pausing -> paused()
            current == null -> Unit
            Dictation.shouldPause(clock(), lastWords) -> { state = current.copy(heard = words.text, paused = true); previewTidy() }
            else -> {
                state = current.copy(heard = words.text)
                // Straight on to the next session, so words right after a pause aren't lost; a failing recognizer gets a moment.
                restart = scope.launch {
                    delay(Dictation.restartDelay(failedFast))
                    if ((state as? VoiceState.Listening)?.paused == false && session == null) startSession()
                }
            }
        }
    }

    private fun complete() {
        finishTimer?.cancel(); finishTimer = null
        finishing = false
        val text = words.text
        val edit = editWhenDone
        editWhenDone = false
        stopListening()
        if (text.isBlank()) {
            // The keyboard with nothing said is typing instead.
            if (edit) { sendWhenDone = false; state = VoiceState.Review("", editing = true) }
            else if (attachments.isEmpty()) { sendWhenDone = false; state = VoiceState.Failed("", NO_SPEECH, false) }
            else if (sendWhenDone) { sendWhenDone = false; send("") }
            else state = VoiceState.Review("")
        } else heard(text, edit)
    }

    /** Listening can't go on; the words so far go to review with the reason. */
    private fun stopWith(problem: String) {
        val text = words.text
        stopListening()
        state = if (text.isBlank()) VoiceState.Failed("", problem, false)
            else VoiceState.Review(text, notice = "$problem Your words so far are kept.")
    }

    fun micDenied() { state = VoiceState.NeedsMic(denied = true) }

    /** Write the request instead, such as when on-device speech isn't installed. Words heard so far stay. */
    fun type(text: String = "") {
        val listening = state as? VoiceState.Listening
        val open = session
        if (listening != null && open != null && !pausing && words.text.isNotBlank()) {
            // Ask for the last words first, as Send does, so switching to the keyboard can't cut off a sentence.
            // With nothing said yet it is plain typing, and the box opens at once.
            sendWhenDone = false; editWhenDone = true
            finishing = true
            open.finish()
            finishTimer?.cancel()
            finishTimer = scope.launch { delay(Dictation.FINISH_MS); if (finishing) { words.interrupted(); complete() } }
            return
        }
        val kept = if (listening != null) words.also { it.interrupted() }.text.ifBlank { text } else text
        stopListening(); sendWhenDone = false
        // Hidden words are tidied before they show, even when the user turns to typing; a pause tidy already out or
        // done is used rather than asked again.
        if (listening?.hidden == true && kept.isNotBlank()) tidyThenReview(kept, listening.kept, editing = true)
        else state = VoiceState.Review(kept, editing = true)
    }

    private fun heard(words: String, edit: Boolean = false) {
        val text = words.trim()
        if (text.isEmpty()) { sendWhenDone = false; state = VoiceState.Failed("", NO_SPEECH, false); return }
        val listening = state as? VoiceState.Listening
        if (listening?.hidden == true) { tidyThenReview(text, listening.kept, editing = edit); return }
        // The words were on screen as heard, so Send sends them as they are.
        if (sendWhenDone) { sendWhenDone = false; send(text) } else state = VoiceState.Review(text, editing = edit)
    }

    /**
     * New words arrived: tidy what was said so far once [ProgressiveTidy.WORDS] more have come and
     * [ProgressiveTidy.GAP_MS] have passed since the last round, or once the user has been quiet for
     * [ProgressiveTidy.PAUSE_MS], whichever is first.
     */
    private fun heardMore() {
        if (progressive == null) return
        pauseTimer?.cancel()
        pauseTimer = scope.launch { delay(ProgressiveTidy.PAUSE_MS); previewTidy() }
        considerRound()
    }

    /** A round now if enough words and time have passed; with the words in but not the time, a round when it has. */
    private fun considerRound() {
        val plan = progressive ?: return
        val wait = plan.waitMs(freshWords() ?: return, clock()) ?: return
        gapTimer?.cancel()
        if (wait == 0L) previewTidy() else gapTimer = scope.launch { delay(wait); considerRound() }
    }

    /** The words said in this dictation after the kept text, while listening. */
    private fun freshWords(): String? {
        val listening = state as? VoiceState.Listening ?: return null
        return VoiceTidy.added(listening.kept, words.text)
    }

    /**
     * At a pause, with tidying on: sends everything said so far to be tidied while listening goes on, and shows the
     * answer in place of the hidden words. Words said meanwhile stay hidden until the next pause re-tidies the lot.
     * A failure changes nothing on screen; Done tidies again, and the words themselves are never at risk.
     */
    private fun previewTidy() {
        pauseTimer?.cancel(); pauseTimer = null
        gapTimer?.cancel(); gapTimer = null
        // Not while Send, the keyboard or a pause is collecting the last words; they decide what is tidied next.
        if (finishing || pausing) return
        val listening = state as? VoiceState.Listening ?: return
        val plan = progressive ?: return
        if (!listening.hidden) return
        // Words that start like a phone command stay on the phone: no model sees them while the user is talking.
        if (PhoneCommands.mightBeCommand(words.text)) return
        val fresh = VoiceTidy.added(listening.kept, words.text) ?: return
        val source = plan.next(fresh, clock()) ?: return
        val answer = CompletableDeferred<TidyResult>()
        previewAnswer = answer
        tidyPending = true
        previewJob = scope.launch {
            var result: TidyResult? = null
            try { result = tidyVoice(source) }
            finally {
                // Send may be waiting on this request; it always gets an answer, the words as heard at worst.
                answer.complete(result ?: TidyResult(source, false, "Tidying stopped."))
                if (result == null) plan.dropped(source)
                if (previewAnswer === answer) tidyPending = false
            }
            if (progressive !== plan) return@launch
            val changed = plan.answered(source, result)
            val now = state as? VoiceState.Listening ?: return@launch
            if (changed) state = now.copy(preview = plan.shown)
            // Words said while this was out: a round now if the user is quiet, otherwise when enough words and time.
            if (now.paused || clock() - lastWords >= ProgressiveTidy.PAUSE_MS) previewTidy() else considerRound()
        }
    }

    /** Stops tidying at pauses. A request Done is waiting on still answers, as heard. */
    private fun endPreview() {
        pauseTimer?.cancel(); pauseTimer = null
        gapTimer?.cancel(); gapTimer = null
        previewJob?.cancel(); previewJob = null
        previewAnswer = null
        tidyPending = false
    }

    /**
     * Tidies the words said after [kept] on the desktop, then shows them for review. Until the answer comes the
     * words stay hidden; any failure shows them as heard with the reason, so nothing said is lost. Auto-send waits
     * for review when the words couldn't be tidied. When a pause already tidied everything said, that is used at
     * once; when the request from the last pause covers it all, Done waits for that instead of asking twice.
     */
    private fun tidyThenReview(all: String, kept: String, editing: Boolean = false) {
        // A phone command is handled here, with its text message tidied on the phone; the desktop's model never sees it.
        if (commandFor(all.trim()) != null) {
            endPreview(); progressive = null
            val go = sendWhenDone && surfaces > 0
            sendWhenDone = false
            if (go) send(all.trim()) else { if (surfaces == 0) keptAt = clock(); state = VoiceState.Review(all.trim(), editing = editing) }
            return
        }
        val added = VoiceTidy.added(kept, all)
        val base = if (added == null) "" else kept.trim()
        val fresh = added ?: all.trim()
        val plan = progressive.takeIf { added != null }
        progressive = null
        if (fresh.isBlank()) { endPreview(); tidied("", TidyResult(all.trim(), true), editing); return }
        val ready = plan?.ready(fresh)
        if (ready != null) { endPreview(); tidied(base, TidyResult(ready, true), editing); return }
        val pending = previewAnswer.takeIf { plan?.asking == fresh }
        if (pending == null) endPreview() else { pauseTimer?.cancel(); pauseTimer = null }
        state = VoiceState.Tidying(all.trim(), base, preview = plan?.shown.orEmpty())
        tidying?.cancel()
        tidying = scope.launch {
            val result = pending?.await() ?: tidyVoice(fresh)
            if (state !is VoiceState.Tidying) return@launch
            tidying = null; previewJob = null; previewAnswer = null
            tidied(base, result, editing)
        }
    }

    /**
     * The tidied words are ready. After Send they go now; otherwise, or when they couldn't be tidied, they wait in
     * the box. Words the user hasn't seen are never sent untidied.
     */
    private fun tidied(base: String, result: TidyResult, editing: Boolean) {
        // Not cut at Rem's message limit: words that long go to Rem as a text file when sent.
        val text = DictationText.join(base, result.text).take(LongText.MAX_CHARS)
        val notice = VoiceTidy.notice(result)
        val sendNow = sendWhenDone && result.tidied && surfaces > 0
        sendWhenDone = false
        // The words as heard weren't a phone command (tidyThenReview checks), so the desktop model's wording can't become one.
        if (sendNow) { send(text, unlocked = false, toRem = true); return }
        if (surfaces == 0) keptAt = clock()
        state = VoiceState.Review(text, notice = notice, editing = editing)
    }

    /** Sends to desktop Rem, or carries out a phone command the user said. A locked phone asks for an unlock first. */
    fun send(text: String) = send(text, unlocked = false)

    /**
     * Sends a suggestion Rem wrote under its reply. It always goes to Rem as a message: words Rem chose never run as a
     * phone command, so a suggestion can't call or text anyone.
     */
    fun sendSuggestion(text: String) = send(text, unlocked = false, toRem = true)

    private fun send(text: String, unlocked: Boolean, toRem: Boolean = false) {
        val words = VoiceLogic.outgoing(text) ?: if (attachments.isNotEmpty()) "" else return
        stopListening(); speaker?.stop(); sendWhenDone = false
        // The user's own phone commands never go to the desktop. Rem's suggestions, messages with files and long text are for Rem.
        if (VoiceLogic.mayRunOnPhone(toRem, attachments.size) && !VoiceLogic.asFile(words)) commandFor(words)?.let { runCommand(words, it, unlocked); return }
        if (!unlocked && locked()) { state = VoiceState.NeedsUnlock(words, toRem); return }
        turn?.cancel()
        state = VoiceState.Sending(words)
        turn = scope.launch { deliver(words) }
    }

    /**
     * Android reported a successful unlock from the voice screen. The lock state can still read locked for a moment
     * afterwards, so this trusts the report instead of checking again.
     */
    fun unlocked() {
        when (val current = state) {
            is VoiceState.NeedsUnlock -> send(current.text, unlocked = true, toRem = current.toRem)
            is VoiceState.Replied -> if (current.hidden) { state = current.copy(hidden = false); speakReply(current.reply) }
            else -> Unit
        }
    }

    /**
     * The voice screen came back to an unlocked phone. A request the user already sent, which waited only for the
     * unlock, goes now; a hidden reply shows. Nothing new starts.
     */
    fun unlockedElsewhere() {
        if (locked()) return
        when (state) { is VoiceState.NeedsUnlock, is VoiceState.Replied -> unlocked(); else -> Unit }
    }

    fun stopSpeaking() { speaker?.stop() }

    /** Turns reading replies aloud on or off. Off stops anything being read now. */
    fun talkBack(on: Boolean) {
        VoicePrefs.setSpeak(app, on)
        talkBack = on
        if (!on) speaker?.stop()
    }

    /**
     * Android handed over the text on screen as Rem opened. Ignored unless screen sharing is allowed in settings; even
     * then it stays on this phone, in memory, unless the user taps share for this message.
     */
    fun screenSeen(from: String, lines: List<String>) {
        forgetScreen()
        if (!VoicePrefs.screen(app)) return
        val text = ScreenText.clean(lines)
        screen = if (text.isEmpty()) ScreenShare.Unavailable(ScreenText.EMPTY) else ScreenShare.Ready(ScreenContext(from, text, clock()))
    }

    /** The screen couldn't be read; the reason shows only if the user taps share. */
    fun screenUnavailable(reason: String) {
        forgetScreen()
        if (VoicePrefs.screen(app)) screen = ScreenShare.Unavailable(reason)
    }

    /** Rem was opened without the screen (a tile, the shortcut, sharing off): nothing from an earlier opening carries over. */
    fun dropScreen() = forgetScreen()

    /** The share-screen button: share this screen with the next message, or stop sharing it. */
    fun toggleShareScreen() {
        val home = client.desktopViews["rem.home"] as? RemHome
        screenNote = null
        when (val current = screen) {
            is ScreenShare.Ready -> if (!shareScreen && home != null && !home.screenContext) screenNote = ScreenText.DESKTOP_OLD
                else shareScreen = !shareScreen
            is ScreenShare.Unavailable -> { shareScreen = false; screenNote = current.reason }
            ScreenShare.None -> { shareScreen = false; screenNote = ScreenText.NOT_OVER_APP }
        }
    }

    private fun forgetScreen() { screen = ScreenShare.None; shareScreen = false; screenNote = null }

    /** Picked files to read and upload for the next message. */
    internal fun attach(files: List<Picked>) { attachNotice = null; importing.add(files) }

    /** Takes a file off the message. One still uploading stops, so it doesn't hold up the next. */
    fun removeAttachment(id: String) {
        attachments = attachments - id
        spokenFiles = spokenFiles - id
        val file = client.files.firstOrNull { it.id == id }
        if (file?.direction == "upload" && file.state in setOf("offered", "receiving")) client.cancelFile(id)
    }

    private fun discardAttachments() { attachments.forEach(::removeAttachment); attachNotice = null }

    /** The overlay is about to pass the turn to the voice screen, for a step only an app screen can do. */
    fun handOff() { handingOff = true }

    /** Another app is about to pick files. Listening pauses so the microphone isn't on in other apps; the words stay. */
    fun leavingForFiles() {
        val listening = state as? VoiceState.Listening
        if (listening != null && !listening.paused) { resumeAfterFiles = true; toggleListening() }
    }

    /** Back from picking files: listening carries on if it was on before. */
    fun backFromFiles() {
        if (resumeAfterFiles && (state as? VoiceState.Listening)?.paused == true) toggleListening()
        resumeAfterFiles = false
    }

    /** Ends the turn. A message already handed to the phone's queue still goes to Rem. */
    fun cancel() {
        commandJob?.cancel(); commandJob = null; command = null
        stopListening(); turn?.cancel(); turn = null; speaker?.stop(); tidying?.cancel(); tidying = null; sendWhenDone = false; editWhenDone = false
        endPreview(); progressive = null
        forgetScreen(); discardAttachments(); resumeAfterFiles = false; overRemChat = false
        state = VoiceState.Idle
    }

    // ---- phone commands, all on the phone ----

    /** The phone command in [text], or null. An app is opened only when one with that name is installed. */
    private fun commandFor(text: String): PhoneCommand? {
        PeopleStore.load(app)
        val found = PhoneCommands.parse(text, knownName = { PhonePeople.matches(PeopleStore.people, it).isNotEmpty() }) ?: return null
        return found.takeUnless { it is PhoneCommand.OpenApp && effects.app(it.name) == null }
    }

    /** Sends [text] to Rem after all, for words that looked like a command but weren't meant as one. */
    fun askRem(text: String) {
        commandJob?.cancel(); commandJob = null; command = null
        send(text, unlocked = false, toRem = true)
    }

    /** Carries out [found]. Calls, texts and opening other apps need an unlocked phone; the rest work locked too. */
    private fun runCommand(said: String, found: PhoneCommand, unlocked: Boolean) {
        commandJob?.cancel(); commandJob = null
        command = found
        val needsUnlock = found is PhoneCommand.Call || found is PhoneCommand.Text || found is PhoneCommand.Open ||
            found is PhoneCommand.OpenApp || found is PhoneCommand.ShowAlarms
        if (needsUnlock && !unlocked && locked()) { state = VoiceState.NeedsUnlock(said); return }
        fun step(next: CommandStep) { state = VoiceState.Command(said, next) }
        when (found) {
            is PhoneCommand.Call -> person(said, true, found.who, "", found.label)
            is PhoneCommand.Text -> {
                val body = TextTidy.tidy(found.body)
                if (body.isEmpty()) step(CommandStep.Problem(CommandPlan.NO_BODY, askRem = true)) else person(said, false, found.who, body, "")
            }
            is PhoneCommand.Open -> step(CommandStep.Launch(dev.raticode.mobile.live.PhoneActions.intent(found.action), CommandPlan.doing(found.action)))
            PhoneCommand.ShowAlarms -> step(CommandStep.Launch(dev.raticode.mobile.live.PhoneIntentSpec(android.provider.AlarmClock.ACTION_SHOW_ALARMS, null, emptyMap()), "Opening your alarms"))
            is PhoneCommand.OpenApp -> effects.app(found.name)?.let { (label, spec) -> step(CommandStep.Launch(spec, "Opening $label")) }
                ?: step(CommandStep.Problem("No app called “${found.name}” on this phone.", askRem = true))
            is PhoneCommand.Flashlight -> done(said, effects.flashlight(found.on), if (found.on) "Flashlight on." else "Flashlight off.")
            is PhoneCommand.Media -> { effects.media(found.key); done(said, null, when (found.key) {
                MediaKey.PLAY -> "Playing."; MediaKey.PAUSE -> "Paused."; MediaKey.NEXT -> "Next track."; MediaKey.PREVIOUS -> "Previous track." }) }
            is PhoneCommand.Volume -> {
                val level = effects.volume(found.change, found.percent)
                done(said, if (level == null) "Android didn't let Raticode change the volume right now." else null, when (found.change) {
                    VolumeChange.MUTE -> "Muted."; VolumeChange.UNMUTE -> "Volume back on at $level%."; else -> "Volume at $level%." })
            }
            is PhoneCommand.Ask -> {
                val answer = CommandPlan.answer(found.what, java.time.ZonedDateTime.now(), if (found.what == Question.BATTERY) effects.battery() else null)
                step(CommandStep.Done(answer, speak = true, askRem = true)); speakReply(answer)
            }
        }
    }

    /**
     * A device action finished: say so, with Ask Rem instead in case the words weren't meant as a command, then step
     * aside so the user is back where they were.
     */
    private fun done(said: String, problem: String?, ok: String, askRem: Boolean = true) {
        state = VoiceState.Command(said, if (problem == null) CommandStep.Done(ok, askRem = askRem) else CommandStep.Problem(problem, askRem = askRem))
        if (problem != null) return
        val shown = state
        commandJob = scope.launch { delay(DONE_MS); if (state === shown) closeSurface?.invoke() }
    }

    /** Finds who a call or text is for, then counts down to it, asks which one, or asks for a contact. */
    private fun person(said: String, call: Boolean, words: String, body: String, label: String) {
        PeopleStore.load(app)
        val card = CommandPlan.card(call, words, body)
        when (val who = CommandPlan.resolve(words, PeopleStore.people, PeopleStore.chosen[card], label)) {
            is Resolved.One -> go(said, call, who.recipient, body)
            is Resolved.Several -> state = VoiceState.Command(said, CommandStep.Choose(call, words, body, who.people))
            Resolved.None -> state = VoiceState.Command(said, CommandStep.Pick(call, words, body, card))
        }
    }

    /** The user tapped one of several people. */
    fun choose(person: SavedPerson) {
        val current = state as? VoiceState.Command ?: return
        val step = current.step as? CommandStep.Choose ?: return
        go(current.said, step.call, Recipient(PhonePeople.describe(person), person.number), step.body)
    }

    /** A contact was picked for [card] on the voice screen: the command carries on with that person. */
    fun picked(card: dev.raticode.mobile.live.PhoneAction) {
        val current = state as? VoiceState.Command ?: return
        // From "Choose contact" when nobody matched, or "Someone else" when several did.
        val (call, words, body) = when (val step = current.step) {
            is CommandStep.Pick -> Triple(step.call, step.words, step.body)
            is CommandStep.Choose -> Triple(step.call, step.words, step.body)
            else -> return
        }
        if (CommandPlan.card(call, words, body) != card || PeopleStore.chosen[card] == null) return
        person(current.said, call, words, body, "")
    }

    /** Checks the permission and emergency rules, then counts down so the user can still cancel. */
    private fun go(said: String, call: Boolean, to: Recipient, body: String) {
        fun step(next: CommandStep) { state = VoiceState.Command(said, next) }
        if (!effects.hasTelephony()) { step(CommandStep.Problem(if (call) PhoneEffects.NO_PHONE else PhoneEffects.NO_SMS)); return }
        if (call && effects.isEmergency(to.number)) {
            step(CommandStep.Launch(CommandPlan.fallback(true, to.number, ""), CommandPlan.EMERGENCY)); return
        }
        if (!call && !effects.mayText()) {
            step(CommandStep.Launch(CommandPlan.fallback(false, to.number, body), NOT_ASSISTANT_DRAFT)); return
        }
        if (if (call) !effects.canCall() else !effects.canText()) { step(CommandStep.NeedsPermission(call, to.who, to.number, body)); return }
        val total = if (call) CommandPlan.CALL_MS else CommandPlan.TEXT_MS
        val countdown = CommandStep.Countdown(call, to.who, to.number, body, clock() + total, total)
        step(countdown)
        commandJob?.cancel()
        commandJob = scope.launch { delay(total); if ((state as? VoiceState.Command)?.step === countdown) now() }
    }

    /** Calls or sends now, skipping the rest of the countdown. */
    fun now() {
        val current = state as? VoiceState.Command ?: return
        val step = current.step as? CommandStep.Countdown ?: return
        commandJob?.cancel(); commandJob = null
        if (locked()) { state = VoiceState.NeedsUnlock(current.said); return }
        if (step.call) {
            val problem = effects.placeCall(step.number)
            if (problem != null) {
                state = VoiceState.Command(current.said, CommandStep.Problem(problem, fallback = CommandPlan.fallback(true, step.number, ""))); return
            }
            state = VoiceState.Command(current.said, CommandStep.Done("Calling ${step.who}."))
            // The call screen takes over; voice mode steps aside.
            commandJob = scope.launch { delay(CALL_CLOSE_MS); closeSurface?.invoke() }
        } else {
            state = VoiceState.Command(current.said, CommandStep.Working("Sending to ${step.who}…"))
            effects.sendText(step.number, step.body) { problem ->
                if ((state as? VoiceState.Command)?.said != current.said) return@sendText
                if (problem == null) {
                    done(current.said, null, "Sent to ${step.who}.", askRem = false)
                    if (talkBack) speaker?.speak("Sent.")
                } else state = VoiceState.Command(current.said, CommandStep.Problem(problem, fallback = CommandPlan.fallback(false, step.number, step.body)))
            }
        }
    }

    /** Puts a text's words back in the box to change them; sending again routes it as a command. */
    fun editCommand() {
        val current = state as? VoiceState.Command ?: return
        commandJob?.cancel(); commandJob = null
        val text = when (val step = current.step) {
            is CommandStep.Countdown -> if (step.call) current.said else "Text ${step.who.substringBefore(" · ")} ${step.body}"
            else -> current.said
        }
        command = null
        state = VoiceState.Review(text, editing = true)
    }

    /** Android answered the permission request made for the command on screen. */
    fun permissionAnswered(granted: Boolean) {
        pendingPermission = null
        val current = state as? VoiceState.Command ?: return
        val step = current.step as? CommandStep.NeedsPermission ?: return
        if (granted) go(current.said, step.call, Recipient(step.who, step.number), step.body)
        else state = VoiceState.Command(current.said, CommandStep.Launch(CommandPlan.fallback(step.call, step.number, step.body),
            if (step.call) "Calling isn't allowed, so the dialer is open with the number" else "Texting isn't allowed, so the message is open as a draft"))
    }

    /** Opens the dialer or a draft instead, after a call or text couldn't go directly. */
    fun openInstead(spec: dev.raticode.mobile.live.PhoneIntentSpec) {
        val current = state as? VoiceState.Command ?: return
        state = VoiceState.Command(current.said, CommandStep.Launch(spec, "Opening it in the app instead"))
    }

    /** Another app opened for a command, or couldn't. */
    fun launched(problem: String?) {
        val current = state as? VoiceState.Command ?: return
        if (problem != null) state = VoiceState.Command(current.said, CommandStep.Problem(problem))
    }

    fun readiness(): DeskReadiness = VoiceLogic.readiness(client.loaded, client.paired, client.remReady != null, client.desktopControl)

    private suspend fun deliver(text: String) {
        val ready = withTimeoutOrNull(CONNECT_MS) {
            snapshotFlow { readiness() }.first { it != DeskReadiness.LOADING && it != DeskReadiness.CONNECTING }
        }
        when (ready) {
            null -> { state = VoiceState.Failed(text, unreachable(), true); return }
            DeskReadiness.NOT_PAIRED -> { state = VoiceState.Failed(text, NOT_PAIRED, false); return }
            DeskReadiness.CONTROL_OFF -> { state = VoiceState.Failed(text, CONTROL_OFF, true); return }
            else -> Unit
        }
        // Long words go as a text file, with nothing else in the message; Rem reads the file like any text attachment.
        // Offline, words that still fit in a message wait in the phone's queue as a message instead.
        var body = text
        sentAsFile = false
        if (VoiceLogic.asFile(text) && !(text.length <= VoiceLogic.MAX_CHARS && !client.connected)) {
            val reused = attachments.any { spokenFiles[it] == text }
            val filesThread = (client.desktopViews["rem.home"] as? RemHome)?.filesThread != null
            VoiceLogic.longProblem(text, if (reused) 1 else attachRoom, filesThread)?.let { state = VoiceState.Failed(text, it, true); return }
            if (reused || (attachRoom > 0 && filesThread)) {
                if (!attachSpoken(text)) { state = VoiceState.Failed(text, attachNotice?.text ?: LongText.FILE_FAILED, true); return }
                body = ""; sentAsFile = true
            }
        }
        // Files go once they have all arrived on the desktop; one that stopped has to be dealt with first.
        val files = attachments
        if (files.isNotEmpty()) {
            val arrived = withTimeoutOrNull(UPLOAD_MS) {
                snapshotFlow { VoiceLogic.files(files.map { id -> client.files.firstOrNull { it.id == id }?.state }) }
                    .first { it != VoiceLogic.Files.UPLOADING }
            }
            if (arrived != VoiceLogic.Files.READY) {
                state = VoiceState.Failed(text, if (arrived == null) FILES_SLOW else VoiceLogic.FILES_STOPPED, true); return
            }
        }
        val queued = CompletableDeferred<String>()
        // The screen goes with this message only: if the user chose it, sharing is still allowed and it is recent.
        val shared = VoiceLogic.screenToSend(screen, shareScreen, VoicePrefs.screen(app), clock())
        forgetScreen()
        client.sendRemMessage(body, files, onRequest = { queued.complete(it) }, screen = shared)
        val request = withTimeoutOrNull(QUEUE_MS) { queued.await() }
        if (request == null) { state = VoiceState.Failed(text, NOT_QUEUED, true); return }
        // Queued with the message; the desktop keeps its own copies.
        attachments = emptyList(); spokenFiles = emptyMap()
        state = VoiceState.Waiting(text, request)
        // Over Rem's chat the message and its reply show right behind; a second chat on top would only be in the way.
        // The message is already in the phone's encrypted queue, so closing can't lose it.
        val close = closeSurface
        if (VoiceLogic.closeOnceQueued(overRemChat) && close != null) { overRemChat = false; close(); return }
        val (home, refusal) = snapshotFlow { (client.desktopViews["rem.home"] as? RemHome) to client.remRefusal }
            .first { (home, refusal) -> refusal?.first == request || VoiceLogic.reply(home, request) != null }
        if (refusal?.first == request) state = VoiceState.Failed(text, refusal.second, true)
        else replied(text, VoiceLogic.reply(home, request)!!, VoiceLogic.actions(home, request))
    }

    /**
     * Attaches long spoken [text] as a text file, or finds the one a failed try already attached. True once it is
     * attached; false when the phone couldn't write or read it, with the reason in [attachNotice].
     */
    private suspend fun attachSpoken(text: String): Boolean {
        if (attachments.any { spokenFiles[it] == text }) return true
        val picked = pickedText(app, text, LongText.Kind.SPOKEN) ?: run { attachNotice = AttachNotice(LongText.FILE_FAILED); return false }
        attachNotice = null
        importing.add(listOf(picked))
        // The import queue always finishes an item, attached or not, within its own time limit.
        snapshotFlow { importing.waiting.none { it === picked } }.first { it }
        return attachments.any { spokenFiles[it] == text }
    }

    private fun replied(text: String, message: RemMessage, actions: PhoneActionList) {
        val words = VoiceLogic.replyText(message)
        val hidden = !VoiceLogic.showReply(locked(), VoicePrefs.whileLocked(app))
        state = VoiceState.Replied(text, words, message.suggestions, hidden, actions)
        if (!hidden) speakReply(words)
    }

    private fun speakReply(words: String) { if (talkBack) speaker?.speak(SpeechText.forSpeech(words)) }

    private fun unreachable(): String {
        val detail = client.status.takeUnless { it.isBlank() || it.startsWith("Disconnected") || it.startsWith("Connected") }
        return listOfNotNull("Couldn't reach your desktop, so this wasn't sent.", detail).joinToString(" ")
    }

    /** Locked with a PIN, pattern or password. A swipe-only lock screen protects nothing, so it doesn't hold a request. */
    private fun locked() = app.getSystemService(KeyguardManager::class.java).isDeviceLocked

    private fun stopListening() {
        pausing = false
        pauseTimer?.cancel(); pauseTimer = null
        gapTimer?.cancel(); gapTimer = null
        restart?.cancel(); restart = null; finishTimer?.cancel(); finishTimer = null; finishing = false
        session?.close(); session = null; level = 0f
        warm.release()
    }

    companion object {
        const val HANDOFF_MS = 2000L
        /** How long a finished device command stays on screen before voice mode steps aside. */
        const val DONE_MS = 3000L
        const val CALL_CLOSE_MS = 600L
        const val NOT_ASSISTANT_DRAFT = "Raticode sends texts itself only as the phone's assistant, so the message is open as a draft"
        const val CONNECT_MS = 25000L
        const val QUEUE_MS = 10000L
        const val IMPORT_MS = 45_000L
        /** How long a message waits for its files to finish uploading. */
        const val UPLOAD_MS = 3 * 60_000L
        const val FILES_SLOW = "The attached files are still uploading. Try again once they finish, or remove them."
        /** How long unsent words wait for voice mode to open again. */
        const val KEEP_MS = 10 * 60_000L
        const val NO_SPEECH = "No speech recognized. Tap Talk again and speak after the microphone appears."
        const val UNSUPPORTED = "Voice mode needs Android 12 or newer, for speech recognition that stays on the phone. You can still type to Rem."
        const val NO_RECOGNIZER = "On-device speech recognition isn't available. Install an offline speech language in Android settings, or type instead."
        const val NOT_PAIRED = "Pair this phone with Raticode on your computer first. Open Raticode and scan the desktop's QR code."
        const val CONTROL_OFF = "Rem isn't allowed from this phone yet. On your computer, open Paired devices and turn on Use Raticode from this phone."
        const val NOT_QUEUED = "The phone couldn't queue this message. Try again, or open Raticode to check its storage."
    }
}

/** The one voice controller, shared by every voice surface in the app's process. */
@RequiresApi(29)
object VoiceRuntime {
    private var instance: VoiceController? = null
    @MainThread fun controller(app: Application): VoiceController = instance ?: VoiceController(app).also { instance = it }
}
