package dev.raticode.mobile.live

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

/** Usage › Cost at API prices, as the desktop sends it. */
class UsageCostTest {
    private val answer = JSONObject("""{
        "days": 7, "activity": {"calls": 3},
        "api_cost": {
            "method": "Estimate at public API list prices.", "prices_checked": "2026-11-17",
            "total": {"cost_usd": 12.5, "lower_bound": true, "calls": 3, "unsplit_calls": 1, "unpriced_calls": 1},
            "providers": [
                {"provider": "claude_code", "profile": null, "cost_usd": 12.5, "lower_bound": false, "unsplit_calls": 1, "unpriced_models": []},
                {"provider": "codex", "profile": "work", "cost_usd": null, "lower_bound": false, "unsplit_calls": 0, "unpriced_models": ["cli-default"]}
            ]
        }
    }""")

    @Test fun parsesTheEstimateAndKeepsUnknownAsNull() {
        val usage = DesktopViews.parse("usage.overview", answer) as DesktopUsage
        val cost = usage.apiCost!!
        assertEquals(12.5, cost.costUsd!!, 0.0)
        assertTrue(cost.lowerBound)
        assertNull(cost.providers[0].profile)
        assertEquals("work", cost.providers[1].profile)
        assertNull(cost.providers[1].costUsd)
        assertEquals(listOf("cli-default"), cost.providers[1].unpricedModels)
        // An older desktop sends no estimate.
        assertNull((DesktopViews.parse("usage.overview", JSONObject("""{"days": 30}""")) as DesktopUsage).apiCost)
    }

    @Test fun moneyReadsAsAnEstimateNeverAsZero() {
        assertEquals("Unknown", UsageCosts.usd(null))
        assertEquals("Unknown", UsageCosts.label(null, true))
        assertEquals("$0.00", UsageCosts.usd(0.0))
        assertEquals("<$0.01", UsageCosts.usd(0.004))
        assertEquals("$1,234.57", UsageCosts.usd(1234.567))
        assertEquals("−$10.50", UsageCosts.usd(-10.5))
        assertEquals("at least $3.20", UsageCosts.label(3.2, true))
    }

    @Test fun thePlanPriceActionIsGone() {
        // Saved vs API and its plan-price form were removed; the phone never sends usage.plans.save.
        assertFalse("usage.plans.save" in DesktopViews.ACTIONS)
        assertFalse("usage.plans.save" in DesktopViews.OPERATIONS)
    }
}
