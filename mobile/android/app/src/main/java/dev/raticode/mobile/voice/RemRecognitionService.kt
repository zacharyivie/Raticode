package dev.raticode.mobile.voice

import android.content.Intent
import android.os.Bundle
import android.os.RemoteException
import android.speech.RecognitionListener
import android.speech.RecognitionService
import android.speech.SpeechRecognizer
import androidx.annotation.RequiresApi

/**
 * The speech recognizer Android requires every digital assistant to provide. It hands each request to the phone's
 * on-device recognizer and refuses when there is none, so it never sends audio to an online service. Android checks
 * that the calling app may record audio before a request reaches here.
 */
@RequiresApi(31)
class RemRecognitionService : RecognitionService() {
    private var engine: SpeechRecognizer? = null

    override fun onStartListening(recognizerIntent: Intent, listener: Callback) {
        engine?.destroy(); engine = null
        if (!SpeechRecognizer.isOnDeviceRecognitionAvailable(this)) { report { listener.error(SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE) }; return }
        engine = SpeechRecognizer.createOnDeviceSpeechRecognizer(this).apply {
            setRecognitionListener(object : RecognitionListener {
                override fun onReadyForSpeech(params: Bundle?) = report { listener.readyForSpeech(params ?: Bundle()) }
                override fun onBeginningOfSpeech() = report { listener.beginningOfSpeech() }
                override fun onRmsChanged(rmsdB: Float) = report { listener.rmsChanged(rmsdB) }
                override fun onBufferReceived(buffer: ByteArray?) = Unit
                override fun onEndOfSpeech() = report { listener.endOfSpeech() }
                override fun onError(error: Int) = report { listener.error(error) }
                override fun onResults(results: Bundle?) = report { listener.results(results ?: Bundle()) }
                override fun onPartialResults(partialResults: Bundle?) = report { listener.partialResults(partialResults ?: Bundle()) }
                override fun onEvent(eventType: Int, params: Bundle?) = Unit
            })
            startListening(recognizerIntent)
        }
    }

    override fun onStopListening(listener: Callback) { engine?.stopListening() }
    override fun onCancel(listener: Callback) { engine?.cancel() }
    override fun onDestroy() { engine?.destroy(); engine = null; super.onDestroy() }

    /** A caller that went away can't be told anything. */
    private inline fun report(call: () -> Unit) { try { call() } catch (_: RemoteException) {} }
}
