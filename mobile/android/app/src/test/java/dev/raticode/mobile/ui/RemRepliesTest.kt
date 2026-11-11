package dev.raticode.mobile.ui

import androidx.compose.runtime.saveable.SaverScope
import dev.raticode.mobile.live.RemMessage
import dev.raticode.mobile.live.RemReference
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.LocalDate

/** The reply being written survives rotation, and a reply's quote finds its message in the phone's part of the chat. */
class RemRepliesTest {
    private val scope = SaverScope { true }

    @Test fun theReplyTargetIsKeptLikeTheDraft() {
        val target = RemReference("rem-1", "rem", "The deploy failed.", "2026-11-03T10:00:00Z", "task_update", "Deploy", 2)
        val saved = with(ReplyTargetSaver) { scope.save(target) }!!
        assertEquals(target, ReplyTargetSaver.restore(saved))
        val gone = RemReference("gone", unavailable = true)
        assertEquals(gone, ReplyTargetSaver.restore(with(ReplyTargetSaver) { scope.save(gone) }!!))
        assertNull(ReplyTargetSaver.restore(with(ReplyTargetSaver) { scope.save(null) }!!))
    }

    @Test fun aQuoteScrollsOnlyToAMessageThePhoneHas() {
        val day = "2026-11-03T10:00:00Z"
        val rows = remRows(listOf(RemMessage("a", "rem", "Hi", day, null, ""), RemMessage("b", "user", "Why?", day, null, "", refersTo = "a")),
            LocalDate.parse("2026-11-03"))
        // One list item comes before the rows, then a day label.
        assertEquals(2, rowIndex(rows, "a"))
        assertEquals(3, rowIndex(rows, "b"))
        assertEquals(-1, rowIndex(rows, "older"))
    }
}
