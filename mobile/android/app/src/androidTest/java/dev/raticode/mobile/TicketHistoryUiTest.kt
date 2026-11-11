package dev.raticode.mobile

import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.unit.dp
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.Kanban
import dev.raticode.mobile.live.TicketComment
import dev.raticode.mobile.live.TicketTimeline
import dev.raticode.mobile.ui.RaticodeTheme
import dev.raticode.mobile.ui.TicketHistoryView
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import java.time.OffsetDateTime

/** A board ticket's history on the phone: its moves in order with their reasons, folded details, checks and comments. */
class TicketHistoryUiTest {
    @get:Rule val compose = createComposeRule()

    private fun screenshot(name: String) {
        compose.waitForIdle()
        Thread.sleep(600)
        InstrumentationRegistry.getInstrumentation().uiAutomation.executeShellCommand("screencap -p /data/local/tmp/raticode-$name").use {
            android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
        }
    }

    private val now = OffsetDateTime.now()

    // The shape of the desktop's kanban.timeline answer.
    private val answer = JSONObject("""{"ticket":{"ticket_id":"t1","scope":"rem","key":"REM-7","title":"Port the importer","status":"blocked","revision":4},
        "earlier":0,"can_comment":true,"comments":[
        {"id":"1","at":"${now.minusMinutes(50)}","author":"system","author_name":"Raticode","kind":"status","from":"in_progress","to":"in_review",
         "title":"Work complete, moved to In review","body":"Ported it. All 12 tests pass.","details":"Ported it. All 12 tests pass.\n\nFull log line 1\nFull log line 2","checks":[],"thread_id":"th1"},
        {"id":"2","at":"${now.minusMinutes(40)}","author":"system","author_name":"Raticode","kind":"status","from":"in_review","to":"blocked",
         "title":"Blocked: review checks failed","body":"The review left these checks unresolved: Docs updated","details":"",
         "checks":[{"name":"Tests pass","result":"pass"},{"name":"Docs updated","result":"fail"}]},
        {"id":"3","at":"${now.minusMinutes(5)}","author":"rem","author_name":"Rem","kind":"comment","title":"Still blocked",
         "body":"It needs your answer before it can go on.","details":"","checks":[]}]}""")

    private fun timeline() = Kanban.parseTimeline(answer)

    @Test fun showsTheMovesInOrderWithChecksAndTakesAComment() {
        val sent = mutableListOf<String>()
        compose.setContent {
            RaticodeTheme { Surface { androidx.compose.foundation.layout.Column(Modifier.verticalScroll(rememberScrollState()).padding(16.dp)) {
                TicketHistoryView("/t1", timeline(), supported = true, connected = true, busy = false) { sent += it }
            } } }
        }
        val titles = listOf("Work complete, moved to In review", "Blocked: review checks failed", "Still blocked")
        val tops = titles.map { compose.onNodeWithText(it, substring = true).assertExists().fetchSemanticsNode().boundsInRoot.top }
        assertEquals(tops.sorted(), tops)
        compose.onNodeWithText("In review → Blocked").assertExists()
        compose.onNodeWithText("1 of 2 checks passed").assertExists()
        compose.onNodeWithContentDescription("Failed: Docs updated").assertExists()
        // The full result is folded away until asked for.
        compose.onAllNodesWithText("Full log line 1", substring = true).assertCountEquals(0)
        screenshot("ticket-history.png")
        compose.onNodeWithTag("kanbanEntryDetails").performClick()
        compose.onNodeWithText("Full log line 1", substring = true).assertExists()
        compose.onNodeWithTag("kanbanCommentField").performTextInput("I'll fix the README tonight.")
        compose.onNodeWithTag("kanbanCommentSend").performClick()
        assertEquals(listOf("I'll fix the README tonight."), sent)
        compose.onNodeWithTag("kanbanCommentSend").assertIsNotEnabled()
    }

    @Test fun anOlderDesktopSaysToUpdateAndOffersNoCommentBox() {
        compose.setContent { RaticodeTheme { Surface { TicketHistoryView("/t1", null, supported = false, connected = true, busy = false) {} } } }
        compose.onNodeWithTag("kanbanHistoryUnsupported").assertTextEquals(Kanban.UPDATE_COMMENTS)
        compose.onAllNodesWithTag("kanbanCommentField").assertCountEquals(0)
    }

    @Test fun aLongHistoryShowsTheNewestAndFoldsTheRest() {
        val many = TicketTimeline(null, (1..11).map { TicketComment("$it", now.toString(), "user", "You", "comment", "", "Note $it", "", "", "", emptyList()) }, 0, true)
        compose.setContent {
            RaticodeTheme { Surface { androidx.compose.foundation.layout.Column(Modifier.verticalScroll(rememberScrollState())) {
                TicketHistoryView("/t1", many, supported = true, connected = true, busy = false) {}
            } } }
        }
        compose.onAllNodesWithText("Note 1").assertCountEquals(0)
        compose.onNodeWithText("Note 11").assertExists()
        compose.onNodeWithTag("kanbanHistoryEarlier").assertTextEquals("Show 3 earlier").performClick()
        compose.onNodeWithText("Note 1").assertExists()
    }
}
