package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Searching Rem's chat: the desktop's answers, the phone's own search offline, and earlier pages joining the chat. */
class RemSearchTest {
    private fun message(id: String, body: String, role: String = "rem", state: String = "", work: RemWork? = null, files: List<RemFile> = emptyList()) =
        RemMessage(id, role, body, "2026-11-10T15:00:00+00:00", work, state, attachments = files)

    private fun home(vararg ids: String, earlier: Int = 10, search: Boolean = true) =
        RemHome("idle", "", "", earlier, ids.map { message(it, "Message $it") }, emptyList(), search = search)

    private fun marked(parts: List<RemSearchPart>) = parts.filter { it.match }.map { it.text }
    private fun text(parts: List<RemSearchPart>) = parts.joinToString("") { it.text }

    @Test fun theDesktopsResultsAndFlagAreRead() {
        val page = DesktopViews.parse(RemSearch.SEARCH, JSONObject().put("query", "release notes").put("total", 7).put("next", 812)
            .put("results", JSONArray().put(JSONObject().put("id", "m1").put("role", "user").put("created_at", "2026-11-01T10:00:00Z").put("where", "message")
                .put("snippet", JSONArray().put(JSONObject().put("text", "Draft the ").put("match", false)).put(JSONObject().put("text", "release").put("match", true))))
                .put(JSONObject().put("id", "m2").put("role", "rem").put("kind", "task_update").put("where", "task")
                    .put("title", JSONArray().put(JSONObject().put("text", "Notes").put("match", true))).put("snippet", JSONArray())))) as RemSearchPage
        assertEquals(7, page.total)
        assertEquals(812L, page.next)
        assertFalse(page.local)
        assertEquals(listOf("release"), marked(page.results[0].snippet))
        assertEquals("You" to "", RemSearch.label(page.results[0]))
        assertEquals("Task update" to "In the task title", RemSearch.label(page.results[1]))
        assertNull((DesktopViews.parse(RemSearch.SEARCH, JSONObject().put("total", 0).put("next", JSONObject.NULL)) as RemSearchPage).next)
        assertTrue((DesktopViews.parse("rem.home", JSONObject().put("search", true)) as RemHome).search)
        // An older desktop says nothing, so the phone searches only what it holds.
        assertFalse((DesktopViews.parse("rem.home", JSONObject()) as RemHome).search)
        assertTrue(RemSearch.SEARCH in DesktopViews.READS && RemSearch.HISTORY in DesktopViews.READS)
        assertEquals(RemSearch.SEARCH, DesktopViews.key(RemSearch.SEARCH, RemSearch.params("x", null)))
        assertEquals(RemSearch.HISTORY, DesktopViews.key(RemSearch.HISTORY, RemSearch.historyParams("a", "b")))
    }

    @Test fun anEarlierPageIsReadLikeTheChat() {
        val page = DesktopViews.parse(RemSearch.HISTORY, JSONObject().put("earlier", 3)
            .put("messages", JSONArray().put(JSONObject().put("id", "old").put("role", "user").put("body", "From last week")
                .put("attachments", JSONArray().put(JSONObject().put("file_id", "f").put("name", "a.csv").put("size", 3)))))
            .put("tickets", JSONObject())) as RemHistoryPage
        assertEquals(3, page.earlier)
        assertEquals("From last week", page.messages.single().body)
        assertEquals("a.csv", page.messages.single().attachments.single().name)
    }

    @Test fun requestsCarryOnlyTheWordsAndPositions() {
        assertEquals("""{"query":"release notes"}""", RemSearch.params("  release   notes Release ", null).toString())
        RemSearch.params("a", 12).let { assertEquals("a", it.getString("query")); assertEquals(12L, it.getLong("before")); assertEquals(2, it.length()) }
        RemSearch.historyParams("m40", "m3").let { assertEquals("m40", it.getString("before")); assertEquals("m3", it.getString("through")); assertEquals(2, it.length()) }
        assertEquals("""{"before":"m40"}""", RemSearch.historyParams("m40", null).toString())
        assertEquals(8, RemSearch.terms((1..20).joinToString(" ") { "w$it" }).size)
        assertEquals(200, RemSearch.terms("x".repeat(500)).single().length)
    }

    @Test fun offlineSearchFollowsTheDesktopsRules() {
        val messages = listOf(
            message("a", "Please draft the RELEASE notes", role = "user"),
            message("b", "See [the **deploy** guide](/x.md)"),
            message("c", "Done.", work = RemWork("t", "r", "h", "Audit the Kanban board")),
            message("d", "Attached", role = "user", files = listOf(RemFile("f", "Kanban-export.csv", 3))),
            message("e", "release notes waiting", role = "user", state = "waiting"),
        )
        val release = RemSearch.local(messages, "notes release")
        assertTrue(release.local)
        assertEquals(listOf("a"), release.results.map { it.id })
        assertEquals(listOf("RELEASE", "notes"), marked(release.results[0].snippet))
        assertEquals("the deploy guide", RemSearch.local(messages, "deploy guide").results.single().snippet.let(::text).removePrefix("See "))
        val kanban = RemSearch.local(messages, "kanban")
        assertEquals(listOf("d", "c"), kanban.results.map { it.id })
        assertEquals(listOf("file", "task"), kanban.results.map { it.where })
        assertEquals(listOf("Kanban"), marked(kanban.results[1].title))
        assertEquals(0, RemSearch.local(messages, "release lunch").total)
        assertEquals(0, RemSearch.local(messages, "   ").total)
        assertEquals(1, RemSearch.local(listOf(message("x", "Café ÉCOLE")), "école").total)
    }

    @Test fun longMessagesGetASnippetAroundTheirMatch() {
        val words = (0 until 200).joinToString(" ") { "word$it" }
        val snippet = RemSearch.local(listOf(message("m", "$words needle $words")), "NEEDLE").results.single().snippet
        val shown = text(snippet)
        assertTrue(shown.startsWith("…") && shown.endsWith("…") && shown.length <= 182)
        assertEquals(listOf("needle"), marked(snippet))
        assertTrue(shown.drop(1).startsWith("word"))
    }

    @Test fun summariesSayWhatTheSearchFound() {
        assertEquals("", RemSearch.summary(RemSearchState()))
        assertEquals("Searching…", RemSearch.summary(RemSearchState("x", RemSearchState.Status.LOADING)))
        assertEquals("No matches", RemSearch.summary(RemSearchState("x", RemSearchState.Status.READY, RemSearchPage("x", 0, emptyList()))))
        val one = RemSearchResult("m", "rem", "", "", "message", emptyList())
        assertEquals("1 match", RemSearch.summary(RemSearchState("x", RemSearchState.Status.READY, RemSearchPage("x", 1, listOf(one)))))
        assertEquals("1,204 matches, showing 1", RemSearch.summary(RemSearchState("x", RemSearchState.Status.READY, RemSearchPage("x", 1204, listOf(one), 5))))
    }

    @Test fun earlierPagesJoinTheChatAndFollowItsNewestWindow() {
        val earlier = listOf(message("m1", "One"), message("m2", "Two"))
        val first = home("m3", "m4", "m5")
        assertEquals(listOf("m1", "m2", "m3", "m4", "m5"), RemSearch.joined(earlier, first.messages).map { it.id })
        // A new message pushes m3 out of the newest window; it joins the earlier pages, so nothing goes missing.
        val next = home("m4", "m5", "m6")
        val kept = RemSearch.keepEarlier(earlier, first, next)
        assertEquals(listOf("m1", "m2", "m3"), kept.map { it.id })
        assertEquals(listOf("m1", "m2", "m3", "m4", "m5", "m6"), RemSearch.joined(kept, next.messages).map { it.id })
        // A message in both lists shows once.
        assertEquals(listOf("m1", "m2", "m3"), RemSearch.joined(listOf(message("m1", ""), message("m2", "")), home("m2", "m3").messages).map { it.id })
        // The window holds the whole chat, or it no longer overlaps what came before (history cleared): pages go.
        assertEquals(emptyList<RemMessage>(), RemSearch.keepEarlier(earlier, first, home("m4", "m5", earlier = 0)))
        assertEquals(emptyList<RemMessage>(), RemSearch.keepEarlier(earlier, first, home("n1", "n2")))
        assertEquals(emptyList<RemMessage>(), RemSearch.keepEarlier(earlier, first, home(earlier = 0)))
        assertEquals(earlier, RemSearch.keepEarlier(earlier, null, next))
    }
}
