package dev.raticode.mobile.ui

import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer

/**
 * Never falls back to a speech provider that may upload audio. Main-thread lifecycle.
 * [partial] receives the words heard so far, when the caller wants them; [ended] gets null after a result.
 */
class OnDeviceDictation(context: Context, private val result: (String) -> Unit, private val ended: (String?) -> Unit,
                        private val partial: ((String) -> Unit)? = null) {
    private var closed = false
    private var recognizer: SpeechRecognizer? = null
    init {
        if (Build.VERSION.SDK_INT >= 31 && SpeechRecognizer.isOnDeviceRecognitionAvailable(context)) {
            recognizer = SpeechRecognizer.createOnDeviceSpeechRecognizer(context).apply {
                setRecognitionListener(object : RecognitionListener {
                    override fun onReadyForSpeech(params: Bundle?) = Unit
                    override fun onBeginningOfSpeech() = Unit
                    override fun onRmsChanged(rmsdB: Float) = Unit
                    override fun onBufferReceived(buffer: ByteArray?) = Unit
                    override fun onEndOfSpeech() = Unit
                    override fun onPartialResults(partialResults: Bundle?) {
                        if (closed) return
                        partialResults?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()?.takeIf { it.isNotBlank() }?.let { partial?.invoke(it) }
                    }
                    override fun onEvent(eventType: Int, params: Bundle?) = Unit
                    override fun onError(error: Int) { if (closed) return; ended(when (error) {
                        SpeechRecognizer.ERROR_NO_MATCH, SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> NO_SPEECH
                        SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "Microphone permission is required for dictation."
                        SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> "Another app is using speech recognition. Try again in a moment."
                        // Only a missing offline language means installing one; a busy microphone
                        // or a stopped service needs a different answer.
                        else -> dev.raticode.mobile.voice.Dictation.problem(error)
                    }) }
                    override fun onResults(results: Bundle?) {
                        if (closed) return
                        results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()?.let(result)
                        ended(null)
                    }
                })
            }
        }
    }
    fun start(): Boolean {
        val engine = recognizer ?: return false
        return try {
            engine.startListening(Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, partial != null)
                putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
                // Words as said, swear words included; Android masks them by default (option added in Android 13).
                if (Build.VERSION.SDK_INT >= 33) putExtra(RecognizerIntent.EXTRA_MASK_OFFENSIVE_WORDS, false)
            }); true
        } catch (_: Exception) { false }
    }
    /** Stops listening and keeps what was said; the result still arrives through the result callback. */
    fun finish() { if (!closed) try { recognizer?.stopListening() } catch (_: Exception) {} }
    fun close() {
        closed = true
        try { recognizer?.cancel() } finally { recognizer?.destroy(); recognizer = null }
    }
    companion object { const val NO_SPEECH = "No speech recognized. Try again." }
}
