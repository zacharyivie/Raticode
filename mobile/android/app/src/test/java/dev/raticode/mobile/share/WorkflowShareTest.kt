package dev.raticode.mobile.share

import dev.raticode.mobile.live.WorkflowFileInput
import dev.raticode.mobile.live.WorkflowInput
import dev.raticode.mobile.live.WorkflowTextInput
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class WorkflowShareTest {
    private fun input(name: String, kind: String = "text", secret: Boolean = false) =
        WorkflowInput(name, name.uppercase(), "", kind, false, secret, false, null, emptyList(), null, null, "value")

    @Test fun aBrowserShareKeepsTitleAndLinkAndNamesTheShortcutsWorkflow() {
        val share = WorkflowShare.pending(1, "Bread recipe", "https://example.com/bread", hasFiles = false, shortcutId = "run-workflow:wf-1")
        assertEquals("Bread recipe\n\nhttps://example.com/bread", share.text)
        assertTrue(share.notices.isEmpty())
        assertEquals("wf-1", share.workflowId)
        assertNull(share.target)
        // The share sheet's own entry, or another app's shortcut, names no workflow.
        assertNull(WorkflowShare.pending(2, null, "x", false, null).workflowId)
        assertNull(WorkflowShare.pending(3, null, "x", false, "rem-chat").workflowId)
        assertNull(WorkflowShare.pending(4, null, "x", false, "run-workflow:").workflowId)
    }

    @Test fun filesAreLeftOutAndTextIsCleanedAndCapped() {
        val files = WorkflowShare.pending(1, null, null, hasFiles = true, shortcutId = null)
        assertEquals("", files.text)
        assertEquals(listOf(WorkflowShare.FILES_REFUSED), files.notices)
        assertEquals(listOf(WorkflowShare.NOTHING), WorkflowShare.pending(2, null, "  ", false, null).notices)
        val long = WorkflowShare.pending(3, null, "pay‮exe " + "a".repeat(ShareIntake.MAX_TEXT), false, null)
        assertEquals(ShareIntake.MAX_TEXT, long.text.length)
        assertTrue(long.text.startsWith("payexe "))
        assertEquals(listOf(WorkflowShare.SHORTENED), long.notices)
    }

    @Test fun onlyPlainTextInputsTakeSharedText() {
        val inputs = listOf(input("topic"), input("count", "integer"), input("token", secret = true), input("mode", "choice"), input("notes"))
        assertEquals(listOf(WorkflowTextInput("topic", "TOPIC"), WorkflowTextInput("notes", "NOTES")), WorkflowShare.textInputs(inputs))
        assertEquals(emptyList<WorkflowTextInput>(), WorkflowShare.textInputs(null))
    }

    @Test fun theRememberedInputWinsThenTheOnlyOneOtherwiseAsk() {
        val two = listOf(WorkflowTextInput("topic", "Topic"), WorkflowTextInput("notes", "Notes"))
        assertEquals("notes", WorkflowShare.inputFor(two, "notes"))
        assertNull(WorkflowShare.inputFor(two, null))
        // An input the workflow no longer has is asked again.
        assertNull(WorkflowShare.inputFor(two, "gone"))
        assertEquals("topic", WorkflowShare.inputFor(two.take(1), "gone"))
        assertNull(WorkflowShare.inputFor(emptyList(), "topic"))
    }

    @Test fun rememberedInputsArePerWorkflowAndBounded() {
        var saved = WorkflowShare.parseInputs(null)
        saved = WorkflowShare.remember(saved, "a", "topic")
        saved = WorkflowShare.remember(saved, "b", "url")
        saved = WorkflowShare.remember(saved, "a", "notes")
        val parsed = WorkflowShare.parseInputs(WorkflowShare.encodeInputs(saved))
        assertEquals(listOf("a" to "notes", "b" to "url"), parsed)
        repeat(WorkflowShare.MAX_REMEMBERED + 5) { saved = WorkflowShare.remember(saved, "w$it", "x") }
        assertEquals(WorkflowShare.MAX_REMEMBERED, saved.size)
        assertEquals(emptyList<Pair<String, String>>(), WorkflowShare.parseInputs("not json"))
    }

    @Test fun theThreeMostRecentWorkflowsBecomeShortcuts() {
        var recent = emptyList<WorkflowShare.Recent>()
        listOf("a", "b", "c", "b", "d").forEach { recent = WorkflowShare.used(recent, WorkflowShare.Recent(it, "Workflow $it")) }
        assertEquals(listOf("d", "b", "c"), recent.map { it.id })
        assertEquals(recent, WorkflowShare.parseRecent(WorkflowShare.encodeRecent(recent)))
        assertEquals(emptyList<WorkflowShare.Recent>(), WorkflowShare.parseRecent("{"))
        assertEquals("run-workflow:d", WorkflowShare.shortcutId("d"))
        assertEquals("d", WorkflowShare.workflowOf(WorkflowShare.shortcutId("d")))
        assertEquals("Workflow", WorkflowShare.shortLabel("  "))
        assertEquals(25, WorkflowShare.shortLabel("x".repeat(40)).length)
        assertEquals("Run Summarize link", WorkflowShare.longLabel("Summarize link"))
    }

    @Test fun oneSharedFileIsKeptForAFileInputAndTheTextIsLeftOut() {
        val file = WorkflowShare.SharedFile("content://photos/1", "scan.pdf", 2048, "application/pdf")
        val share = WorkflowShare.pending(1, "Subject", "Some text", hasFiles = true, shortcutId = null, files = listOf(file))
        assertEquals(file, share.file)
        assertEquals("", share.text)
        assertEquals(listOf(WorkflowShare.TEXT_WITH_FILE), share.notices)
        val many = WorkflowShare.pending(2, null, null, hasFiles = true, shortcutId = null, files = listOf(file, file.copy(uri = "content://photos/2")))
        assertNull(many.file)
        assertEquals(listOf(WorkflowShare.MANY_FILES), many.notices)
    }

    @Test fun aSharedFileFitsOnlyFileInputsThatAcceptItsKindAndSize() {
        val file = WorkflowShare.SharedFile("content://photos/1", "Photo.JPG", 3 * 1024 * 1024, "image/jpeg")
        val inputs = listOf(
            WorkflowFileInput("scan", "Scan", listOf(".pdf"), 20L * 1024 * 1024),
            WorkflowFileInput("photo", "Photo", listOf("image/*"), 20L * 1024 * 1024),
            WorkflowFileInput("small", "Small", emptyList(), 1024 * 1024),
            WorkflowFileInput("any", "Any", emptyList(), 20L * 1024 * 1024),
        )
        assertEquals(listOf(WorkflowTextInput("photo", "Photo"), WorkflowTextInput("any", "Any")), WorkflowShare.fileInputs(inputs, file))
        assertEquals("Photo.JPG is 3 MB, more than the 1 MB Small takes.", WorkflowShare.fileProblem(inputs[2], file))
        assertEquals("Scan takes .pdf, not Photo.JPG.", WorkflowShare.fileProblem(inputs[0], file))
        assertTrue(WorkflowShare.accepts(listOf(".jpg"), "Photo.JPG", ""))
        assertFalse(WorkflowShare.accepts(listOf("image/*"), "photo", ""))
        assertEquals("1.5 KB", WorkflowShare.size(1536))
        assertEquals("1 byte", WorkflowShare.size(1))
    }

    @Test fun raticodesOwnFilesCantBeSharedBackIntoAWorkflowRun() {
        val own = "dev.raticode.mobile"
        val providers = mapOf("com.example.files" to "com.example", "media" to "com.android.providers.media", "renamed.cache" to own)
        fun readable(scheme: String?, authority: String?) = WorkflowShare.readable(scheme, authority, own) { providers[it] }
        assertTrue(readable("content", "com.example.files"))
        assertTrue(readable("content", "media"))
        // A user-ID prefix opens Raticode's own provider on Android, so it must be refused like the plain authority.
        assertFalse(readable("content", "dev.raticode.mobile.opened"))
        assertFalse(readable("content", "0@dev.raticode.mobile.opened"))
        assertFalse(readable("content", "10@x@dev.raticode.mobile.opened"))
        assertFalse(readable("content", "dev.raticode.mobile"))
        // A provider Raticode serves under another name is refused through the package lookup.
        assertFalse(readable("content", "renamed.cache"))
        assertFalse(readable("file", "com.example.files"))
        assertFalse(readable("content", null))
        assertFalse(readable("content", ""))
    }
}
