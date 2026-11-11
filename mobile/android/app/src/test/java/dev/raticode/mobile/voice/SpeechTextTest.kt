package dev.raticode.mobile.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SpeechTextTest {
    @Test fun markdownBecomesPlainSentences() {
        val spoken = SpeechText.forSpeech("""
            ## Status
            - **Two** tasks are running
            - One is _waiting_ for review in my_task_name
            1. Read the [report](https://example.com/r.html) first
            > Quoted note
        """.trimIndent())
        assertEquals("Status. Two tasks are running. One is waiting for review in my_task_name. Read the report first. Quoted note.", spoken)
    }

    @Test fun codeLinksAndImagesAreNotReadOut() {
        val spoken = SpeechText.forSpeech("Run this:\n```bash\nrm -rf build\n```\nSee ![chart](https://x/c.png) and https://example.com/page or `gradlew`.")
        assertFalse(spoken.contains("rm -rf"))
        assertFalse(spoken.contains("https"))
        assertFalse(spoken.contains("chart"))
        assertTrue(spoken, spoken.startsWith("Run this: There's code on screen."))
        assertTrue(spoken, spoken.endsWith("See and a link or gradlew."))
    }

    @Test fun tablesReadAsListsWithoutRules() {
        val spoken = SpeechText.forSpeech("| Task | State |\n| --- | :---: |\n| Sync | done |\n\n---\nEnd")
        assertEquals("Task, State. Sync, done. End.", spoken)
    }

    @Test fun longRepliesStopAtASentenceAndPointToTheScreen() {
        val reply = (1..40).joinToString(" ") { "Sentence number $it is here." }
        val spoken = SpeechText.forSpeech(reply)
        assertTrue(spoken.length <= SpeechText.LIMIT + SpeechText.REST.length + 1)
        assertTrue(spoken, spoken.endsWith("is here. " + SpeechText.REST))
        val oneLongSentence = SpeechText.forSpeech("word ".repeat(300), limit = 100)
        assertTrue(oneLongSentence, oneLongSentence.endsWith("… " + SpeechText.REST))
        assertEquals("Short.", SpeechText.forSpeech("Short."))
    }
}
