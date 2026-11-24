package dev.raticode.mobile.live

import dev.raticode.mobile.ui.TryDraft
import dev.raticode.mobile.ui.sideText
import dev.raticode.mobile.ui.totalText
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

/** Try another model on a workflow's tests: the desktop's views parse leniently and the phone sends only what it showed. */
class WorkflowTestsTest {
    // As the desktop's workflow_tests.tests_view words it after a try finished.
    private val tests = JSONObject("""{
        "workflow_id": "wf-1", "revision": "sha256:new", "exists": true, "cases": 2, "error": "",
        "model_nodes": [{"node": "write", "provider": "codex", "model": "gpt-5.5"}, {"node": "", "provider": "x", "model": "y"}],
        "providers": [{"id": "claude-code", "models": ["claude-sonnet-5-5", ""]}, {"id": "", "models": []}],
        "running": false, "future": 1,
        "result": {"result_id": "try-1", "status": "passed", "revision": "sha256:new", "stale": false,
            "tried": "write: claude-code claude-sonnet-5-5",
            "models": [{"node": "write", "provider": "claude-code", "model": "claude-sonnet-5-5"}], "passed": 2, "total": 2,
            "comparison": {"sentence": "Current model passed 1/2 for $0.02; the candidate passed 2/2 for $0.01. Cases: 1 fixed.",
                "baseline_reused": false,
                "rows": [{"case_id": "cats", "name": "Cats", "change": "same",
                          "current": {"status": "passed", "cost": "$0.01", "time": "1.2s"}, "candidate": {"status": "passed", "cost": "$0.0050", "time": "0.8s"}},
                         {"case_id": "dogs", "name": "", "change": "fixed",
                          "current": {"status": "failed", "cost": "$0.01", "time": "0.9s"}, "candidate": {"status": "passed", "cost": "$0.0050", "time": "0.7s"}},
                         {"case_id": "", "name": "No ID"}],
                "totals": {"current": {"passed": 1, "cases": 2, "cost": "$0.02", "time": "2.1s"},
                           "candidate": {"passed": 2, "cases": 2, "cost": "$0.01", "time": "1.5s"}}},
            "use": {"allowed": true, "reason": ""}}}""")

    @Test fun testsViewParsesTheSideBySideResult() {
        val view = Workflows.parseTests(tests)
        assertEquals(listOf(TriedModel("write", "codex", "gpt-5.5")), view.modelNodes)
        assertEquals(listOf(TestProvider("claude-code", listOf("claude-sonnet-5-5"))), view.providers)
        val result = view.result!!
        assertEquals("write: claude-code claude-sonnet-5-5", result.tried)
        assertTrue(result.useAllowed && !result.running && !result.stale)
        val comparison = result.comparison!!
        assertEquals(listOf("cats", "dogs"), comparison.rows.map { it.caseId })
        val dogs = comparison.rows[1]
        assertEquals("dogs", dogs.name)
        assertEquals("Fixed", Workflows.changeLabel(dogs.change))
        assertEquals("Failed · $0.01 · 0.9s", sideText(dogs.current))
        assertEquals("Passed · $0.0050 · 0.7s", sideText(dogs.candidate))
        assertEquals("1/2 passed · $0.02 · 2.1s", totalText(comparison.current))
        assertEquals("2/2 passed · $0.01 · 1.5s", totalText(comparison.candidate))
        assertEquals("Waiting", Workflows.caseLabel("pending"))
        assertEquals("Could not run", Workflows.caseLabel("error"))
    }

    @Test fun aWorkflowsOwnResultHasNoComparisonOrUse() {
        val own = Workflows.parseTests(JSONObject("""{"workflow_id": "wf-1", "exists": true, "cases": 1,
            "result": {"result_id": "r1", "status": "failed", "passed": 0, "total": 1,
                       "use": {"allowed": false, "reason": "The newest result didn't try another model."}}}"""))
        assertNull(own.result!!.comparison)
        assertEquals("", own.result!!.tried)
        assertFalse(own.result!!.useAllowed)
        assertNull(Workflows.parseTests(JSONObject("""{"workflow_id": "wf-1", "result": null}""")).result)
    }

    @Test fun estimateShowsBothModels() {
        val estimate = Workflows.parseEstimate(JSONObject("""{"workflow_id": "wf-1", "revision": "sha256:new",
            "current": {"summary": "Reuses result r1 from today: ${'$'}0.", "reused": true},
            "candidate": {"label": "write: claude-code claude-sonnet-5-5", "summary": "About ${'$'}0.01 for 2 cases (2 model calls)."},
            "total": "$0.01"}"""))
        assertTrue(estimate.reused)
        assertEquals("write: claude-code claude-sonnet-5-5", estimate.candidateLabel)
        assertEquals("$0.01", estimate.total)
    }

    @Test fun actionsCarryTheShownRevisionAndTryIsConfirmed() {
        val models = listOf(TriedModel("write", "claude-code", "claude-sonnet-5-5"))
        val estimate = Workflows.estimateParams("wf-1", "sha256:new", models)
        assertEquals("sha256:new", estimate.getString("expected_revision"))
        assertEquals("claude-sonnet-5-5", estimate.getJSONArray("models").getJSONObject(0).getString("model"))
        assertFalse(estimate.has("confirmed"))
        assertTrue(Workflows.tryAction("wf-1", "sha256:new", models).getBoolean("confirmed"))
        val result = Workflows.parseTests(tests).result!!
        val use = Workflows.useModelAction("wf-1", result)
        assertEquals("sha256:new", use.getString("expected_revision"))
        assertEquals("write", use.getJSONArray("models").getJSONObject(0).getString("node"))
        assertFalse(use.has("confirmed"))
    }

    @Test fun triesAndUsesRefreshTheTestsViewAndEachCandidateHasItsOwnEstimate() {
        val params = Workflows.testsParams("wf-1")
        val key = DesktopViews.key(Workflows.TESTS, params)
        assertEquals("workflows.tests:wf-1", key)
        val models = listOf(TriedModel("write", "claude-code", "claude-sonnet-5-5"))
        assertEquals(key, DesktopViews.key(Workflows.TRY, Workflows.tryAction("wf-1", "sha256:new", models)))
        assertEquals(key, DesktopViews.key(Workflows.USE_MODEL, Workflows.estimateParams("wf-1", "sha256:new", models)))
        val one = DesktopViews.key(Workflows.ESTIMATE, Workflows.estimateParams("wf-1", "sha256:new", models))
        val other = DesktopViews.key(Workflows.ESTIMATE, Workflows.estimateParams("wf-1", "sha256:new", listOf(TriedModel("write", "codex", "gpt-6"))))
        assertNotEquals(one, other)
        assertTrue(DesktopViews.parse(key, tests) is WorkflowTests)
        assertTrue(DesktopViews.parse(one, JSONObject().put("workflow_id", "wf-1")) is TestEstimate)
        assertTrue(Workflows.TEST_READS.all { it in DesktopViews.READS } && Workflows.TEST_ACTIONS.all { it in DesktopViews.ACTIONS })
        assertTrue(Workflows.TEST_READS.none { it in DesktopViews.ACTIONS })
    }

    @Test fun onlyADesktopThatSaysSoGetsTheTestOps() {
        val overview = { json: String -> DesktopViews.parse("overview", JSONObject(json)) as DesktopOverview }
        assertTrue(overview("""{"workflows": true, "workflow_tests": true}""").workflowTests)
        assertFalse(overview("""{"workflows": true}""").workflowTests)
        assertFalse(overview("""{"workflows": false, "workflow_tests": true}""").workflowTests)
    }

    @Test fun theFormSendsOnlyAModelTheDesktopTakes() {
        assertEquals(listOf(TriedModel("write", "opencode", "llama3:8b")), TryDraft("write", "opencode", " llama3:8b ").models())
        assertNull(TryDraft("write", "codex", "gpt 5").models())
        assertNull(TryDraft("write", "codex", "").models())
        assertNull(TryDraft("", "codex", "gpt-5.5").models())
        assertNull(TryDraft("write", "codex", "x\"y").models())
    }
}
