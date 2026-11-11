package dev.raticode.mobile

import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.unit.dp
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.RemCheck
import dev.raticode.mobile.live.RemChecks
import dev.raticode.mobile.live.RemRun
import dev.raticode.mobile.ui.RaticodeTheme
import dev.raticode.mobile.ui.RemTaskChecksGroup
import org.junit.Rule
import org.junit.Test

/** A task's failed and unresolved checks on the phone, each by name with its reason, as in the desktop's task pane. */
class RemTaskChecksUiTest {
    @get:Rule val compose = createComposeRule()

    private fun screenshot(name: String) {
        compose.waitForIdle()
        Thread.sleep(600)
        InstrumentationRegistry.getInstrumentation().uiAutomation.executeShellCommand("screencap -p /data/local/tmp/raticode-$name").use {
            android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
        }
    }

    private val reason = "The export ignores the status filter: with Failed selected, the file still held all 48 rows. " +
        "Checked runs_export.py and the Runs list filter state; the query builder drops the status clause when the date range is empty. ".repeat(3).trim()

    private fun run(state: String, details: List<RemCheck>?) = RemRun("r", state, "", "", "Needs you", false, false, "", "",
        checks = RemChecks(details.orEmpty().count { it.result == "pass" }, details.orEmpty().count { it.result == "fail" }, details?.size ?: 2), details = details)

    @Test fun failedAndUnresolvedChecksShowTheirReasons() {
        var shown by mutableStateOf(run("blocked", listOf(
            RemCheck("a", "Exported rows match the filtered list", "fail", reason),
            RemCheck("b", "The export test covers an empty list", "unknown", "Not accepted: no project check receipts. No test run was recorded."),
            RemCheck("c", "The button is keyboard reachable", "pass", "Tab reaches Export CSV after the filter bar."),
        )))
        var dark by mutableStateOf(false)
        compose.setContent {
            RaticodeTheme(darkTheme = dark) {
                Surface(color = MaterialTheme.colorScheme.background) {
                    androidx.compose.foundation.layout.Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp)) {
                        RemTaskChecksGroup(shown)
                    }
                }
            }
        }
        compose.onNodeWithText("Checks").assertExists()
        compose.onNodeWithTag("remChecksSummary").assertTextEquals("1 of 3 passed, 1 failed, 1 unresolved")
        compose.onNodeWithText("Failed · 1").assertExists()
        compose.onNodeWithText("Unresolved · 1").assertExists()
        compose.onNodeWithText("Exported rows match the filtered list").assertIsDisplayed()
        compose.onNodeWithText("The export test covers an empty list").assertIsDisplayed()
        compose.onNodeWithText("Not accepted: no project check receipts. No test run was recorded.").assertIsDisplayed()
        // Passed checks fold away until opened.
        compose.onNodeWithText("The button is keyboard reachable").assertDoesNotExist()
        // A long reason starts folded to three lines and opens in place.
        val folded = compose.onNodeWithText(reason).fetchSemanticsNode().size.height
        screenshot("task-checks.png")
        compose.onNodeWithText("Show more").performScrollTo().performClick()
        compose.waitForIdle()
        check(compose.onNodeWithText(reason).fetchSemanticsNode().size.height > folded * 1.5) { "Show more did not reveal the whole reason" }
        compose.onNodeWithText("▸ Passed · 1").performScrollTo().performClick()
        compose.onNodeWithText("The button is keyboard reachable").assertExists()
        dark = true
        screenshot("task-checks-dark.png")

        // A run being fixed says what the review found.
        shown = run("repairing", listOf(RemCheck("a", "The test passes 20 runs in a row", "fail", "Run 14 of 20 timed out.")))
        compose.onNodeWithText("What the review found").assertExists()
        compose.onNodeWithText("Run 14 of 20 timed out.").assertExists()

        // Before a review, only the count line shows, as the desktop's "checks not run yet".
        shown = run("blocked", listOf(RemCheck("a", "One", "unknown", ""), RemCheck("b", "Two", "unknown", "")))
        compose.onNodeWithTag("remChecksSummary").assertTextEquals("2 checks not run yet")
        compose.onNodeWithText("One").assertDoesNotExist()

        // A failed check without a reason or name still reads clearly.
        shown = run("blocked", listOf(RemCheck("", "", "fail", "")))
        compose.onNodeWithText("Check 1").assertExists()
        compose.onNodeWithText("The review gave no reason.").assertExists()

        // An older desktop sends counts only: the phone shows the count line it always could.
        shown = run("blocked", null).copy(checks = RemChecks(1, 1, 2))
        compose.onNodeWithText("✕ 1 of 2 checks failed").assertExists()
        compose.onAllNodesWithTag("remChecksSummary").assertCountEquals(0)
    }
}
