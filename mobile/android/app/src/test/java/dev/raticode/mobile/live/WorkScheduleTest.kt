package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

/**
 * An organization's work schedule on the phone. The two reads are real answers from the desktop's phone protocol
 * (desktop tests/unit/test_device_organizations_v2.py builds the same organization): one before any schedule is
 * applied, one after saving the suggested schedule in America/New_York.
 */
class WorkScheduleTest {
    private fun read(name: String) = JSONObject(javaClass.getResource("/desktop/$name")!!.readText())
    private fun plan(json: JSONObject) = (DesktopViews.parse("organizations.read:x", json) as Organization).plan!!

    private fun block(id: String, label: String = id, cadence: String = "daily", start: String = "08:00", end: String = "12:00",
                      days: List<Int> = emptyList(), monthDay: Int? = null, nth: Int? = null, weekday: Int? = null, quarter: Int = 1) =
        OrgScheduleBlock(id, label, "", cadence, start, end, days, monthDay, nth, weekday, quarter)

    /** JSON with sorted keys, so two objects compare by content. */
    private fun canonical(value: Any?): String = when (value) {
        is JSONObject -> value.keys().asSequence().sorted().joinToString(",", "{", "}") { "\"$it\":" + canonical(value.get(it)) }
        is JSONArray -> (0 until value.length()).joinToString(",", "[", "]") { canonical(value.get(it)) }
        is String -> JSONObject.quote(value)
        else -> value.toString()
    }

    @Test fun anUnscheduledOrganizationCarriesTheSuggestedScheduleButNoBlocks() {
        val schedule = plan(read("organizations-read-v2.json")).schedule!!
        assertFalse(schedule.configured)
        assertTrue(schedule.blocks.isEmpty())
        assertNull(schedule.current)
        assertNull(schedule.next)
        assertEquals("UTC", schedule.timezone)
        assertEquals("", schedule.changesAt)
        assertEquals(7, schedule.template.size)
        assertEquals(listOf("New feature work", "Application hardening"), schedule.template.take(2).map { it.label })
        val audit = schedule.template[5]
        assertEquals("monthly", audit.cadence)
        assertEquals(1, audit.nthWeek)
        assertEquals(0, audit.weekday)
        assertNull(audit.monthDay)
        assertEquals("Monthly, 1st Monday, 09:00–12:00", audit.whenText)
        assertEquals(listOf(4), schedule.template[4].days)
        // The template already passes the phone's own checks.
        assertNull(WorkSchedule.problem(schedule.template))
        assertNull(WorkSchedule.clash(schedule.template))
    }

    @Test fun aSavedScheduleSaysWhatIsOnNowAndNext() {
        val schedule = plan(read("organizations-read-schedule.json")).schedule!!
        assertTrue(schedule.configured)
        assertEquals("America/New_York", schedule.timezone)
        assertEquals(7, schedule.blocks.size)
        assertEquals(listOf("Fridays 13:00–17:00", "Monthly, 1st Monday, 09:00–12:00", "Quarterly, 1st Tuesday of the 1st month, 09:00–17:00"),
            schedule.blocks.drop(4).map { it.whenText })
        val now = schedule.current!!
        assertEquals("features", now.block.id)
        assertEquals("2026-11-10T22:00:00+00:00", now.endsAt)
        // The current block's times come from the saved list.
        assertEquals("08:00", WorkSchedule.saved(schedule, now.block).start)
        assertEquals("Application hardening", schedule.next!!.label)
        val at = Instant.parse("2026-11-10T15:00:00Z")
        assertEquals("17:00", WorkSchedule.clock(now.endsAt, schedule.timezone, at))
        assertEquals("17:00 tomorrow", WorkSchedule.clock("2026-11-11T22:00:00+00:00", schedule.timezone, at))
        assertEquals("Mon 09:00", WorkSchedule.clock("2026-11-16T14:00:00+00:00", schedule.timezone, at))
        assertEquals("Dec 7, 09:00", WorkSchedule.clock("2026-12-07T14:00:00+00:00", schedule.timezone, at))
        assertEquals("", WorkSchedule.clock("", schedule.timezone, at))
    }

    @Test fun anOlderDesktopHasNoSchedule() {
        val older = read("organizations-read-schedule.json").apply { remove("schedule") }
        assertNull(plan(older).schedule)
        assertNotNull(plan(older).workers["lead"])
    }

    @Test fun readsWarningsAndWhoWorksUnderWhichBlock() {
        val schedule = WorkSchedule.parse(JSONObject().put("timezone", "Europe/Paris").put("configured", true)
            .put("blocks", JSONArray().put(WorkSchedule.blockJson(block("a")).put("when", "Every day 08:00–12:00")))
            .put("issues", JSONArray().put(JSONObject().put("kind", "gap").put("text", "Nothing is scheduled 12:00–08:00 every day."))
                .put(JSONObject().put("kind", "outside_hours").put("text", "")))
            .put("underway", JSONArray().put(JSONObject().put("employee_id", "lead").put("name", "Lead").put("task_id", "t1").put("title", "Fix login")
                .put("check_in", false).put("block_label", "A")).put(JSONObject().put("employee_id", "dev").put("check_in", true))))!!
        assertEquals(listOf(OrgScheduleIssue("gap", "Nothing is scheduled 12:00–08:00 every day.")), schedule.issues)
        assertEquals(OrgUnderway("lead", "Lead", "t1", "Fix login", false, "A"), schedule.underway[0])
        assertTrue(schedule.underway[1].checkIn)
        assertEquals("", schedule.underway[1].blockLabel)
        assertEquals("Every day 08:00–12:00", schedule.blocks.single().whenText)
        assertNull(WorkSchedule.parse(null))
    }

    @Test fun theOverviewSaysWhetherTheDesktopTakesScheduleSaves() {
        assertTrue((DesktopViews.parse("overview", JSONObject().put("organizations_v2", true).put("organization_schedule", true)) as DesktopOverview).organizationSchedule)
        assertFalse((DesktopViews.parse("overview", JSONObject().put("organizations_v2", true)) as DesktopOverview).organizationSchedule)
        assertTrue(WorkSchedule.OP in DesktopViews.ACTIONS && WorkSchedule.OP !in DesktopViews.READS)
        // A save answers with the organization's read, which replaces the open organization.
        assertEquals("organizations.read:o1", DesktopViews.key(WorkSchedule.OP, JSONObject().put("organization_id", "o1")))
    }

    @Test fun describesBlocksAsTheDesktopDoes() {
        val saved = plan(read("organizations-read-schedule.json")).schedule!!.blocks
        saved.forEach { assertEquals(it.whenText, WorkSchedule.describe(it.copy(whenText = ""))) }
        assertEquals("Weekdays 22:00–06:00", WorkSchedule.describe(block("a", cadence = "weekly", start = "22:00", end = "06:00", days = listOf(0, 1, 2, 3, 4))))
        assertEquals("Weekends all day", WorkSchedule.describe(block("a", cadence = "weekly", start = "00:00", end = "00:00", days = listOf(5, 6))))
        assertEquals("Mon, Wed 06:00 for 24 hours", WorkSchedule.describe(block("a", cadence = "weekly", start = "06:00", end = "06:00", days = listOf(0, 2))))
        assertEquals("Monthly, last day, 09:00–10:00", WorkSchedule.describe(block("a", cadence = "monthly", start = "09:00", end = "10:00", monthDay = 31)))
        assertEquals("Quarterly, last Friday of the 3rd month, 09:00–10:00",
            WorkSchedule.describe(block("a", cadence = "quarterly", start = "09:00", end = "10:00", nth = -1, weekday = 4, quarter = 3)))
        assertEquals("Ends the next day", WorkSchedule.span("17:00", "00:00"))
        assertTrue(WorkSchedule.span("08:00", "08:00").startsWith("All day"))
        assertEquals("", WorkSchedule.span("08:00", "17:00"))
        assertEquals(24 * 60, WorkSchedule.length(block("a", start = "08:00", end = "08:00")))
        assertEquals(7 * 60, WorkSchedule.length(block("a", start = "17:00", end = "00:00")))
    }

    @Test fun checksBlocksWithTheDesktopsRules() {
        assertNull(WorkSchedule.problem(block("a")))
        assertEquals("Give each block a name", WorkSchedule.problem(block("a", label = " ")))
        assertEquals("a: use 24-hour times such as 08:00 and 17:30", WorkSchedule.problem(block("a", start = "8:00")))
        assertEquals("a: use 24-hour times such as 08:00 and 17:30", WorkSchedule.problem(block("a", end = "24:00")))
        assertEquals("a: choose the weekdays it runs on", WorkSchedule.problem(block("a", cadence = "weekly")))
        assertTrue(WorkSchedule.problem(block("a", cadence = "monthly"))!!.contains("either a day of the month"))
        assertTrue(WorkSchedule.problem(block("a", cadence = "monthly", monthDay = 3, nth = 1, weekday = 0))!!.contains("either a day of the month"))
        assertEquals("a: choose which weekday and which week", WorkSchedule.problem(block("a", cadence = "monthly", nth = 1)))
        assertNull(WorkSchedule.problem(block("a", cadence = "quarterly", nth = -1, weekday = 6, quarter = 3)))
        assertNotNull(WorkSchedule.problem(block("a", cadence = "quarterly", monthDay = 1, quarter = 4)))
        assertNotNull(WorkSchedule.problem(block("-a")))
        assertNotNull(WorkSchedule.problem(block("a").copy(keywords = (1..13).map { "k$it" })))
        assertNotNull(WorkSchedule.problem(block("a").copy(keywords = listOf("x".repeat(41)))))
        assertEquals("Each block needs its own ID", WorkSchedule.problem(listOf(block("a"), block("a", label = "b"))))
        assertTrue(WorkSchedule.validZone("America/New_York") && WorkSchedule.validZone("UTC"))
        assertFalse(WorkSchedule.validZone("Mars/Base") || WorkSchedule.validZone(""))
    }

    @Test fun findsOverlapsThatNeitherBlockWins() {
        // The desktop's own example: same cadence, same length, overlapping.
        assertEquals("A" to "B", WorkSchedule.clash(listOf(block("a", "A", start = "08:00", end = "12:00"), block("b", "B", start = "10:00", end = "14:00"))))
        // A shorter block or a longer cadence takes precedence.
        assertNull(WorkSchedule.clash(listOf(block("a", start = "08:00", end = "12:00"), block("b", start = "10:00", end = "13:00"))))
        assertNull(WorkSchedule.clash(listOf(block("a"), block("b", cadence = "weekly", days = listOf(0)))))
        // Back to back is no overlap; overnight wraps into the next day.
        assertNull(WorkSchedule.clash(listOf(block("a", start = "08:00", end = "12:00"), block("b", start = "12:00", end = "16:00"))))
        assertNotNull(WorkSchedule.clash(listOf(block("a", start = "22:00", end = "02:00"), block("b", start = "01:00", end = "05:00"))))
        assertNull(WorkSchedule.clash(listOf(block("a", cadence = "weekly", days = listOf(0)), block("b", cadence = "weekly", days = listOf(1)))))
        assertNotNull(WorkSchedule.clash(listOf(block("a", cadence = "weekly", start = "22:00", end = "02:00", days = listOf(6)),
            block("b", cadence = "weekly", start = "00:00", end = "04:00", days = listOf(0)))))
        // The 31st falls on the last day, which the last Friday sometimes is.
        assertNotNull(WorkSchedule.clash(listOf(block("a", cadence = "monthly", monthDay = 31), block("b", cadence = "monthly", nth = -1, weekday = 4))))
        assertNull(WorkSchedule.clash(listOf(block("a", cadence = "monthly", monthDay = 1), block("b", cadence = "monthly", nth = -1, weekday = 4))))
        assertNull(WorkSchedule.clash(listOf(block("a", cadence = "quarterly", monthDay = 1, quarter = 1), block("b", cadence = "quarterly", monthDay = 1, quarter = 2))))
    }

    @Test fun buildsTheSaveAsTheContractFixtureDoes() {
        val blocks = listOf(
            OrgScheduleBlock("features", "New feature work", "Build planned features.", "daily", "08:00", "17:00", keywords = listOf("feature"),
                whenText = "Every day 08:00–17:00"),
            OrgScheduleBlock("weekly-release", "Weekly release", cadence = "weekly", start = "13:00", end = "17:00", days = listOf(4)),
            OrgScheduleBlock("audit", "Dependency audit", cadence = "monthly", start = "09:00", end = "12:00", nthWeek = 1, weekday = 0),
            OrgScheduleBlock("review", "Quarterly review", cadence = "quarterly", start = "09:00", end = "17:00", monthDay = 1, quarterMonth = 1))
        val params = WorkSchedule.params("org1", 4, blocks, "America/New_York", "UTC")
        // src/gofer/devices/protocol/v2/fixtures/desktop.action.organizations-schedule.json
        val fixture = JSONObject("""{"organization_id":"org1","expected_revision":4,"timezone":"America/New_York","blocks":[
            {"block_id":"features","label":"New feature work","focus":"Build planned features.","cadence":"daily","start":"08:00","end":"17:00","keywords":["feature"]},
            {"block_id":"weekly-release","label":"Weekly release","focus":"","cadence":"weekly","days":[4],"start":"13:00","end":"17:00","keywords":[]},
            {"block_id":"audit","label":"Dependency audit","focus":"","cadence":"monthly","nth_week":1,"weekday":0,"start":"09:00","end":"12:00","keywords":[]},
            {"block_id":"review","label":"Quarterly review","focus":"","cadence":"quarterly","quarter_month":1,"month_day":1,"start":"09:00","end":"17:00","keywords":[]}]}""")
        assertEquals(canonical(fixture), canonical(params))
        // "when" is the desktop's to write, and an unchanged time zone stays off the request.
        assertFalse(params.getJSONArray("blocks").getJSONObject(0).has("when"))
        assertFalse(WorkSchedule.params("org1", 4, blocks, "UTC", "UTC").has("timezone"))
        assertFalse(WorkSchedule.params("org1", 4, blocks, " ", "UTC").has("timezone"))
        assertEquals(0, WorkSchedule.params("org1", 4, emptyList(), "UTC", "UTC").getJSONArray("blocks").length())
    }

    @Test fun editingKeepsOnlyTheFieldsTheCadenceUses() {
        val original = plan(read("organizations-read-schedule.json")).schedule!!.blocks[5]
        // Unchanged, the desktop's description stays.
        val same = WorkSchedule.finish(original, original.keywords, original, false, emptyList())
        assertEquals(original, same)
        val weekly = WorkSchedule.finish(WorkSchedule.withCadence(original, "weekly"), original.keywords, original, false, emptyList())
        assertEquals(listOf(0, 1, 2, 3, 4), weekly.days)
        assertNull(weekly.nthWeek); assertNull(weekly.weekday); assertNull(weekly.monthDay)
        assertEquals("", weekly.whenText)
        assertEquals("Weekdays 09:00–12:00", WorkSchedule.describe(weekly))
        val monthly = WorkSchedule.withCadence(block("x", " Nightly  "), "monthly")
        assertEquals(1, monthly.monthDay)
        val made = WorkSchedule.finish(monthly.copy(focus = " Ship it "), WorkSchedule.keywords("release, Release ,  ,ship"), WorkSchedule.blank(), true,
            listOf("nightly"))
        assertEquals("nightly-2", made.id)
        assertEquals("Nightly", made.label)
        assertEquals("Ship it", made.focus)
        assertEquals(listOf("release", "ship"), made.keywords)
        assertNull(WorkSchedule.problem(made))
        assertEquals("block", WorkSchedule.newId("  !! ", emptyList()))
        assertEquals("qa-review-2", WorkSchedule.newId("QA review", listOf("qa-review")))
        assertTrue(WorkSchedule.newId("x".repeat(300), emptyList()).length <= 100)
        // Mid-edit state survives exactly, untrimmed.
        val editing = block("a", " A ").copy(focus = "f ", keywords = listOf("k"), whenText = "w")
        assertEquals(listOf(editing, monthly), WorkSchedule.decode(WorkSchedule.encode(listOf(editing, monthly))))
        assertEquals(WorkSchedule.sendable(listOf(original)), WorkSchedule.sendable(listOf(same)))
    }
}
