package dev.raticode.mobile.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class LiveNavigationTest {
    @Test fun barStartsWithRemThenThreads() {
        assertEquals(listOf("Rem", "Threads", "Organizations", "Swarms", "More"), LiveNavigation.tabs().map { it.first })
        assertEquals(listOf("Rem", "Threads", "Orgs", "Swarms", "More"), LiveNavigation.tabs().map { it.second })
    }

    @Test fun plainLaunchOpensRemAndLinksKeepTheirPage() {
        assertEquals("Rem", LiveNavigation.initialPage(null))
        assertEquals("Rem", LiveNavigation.initialPage("rem"))
        assertEquals("Approvals", LiveNavigation.initialPage("approvals"))
        assertEquals("Chat", LiveNavigation.initialPage("8b0c6f2e-thread"))
    }

    @Test fun backReturnsToRemAndThenLeaves() {
        assertNull(LiveNavigation.back("Rem", "Rem"))
        assertEquals("Rem", LiveNavigation.back("Swarms", "Swarms"))
        assertEquals("Rem", LiveNavigation.back("Threads", "Threads"))
        assertEquals("Rem", LiveNavigation.back("Organizations", "Organizations"))
        assertEquals("Rem", LiveNavigation.back("More", "More"))
        assertEquals("Threads", LiveNavigation.back("Chat", "Threads"))
        assertEquals("Chat", LiveNavigation.back("Files", "Threads"))
        assertEquals("More", LiveNavigation.back("Approvals", "More"))
        assertEquals("Threads", LiveNavigation.back("Desktop", "Threads"))
    }

    @Test fun browserIsAPageMoreListsAndTheBarCanHold() {
        assertEquals("Browser", LiveNavigation.destination("Browser")?.title)
        assertTrue(LiveNavigation.omitted(LiveNavigation.DEFAULT).any { it.page == "Browser" })
        assertEquals(listOf("Rem", "Browser"), LiveNavigation.parse("Rem,Browser"))
        assertEquals("More", LiveNavigation.back("Browser", "More"))
        assertEquals("Rem", LiveNavigation.back("Browser", "Browser", bar = listOf("Rem", "Browser")))
    }

    @Test fun threadsHomeKeepsTheEarlierBackRules() {
        assertEquals("Threads", LiveNavigation.initialPage(null, home = "Threads"))
        assertNull(LiveNavigation.back("Threads", "Threads", home = "Threads"))
        assertEquals("Threads", LiveNavigation.back("Rem", "Rem", home = "Threads"))
        assertEquals("Threads", LiveNavigation.back("Chat", "Rem", home = "Threads"))
    }

    @Test fun savedBarsKeepKnownPagesOnceAndAtMostFour() {
        assertEquals(LiveNavigation.DEFAULT, LiveNavigation.parse(null))
        assertEquals(LiveNavigation.DEFAULT, LiveNavigation.parse(""))
        assertEquals(LiveNavigation.DEFAULT, LiveNavigation.parse("Nope,More"))
        assertEquals(listOf("Rem", "SourceControl"), LiveNavigation.parse("Rem, SourceControl,Rem,More"))
        assertEquals(listOf("Rem", "Threads", "SourceControl", "Workflows"),
            LiveNavigation.parse("Rem,Threads,SourceControl,Workflows,Usage"))
        val bar = listOf("Rem", "SourceControl", "Threads")
        assertEquals(bar, LiveNavigation.parse(LiveNavigation.encode(bar)))
    }

    @Test fun replacingOrgsAndSwarmsWithSourceControlAndWorkflows() {
        var bar = LiveNavigation.DEFAULT
        // The bar is full, so a fifth destination waits until one leaves.
        assertEquals(bar, LiveNavigation.toggle(bar, "SourceControl"))
        bar = LiveNavigation.toggle(bar, "Organizations")
        bar = LiveNavigation.toggle(bar, "Swarms")
        bar = LiveNavigation.toggle(bar, "SourceControl")
        bar = LiveNavigation.toggle(bar, "Workflows")
        assertEquals(listOf("Rem", "Threads", "SourceControl", "Workflows"), bar)
        assertEquals(listOf("Rem", "Threads", "Source", "Workflows", "More"), LiveNavigation.tabs(bar).map { it.second })
        // Orgs and Swarms are still listed in More, with every other destination left off the bar.
        val omitted = LiveNavigation.omitted(bar).map { it.page }
        assertTrue("Organizations" in omitted && "Swarms" in omitted)
        assertEquals(LiveNavigation.destinations.size, omitted.size + bar.size)
    }

    @Test fun moreKeepsItsOldOrderWithNewerPagesAfter() {
        assertEquals(listOf("Voice", "Workflows", "Approvals", "Settings", "Usage", "SourceControl", "Explorer", "Developer", "Browser", "Kanban"),
            LiveNavigation.omitted(LiveNavigation.DEFAULT).map { it.page })
    }

    @Test fun theLastDestinationStaysAndUnknownPagesAreIgnored() {
        assertEquals(listOf("Rem"), LiveNavigation.toggle(listOf("Rem"), "Rem"))
        assertEquals(listOf("Rem"), LiveNavigation.toggle(listOf("Rem"), "More"))
        assertEquals(listOf("Rem"), LiveNavigation.toggle(listOf("Rem"), "Chat"))
    }

    @Test fun movingReordersWithinTheBar() {
        val bar = listOf("Rem", "Threads", "SourceControl")
        assertEquals(listOf("Rem", "SourceControl", "Threads"), LiveNavigation.move(bar, "SourceControl", -1))
        assertEquals(listOf("Threads", "Rem", "SourceControl"), LiveNavigation.move(bar, "Rem", 1))
        assertEquals(bar, LiveNavigation.move(bar, "Rem", -1))
        assertEquals(bar, LiveNavigation.move(bar, "SourceControl", 1))
        assertEquals(bar, LiveNavigation.move(bar, "Usage", 1))
    }

    @Test fun pagesLeftOffTheBarGoBackToMore() {
        val bar = listOf("SourceControl", "Rem", "Threads")
        // The first destination is home: back there leaves the app.
        assertNull(LiveNavigation.back("SourceControl", "SourceControl", home = "SourceControl", bar = bar))
        assertEquals("SourceControl", LiveNavigation.back("Rem", "Rem", home = "SourceControl", bar = bar))
        // Orgs and Swarms, opened from More, return to it.
        assertEquals("More", LiveNavigation.back("Organizations", "More", home = "SourceControl", bar = bar))
        assertEquals("More", LiveNavigation.back("Swarms", "More", home = "SourceControl", bar = bar))
        assertEquals("More", LiveNavigation.back(LiveNavigation.NAV_BAR, "More", home = "SourceControl", bar = bar))
        // A notification link to Approvals goes back to home when nothing else was open.
        assertEquals("SourceControl", LiveNavigation.back("Approvals", "SourceControl", home = "SourceControl", bar = bar))
        // A bar destination is top level; More is always on the bar.
        assertEquals(setOf("SourceControl", "Rem", "Threads", "More"), LiveNavigation.topLevel(bar))
    }
}
