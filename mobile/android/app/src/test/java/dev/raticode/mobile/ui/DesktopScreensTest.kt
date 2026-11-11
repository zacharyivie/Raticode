package dev.raticode.mobile.ui

import dev.raticode.mobile.live.RemRun
import dev.raticode.mobile.live.RemSchedule
import dev.raticode.mobile.live.RemTask
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Rem task status and schedules read the same as on the desktop. */
class DesktopScreensTest {
    private fun run(state: String, acknowledged: Boolean = false) = RemRun("r", state, "", "", "", false, acknowledged, "", "")
    private fun task(state: String = "enabled", active: RemRun? = null, last: RemRun? = null, next: String = "",
                     schedule: RemSchedule = RemSchedule("once", "", "", emptyList(), 0)) =
        RemTask("t", "Task", "errand", state, 1, schedule, next, "", active, last)

    @Test fun activityPrefersRunningWorkThenAttention() {
        assertEquals("Working", remActivity(task(active = run("executing"), last = run("failed"))))
        // The desktop's words: a failed run "didn't finish".
        assertEquals("Didn't finish", remActivity(task(last = run("failed"))))
        assertEquals("Paused", remActivity(task(state = "paused", last = run("failed", acknowledged = true))))
        assertEquals("Not scheduled", remActivity(task()))
        // A desktop that names the step or the wait is quoted, as on the desktop's cards.
        assertEquals("Checking the work", remActivity(task(active = run("verifying").copy(phaseLabel = "Checking the work"))))
        assertEquals("Waiting for a free slot", remActivity(task(active = run("queued").copy(waitingReason = "Waiting for a free slot"))))
    }

    @Test fun workCardsExplainTasksThatLeftTheList() {
        val done = RemRun("r", "succeeded", "", "2026-10-31T10:00:00+00:00", "", false, false, "w", "")
        val work = dev.raticode.mobile.live.RemWork("t", "r", "w", "Task")
        assertEquals(null, workStatus(work, null))
        assertEquals("Removed from your desktop", workStatus(work.copy(taskAvailable = false, threadAvailable = false), null))
        assertEquals("Working", workStatus(work, task(active = run("executing"))))
        assertTrue(workStatus(work, task(last = done))!!.matches(Regex("Done [^·]+")))
        assertEquals("This one-time task finished, so it's no longer in the task list. The chat keeps its link.", cleanupText(""))
        assertTrue(cleanupText("2026-11-30T10:00:00+00:00").endsWith("Your desktop removes it on ${whenText("2026-11-30T10:00:00+00:00")}."))
    }

    @Test fun dayLabelsMatchTheDesktop() {
        val today = java.time.LocalDate.of(2026, 10, 30)
        assertEquals("Today", dayLabel(today, today))
        assertEquals("Yesterday", dayLabel(today.minusDays(1), today))
        assertEquals("Monday, Oct 26", dayLabel(today.minusDays(4), today, java.util.Locale.US))
        assertEquals("Wednesday, Dec 31, 2025", dayLabel(java.time.LocalDate.of(2025, 12, 31), today, java.util.Locale.US))
    }

    @Test fun messageStampsMatchTheDesktop() {
        val zone = java.time.ZoneId.of("America/Denver")
        val today = java.time.LocalDate.of(2026, 10, 7)
        fun stamp(value: String, use24Hour: Boolean = false) = messageStamp(messageDate(value, zone)!!, today, use24Hour, java.util.Locale.US)
        val morning = stamp("2026-10-07T15:05:00+00:00")
        assertEquals("9:05 AM", morning.label)
        assertEquals("Wednesday, October 7, 2026, 9:05 AM", morning.full)
        assertEquals("09:05", stamp("2026-10-07T15:05:00Z", use24Hour = true).label)
        assertEquals("12:00 PM", stamp("2026-10-07T18:00:00+00:00").label)
        assertEquals("Yesterday, 11:59 PM", stamp("2026-10-07T05:59:00Z").label)
        assertEquals("Oct 1, 12:30 AM", stamp("2026-10-01T06:30:00Z").label)
        assertEquals("Dec 31, 2025, 8:00 AM", stamp("2025-12-31T15:00:00Z").label)
        assertEquals("2:15 PM", stamp("2026-10-07T14:15:00").label) // no offset: read as phone time, like the desktop
        for (value in listOf("", "not a date", "2026-13-45T99:00:00Z")) assertEquals(null, messageDate(value, zone))
    }

    @Test fun cardsOneSelfWorkPassStartedShowAsOneRow() {
        val now = java.time.OffsetDateTime.now().withHour(9).withMinute(0).toString()
        fun card(id: String, batch: String, kind: String = "") = dev.raticode.mobile.live.RemMessage(id, "rem", id, now,
            dev.raticode.mobile.live.RemWork("t-$id", "r-$id", "w", id, selfWork = "ticket"), "", kind = kind,
            selfWork = dev.raticode.mobile.live.RemSelfWork("ticket", batch))
        val rows = remRows(listOf(dev.raticode.mobile.live.RemMessage("u", "user", "hi", now, null, ""),
            card("a", "p1"), card("b", "p1"), card("c", "p1"), card("d", "p2"), card("e", "p1", kind = "task_update"), card("f", "p3")))
        assertEquals(listOf("day", "u", "a+b+c", "d", "e", "f"), rows.map {
            when (it) { is DayRow -> "day"; is BatchRow -> it.messages.joinToString("+") { m -> m.id }; else -> (it as MessageRow).message.id }
        })
        val batch = rows.filterIsInstance<BatchRow>().single()
        assertTrue(batch.first)
        // A reply's quote can still scroll to a card inside the batch.
        assertEquals(rows.indexOf(batch) + 1, rowIndex(rows, "b"))
        assertEquals(rows.size, rows.map { it.key }.toSet().size)
    }

    @Test fun remRowsKeepUndatedMessagesWithTheirNeighborsAndDivideRemReplies() {
        val today = java.time.LocalDate.now()
        val now = java.time.OffsetDateTime.now().withHour(9).withMinute(0).toString()
        val yesterday = java.time.OffsetDateTime.now().minusDays(1).withHour(9).toString()
        fun message(id: String, role: String, at: String) = dev.raticode.mobile.live.RemMessage(id, role, id, at, null, "")
        val rows = remRows(listOf(message("a", "rem", ""), message("b", "rem", "soon"), message("c", "user", yesterday), message("d", "rem", yesterday),
            message("e", "rem", now), message("f", "rem", now), message("g", "rem", "bad"), message("h", "user", now)), today)
        assertEquals(listOf("day:Earlier", "a", "b", "day:Yesterday", "c", "d", "day:Today", "e", "f", "g", "h"),
            rows.map { if (it is DayRow) "day:${it.label}" else (it as MessageRow).message.id })
        val messages = rows.filterIsInstance<MessageRow>().associateBy { it.message.id }
        assertEquals(listOf("b", "f", "g"), messages.values.filter { it.follows }.map { it.message.id })
        assertEquals(null, messages.getValue("g").stamp)
        assertTrue(messages.getValue("d").stamp!!.label.startsWith("Yesterday, "))
        assertEquals(rows.size, rows.map { it.key }.toSet().size)
    }

    @Test fun markdownImagesKeepTheirOrderAndAltText() {
        val document = org.commonmark.parser.Parser.builder().build().parse("Here is a cat:\n\n![A *sleepy* cat](https://example.com/cat.jpg) and ![](https://example.com/b.png)")
        val paragraph = document.firstChild.next
        assertEquals(listOf("https://example.com/cat.jpg" to "A sleepy cat", "https://example.com/b.png" to ""), markdownImages(paragraph))
        assertEquals(emptyList<Pair<String, String>>(), markdownImages(document.firstChild))
    }

    @Test fun schedulesUseDesktopWording() {
        assertEquals("Weekdays at 09:00", scheduleText(RemSchedule("weekly", "", "09:00", listOf("friday", "monday", "tuesday", "wednesday", "thursday"), 0)))
        assertEquals("Mon, Thu at 08:30", scheduleText(RemSchedule("weekly", "", "08:30", listOf("thursday", "monday"), 0)))
        assertEquals("Every 2 h", scheduleText(RemSchedule("interval", "", "", emptyList(), 120)))
        assertEquals("Once, right away", scheduleText(RemSchedule("once", "", "", emptyList(), 0)))
    }
}
