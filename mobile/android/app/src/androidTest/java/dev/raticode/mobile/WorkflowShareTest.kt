package dev.raticode.mobile

import android.content.Intent
import androidx.compose.runtime.*
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.core.content.pm.ShortcutManagerCompat
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.WorkflowTextInput
import dev.raticode.mobile.live.Workflows
import dev.raticode.mobile.share.WorkflowShare
import dev.raticode.mobile.share.WorkflowShareInbox
import dev.raticode.mobile.share.WorkflowShareStore
import dev.raticode.mobile.ui.RaticodeTheme
import dev.raticode.mobile.ui.SHARE_NO_TEXT_INPUTS
import dev.raticode.mobile.ui.SHARE_UPDATE_DESKTOP
import dev.raticode.mobile.ui.ShareWorkflowPicker
import dev.raticode.mobile.ui.SharedRun
import dev.raticode.mobile.ui.WorkflowDetail
import dev.raticode.mobile.ui.WorkflowScreenState
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

/**
 * Run a workflow, the share target: the picker lists only workflows with a text input, the chosen input is asked for
 * when there is more than one, and the workflow's own Run sheet opens with the shared text in it. Nothing is sent
 * until Run. The direct-share shortcuts keep the three most recent workflows and leave Rem's shortcut alone.
 */
class WorkflowShareTest {
    @get:Rule val compose = createComposeRule()
    private val app get() = InstrumentationRegistry.getInstrumentation().targetContext
    private val online = WorkflowScreenState(true, false, null, null)
    private val link = "Bread recipe\n\nhttps://example.com/bread"

    @After fun cleanUp() { WorkflowShareStore.clear(app); WorkflowShareInbox.clear() }

    private fun list(textInputs: Boolean = true) = Workflows.parseList(JSONObject().put("earlier", 0).put("workflows", JSONArray()
        .put(JSONObject().put("workflow_id", "wf-link").put("name", "Summarize a link").put("project_name", "Notes").put("status", "idle")
            .apply { if (textInputs) put("text_inputs", JSONArray().put(JSONObject().put("name", "url").put("label", "Link"))) })
        .put(JSONObject().put("workflow_id", "wf-numbers").put("name", "Count things").put("project_name", "Notes").put("status", "idle")
            .apply { if (textInputs) put("text_inputs", JSONArray()) })))

    private fun workflow(textInputs: List<Pair<String, String>>) = Workflows.parse(JSONObject("""{
        "workflow_id": "wf-link", "name": "Summarize a link", "project_name": "Notes", "status": "idle",
        "nodes": [{"node_id": "fetch", "label": "Fetch the page", "kind": "tool"}], "edges": [], "runs": [],
        "revision": "sha256:fixture", "preview": {"blocking": [], "destructive": [], "warnings": []},
        "controls": {"run": {"allowed": true, "reason": ""}, "stop": {"allowed": false, "reason": ""}, "resume": {"allowed": false, "reason": ""}}}""")
        .put("inputs", JSONArray().apply {
            textInputs.forEach { (name, label) -> put(JSONObject().put("name", name).put("label", label).put("kind", "text").put("required", true)) }
            put(JSONObject().put("name", "length").put("label", "Length").put("kind", "choice").put("has_default", true)
                .put("choices", JSONArray().put("short").put("long")).put("default", "short"))
            put(JSONObject().put("name", "key").put("label", "Key").put("kind", "text").put("secret", true).put("has_default", true))
        }))

    @Test fun thePickerListsOnlyWorkflowsWithATextInput() {
        val picked = mutableListOf<String>()
        var cancelled = 0
        val share = WorkflowShare.pending(1, "Bread recipe", "https://example.com/bread", hasFiles = false, shortcutId = null)
        compose.setContent { RaticodeTheme { ShareWorkflowPicker(share, list(), online, {}, { cancelled++ }) { picked += it } } }
        compose.onNodeWithTag("workflowShareText").assert(hasText(link))
        compose.onNodeWithText(WorkflowShare.CAUTION).assertIsDisplayed()
        compose.onNodeWithText("Text input: Link").assertIsDisplayed()
        compose.onAllNodesWithTag("shareWorkflow:wf-numbers").assertCountEquals(0)
        compose.onNodeWithTag("shareWorkflow:wf-link").performClick()
        compose.onNodeWithTag("workflowShareCancel").performClick()
        assertEquals(listOf("wf-link"), picked)
        assertEquals(1, cancelled)
    }

    @Test fun anOlderDesktopOrNoTextInputsSaysWhatToDo() {
        val share = WorkflowShare.pending(1, null, "notes", hasFiles = false, shortcutId = null)
        var older by mutableStateOf(true)
        compose.setContent { RaticodeTheme { ShareWorkflowPicker(share, if (older) list(textInputs = false) else
            Workflows.parseList(JSONObject().put("workflows", JSONArray().put(JSONObject().put("workflow_id", "a").put("text_inputs", JSONArray())))),
            online, {}, {}) {} } }
        compose.onNodeWithText(SHARE_UPDATE_DESKTOP).assertIsDisplayed()
        older = false
        compose.onNodeWithText(SHARE_NO_TEXT_INPUTS).assertIsDisplayed()
    }

    @Test fun theRunSheetOpensWithTheSharedTextAndSendsOnlyOnRun() {
        val sent = mutableListOf<JSONObject>()
        val shared = SharedRun(link, emptyList(), listOf(WorkflowTextInput("url", "Link")), "url", onChoose = {}, onDiscard = {})
        compose.setContent { RaticodeTheme {
            WorkflowDetail(workflow(listOf("url" to "Link")), null, online, {}, {}, {}, onRunWithInputs = { sent += it }, shared = shared) { _, _ -> }
        } }
        compose.onNodeWithTag("workflowRunSheet").assertIsDisplayed()
        compose.onNodeWithTag("runInput:url").assert(hasText(link))
        compose.onNodeWithTag("workflowRunShareNote").assert(hasText("What you shared is in Link.", substring = true))
            .assert(hasText("instructions you didn't write", substring = true))
        assertTrue(sent.isEmpty())
        compose.onNodeWithTag("workflowRunSend").performScrollTo().performClick()
        val values = sent.single()
        assertEquals(link, values.getString("url"))
        assertEquals("short", values.getString("length"))
        // A password input keeps its default on the computer; shared text never goes in it.
        assertFalse(values.has("key"))
    }

    @Test fun twoTextInputsAskWhichOneThenFillIt() {
        val sent = mutableListOf<JSONObject>()
        var input by mutableStateOf<String?>(null)
        val choices = listOf(WorkflowTextInput("url", "Link"), WorkflowTextInput("notes", "Notes"))
        compose.setContent { RaticodeTheme {
            WorkflowDetail(workflow(listOf("url" to "Link", "notes" to "Notes")), null, online, {}, {}, {}, onRunWithInputs = { sent += it },
                shared = SharedRun(link, emptyList(), choices, input, onChoose = { input = it }, onDiscard = {})) { _, _ -> }
        } }
        compose.onNodeWithTag("workflowShareInputs").assertIsDisplayed()
        compose.onAllNodesWithTag("workflowRunSheet").assertCountEquals(0)
        compose.onNodeWithTag("shareInput:notes").performClick()
        compose.onNodeWithTag("workflowShareInputConfirm").performClick()
        assertEquals("notes", input)
        compose.onNodeWithTag("runInput:notes").assert(hasText(link))
        compose.onNodeWithTag("runInput:url").performScrollTo().performTextInput("https://example.org")
        compose.onNodeWithTag("workflowRunSend").performScrollTo().performClick()
        assertEquals(link, sent.single().getString("notes"))
    }

    @Test fun theShareTargetKeepsTheTextInMemoryAndOpensWorkflows() {
        WorkflowShareInbox.clear()
        app.startActivity(Intent(Intent.ACTION_SEND).setClassName(app, "dev.raticode.mobile.share.WorkflowShareActivity").setType("text/plain")
            .putExtra(Intent.EXTRA_SUBJECT, "Bread recipe").putExtra(Intent.EXTRA_TEXT, "https://example.com/bread")
            .putExtra(Intent.EXTRA_SHORTCUT_ID, WorkflowShare.shortcutId("wf-link")).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        val deadline = System.currentTimeMillis() + 5_000
        while (WorkflowShareInbox.pending == null && System.currentTimeMillis() < deadline) Thread.sleep(50)
        val pending = WorkflowShareInbox.pending!!
        assertEquals(link, pending.text)
        assertEquals("wf-link", pending.workflowId)
    }

    @Test fun theThreeMostRecentWorkflowsBecomeDirectShareShortcuts() {
        listOf("a", "b", "c", "d").forEach { WorkflowShareStore.used(app, "wf-$it", "Workflow $it") }
        val ours = ShortcutManagerCompat.getDynamicShortcuts(app).filter { it.id.startsWith(WorkflowShare.SHORTCUT_PREFIX) }
        assertEquals(setOf("wf-d", "wf-c", "wf-b").map(WorkflowShare::shortcutId).toSet(), ours.map { it.id }.toSet())
        ours.forEach { assertEquals(setOf(WorkflowShare.CATEGORY), it.categories) }
        assertEquals("Workflow d", ours.first { it.rank == 0 }.shortLabel.toString())
        WorkflowShareStore.rememberInput(app, "wf-d", "url")
        assertEquals("url", WorkflowShareStore.input(app, "wf-d"))
        WorkflowShareStore.clear(app)
        assertTrue(ShortcutManagerCompat.getDynamicShortcuts(app).none { it.id.startsWith(WorkflowShare.SHORTCUT_PREFIX) })
        assertNull(WorkflowShareStore.input(app, "wf-d"))
    }
}
