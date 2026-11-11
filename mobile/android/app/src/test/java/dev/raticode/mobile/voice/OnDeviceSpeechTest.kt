package dev.raticode.mobile.voice

import android.speech.RecognizerIntent
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class OnDeviceSpeechTest {
    @Test fun silenceLengthsAreWholeNumbersTheRecognizerReads() {
        // Google's recognizer reads these as Int and ignores a Long, which ended a session at every short pause.
        val extras = OnDeviceSpeech.extras(33)
        assertEquals(5000, extras[RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS])
        assertEquals(4000, extras[RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS])
        extras.values.forEach { assertFalse("$it is a Long", it is Long) }
    }

    @Test fun androidThirteenAndNewerAskForOneSessionAcrossPauses() {
        assertEquals(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, OnDeviceSpeech.extras(33)[RecognizerIntent.EXTRA_SEGMENTED_SESSION])
        assertFalse(RecognizerIntent.EXTRA_SEGMENTED_SESSION in OnDeviceSpeech.extras(32))
    }

    @Test fun swearWordsAreWrittenOutWhereAndroidAllowsIt() {
        // Without this Android writes "f***"; the option exists from Android 13.
        assertEquals(false, OnDeviceSpeech.extras(33)[RecognizerIntent.EXTRA_MASK_OFFENSIVE_WORDS])
        assertEquals(false, OnDeviceSpeech.extras(36)[RecognizerIntent.EXTRA_MASK_OFFENSIVE_WORDS])
        assertFalse(RecognizerIntent.EXTRA_MASK_OFFENSIVE_WORDS in OnDeviceSpeech.extras(32))
    }

    @Test fun recognitionStaysOfflineAndFreeForm() {
        val extras = OnDeviceSpeech.extras(31)
        assertEquals(true, extras[RecognizerIntent.EXTRA_PREFER_OFFLINE])
        assertEquals(RecognizerIntent.LANGUAGE_MODEL_FREE_FORM, extras[RecognizerIntent.EXTRA_LANGUAGE_MODEL])
        assertEquals(true, extras[RecognizerIntent.EXTRA_PARTIAL_RESULTS])
    }
}
