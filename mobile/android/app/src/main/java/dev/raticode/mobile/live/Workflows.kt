package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import org.json.JSONTokener

/**
 * Phone views of desktop workflows: the list, one workflow's graph, its runs and the controls
 * the desktop allows. The desktop decides and carries out every run, stop, resume and run again.
 * The phone edits a workflow only through Use this model after Try another model, which the
 * desktop writes through its own editor.
 */
object Workflows {
    const val LIST = "workflows.list"
    const val READ = "workflows.read"
    /** Every workflow's runs in one list, newest first, with `since`, `status` and `workflow_id` filters. */
    const val RUNS = "workflows.runs"
    /** What changed in a trigger's workflow since the version it runs was approved, as a diff. */
    const val CHANGES = "workflows.trigger.changes"
    /**
     * What Ask Rem about this failure attaches for one run: a file name and compact text the desktop marks as untrusted run
     * output (workflow file, run, node and type, error, the end of its output, preflight problems, inputs with passwords masked).
     */
    const val FAILURE = "workflows.failure"
    /**
     * Try another model: one workflow's test set with the steps that call a model, the desktop's providers and the newest
     * result. A try's result compares each case side by side, current model against the candidate. Polled while a try runs.
     */
    const val TESTS = "workflows.tests"
    /** The expected cost of both models before anything runs. */
    const val ESTIMATE = "workflows.tests.estimate"
    /** Runs the test set with the candidate beside the current model. Sent only after [ESTIMATE] was shown, with `confirmed`. */
    const val TRY = "workflows.tests.try"
    /** Writes the tried model into the step through the desktop's editor; triggers ask for approval again. */
    const val USE_MODEL = "workflows.tests.use_model"
    val TEST_READS = setOf(TESTS, ESTIMATE)
    /** Both answer with the [TESTS] view, so they refresh the screen that sent them. */
    val TEST_ACTIONS = setOf(TRY, USE_MODEL)
    const val UPDATE_TESTS = "Update Raticode on your computer to try another model on a workflow's tests from this phone."
    val READS = setOf(LIST, READ, RUNS, CHANGES, FAILURE) + TEST_READS
    const val UPDATE_FAILURE = "Update Raticode on your computer to ask Rem about a failed run from this phone."
    /** Runs whose card offers Ask Rem about this failure. */
    fun canAskRem(status: String) = status == "failed" || status == "interrupted"
    /** The read for one run; without [nodeId] the desktop picks the node that failed. */
    fun failureParams(workflowId: String, runId: String, nodeId: String? = null): JSONObject =
        JSONObject().put("workflow_id", workflowId).put("run_id", runId).apply { nodeId?.takeIf { it.isNotBlank() }?.let { put("node_id", it) } }
    fun failureKey(params: JSONObject) = FAILURE + ":" + params.optString("workflow_id") + "/" + params.optString("run_id") + "/" + params.optString("node_id")
    fun parseFailure(json: JSONObject) = RunFailure(json.optString("run_id"), json.optString("node_id"),
        json.optString("name").ifBlank { "run-failure.txt" }, json.optString("text"))
    /** Runs a workflow again with one run's inputs and trigger events, as the desktop saved them. The phone sends no values. */
    const val RERUN = "workflows.rerun"
    /** Turns one of a workflow's triggers on or off. Turning one on is confirmed on the phone first. */
    const val TRIGGER = "workflows.trigger"
    /** Pauses or resumes every trigger on the desktop. Each trigger keeps its own on/off setting. */
    const val PAUSE = "workflows.triggers.pause"
    /** Approves the workflow version a trigger runs: exactly the one [CHANGES] showed, named by its fingerprint. */
    const val APPROVE = "workflows.trigger.approve"
    val ACTIONS = setOf("workflows.run", "workflows.stop", "workflows.resume", RERUN, TRIGGER, PAUSE, APPROVE)
    const val UPDATE_APPROVAL = "Update Raticode on your computer to review and approve trigger changes from this phone."
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
        json.optJSONObject("triggers")?.let { TriggerPause(it.optBoolean("paused"), it.optString("paused_at"), it.optInt("count"), it.optInt("enabled"),
            it.optString("daily_limit").take(300), it.optBoolean("limit_reached")) })

    /** True for a pause or resume answer rather than the workflow list, so it never replaces the list. */
    fun isPauseAnswer(json: JSONObject) = !json.has("workflows") && json.has("paused")

    /**
     * Turning a trigger on is sent only after the phone asked, so it carries `confirmed`; the desktop refuses it without.
     * Turning one off needs no confirmation.
     */
    fun triggerAction(workflow: Workflow, trigger: WorkflowTrigger, enabled: Boolean): JSONObject = JSONObject()
        .put("workflow_id", workflow.id).put("trigger_id", trigger.id).put("enabled", enabled).apply { if (enabled) put("confirmed", true) }

    /** Asks for what changed in a trigger's workflow since it was approved. */
    fun changesParams(workflowId: String, triggerId: String): JSONObject =
        JSONObject().put("workflow_id", workflowId).put("trigger_id", triggerId)

    /**
     * Approves the version [changes] showed; a workflow that changed again since is refused by the desktop.
     * [unseenConfirmed] is the second yes for changes the phone couldn't show in full, sent only after asking.
     */
    fun approveAction(workflowId: String, changes: TriggerChanges, unseenConfirmed: Boolean = false): JSONObject = JSONObject()
        .put("workflow_id", workflowId).put("trigger_id", changes.triggerId).put("fingerprint", changes.fingerprint)
        .apply { if (unseenConfirmed) put("unseen_confirmed", true) }

    fun parseChanges(json: JSONObject) = TriggerChanges(json.optString("trigger_id"), json.optString("approval"),
        json.optString("message"), json.optString("fingerprint"), json.optString("approved_at"),
        json.objects("files").map { TriggerChangedFile(it.optString("path"), it.optString("kind"), it.optString("change"),
            it.optString("diff"), it.optBoolean("diff_available", true), it.optBoolean("truncated"),
            it.optString("unseen").take(20), it.optString("open_path").take(1000), it.optString("warning").take(200)) }.filter { it.path.isNotBlank() },
        json.optBoolean("truncated"), json.optString("project_id").take(128), json.optInt("unseen_hidden").coerceAtLeast(0))

    /**
     * "Its diff was cut short" or "Binary or too large to show" for a file the phone couldn't show in full, or the
     * warning for an MCP server whose package has no exact version, so each run can fetch a release nobody reviewed.
     */
    fun unseenLabel(reason: String) = when (reason) {
        "cut" -> "Its diff was cut short"; "unreadable" -> "Binary or too large to show"
        "unpinned" -> UNPINNED_WARNING; else -> "Not shown"
    }

    const val UNPINNED_WARNING = "Package version not pinned: each run fetches the newest release"

    /** "Changed", "Now used", "No longer used" or "Missing", for one file in a trigger's changes. */
    fun fileChangeLabel(change: String) = when (change) {
        "added" -> "Now used"; "removed" -> "No longer used"; "missing" -> "Missing"; "changed" -> "Changed"
        else -> change.replaceFirstChar { it.uppercase() }
    }

    /** Resuming starts unattended work again, so it is confirmed on the phone first and says so. */
    fun pauseAction(paused: Boolean): JSONObject = JSONObject().put("paused", paused).apply { if (!paused) put("confirmed", true) }

    /** A trigger's last result in the words the phone's run statuses use. */
    fun triggerStatus(status: String) = when (status) { "passed" -> "succeeded"; else -> status }

    /** A trigger's last result. "waiting" is a run the desktop resumes once its provider's usage limit resets. */
    fun triggerResultLabel(status: String) = when (status) {
        "missed" -> "Missed"; "skipped" -> "Skipped"; "waiting" -> USAGE_LIMIT_WAIT; else -> label(triggerStatus(status))
    }

    const val USAGE_LIMIT_WAIT = "Waiting for usage limit"

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

    fun testsParams(workflowId: String): JSONObject = JSONObject().put("workflow_id", workflowId)
    fun testsKey(workflowId: String) = "$TESTS:$workflowId"

    private fun modelsJson(models: List<TriedModel>) = JSONArray().apply {
        models.forEach { put(JSONObject().put("node", it.node).put("provider", it.provider).put("model", it.model)) }
    }

    /** The estimate read for [models] on the revision the tests view showed. */
    fun estimateParams(workflowId: String, revision: String, models: List<TriedModel>): JSONObject =
        JSONObject().put("workflow_id", workflowId).put("expected_revision", revision).put("models", modelsJson(models))

    /** Sent only after the phone showed both expected costs, so it always carries `confirmed`. */
    fun tryAction(workflowId: String, revision: String, models: List<TriedModel>): JSONObject =
        estimateParams(workflowId, revision, models).put("confirmed", true)

    /** Use this model names the revision the try tested; a workflow that changed since is refused. */
    fun useModelAction(workflowId: String, result: TestResult): JSONObject =
        estimateParams(workflowId, result.revision, result.models)

    /** A model name the desktop takes: no spaces or quotes, at most 200 characters. */
    fun validModel(model: String) = Regex("[^\\s\"'\\\\]{1,200}").matches(model)

    fun parseTests(json: JSONObject) = WorkflowTests(json.optString("workflow_id"), json.optString("revision"), json.optBoolean("exists"),
        json.optInt("cases"), json.optString("error"),
        json.objects("model_nodes").map(::triedModel).filter { it.node.isNotBlank() },
        json.objects("providers").map { TestProvider(it.optString("id"), it.texts("models")) }.filter { it.id.isNotBlank() },
        json.optBoolean("running"), json.optJSONObject("result")?.let(::testResult)?.takeIf { it.id.isNotBlank() },
        json.optString("message"))

    fun parseEstimate(json: JSONObject) = TestEstimate(json.optString("workflow_id"), json.optString("revision"),
        json.optJSONObject("current")?.optString("summary").orEmpty(), json.optJSONObject("current")?.optBoolean("reused") == true,
        json.optJSONObject("candidate")?.optString("label").orEmpty(), json.optJSONObject("candidate")?.optString("summary").orEmpty(),
        json.optString("total"))

    private fun triedModel(json: JSONObject) = TriedModel(json.optString("node"), json.optString("provider"), json.optString("model"))

    private fun testResult(json: JSONObject) = TestResult(json.optString("result_id"), json.optString("status"), json.optString("revision"),
        json.optBoolean("stale"), json.optString("tried"), json.objects("models").map(::triedModel), json.optInt("passed"), json.optInt("total"),
        json.optJSONObject("comparison")?.let { c ->
            fun side(o: JSONObject?) = CaseSide(o?.optString("status").orEmpty(), o?.optString("cost").orEmpty(), o?.optString("time").orEmpty())
            fun total(o: JSONObject?) = SideTotal(o?.optInt("passed") ?: 0, o?.optInt("cases") ?: 0, o?.optString("cost").orEmpty(), o?.optString("time").orEmpty())
            val totals = c.optJSONObject("totals")
            ModelComparison(c.optString("sentence"), c.optBoolean("baseline_reused"),
                c.objects("rows").map { ComparisonRow(it.optString("case_id"), it.optString("name").ifBlank { it.optString("case_id") },
                    it.optString("change"), side(it.optJSONObject("current")), side(it.optJSONObject("candidate"))) }.filter { it.caseId.isNotBlank() },
                total(totals?.optJSONObject("current")), total(totals?.optJSONObject("candidate")))
        },
        json.optJSONObject("use")?.optBoolean("allowed") == true, json.optJSONObject("use")?.optString("reason").orEmpty())

    /** Passed, Failed, Could not run, Skipped, Waiting or Running for one side of a case. */
    fun caseLabel(status: String) = when (status) {
        "passed" -> "Passed"; "failed" -> "Failed"; "error" -> "Could not run"; "skipped" -> "Skipped"; "running" -> "Running"
        "", "pending" -> "Waiting"; else -> status.replaceFirstChar { it.uppercase() }
    }

    fun changeLabel(change: String) = when (change) { "fixed" -> "Fixed"; "broke" -> "Broke"; "changed" -> "Changed"; else -> "" }

    private fun trigger(json: JSONObject) = WorkflowTrigger(json.optString("trigger_id"), json.optString("kind"),
        json.optString("kind_label").ifBlank { json.optString("kind") }, json.optString("label"), json.optString("description"),
        json.optBoolean("enabled"), json.optString("next_run_at"),
        json.optJSONObject("last_result")?.let { TriggerResult(it.optString("status"), it.optString("at"), it.optString("message")) },
        json.optString("daily_limit").take(300), json.optBoolean("limit_reached"),
        // An older desktop sends no approval, so its triggers show as before.
        json.optString("approval").ifBlank { "approved" }, json.optString("approval_message"),
        // An older desktop sends no Time limit, so the line stays hidden.
        timeLimit = json.optString("time_limit").take(80))

    private fun input(json: JSONObject) = WorkflowInput(json.optString("name"), json.optString("label").ifBlank { json.optString("name") },
        json.optString("description"), json.optString("kind").ifBlank { "json" }, json.optBoolean("required"), json.optBoolean("secret"),
        json.optBoolean("has_default"), if (json.has("default")) json.opt("default") else null,
        json.optJSONArray("choices")?.let { a -> (0 until a.length()).map { a.opt(it) } }.orEmpty(),
        json.optNumber("minimum"), json.optNumber("maximum"), json.optString("json_type").ifBlank { "value" },
        json.texts("accept"), json.optLong("max_size"))

    private fun JSONObject.optNumber(key: String): Double? = (opt(key) as? Number)?.toDouble()

    /** True for a full workflow view rather than an action's short answer. */
    fun isView(json: JSONObject) = json.has("nodes") && json.has("workflow_id")

    /**
     * Polling reads are safe to drop: a reconnect may reach an older desktop that would reject
     * an unknown op and close the session before it reports what it supports.
     */
    fun dropQueuedReads(state: JSONObject): Boolean {
        return Outbox.drop(state) { it.optString("type") == "desktop.read" && isWorkflowOp(it.optJSONObject("payload")?.optString("op").orEmpty()) }
    }

    private fun summary(json: JSONObject) = WorkflowSummary(json.optString("workflow_id"), json.optString("name"), json.optString("description"),
        json.optString("project_name"), json.optString("status"), json.optString("updated_at"),
        json.optJSONObject("latest_run")?.let(::run)?.takeIf { it.id.isNotBlank() },
        // A desktop that names text inputs always sends the list, even when it is empty.
        if (json.has("text_inputs")) json.objects("text_inputs").map { WorkflowTextInput(it.optString("name"), it.optString("label").ifBlank { it.optString("name") }) }
            .filter { it.name.isNotBlank() }.distinctBy { it.name } else null,
        // Likewise for file inputs; an older desktop sends neither, and can't take a shared file.
        if (json.has("file_inputs")) json.objects("file_inputs").map { WorkflowFileInput(it.optString("name"), it.optString("label").ifBlank { it.optString("name") },
            it.texts("accept"), it.optLong("max_size")) }.filter { it.name.isNotBlank() }.distinctBy { it.name } else null)

    private fun run(json: JSONObject) = WorkflowRun(json.optString("run_id"), json.optString("status"), json.optString("started_at"),
        json.optString("finished_at"), json.optString("trigger"), json.optString("summary"), json.optString("error"))

    private fun JSONObject?.texts(key: String): List<String> =
        this?.optJSONArray(key)?.let { a -> (0 until a.length()).map { a.optString(it) }.filter { it.isNotBlank() } }.orEmpty()
    private fun JSONObject.objects(key: String): List<JSONObject> = optJSONArray(key)?.let { a -> (0 until a.length()).mapNotNull { a.optJSONObject(it) } }.orEmpty()
}

/** [error] says why a failed, interrupted or stopped run ended; blank for other runs and from a desktop with Raticode 0.6.1 or older. */
data class WorkflowRun(val id: String, val status: String, val startedAt: String, val finishedAt: String, val trigger: String, val summary: String,
                       val error: String = "") {
    val active get() = Workflows.active(status)
}
data class WorkflowSummary(val id: String, val name: String, val description: String, val project: String, val status: String,
                           val updatedAt: String, val latestRun: WorkflowRun?,
                           /** Plain text inputs shared text can fill; null from a desktop that doesn't say. */
                           val textInputs: List<WorkflowTextInput>? = null,
                           /** File inputs a shared file can fill; null from a desktop that doesn't say. */
                           val fileInputs: List<WorkflowFileInput>? = null) {
    val active get() = Workflows.active(status) || latestRun?.active == true
}
/** A workflow input that takes plain text, by name and the label the Run form shows. */
data class WorkflowTextInput(val name: String, val label: String)
/** A workflow input that takes a file: the extensions and MIME patterns it accepts, and its size limit in bytes. */
data class WorkflowFileInput(val name: String, val label: String, val accept: List<String>, val maxSize: Long)
/** [triggers] is null from a desktop that can't pause triggers from the phone. */
data class WorkflowList(val items: List<WorkflowSummary>, val earlier: Int, val triggers: TriggerPause? = null)
/**
 * Whether every trigger on the desktop is paused, and how many triggers there are and are on. [dailyLimit] is the
 * computer-wide daily limit with today's use as the desktop words it, blank without one; [limitReached] means triggered
 * starts are skipped until midnight.
 */
data class TriggerPause(val paused: Boolean, val pausedAt: String, val count: Int, val enabled: Int,
                        val dailyLimit: String = "", val limitReached: Boolean = false)
/**
 * One file that differs from a trigger's approved version: its path in the project, what it is (Workflow, Prompt file,
 * Script, Child workflow), how it changed, and its unified diff. [diffAvailable] is false for a binary or very large file.
 */
data class TriggerChangedFile(val path: String, val kind: String, val change: String, val diff: String = "",
                              val diffAvailable: Boolean = true, val truncated: Boolean = false,
                              /**
                               * `cut` or `unreadable` when the phone couldn't show all of the change, `unpinned` for an MCP server
                               * whose package has no exact version; blank otherwise.
                               */
                              val unseen: String = "",
                              /** The file's path inside the project, to open it with the file viewer; blank outside it. */
                              val openPath: String = "",
                              /** The desktop's warning about this entry, such as an MCP server's unpinned package; blank for none. */
                              val warning: String = "")
/**
 * Review changes for one trigger. [fingerprint] names the version shown, which Approve this version approves.
 * [projectId] names the project the files open in; [unseenHidden] counts changed files past the list's limit.
 */
data class TriggerChanges(val triggerId: String, val approval: String, val message: String, val fingerprint: String,
                          val approvedAt: String, val files: List<TriggerChangedFile>, val truncated: Boolean,
                          val projectId: String = "", val unseenHidden: Int = 0) {
    /** Files whose change the phone couldn't show in full: approving them asks a second time. */
    val unseen get() = files.filter { it.unseen.isNotBlank() }
    val needsUnseenConfirm get() = unseen.isNotEmpty() || unseenHidden > 0
}
/** A trigger's latest entry in its history: passed, failed, missed, skipped, running and so on. */
data class TriggerResult(val status: String, val at: String, val message: String)
/**
 * One saved trigger of a workflow. The desktop never sends a webhook's address or token. [dailyLimit] is the trigger's
 * daily limit with today's use, blank without one; [limitReached] means its starts are skipped until midnight.
 * [timeLimit] is its Time limit as the desktop words it ("Time limit: 6 h" or "No time limit"), set on the computer.
 */
data class WorkflowTrigger(val id: String, val kind: String, val kindLabel: String, val label: String, val description: String,
                           val enabled: Boolean, val nextRunAt: String, val lastResult: TriggerResult?,
                           val dailyLimit: String = "", val limitReached: Boolean = false,
                           /** approved, changed, unapproved or missing. [approvalMessage] is blank unless it waits for approval. */
                           val approval: String = "approved", val approvalMessage: String = "",
                           val timeLimit: String = "") {
    val title get() = label.ifBlank { kindLabel.ifBlank { "Trigger" } }
    /** The trigger skips its starts until the user approves the workflow as it is now. */
    val needsApproval get() = approvalMessage.isNotBlank()
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
/** The attachment Ask Rem about this failure puts in Rem's message box; nothing is sent until the user sends it. */
data class RunFailure(val runId: String, val nodeId: String, val name: String, val text: String)
/** One Ask Rem about this failure read: waiting while [failure] and [error] are both empty. */
data class RunFailureRead(val key: String, val failure: RunFailure? = null, val error: String = "")
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
                         val minimum: Double?, val maximum: Double?, val jsonType: String,
                         /** For a file input: the kinds it accepts and its size limit. Only a shared file can fill one. */
                         val accept: List<String> = emptyList(), val maxSize: Long = 0) {
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
    const val FILE_NEEDED = "Share a file to Raticode from another app, then choose Run a workflow."

    fun values(inputs: List<WorkflowInput>, drafts: Map<String, String>): Pair<JSONObject, Map<String, String>> {
        val values = JSONObject()
        val errors = mutableMapOf<String, String>()
        for (input in inputs) {
            val draft = drafts[input.name].orEmpty()
            if (input.kind == "boolean") { values.put(input.name, draft == "true"); continue }
            // A file input's draft is the shared file's name; the file itself goes as an upload, not a value.
            if (input.kind == "file") {
                if (draft.isEmpty() && input.needed) errors[input.name] = FILE_NEEDED
                continue
            }
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

/** One step and the provider and model it runs with: what it uses now, or the candidate to try. */
data class TriedModel(val node: String, val provider: String, val model: String)
/** A provider on the desktop by its Rattish ID, with the models it lists. */
data class TestProvider(val id: String, val models: List<String>)
/** One side of a case: its status and the cost and time as the desktop words them. */
data class CaseSide(val status: String, val cost: String, val time: String)
data class SideTotal(val passed: Int, val cases: Int, val cost: String, val time: String)
/** One case side by side: current model against the candidate. [change] is fixed, broke, changed, same or pending. */
data class ComparisonRow(val caseId: String, val name: String, val change: String, val current: CaseSide, val candidate: CaseSide)
data class ModelComparison(val sentence: String, val baselineReused: Boolean, val rows: List<ComparisonRow>,
                           val current: SideTotal, val candidate: SideTotal)
/**
 * The newest test result. [tried] names the models a try used; blank for the workflow's own run. [useAllowed] says whether
 * Use this model can write them now, and [useReason] why not.
 */
data class TestResult(val id: String, val status: String, val revision: String, val stale: Boolean, val tried: String,
                      val models: List<TriedModel>, val passed: Int, val total: Int, val comparison: ModelComparison?,
                      val useAllowed: Boolean, val useReason: String) {
    val running get() = status == "running"
}
/** One workflow's tests for Try another model; [message] is an action's answer, such as a started try. */
data class WorkflowTests(val workflowId: String, val revision: String, val exists: Boolean, val cases: Int, val error: String,
                         val modelNodes: List<TriedModel>, val providers: List<TestProvider>, val running: Boolean,
                         val result: TestResult?, val message: String = "")
/** The expected cost of both models; [reused] means the current model's side reuses an earlier result and costs nothing. */
data class TestEstimate(val workflowId: String, val revision: String, val currentSummary: String, val reused: Boolean,
                        val candidateLabel: String, val candidateSummary: String, val total: String)
