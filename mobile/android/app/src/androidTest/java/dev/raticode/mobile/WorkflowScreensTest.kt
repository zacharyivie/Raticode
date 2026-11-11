package dev.raticode.mobile

import android.graphics.Bitmap
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.requiredSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalWindowInfo
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.Workflows
import dev.raticode.mobile.ui.RaticodeTheme
import dev.raticode.mobile.ui.WorkflowDetail
import dev.raticode.mobile.ui.WorkflowList
import dev.raticode.mobile.ui.WorkflowScreenState
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import java.io.File

/**
 * Workflow screens on phone-sized windows: small (320×568 dp), common (360×740 dp) and large (412×915 dp),
 * at 1.3× font in dark mode too. Data matches the desktop's workflow projection; no desktop runs anything.
 */
class WorkflowScreensTest {
    @get:Rule val compose = createComposeRule()

    private val now = java.time.OffsetDateTime.now()
    private fun ago(minutes: Long) = now.minusMinutes(minutes).toString()

    private fun workflow(running: Boolean, resume: Boolean = false) = Workflows.parse(JSONObject("""{
        "workflow_id": "wf-report", "name": "Nightly research report", "project_name": "Notes", "status": "${if (running) "running" else "failed"}",
        "nodes": [{"node_id": "start", "label": "Collect sources", "kind": "tool"}, {"node_id": "summarize", "label": "Summarize findings", "kind": "agent"},
                  {"node_id": "check", "label": "Check citations", "kind": "agent"}, {"node_id": "approve", "label": "Approve publishing", "kind": "approval"},
                  {"node_id": "publish", "label": "Publish report", "kind": "tool"}],
        "edges": [{"from": "start", "to": "summarize"}, {"from": "start", "to": "check"}, {"from": "summarize", "to": "approve"},
                  {"from": "check", "to": "approve"}, {"from": "approve", "to": "summarize", "label": "revise"}, {"from": "approve", "to": "publish", "label": "approved"}],
        "runs": [{"run_id": "r3", "status": "${if (running) "running" else "failed"}", "started_at": "${ago(4)}", "finished_at": "${if (running) "" else ago(1)}", "trigger": "phone",
                  "summary": "${if (running) "" else "Check citations failed: two sources were unreachable."}"},
                 {"run_id": "r2", "status": "succeeded", "started_at": "${ago(1500)}", "finished_at": "${ago(1490)}", "trigger": "schedule", "summary": "Published"},
                 {"run_id": "r1", "status": "stopped", "started_at": "${ago(3000)}", "finished_at": "${ago(2995)}", "trigger": "manual"}],
        "earlier_runs": 12,
        "run": {"run_id": "r3", "status": "${if (running) "running" else "failed"}", "error": "", "nodes": [
            {"node_id": "start", "status": "succeeded", "started_at": "${ago(4)}", "finished_at": "${ago(3)}"},
            {"node_id": "summarize", "status": "${if (running) "running" else "succeeded"}", "started_at": "${ago(3)}", "detail": "Drafting section 2 of 4"},
            {"node_id": "check", "status": "${if (running) "succeeded" else "failed"}", "started_at": "${ago(3)}", "finished_at": "${ago(2)}"},
            {"node_id": "approve", "status": "pending"}, {"node_id": "publish", "status": "pending"}]},
        "revision": "sha256:fixture", "preview": {"blocking": [], "destructive": ["Publishes the report to the shared folder"], "warnings": []},
        "controls": {"run": {"allowed": ${!running}, "reason": "${if (running) "A run is already going." else ""}"}, "stop": {"allowed": $running, "reason": ""},
                     "resume": {"allowed": $resume, "reason": "${if (resume) "" else "Resuming Rattish workflow runs is not supported yet. Start a new run instead."}"}}}"""))

    private val list = Workflows.parseList(JSONObject("""{"earlier": 0, "workflows": [
        {"workflow_id": "wf-report", "name": "Nightly research report", "project_name": "Notes", "status": "running",
         "latest_run": {"run_id": "r3", "status": "running", "started_at": "${ago(4)}", "trigger": "phone"}},
        {"workflow_id": "wf-triage", "name": "Triage new issues and label them by area", "description": "Reads new GitHub issues and suggests labels.", "project_name": "Raticode", "status": "failed",
         "latest_run": {"run_id": "t9", "status": "failed", "started_at": "${ago(90)}", "finished_at": "${ago(88)}", "trigger": "schedule"}},
        {"workflow_id": "wf-new", "name": "Weekly digest", "project_name": "Notes", "status": "idle", "latest_run": null}]}"""))

    private val sizes = listOf("small" to (320 to 568), "common" to (360 to 740), "large" to (412 to 915))

    @Composable
    private fun Phone(width: Int, height: Int, fontScale: Float = 1f, dark: Boolean = false, content: @Composable () -> Unit) {
        // The shared emulator's window may be smaller than the phone being checked. Lowering the density
        // keeps the layout at the exact dp size; only the pixels drawn get smaller.
        val window = LocalWindowInfo.current.containerSize
        val density = minOf(LocalDensity.current.density, window.width.toFloat() / width, window.height.toFloat() / height)
        CompositionLocalProvider(LocalDensity provides Density(density, fontScale)) {
            RaticodeTheme(darkTheme = dark) {
                Box(Modifier.requiredSize(width.dp, height.dp).background(MaterialTheme.colorScheme.background)) { content() }
            }
        }
    }

    private fun shot(name: String) {
        // A sheet or dialog is a second root drawn over the screen; capture the topmost.
        val bitmap = compose.onAllNodes(isRoot()).onLast().captureToImage().asAndroidBitmap()
        val app = InstrumentationRegistry.getInstrumentation().targetContext
        val file = File(app.getExternalFilesDir(null), "workflow-$name.png")
        file.outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
        // The app and its files are removed after the run; keep a copy where adb can pull it.
        InstrumentationRegistry.getInstrumentation().uiAutomation.executeShellCommand("cp ${file.absolutePath} /data/local/tmp/raticode-workflow-$name.png").close()
    }

    @Test fun runningWorkflowFitsEveryPhoneSizeAndOnlyOffersStop() {
        var size by mutableStateOf(sizes.first().second)
        var font by mutableFloatStateOf(1f)
        var dark by mutableStateOf(false)
        val actions = mutableListOf<Pair<String, String?>>()
        compose.setContent {
            Phone(size.first, size.second, font, dark) {
                WorkflowDetail(workflow(running = true), null, WorkflowScreenState(true, false, null, null), {}, {}, {}) { a, r -> actions += a to r }
            }
        }
        for ((name, dims) in sizes) {
            size = dims; compose.waitForIdle()
            compose.onNodeWithTag("workflowStop").assertIsDisplayed().assertIsEnabled()
            compose.onAllNodesWithTag("workflowRun").assertCountEquals(0)
            compose.onAllNodesWithText("Running", useUnmergedTree = true).onFirst().assertExists()
            compose.onNodeWithTag("workflowGraph").assertIsDisplayed()
            compose.onNodeWithTag("workflowNode:summarize").assertExists()
            shot("running-$name")
        }
        font = 1.3f; dark = true; size = sizes.first().second; compose.waitForIdle()
        compose.onNodeWithTag("workflowStop").assertIsDisplayed()
        shot("running-small-large-font-dark")
        compose.onNodeWithTag("workflowStop").performClick()
        compose.onNodeWithText("Stop this run?").assertIsDisplayed()
        compose.onNodeWithText("Cancel").performClick()
        assertTrue(actions.isEmpty())
        compose.onNodeWithTag("workflowStop").performClick()
        compose.onNodeWithText("Stop run").performClick()
        assertEquals(listOf("workflows.stop" to "r3"), actions)
    }

    @Test fun failedRunExplainsWhyResumeIsOffAndRunsNeedConfirmation() {
        val actions = mutableListOf<Pair<String, String?>>()
        compose.setContent {
            Phone(320, 568) { WorkflowDetail(workflow(running = false), null, WorkflowScreenState(true, false, null, null), {}, {}, {}) { a, r -> actions += a to r } }
        }
        compose.onNodeWithTag("workflowResume").assertIsNotEnabled()
        compose.onNodeWithText("Resume: Resuming Rattish workflow runs is not supported yet. Start a new run instead.").assertExists()
        compose.onNodeWithText("Check citations failed: two sources were unreachable.").assertExists()
        shot("failed-small")
        compose.onNodeWithTag("workflowRun").assertIsEnabled().performClick()
        compose.onNodeWithText("Run Nightly research report?").assertIsDisplayed()
        compose.onNodeWithText("This run can change things").assertIsDisplayed()
        compose.onNodeWithText("• Publishes the report to the shared folder").assertIsDisplayed()
        shot("run-confirm-small")
        compose.onNode(hasText("Run") and hasAnyAncestor(isDialog())).performClick()
        assertEquals(listOf("workflows.run" to null), actions)
    }

    @Test fun resumeIsOfferedOnlyWhenTheDesktopAllowsIt() {
        val actions = mutableListOf<Pair<String, String?>>()
        compose.setContent {
            Phone(360, 740) { WorkflowDetail(workflow(running = false, resume = true), null, WorkflowScreenState(true, false, null, null), {}, {}, {}) { a, r -> actions += a to r } }
        }
        compose.onAllNodesWithTag("workflowReason").assertCountEquals(0)
        compose.onNodeWithTag("workflowResume").assertIsEnabled().performClick()
        compose.onNode(hasText("Resume") and hasAnyAncestor(isDialog())).performClick()
        assertEquals(listOf("workflows.resume" to "r3"), actions)
    }

    @Test fun offlineAndBusyStatesTurnControlsOff() {
        var state by mutableStateOf(WorkflowScreenState(false, false, null, null))
        compose.setContent { Phone(360, 740) { WorkflowDetail(workflow(running = false), null, state, {}, {}, {}) { _, _ -> } } }
        compose.onNodeWithText("Offline. Showing what your desktop last sent. Run, stop and resume return when you reconnect.").assertIsDisplayed()
        compose.onNodeWithTag("workflowRun").assertIsNotEnabled()
        shot("offline-common")
        state = WorkflowScreenState(true, true, null, null); compose.waitForIdle()
        compose.onNodeWithText("Sending to your desktop…").assertIsDisplayed()
        compose.onNodeWithTag("workflowRun").assertIsNotEnabled()
        state = WorkflowScreenState(true, false, "This workflow needs inputs. Start it on your computer.", null); compose.waitForIdle()
        compose.onNodeWithText("This workflow needs inputs. Start it on your computer.").assertIsDisplayed()
        compose.onNodeWithText("Try again").assertIsDisplayed()
        compose.onNodeWithTag("workflowRun").assertIsEnabled()
        state = WorkflowScreenState(true, false, null, "Stop requested"); compose.waitForIdle()
        compose.onNodeWithText("Stop requested").assertIsDisplayed()
    }

    @Test fun stepsRunsAndNodeDetailsAreReachable() {
        val selected = mutableListOf<String?>()
        compose.setContent {
            Phone(320, 568) { WorkflowDetail(workflow(running = true), null, WorkflowScreenState(true, false, null, null), { selected += it }, {}, {}) { _, _ -> } }
        }
        compose.onNodeWithTag("workflowNode:summarize").performScrollTo().performClick()
        compose.onNodeWithText("Comes after").assertExists()
        compose.onNodeWithText("Approve publishing (revise)").assertExists()
        shot("node-sheet-small")
        pressBack()
        compose.onNodeWithText("Steps").performClick()
        compose.onNodeWithTag("workflowSteps").assertIsDisplayed()
        compose.onNodeWithText("Drafting section 2 of 4").assertExists()
        shot("steps-small")
        compose.onNodeWithText("Runs (15)").performClick()
        compose.onNodeWithText("12 earlier runs are on your desktop.").performScrollTo().assertIsDisplayed()
        shot("runs-small")
        compose.onNodeWithText("Published").performClick()
        assertEquals(listOf<String?>("r2"), selected)
        compose.onNodeWithTag("workflowGraph").assertIsDisplayed()
    }

    @Test fun listFiltersAndOpensWorkflows() {
        val opened = mutableListOf<String>()
        var size by mutableStateOf(sizes.first().second)
        compose.setContent { Phone(size.first, size.second) { WorkflowList(list, WorkflowScreenState(true, false, null, null), {}) { opened += it } } }
        for ((name, dims) in sizes) { size = dims; compose.waitForIdle(); shot("list-$name") }
        compose.onNodeWithText("Running (1)").performClick()
        compose.onAllNodesWithTag("workflow:wf-triage").assertCountEquals(0)
        compose.onNodeWithText("Failed (1)").performClick()
        compose.onNodeWithTag("workflow:wf-triage").assertIsDisplayed().performClick()
        compose.onNodeWithText("All (3)").performClick()
        compose.onNodeWithText("Not run yet", useUnmergedTree = true).assertExists()
        assertEquals(listOf("wf-triage"), opened)
    }

    @Test fun emptyListSaysWhereToCreateOne() {
        compose.setContent { Phone(320, 568) { WorkflowList(Workflows.parseList(JSONObject()), WorkflowScreenState(true, false, null, null), {}) {} } }
        compose.onNodeWithText("No workflows yet. Create one in Raticode on your computer, then it shows up here.").assertIsDisplayed()
    }

    private fun pressBack() = InstrumentationRegistry.getInstrumentation().sendKeyDownUpSync(android.view.KeyEvent.KEYCODE_BACK).also { compose.waitForIdle() }
}
