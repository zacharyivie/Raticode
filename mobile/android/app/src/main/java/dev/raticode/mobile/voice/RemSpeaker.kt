package dev.raticode.mobile.voice

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.speech.tts.Voice
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import java.util.Locale

/**
 * Reads Rem's replies with an offline voice of the phone's text-to-speech engine. A voice that needs the internet is
 * never used, so reply text stays on the phone. Main-thread object.
 */
class RemSpeaker(context: Context) {
    /** Null while the engine starts, false when no offline voice for the phone's language is installed. */
    var ready by mutableStateOf<Boolean?>(null); private set
    var speaking by mutableStateOf(false); private set
    private val main = Handler(Looper.getMainLooper())
    private val audio = context.getSystemService(AudioManager::class.java)
    private val attributes = AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ASSISTANT)
        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build()
    private val focus = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK).setAudioAttributes(attributes).build()
    private var pending: String? = null
    private var closed = false
    private val engine: TextToSpeech = TextToSpeech(context.applicationContext) { status -> main.post { started(status) } }

    private fun started(status: Int) {
        if (closed) return
        val voice = if (status == TextToSpeech.SUCCESS) voiceLookup(engine) else null
        if (voice == null || engine.setVoice(voice) != TextToSpeech.SUCCESS) { ready = false; pending = null; return }
        engine.setAudioAttributes(attributes)
        engine.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) = Unit
            override fun onDone(utteranceId: String?) { main.post { finished() } }
            @Deprecated("Deprecated in Java") override fun onError(utteranceId: String?) { main.post { finished() } }
            override fun onError(utteranceId: String?, errorCode: Int) { main.post { finished() } }
            override fun onStop(utteranceId: String?, interrupted: Boolean) { main.post { finished() } }
        })
        ready = true
        pending?.let { pending = null; speak(it) }
    }

    /** Speaks [text] now, or once the engine is ready. Replaces anything still being read. */
    fun speak(text: String) {
        if (closed || text.isBlank()) return
        when (ready) {
            null -> pending = text
            false -> Unit
            true -> {
                audio.requestAudioFocus(focus)
                // Ask the engine not to use network synthesis, on top of the offline voice.
                val params = Bundle().apply { putString(TextToSpeech.Engine.KEY_FEATURE_NETWORK_SYNTHESIS, "false") }
                speaking = engine.speak(text, TextToSpeech.QUEUE_FLUSH, params, "rem-reply") == TextToSpeech.SUCCESS
                if (!speaking) audio.abandonAudioFocusRequest(focus)
            }
        }
    }

    fun stop() {
        pending = null
        if (ready == true) engine.stop()
        finished()
    }

    fun shutdown() {
        stop(); closed = true
        engine.shutdown()
    }

    private fun finished() {
        if (speaking) audio.abandonAudioFocusRequest(focus)
        speaking = false
    }

    companion object {
        /** How a voice is chosen; tests replace it to stand in for a phone with no voice installed. */
        internal var voiceLookup: (TextToSpeech) -> Voice? = ::offlineVoice

        /** The engine's default voice if it works offline, else an offline voice in the phone's language. */
        internal fun offlineVoice(engine: TextToSpeech): Voice? {
            val offline = runCatching { engine.voices }.getOrNull().orEmpty()
                .filter { !it.isNetworkConnectionRequired && TextToSpeech.Engine.KEY_FEATURE_NOT_INSTALLED !in it.features }
            val language = Locale.getDefault()
            val default = runCatching { engine.defaultVoice }.getOrNull()
            return offline.firstOrNull { it == default && it.locale.language == language.language }
                ?: offline.firstOrNull { it.locale == language }
                ?: offline.firstOrNull { it.locale.language == language.language }
        }
    }
}
