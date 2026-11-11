package dev.raticode.mobile.voice

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import androidx.annotation.RequiresApi

/** What one recognition session reports. Callbacks arrive on the main thread. */
interface SpeechEvents {
    /** The session's words so far; each call replaces the previous one. */
    fun partial(words: String)
    /** The session's final words, possibly empty. The session is over. */
    fun result(words: String)
    /** Final words for one stretch of speech; the session keeps listening. Only from recognizers that keep a session across pauses. */
    fun segment(words: String)
    /** The session ended without a result; [error] is a [SpeechRecognizer] error code. */
    fun ended(error: Int)
    /** Loudness in decibels, for the listening animation. */
    fun level(rmsDb: Float)
}

/** One recognition session. Android ends each one at a pause; voice mode starts the next until the user taps Done. */
interface SpeechSession {
    fun start(): Boolean
    /** Stop listening and deliver what was heard. */
    fun finish()
    fun close()
}

/**
 * Keeps one on-device recognizer between the sessions of a dictation. Making a new one has Android start the
 * recognition service again, which leaves the microphone off for a noticeable moment at every pause.
 */
class WarmRecognizer(private val context: Context) {
    private var idle: SpeechRecognizer? = null

    @RequiresApi(31)
    internal fun take(): SpeechRecognizer = idle?.also { idle = null } ?: SpeechRecognizer.createOnDeviceSpeechRecognizer(context)
    internal fun keep(recognizer: SpeechRecognizer) { idle?.destroy(); idle = recognizer }
    /** Listening has stopped: let the recognition service go. */
    fun release() { idle?.destroy(); idle = null }
}

/**
 * A session with Android's on-device recognizer only. There is no fallback to a recognizer that may upload audio.
 * The silence hints ask for longer pauses where the recognizer honors them, and on Android 13 and newer a recognizer
 * that supports it keeps one session across pauses. With [warm], the recognizer is reused by the next session.
 */
class OnDeviceSpeech(private val context: Context, private val events: SpeechEvents, private val warm: WarmRecognizer? = null) : SpeechSession {
    private var closed = false
    private var recognizer: SpeechRecognizer? = null
    /** The session ended the ordinary way, so its recognizer can serve the next one. */
    private var reusable = false

    override fun start(): Boolean {
        if (Build.VERSION.SDK_INT < 31 || !SpeechRecognizer.isOnDeviceRecognitionAvailable(context)) return false
        return try {
            recognizer = (warm?.take() ?: SpeechRecognizer.createOnDeviceSpeechRecognizer(context)).apply {
                setRecognitionListener(object : RecognitionListener {
                    override fun onReadyForSpeech(params: Bundle?) = Unit
                    override fun onBeginningOfSpeech() = Unit
                    override fun onRmsChanged(rmsdB: Float) { if (!closed) events.level(rmsdB) }
                    override fun onBufferReceived(buffer: ByteArray?) = Unit
                    override fun onEndOfSpeech() = Unit
                    override fun onPartialResults(partialResults: Bundle?) {
                        if (!closed) events.partial(words(partialResults))
                    }
                    override fun onEvent(eventType: Int, params: Bundle?) = Unit
                    override fun onError(error: Int) {
                        if (closed) return
                        reusable = Dictation.reusableAfter(error)
                        events.ended(error)
                    }
                    override fun onResults(results: Bundle?) { if (!closed) { reusable = true; events.result(words(results)) } }
                    override fun onSegmentResults(segmentResults: Bundle) { if (!closed) events.segment(words(segmentResults)) }
                    override fun onEndOfSegmentedSession() { if (!closed) { reusable = true; events.result("") } }
                })
                startListening(Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                    extras(Build.VERSION.SDK_INT).forEach { (key, value) ->
                        when (value) {
                            is Boolean -> putExtra(key, value)
                            is Int -> putExtra(key, value)
                            is String -> putExtra(key, value)
                        }
                    }
                })
            }
            true
        } catch (_: Exception) { close(); false }
    }

    override fun finish() { if (!closed) try { recognizer?.stopListening() } catch (_: Exception) {} }

    override fun close() {
        closed = true
        val done = recognizer ?: return
        recognizer = null
        try { done.cancel() } catch (_: Exception) { reusable = false }
        if (reusable && warm != null) warm.keep(done) else done.destroy()
    }

    private fun words(bundle: Bundle?) = bundle?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull().orEmpty()

    companion object {
        /** Wait this long after speech stops before ending, where the recognizer honors it. */
        const val SILENCE_MS = 5000
        const val MAYBE_SILENCE_MS = 4000

        /**
         * What each session asks of the recognizer. Silence lengths are whole numbers (Int): Google's recognizer reads
         * them as Int and silently ignores a Long, which made every short pause end the session.
         */
        // [sdk] is Build.VERSION.SDK_INT; the Android 13 extra is only added from 33, and older recognizers would ignore it anyway.
        @SuppressLint("InlinedApi")
        internal fun extras(sdk: Int): Map<String, Any> = buildMap {
            put(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            put(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            put(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
            put(RecognizerIntent.EXTRA_PREFER_OFFLINE, true)
            put(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, SILENCE_MS)
            put(RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS, MAYBE_SILENCE_MS)
            // One session across pauses, ending only after the silence above, where the recognizer supports it.
            if (sdk >= 33) put(RecognizerIntent.EXTRA_SEGMENTED_SESSION, RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS)
            // Words as said: Android's recognizer writes swear words as "f***" unless told not to. Older versions have
            // no such option; voice tidying restores masked words there when they are unambiguous.
            if (sdk >= 33) put(RecognizerIntent.EXTRA_MASK_OFFENSIVE_WORDS, false)
        }
    }
}
