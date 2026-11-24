package dev.raticode.mobile.live

import org.junit.Assert.*
import org.junit.Test
import java.time.LocalDate

/** The Usage page's periods, the saved choice and the chart's numbers. */
class UsageHistoryTest {
    @Test fun periodsMatchTheDesktop() {
        assertEquals(listOf("24h", "7d", "30d", "3mo", "6mo", "1Y", "5Y", "All", "Custom"), UsageHistory.RANGES.map { it.short })
        assertEquals("Last 3 months", UsageHistory.label(UsageRangeChoice("3mo")))
    }

    @Test fun savedNinetyDaysBecomesThreeMonths() {
        assertEquals(UsageRangeChoice("3mo"), UsageHistory.parse("90"))
        assertEquals(UsageRangeChoice("3mo"), UsageHistory.parse("90d"))
        assertEquals(UsageRangeChoice("24h"), UsageHistory.parse("1"))
        assertEquals(UsageRangeChoice("6mo"), UsageHistory.parse("6mo"))
        assertEquals(UsageRangeChoice(), UsageHistory.parse(null))
        assertEquals(UsageRangeChoice(), UsageHistory.parse("2y"))
        assertEquals(UsageRangeChoice(), UsageHistory.parse("custom"))
    }

    @Test fun customRangesRoundTripAndAreChecked() {
        val choice = UsageRangeChoice("custom", "2026-01-01", "2026-02-01")
        assertEquals("custom:2026-01-01:2026-02-01", UsageHistory.encode(choice))
        assertEquals(choice, UsageHistory.parse(UsageHistory.encode(choice)))
        assertEquals(UsageRangeChoice(), UsageHistory.parse("custom:2026-02-01:2026-01-01"))
        val today = LocalDate.of(2026, 11, 16)
        assertEquals("The start date must be on or before the end date.", UsageHistory.customError("2026-03-02", "2026-03-01", today))
        assertEquals("The start date can't be in the future.", UsageHistory.customError("2026-12-01", "2026-12-02", today))
        assertEquals("The start date must be in 2000 or later.", UsageHistory.customError("1999-01-01", "2026-01-01", today))
        assertEquals("Choose an end date.", UsageHistory.customError("2026-01-01", "", today))
        assertNull(UsageHistory.customError("2026-01-01", "2026-01-01", today))
    }

    @Test fun requestNamesThePeriodAndZone() {
        val preset = UsageHistory.params(UsageRangeChoice("5y"), "Europe/Paris")
        assertEquals("5y", preset.getString("range")); assertEquals("Europe/Paris", preset.getString("tz")); assertFalse(preset.has("start"))
        val custom = UsageHistory.params(UsageRangeChoice("custom", "2026-01-01", "2026-01-31"), "UTC")
        assertEquals("2026-01-01", custom.getString("start")); assertEquals("2026-01-31", custom.getString("end"))
    }

    @Test fun shortNumbersAndShares() {
        assertEquals("950", UsageHistory.compact(950))
        assertEquals("12.4K", UsageHistory.compact(12_400))
        assertEquals("3.4M", UsageHistory.compact(3_400_000))
        assertEquals("250M", UsageHistory.compact(250_000_000))
        assertEquals("<1%", UsageHistory.share(1, 1000))
        assertEquals("75%", UsageHistory.share(300, 400))
        assertEquals("0%", UsageHistory.share(0, 0))
        assertEquals("Claude Code", UsageHistory.providerName("claude_code"))
    }

    @Test fun olderDesktopSendsNoSeries() {
        val usage = DesktopViews.parse("usage.overview", org.json.JSONObject("""{"days":30,"accounts":[],"activity":{"calls":2,"providers":[]}}""")) as DesktopUsage
        assertNull(usage.range); assertNull(usage.series); assertEquals(30, usage.days)
    }

    @Test fun theSplitChoiceIsReadBackAndBadValuesMeanByProvider() {
        assertEquals(UsageSplit.ACCOUNT, UsageSplit.parse("account"))
        assertEquals(UsageSplit.PROVIDER, UsageSplit.parse("provider"))
        assertEquals(UsageSplit.PROVIDER, UsageSplit.parse(null))
        assertEquals(UsageSplit.PROVIDER, UsageSplit.parse("model"))
        assertEquals(listOf("By provider", "By account"), UsageSplit.entries.map { it.label })
    }

    @Test fun anOlderDesktopWithoutAccountsStaysSplitByProvider() {
        val series = UsageHistory.parseSeries(org.json.JSONObject(
            """{"bucket":"day","providers":[{"provider":"codex","total_tokens":5,"calls":1}],"buckets":[{"start":"s","end":"e","calls":1,"total_tokens":5,"providers":{"codex":5}}]}"""))!!
        assertTrue(series.accounts.isEmpty())
        assertEquals(series, UsageHistory.view(series, UsageSplit.ACCOUNT))
        assertEquals("Codex", UsageHistory.pieceName(series.providers.single()))
    }

    @Test fun providerStylesAreKeptOnlyWhenWellFormed() {
        fun style(json: String) = UsageHistory.parseStyle(org.json.JSONObject(json))
        assertEquals(UsageSeriesStyle(0xFF10A37F, 0xFF0D8A6B, "stripes"), style("""{"dark":"#10A37F","light":"#0D8A6B","pattern":"stripes"}"""))
        assertEquals(0xFFF7F7F4, style("""{"dark":"#f7f7f4","light":"#26251E","pattern":"dots"}""")!!.dark)
        assertNull(style("""{"dark":"red","light":"#0D8A6B","pattern":"stripes"}"""))
        assertNull(style("""{"dark":"#10A37F","light":"#0D8A6","pattern":"stripes"}"""))
        assertNull(style("""{"dark":"#10A37F","light":"#0D8A6B","pattern":"plaid"}"""))
        assertNull(UsageHistory.parseStyle(null))
        // Every pattern the desktop palette uses can be drawn here.
        assertEquals(setOf("solid", "stripes", "back-stripes", "horizontal", "vertical", "dots", "grid", "cross", "checks", "rings"), UsageHistory.PATTERNS)
    }
}
