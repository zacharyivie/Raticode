package dev.raticode.mobile.voice

import android.speech.SpeechRecognizer

/**
 * The words of one dictation across many recognition sessions. Earlier words stay when listening resumes, and a
 * session cut short keeps its last partial words instead of dropping them. Pure, so it is unit tested.
 */
internal class DictationText(keep: String = "") {
    var committed = keep.trim(); private set
    private var partial = ""
    /** Everything heard so far, including the current session's words. */
    val text get() = join(committed, partial)

    fun partial(words: String) { partial = words.trim() }
    /** A session's final words. An empty final keeps the partial words heard before it. */
    fun result(words: String) { committed = join(committed, words.trim().ifEmpty { partial }); partial = "" }
    /** A session ended without final words: keep what it had. */
    fun interrupted() { committed = join(committed, partial); partial = "" }

    companion object {
        fun join(first: String, second: String) = listOf(first.trim(), second.trim()).filter { it.isNotEmpty() }.joinToString(" ")
    }
}

/** How a recognition session's end is handled while the user is still talking. */
internal enum class SessionEnd {
    /** A pause or a session the recognizer closed: start the next one. */
    CONTINUE,
    /** The microphone or recognizer can't go on: stop and keep the words. */
    STOP,
}

internal object Dictation {
    /** No words for this long pauses listening, so the microphone doesn't stay on unattended. The words stay. */
    const val QUIET_MS = 45_000L
    /** Sessions failing this quickly this many times in a row mean the recognizer is stuck. */
    const val FAST_FAILURE_MS = 1_000L
    const val FAST_FAILURES = 5
    /** Done waits this long for the last session's final words before using what was heard. */
    const val FINISH_MS = 2_500L

    fun classify(error: Int): SessionEnd = when (error) {
        SpeechRecognizer.ERROR_NO_MATCH, SpeechRecognizer.ERROR_SPEECH_TIMEOUT,
        SpeechRecognizer.ERROR_RECOGNIZER_BUSY, SpeechRecognizer.ERROR_CLIENT -> SessionEnd.CONTINUE
        else -> SessionEnd.STOP
    }

    /** Why listening stopped, in words; the caller adds that the words so far are kept. */
    fun problem(error: Int): String = when (error) {
        SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "Microphone access was turned off."
        SpeechRecognizer.ERROR_AUDIO -> "The microphone stopped working."
        SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED, SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE ->
            "This phone can't recognize your language offline. Install an offline speech language in Android settings."
        SpeechRecognizer.ERROR_SERVER_DISCONNECTED -> "Android's speech recognition stopped."
        else -> "Speech recognition stopped."
    }

    fun shouldPause(now: Long, lastWords: Long) = now - lastWords >= QUIET_MS

    /** An ordinary pause or no-match leaves the recognizer fit for the next session; anything else gets a fresh one. */
    fun reusableAfter(error: Int) = error == SpeechRecognizer.ERROR_NO_MATCH || error == SpeechRecognizer.ERROR_SPEECH_TIMEOUT

    /** A pause restarts listening at once; a session that failed straight away waits a moment so the recognizer can recover. */
    const val RETRY_MS = 150L
    fun restartDelay(failedFast: Boolean) = if (failedFast) RETRY_MS else 0L
}
