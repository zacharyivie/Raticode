package dev.raticode.mobile

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.DesktopViews
import dev.raticode.mobile.live.Kanban
import dev.raticode.mobile.live.SelfWorkView
import dev.raticode.mobile.ui.RaticodeTheme
import dev.raticode.mobile.ui.SelfWorkBody
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/** Rem self-work's settings page on the phone, with the computer's board on and off, from a real desktop answer. */
class SelfWorkSettingsUiTest {
    @get:Rule val compose = createComposeRule()

    private fun view(board: Boolean): SelfWorkView {
        // The parts of a real desktop answer with the board off (app/src/test/resources/desktop/selfwork-read-board-off.json).
        val json = JSONObject("""{"settings":{"enabled":true,"max_threads":1,"auto_pull_backlog":false,"idle_minutes":15,"revision":1},
            "limits":{"max_threads_min":1,"max_threads_max":16},
            "state":{"mode":"waiting","active_threads":0,"thread_limit":1,"todo_remaining":0,"proposals_waiting":0,"assigned_projects":1,
              "outside_projects":0,"board_enabled":false,"activity":[],"last_prompt_at":"","starts_at":"",
              "detail":"Out of work. Research is done for now, and with the board off there are no tickets to take on."},
            "report":{"since":"2026-11-10T13:59:12+00:00","updated_at":"2026-11-10T13:59:13+00:00","unseen":false,"summary":"Researched 1 project.",
              "tickets_added":[],"duplicates_merged":[],
              "work_done":[{"ticket_id":"","title":"Research new features for project","state":"researched","summary":"Found 1 idea: Voice shortcuts.","thread_id":"t1","at":"2026-11-10T13:59:13+00:00"}],
              "notes":[{"text":"Started after 15 quiet minutes with the board off: 1 research run, no tickets.","at":"2026-11-10T13:59:12+00:00"}]}}""")
        json.getJSONObject("state").put("board_enabled", board)
        return DesktopViews.parse(Kanban.SELF_WORK, json) as SelfWorkView
    }

    @Test fun boardOffSettingsSayNoTicketsAndTurnOffBacklogPulling() {
        val sent = mutableListOf<JSONObject>()
        var shown by mutableStateOf(view(board = false))
        var settingsPage by mutableStateOf(true)
        var linked = 0
        compose.setContent {
            RaticodeTheme {
                Surface(color = MaterialTheme.colorScheme.background) {
                    SelfWorkBody(shown, null, busy = false, connected = true, error = null, showSettings = settingsPage, onSettings = { linked++ },
                        projectsEnabled = true, onProjects = {}, onTicket = {}, onSeen = {}) { sent += it }
                }
            }
        }
        compose.onNodeWithTag("selfWorkBoardOff").assertIsDisplayed()
        compose.onNodeWithText("The Kanban board is off on your computer", substring = true).assertExists()
        compose.onNodeWithTag("selfWorkAutoPull").assertIsNotEnabled()
        compose.onNodeWithText("Needs the Kanban board", substring = true).assertExists()
        // The research's ideas are in the report.
        compose.onNodeWithTag("selfWork").performScrollToNode(hasText("Found 1 idea: Voice shortcuts.", substring = true))
        compose.onNodeWithText("Found 1 idea: Voice shortcuts.", substring = true).assertExists()
        // Turning self-work off asks the desktop, with the revision it was shown.
        compose.onNodeWithTag("selfWork").performScrollToNode(hasTestTag("selfWorkEnabled"))
        compose.onNodeWithTag("selfWorkEnabled").assertIsOn().performClick()
        compose.waitForIdle()
        assertEquals(1, sent.size)
        assertEquals(false, sent[0].getBoolean("enabled"))
        assertEquals(shown.settings.revision, sent[0].getLong("expected_revision"))

        // With the board on, backlog pulling works again and the note goes away.
        shown = view(board = true)
        compose.waitForIdle()
        compose.onAllNodesWithTag("selfWorkBoardOff").assertCountEquals(0)
        compose.onNodeWithTag("selfWork").performScrollToNode(hasTestTag("selfWorkAutoPull"))
        compose.onNodeWithTag("selfWorkAutoPull").assertIsEnabled()

        // On the board's Self-work tab the settings are a link to this page.
        settingsPage = false
        compose.waitForIdle()
        compose.onAllNodesWithTag("selfWorkEnabled").assertCountEquals(0)
        compose.onNodeWithTag("selfWork").performScrollToNode(hasTestTag("selfWorkSettingsLink"))
        compose.onNodeWithTag("selfWorkSettingsLink").performClick()
        compose.waitForIdle()
        assertTrue(linked == 1)
    }

    @Test fun dailyCapStepsAndSavesOnceTheTapsStop() {
        val json = JSONObject("""{"settings":{"enabled":true,"max_threads":1,"auto_pull_backlog":false,"idle_minutes":15,"revision":7,"daily_allowance_percent":10},
            "limits":{"max_threads_min":1,"max_threads_max":16,"daily_allowance_percent_max":100},
            "state":{"mode":"waiting","active_threads":0,"thread_limit":1,"board_enabled":true,"detail":"",
              "daily_allowance":{"cap_percent":10,"used_percent":4,"available":true,
                "message":"Used today: about 4% of 10% of the Claude Code weekly allowance. Resets at midnight."}}}""")
        val sent = mutableListOf<JSONObject>()
        compose.setContent {
            RaticodeTheme {
                Surface(color = MaterialTheme.colorScheme.background) {
                    SelfWorkBody(DesktopViews.parse(Kanban.SELF_WORK, json) as SelfWorkView, null, busy = false, connected = true, error = null, showSettings = true,
                        onSettings = {}, projectsEnabled = true, onProjects = {}, onTicket = {}, onSeen = {}) { sent += it }
                }
            }
        }
        compose.onNodeWithTag("selfWork").performScrollToNode(hasTestTag("selfWorkDailyCap"))
        compose.onNodeWithTag("selfWorkDailyCapValue").assertTextEquals("10%")
        compose.onNodeWithText("Used today: about 4% of 10%", substring = true).assertExists()
        // Two quick taps become one change, sent with the revision the page showed.
        compose.onNodeWithTag("selfWorkDailyCapMore").performClick()
        compose.onNodeWithTag("selfWorkDailyCapMore").performClick()
        compose.onNodeWithTag("selfWorkDailyCapValue").assertTextEquals("12%")
        compose.mainClock.advanceTimeBy(1000)
        compose.waitForIdle()
        assertEquals(1, sent.size)
        assertEquals(12, sent[0].getInt("daily_allowance_percent"))
        assertEquals(7L, sent[0].getLong("expected_revision"))
    }
}
