package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import org.json.JSONTokener

/**
 * Phone views of desktop workflows: the list, one workflow's graph, its runs and the controls
 * the desktop allows. The phone never edits a workflow; the desktop decides and carries out
 * every run, stop, resume and run again.
 */
object Workflows {
    const val LIST = "workflows.list"
    const val READ = "workflows.read"
    /** Every workflow's runs in one list, newest first, with `since`, `status` and `workflow_id` filters. */
    const val RUNS = "workflows.runs"
    val READS = setOf(LIST, READ, RUNS)
    /** Runs a workflow again with one run's inputs and trigger events, as the desktop saved them. The phone sends no values. */
    const val RERUN = "workflows.rerun"
    /** Turns one of a workflow's triggers on or off. Turning one on is confirmed on the phone first. */
    const val TRIGGER = "workflows.trigger"
    /** Pauses or resumes every trigger on the desktop. Each trigger keeps its own on/off setting. */
    const val PAUSE = "workflows.triggers.pause"
    val ACTIONS = setOf("workflows.run", "workflows.stop", "workflows.resume", RERUN, TRIGGER, PAUSE)
    const val UPDATE_TRIGGERS = "Update Raticode on your computer to turn its triggers on and off from this phone."
    const val UPDATE_DESKTOP = "Update Raticode on your computer to see and run workflows from this phone."
    const val UPDATE_RUNS = "Update Raticode on your computer to see every workflow's runs in one list."
    /** The time ranges the all-runs list offers; blank is all time. */
    val SPANS = listOf("24h" to "24 hours", "7d" to "7 days", "30d" to "30 days", "" to "All time")
    /** Status filters the desktop takes, with their chip labels; blank is any status. */
    val STATUS_FILTERS = listOf("" to "Any status", "error" to "Failed", "running" to "Running", "success" to "Passed", "stopped" to "Stopped")
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

    fun startedByText(value: String) = when (value) {
        "trigger" -> "Trigger"; "phone" -> "Phone"; "rem" -> "Rem"; else -> "Manual"
    }

    fun runsParams(since: String, status: String, workflow: String): JSONObject = JSONObject().put("since", since).apply {
        if (status.isNotBlank()) put("status", status)
        if (workflow.isNotBlank()) put("workflow_id", workflow)
    }

    fun parseRuns(json: JSONObject) = AllWorkflowRuns(
        json.objects("runs").map { AllWorkflowRun(run(it), it.optString("workflow_id"), it.optString("workflow_name"), it.optString("project_name"),
            it.optString("started_by"), it.optString("tokens"), it.optString("cost")) }.filter { it.run.id.isNotBlank() && it.workflowId.isNotBlank() },
        json.optInt("earlier"), json.optString("summary"),
        json.objects("workflows").map { it.optString("workflow_id") to it.optString("name") }.filter { it.first.isNotBlank() })

    fun parseList(json: JSONObject) = WorkflowList(json.objects("workflows").map(::summary), json.optInt("earlier"),
        json.optJSONObject("triggers")?.let { TriggerPause(it.optBoolean("paused"), it.optString("paused_at"), it.optInt("count"), it.optInt("enabled")) })

    /** True for a pause or resume answer rather than the workflow list, so it never replaces the list. */
    fun isPauseAnswer(json: JSONObject) = !json.has("workflows") && json.has("paused")

    /**
     * Turning a trigger on is sent only after the phone asked, so it carries `confirmed`; the desktop refuses it without.
     * Turning one off needs no confirmation.
     */
    fun triggerAction(workflow: Workflow, trigger: WorkflowTrigger, enabled: Boolean): JSONObject = JSONObject()
        .put("workflow_id", workflow.id).put("trigger_id", trigger.id).put("enabled", enabled).apply { if (enabled) put("confirmed", true) }

    /** Resuming starts unattended work again, so it is confirmed on the phone first and says so. */
    fun pauseAction(paused: Boolean): JSONObject = JSONObject().put("paused", paused).apply { if (!paused) put("confirmed", true) }

    /** A trigger's last result in the words the phone's run statuses use. */
    fun triggerStatus(status: String) = when (status) { "passed" -> "succeeded"; else -> status }

    fun triggerResultLabel(status: String) = when (status) {
        "missed" -> "Missed"; "skipped" -> "Skipped"; else -> label(triggerStatus(status))
    }

    fun parse(json: JSONObject): Workflow {
        val controls = json.optJSONObject("controls")
        fun control(name: String) = controls?.optJSONObject(name).let { c ->
            WorkflowControl(c?.optBoolean("allowed") == true, c?.optString("reason").orEmpty(), c.texts("rerun_nodes"))
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
            json.optJSONObject("preview").let { p -> WorkflowPreview(p.texts("blocking"), p.texts("destructive"), p.texts("warnings")) },
            // A desktop that takes run inputs always sends the list, even when it is empty.
            if (json.has("inputs")) json.objects("inputs").map(::input).filter { it.name.isNotBlank() } else null,
            // Only a desktop that can run a run again sends this control; older ones never see the op.
            if (controls?.has("rerun") == true) control("rerun") else null,
            // A desktop that lists triggers always sends the list, even when it is empty.
            if (json.has("triggers")) json.objects("triggers").map(::trigger).filter { it.id.isNotBlank() } else null,
            json.optBoolean("triggers_paused"))
    }

    private fun trigger(json: JSONObject) = WorkflowTrigger(json.optString("trigger_id"), json.optString("kind"),
        json.optString("kind_label").ifBlank { json.optString("kind") }, json.optString("label"), json.optString("description"),
        json.optBoolean("enabled"), json.optString("next_run_at"),
        json.optJSONObject("last_result")?.let { TriggerResult(it.optString("status"), it.optString("at"), it.optString("message")) })

    private fun input(json: JSONObject) = WorkflowInput(json.optString("name"), json.optString("label").ifBlank { json.optString("name") },
        json.optString("description"), json.optString("kind").ifBlank { "json" }, json.optBoolean("required"), json.optBoolean("secret"),
        json.optBoolean("has_default"), if (json.has("default")) json.opt("default") else null,
        json.optJSONArray("choices")?.let { a -> (0 until a.length()).map { a.opt(it) } }.orEmpty(),
        json.optNumber("minimum"), json.optNumber("maximum"), json.optString("json_type").ifBlank { "value" })

    private fun JSONObject.optNumber(key: String): Double? = (opt(key) as? Number)?.toDouble()

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
/** [triggers] is null from a desktop that can't pause triggers from the phone. */
data class WorkflowList(val items: List<WorkflowSummary>, val earlier: Int, val triggers: TriggerPause? = null)
/** Whether every trigger on the desktop is paused, and how many triggers there are and are on. */
data class TriggerPause(val paused: Boolean, val pausedAt: String, val count: Int, val enabled: Int)
/** A trigger's latest entry in its history: passed, failed, missed, skipped, running and so on. */
data class TriggerResult(val status: String, val at: String, val message: String)
/** One saved trigger of a workflow. The desktop never sends a webhook's address or token. */
data class WorkflowTrigger(val id: String, val kind: String, val kindLabel: String, val label: String, val description: String,
                           val enabled: Boolean, val nextRunAt: String, val lastResult: TriggerResult?) {
    val title get() = label.ifBlank { kindLabel.ifBlank { "Trigger" } }
}
/** One row of the all-runs list: the run, its workflow and project, how it started, and its tokens and estimated cost as the desktop words them. */
data class AllWorkflowRun(val run: WorkflowRun, val workflowId: String, val workflowName: String, val project: String,
                          val startedBy: String, val tokens: String, val cost: String)
/** Every open project's workflow runs, newest first; [summary] reads like "6 workflow runs: 5 passed, 1 failed · est. $0.42". */
data class AllWorkflowRuns(val runs: List<AllWorkflowRun>, val earlier: Int, val summary: String, val workflows: List<Pair<String, String>>)
data class WorkflowNode(val id: String, val label: String, val kind: String)
data class WorkflowEdge(val from: String, val to: String, val label: String)
data class WorkflowNodeRun(val nodeId: String, val status: String, val startedAt: String, val finishedAt: String, val detail: String)
data class WorkflowRunDetail(val run: WorkflowRun, val error: String, val nodes: List<WorkflowNodeRun>)
/** What the desktop's run preview shows before Run: problems that stop it, actions that change things, and warnings. */
data class WorkflowPreview(val blocking: List<String> = emptyList(), val destructive: List<String> = emptyList(), val warnings: List<String> = emptyList())
/** Whether the desktop allows a control now, and the sentence to show when it does not. Resume also names the steps it runs again. */
data class WorkflowControl(val allowed: Boolean, val reason: String, val rerunNodes: List<String> = emptyList())

/**
 * One field of the Run form, built by the desktop from the workflow's input schema: [kind] is text, integer, number,
 * boolean, choice or json. A [secret] input is masked and its default stays on the desktop, so leaving it blank uses it.
 */
data class WorkflowInput(val name: String, val label: String, val description: String, val kind: String, val required: Boolean,
                         val secret: Boolean, val hasDefault: Boolean, val default: Any?, val choices: List<Any?>,
                         val minimum: Double?, val maximum: Double?, val jsonType: String) {
    /** Blank leaves the input out, so the workflow's default applies. Required inputs without one must be filled in. */
    val needed get() = required && !hasDefault
}

/**
 * The Run form's drafts and the values it sends. Drafts stay the text the user typed until Run, so editing a number or
 * JSON never snaps back. The desktop checks the values against the schema again before it starts anything.
 */
object RunForm {
    fun drafts(inputs: List<WorkflowInput>): Map<String, String> = inputs.associate { input ->
        val value = input.default.takeUnless { input.secret }
        input.name to when (input.kind) {
            "boolean" -> (value == true).toString()
            "choice" -> input.choices.indexOfFirst { same(it, value) }.takeIf { value != null && it >= 0 }?.toString().orEmpty()
            "json" -> if (value == null) "" else jsonText(value)
            else -> when (value) { null, JSONObject.NULL -> ""; is Number -> number(value.toDouble(), value); else -> value.toString() }
        }
    }

    /** The values to send and a message per field that needs fixing. A field left blank is not sent. */
    fun values(inputs: List<WorkflowInput>, drafts: Map<String, String>): Pair<JSONObject, Map<String, String>> {
        val values = JSONObject()
        val errors = mutableMapOf<String, String>()
        for (input in inputs) {
            val draft = drafts[input.name].orEmpty()
            if (input.kind == "boolean") { values.put(input.name, draft == "true"); continue }
            if ((if (input.kind == "text") draft.isEmpty() else draft.isBlank())) {
                if (input.needed) errors[input.name] = "Required"
                continue
            }
            when (input.kind) {
                "text" -> values.put(input.name, draft)
                "integer" -> draft.trim().takeIf { Regex("[-+]?\\d{1,18}").matches(it) }?.toLong()?.let { n ->
                    bounds(input, n.toDouble())?.let { errors[input.name] = it } ?: values.put(input.name, n)
                } ?: run { errors[input.name] = "Enter a whole number" }
                "number" -> draft.trim().toDoubleOrNull()?.takeIf { it.isFinite() }?.let { n ->
                    bounds(input, n)?.let { errors[input.name] = it } ?: values.put(input.name, n)
                } ?: run { errors[input.name] = "Enter a number" }
                "choice" -> {
                    val index = draft.toIntOrNull()
                    if (index != null && index in input.choices.indices) { values.put(input.name, input.choices[index] ?: JSONObject.NULL) }
                    else { errors[input.name] = "Choose an option" }
                }
                else -> {
                    val parsed = parseJson(draft)
                    val fits = parsed !== NOT_JSON && (input.jsonType != "array" || parsed is JSONArray) &&
                        (input.jsonType != "object" || parsed is JSONObject)
                    if (fits) { values.put(input.name, parsed) } else { errors[input.name] = jsonHint(input) }
                }
            }
        }
        return values to errors
    }

    fun jsonHint(input: WorkflowInput) = when (input.jsonType) {
        "array" -> "Enter a JSON list, e.g. [\"a\", \"b\"]"
        "object" -> "Enter a JSON object, e.g. {\"key\": \"value\"}"
        else -> "Enter a JSON value, e.g. \"text\", 3 or [1, 2]"
    }

    fun choiceText(value: Any?): String = when (value) { null, JSONObject.NULL -> "None"; is String -> value; else -> jsonText(value) }

    private val NOT_JSON = Any()

    private fun parseJson(text: String): Any = runCatching {
        val tokener = JSONTokener(text.trim())
        val value = tokener.nextValue()
        if (tokener.more()) NOT_JSON else value
    }.getOrDefault(NOT_JSON)

    private fun jsonText(value: Any): String = when (value) {
        is String -> JSONObject.quote(value)
        else -> JSONArray().put(value).toString().removePrefix("[").removeSuffix("]")
    }

    private fun same(a: Any?, b: Any?): Boolean = when {
        a is Number && b is Number -> a.toDouble() == b.toDouble()
        else -> a == b
    }

    private fun bounds(input: WorkflowInput, value: Double): String? = when {
        input.minimum != null && value < input.minimum -> "Must be at least ${number(input.minimum)}"
        input.maximum != null && value > input.maximum -> "Must be at most ${number(input.maximum)}"
        else -> null
    }

    private fun number(value: Double, original: Number? = null): String =
        if (original != null && original !is Double && original !is Float) original.toString()
        else if (value == Math.rint(value) && kotlin.math.abs(value) < 1e15) value.toLong().toString() else value.toString()
}
data class Workflow(val summary: WorkflowSummary, val nodes: List<WorkflowNode>, val edges: List<WorkflowEdge>, val runs: List<WorkflowRun>,
                    val earlierRuns: Int, val run: WorkflowRunDetail?, val runControl: WorkflowControl, val stopControl: WorkflowControl,
                    val resumeControl: WorkflowControl, val revision: String = "", val preview: WorkflowPreview = WorkflowPreview(),
                    /** The Run form's fields; null when the desktop can't take run inputs from the phone. */
                    val inputs: List<WorkflowInput>? = null,
                    /** Run the shown run again with its saved inputs; null when the desktop can't. */
                    val rerunControl: WorkflowControl? = null,
                    /** This workflow's triggers; null when the desktop doesn't list them to the phone. */
                    val triggers: List<WorkflowTrigger>? = null,
                    /** All triggers on the desktop are paused; each still shows its own setting. */
                    val triggersPaused: Boolean = false) {
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
