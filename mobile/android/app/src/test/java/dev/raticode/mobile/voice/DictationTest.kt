package dev.raticode.mobile.voice

import android.speech.SpeechRecognizer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DictationTest {
    @Test fun wordsAccumulateAcrossSessionsAfterKeptText() {
        val words = DictationText("  Book a meeting ")
        assertEquals("Book a meeting", words.text)
        words.partial("for")
        words.partial("for Friday")
        assertEquals("Book a meeting for Friday", words.text)
        words.result("for Friday at noon")
        assertEquals("Book a meeting for Friday at noon", words.committed)
        words.partial("and invite Sam")
        assertEquals("Book a meeting for Friday at noon and invite Sam", words.text)
    }

    @Test fun cutOffSessionsKeepTheirPartialWords() {
        val words = DictationText()
        words.partial("remind me")
        words.interrupted()
        assertEquals("remind me", words.text)
        words.partial("tomorrow")
        // An empty final result keeps the words heard before it.
        words.result("")
        assertEquals("remind me tomorrow", words.text)
        words.interrupted()
        assertEquals("Nothing new is added twice", "remind me tomorrow", words.text)
    }

    @Test fun pausesContinueAndBrokenMicrophonesStop() {
        listOf(SpeechRecognizer.ERROR_NO_MATCH, SpeechRecognizer.ERROR_SPEECH_TIMEOUT, SpeechRecognizer.ERROR_RECOGNIZER_BUSY, SpeechRecognizer.ERROR_CLIENT)
            .forEach { assertEquals(it.toString(), SessionEnd.CONTINUE, Dictation.classify(it)) }
        listOf(SpeechRecognizer.ERROR_AUDIO, SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS, SpeechRecognizer.ERROR_SERVER_DISCONNECTED,
            SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE, SpeechRecognizer.ERROR_NETWORK)
            .forEach { assertEquals(it.toString(), SessionEnd.STOP, Dictation.classify(it)) }
        assertEquals("The microphone stopped working.", Dictation.problem(SpeechRecognizer.ERROR_AUDIO))
        assertTrue(Dictation.problem(SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED).contains("offline speech language"))
    }

    @Test fun longQuietPausesTheMicrophone() {
        assertFalse(Dictation.shouldPause(now = 10_000 + Dictation.QUIET_MS - 1, lastWords = 10_000))
        assertTrue(Dictation.shouldPause(now = 10_000 + Dictation.QUIET_MS, lastWords = 10_000))
    }

    @Test fun aPauseRestartsAtOnceButAFailingRecognizerGetsAMoment() {
        assertEquals(0L, Dictation.restartDelay(failedFast = false))
        assertTrue(Dictation.restartDelay(failedFast = true) > 0)
    }

    @Test fun onlyOrdinaryEndsKeepTheRecognizerForTheNextSession() {
        assertTrue(Dictation.reusableAfter(SpeechRecognizer.ERROR_SPEECH_TIMEOUT))
        assertTrue(Dictation.reusableAfter(SpeechRecognizer.ERROR_NO_MATCH))
        listOf(SpeechRecognizer.ERROR_RECOGNIZER_BUSY, SpeechRecognizer.ERROR_CLIENT, SpeechRecognizer.ERROR_AUDIO, SpeechRecognizer.ERROR_SERVER_DISCONNECTED)
            .forEach { assertFalse(it.toString(), Dictation.reusableAfter(it)) }
    }

    @Test fun segmentsAddUpWithinOneSession() {
        val words = DictationText("Book")
        words.partial("a meeting")
        words.result("a meeting")
        words.partial("for")
        words.result("for Friday")
        assertEquals("Book a meeting for Friday", words.text)
        // The session's empty end keeps everything.
        words.result("")
        assertEquals("Book a meeting for Friday", words.text)
    }
}
