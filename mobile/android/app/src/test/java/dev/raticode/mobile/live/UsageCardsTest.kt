package dev.raticode.mobile.live

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.time.Instant
import java.time.ZoneOffset

/** Usage › Accounts at a glance: the countdown, the last-checked time and the "Rem uses next" badge. */
class UsageCardsTest {
    private val now = Instant.parse("2026-11-17T12:00:00Z")

    @Test fun countdownReadsInDaysHoursAndMinutes() {
        assertEquals("resets in 2h 14m", UsageCards.countdown("2026-11-17T14:14:00+00:00", now))
        assertEquals("resets in 2h 14m", UsageCards.countdown("2026-11-17T14:14:59+00:00", now))
        assertEquals("resets in 1h 0m", UsageCards.countdown("2026-11-17T13:00:00Z", now))
        assertEquals("resets in 9m", UsageCards.countdown("2026-11-17T12:09:00Z", now))
        assertEquals("resets in under a minute", UsageCards.countdown("2026-11-17T12:00:30Z", now))
        assertEquals("resets in 3d 3h", UsageCards.countdown("2026-11-20T15:30:00Z", now))
        // A passed reset means the reading is out of date, not an empty window.
        assertEquals("reset time passed", UsageCards.countdown("2026-11-17T11:59:00Z", now))
        assertEquals("", UsageCards.countdown("", now))
        assertEquals("", UsageCards.countdown("soon", now))
    }

    @Test fun lastCheckedIsRelativeWhileRecent() {
        assertEquals("Checked just now", UsageCards.checked("2026-11-17T11:59:40Z", now))
        assertEquals("Checked 14 min ago", UsageCards.checked("2026-11-17T11:46:00Z", now))
        assertEquals("Checked 3h ago", UsageCards.checked("2026-11-17T09:00:00Z", now))
        assertTrue(UsageCards.checked("2026-11-15T09:00:00Z", now, ZoneOffset.UTC).startsWith("Checked Nov 15"))
        assertEquals("Not checked yet", UsageCards.checked("", now))
    }

    private val overview = JSONObject("""{
        "days": 30,
        "accounts": [
            {"provider": "claude_code", "profile": "", "label": "Claude Code", "status": "available", "sign_in_state": "signed_in",
             "observed_at": "2026-11-17T11:50:00+00:00",
             "windows": [{"id": "seven_day", "label": "Weekly allowance", "used_percent": 40, "resets_at": "2026-11-20T00:00:00+00:00"},
                         {"id": "five_hour", "label": "Five-hour allowance", "used_percent": 10, "resets_at": "2026-11-17T14:14:00+00:00"},
                         {"id": "seven_day_opus", "label": "Weekly Opus allowance", "used_percent": 20, "resets_at": ""}]},
            {"provider": "claude_code", "profile": "work", "label": "Claude Code · work", "status": "available", "sign_in_state": "signed_in",
             "rem_next": {"why": "Most room left: 4% of its weekly allowance used."},
             "windows": [{"id": "five_hour", "label": "Five-hour allowance", "used_percent": 95, "resets_at": ""}]},
            {"provider": "claude_code", "profile": "home", "label": "Claude Code · home", "status": "needs sign-in", "sign_in_state": "needs_sign_in", "windows": []},
            {"provider": "codex", "profile": "", "label": "Codex", "status": "available", "windows": [{"id": "primary", "label": "5h limit", "used_percent": 50, "resets_at": ""}]},
            {"provider": "cursor", "profile": "", "label": "Cursor", "status": "not_installed", "windows": []},
            {"provider": "openai_api", "profile": "", "label": "OpenAI API", "status": "unavailable", "windows": []}
        ]
    }""")

    @Test fun oneCardPerAccountWithBothClaudeWindowsSideBySide() {
        val usage = DesktopViews.parse("usage.overview", overview) as DesktopUsage
        val cards = UsageCards.cards(usage.accounts)
        assertEquals(listOf("Default sign-in", "work", "home", "Default account"), cards.map { it.accountName })
        val main = cards[0]
        assertEquals(listOf("5-hour", "Weekly", "Weekly Opus"), main.meters.map { it.label })
        assertEquals(90.0, main.meters[0].leftPercent!!, 0.0)
        assertEquals(60.0, main.meters[1].leftPercent!!, 0.0)
        assertEquals("signed_in", main.signIn)
        // A window with no reading keeps its place beside the other one.
        val work = cards[1]
        assertEquals(listOf("5-hour", "Weekly"), work.meters.map { it.label })
        assertNull(work.meters[1].window)
        assertTrue(cards[2].needsSignIn)
        assertNull(cards[3].signIn)
        // Each meter is that account's own reading; nothing is added across accounts.
        assertEquals(5.0, work.meters[0].leftPercent!!, 0.0)
    }

    @Test fun onlyRemsNextAccountCarriesTheBadgeAndItsReason() {
        val cards = UsageCards.cards((DesktopViews.parse("usage.overview", overview) as DesktopUsage).accounts)
        assertEquals(listOf(null, "Most room left: 4% of its weekly allowance used.", null, null), cards.map { it.remNext })
        // An older desktop, or Rem not choosing by usage, sends no badge at all.
        val older = JSONObject("""{"days": 30, "accounts": [{"provider": "claude_code", "label": "Claude Code", "status": "available",
            "windows": [{"label": "five hour", "used_percent": 40, "resets_at": ""}]}]}""")
        val card = UsageCards.cards((DesktopViews.parse("usage.overview", older) as DesktopUsage).accounts).single()
        assertNull(card.remNext)
        assertEquals("Default sign-in", card.accountName)
        // An older desktop sends no window ids, so its windows show as they came.
        assertEquals(listOf("five hour"), card.meters.map { it.label })
    }

    @Test fun aLiveSignInCheckBeatsAnOlderUsageReading() {
        val account = UsageAccount("Claude Code · x", "needs sign-in", "", emptyList(), "claude_code", "x", "", "signed_in")
        assertFalse(UsageCards.cards(listOf(account)).single().needsSignIn)
    }

    @Test fun codexAccountCardsCarryTheirOwnSignInState() {
        val work = UsageAccount("Codex · work", "available", "", emptyList(), "codex", "work", "", "signed_in", "Most room left.")
        val home = UsageAccount("Codex · home", "available", "", emptyList(), "codex", "home", "", "needs_sign_in")
        val gone = UsageAccount("Codex · gone", "auth_required", "", emptyList(), "codex", "gone", "", "signed_in")
        val plain = UsageAccount("Codex", "available", "", emptyList(), "codex")
        val cards = UsageCards.cards(listOf(work, home, gone, plain))
        assertEquals(listOf("signed_in", "needs_sign_in", "signed_in", null), cards.map { it.signIn })
        assertEquals(listOf(false, true, false, false), cards.map { it.needsSignIn })
        assertEquals("Most room left.", cards[0].remNext)
        assertEquals("Codex not found", UsageCards.signInLabel("unavailable", "Codex"))
        assertEquals("Signed in", UsageCards.signInLabel("signed_in", "Codex"))
    }

    private val withHistory = JSONObject("""{
        "accounts": [
            {"provider": "claude_code", "profile": "work", "label": "Claude Code · work", "status": "available",
             "observed_at": "2026-11-17T12:00:00+00:00",
             "windows": [{"id": "seven_day", "label": "Weekly allowance", "used_percent": 20, "resets_at": "2026-11-21T12:00:00+00:00"}],
             "weekly_burndown": {"window_id": "seven_day", "label": "Weekly allowance", "starts_at": "2026-11-14T12:00:00+00:00",
               "resets_at": "2026-11-21T12:00:00+00:00", "reading_count": 2,
               "readings": [{"observed_at": "2026-11-15T12:00:00+00:00", "used_percent": 10}, {"observed_at": "2026-11-16T12:00:00+00:00", "used_percent": 20}],
               "pace": {"state": "runs_out", "runs_out_at": "2026-11-19T14:00:00+00:00", "text": "desktop text"}}},
            {"provider": "codex", "label": "Codex", "status": "available", "windows": []}
        ]
    }""")

    @Test fun weeklyBurndownStepsThroughReadingsAndHoldsTheLastUntilChecked() {
        val cards = UsageCards.cards((DesktopViews.parse("usage.overview", withHistory) as DesktopUsage).accounts)
        val weekly = cards.first().weekly!!
        assertEquals(2, weekly.readingCount)
        assertNull(cards.last().weekly)
        val points = UsageCards.burndownPoints(weekly, cards.first().observedAt).map { (x, y) -> Math.round(x * 7 * 100) / 100.0 to y.toDouble() }
        // Day 1 at 10% used, held to day 2, then 20% held to the check on day 3.
        assertEquals(listOf(1.0 to 0.1, 2.0 to 0.1, 2.0 to 0.2, 3.0 to 0.2), points.map { (x, y) -> x to Math.round(y * 100) / 100.0 })
    }

    @Test fun paceTextNamesTheRunOutTimeOrSaysReadingsAreTooFew() {
        val weekly = UsageCards.cards((DesktopViews.parse("usage.overview", withHistory) as DesktopUsage).accounts).first().weekly!!
        assertEquals("At this pace, runs out around Thu 14:00", UsageCards.paceText(weekly, ZoneOffset.UTC))
        assertEquals("On pace to last until reset", UsageCards.paceText(weekly.copy(paceState = "on_pace"), ZoneOffset.UTC))
        assertEquals("Not enough readings yet", UsageCards.paceText(weekly.copy(paceState = "not_enough", readings = emptyList())))
        assertEquals("Not enough readings yet", UsageCards.paceText(weekly.copy(paceState = "")))
        assertEquals("Not enough readings yet", UsageCards.paceText(weekly.copy(runsOutAt = "")))
        assertEquals(emptyList<Pair<Float, Float>>(), UsageCards.burndownPoints(weekly.copy(startsAt = ""), ""))
    }

    @Test fun farOffDatesFromTheComputerDrawNoChartInsteadOfEndingTheApp() {
        val weekly = UsageCards.cards((DesktopViews.parse("usage.overview", withHistory) as DesktopUsage).accounts).first().weekly!!
        // java.time reads these, but their span in milliseconds overflows a Long.
        val far = weekly.copy(startsAt = "-999999999-01-01T00:00:00Z", resetsAt = "+999999999-12-31T00:00:00Z")
        assertEquals(emptyList<Pair<Float, Float>>(), UsageCards.burndownPoints(far, ""))
        assertEquals(emptyList<Pair<Float, Float>>(), UsageCards.burndownPoints(weekly.copy(resetsAt = "+999999999-12-31T00:00:00Z"), ""))
    }

    @Test fun onlyNamedAccountsTheDesktopCanSignInGetTheButton() {
        val codex = UsageAccount("Codex · work", "available", "", emptyList(), "codex", "work", "", "needs_sign_in", phoneSignIn = true)
        val claude = UsageAccount("Claude Code · laptop", "available", "", emptyList(), "claude_code", "laptop", "", "needs_sign_in")
        val unnamed = UsageAccount("Codex", "auth_required", "", emptyList(), "codex", phoneSignIn = true)
        val cards = UsageCards.cards(listOf(codex, claude, unnamed))
        assertEquals(listOf(true, false, false), cards.map { it.phoneSignIn })
        assertEquals("codex", cards[0].provider)
    }
}
