package dev.raticode.mobile.live

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.util.UUID

/** Links in desktop messages: which open the browser, which ask the desktop for a file, and which do nothing. */
class ChatLinksTest {
    @Test fun webAddressesOpenInTheBrowser() {
        assertEquals(ChatLink.Web("https://example.com/a?b=1#c"), ChatLinks.classify("https://example.com/a?b=1#c"))
        assertEquals(ChatLink.Web("http://example.com"), ChatLinks.classify(" http://example.com "))
        // No host, or a user name that can disguise the real site.
        assertNull(ChatLinks.classify("https://"))
        assertNull(ChatLinks.classify("https://me@example.com/"))
    }

    @Test fun fileLinksKeepTheMessagesTextForTheDesktopToResolve() {
        for (link in listOf("/home/doonk/Projects/second-brain/reports/a b.html", "file:///home/doonk/x.html", "src/a.py:12",
            "a.py:12:3", "~/notes/todo.md", "../README.md", "C:\\Users\\me\\x.txt", "reports/plan.html?x#y")) {
            val found = ChatLinks.classify(link)
            assertTrue(link, found is ChatLink.File)
            assertEquals(link, (found as ChatLink.File).destination)
        }
        assertEquals("a b.html", ChatLinks.fileName("/home/doonk/reports/a%20b.html"))
        assertEquals("a.py", ChatLinks.fileName("src/a.py:12"))
        assertEquals("x.html", ChatLinks.fileName("file:///home/doonk/x.html"))
        assertEquals("c++.md", ChatLinks.fileName("/notes/c++.md"))
        assertEquals("File", ChatLinks.fileName("/"))
    }

    @Test fun otherSchemesAndAnchorsAreNotActedOn() {
        for (link in listOf("javascript:alert(1)", "intent://x#Intent;end", "mailto:a@example.com", "tel:123", "content://x/y",
            "data:text/html,hi", "#section", "//example.com/x", "", "https://example.com/\u0000", "/x".repeat(1025))) {
            assertNull(link, ChatLinks.classify(link))
        }
    }

    @Test fun plainTextAddressesAndPathsAreLinked() {
        val text = "See https://example.com/a_(b). Report: /home/doonk/Projects/second-brain/reports/x.html, and src/a.py or /etc."
        val found = ChatLinks.bare(text).map { (range, link) -> text.substring(range.first, range.last + 1) to link }
        assertEquals(listOf(
            "https://example.com/a_(b)" to ChatLink.Web("https://example.com/a_(b)"),
            "/home/doonk/Projects/second-brain/reports/x.html" to ChatLink.File("/home/doonk/Projects/second-brain/reports/x.html", "x.html"),
        ), found)
        // A path inside an address is part of the address only.
        assertEquals(1, ChatLinks.bare("https://example.com/docs/a.html").size)
        assertEquals(listOf("/srv/app/main.kt:40"), ChatLinks.bare("at /srv/app/main.kt:40.").map { (r, _) -> "at /srv/app/main.kt:40.".substring(r.first, r.last + 1) })
    }

    @Test fun linkSourcesNameOneDesktopMessage() {
        val thread = UUID.randomUUID().toString()
        assertNotNull(LinkSource.of("rem", null, "m1"))
        assertNotNull(LinkSource.of("thread", thread, "m1"))
        assertNotNull(LinkSource.of("rem_thread", thread, "m1"))
        assertNull(LinkSource.of("rem", thread, "m1"))
        assertNull(LinkSource.of("thread", null, "m1"))
        assertNull(LinkSource.of("files", thread, "m1"))
        assertNull(LinkSource.of("thread", thread, ""))
        assertNotNull(LinkSource.of("thread", thread, "m".repeat(200)))
        assertNull(LinkSource.of("thread", thread, "m".repeat(201)))
    }

    @Test fun fetchResultsAreReadAndAdvertised() {
        val params = JSONObject().put("source", "rem").put("message_id", "m1").put("href", "/x/a.md")
        assertEquals(ChatLinks.KEY + "/x/a.md", DesktopViews.key("files.link", params))
        assertTrue("files.link" in DesktopViews.ACTIONS)
        val overview = DesktopViews.parse("overview", JSONObject().put("file_links", true)) as DesktopOverview
        assertTrue(overview.fileLinks)
        assertFalse((DesktopViews.parse("overview", JSONObject()) as DesktopOverview).fileLinks)
        assertTrue((DesktopViews.parse("rem.home", JSONObject().put("file_links", true)) as RemHome).fileLinks)
        IncomingValidation.validate(JSONObject().put("type", "desktop.result").put("request_id", UUID.randomUUID().toString())
            .put("thread_id", JSONObject.NULL).put("payload", JSONObject().put("op", "files.link").put("ok", false).put("part", 0).put("parts", 1)
                .put("body", "").put("code", "not_found").put("detail", "That file isn't in this message.")))
    }

    @Test fun desktopMentionsReachTheMessageBounded() {
        val view = DesktopViews.parse("rem.home", JSONObject().put("messages", org.json.JSONArray()
            .put(JSONObject().put("id", "r").put("role", "rem").put("body", "x")
                .put("file_mentions", org.json.JSONArray(listOf("/a/b.txt", "/a/b c d.txt", "relative/x.txt", "/x\u0000y", "/a/b.txt"))))
            .put(JSONObject().put("id", "u").put("role", "user").put("body", "x").put("file_mentions", org.json.JSONArray(listOf("/a/b.txt")))))) as RemHome
        // Longest first, only full paths, no duplicates, and nothing on the user's own message.
        assertEquals(listOf("/a/b c d.txt", "/a/b.txt"), view.messages[0].fileMentions)
        assertEquals(emptyList<String>(), view.messages[1].fileMentions)
        assertEquals(ChatLink.File("/a/b c #2?.txt", "b c #2?.txt"), ChatLinks.mention("/a/b c #2?.txt"))
    }
}
