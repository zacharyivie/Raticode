package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

/** The phone's file explorer: what each file opens with, what the desktop's answers say, and the cached copies. */
class FileExplorerTest {
    @Test fun everyTypeTheDesktopOpensHasAViewer() {
        // The desktop's mediaKind.js pictures, videos and sound, plus phone camera formats.
        for (name in listOf("a.avif", "a.bmp", "a.gif", "a.ico", "a.jpeg", "A.JPG", "a.png", "a.webp", "a.heic"))
            assertEquals(name, FileViewer.IMAGE, FileExplorer.viewer(name))
        for (name in listOf("a.mp4", "a.m4v", "a.mov", "a.webm", "a.ogv", "a.mkv", "a.avi"))
            assertEquals(name, FileViewer.VIDEO, FileExplorer.viewer(name))
        for (name in listOf("a.mp3", "a.m4a", "a.aac", "a.wav", "a.ogg", "a.oga", "a.opus", "a.flac", "a.aif", "a.aiff"))
            assertEquals(name, FileViewer.AUDIO, FileExplorer.viewer(name))
        assertEquals(FileViewer.PDF, FileExplorer.viewer("report.pdf"))
        assertEquals(FileViewer.SVG, FileExplorer.viewer("logo.svg"))
        assertEquals(FileViewer.HTML, FileExplorer.viewer("index.HTML"))
        assertEquals(FileViewer.HTML, FileExplorer.viewer("page.htm"))
        for (name in listOf("README.md", "notes.markdown", "a.mdown", "a.mkd")) assertEquals(name, FileViewer.MARKDOWN, FileExplorer.viewer(name))
        // Everything else opens as text, as in the desktop editor, including names without an extension.
        for (name in listOf("main.kt", "flow.rattish", "data.csv", "nb.ipynb", "Dockerfile", "Makefile", ".env", ".gitignore", "server.log", "x.unknown"))
            assertEquals(name, FileViewer.TEXT, FileExplorer.viewer(name))
        // Known binary formats go straight to another app instead of a text read that would say "not text".
        for (name in listOf("deck.pptx", "book.xlsx", "a.zip", "font.woff2", "app.apk", "photo.tiff", "model.onnx"))
            assertEquals(name, FileViewer.OTHER, FileExplorer.viewer(name))
        assertTrue(FileExplorer.readsText(FileViewer.MARKDOWN) && FileExplorer.readsText(FileViewer.SVG) && !FileExplorer.readsText(FileViewer.PDF))
    }

    @Test fun codeWithoutATypeOpensInTheAppFirst() {
        assertTrue(FileExplorer.inAppFirst("main.kt", "application/octet-stream"))
        assertTrue(FileExplorer.inAppFirst("Dockerfile", "application/octet-stream"))
        // Files with a type of their own go to the phone's apps first, as before.
        assertFalse(FileExplorer.inAppFirst("notes.txt", "text/plain"))
        assertFalse(FileExplorer.inAppFirst("README.md", "application/octet-stream"))
        assertFalse(FileExplorer.inAppFirst("a.zip", "application/octet-stream"))
    }

    @Test fun codeIsHighlightedLikeTheDesktopEditor() {
        val expected = mapOf("app.tsx" to "ts", "a.mjs" to "js", "a.py" to "py", "a.kts" to "kt", "a.rs" to "rust", "a.go" to "go",
            "a.zsh" to "sh", "a.jsonc" to "json", "a.toml" to "yaml", "a.xml" to "html", "a.svg" to "html", "a.cpp" to "c", "a.cs" to "cs",
            "a.rb" to "rb", "a.php" to "php", "a.swift" to "swift", "flow.rattish" to "yaml", "Dockerfile" to "sh", "a.scss" to "css")
        for ((name, language) in expected) {
            assertEquals(name, language, FileExplorer.language(name))
            assertTrue(name, CodeHighlight.supports(language))
        }
        assertNull(FileExplorer.language("notes.txt"))
        assertEquals(CodeHighlight.Kind.KEYWORD, CodeHighlight.tokenize("def x", "rb").first().kind)
        assertEquals(CodeHighlight.Kind.COMMENT, CodeHighlight.tokenize("# note", "rb").first().kind)
    }

    @Test fun listingsAreReadDefensively() {
        val folder = FileExplorer.parseView(FileExplorer.listKey("p1", "docs"), JSONObject().put("project_id", "p1").put("project", "Notes")
            .put("path", "docs").put("more", 3).put("hidden", 1).put("entries", JSONArray()
                .put(JSONObject().put("name", "deep").put("type", "folder").put("modified", "2026-11-06T10:00:00Z"))
                .put(JSONObject().put("name", "a.md").put("type", "file").put("size", 12).put("modified", "2026-11-06T10:00:00Z"))
                .put(JSONObject().put("name", ".env").put("type", "file").put("size", 5).put("locked", true))
                // Names that could reach another folder are dropped.
                .put(JSONObject().put("name", "../up").put("type", "file"))
                .put(JSONObject().put("name", "..").put("type", "folder"))
                .put(JSONObject().put("name", "neg").put("type", "file").put("size", -4)))) as ExplorerFolder
        assertEquals(listOf("deep", "a.md", ".env", "neg"), folder.entries.map { it.name })
        assertTrue(folder.entries[0].folder && folder.entries[2].locked && folder.entries[3].size == null)
        assertEquals(12L, folder.entries[1].size)
        assertEquals(3 to 1, folder.more to folder.hidden)
        val projects = FileExplorer.parseView(FileExplorer.LIST, JSONObject().put("projects", JSONArray()
            .put(JSONObject().put("project_id", "p1").put("name", "Notes")).put(JSONObject().put("name", "No ID"))).put("more", 0)) as ExplorerProjects
        assertEquals(listOf(ExplorerProject("p1", "Notes")), projects.projects)
        val text = FileExplorer.parseView(FileExplorer.textKey("p1", "a.md"), JSONObject().put("path", "a.md").put("name", "a.md").put("size", 4)
            .put("text", "# Hi").put("truncated", false).put("binary", false)) as ExplorerText
        assertEquals("# Hi", text.text)
        // The desktop's view keys and the phone's agree, and every op is one the phone may send or receive.
        val params = FileExplorer.listParams("p1", "docs")
        assertEquals(FileExplorer.listKey("p1", "docs"), DesktopViews.key(FileExplorer.LIST, params))
        assertEquals(FileExplorer.LIST, DesktopViews.key(FileExplorer.LIST, FileExplorer.listParams(null)))
        assertTrue(DesktopViews.parse(FileExplorer.listKey("p1", "docs"), JSONObject().put("entries", JSONArray())) is ExplorerFolder)
        assertTrue(setOf("files.list", "files.text") == FileExplorer.READS && FileExplorer.READS.all { it in DesktopViews.READS })
        assertTrue(FileExplorer.OPEN in DesktopViews.ACTIONS && FileExplorer.OPEN !in DesktopViews.READS)
        assertTrue(DesktopViews.parse("overview", JSONObject().put("file_explorer", true)).let { (it as DesktopOverview).fileExplorer })
    }

    @Test fun pathsCrumbsAndFilter() {
        assertEquals("a", FileExplorer.child("", "a"))
        assertEquals("a/b c", FileExplorer.child("a", "b c"))
        assertEquals("a", FileExplorer.parent("a/b"))
        assertEquals("", FileExplorer.parent("a"))
        assertEquals(listOf("" to "Notes", "docs" to "docs", "docs/deep" to "deep"), FileExplorer.crumbs("docs/deep", "Notes"))
        assertEquals(listOf("" to "Notes"), FileExplorer.crumbs("", "Notes"))
        val entries = listOf("Build.gradle", "README.md", "readme-old.txt", "src").map { ExplorerEntry(it, "file", 1, "", false) }
        assertEquals(listOf("README.md", "readme-old.txt"), FileExplorer.filter(entries, " READ ").map { it.name })
        assertEquals(listOf("readme-old.txt"), FileExplorer.filter(entries, "read old").map { it.name })
        assertEquals(entries, FileExplorer.filter(entries, "  "))
    }

    @Test fun sizesAndTimesReadNaturally() {
        assertEquals("512 B", FileExplorer.size(512))
        assertEquals("2 KB", FileExplorer.size(2048))
        assertEquals("4.2 MB", FileExplorer.size(4_400_000))
        assertEquals("1.5 GB", FileExplorer.size(1_610_612_736))
        val utc = java.util.TimeZone.getTimeZone("UTC")
        val now = java.time.Instant.parse("2026-11-06T18:00:00Z").toEpochMilli()
        assertEquals("Today 09:05", FileExplorer.modified("2026-11-06T09:05:00Z", now, utc))
        assertEquals("Yesterday", FileExplorer.modified("2026-11-05T23:00:00Z", now, utc))
        assertEquals("Nov 3", FileExplorer.modified("2026-11-03T08:00:00Z", now, utc))
        assertEquals("Dec 24, 2025", FileExplorer.modified("2025-12-24T08:00:00Z", now, utc))
        assertEquals("", FileExplorer.modified("not a time", now, utc))
    }

    private fun state(vararg files: Triple<String, String, Long>) = JSONObject().put("files", JSONObject().apply {
        for ((id, fileState, size) in files) put(id, JSONObject().put("file_id", id).put("direction", "download").put("state", fileState)
            .put("size", size).put("thread", "t"))
    })

    @Test fun anUnchangedCopyOpensAgainEvenOffline() {
        val state = state(Triple("f1", "available", 10), Triple("f2", "failed", 10))
        FileExplorer.remember(state, "p1", "a.png", "f1", 10, "2026-11-06T10:00:00Z")
        assertEquals("f1", FileExplorer.copy(state, "p1", "a.png", 10, "2026-11-06T10:00:00Z"))
        // A file that changed on the computer is fetched again.
        assertNull(FileExplorer.copy(state, "p1", "a.png", 11, "2026-11-06T10:00:00Z"))
        assertNull(FileExplorer.copy(state, "p1", "a.png", 10, "2026-11-06T11:00:00Z"))
        assertNull(FileExplorer.copy(state, "p2", "a.png", 10, "2026-11-06T10:00:00Z"))
        // A failed download is no copy.
        FileExplorer.remember(state, "p1", "b.png", "f2", 10, "")
        assertNull(FileExplorer.copy(state, "p1", "b.png", 10, ""))
        // Entries whose download is gone are forgotten when the next copy is recorded.
        FileExplorer.remember(state, "p1", "c.png", "missing", 1, "")
        FileExplorer.remember(state, "p1", "d.png", "f1", 10, "")
        assertFalse(state.getJSONObject(FileExplorer.STATE).has("p1\nc.png"))
        assertEquals(listOf("f1", "f2", "f1"), FileExplorer.files(state))
        FileExplorer.forget(state, listOf("f1"))
        assertEquals(listOf("f2"), FileExplorer.files(state))
    }

    @Test fun explorerCopiesMakeRoomBeforeAttachments() {
        val big = 15L * 1024 * 1024
        val state = state(Triple("old-file", "available", big), Triple("attachment", "available", big), Triple("new-file", "available", big))
        FileExplorer.remember(state, "p", "old.mp4", "old-file", big, "")
        FileExplorer.remember(state, "p", "new.mp4", "new-file", big, "")
        RemAttachments.remember(state, "a1", "attachment")
        // 45 MB held; 10 more needs one copy gone, and the oldest explorer copy goes first.
        assertEquals(listOf("old-file"), RemAttachments.evictions(state, 10L * 1024 * 1024, FileExplorer.files(state)))
        assertEquals(listOf("old-file", "new-file"), RemAttachments.evictions(state, 25L * 1024 * 1024, FileExplorer.files(state)))
        assertEquals(listOf("old-file", "new-file", "attachment"), RemAttachments.evictions(state, 39L * 1024 * 1024, FileExplorer.files(state)))
        assertNull(RemAttachments.evictions(state, 41L * 1024 * 1024, FileExplorer.files(state)))
        // Without an attachment map the explorer copies can still make room.
        val plain = state(Triple("x", "available", big), Triple("y", "available", big), Triple("z", "available", big))
        FileExplorer.remember(plain, "p", "x", "x", big, "")
        assertEquals(listOf("x"), RemAttachments.evictions(plain, 1L * 1024 * 1024, FileExplorer.files(plain)))
    }

    @Test fun queuedExplorerRequestsAreDroppedBeforeReconnecting() {
        fun event(type: String, op: String) = JSONObject().put("type", type).put("payload", JSONObject().put("op", op)).toString()
        val state = JSONObject().put("outbox", JSONArray().put(event("desktop.read", "files.list")).put(event("desktop.action", "files.open"))
            .put(event("desktop.read", "files.text")).put(event("desktop.action", "files.link")).put(event("message.send", "files.open")))
        assertTrue(FileExplorer.dropQueued(state))
        val left = state.getJSONArray("outbox")
        assertEquals(listOf("files.link", "files.open"), (0 until left.length()).map { JSONObject(left.getString(it)).getJSONObject("payload").getString("op") })
        assertFalse(FileExplorer.dropQueued(state))
    }
}
