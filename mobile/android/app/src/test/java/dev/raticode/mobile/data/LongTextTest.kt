package dev.raticode.mobile.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Long pasted, dictated or spoken text goes to Rem as a text file instead of hitting the message limit. */
class LongTextTest {
    private val paste = "Meeting notes from Tuesday: " + "the build needs a look. ".repeat(120)

    @Test fun aLongPasteGoesAsAFileAndTypedWordsStay() {
        val old = "Summarize this: "
        val edit = LongText.edit(old, old + paste, canFile = true)
        edit as LongText.Edit.ToFile
        assertEquals(old, edit.text)
        assertEquals(old.length, edit.cursor)
        assertEquals(paste.trim(), edit.file)
    }

    @Test fun aPasteInTheMiddleKeepsTheWordsAroundIt() {
        val edit = LongText.edit("Before after", "Before " + paste + "after", canFile = true) as LongText.Edit.ToFile
        assertEquals("Before after", edit.text)
        assertEquals("Before ".length, edit.cursor)
    }

    @Test fun aPasteOverASelectionReplacesIt() {
        val edit = LongText.edit("Keep THIS end", "Keep " + paste + " end", canFile = true) as LongText.Edit.ToFile
        assertEquals("Keep  end", edit.text)
    }

    @Test fun shortPastesAndTypingStayInTheMessage() {
        assertEquals(LongText.Edit.Keep("Hello Rem"), LongText.edit("Hello Re", "Hello Rem", canFile = true))
        val medium = "x ".repeat(900)
        assertEquals(LongText.Edit.Keep(medium), LongText.edit("", medium, canFile = true))
        // Deleting never makes a file.
        assertEquals(LongText.Edit.Keep("Hel"), LongText.edit("Hello", "Hel", canFile = true))
    }

    @Test fun aMediumPastePastTheLimitAlsoGoesAsAFile() {
        val typed = "y".repeat(3800)
        val addition = "z ".repeat(150)
        val edit = LongText.edit(typed, typed + addition, canFile = true) as LongText.Edit.ToFile
        assertEquals(typed, edit.text)
        assertEquals(addition.trim(), edit.file)
    }

    @Test fun typingStopsAtTheLimitAndSaysHowMuchDidntFit() {
        val full = "y".repeat(LongText.LIMIT)
        val edit = LongText.edit(full, full + "abc", canFile = true) as LongText.Edit.Keep
        assertEquals(full, edit.text)
        assertEquals(LongText.LIMIT, edit.cursor)
        assertEquals(3, edit.cut)
    }

    @Test fun withoutFilesALongPasteIsCutAndTheCutIsCounted() {
        val huge = paste + paste
        val edit = LongText.edit("Hi ", "Hi " + huge, canFile = false) as LongText.Edit.Keep
        assertEquals(LongText.LIMIT, edit.text.length)
        assertTrue(edit.text.startsWith("Hi Meeting notes"))
        assertEquals(huge.length - (LongText.LIMIT - 3), edit.cut)
        assertEquals(LongText.LIMIT, edit.cursor)
    }

    @Test fun aCutPasteInTheMiddleKeepsTheEnd() {
        val old = "a".repeat(1000) + "END"
        val edit = LongText.edit(old, "a".repeat(1000) + "b".repeat(5000) + "END", canFile = false) as LongText.Edit.Keep
        assertEquals(LongText.LIMIT, edit.text.length)
        assertTrue(edit.text.endsWith("bEND"))
        assertEquals(LongText.LIMIT - 3, edit.cursor)
        assertEquals(5000 - (LongText.LIMIT - old.length), edit.cut)
    }

    @Test fun findsWhatWasAdded() {
        assertEquals(3 until 6, LongText.added("abcdef", "abcXYZdef"))
        assertEquals(0 until 3, LongText.added("", "abc"))
        // Repeated letters: the addition is still the right length.
        assertEquals(3, LongText.added("aaa", "aaaaaa").let { it.last - it.first + 1 })
    }

    @Test fun namesSurviveTheDesktopsFileNameRules() {
        assertEquals("pasted-text-meeting-notes-from-tuesday.txt", LongText.name(LongText.Kind.PASTED, paste))
        assertEquals("spoken-text-so-i-was-thinking.txt", LongText.name(LongText.Kind.SPOKEN, "So, I was thinking — about the build…"))
        assertEquals("long-message.txt", LongText.name(LongText.Kind.MESSAGE, "日本語だけ"))
        assertEquals("pasted-text-${"supercalifragilisticexpialidocious".take(32)}.txt",
            LongText.name(LongText.Kind.PASTED, "Supercalifragilisticexpialidocious and more words"))
        // Only what the desktop keeps as it is.
        assertTrue(LongText.name(LongText.Kind.PASTED, "Ünïcode / slashes: \"quotes\" <tags> ../../etc").matches(Regex("[a-z0-9.-]+")))
    }

    @Test fun previewIsOneShortLine() {
        assertEquals("Short note", LongText.preview("  Short\n\nnote "))
        val shown = LongText.preview(paste)
        assertTrue(shown.length <= LongText.PREVIEW_CHARS + 1)
        assertTrue(shown.endsWith("…"))
        assertFalse(shown.contains("\n"))
        assertTrue(shown.startsWith("Meeting notes from Tuesday"))
    }

    @Test fun counterShowsNearTheLimit() {
        assertNull(LongText.counter(3499))
        assertEquals("3,500 / 4,000", LongText.counter(3500))
        assertEquals("4,000 / 4,000", LongText.counter(4000))
    }

    @Test fun putBackOnlyWhenItFits() {
        assertEquals("Typed\n\nPasted words", LongText.putBack("Typed ", "Pasted words"))
        assertEquals("Pasted words", LongText.putBack("", " Pasted words "))
        assertNull(LongText.putBack("x".repeat(3000), "y".repeat(1500)))
    }

    @Test fun saysWhyLongTextCantGoAsAFile() {
        assertNull(LongText.blocked(connected = true, filesThread = true, room = 1))
        assertEquals("Connect to your computer to send long text as a file.", LongText.blocked(false, true, 8))
        assertEquals("Update Raticode on your computer to send long text as a file.", LongText.blocked(true, false, 8))
        assertEquals("This message already carries 8 files, so long text can't go as another.", LongText.blocked(true, true, 0))
        assertEquals("Rem takes up to 4,000 characters in a message, so the last 1,234 characters weren't added. " +
            "Connect to your computer to send long text as a file.", LongText.cutNote(1234, LongText.blocked(false, true, 8)))
    }

    @Test fun theLargestFileFitsTheFileLimit() {
        // Three bytes per character at most in UTF-8 (surrogate pairs are four bytes for two characters).
        assertTrue(LongText.MAX_CHARS * 3L < FileMetadata.MAX_FILE)
        assertTrue(LongText.FILE_AT < LongText.LIMIT)
        assertTrue(LongText.COUNTER_AT < LongText.LIMIT)
    }
}
