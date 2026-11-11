package dev.raticode.mobile

import android.accessibilityservice.AccessibilityServiceInfo
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.view.accessibility.AccessibilityNodeInfo
import androidx.activity.ComponentActivity
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.SourceCommitDetail
import dev.raticode.mobile.live.SourceCommitFile
import dev.raticode.mobile.live.SourceDiff
import dev.raticode.mobile.live.SourceHistoryCommit
import dev.raticode.mobile.ui.CodeView
import dev.raticode.mobile.ui.CommitView
import dev.raticode.mobile.ui.DiffView
import dev.raticode.mobile.ui.RaticodeTheme
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

/** Ask Rem in the file viewer's selection menu, on a change and its hunks, and on a commit. Needs no desktop. */
class AskRemUiTest {
    @get:Rule val compose = createAndroidComposeRule<ComponentActivity>()
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val clipboard get() = compose.activity.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager

    private fun clipboardText() = compose.runOnUiThread<String> { clipboard.primaryClip?.getItemAt(0)?.text?.toString().orEmpty() }

    /** The selection menu is the system's floating toolbar, so it is found and pressed through accessibility. */
    private fun menuItem(label: String, timeout: Long = 8000): AccessibilityNodeInfo {
        val automation = instrumentation.uiAutomation
        automation.serviceInfo = automation.serviceInfo.apply { flags = flags or AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS }
        var found: AccessibilityNodeInfo? = null
        var opened = false
        compose.waitUntil(timeout) {
            val nodes = automation.windows.mapNotNull { it.root }
            found = nodes.firstNotNullOfOrNull { root -> root.findAccessibilityNodeInfosByText(label).firstOrNull { it.text?.toString() == label } }
            if (found == null && !opened) nodes.firstNotNullOfOrNull { it.findAccessibilityNodeInfosByText("More options").firstOrNull() }?.let {
                it.performAction(AccessibilityNodeInfo.ACTION_CLICK); opened = true
            }
            found != null
        }
        return found!!
    }

    private fun screenshot(name: String) {
        compose.waitForIdle()
        instrumentation.uiAutomation.executeShellCommand("screencap -p /data/local/tmp/raticode-askrem-$name.png").use {
            android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
        }
    }

    private fun press(label: String) {
        val item = menuItem(label)
        val target = generateSequence(item) { it.parent }.firstOrNull { it.isClickable } ?: item
        assertTrue("$label pressed", target.performAction(AccessibilityNodeInfo.ACTION_CLICK))
        compose.waitForIdle()
    }

    @Test fun selectionMenuHandsOverTextAndItsLineWithoutTouchingTheClipboard() {
        val lines = listOf("package demo", "", "val shared = 1", "val other = 2", "val shared = 1", "// the end")
        val asked = mutableListOf<Pair<String, IntRange?>>()
        compose.runOnUiThread { clipboard.setPrimaryClip(ClipData.newPlainText("test", "kept")) }
        compose.setContent { RaticodeTheme { CodeView(lines, "kt", wrap = true, onAskRem = { text, range -> asked += text to range }) } }
        compose.waitForIdle()

        // The same words are on lines 3 and 5, and the long press settles which one is meant.
        compose.onAllNodesWithText("val shared = 1")[1].performTouchInput { longClick(centerLeft + androidx.compose.ui.geometry.Offset(30f, 0f)) }
        compose.waitForIdle()
        screenshot("menu")
        press("Ask Rem")
        compose.waitUntil(5000) { asked.isNotEmpty() }
        assertEquals("val", asked.single().first)
        assertEquals(5..5, asked.single().second)
        assertEquals("Ask Rem never uses the phone's clipboard", "kept", clipboardText())

        // Copy still goes to the clipboard, and without the line numbers.
        asked.clear()
        compose.onAllNodesWithText("val other = 2")[0].performTouchInput { longClick(centerLeft + androidx.compose.ui.geometry.Offset(30f, 0f)) }
        compose.waitForIdle()
        press("Copy")
        compose.waitUntil(5000) { clipboardText() != "kept" }
        assertEquals("val", clipboardText())
        assertTrue("Copy asks Rem nothing", asked.isEmpty())
    }

    @Test fun aViewerWithoutAskRemKeepsThePlainMenu() {
        compose.setContent { RaticodeTheme { CodeView(listOf("val only = 1"), "kt", wrap = true) } }
        compose.waitForIdle()
        compose.onNodeWithText("val only = 1").performTouchInput { longClick(centerLeft + androidx.compose.ui.geometry.Offset(30f, 0f)) }
        compose.waitForIdle()
        menuItem("Copy")
        val automation = instrumentation.uiAutomation
        assertTrue(automation.windows.mapNotNull { it.root }.all { it.findAccessibilityNodeInfosByText("Ask Rem").isEmpty() })
    }

    @Test fun aChangeAndEachHunkFillTheMessage() {
        val asked = mutableListOf<String>()
        val text = "diff --git a/a.kt b/a.kt\n--- a/a.kt\n+++ b/a.kt\n@@ -1,2 +1,2 @@\n a\n-b\n+c\n@@ -8,2 +8,1 @@\n d\n-e\n"
        compose.setContent { RaticodeTheme { DiffView(SourceDiff("src/a.kt", "commit", text, false, false), "Demo", "abc123", { asked += it }) } }
        compose.onNodeWithText("Ask Rem about this file").performClick()
        assertEquals("About this change: src/a.kt\nProject: Demo\nChange: in commit abc123", asked.single())
        asked.clear()
        compose.onNodeWithContentDescription("Ask Rem about this change, lines: 1–2").performClick()
        compose.onNodeWithContentDescription("Ask Rem about this change, lines: 8").performClick()
        assertEquals(2, asked.size)
        assertTrue(asked[0], asked[0].contains("Lines: 1–2") && asked[0].contains("```diff\n@@ -1,2 +1,2 @@\n a\n-b\n+c\n```"))
        assertTrue(asked[1], asked[1].contains("Lines: 8") && asked[1].contains("\n-e\n```"))
    }

    @Test fun aSecretLookingFileOffersNothing() {
        val text = "@@ -1 +1 @@\n-a\n+b\n"
        compose.setContent { RaticodeTheme { DiffView(SourceDiff("config/.env.local", "unstaged", text, false, false), "Demo", "", { fail("Nothing reaches Rem") }) } }
        compose.waitForIdle()
        compose.onNodeWithContentDescription("Added: b").assertIsDisplayed()
        compose.onAllNodes(hasText("Ask Rem", substring = true)).assertCountEquals(0)
    }

    @Test fun aCommitFillsItsId() {
        val asked = mutableListOf<String>()
        val hash = "0123456789".repeat(4)
        val commit = SourceHistoryCommit(hash, hash.take(7), "A", "2026-11-09T10:00:00Z", "Subject", "", emptyList(), emptyList(), false, 1, 0, 0)
        compose.setContent { RaticodeTheme { CommitView(SourceCommitDetail(commit, listOf(SourceCommitFile("a.kt", "", "modified", 1, 0, false)), 0), "Demo", { asked += it }, {}) } }
        compose.onNodeWithText("Ask Rem about this commit").performClick()
        assertEquals("About this commit: $hash\nProject: Demo", asked.single())
    }
}
