package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * `desktop-source-history.json` holds the desktop's real `source_control.history` answer for a scratch repository with
 * three branches, two merges, a merge back into a branch, a tag and an unrelated root, and the rows the desktop's own
 * `layoutHistoryGraph` and `filterHistory` (Node) drew for it. The phone must draw the same lanes.
 */
class HistoryGraphTest {
    private val fixture = JSONObject(javaClass.classLoader!!.getResource("desktop-source-history.json")!!.readText())
    private val history = SourceControl.parseHistory(fixture.getJSONObject("commits"))

    private fun shape(layout: HistoryGraph.Layout) = layout.rows.map { row ->
        listOf(row.commit.hash, row.lane, row.color, row.segments.map { listOf(it.from, it.to, it.color, it.full, it.top) })
    }

    private fun expected(key: String) = fixture.getJSONArray(key).let { rows ->
        (0 until rows.length()).map { rows.getJSONObject(it) }.map { row ->
            val segments = row.getJSONArray("segments")
            listOf(row.getString("hash"), row.getInt("lane"), row.getInt("color"), (0 until segments.length()).map { segments.getJSONObject(it) }.map {
                listOf(it.getInt("from"), it.getInt("to"), it.getInt("color"), it.getBoolean("full"), it.getBoolean("top"))
            })
        }
    }

    @Test fun lanesMatchTheDesktopGraph() {
        val layout = HistoryGraph.layout(history.commits)
        assertEquals(expected("expected"), shape(layout))
        // The desktop's width is (lanes - 1) * 16 + 2 * 8 pixels.
        assertEquals(fixture.getInt("width"), (layout.lanes - 1) * 16 + 16)
        assertTrue(layout.rows.any { it.segments.size > 2 })
    }

    @Test fun searchBridgesHiddenCommitsLikeTheDesktop() {
        val filtered = HistoryGraph.filter(history.commits, fixture.getString("filter"))
        val wanted = fixture.getJSONArray("filtered")
        assertEquals((0 until wanted.length()).map { wanted.getJSONObject(it) }.map { it.getString("hash") to it.getJSONArray("parents").strings() },
            filtered.map { it.hash to it.parents })
        assertEquals(expected("expectedFiltered"), shape(HistoryGraph.layout(filtered)))
        assertEquals(history.commits, HistoryGraph.filter(history.commits, "   "))
    }

    @Test fun historyParsesRefsLabelsAndCounts() {
        val merge = history.commits.first { it.subject == "Merge feature/b" }
        assertEquals(2, merge.parents.size)
        assertTrue("refs/tags/v2" in merge.labels)
        val main = history.refs.first { it.id == "refs/heads/main" }
        assertTrue(main.current && main.type == "local")
        assertEquals("tag", history.refs.first { it.id == "refs/tags/v2" }.type)
        assertTrue(history.commits.first { it.subject == "Add after" }.isHead)
        assertEquals(1, history.commits.first { it.subject == "Add c1" }.insertions)
        assertFalse(history.hasMore)
    }

    @Test fun keysAndViewsForEachRead() {
        val params = JSONObject().put("project_id", "p").put("source", "commit").put("hash", "abc1234").put("path", "src/a.kt")
        assertEquals("source_control.diff:p:commit:abc1234:src/a.kt", DesktopViews.key(SourceControl.DIFF, params))
        assertEquals("source_control.history:p", DesktopViews.key(SourceControl.HISTORY, JSONObject().put("project_id", "p").put("limit", 200)))
        assertEquals("source_control.commit:p:abc1234", DesktopViews.key(SourceControl.COMMIT, params))
        val diff = DesktopViews.parse("source_control.diff:p:commit:abc1234:src/a.kt",
            JSONObject().put("path", "src/a.kt").put("source", "commit").put("text", "@@ -1 +1 @@\n-a\n+b\n").put("truncated", false)) as SourceDiff
        assertEquals("src/a.kt", diff.path)
        val commit = DesktopViews.parse("source_control.commit:p:abc1234", JSONObject().put("hash", "abc1234").put("subject", "Fix")
            .put("files", JSONArray().put(JSONObject().put("path", "a.kt").put("status", "modified").put("insertions", 2)))) as SourceCommitDetail
        assertEquals(listOf("a.kt"), commit.files.map { it.path })
        assertEquals(2, commit.files.single().insertions)
        assertTrue(SourceControl.HISTORY in DesktopViews.READS && SourceControl.DIFF in DesktopViews.READS)
        assertTrue((DesktopViews.parse("overview", JSONObject().put("source_control_history", true)) as DesktopOverview).sourceControlHistory)
    }

    @Test fun pathsBranchesAndBadges() {
        assertEquals("App.kt" to "src/main", SourceControl.splitPath("src/main/App.kt"))
        assertEquals("README.md" to "", SourceControl.splitPath("README.md"))
        assertEquals("M", SourceControl.statusLetter("modified"))
        assertEquals("!", SourceControl.statusLetter("conflict"))
        fun branch(upstream: String, ahead: Int = 0, behind: Int = 0, gone: Boolean = false) = SourceBranch("b", false, false, upstream, ahead, behind, gone)
        assertEquals("Local only", SourceControl.trackingText(branch("")))
        assertEquals("Up to date with origin/b", SourceControl.trackingText(branch("origin/b")))
        assertEquals("2 to push, 1 to pull · origin/b", SourceControl.trackingText(branch("origin/b", 2, 1)))
        assertEquals("Upstream origin/b is missing", SourceControl.trackingText(branch("origin/b", gone = true)))
    }

    private fun commit(json: String) = SourceControl.parseHistory(JSONObject().put("commits", JSONArray().put(JSONObject(json)))).commits.single()

    /** The desktop's `commit line counts describe exactly what git reported` test (gitHistoryGraph.test.mjs), case for case. */
    @Test fun lineCountsDescribeExactlyWhatGitReported() {
        assertEquals(CommitStats(17, 3, 1, true, "17 insertions, 3 deletions, 1 binary file without line counts"),
            SourceControl.commitStats(commit("""{"insertions":17,"deletions":3,"binary_files":1,"parents":["a"]}""")))
        assertEquals("1 insertion, 1 deletion", SourceControl.commitStats(commit("""{"insertions":1,"deletions":1,"binary_files":0}"""))?.label)
        assertTrue("A commit without changed lines still shows +0 −0", SourceControl.commitStats(commit("""{"insertions":0,"deletions":0}"""))!!.lines)
        val binaryOnly = SourceControl.commitStats(commit("""{"insertions":0,"deletions":0,"binary_files":2}"""))!!
        assertFalse("Binary-only commits must not claim zero changed lines", binaryOnly.lines)
        assertEquals("0 insertions, 0 deletions, 2 binary files without line counts", binaryOnly.label)
        assertTrue(SourceControl.commitStats(commit("""{"insertions":4,"deletions":2,"parents":["a","b"]}"""))!!.label.endsWith("compared with the first parent"))
        for (missing in listOf("{}", """{"insertions":5}""", """{"insertions":5,"deletions":null}""", """{"insertions":"5","deletions":1}""",
                """{"insertions":-1,"deletions":0}""", """{"insertions":1.5,"deletions":0}""", """{"insertions":99999999999,"deletions":0}""")) {
            assertEquals("Missing or invalid counts must show nothing rather than +0: $missing", null, SourceControl.commitStats(commit(missing)))
        }
    }

    @Test fun everyRealDesktopCommitHasCounts() {
        // The desktop's own answer always carries counts, so every row in the fixture shows them, merges included.
        assertTrue(history.commits.all { SourceControl.commitStats(it) != null })
        val merge = history.commits.first { it.subject == "Merge feature/b" }
        assertTrue(SourceControl.commitStats(merge)!!.label.endsWith("compared with the first parent"))
    }

    private fun JSONArray.strings() = (0 until length()).map { getString(it) }
}
