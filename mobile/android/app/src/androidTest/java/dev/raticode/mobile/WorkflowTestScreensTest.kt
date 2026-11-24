package dev.raticode.mobile

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.requiredSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalWindowInfo
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.live.TestEstimate
import dev.raticode.mobile.live.TestResult
import dev.raticode.mobile.live.TriedModel
import dev.raticode.mobile.live.Workflows
import dev.raticode.mobile.ui.RaticodeTheme
import dev.raticode.mobile.ui.WorkflowScreenState
import dev.raticode.mobile.ui.WorkflowTestsView
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

/** Try another model on a small phone (320×568 dp): both costs first, the case table, and Use this model after asking. */
class WorkflowTestScreensTest {
    @get:Rule val compose = createComposeRule()

    private fun tests(withResult: Boolean) = Workflows.parseTests(JSONObject("""{
        "workflow_id": "wf-1", "revision": "sha256:new", "exists": true, "cases": 2,
        "model_nodes": [{"node": "write", "provider": "codex", "model": "gpt-5.5"}],
        "providers": [{"id": "codex", "models": ["gpt-5.5", "gpt-5.5-mini"]}, {"id": "claude-code", "models": ["claude-sonnet-5-5"]}],
        "running": false,
        "result": ${if (!withResult) "null" else """{"result_id": "try-1", "status": "passed", "revision": "sha256:new", "stale": false,
            "tried": "write: claude-code claude-sonnet-5-5", "models": [{"node": "write", "provider": "claude-code", "model": "claude-sonnet-5-5"}],
            "passed": 2, "total": 2,
            "comparison": {"sentence": "Cases: 1 fixed.", "baseline_reused": false,
                "rows": [{"case_id": "dogs", "name": "Dogs", "change": "fixed",
                          "current": {"status": "failed", "cost": "$0.01", "time": "0.9s"}, "candidate": {"status": "passed", "cost": "$0.0050", "time": "0.7s"}}],
                "totals": {"current": {"passed": 0, "cases": 1, "cost": "$0.01", "time": "0.9s"},
                           "candidate": {"passed": 1, "cases": 1, "cost": "$0.0050", "time": "0.7s"}}},
            "use": {"allowed": true, "reason": ""}}"""}}"""))

    @Composable
    private fun Phone(content: @Composable () -> Unit) {
        val window = LocalWindowInfo.current.containerSize
        val density = minOf(LocalDensity.current.density, window.width.toFloat() / 320, window.height.toFloat() / 568)
        CompositionLocalProvider(LocalDensity provides Density(density, 1f)) {
            RaticodeTheme { Box(Modifier.requiredSize(320.dp, 568.dp).background(MaterialTheme.colorScheme.background)) { content() } }
        }
    }

    @Test fun checksTheCostOfBothModelsBeforeRunningBoth() {
        val estimated = mutableListOf<List<TriedModel>>()
        val tried = mutableListOf<List<TriedModel>>()
        var estimate by mutableStateOf<TestEstimate?>(null)
        var checking by mutableStateOf(false)
        compose.setContent {
            Phone {
                WorkflowTestsView(tests(withResult = false), WorkflowScreenState(true, false, null, null), estimate, checking, onRetry = {},
                    onEstimate = { estimated += it; checking = true }, onChange = { checking = false }, onTry = { tried += it }, onUse = {})
            }
        }
        compose.onNodeWithTag("tryModelCheck").assertIsNotEnabled()
        compose.onNodeWithText("claude-code").performClick()
        compose.onNodeWithText("claude-sonnet-5-5").performClick()
        compose.onNodeWithTag("tryModelCheck").performScrollTo().assertIsEnabled().performClick()
        assertEquals(listOf(listOf(TriedModel("write", "claude-code", "claude-sonnet-5-5"))), estimated)
        // Run both waits for the desktop's estimate of both models.
        compose.onNodeWithTag("tryModelRun").assertIsNotEnabled()
        estimate = TestEstimate("wf-1", "sha256:new", "About $0.02 for 2 cases (2 model calls).", false,
            "write: claude-code claude-sonnet-5-5", "About $0.01 for 2 cases (2 model calls).", "$0.03")
        compose.onNodeWithText("About $0.02 for 2 cases (2 model calls).").assertExists()
        compose.onNodeWithText("About $0.01 for 2 cases (2 model calls).").assertExists()
        compose.onNodeWithText("Total: $0.03").assertExists()
        compose.onNodeWithTag("tryModelRun").performScrollTo().assertIsEnabled().performClick()
        assertEquals(estimated, tried)
    }

    @Test fun showsEachCaseSideBySideAndAsksBeforeUsingTheModel() {
        val used = mutableListOf<TestResult>()
        compose.setContent {
            Phone {
                WorkflowTestsView(tests(withResult = true), WorkflowScreenState(true, false, null, null), null, false, onRetry = {},
                    onEstimate = {}, onChange = {}, onTry = {}, onUse = { used += it })
            }
        }
        compose.onNodeWithText("Current model against write: claude-code claude-sonnet-5-5").assertExists()
        compose.onNodeWithContentDescription("Dogs: current Failed · $0.01 · 0.9s; candidate Passed · $0.0050 · 0.7s; Fixed").assertExists()
        compose.onNodeWithText("1/1 passed · $0.0050 · 0.7s").assertExists()
        compose.onNodeWithTag("useModel").performScrollTo().performClick()
        assertTrue(used.isEmpty())
        compose.onNodeWithTag("useModelConfirm").performClick()
        assertEquals(listOf("try-1"), used.map { it.id })
    }
}
