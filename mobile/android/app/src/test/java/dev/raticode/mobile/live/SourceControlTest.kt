package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SourceControlTest {
    private val listBody = JSONObject("""{"projects":[
        {"project_id":"p1","name":"Raticode","state":"ok","branch":"dev","detached":false,"head":"0123456789ab","upstream":"origin/dev",
         "ahead":2,"behind":0,"changes":{"total":3,"staged":1,"unstaged":1,"untracked":1,"conflicts":0},"operation":"",
         "fetched_at":"2026-11-04T10:00:00+00:00","last_commit":{"hash":"0123456789ab","subject":"Add bar","author":"Ada","at":"2026-11-04T09:00:00+00:00"},
         "subfolder":""},
        {"project_id":"p2","name":"Notes","state":"not_repository","detail":"This project isn't a Git repository."},
        {"project_id":"","name":"No ID","state":"ok"}
    ],"more":1,"digest":"abc"}""")

    @Test fun listParsesSummariesAndDropsEntriesWithoutAnId() {
        val list = DesktopViews.parse(SourceControl.LIST, listBody) as SourceProjectList
        assertEquals(listOf("p1", "p2"), list.projects.map { it.id })
        assertEquals(1, list.more)
        val first = list.projects[0]
        assertEquals(SourceChanges(3, 1, 1, 1, 0), first.changes)
        assertEquals("Add bar", first.lastCommit?.subject)
        assertFalse(first.detailed)
        assertEquals("3 changed files", SourceControl.headline(first))
        assertEquals("2 to push", SourceControl.syncText(first))
        assertEquals("dev", SourceControl.branchText(first))
        assertEquals("Not a Git repository", SourceControl.headline(list.projects[1]))
        assertEquals("", SourceControl.syncText(list.projects[1]))
    }

    @Test fun readParsesFilesHistoryAndWorktrees() {
        val body = JSONObject(listBody.getJSONArray("projects").getJSONObject(0).toString())
            .put("files", JSONArray("""[{"path":"b.kt","status":"modified","staged":false,"unstaged":true},
                {"path":"a.kt","status":"conflict","staged":false,"unstaged":true},
                {"path":"c.kt","status":"added","staged":true,"unstaged":false}]"""))
            .put("more_files", 4).put("stashes", 1)
            .put("commits", JSONArray("""[{"hash":"0123456789ab","subject":"Add bar","author":"Ada","at":"2026-11-04T09:00:00+00:00"}]"""))
            .put("worktrees", JSONArray("""[{"name":"raticode","branch":"dev","current":true,"changes":3},
                {"name":".worktrees/x","branch":"x","current":false,"changes":null,"missing":true}]"""))
        assertEquals("source_control.read:p1", DesktopViews.key(SourceControl.READ, JSONObject().put("project_id", "p1")))
        val project = DesktopViews.parse("source_control.read:p1", body) as SourceProject
        assertTrue(project.detailed)
        assertEquals(4, project.moreFiles)
        assertEquals(listOf("Conflicts", "Staged", "Changes"), SourceControl.groups(project.files).map { it.first })
        assertEquals(listOf("a.kt"), SourceControl.groups(project.files)[0].second.map { it.path })
        assertEquals(3, project.worktrees[0].changes)
        assertNull(project.worktrees[1].changes)
        assertTrue(project.worktrees[1].missing)
    }

    @Test fun branchStatesReadAsWords() {
        val ok = SourceControl.parse(JSONObject("""{"project_id":"p","state":"ok","branch":"main","upstream":"origin/main","ahead":0,"behind":0}"""))
        assertEquals("Up to date with origin/main", SourceControl.syncText(ok))
        assertFalse(SourceControl.attention(ok))
        val behind = ok.copy(ahead = 1, behind = 3)
        assertEquals("1 to push, 3 to pull", SourceControl.syncText(behind))
        assertTrue(SourceControl.attention(behind))
        assertEquals("No upstream branch", SourceControl.syncText(ok.copy(upstream = "", ahead = null, behind = null)))
        val detached = ok.copy(branch = "", detached = true, head = "abcdef123456", upstream = "")
        assertEquals("Detached at abcdef1", SourceControl.branchText(detached))
        assertEquals("Detached HEAD", SourceControl.syncText(detached))
        assertEquals("No commits yet", SourceControl.branchText(ok.copy(branch = "", detached = false, head = "")))
        val merging = ok.copy(operation = "merge", changes = SourceChanges(1, 0, 1, 0, 1))
        assertEquals("Merge in progress", SourceControl.headline(merging))
        assertEquals("1 conflict", SourceControl.headline(merging.copy(operation = "")))
        assertTrue(SourceControl.attention(merging))
        assertEquals("No uncommitted changes", SourceControl.headline(ok))
        assertEquals("Status unavailable", SourceControl.headline(ok.copy(state = "unavailable")))
        assertEquals("Folder missing", SourceControl.headline(ok.copy(state = "missing")))
    }

    /** Answers written by the desktop's own source_control.py for a scratch repository (see the file's keys). */
    private val desktop = JSONObject(javaClass.classLoader!!.getResource("desktop-source-control.json")!!.readText())

    @Test fun realDesktopAnswersParse() {
        val list = DesktopViews.parse(SourceControl.LIST, desktop.getJSONObject("list")) as SourceProjectList
        val (repo, plain) = list.projects
        assertEquals("Raticode", repo.name)
        assertEquals("dev", SourceControl.branchText(repo))
        assertEquals("1 to push", SourceControl.syncText(repo))
        assertEquals(SourceChanges(3, 1, 1, 1, 0), repo.changes)
        assertEquals("Add bar settings", repo.lastCommit?.subject)
        assertEquals("Not a Git repository", SourceControl.headline(plain))
        assertEquals("This project isn't a Git repository.", plain.detail)
        val read = DesktopViews.parse(SourceControl.key("p1"), desktop.getJSONObject("read")) as SourceProject
        assertTrue(read.detailed)
        assertEquals(listOf("Staged", "Changes"), SourceControl.groups(read.files).map { it.first })
        assertEquals(listOf("Add bar settings", "First commit"), read.commits.map { it.subject })
        assertEquals(listOf(".worktrees/feature" to 0, "raticode" to 3), read.worktrees.map { it.name to it.changes }.sortedBy { it.first })
        val notRepo = DesktopViews.parse(SourceControl.key("p2"), desktop.getJSONObject("read.not_repository")) as SourceProject
        assertEquals("not_repository", notRepo.state)
        assertFalse(notRepo.detailed)
    }

    @Test fun projectsOpenToHistoryUnlessTheDesktopCantSendIt() {
        assertEquals("history", SourceControl.openingTab(true))
        assertEquals("history", SourceControl.openingTab(null))
        assertEquals("changes", SourceControl.openingTab(false))
    }

    @Test fun overviewSaysWhetherTheDesktopAnswersGitStatus() {
        assertTrue((DesktopViews.parse("overview", JSONObject().put("source_control", true)) as DesktopOverview).sourceControl)
        assertFalse((DesktopViews.parse("overview", JSONObject()) as DesktopOverview).sourceControl)
        assertTrue(SourceControl.LIST in DesktopViews.READS && SourceControl.READ in DesktopViews.READS)
        assertFalse(SourceControl.LIST in DesktopViews.ACTIONS)
    }

    @Test fun aDiffSplitsIntoHunksWithTheirLines() {
        val lines = SourceControl.diffLines("diff --git a/a b/a\nindex 1..2\n--- a/a\n+++ b/a\n@@ -1,2 +1,2 @@ first\n a\n-b\n+c\n@@ -9 +9,2 @@\n d\n+e\n\\ No newline at end of file\n")
        assertEquals(listOf('@', ' ', '-', '+', '@', ' ', '+', '\\'), lines.map { it.kind })
        assertEquals(listOf(null, 1, 2, 2, null, 9, 10, null), lines.map { it.number })
        val hunks = SourceControl.hunks(lines)
        assertEquals(setOf(0, 4), hunks.keys)
        assertEquals("@@ -1,2 +1,2 @@ first", hunks[0]!!.header)
        assertEquals(listOf("a", "b", "c"), hunks[0]!!.lines.map { it.text })
        assertEquals(listOf("d", "e", "No newline at end of file"), hunks[4]!!.lines.map { it.text })
        assertEquals(emptyMap<Int, DiffHunk>(), SourceControl.hunks(SourceControl.diffLines("Binary files differ")))
    }

    @Test fun queuedStatusReadsAreDroppedBeforeReconnecting() {
        fun event(type: String, op: String) = JSONObject().put("type", type).put("payload", JSONObject().put("op", op).put("params", JSONObject())).toString()
        val state = JSONObject().put("outbox", JSONArray().put(event("desktop.read", "rem.home")).put(event("desktop.read", SourceControl.LIST))
            .put(event("desktop.read", SourceControl.READ)).put(JSONObject().put("type", "sync.request").put("payload", JSONObject()).toString()))
        assertTrue(SourceControl.dropQueuedReads(state))
        val left = (0 until state.getJSONArray("outbox").length()).map { JSONObject(state.getJSONArray("outbox").getString(it)) }
        assertEquals(listOf("rem.home", ""), left.map { it.getJSONObject("payload").optString("op") })
        assertFalse(SourceControl.dropQueuedReads(state))
    }
}
