package dev.raticode.mobile.ui

import org.junit.Assert.assertEquals
import org.junit.Test

/** Picked files are read one at a time, and a refused one doesn't hold up the rest. */
class ImportQueueTest {
    @Test fun filesAreReadInTurnAndEachAnswerCountsOnce() {
        val started = mutableListOf<String>()
        val answers = mutableListOf<() -> Unit>()
        val queue = ImportQueue<String> { item, done -> started += item; answers += done }
        queue.add(listOf("a", "b"))
        queue.add(listOf("c"))
        assertEquals(listOf("a"), started)
        assertEquals(listOf("a", "b", "c"), queue.waiting)
        // A late second answer for the same file (a timeout, then the import) doesn't skip the next one.
        answers[0](); answers[0]()
        assertEquals(listOf("a", "b"), started)
        assertEquals(listOf("b", "c"), queue.waiting)
        answers[1](); answers[2]()
        assertEquals(listOf("a", "b", "c"), started)
        assertEquals(emptyList<String>(), queue.waiting)
        queue.add(emptyList())
        assertEquals(3, started.size)
    }

    @Test fun anAnswerDuringReadMovesOnAtOnce() {
        val started = mutableListOf<String>()
        val queue = ImportQueue<String> { item, done -> started += item; done() }
        queue.add(listOf("a", "b", "c"))
        assertEquals(listOf("a", "b", "c"), started)
        assertEquals(emptyList<String>(), queue.waiting)
    }
}
