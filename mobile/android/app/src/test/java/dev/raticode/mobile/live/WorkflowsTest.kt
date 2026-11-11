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
        assertEquals(WorkflowPreview(), Workflows.parse(JSONObject().put("workflow_id", "x")).preview)
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
        Workflows.ACTIONS.forEach { assertEquals(read, DesktopViews.key(it, JSONObject().put("workflow_id", "wf-1").put("run_id", "r9"))) }
        assertEquals(Workflows.LIST, DesktopViews.key(Workflows.LIST, JSONObject()))
        assertTrue(Workflows.READS.all { it in DesktopViews.READS } && Workflows.ACTIONS.all { it in DesktopViews.ACTIONS })
        assertTrue(Workflows.READS.none { it in DesktopViews.ACTIONS })
        // The phone has no op that edits a workflow.
        assertTrue(DesktopViews.OPERATIONS.filter { it.startsWith("workflows.") }.toSet() == setOf("workflows.list", "workflows.read", "workflows.run", "workflows.stop", "workflows.resume"))
        assertTrue(DesktopViews.parse("overview", JSONObject().put("workflows", true)).let { it is DesktopOverview && it.workflows })
        assertFalse((DesktopViews.parse("overview", JSONObject()) as DesktopOverview).workflows)
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
}
