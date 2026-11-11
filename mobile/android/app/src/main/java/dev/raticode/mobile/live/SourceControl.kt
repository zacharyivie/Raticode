package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject

/**
 * Read-only Git status of the desktop's open projects. The desktop runs Git and decides which projects the phone sees;
 * the phone only names a project by its catalog ID. Nothing here changes a repository.
 */
object SourceControl {
    const val LIST = "source_control.list"
    const val READ = "source_control.read"
    const val HISTORY = "source_control.history"
    const val COMMIT = "source_control.commit"
    const val DIFF = "source_control.diff"
    /** History, one commit and one file's diff need a desktop that reports `source_control_history`. */
    val LATER = setOf(HISTORY, COMMIT, DIFF)
    val READS = setOf(LIST, READ) + LATER
    const val UPDATE_FOR_HISTORY = "Update Raticode on your computer to see history, the branch graph and file changes here."
    /** Pages of history the phone asks for; the desktop allows up to 400 commits. */
    const val HISTORY_STEP = 100
    const val HISTORY_MAX = 400
    const val UPDATE_DESKTOP = "Update Raticode on your computer to check source control from this phone."

    fun isOp(op: String) = op.startsWith("source_control.")

    /**
     * The view a project opens to: History, like the desktop's Source control. A desktop too old to send history opens
     * to Changes instead, so the first thing shown isn't a request to update it. Unknown support counts as History.
     */
    fun openingTab(historySupported: Boolean?) = if (historySupported == false) "changes" else "history"

    /** The phone's view key for one project. */
    fun key(projectId: String) = "$READ:$projectId"
    fun historyKey(projectId: String) = "$HISTORY:$projectId"
    fun commitKey(projectId: String, hash: String) = "$COMMIT:$projectId:$hash"
    fun diffKey(projectId: String, source: String, hash: String, path: String) = "$DIFF:$projectId:$source:$hash:$path"

    /** The screen key a read refreshes. */
    fun viewKey(op: String, params: JSONObject): String {
        val project = params.optString("project_id")
        return when (op) {
            READ -> key(project)
            HISTORY -> historyKey(project)
            COMMIT -> commitKey(project, params.optString("hash"))
            DIFF -> diffKey(project, params.optString("source"), params.optString("hash"), params.optString("path"))
            else -> op
        }
    }

    fun parseView(key: String, json: JSONObject): Any = when (key.substringBefore(':')) {
        LIST -> parseList(json)
        HISTORY -> parseHistory(json)
        COMMIT -> parseCommit(json)
        DIFF -> SourceDiff(json.optString("path").take(400), json.optString("source").take(20), json.optString("text").take(250_000),
            json.optBoolean("truncated"), json.optBoolean("binary"))
        else -> project(json)
    }

    fun parseHistory(json: JSONObject) = SourceHistory(
        json.objects("refs").take(300).map { SourceRef(it.optString("id").take(300), it.optString("name").take(250), it.optString("hash").take(40),
            it.optString("type").take(10), it.optBoolean("current"), it.optString("upstream").take(250), it.optBoolean("upstream_gone"),
            it.optInt("ahead").coerceAtLeast(0), it.optInt("behind").coerceAtLeast(0)) },
        json.optString("head").take(40), json.objects("commits").take(HISTORY_MAX).map(::historyCommit), json.optBoolean("has_more"))

    fun parseCommit(json: JSONObject) = historyCommit(json).let { c ->
        SourceCommitDetail(c, json.objects("files").take(300).map { SourceCommitFile(it.optString("path").take(400), it.optString("old_path").take(400),
            it.optString("status").take(20), it.optInt("insertions").coerceAtLeast(0), it.optInt("deletions").coerceAtLeast(0), it.optBoolean("binary")) },
            json.optInt("more_files").coerceAtLeast(0))
    }

    private fun historyCommit(json: JSONObject) = SourceHistoryCommit(json.optString("hash").take(40), json.optString("short").take(12),
        json.optString("author").take(80), json.optString("at").take(40), json.optString("subject").take(300), json.optString("body").take(2000),
        json.strings("parents").take(8), json.strings("labels").take(40), json.optBoolean("is_head"),
        json.count("insertions"), json.count("deletions"), json.count("binary_files") ?: 0)

    /** A line count as git reported it, or null when it is missing or not a whole number of at least zero. */
    private fun JSONObject.count(key: String): Int? = (opt(key) as? Number)?.let { n ->
        // org.json may hand back an Integer, Long, Double or BigDecimal; only a whole number from 0 up counts.
        val whole = n.toLong()
        whole.takeIf { it >= 0 && it <= Int.MAX_VALUE && java.math.BigDecimal(n.toString()).compareTo(java.math.BigDecimal(it)) == 0 }?.toInt()
    }

    /**
     * What a commit's line counts say, as the desktop's `commitStats` (`gitHistoryGraph.js`) describes them: null when the
     * counts are missing, so nothing shows rather than +0. [CommitStats.lines] is false for a commit that changed only
     * binary files, so it never claims +0 −0; a merge is counted against its first parent.
     */
    fun commitStats(c: SourceHistoryCommit): CommitStats? {
        val insertions = c.insertions ?: return null
        val deletions = c.deletions ?: return null
        val binary = c.binaryFiles.coerceAtLeast(0)
        fun plural(n: Int, one: String) = "$n $one${if (n == 1) "" else "s"}"
        val label = listOfNotNull(plural(insertions, "insertion"), plural(deletions, "deletion"),
            binary.takeIf { it > 0 }?.let { "${plural(it, "binary file")} without line counts" },
            "compared with the first parent".takeIf { c.parents.size > 1 }).joinToString(", ")
        return CommitStats(insertions, deletions, binary, binary == 0 || insertions > 0 || deletions > 0, label)
    }

    /** A file name and the folder it sits in, as the desktop panel shows a change. */
    fun splitPath(path: String): Pair<String, String> = path.trimEnd('/').let { p ->
        val cut = p.lastIndexOf('/')
        if (cut < 0) p to "" else p.substring(cut + 1) to p.substring(0, cut)
    }

    /** A one-letter badge, as the desktop panel marks each change. */
    fun statusLetter(status: String) = when (status) {
        "modified" -> "M"; "added" -> "A"; "deleted" -> "D"; "renamed" -> "R"; "untracked" -> "U"; "conflict" -> "!"; else -> "•"
    }

    fun trackingText(b: SourceBranch) = when {
        b.upstreamGone -> "Upstream ${b.upstream} is missing"
        b.upstream.isBlank() -> "Local only"
        b.ahead == 0 && b.behind == 0 -> "Up to date with ${b.upstream}"
        else -> listOfNotNull(b.ahead.takeIf { it > 0 }?.let { "$it to push" }, b.behind.takeIf { it > 0 }?.let { "$it to pull" }).joinToString(", ") + " · ${b.upstream}"
    }

    fun parseList(json: JSONObject) = SourceProjectList(json.objects("projects").map(::project).filter { it.id.isNotBlank() }.take(100),
        json.optInt("more").coerceAtLeast(0))

    fun parse(json: JSONObject) = project(json)

    /**
     * Drops queued Git status reads. The outbox resends every event on reconnect, and a desktop without source control
     * would refuse the unknown op; the open screen asks again once the desktop says it supports it.
     */
    fun dropQueuedReads(state: JSONObject): Boolean {
        val outbox = state.optJSONArray("outbox") ?: return false
        val kept = JSONArray()
        for (i in 0 until outbox.length()) {
            val event = JSONObject(outbox.getString(i))
            val op = event.optJSONObject("payload")?.optString("op").orEmpty()
            if (event.optString("type") != "desktop.read" || !isOp(op)) kept.put(outbox.getString(i))
        }
        if (kept.length() == outbox.length()) return false
        state.put("outbox", kept)
        return true
    }

    /** Unified diff lines without the file headers, with each line's number in the file it belongs to. */
    fun diffLines(text: String): List<DiffLine> {
        var old = 0; var new = 0
        val out = mutableListOf<DiffLine>()
        for (line in text.lineSequence()) {
            when {
                line.startsWith("@@") -> {
                    Regex("""@@ -(\d+)(?:,\d+)? \+(\d+)""").find(line)?.let { old = it.groupValues[1].toInt(); new = it.groupValues[2].toInt() }
                    out.add(DiffLine('@', line, null))
                }
                out.isEmpty() -> Unit // diff --git, index, ---, +++ and mode lines
                line.startsWith("+") -> out.add(DiffLine('+', line.drop(1), new++))
                line.startsWith("-") -> out.add(DiffLine('-', line.drop(1), old++))
                line.startsWith("\\") -> out.add(DiffLine('\\', line.drop(2), null))
                line.isEmpty() -> Unit
                else -> out.add(DiffLine(' ', line.drop(1), new++.also { old++ }))
            }
        }
        return out
    }

    /** Each hunk of [lines], by the position of its `@@` line in [lines]. */
    fun hunks(lines: List<DiffLine>): Map<Int, DiffHunk> {
        val found = linkedMapOf<Int, DiffHunk>()
        var start = -1
        fun finish(end: Int) { if (start >= 0) found[start] = DiffHunk(lines[start].text, lines.subList(start + 1, end).toList()) }
        lines.forEachIndexed { index, line -> if (line.kind == '@') { finish(index); start = index } }
        finish(lines.size)
        return found
    }

    /** One short line for the project list: the most important thing to know first. */
    fun headline(p: SourceProject): String = when (p.state) {
        "ok" -> when {
            p.operation.isNotBlank() -> "${p.operation.replaceFirstChar { it.uppercase() }} in progress"
            p.changes.conflicts > 0 -> count(p.changes.conflicts, "conflict")
            p.changes.total > 0 -> count(p.changes.total, "changed file")
            else -> "No uncommitted changes"
        }
        "not_repository" -> "Not a Git repository"
        "missing" -> "Folder missing"
        else -> "Status unavailable"
    }

    /** Where the branch stands against its upstream, in words. */
    fun syncText(p: SourceProject): String = when {
        p.state != "ok" -> ""
        p.upstream.isBlank() -> if (p.detached) "Detached HEAD" else "No upstream branch"
        p.ahead == null || p.behind == null -> "Tracking ${p.upstream}"
        p.ahead == 0 && p.behind == 0 -> "Up to date with ${p.upstream}"
        else -> listOfNotNull(p.ahead.takeIf { it > 0 }?.let { "$it to push" }, p.behind.takeIf { it > 0 }?.let { "$it to pull" })
            .joinToString(", ")
    }

    fun branchText(p: SourceProject) = when {
        p.state != "ok" -> ""
        p.branch.isNotBlank() -> p.branch
        p.detached && p.head.isNotBlank() -> "Detached at ${p.head.take(7)}"
        else -> "No commits yet"
    }

    /** Whether the project needs a look: a conflict, an unfinished merge or rebase, or something to pull. */
    fun attention(p: SourceProject) = p.state == "ok" && (p.changes.conflicts > 0 || p.operation.isNotBlank() || (p.behind ?: 0) > 0)

    fun statusLabel(status: String) = when (status) {
        "modified" -> "Modified"; "added" -> "Added"; "deleted" -> "Deleted"; "renamed" -> "Renamed"
        "untracked" -> "New"; "conflict" -> "Conflict"; else -> status.replaceFirstChar { it.uppercase() }
    }

    /** The project's file list grouped like the desktop panel: conflicts, staged, then other changes. */
    fun groups(files: List<SourceFile>): List<Pair<String, List<SourceFile>>> = listOf(
        "Conflicts" to files.filter { it.status == "conflict" },
        "Staged" to files.filter { it.staged && it.status != "conflict" },
        "Changes" to files.filter { !it.staged && it.status != "conflict" },
    ).filter { it.second.isNotEmpty() }

    private fun count(n: Int, one: String) = "$n $one${if (n == 1) "" else "s"}"

    private fun project(json: JSONObject) = SourceProject(
        json.optString("project_id").take(128), json.optString("name").take(160), json.optString("state").take(32),
        json.optString("detail").take(300), json.optString("branch").take(200), json.optBoolean("detached"), json.optString("head").take(40),
        json.optString("upstream").take(200), json.optIntOrNull("ahead"), json.optIntOrNull("behind"),
        json.optJSONObject("changes").let { c -> SourceChanges(c.int("total"), c.int("staged"), c.int("unstaged"), c.int("untracked"), c.int("conflicts")) },
        json.optString("operation").take(40), json.optString("fetched_at").take(64),
        json.optJSONObject("last_commit")?.let(::commit), json.optString("subfolder").take(400),
        json.objects("files").take(200).map { SourceFile(it.optString("path").take(400), it.optString("status").take(20), it.optBoolean("staged"), it.optBoolean("unstaged")) },
        json.optInt("more_files").coerceAtLeast(0),
        json.objects("commits").take(30).map(::commit), json.optInt("stashes").coerceAtLeast(0),
        json.objects("worktrees").take(20).map { SourceWorktree(it.optString("name").take(160), it.optString("branch").take(200), it.optBoolean("detached"),
            it.optBoolean("current"), it.optBoolean("locked"), it.optBoolean("missing"), it.optIntOrNull("changes")) },
        json.optInt("more_worktrees").coerceAtLeast(0),
        detailed = json.has("files"),
        branches = json.objects("branches").take(200).map { SourceBranch(it.optString("name").take(250), it.optBoolean("current"), it.optBoolean("worktree"),
            it.optString("upstream").take(250), it.optInt("ahead").coerceAtLeast(0), it.optInt("behind").coerceAtLeast(0), it.optBoolean("upstream_gone")) },
        remotes = json.optInt("remotes", -1))

    private fun commit(json: JSONObject) = SourceCommit(json.optString("hash").take(40), json.optString("subject").take(200),
        json.optString("author").take(80), json.optString("at").take(64))

    private fun JSONObject?.int(key: String) = this?.optInt(key)?.coerceAtLeast(0) ?: 0
    private fun JSONObject.optIntOrNull(key: String): Int? = (opt(key) as? Number)?.toInt()
    private fun JSONObject.strings(key: String): List<String> = optJSONArray(key)?.let { a -> (0 until a.length()).map { a.optString(it) } }.orEmpty()
    private fun JSONObject.objects(key: String): List<JSONObject> = optJSONArray(key)?.let { a -> (0 until a.length()).mapNotNull { a.optJSONObject(it) } }.orEmpty()
}

data class SourceChanges(val total: Int, val staged: Int, val unstaged: Int, val untracked: Int, val conflicts: Int)
data class SourceCommit(val hash: String, val subject: String, val author: String, val at: String)
data class SourceFile(val path: String, val status: String, val staged: Boolean, val unstaged: Boolean)
data class SourceWorktree(val name: String, val branch: String, val detached: Boolean, val current: Boolean, val locked: Boolean,
                          val missing: Boolean, val changes: Int?)
/**
 * One open desktop project's Git state. [state] is `ok`, `not_repository`, `missing` (the folder is gone) or `unavailable`
 * (Git is missing or failed), with the desktop's sentence in [detail]. [ahead] and [behind] are null without an upstream.
 * The file list, history, stashes and worktrees come only with one project's read ([detailed]).
 */
data class SourceProject(val id: String, val name: String, val state: String, val detail: String, val branch: String, val detached: Boolean,
                         val head: String, val upstream: String, val ahead: Int?, val behind: Int?, val changes: SourceChanges,
                         val operation: String, val fetchedAt: String, val lastCommit: SourceCommit?, val subfolder: String,
                         val files: List<SourceFile> = emptyList(), val moreFiles: Int = 0, val commits: List<SourceCommit> = emptyList(),
                         val stashes: Int = 0, val worktrees: List<SourceWorktree> = emptyList(), val moreWorktrees: Int = 0,
                         val detailed: Boolean = false,
                         /** Local branches; empty from a desktop without history support. [remotes] is -1 when unknown. */
                         val branches: List<SourceBranch> = emptyList(), val remotes: Int = -1)
data class SourceBranch(val name: String, val current: Boolean, val worktree: Boolean, val upstream: String, val ahead: Int, val behind: Int,
                        val upstreamGone: Boolean)
/** A branch, remote branch or tag. [id] is the full ref name the history filter sends back. */
data class SourceRef(val id: String, val name: String, val hash: String, val type: String, val current: Boolean, val upstream: String,
                     val upstreamGone: Boolean, val ahead: Int, val behind: Int)
/** [labels] are the [SourceRef.id]s pointing at this commit. [body] is the message after its subject. */
data class SourceHistoryCommit(val hash: String, val short: String, val author: String, val at: String, val subject: String, val body: String,
                               val parents: List<String>, val labels: List<String>, val isHead: Boolean,
                               /** Null when the desktop sent no count; see [SourceControl.commitStats]. */
                               val insertions: Int?, val deletions: Int?, val binaryFiles: Int)
/** A commit's line counts and how to say them; [lines] is false when only binary files changed. */
data class CommitStats(val insertions: Int, val deletions: Int, val binaryFiles: Int, val lines: Boolean, val label: String)
data class SourceHistory(val refs: List<SourceRef>, val head: String, val commits: List<SourceHistoryCommit>, val hasMore: Boolean)
data class SourceCommitFile(val path: String, val oldPath: String, val status: String, val insertions: Int, val deletions: Int, val binary: Boolean)
data class SourceCommitDetail(val commit: SourceHistoryCommit, val files: List<SourceCommitFile>, val moreFiles: Int)
/** One line of a unified diff: [kind] is `@` for a hunk's heading, `+`, `-`, a space, or a backslash for git's own notes. */
data class DiffLine(val kind: Char, val text: String, val number: Int?)
/** A diff's `@@` line and the lines under it. */
data class DiffHunk(val header: String, val lines: List<DiffLine>)
/** One file's unified diff. [source] is `staged`, `unstaged` or `commit`. */
data class SourceDiff(val path: String, val source: String, val text: String, val truncated: Boolean, val binary: Boolean)
data class SourceProjectList(val projects: List<SourceProject>, val more: Int)
