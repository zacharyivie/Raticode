package dev.raticode.mobile.browser

import dev.raticode.mobile.browser.TabRecovery.Decision
import dev.raticode.mobile.browser.TabRecovery.Hang
import org.junit.Assert.assertEquals
import org.junit.Test

class TabRecoveryTest {
    private var now = 1_000_000L
    private val recovery = TabRecovery { now }

    @Test fun aPageEndedWhileAwayReloadsAtOnceWithNothingShown() {
        assertEquals(Decision.Restore(0L), recovery.gone("a"))
        // Every tab sharing the ended process gets the same answer, each counted on its own.
        assertEquals(Decision.Restore(0L), recovery.gone("b"))
    }

    @Test fun aPageThatKeepsStoppingWaitsLongerEachTimeThenGetsAReloadButton() {
        assertEquals(Decision.Restore(0L), recovery.gone("a")); recovery.restored("a")
        assertEquals(Decision.Restore(TabRecovery.BACKOFF_MS[1]), recovery.gone("a")); recovery.restored("a")
        assertEquals(Decision.Restore(TabRecovery.BACKOFF_MS[2]), recovery.gone("a")); recovery.restored("a")
        assertEquals(TabRecovery.MAX_RESTORES, recovery.recentRestores("a"))
        assertEquals(Decision.GiveUp, recovery.gone("a"))
        // Another tab still reloads by itself.
        assertEquals(Decision.Restore(0L), recovery.gone("b"))
    }

    @Test fun reloadsLongAgoDontCountTowardsALoop() {
        repeat(TabRecovery.MAX_RESTORES) { recovery.gone("a"); recovery.restored("a") }
        now += TabRecovery.LOOP_WINDOW_MS + 1
        assertEquals(Decision.Restore(0L), recovery.gone("a"))
    }

    @Test fun theUserReloadingOrLeavingTheTabStartsCountingAgain() {
        repeat(TabRecovery.MAX_RESTORES) { recovery.gone("a"); recovery.restored("a") }
        recovery.forget("a")
        assertEquals(Decision.Restore(0L), recovery.gone("a"))
    }

    @Test fun aStuckPageNotOnScreenIsLeftAloneWithNothingShown() {
        repeat(5) { now += 5_000; assertEquals(Hang.LEAVE, recovery.unresponsive("a", inView = false, busy = false)) }
    }

    @Test fun aStuckPageOnScreenGetsAGracePeriodThenIsEnded() {
        assertEquals(Hang.WAIT, recovery.unresponsive("a", inView = true, busy = false))
        now += 5_000
        assertEquals(Hang.WAIT, recovery.unresponsive("a", inView = true, busy = false))
        now += TabRecovery.HANG_GRACE_MS
        assertEquals(Hang.END, recovery.unresponsive("a", inView = true, busy = false))
    }

    @Test fun aPageThatAnswersAgainOrLeavesTheScreenStartsTheGraceAgain() {
        recovery.unresponsive("a", inView = true, busy = false)
        now += TabRecovery.HANG_GRACE_MS
        recovery.responsive("a")
        assertEquals(Hang.WAIT, recovery.unresponsive("a", inView = true, busy = false))
        now += TabRecovery.HANG_GRACE_MS
        recovery.unresponsive("a", inView = false, busy = false)
        assertEquals(Hang.WAIT, recovery.unresponsive("a", inView = true, busy = false))
    }

    @Test fun nothingIsEndedWhileSoundPlaysOrAPageFileIsBeingRead() {
        recovery.unresponsive("a", inView = true, busy = true)
        now += TabRecovery.HANG_GRACE_MS * 3
        assertEquals(Hang.LEAVE, recovery.unresponsive("a", inView = true, busy = true))
        assertEquals(Hang.END, recovery.unresponsive("a", inView = true, busy = false))
    }
}
