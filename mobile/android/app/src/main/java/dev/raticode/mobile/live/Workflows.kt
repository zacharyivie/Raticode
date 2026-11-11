package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject

/**
 * Phone views of desktop workflows: the list, one workflow's graph, its runs and the controls
 * the desktop allows. The phone never edits a workflow; the desktop decides and carries out
 * every run, stop and resume.
 */
object Workflows {
    const val LIST = "workflows.list"
    const val READ = "workflows.read"
    val READS = setOf(LIST, READ)
    val ACTIONS = setOf("workflows.run", "workflows.stop", "workflows.resume")
    const val UPDATE_DESKTOP = "Update Raticode on your computer to see and run workflows from this phone."
    private val ACTIVE = setOf("queued", "running", "waiting", "stopping")

    fun isWorkflowOp(op: String) = op.startsWith("workflows.")

    /** Run and node statuses that are still going. Unknown statuses count as finished. */
    fun active(status: String) = status in ACTIVE

    fun label(status: String) = when (status) {
        "idle" -> "Not run yet"; "queued" -> "Queued"; "running" -> "Running"; "waiting" -> "Waiting for you"
        "stopping" -> "Stopping"; "succeeded" -> "Succeeded"; "failed" -> "Failed"; "stopped" -> "Stopped"; "interrupted" -> "Interrupted"; "invalid" -> "Has errors"
        "pending" -> "Not started"; "skipped" -> "Skipped"; "" -> "Unknown"
        else -> status.replace('_', ' ').replaceFirstChar { it.uppercase() }
    }

    fun parseList(json: JSONObject) = WorkflowList(json.objects("workflows").map(::summary), json.optInt("earlier"))

    fun parse(json: JSONObject): Workflow {
        val controls = json.optJSONObject("controls")
        fun control(name: String) = controls?.optJSONObject(name).let { c ->
            WorkflowControl(c?.optBoolean("allowed") == true, c?.optString("reason").orEmpty())
        }
        return Workflow(summary(json),
            json.objects("nodes").map { WorkflowNode(it.optString("node_id"), it.optString("label"), it.optString("kind")) }
                .filter { it.id.isNotBlank() }.distinctBy { it.id },
            json.objects("edges").map { WorkflowEdge(it.optString("from"), it.optString("to"), it.optString("label")) },
            json.objects("runs").map(::run).filter { it.id.isNotBlank() }, json.optInt("earlier_runs"),
            json.optJSONObject("run")?.let { r ->
                WorkflowRunDetail(run(r), r.optString("error"), r.objects("nodes").map {
                    WorkflowNodeRun(it.optString("node_id"), it.optString("status"), it.optString("started_at"), it.optString("finished_at"), it.optString("detail"))
                })
            }?.takeIf { it.run.id.isNotBlank() },
            control("run"), control("stop"), control("resume"), json.optString("revision"),
            json.optJSONObject("preview").let { p -> WorkflowPreview(p.texts("blocking"), p.texts("destructive"), p.texts("warnings")) })
    }

    /** True for a full workflow view rather than an action's short answer. */
    fun isView(json: JSONObject) = json.has("nodes") && json.has("workflow_id")

    /**
     * Polling reads are safe to drop: a reconnect may reach an older desktop that would reject
     * an unknown op and close the session before it reports what it supports.
     */
    fun dropQueuedReads(state: JSONObject): Boolean {
        val outbox = state.optJSONArray("outbox") ?: return false
        val kept = JSONArray()
        for (i in 0 until outbox.length()) {
            val event = JSONObject(outbox.getString(i))
            val op = event.optJSONObject("payload")?.optString("op").orEmpty()
            if (event.optString("type") != "desktop.read" || !isWorkflowOp(op)) kept.put(outbox.getString(i))
        }
        if (kept.length() == outbox.length()) return false
        state.put("outbox", kept)
        return true
    }

    private fun summary(json: JSONObject) = WorkflowSummary(json.optString("workflow_id"), json.optString("name"), json.optString("description"),
        json.optString("project_name"), json.optString("status"), json.optString("updated_at"),
        json.optJSONObject("latest_run")?.let(::run)?.takeIf { it.id.isNotBlank() })

    private fun run(json: JSONObject) = WorkflowRun(json.optString("run_id"), json.optString("status"), json.optString("started_at"),
        json.optString("finished_at"), json.optString("trigger"), json.optString("summary"))

    private fun JSONObject?.texts(key: String): List<String> =
        this?.optJSONArray(key)?.let { a -> (0 until a.length()).map { a.optString(it) }.filter { it.isNotBlank() } }.orEmpty()
    private fun JSONObject.objects(key: String): List<JSONObject> = optJSONArray(key)?.let { a -> (0 until a.length()).mapNotNull { a.optJSONObject(it) } }.orEmpty()
}

data class WorkflowRun(val id: String, val status: String, val startedAt: String, val finishedAt: String, val trigger: String, val summary: String) {
    val active get() = Workflows.active(status)
}
data class WorkflowSummary(val id: String, val name: String, val description: String, val project: String, val status: String,
                           val updatedAt: String, val latestRun: WorkflowRun?) {
    val active get() = Workflows.active(status) || latestRun?.active == true
}
data class WorkflowList(val items: List<WorkflowSummary>, val earlier: Int)
data class WorkflowNode(val id: String, val label: String, val kind: String)
data class WorkflowEdge(val from: String, val to: String, val label: String)
data class WorkflowNodeRun(val nodeId: String, val status: String, val startedAt: String, val finishedAt: String, val detail: String)
data class WorkflowRunDetail(val run: WorkflowRun, val error: String, val nodes: List<WorkflowNodeRun>)
/** What the desktop's run preview shows before Run: problems that stop it, actions that change things, and warnings. */
data class WorkflowPreview(val blocking: List<String> = emptyList(), val destructive: List<String> = emptyList(), val warnings: List<String> = emptyList())
/** Whether the desktop allows a control now, and the sentence to show when it does not. */
data class WorkflowControl(val allowed: Boolean, val reason: String)
data class Workflow(val summary: WorkflowSummary, val nodes: List<WorkflowNode>, val edges: List<WorkflowEdge>, val runs: List<WorkflowRun>,
                    val earlierRuns: Int, val run: WorkflowRunDetail?, val runControl: WorkflowControl, val stopControl: WorkflowControl,
                    val resumeControl: WorkflowControl, val revision: String = "", val preview: WorkflowPreview = WorkflowPreview()) {
    val id get() = summary.id
    /** The run whose node statuses are shown, falling back to the newest. */
    val shownRun get() = run?.run ?: runs.firstOrNull() ?: summary.latestRun
    val activeRun get() = runs.firstOrNull { it.active } ?: summary.latestRun?.takeIf { it.active } ?: run?.run?.takeIf { it.active }
}

/** A node's place in a layered top-to-bottom drawing. */
data class GraphSlot(val layer: Int, val column: Int)

/**
 * Lays a workflow out in layers for a narrow screen: each node sits below every node that leads
 * to it, except along edges that loop back, which are drawn on the side instead.
 */
data class GraphLayout(val slots: Map<String, GraphSlot>, val layers: Int, val columns: Int, val edges: List<WorkflowEdge>, val backEdges: Set<WorkflowEdge>) {
    /** Nodes in reading order: by layer, then left to right. */
    val order: List<String> get() = slots.entries.sortedWith(compareBy({ it.value.layer }, { it.value.column })).map { it.key }

    companion object {
        fun of(nodes: List<WorkflowNode>, edges: List<WorkflowEdge>): GraphLayout {
            val ids = nodes.map { it.id }.distinct()
            val known = ids.toSet()
            val kept = edges.filter { it.from in known && it.to in known && it.from != it.to }.distinctBy { it.from to it.to }
            val out = ids.associateWith { id -> kept.filter { it.from == id } }
            // Depth-first from nodes nothing leads to marks edges that return to an open node.
            val back = mutableSetOf<WorkflowEdge>()
            val state = HashMap<String, Int>()
            fun visit(start: String) {
                val stack = ArrayDeque<Pair<String, Iterator<WorkflowEdge>>>()
                state[start] = 1; stack.addLast(start to out.getValue(start).iterator())
                while (stack.isNotEmpty()) {
                    val (node, next) = stack.last()
                    if (!next.hasNext()) { state[node] = 2; stack.removeLast(); continue }
                    val edge = next.next()
                    when (state[edge.to]) {
                        1 -> back += edge
                        null -> { state[edge.to] = 1; stack.addLast(edge.to to out.getValue(edge.to).iterator()) }
                    }
                }
            }
            val targets = kept.map { it.to }.toSet()
            ids.filter { it !in targets }.forEach { if (it !in state) visit(it) }
            ids.forEach { if (it !in state) visit(it) }
            val forward = kept.filter { it !in back }
            // Longest path keeps every forward edge pointing down.
            val layer = HashMap<String, Int>()
            val incoming = ids.associateWith { id -> forward.count { it.to == id } }.toMutableMap()
            val ready = ArrayDeque(ids.filter { incoming[it] == 0 })
            while (ready.isNotEmpty()) {
                val node = ready.removeFirst()
                val depth = layer.getOrPut(node) { 0 }
                for (edge in forward.filter { it.from == node }) {
                    layer[edge.to] = maxOf(layer[edge.to] ?: 0, depth + 1)
                    incoming[edge.to] = incoming.getValue(edge.to) - 1
                    if (incoming[edge.to] == 0) ready.addLast(edge.to)
                }
            }
            val rows = ids.groupBy { layer[it] ?: 0 }.toSortedMap()
            // Order each row under the average position of its parents to reduce crossings.
            val column = HashMap<String, Double>()
            rows.values.forEach { row ->
                row.mapIndexed { index, id ->
                    val parents = forward.filter { it.to == id }.mapNotNull { column[it.from] }
                    id to (if (parents.isEmpty()) index.toDouble() else parents.average()) + index * 1e-3
                }.sortedBy { it.second }.forEachIndexed { index, (id, _) -> column[id] = index.toDouble() }
            }
            val slots = ids.associateWith { GraphSlot(layer[it] ?: 0, column.getValue(it).toInt()) }
            return GraphLayout(slots, (rows.keys.maxOrNull() ?: -1) + 1, rows.values.maxOfOrNull { it.size } ?: 0, kept, back)
        }
    }
}
