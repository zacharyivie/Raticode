package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import java.time.OffsetDateTime

/** One synced thread with the fields desktop uses to place it in a section. */
data class ThreadEntry(val id: String, val title: String, val group: String = "active", val running: Boolean = false,
                      val unread: Boolean = false, val updatedAt: String = "", val readAt: String = "")

data class ThreadSections(val pinned: List<ThreadEntry>, val unread: List<ThreadEntry>, val read: List<ThreadEntry>,
                          val running: List<ThreadEntry>, val archived: List<ThreadEntry>) {
    val visibleCount get() = pinned.size + unread.size + read.size
}

/**
 * Mirrors desktop `organizeThreads` in frontend/src/lib/threadActivity.js. Desktop already
 * folds missing projects and read threads past its limit into the archived group before sync.
 */
object ThreadActivity {
    const val READ_LIMIT = 7

    fun time(value: String): Long = runCatching { OffsetDateTime.parse(value).toInstant().toEpochMilli() }.getOrDefault(0L)
    private fun readTime(entry: ThreadEntry) = maxOf(time(entry.readAt), time(entry.updatedAt))

    // Pinned, then unread, then the most recently read threads. Running threads leave
    // those groups until their turn ends; older read threads are archived.
    fun organize(entries: List<ThreadEntry>, readLimit: Int = READ_LIMIT): ThreadSections {
        val pinned = mutableListOf<ThreadEntry>(); val unread = mutableListOf<ThreadEntry>()
        val running = mutableListOf<ThreadEntry>(); val archived = mutableListOf<ThreadEntry>()
        val readCandidates = mutableListOf<ThreadEntry>()
        for (entry in entries) when {
            entry.group == "archived" -> archived += entry
            entry.group == "pinned" -> pinned += entry
            entry.running -> running += entry
            entry.unread -> unread += entry
            else -> readCandidates += entry
        }
        val byUpdate = compareByDescending<ThreadEntry> { time(it.updatedAt) }
        val byRead = compareByDescending<ThreadEntry>(::readTime)
        readCandidates.sortWith(byRead)
        archived += readCandidates.drop(readLimit)
        return ThreadSections(pinned.sortedWith(byUpdate), unread.sortedWith(byUpdate), readCandidates.take(readLimit),
            running.sortedWith(byUpdate), archived.sortedWith(byRead))
    }

    /** Desktop's unread flag, unless this phone already opened the thread since that update. */
    fun unread(thread: JSONObject, marks: JSONObject?): Boolean =
        thread.optBoolean("unread") && marks?.optString(thread.optString("thread_id")) != thread.optString("updated_at")

    /**
     * Records that the phone showed [thread] at its current desktop update time. The mark
     * lives in the encrypted journal and never changes desktop state.
     */
    fun markRead(state: JSONObject, thread: String?): Boolean {
        val threads = state.optJSONArray("threads") ?: JSONArray()
        val ids = (0 until threads.length()).map { threads.getJSONObject(it) }.associateBy { it.optString("thread_id") }
        val marks = state.optJSONObject("read_marks") ?: JSONObject()
        val before = marks.toString()
        marks.keys().asSequence().toList().filter { it !in ids }.forEach(marks::remove)
        ids[thread]?.takeIf { it.optBoolean("unread") }?.let { marks.put(thread, it.optString("updated_at")) }
        if (marks.length() == 0) state.remove("read_marks") else state.put("read_marks", marks)
        return marks.toString() != before
    }

    /**
     * A thread.snapshot answers this phone's own request, such as archiving, and can move the
     * thread's update time. A thread read here stays read; new desktop replies arrive by sync instead.
     */
    fun keepRead(state: JSONObject, thread: String, before: String?, after: String) {
        val marks = state.optJSONObject("read_marks") ?: return
        if (before != null && marks.optString(thread) == before && after.isNotEmpty()) marks.put(thread, after)
    }

    /**
     * Marks [threads] read (or clears their marks for unread) on this phone at once, before the
     * desktop confirms. Returns whether anything changed.
     */
    fun setRead(state: JSONObject, threads: Collection<String>, read: Boolean): Boolean {
        val list = state.optJSONArray("threads") ?: JSONArray()
        val ids = (0 until list.length()).map { list.getJSONObject(it) }.associateBy { it.optString("thread_id") }
        val marks = state.optJSONObject("read_marks") ?: JSONObject()
        val before = marks.toString()
        for (thread in threads) {
            if (!read) marks.remove(thread)
            else ids[thread]?.takeIf { it.optBoolean("unread") }?.let { marks.put(thread, it.optString("updated_at")) }
        }
        if (marks.length() == 0) state.remove("read_marks") else state.put("read_marks", marks)
        return marks.toString() != before
    }
}

/** One thing the user can do to every selected thread. */
data class ThreadAction(val action: String, val label: String, val danger: Boolean = false)

/**
 * Selection mode for the phone's thread list. Mirrors desktop
 * frontend/src/lib/threadSelection.js: a long press starts it, taps toggle threads, and a
 * section header selects every thread in that section.
 */
object ThreadSelection {
    /** thread.manage actions; `read` and `unread` need a desktop whose overview reports `thread_read`. */
    val MANAGE_ACTIONS = setOf("pin", "unpin", "archive", "restore", "delete", "read", "unread")

    fun toggle(selected: Set<String>, id: String): Set<String> = if (id in selected) selected - id else selected + id

    /** A header selects its section; activating it again while exactly that is selected clears it. */
    fun section(selected: Set<String>, section: List<String>): Set<String> =
        if (section.isNotEmpty() && selected == section.toSet()) emptySet() else section.toSet()

    /**
     * Only actions that change at least one selected thread. Marking unread needs a desktop that
     * accepts read state from the phone ([readSync]); marking read always updates this phone.
     */
    fun actions(selected: List<ThreadEntry>, readSync: Boolean): List<ThreadAction> = listOfNotNull(
        ThreadAction("read", "Mark as read").takeIf { selected.any { it.unread } },
        ThreadAction("unread", "Mark as unread").takeIf { readSync && selected.any { !it.unread } },
        ThreadAction("pin", "Pin").takeIf { selected.any { it.group != "pinned" } },
        ThreadAction("unpin", "Unpin").takeIf { selected.any { it.group == "pinned" } },
        ThreadAction("archive", "Archive").takeIf { selected.any { it.group != "archived" } },
        ThreadAction("restore", "Restore").takeIf { selected.any { it.group == "archived" } },
        ThreadAction("delete", "Delete", danger = true).takeIf { selected.isNotEmpty() },
    )
}
