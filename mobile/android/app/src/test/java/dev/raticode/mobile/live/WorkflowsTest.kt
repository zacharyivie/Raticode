package dev.raticode.mobile.live

import dev.raticode.mobile.ui.durationText
import dev.raticode.mobile.ui.runLine
import dev.raticode.mobile.ui.runSummary
import dev.raticode.mobile.ui.workflowAction
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.time.Instant

/** Workflow views: lenient parsing, controls the desktop decides, a phone-sized graph layout and safe reconnects. */
class WorkflowsTest {
    private fun node(id: String) = WorkflowNode(id, id.uppercase(), "agent")
    private fun edge(from: String, to: String, label: String = "") = WorkflowEdge(from, to, label)

    private val detail = JSONObject("""{
        "workflow_id": "wf-1", "name": "Nightly report", "description": "Builds the report", "project_name": "Notes",
        "status": "running", "updated_at": "2026-10-31T19:00:00+00:00", "future_field": {"ignored": true},
        "latest_run": {"run_id": "r2", "status": "running", "started_at": "2026-10-31T19:00:00+00:00", "finished_at": "", "trigger": "phone", "summary": ""},
        "nodes": [{"node_id": "fetch", "label": "Fetch", "kind": "tool"}, {"node_id": "write", "label": "Write", "kind": "agent"},
                  {"node_id": "fetch", "label": "Duplicate", "kind": "tool"}, {"node_id": "", "label": "No ID"}],
        "edges": [{"from": "fetch", "to": "write", "label": "ok"}],
        "runs": [{"run_id": "r2", "status": "running", "started_at": "2026-10-31T19:00:00+00:00"},
                 {"run_id": "r1", "status": "failed", "started_at": "2026-10-30T19:00:00+00:00", "finished_at": "2026-10-30T19:02:00+00:00", "summary": "Fetch failed"}],
        "earlier_runs": 3,
        "run": {"run_id": "r2", "status": "running", "error": "", "nodes": [{"node_id": "fetch", "status": "succeeded"}, {"node_id": "write", "status": "running", "detail": "Drafting"}]},
        "controls": {"run": {"allowed": false, "reason": "A run is already going."}, "stop": {"allowed": true, "reason": ""},
                     "resume": {"allowed": false, "reason": "Resuming Rattish workflow runs is not supported yet. Start a new run instead."}},
        "revision": "sha256:abc", "preview": {"blocking": [], "destructive": ["Deletes old report drafts"], "warnings": ["", "Uses the network"]},
        "digest": "abc"}""")

    @Test fun detailParsesLenientlyAndKeepsDesktopDecisions() {
        val workflow = Workflows.parse(detail)
        assertEquals("Nightly report", workflow.summary.name)
        assertEquals(listOf("fetch", "write"), workflow.nodes.map { it.id })
        assertEquals("Fetch", workflow.nodes.first().label)
        assertEquals(3, workflow.earlierRuns)
        assertEquals("r2", workflow.activeRun?.id)
        assertEquals("r2", workflow.shownRun?.id)
        assertEquals("Drafting", workflow.run!!.nodes.last().detail)
        assertFalse(workflow.runControl.allowed)
        assertTrue(workflow.stopControl.allowed)
        assertEquals("Resuming Rattish workflow runs is not supported yet. Start a new run instead.", workflow.resumeControl.reason)
        assertTrue(Workflows.isView(detail))
        assertFalse(Workflows.isView(JSONObject().put("workflow_id", "wf-1").put("run_id", "r3").put("message", "Started")))
    }

    @Test fun runNamesTheRevisionShownAndOnlyStopAndResumeNameARun() {
        val workflow = Workflows.parse(detail)
        assertEquals(WorkflowPreview(emptyList(), listOf("Deletes old report drafts"), listOf("Uses the network")), workflow.preview)
        fun fields(params: JSONObject) = params.keys().asSequence().associateWith { params.getString(it) }
        assertEquals(mapOf("workflow_id" to "wf-1", "expected_revision" to "sha256:abc"), fields(workflowAction(workflow, "workflows.run", null)))
        assertEquals(mapOf("workflow_id" to "wf-1", "run_id" to "r2"), fields(workflowAction(workflow, "workflows.stop", "r2")))
        assertEquals(mapOf("workflow_id" to "wf-1", "run_id" to "r1"), fields(workflowAction(workflow, "workflows.resume", "r1")))
        // Run again names the run only; the desktop keeps its inputs, so none are sent even when the form has values.
        assertEquals(mapOf("workflow_id" to "wf-1", "run_id" to "r1"),
            fields(workflowAction(workflow, Workflows.RERUN, "r1", JSONObject().put("target", "prod"))))
        assertEquals(WorkflowPreview(), Workflows.parse(JSONObject().put("workflow_id", "x")).preview)
    }

    @Test fun runAgainShowsOnlyWhenTheDesktopOffersIt() {
        // An older desktop sends no rerun control, so the phone never offers the op.
        assertNull(Workflows.parse(detail).rerunControl)
        val withRerun = JSONObject(detail.toString())
        withRerun.getJSONObject("controls").put("rerun", JSONObject().put("allowed", false)
            .put("reason", "This run's input token is a password, which Raticode doesn't save, and this phone can't fill it in. Use Run again on your computer."))
        val control = Workflows.parse(withRerun).rerunControl!!
        assertFalse(control.allowed)
        assertTrue(control.reason.contains("token is a password"))
        withRerun.getJSONObject("controls").put("rerun", JSONObject().put("allowed", true).put("reason", ""))
        assertTrue(Workflows.parse(withRerun).rerunControl!!.allowed)
    }

    /** Produced by the desktop's workflow projection with a fake runner and disposable test workflows. */
    private val desktop = JSONObject(javaClass.classLoader!!.getResource("desktop-workflows.json")!!.readText())

    @Test fun theDesktopsRealProjectionsParse() {
        assertTrue((DesktopViews.parse("overview", desktop.getJSONObject("overview")) as DesktopOverview).workflows)
        val idle = DesktopViews.parse(DesktopViews.key(Workflows.READ, JSONObject().put("workflow_id", "phone-check")), desktop.getJSONObject("read.idle")) as Workflow
        assertEquals(listOf("build", "lint", "report"), idle.nodes.map { it.id })
        assertTrue(idle.revision.startsWith("sha256:"))
        assertTrue(idle.runControl.allowed && !idle.stopControl.allowed && !idle.resumeControl.allowed)
        assertTrue(idle.preview.destructive.isNotEmpty())
        // The "needs" edge skips a layer; the graph still draws it pointing down.
        val layout = GraphLayout.of(idle.nodes, idle.edges)
        assertEquals(listOf("build", "report", "lint"), layout.order)
        assertTrue(layout.backEdges.isEmpty())
        val running = Workflows.parse(desktop.getJSONObject("read.running"))
        assertNotNull(running.activeRun)
        assertEquals("running", running.run!!.nodes.first { it.nodeId == "build" }.status)
        assertTrue(running.stopControl.allowed)
        val stopped = Workflows.parse(desktop.getJSONObject("read.stopped"))
        assertNull(stopped.activeRun)
        assertEquals("stopped", stopped.run!!.run.status)
        assertTrue(Workflows.parseList(desktop.getJSONObject("list.running")).items.single().active)
        // Action answers are short status lines, never mistaken for a view.
        listOf("run.answer", "stop.answer").forEach { assertFalse(it, Workflows.isView(desktop.getJSONObject(it))) }
        assertEquals("Stopping this run.", desktop.getJSONObject("stop.answer").getString("message"))
        assertEquals("unavailable", desktop.getJSONObject("resume.refused").getString("code"))
        assertEquals("conflict", desktop.getJSONObject("run.stale").getString("code"))
    }

    @Test fun theRunFormComesFromTheDesktopsInputSchema() {
        val workflow = Workflows.parse(desktop.getJSONObject("read.inputs"))
        // A desktop that reads the phone's form allows Run for a workflow whose inputs have no defaults.
        assertTrue(workflow.runControl.allowed)
        val inputs = workflow.inputs!!.associateBy { it.name }
        assertEquals(mapOf("count" to "integer", "dry" to "boolean", "extra" to "json", "mode" to "choice", "target" to "text", "token" to "text"),
            inputs.mapValues { it.value.kind })
        assertEquals("Target", inputs.getValue("target").label)
        assertEquals("Where to ship", inputs.getValue("target").description)
        assertTrue(inputs.getValue("target").needed)
        assertFalse(inputs.getValue("count").needed)
        assertEquals(listOf("fast", "safe"), inputs.getValue("mode").choices)
        assertEquals("object", inputs.getValue("extra").jsonType)
        assertEquals(1.0, inputs.getValue("count").minimum)
        // A secret input's default stays on the desktop: the field starts blank and is masked.
        assertTrue(inputs.getValue("token").secret && inputs.getValue("token").hasDefault)
        assertNull(inputs.getValue("token").default)

        val form: List<WorkflowInput> = workflow.inputs
        val drafts = RunForm.drafts(form)
        assertEquals(mapOf("count" to "3", "dry" to "false", "extra" to "", "mode" to "1", "target" to "", "token" to ""), drafts)
        assertEquals(mapOf("target" to "Required"), RunForm.values(form, drafts).second)

        val wrong = RunForm.values(form, drafts + mapOf("target" to "prod", "count" to "12", "extra" to "[1]"))
        assertEquals(mapOf("count" to "Must be at most 9", "extra" to RunForm.jsonHint(inputs.getValue("extra"))), wrong.second)
        assertEquals("Enter a whole number", RunForm.values(form, drafts + mapOf("target" to "x", "count" to "3.5")).second["count"])
        assertEquals(RunForm.jsonHint(inputs.getValue("extra")), RunForm.values(form, drafts + mapOf("target" to "x", "extra" to "{bad")).second["extra"])

        val (values, errors) = RunForm.values(form, drafts + mapOf("target" to "staging", "count" to " 4 ", "dry" to "true",
            "mode" to "0", "extra" to "{\"tags\": [\"a\"]}"))
        assertTrue(errors.isEmpty())
        assertEquals(setOf("target", "count", "dry", "mode", "extra"), values.keys().asSequence().toSet())
        assertEquals("staging", values.getString("target"))
        assertTrue(values.getBoolean("dry"))
        assertEquals("fast", values.getString("mode"))
        assertEquals("""{"tags":["a"]}""", values.getJSONObject("extra").toString())
        // Blank optional fields are left out, so the workflow's default applies on the desktop.
        assertFalse(values.has("token"))
        assertEquals(4L, (values.get("count") as Number).toLong())

        val sent = workflowAction(workflow, "workflows.run", null, values)
        assertEquals(values.toString(), sent.getJSONObject("inputs").toString())
        assertEquals(workflow.revision, sent.getString("expected_revision"))
    }

    @Test fun numbersChoicesAndJsonValuesKeepTheirTypes() {
        fun input(kind: String, default: Any? = null, choices: List<Any?> = emptyList(), json: String = "value", required: Boolean = false) =
            WorkflowInput("v", "V", "", kind, required, false, default != null, default, choices, -1.5, null, json)
        assertEquals("2.5", RunForm.drafts(listOf(input("number", 2.5)))["v"])
        assertEquals("-0.25", RunForm.values(listOf(input("number")), mapOf("v" to "-0.25")).first.get("v").toString())
        assertEquals("Must be at least -1.5", RunForm.values(listOf(input("number")), mapOf("v" to "-2")).second["v"])
        assertEquals("Enter a number", RunForm.values(listOf(input("number")), mapOf("v" to "lots")).second["v"])
        // Choices may be numbers; the chosen value goes back as the desktop sent it.
        val numbers = input("choice", 2, listOf(1, 2, 3))
        assertEquals("1", RunForm.drafts(listOf(numbers))["v"])
        assertEquals(3, RunForm.values(listOf(numbers), mapOf("v" to "2")).first.get("v"))
        assertEquals("Choose an option", RunForm.values(listOf(input("choice", choices = listOf("a"))), mapOf("v" to "7")).second["v"])
        // JSON boxes show defaults as JSON and parse what was typed.
        assertEquals("\"hi\"", RunForm.drafts(listOf(input("json", "hi")))["v"])
        assertEquals("[1,2]", RunForm.drafts(listOf(input("json", JSONArray("[1, 2]"))))["v"])
        assertEquals("[1,2]", RunForm.values(listOf(input("json", json = "array")), mapOf("v" to " [1, 2] ")).first.get("v").toString())
        assertEquals(RunForm.jsonHint(input("json", json = "array")), RunForm.values(listOf(input("json", json = "array")), mapOf("v" to "{}")).second["v"])
        assertEquals(RunForm.jsonHint(input("json")), RunForm.values(listOf(input("json")), mapOf("v" to "[1] 2")).second["v"])
        // Text keeps spaces as typed; a required yes/no is always sent.
        assertEquals("  two words ", RunForm.values(listOf(input("text")), mapOf("v" to "  two words ")).first.get("v"))
        assertEquals(false, RunForm.values(listOf(input("boolean", required = true)), mapOf("v" to "")).first.get("v"))
    }

    @Test fun anOlderDesktopGetsNoInputsAndResumeNamesTheStepsItRunsAgain() {
        val old = Workflows.parse(detail)
        assertNull(old.inputs)
        assertFalse(workflowAction(old, "workflows.run", null, JSONObject().put("target", "x")).has("inputs"))
        val resumable = Workflows.parse(JSONObject(detail.toString()).put("inputs", JSONArray()).put("controls", JSONObject()
            .put("resume", JSONObject().put("allowed", true).put("reason", "").put("rerun_nodes", JSONArray().put("write").put("")))))
        assertEquals(emptyList<WorkflowInput>(), resumable.inputs)
        assertEquals(listOf("write"), resumable.resumeControl.rerunNodes)
        // A workflow without inputs sends none, even to a desktop that takes them.
        assertFalse(workflowAction(resumable, "workflows.run", null, JSONObject()).has("inputs"))
    }

    @Test fun missingControlsAreOffAndUnknownStatusesAreFinished() {
        val workflow = Workflows.parse(JSONObject().put("workflow_id", "wf").put("nodes", JSONArray())
            .put("runs", JSONArray().put(JSONObject().put("run_id", "r").put("status", "exploded"))))
        assertFalse(workflow.runControl.allowed || workflow.stopControl.allowed || workflow.resumeControl.allowed)
        assertNull(workflow.activeRun)
        assertNull(workflow.run)
        assertEquals("Exploded", Workflows.label("exploded"))
        listOf("queued", "running", "waiting", "stopping").forEach { assertTrue(it, Workflows.active(it)) }
        listOf("succeeded", "failed", "stopped", "skipped", "pending", "idle", "").forEach { assertFalse(it, Workflows.active(it)) }
    }

    @Test fun listParsesAndCountsActiveWork() {
        val list = Workflows.parseList(JSONObject().put("earlier", 2).put("workflows", JSONArray()
            .put(JSONObject().put("workflow_id", "a").put("name", "A").put("status", "idle").put("latest_run", JSONObject.NULL))
            .put(JSONObject().put("workflow_id", "b").put("name", "B").put("status", "succeeded")
                .put("latest_run", JSONObject().put("run_id", "r").put("status", "waiting")))))
        assertEquals(2, list.earlier)
        assertNull(list.items[0].latestRun)
        assertEquals(listOf(false, true), list.items.map { it.active })
        assertTrue(DesktopViews.parse(Workflows.LIST, JSONObject().put("workflows", JSONArray())) is WorkflowList)
    }

    @Test fun actionsRefreshTheWorkflowScreenWhicheverRunItShows() {
        val read = DesktopViews.key(Workflows.READ, JSONObject().put("workflow_id", "wf-1").put("run_id", "r1"))
        assertEquals("workflows.read:wf-1", read)
        (Workflows.ACTIONS - Workflows.PAUSE).forEach { assertEquals(read, DesktopViews.key(it, JSONObject().put("workflow_id", "wf-1").put("run_id", "r9"))) }
        assertEquals(Workflows.LIST, DesktopViews.key(Workflows.LIST, JSONObject()))
        // Pausing all triggers answers on the list, where its switch is.
        assertEquals(Workflows.LIST, DesktopViews.key(Workflows.PAUSE, Workflows.pauseAction(true)))
        assertTrue(Workflows.READS.all { it in DesktopViews.READS } && Workflows.ACTIONS.all { it in DesktopViews.ACTIONS })
        assertTrue(Workflows.READS.none { it in DesktopViews.ACTIONS })
        // The phone has no op that edits a workflow.
        assertTrue(DesktopViews.OPERATIONS.filter { it.startsWith("workflows.") }.toSet() == setOf("workflows.list", "workflows.read", "workflows.runs",
            "workflows.run", "workflows.stop", "workflows.resume", "workflows.rerun", "workflows.trigger", "workflows.triggers.pause"))
        assertTrue(DesktopViews.parse("overview", JSONObject().put("workflows", true)).let { it is DesktopOverview && it.workflows })
        assertFalse((DesktopViews.parse("overview", JSONObject()) as DesktopOverview).workflows)
    }

    @Test fun allRunsParseLenientlyAndEachFilterHasItsOwnView() {
        val json = JSONObject("""{
            "runs": [{"run_id": "r2", "status": "failed", "started_at": "2026-11-14T06:00:00+00:00", "finished_at": "2026-11-14T06:01:15+00:00",
                      "workflow_id": "wf-2", "workflow_name": "Release check", "project_name": "Notes", "started_by": "phone",
                      "tokens": "1,200", "cost": "$0.30", "summary": "Took 1 min 15 s", "future": 1},
                     {"run_id": "r1", "status": "running", "started_at": "2026-11-14T05:00:00+00:00", "workflow_id": "wf-1",
                      "workflow_name": "Nightly report", "started_by": "trigger"},
                     {"run_id": "", "workflow_id": "wf-1"}, {"run_id": "r0"}],
            "earlier": 4, "summary": "2 workflow runs: 1 failed, 1 running · est. $0.30",
            "workflows": [{"workflow_id": "wf-1", "name": "Nightly report"}, {"workflow_id": "", "name": "No ID"}]}""")
        val runs = DesktopViews.parse(DesktopViews.key(Workflows.RUNS, Workflows.runsParams("24h", "", "")), json) as AllWorkflowRuns
        assertEquals(listOf("r2", "r1"), runs.runs.map { it.run.id })
        assertEquals(listOf("Phone", "Trigger"), runs.runs.map { Workflows.startedByText(it.startedBy) })
        assertEquals("Manual", Workflows.startedByText(""))
        assertEquals(4, runs.earlier)
        assertEquals("2 workflow runs: 1 failed, 1 running · est. $0.30", runs.summary)
        assertEquals(listOf("wf-1" to "Nightly report"), runs.workflows)
        assertTrue(runs.runs[1].run.active)
        val line = dev.raticode.mobile.ui.allRunLine(runs.runs[0])
        assertTrue(line, line.endsWith("took 1 min 15 s · Phone · 1,200 tokens · $0.30"))

        val day = DesktopViews.key(Workflows.RUNS, Workflows.runsParams("24h", "", ""))
        assertEquals("workflows.runs:24h||", day)
        assertEquals("workflows.runs:7d|error|wf-1", DesktopViews.key(Workflows.RUNS, Workflows.runsParams("7d", "error", "wf-1")))
        assertFalse(Workflows.runsParams("", "", "").has("status"))
        // Only a desktop that also lists workflows says it answers the all-runs list.
        assertTrue((DesktopViews.parse("overview", JSONObject().put("workflows", true).put("workflow_runs", true)) as DesktopOverview).workflowRuns)
        assertFalse((DesktopViews.parse("overview", JSONObject().put("workflow_runs", true)) as DesktopOverview).workflowRuns)
        assertFalse((DesktopViews.parse("overview", JSONObject().put("workflows", true)) as DesktopOverview).workflowRuns)
    }

    @Test fun reconnectDropsOnlyQueuedWorkflowReads() {
        fun event(type: String, op: String) = JSONObject().put("type", type).put("payload", JSONObject().put("op", op).put("params", JSONObject())).toString()
        val state = JSONObject().put("outbox", JSONArray().put(event("desktop.read", "workflows.list")).put(event("desktop.read", "rem.home"))
            .put(event("desktop.action", "workflows.run")).put(event("desktop.read", "workflows.read"))
            .put(JSONObject().put("type", "sync.request").put("payload", JSONObject()).toString()))
        assertTrue(Workflows.dropQueuedReads(state))
        val left = (0 until state.getJSONArray("outbox").length()).map { JSONObject(state.getJSONArray("outbox").getString(it)) }
        assertEquals(listOf("rem.home", "workflows.run", ""), left.map { it.getJSONObject("payload").optString("op") })
        assertFalse(Workflows.dropQueuedReads(state))
    }

    @Test fun linearGraphStacksDownOneColumn() {
        val layout = GraphLayout.of(listOf(node("c"), node("a"), node("b")), listOf(edge("a", "b"), edge("b", "c")))
        assertEquals(listOf("a", "b", "c"), layout.order)
        assertEquals(3, layout.layers)
        assertEquals(1, layout.columns)
        assertTrue(layout.backEdges.isEmpty())
    }

    @Test fun branchesSitSideBySideAndJoinBelowBoth() {
        val layout = GraphLayout.of(listOf("start", "yes", "no", "end").map(::node),
            listOf(edge("start", "yes", "approved"), edge("start", "no", "rejected"), edge("yes", "end"), edge("no", "end"), edge("start", "end")))
        assertEquals(GraphSlot(0, 0), layout.slots["start"])
        assertEquals(setOf(GraphSlot(1, 0), GraphSlot(1, 1)), setOf(layout.slots["yes"], layout.slots["no"]))
        // The longest path decides the layer, so the direct edge still points down.
        assertEquals(2, layout.slots.getValue("end").layer)
        assertEquals(2, layout.columns)
    }

    @Test fun loopsBecomeBackEdgesAndBadEdgesAreIgnored() {
        val layout = GraphLayout.of(listOf("draft", "review", "publish", "orphan").map(::node),
            listOf(edge("draft", "review"), edge("review", "draft", "revise"), edge("review", "publish"), edge("review", "review"),
                edge("ghost", "draft"), edge("draft", "review")))
        assertEquals(setOf(edge("review", "draft", "revise")), layout.backEdges)
        assertEquals(listOf(0, 1, 2), listOf("draft", "review", "publish").map { layout.slots.getValue(it).layer })
        assertEquals(0, layout.slots.getValue("orphan").layer)
        assertEquals(3, layout.edges.size)
        // Every node is placed exactly once, in distinct slots.
        assertEquals(layout.slots.size, layout.slots.values.toSet().size)
    }

    @Test fun aCycleWithNoStartStillLaysOut() {
        val layout = GraphLayout.of(listOf("a", "b").map(::node), listOf(edge("a", "b"), edge("b", "a")))
        assertEquals(1, layout.backEdges.size)
        assertEquals(2, layout.layers)
        assertEquals(GraphLayout.of(emptyList(), emptyList()).let { it.layers to it.columns }, 0 to 0)
    }

    @Test fun durationsAndRunLinesReadPlainly() {
        val now = Instant.parse("2026-10-31T19:10:00Z")
        assertEquals("45 s", durationText("2026-10-31T19:00:00Z", "2026-10-31T19:00:45Z", now))
        assertEquals("2 min 5 s", durationText("2026-10-31T19:00:00Z", "2026-10-31T19:02:05Z", now))
        assertEquals("12 min", durationText("2026-10-31T19:00:00Z", "2026-10-31T19:12:30Z", now))
        assertEquals("1 h 3 min", durationText("2026-10-31T18:00:00Z", "2026-10-31T19:03:00Z", now))
        assertEquals("10 min", durationText("2026-10-31T19:00:00Z", "", now))
        assertEquals("", durationText("", "", now))
        assertEquals("", durationText("yesterday", "", now))
        assertTrue(runLine(WorkflowRun("r", "succeeded", "", "", "phone", "")) == "Started from the phone")
        assertEquals("", runSummary(WorkflowRun("r", "running", "", "", "", "Running")))
        assertEquals("", runSummary(WorkflowRun("r", "stopped", "", "", "", "Took 1 s")))
        assertEquals("Check failed: 2 sources were unreachable", runSummary(WorkflowRun("r", "failed", "", "", "", "Check failed: 2 sources were unreachable")))
    }

    @Test fun triggersParseLenientlyAndOnlyNewerDesktopsSendThem() {
        val json = JSONObject(detail.toString()).put("triggers_paused", true).put("triggers", JSONArray("""[
            {"trigger_id": "trg_1", "kind": "cron", "kind_label": "Schedule", "label": "Weekday digest", "description": "0 9 * * 1-5 (UTC)",
             "enabled": true, "next_run_at": "2026-11-16T09:00:00+00:00", "future": 1,
             "last_result": {"status": "skipped", "at": "2026-11-14T09:00:00+00:00", "message": "Skipped because all triggers were paused."}},
            {"trigger_id": "trg_2", "kind": "webhook", "kind_label": "Webhook", "label": "", "description": "Local webhook on your computer",
             "enabled": false, "next_run_at": "", "last_result": null},
            {"trigger_id": "", "kind": "file"}]"""))
        val workflow = Workflows.parse(json)
        val triggers = workflow.triggers!!
        assertEquals(listOf("trg_1", "trg_2"), triggers.map { it.id })
        assertTrue(workflow.triggersPaused)
        assertEquals("Weekday digest", triggers[0].title)
        assertEquals("Webhook", triggers[1].title)
        assertEquals("skipped", triggers[0].lastResult!!.status)
        assertNull(triggers[1].lastResult)
        assertEquals("Paused with all triggers", dev.raticode.mobile.ui.triggerNextText(triggers[0], paused = true))
        assertEquals("Off", dev.raticode.mobile.ui.triggerNextText(triggers[1], paused = false))
        assertEquals("On: runs when its webhook is called", dev.raticode.mobile.ui.triggerNextText(triggers[1].copy(enabled = true), paused = false))
        assertEquals("Skipped", Workflows.triggerResultLabel("skipped"))
        assertEquals("Succeeded", Workflows.triggerResultLabel("passed"))
        // An older desktop sends no list, so the phone shows no Triggers tab.
        assertNull(Workflows.parse(detail).triggers)
        assertFalse(Workflows.parse(detail).triggersPaused)
    }

    @Test fun turningATriggerOnCarriesTheConfirmationAndOffNeedsNone() {
        val workflow = Workflows.parse(JSONObject(detail.toString()).put("triggers", JSONArray().put(JSONObject().put("trigger_id", "trg_1").put("kind", "cron"))))
        val trigger = workflow.triggers!!.single()
        fun fields(params: JSONObject) = params.keys().asSequence().associateWith { params.get(it) }
        assertEquals(mapOf("workflow_id" to "wf-1", "trigger_id" to "trg_1", "enabled" to true, "confirmed" to true),
            fields(Workflows.triggerAction(workflow, trigger, true)))
        assertEquals(mapOf("workflow_id" to "wf-1", "trigger_id" to "trg_1", "enabled" to false), fields(Workflows.triggerAction(workflow, trigger, false)))
        assertEquals(mapOf("paused" to true), fields(Workflows.pauseAction(true)))
        assertEquals(mapOf("paused" to false, "confirmed" to true), fields(Workflows.pauseAction(false)))
    }

    @Test fun theListCarriesThePauseSwitchAndItsAnswerNeverReplacesTheList() {
        val list = Workflows.parseList(JSONObject("""{"workflows": [], "earlier": 0,
            "triggers": {"paused": true, "paused_at": "2026-11-14T09:00:00+00:00", "count": 3, "enabled": 2}}"""))
        assertEquals(TriggerPause(true, "2026-11-14T09:00:00+00:00", 3, 2), list.triggers)
        assertNull(Workflows.parseList(JSONObject("""{"workflows": [], "earlier": 0}""")).triggers)
        assertTrue(dev.raticode.mobile.ui.pauseText(list.triggers!!).startsWith("Paused since "))
        assertEquals("2 of 3 triggers on.", dev.raticode.mobile.ui.pauseText(list.triggers.copy(paused = false)))
        assertEquals("No triggers yet. Add them to a workflow on your computer.", dev.raticode.mobile.ui.pauseText(TriggerPause(false, "", 0, 0)))
        assertTrue(Workflows.isPauseAnswer(JSONObject().put("paused", false).put("count", 3).put("message", "Triggers resumed")))
        assertFalse(Workflows.isPauseAnswer(JSONObject("""{"workflows": [], "earlier": 0, "triggers": {"paused": true}}""")))
        val overview = DesktopViews.parse("overview", JSONObject().put("workflows", true).put("workflow_triggers", true)) as DesktopOverview
        assertTrue(overview.workflowTriggers)
        assertFalse((DesktopViews.parse("overview", JSONObject().put("workflow_triggers", true)) as DesktopOverview).workflowTriggers)
    }
}
