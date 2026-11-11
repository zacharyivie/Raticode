package dev.raticode.mobile.live

/**
 * The desktop's branch history graph (`frontend/src/lib/gitHistoryGraph.js`), ported line for line so the phone draws
 * the same lanes, colors and joins. Commits come newest first in topological order; each row says which lane its dot
 * sits in and which line segments pass through or leave it.
 */
object HistoryGraph {
    /** Blue, amber, magenta and teal, each with its own dash pattern so color is never the only cue. */
    const val COLORS = 4

    /** One line in a row. [full] lines pass through from the row above; [top] joins the dot from above. */
    data class Segment(val from: Int, val to: Int, val color: Int, val full: Boolean = false, val top: Boolean = false)
    data class Row(val commit: SourceHistoryCommit, val lane: Int, val color: Int, val segments: List<Segment>)
    /** [lanes] is the widest the graph gets, in lanes. */
    data class Layout(val rows: List<Row>, val lanes: Int)

    private data class Slot(val hash: String, val color: Int)

    fun layout(commits: List<SourceHistoryCommit>): Layout {
        val lanes = mutableListOf<Slot?>()
        var nextColor = 0
        var width = 1
        fun allocate(hash: String, color: Int? = null): Int {
            var lane = lanes.indexOfFirst { it == null }
            if (lane < 0) { lane = lanes.size; lanes.add(null) }
            lanes[lane] = Slot(hash, color ?: (nextColor++ % COLORS))
            return lane
        }
        val rows = commits.map { commit ->
            var lane = lanes.indexOfFirst { it?.hash == commit.hash }
            val starts = lane < 0
            if (starts) lane = allocate(commit.hash)
            val top = lanes.toList()
            val color = lanes[lane]!!.color
            lanes[lane] = null
            val targets = commit.parents.distinct().mapIndexed { index, hash ->
                var target = lanes.indexOfFirst { it?.hash == hash }
                if (target < 0) target = allocate(hash, if (index == 0) color else null)
                target to (if (index == 0) color else lanes[target]!!.color)
            }
            val segments = top.flatMapIndexed { index, item ->
                if (item == null || index == lane) emptyList()
                else lanes.indexOfFirst { it?.hash == item.hash }.let { target ->
                    if (target < 0) emptyList() else listOf(Segment(index, target, item.color, full = true))
                }
            }.toMutableList()
            if (!starts) segments.add(Segment(lane, lane, color, top = true))
            targets.forEach { (target, targetColor) -> segments.add(Segment(lane, target, targetColor)) }
            width = maxOf(width, top.size, lanes.size)
            while (lanes.isNotEmpty() && lanes.last() == null) lanes.removeAt(lanes.lastIndex)
            Row(commit, lane, color, segments)
        }
        return Layout(rows, width)
    }

    /**
     * Commits whose ID, subject, message, author or ref names contain every word of [query]. Hidden commits are bridged:
     * each shown commit's parents become its nearest shown ancestors, so a search never suggests a false branch.
     */
    fun filter(commits: List<SourceHistoryCommit>, query: String, refNames: (SourceHistoryCommit) -> List<String> = { emptyList() }): List<SourceHistoryCommit> {
        val words = query.trim().lowercase().split(Regex("\\s+")).filter { it.isNotEmpty() }
        if (words.isEmpty()) return commits
        val byHash = commits.associateBy { it.hash }
        val visible = commits.filter { commit ->
            val text = (listOf(commit.hash, commit.subject, commit.body, commit.author) + refNames(commit)).joinToString(" ").lowercase()
            words.all { it in text }
        }.map { it.hash }.toSet()
        return commits.filter { it.hash in visible }.map { commit ->
            val parents = LinkedHashSet<String>()
            val pending = ArrayDeque(commit.parents.reversed())
            val visited = HashSet<String>()
            while (pending.isNotEmpty()) {
                val hash = pending.removeLast()
                if (!visited.add(hash)) continue
                if (hash in visible || hash !in byHash) parents.add(hash)
                else byHash.getValue(hash).parents.reversed().forEach(pending::addLast)
            }
            commit.copy(parents = parents.toList())
        }
    }
}
