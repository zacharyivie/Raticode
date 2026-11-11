package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.util.UUID

/** Phone sections follow desktop organizeThreads in frontend/src/lib/threadActivity.js. */
class ThreadSectionsTest {
    private fun at(minute: Int) = "2026-10-30T16:%02d:00.000Z".format(minute)
    private fun ids(list: List<ThreadEntry>) = list.map { it.id }

    @Test fun classifiesEveryDesktopStateIntoItsSection() {
        val sections = ThreadActivity.organize(listOf(
            ThreadEntry("pinned", "Pinned", group = "pinned", updatedAt = at(1)),
            ThreadEntry("pinned-running", "Pinned and running", group = "pinned", running = true, updatedAt = at(2)),
            ThreadEntry("unread", "Unread", unread = true, updatedAt = at(3)),
            ThreadEntry("running", "Running", running = true, updatedAt = at(4)),
            ThreadEntry("running-unread", "Running wins over unread", running = true, unread = true, updatedAt = at(5)),
            ThreadEntry("read", "Read", updatedAt = at(6)),
            ThreadEntry("archived", "Archived", group = "archived", updatedAt = at(7)),
            ThreadEntry("archived-running", "Archived stays archived", group = "archived", running = true, unread = true, updatedAt = at(8)),
        ))
        assertEquals(listOf("pinned-running", "pinned"), ids(sections.pinned))
        assertEquals(listOf("unread"), ids(sections.unread))
        assertEquals(listOf("running-unread", "running"), ids(sections.running))
        assertEquals(listOf("read"), ids(sections.read))
        assertEquals(listOf("archived-running", "archived"), ids(sections.archived))
        assertEquals(4, sections.visibleCount)
    }

    @Test fun keepsSevenMostRecentlyReadAndArchivesTheRest() {
        val read = (0 until 9).map { ThreadEntry("read-$it", "Read $it", updatedAt = at(it)) }
        val sections = ThreadActivity.organize(read + ThreadEntry("old-archive", "Old", group = "archived", updatedAt = at(30)))
        assertEquals(ThreadActivity.READ_LIMIT, sections.read.size)
        assertEquals((8 downTo 2).map { "read-$it" }, ids(sections.read))
        // Archived threads sort by when they were last read or updated, newest first.
        assertEquals(listOf("old-archive", "read-1", "read-0"), ids(sections.archived))
    }

    @Test fun readTimeUsesTheLaterOfReadAndUpdateAcrossTimestampFormats() {
        val sections = ThreadActivity.organize(listOf(
            ThreadEntry("updated-later", "A", updatedAt = "2026-10-30T16:10:00+00:00"),
            ThreadEntry("read-latest", "B", updatedAt = at(1), readAt = at(20)),
            ThreadEntry("no-date", "C"),
        ))
        assertEquals(listOf("read-latest", "updated-later", "no-date"), ids(sections.read))
        assertEquals(0L, ThreadActivity.time("not a date"))
    }

    @Test fun emptyListHasNoVisibleThreads() {
        val sections = ThreadActivity.organize(emptyList())
        assertEquals(0, sections.visibleCount)
        assertTrue(sections.archived.isEmpty() && sections.running.isEmpty())
    }

    private fun thread(id: String, unread: Boolean, updated: String) = JSONObject().put("thread_id", id)
        .put("unread", unread).put("updated_at", updated)

    @Test fun openingAThreadOnThePhoneHidesUnreadUntilDesktopUpdatesIt() {
        val id = UUID.randomUUID().toString(); val other = UUID.randomUUID().toString()
        val state = JSONObject().put("threads", JSONArray().put(thread(id, true, at(1))).put(thread(other, true, at(2))))
        assertTrue(ThreadActivity.unread(state.getJSONArray("threads").getJSONObject(0), state.optJSONObject("read_marks")))
        assertTrue(ThreadActivity.markRead(state, id))
        val marks = state.getJSONObject("read_marks")
        assertFalse(ThreadActivity.unread(state.getJSONArray("threads").getJSONObject(0), marks))
        assertTrue("Other threads stay unread", ThreadActivity.unread(state.getJSONArray("threads").getJSONObject(1), marks))
        assertFalse("Marking again changes nothing", ThreadActivity.markRead(state, id))
        // A newer desktop reply makes it unread again.
        state.getJSONArray("threads").getJSONObject(0).put("updated_at", at(5))
        assertTrue(ThreadActivity.unread(state.getJSONArray("threads").getJSONObject(0), state.getJSONObject("read_marks")))
    }

    @Test fun readMarksSkipReadThreadsAndForgetRemovedOnes() {
        val id = UUID.randomUUID().toString()
        val state = JSONObject().put("threads", JSONArray().put(thread(id, false, at(1))))
        assertFalse(ThreadActivity.markRead(state, id))
        assertFalse(state.has("read_marks"))
        assertFalse(ThreadActivity.markRead(state, null))
        state.put("read_marks", JSONObject().put(UUID.randomUUID().toString(), at(1)))
        assertTrue(ThreadActivity.markRead(state, null))
        assertFalse(state.has("read_marks"))
    }

    private fun snapshot(entry: JSONObject) = JSONObject().put("type", "sync.snapshot").put("thread_id", JSONObject.NULL)
        .put("request_id", UUID.randomUUID().toString()).put("payload", JSONObject().put("revision", 1).put("through_sequence", 0)
            .put("threads", JSONArray().put(entry)))

    @Test fun syncAcceptsBoundedActivityFieldsOnly() {
        val entry = JSONObject().put("thread_id", UUID.randomUUID().toString()).put("revision", 1).put("title", "Shared")
            .put("project_id", JSONObject.NULL).put("resource_ids", JSONArray()).put("provider", "codex").put("model", "model-a")
            .put("group", "active").put("running", false).put("updated_at", at(1)).put("unread", true).put("read_at", at(0))
        IncomingValidation.validate(snapshot(entry))
        entry.put("unread", "yes")
        assertThrows(IllegalArgumentException::class.java) { IncomingValidation.validate(snapshot(entry)) }
        entry.put("unread", true).put("read_at", "x".repeat(81))
        assertThrows(IllegalArgumentException::class.java) { IncomingValidation.validate(snapshot(entry)) }
        entry.put("read_at", at(0)).put("group", "unread")
        assertThrows(IllegalArgumentException::class.java) { IncomingValidation.validate(snapshot(entry)) }
    }

    @Test fun threadSnapshotStillRejectsActivityFields() {
        val payload = JSONObject().put("title", "Shared").put("project_id", JSONObject.NULL).put("resource_ids", JSONArray())
            .put("provider", "codex").put("model", "model-a").put("revision", 1).put("unread", true)
        val event = JSONObject().put("type", "thread.snapshot").put("request_id", UUID.randomUUID().toString())
            .put("thread_id", UUID.randomUUID().toString()).put("payload", payload)
        assertThrows(IllegalArgumentException::class.java) { IncomingValidation.validate(event) }
    }

    @Test fun markingReadOrUnreadUpdatesThisPhoneAtOnce() {
        val a = UUID.randomUUID().toString(); val b = UUID.randomUUID().toString()
        val state = JSONObject().put("threads", JSONArray().put(thread(a, true, at(1))).put(thread(b, false, at(2))))
        assertTrue(ThreadActivity.setRead(state, listOf(a, b), read = true))
        val marks = state.getJSONObject("read_marks")
        assertFalse(ThreadActivity.unread(state.getJSONArray("threads").getJSONObject(0), marks))
        assertFalse("Read threads need no mark", marks.has(b))
        assertFalse("Nothing left to change", ThreadActivity.setRead(state, listOf(a), read = true))
        assertTrue(ThreadActivity.setRead(state, listOf(a), read = false))
        assertFalse(state.has("read_marks"))
        assertTrue("Unread again until the desktop says otherwise", ThreadActivity.unread(state.getJSONArray("threads").getJSONObject(0), null))
    }

    @Test fun selectionTogglesThreadsAndHeadersSelectWholeSections() {
        var selected = ThreadSelection.toggle(emptySet(), "a")
        selected = ThreadSelection.toggle(selected, "b")
        assertEquals(setOf("a", "b"), selected)
        assertEquals(setOf("b"), ThreadSelection.toggle(selected, "a"))
        val unread = listOf("u1", "u2")
        val section = ThreadSelection.section(setOf("a"), unread)
        assertEquals("A header replaces another selection", unread.toSet(), section)
        assertEquals("Activating it again clears the section", emptySet<String>(), ThreadSelection.section(section, unread))
        assertEquals(emptySet<String>(), ThreadSelection.section(emptySet(), emptyList()))
    }

    @Test fun selectionOffersOnlyActionsThatChangeSomething() {
        fun names(list: List<ThreadEntry>, sync: Boolean = true) = ThreadSelection.actions(list, sync).map { it.action }
        val unread = ThreadEntry("u", "Unread", unread = true)
        val pinned = ThreadEntry("p", "Pinned", group = "pinned")
        val archived = ThreadEntry("x", "Archived", group = "archived")
        assertEquals(listOf("read", "pin", "archive", "delete"), names(listOf(unread)))
        assertEquals(listOf("unread", "pin", "unpin", "archive", "restore", "delete"), names(listOf(pinned, archived)))
        assertEquals("Mark as unread needs a desktop that accepts read state", listOf("unpin", "archive", "delete"), names(listOf(pinned), sync = false))
        assertTrue(ThreadSelection.actions(listOf(archived), true).last().danger)
        assertTrue(names(emptyList()).isEmpty())
        assertTrue("read" in ThreadSelection.MANAGE_ACTIONS && "unread" in ThreadSelection.MANAGE_ACTIONS)
    }

    @Test fun aThreadReadHereStaysReadWhenThePhonesOwnChangeMovesItsUpdateTime() {
        val id = UUID.randomUUID().toString()
        val state = JSONObject().put("threads", JSONArray().put(thread(id, true, at(1))))
        assertTrue(ThreadActivity.setRead(state, listOf(id), read = true))
        // Archiving answers with a new update time while the synced unread flag is still stale.
        state.getJSONArray("threads").getJSONObject(0).put("updated_at", at(9))
        ThreadActivity.keepRead(state, id, at(1), at(9))
        assertFalse(ThreadActivity.unread(state.getJSONArray("threads").getJSONObject(0), state.getJSONObject("read_marks")))
        // A thread that was not read here is left alone.
        val other = JSONObject().put("threads", JSONArray().put(thread(id, true, at(1)))).put("read_marks", JSONObject())
        ThreadActivity.keepRead(other, id, at(1), at(9))
        assertFalse(other.getJSONObject("read_marks").has(id))
    }
}
