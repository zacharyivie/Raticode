package dev.raticode.mobile.live

import dev.raticode.mobile.ui.endDay
import dev.raticode.mobile.ui.memoryNoteLines
import java.time.LocalDate
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The phone reads Rem's memories and reply notes from the same fields the desktop panel uses. */
class RemMemoryTest {
    @Test fun memoryRequestsShareOneScreen() {
        for (op in listOf("rem.memories", "rem.memory.add", "rem.memory.edit", "rem.memory.forget", "rem.memory.mode", "rem.memory.undo")) {
            assertTrue(op in DesktopViews.OPERATIONS)
            assertEquals("rem.memories", DesktopViews.key(op, JSONObject()))
        }
        assertTrue("rem.memories" in DesktopViews.READS)
        assertFalse("rem.memory.undo" in DesktopViews.READS)
    }

    @Test fun memoryListAndModeParse() {
        val json = JSONObject("""{"mode":"paused","memories":[
            {"id":"a","text":"Works in Eastern time","created_at":"2026-11-01T00:00:00+00:00","edited_at":"","added_by_user":true},
            {"id":"b","text":"Prefers HTML reports","created_at":"2026-10-08T00:00:00+00:00","edited_at":"2026-10-09T00:00:00+00:00","project":"Raticode"},
            {"id":"c","text":"In Lisbon this week","created_at":"2026-11-01T00:00:00+00:00","until":"2026-11-08T12:00:00+00:00","ended":true}]}""")
        val parsed = DesktopViews.parse("rem.memories", json) as RemMemories
        assertEquals("paused", parsed.mode)
        assertEquals(listOf("a", "b", "c"), parsed.memories.map { it.id })
        // A dated memory carries its end, and whether it has passed; others carry neither.
        assertEquals("2026-11-08T12:00:00+00:00", parsed.memories[2].until)
        assertTrue(parsed.memories[2].ended)
        assertEquals("", parsed.memories[0].until)
        assertFalse(parsed.memories[0].ended)
        assertTrue(parsed.memories[0].addedByUser)
        assertEquals("2026-10-09T00:00:00+00:00", parsed.memories[1].editedAt)
        // A project memory carries its project's name for the tag; a global one carries none.
        assertEquals("Raticode", parsed.memories[1].project)
        assertEquals("", parsed.memories[0].project)
        assertEquals("on", (DesktopViews.parse("rem.memories", JSONObject()) as RemMemories).mode)
    }

    @Test fun replyNotesSayWhatChangedAndOfferUndoOnce() {
        val home = DesktopViews.parse("rem.home", JSONObject("""{"memories":true,"messages":[
            {"id":"reply-1","role":"rem","body":"Noted.","memory":{"saved":[{"id":"t","text":"Prefers tea","removed":false}],
              "forgotten":[{"id":"o","text":"","removed":true}],"refused":2,"undone":false}},
            {"id":"reply-2","role":"rem","body":"Undone.","memory":{"saved":[{"id":"x","text":"Lives in Ohio"}],"undone":true}},
            {"id":"reply-3","role":"rem","body":"Plain."},
            {"id":"reply-4","role":"rem","body":"Noted.","memory":{"saved":[{"id":"d","text":"Flying to Oslo","until":"2026-12-01T12:00:00+00:00"}]}}]}""")) as RemHome
        assertTrue(home.memories)
        val (first, second, third) = home.messages
        // A fact saved with an end date says so in its note, beside Undo.
        assertTrue(home.messages[3].memory!!.saved.single().until.startsWith("2026-12-01"))
        assertEquals(listOf("Saved to memory until ${endDay("2026-12-01T12:00:00+00:00")}: “Flying to Oslo”"), memoryNoteLines(home.messages[3].memory!!))
        val note = first.memory!!
        assertTrue(note.undoable)
        assertEquals(listOf("Saved to memory: “Prefers tea”", "Forgot: a memory you later forgot",
            "Didn’t save 2 items that looked like passwords, keys or tokens."), memoryNoteLines(note))
        assertFalse(second.memory!!.undoable)
        assertNull(third.memory)
        // An older desktop says nothing about memory, so the phone shows no memory controls.
        assertFalse((DesktopViews.parse("rem.home", JSONObject("""{"messages":[]}""")) as RemHome).memories)
        assertFalse((DesktopViews.parse("overview", JSONObject()) as DesktopOverview).memories)
    }

    @Test fun endDaysShowTheYearOnlyWhenItDiffers() {
        val today = LocalDate.of(2026, 11, 20)
        assertEquals("Dec 1", endDay("2026-12-01T12:00:00+00:00", today).replace(Regex("[^A-Za-z0-9 ,]"), ""))
        assertTrue(endDay("2027-01-05T12:00:00+00:00", today).endsWith("2027"))
        assertEquals("not-a-date", endDay("not-a-date"))
    }
}
