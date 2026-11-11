package dev.raticode.mobile.voice

import dev.raticode.mobile.live.TidyResult
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ProgressiveTidyTest {
    @Test fun eachPauseRetidiesEverythingSaidAndShowsItAfterTheKeptText() {
        val tidy = ProgressiveTidy("Note:")
        assertEquals("Note:", tidy.shown)
        assertEquals("book a room um for friday", tidy.next(" book a room um for friday ", 0L))
        assertTrue(tidy.answered("book a room um for friday", TidyResult("Book a room for Friday.", true)))
        assertEquals("Note: Book a room for Friday.", tidy.shown)

        // More words after the next pause: the whole message goes again, not just the new sentence.
        val more = "book a room um for friday and invite sam"
        assertEquals(more, tidy.next(more, 0L))
        assertTrue(tidy.answered(more, TidyResult("Book a room for Friday and invite Sam.", true)))
        assertEquals("Note: Book a room for Friday and invite Sam.", tidy.shown)
        assertEquals("Book a room for Friday and invite Sam.", tidy.ready(more))
    }

    @Test fun oneRequestAtATimeAndNothingTwiceForTheSameWords() {
        val tidy = ProgressiveTidy("")
        assertEquals("hello", tidy.next("hello", 0L))
        assertNull("the same words are already out", tidy.next("hello", 0L))
        assertNull("newer words wait for the answer", tidy.next("hello there", 0L))
        tidy.answered("hello", TidyResult("Hello.", true))
        assertNull("already shown", tidy.next("hello", 0L))
        assertEquals("hello there", tidy.next("hello there", 0L))
        assertNull(tidy.next("", 0L))
    }

    @Test fun aFailedAnswerKeepsWhatIsShownAndSendTidiesAgain() {
        val tidy = ProgressiveTidy("")
        tidy.next("first part", 0L)
        tidy.answered("first part", TidyResult("First part.", true))
        tidy.next("first part second part", 0L)
        assertFalse(tidy.answered("first part second part", TidyResult("first part second part", false, "Tidying took too long.")))
        assertEquals("First part.", tidy.shown)
        assertNull("Send has to tidy the words the failure left out", tidy.ready("first part second part"))
        // Not straight back to the same words; more speech, or Send's own final tidy, asks again.
        assertNull(tidy.next("first part second part", 0L))
        assertEquals("first part second part third", tidy.next("first part second part third", 0L))
    }

    @Test fun aDroppedRequestCanBeAskedAgainAndItsLateAnswerIsIgnored() {
        val tidy = ProgressiveTidy("")
        tidy.next("one", 0L)
        tidy.dropped("one")
        assertEquals("one two", tidy.next("one two", 0L))
        assertFalse(tidy.answered("one", TidyResult("One.", true)))
        assertEquals("", tidy.shown)
        assertTrue(tidy.answered("one two", TidyResult("One, two.", true)))
        assertNull(tidy.ready("one two three"))
    }

    @Test fun whileTalkingARoundNeedsFifteenNewWordsAndFiveSecondsSinceTheLast() {
        val tidy = ProgressiveTidy("", startedAt = 0L)
        val words = (1..60).map { "w$it" }
        fun said(n: Int) = words.take(n).joinToString(" ")
        // Slow talker: five seconds pass with three words; nothing is cut off.
        assertNull(tidy.waitMs(said(3), 5_000))
        assertFalse(tidy.due(said(14), 9_000))
        // Fast talker: fifteen words in three seconds wait two more, and every word said meanwhile goes too.
        assertEquals(2_000L, tidy.waitMs(said(15), 3_000))
        assertFalse(tidy.due(said(24), 4_999))
        assertTrue(tidy.due(said(27), 5_000))
        assertEquals(said(27), tidy.next(said(27), 5_000))
        // While it is out nothing more is due, however much is said.
        assertNull(tidy.waitMs(said(50), 20_000))
        tidy.answered(said(27), TidyResult("W1 to w27.", true))
        // The next round counts words and time from that request: 15 more words and 5 more seconds.
        assertFalse(tidy.due(said(41), 20_000))
        assertFalse(tidy.due(said(42), 9_999))
        assertTrue(tidy.due(said(42), 10_000))
        assertEquals(said(42), tidy.next(said(42), 10_000))
        // A failed round still counts, so a failing desktop isn't asked again at every word.
        tidy.answered(said(42), TidyResult(said(42), false, "Tidying took too long."))
        assertFalse(tidy.due(said(56), 30_000))
        assertEquals("W1 to w27.", tidy.shown)
    }

    @Test fun aPauseSendsWhateverIsLeftEvenBelowBothThresholds() {
        val tidy = ProgressiveTidy("", startedAt = 0L)
        assertFalse(tidy.due("just three words", 1_000))
        // The pause path asks directly; thresholds only apply while talking.
        assertEquals("just three words", tidy.next("just three words", 1_000))
    }

    @Test fun onlyRealWordsAreEverTidiedOrCounted() {
        val tidy = ProgressiveTidy("")
        assertNull(tidy.next("", 0L))
        assertNull(tidy.next("  ...  ", 0L))
        assertFalse(tidy.due(". , ! ? ".repeat(20), 60_000))
        assertEquals(3, ProgressiveTidy.count("book a room ... !"))
        assertTrue(ProgressiveTidy.hasVoice("ok"))
        assertFalse(ProgressiveTidy.hasVoice(" - "))
    }

    @Test fun sendWhileListeningNeedsWordsAndNoTidyRoundOut() {
        assertFalse("nothing heard yet", VoiceLogic.canSendSpoken("", tidyPending = false, filesRead = true))
        assertFalse("punctuation isn't voice", VoiceLogic.canSendSpoken(" . ", tidyPending = false, filesRead = true))
        assertFalse("a tidy round is out", VoiceLogic.canSendSpoken("book a room", tidyPending = true, filesRead = true))
        assertFalse("files still being read", VoiceLogic.canSendSpoken("book a room", tidyPending = false, filesRead = false))
        assertTrue(VoiceLogic.canSendSpoken("book a room", tidyPending = false, filesRead = true))
    }

    @Test fun wordsWhoseRoundFailedArentAskedAgainUntilMoreIsSaid() {
        // A desktop that fails at once (tidying off, offline) must not be asked in a loop while the user is quiet.
        val tidy = ProgressiveTidy("")
        assertEquals("check the build", tidy.next("check the build", 0L))
        tidy.answered("check the build", TidyResult("check the build", false, "Tidying is off on your desktop."))
        assertNull(tidy.next("check the build", 10L))
        assertEquals("check the build now", tidy.next("check the build now", 20L))
    }

    @Test fun aPauseIsTwoSecondsOfQuiet() {
        assertEquals(2_000L, ProgressiveTidy.PAUSE_MS)
        assertTrue(ProgressiveTidy.PAUSE_MS < Dictation.QUIET_MS)
    }
}
