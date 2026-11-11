package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

/** Rem's chat alerts: the desktop's `rem.notices` answer, and what an alert may say with and without the user's opt-in. */
class RemNoticesTest {
    /** Produced by the desktop's `rem.notices` projection with disposable test data. */
    private val desktop = JSONObject("""{"cursor": 3, "notices": [
        {"id": "reply-5a1d8f0e-3c2b-4a10-9d7e-0f1e2d3c4b5a", "kind": "reply",
         "body": "**Yes.** The [build log](https://ci.example/1) shows `0 failures`.", "created_at": "2026-11-02T18:59:15.809099+00:00",
         "title": "", "needs_user": false},
        {"id": "finished-run-1", "kind": "task_update", "body": "Needs you: approve the deploy.",
         "created_at": "2026-11-02T18:59:15.809139+00:00", "title": "Deploy check", "needs_user": true}],
        "skipped": 0, "more": false, "digest": "64c6517e8b53ae080aabc393d14161ce"}""")

    @Test fun noticesAreTheirOwnReadAndParse() {
        assertTrue("rem.notices" in DesktopViews.READS && "rem.notices" !in DesktopViews.ACTIONS)
        // Not "rem.home": an alert read must never replace the chat on screen.
        assertEquals("rem.notices", DesktopViews.key("rem.notices", JSONObject().put("after", 3)))
        val read = DesktopViews.parse("rem.notices", desktop) as RemNotices
        assertEquals(3L, read.cursor)
        assertEquals(listOf("reply", "task_update"), read.notices.map { it.kind })
        assertEquals("Deploy check", read.notices[1].title)
        assertTrue(read.notices[1].needsUser && !read.more && read.skipped == 0)
        assertEquals(1793645955809L, RemNoticeText.millis(read.notices[0].createdAt))
        assertNull(RemNoticeText.millis("yesterday"))
        assertTrue((DesktopViews.parse("overview", JSONObject().put("rem_notices", true)) as DesktopOverview).remNotices)
        assertFalse((DesktopViews.parse("overview", JSONObject()) as DesktopOverview).remNotices)
    }

    @Test fun alertsShowRemsWordsOnlyAfterOptIn() {
        val (reply, update) = RemNoticeText.parse(desktop).notices
        // Off by default: no reply text and no task name.
        assertEquals("New message", RemNoticeText.line(reply, showText = false))
        assertEquals("A task needs you", RemNoticeText.line(update, showText = false))
        assertEquals("Task update", RemNoticeText.line(update.copy(needsUser = false), showText = false))
        assertEquals("You replied", RemNoticeText.reply("Ship it to prod", showText = false))
        // Opted in: one readable line without Markdown or link targets.
        assertEquals("Yes. The build log shows 0 failures.", RemNoticeText.line(reply, showText = true))
        assertEquals("Ship it", RemNoticeText.reply("**Ship** it", showText = true))
        assertEquals("New message", RemNoticeText.line(reply.copy(body = "```\n```"), showText = true))
        assertEquals("Plan\nNext", RemNoticeText.plain("# Plan\n- Next").replace(" ", "\n"))
        val long = RemNoticeText.plain("word ".repeat(400))
        assertTrue(long.length <= 600 && long.endsWith("…"))
        assertNull(RemNoticeText.earlier(0))
        assertEquals("1 earlier message from Rem", RemNoticeText.earlier(1))
        assertEquals("12 earlier messages from Rem", RemNoticeText.earlier(12))
    }

    @Test fun answersAreBounded() {
        val many = JSONArray((0 until 20).map { JSONObject().put("id", "m$it").put("kind", "other").put("body", "x".repeat(3000)) })
        val read = RemNoticeText.parse(JSONObject().put("cursor", 9).put("notices", many).put("skipped", -4).put("more", true))
        assertEquals(RemNoticeText.MAX, read.notices.size)
        assertTrue(read.notices.all { it.kind == "reply" && it.body.length == 1500 })
        assertEquals(0, read.skipped)
        assertTrue(read.more)
        for (bad in listOf(JSONObject().put("cursor", -1), JSONObject().put("cursor", 1L shl 53), JSONObject())) {
            assertThrows(Exception::class.java) { RemNoticeText.parse(bad) }
        }
    }

    @Test fun queuedAlertReadsAreDroppedBeforeReconnecting() {
        fun event(type: String, op: String) = JSONObject().put("type", type).put("payload", JSONObject().put("op", op).put("params", JSONObject())).toString()
        val state = JSONObject().put("outbox", JSONArray(listOf(event("desktop.read", "rem.notices"), event("desktop.action", "rem.message"),
            event("desktop.read", "rem.home"))))
        assertTrue(dropQueuedNoticeReads(state))
        assertEquals(listOf("rem.message", "rem.home"), (0 until 2).map { JSONObject(state.getJSONArray("outbox").getString(it)).getJSONObject("payload").getString("op") })
        assertFalse(dropQueuedNoticeReads(state))
    }

    private fun include(params: JSONObject) = params.optJSONArray("include")?.let { list -> (0 until list.length()).map { list.getString(it) } }

    @Test fun onlyADesktopThatAdvertisesTheFilterIsSentIt() {
        val choices = listOf(RemAlertRules.NEEDS_YOU, RemAlertRules.FINISHED)
        // The older request, exactly: no new field whatever the switches say.
        val older = RemNoticeText.readParams(7, filterSupported = false, include = choices, mark = "m1")
        assertEquals(setOf("after"), older.keys().asSequence().toSet())
        assertEquals(setOf<String>(), RemNoticeText.readParams(null, false, choices, null).keys().asSequence().toSet())
        // A supporting desktop with no switch on gets the older request too.
        assertEquals(setOf("after"), RemNoticeText.readParams(7, true, emptyList(), "m1").keys().asSequence().toSet())

        val filtered = RemNoticeText.readParams(7, true, choices, "m1")
        assertEquals(7L, filtered.getLong("after"))
        assertEquals(choices, include(filtered))
        assertEquals("m1", filtered.getString("mark"))
        // The first filtered read has no mark, so the desktop only reports where it is.
        assertFalse(RemNoticeText.readParams(7, true, choices, "").has("mark"))
        assertFalse(RemNoticeText.readParams(7, true, choices, null).has("mark"))
        assertTrue((DesktopViews.parse("overview", JSONObject().put("rem_notices_filter", true)) as DesktopOverview).remNoticesFilter)
        assertFalse((DesktopViews.parse("overview", JSONObject().put("rem_notices", true)) as DesktopOverview).remNoticesFilter)
    }

    @Test fun filteredAnswersParseAndStayBounded() {
        val read = RemNoticeText.parse(JSONObject("""{"cursor": 12, "mark": "r42:o9", "skipped": 0, "more": false, "notices": [
            {"id": "m1", "kind": "task_update", "reason": "needs_you", "body": "Approve", "created_at": "2026-11-09T10:00:00+00:00", "title": "T", "needs_user": true},
            {"id": "o1", "kind": "organization", "reason": "finished", "body": "Done", "created_at": "2026-11-09T10:00:00+00:00", "title": "T", "needs_user": false},
            {"id": "round-3", "kind": "proposals", "body": "", "created_at": "2026-11-09T10:00:00+00:00", "title": "", "needs_user": false},
            {"id": "odd", "kind": "reply", "reason": "surprise", "body": "x", "created_at": "", "title": "", "needs_user": false}]}"""))
        assertEquals("r42:o9", read.mark)
        assertEquals(listOf("task_update", "organization", "proposals", "reply"), read.notices.map { it.kind })
        // A batch always counts under proposals; a reason this app doesn't know counts as none.
        assertEquals(listOf("needs_you", "finished", "proposals", ""), read.notices.map { it.reason })
        // An opaque position can't be shortened, so one that is too long is dropped, and null is no mark.
        assertEquals("", RemNoticeText.parse(JSONObject().put("cursor", 1).put("mark", "x".repeat(257))).mark)
        assertEquals("", RemNoticeText.parse(JSONObject().put("cursor", 1).put("mark", JSONObject.NULL)).mark)
        assertEquals("", RemNoticeText.parse(JSONObject().put("cursor", 1)).mark)
    }

    @Test fun onlyFilteredReadsAreDroppedWhenADesktopStopsAdvertising() {
        fun event(op: String, params: JSONObject) = JSONObject().put("type", "desktop.read").put("payload", JSONObject().put("op", op).put("params", params)).toString()
        val state = JSONObject().put("outbox", JSONArray(listOf(event("rem.notices", JSONObject().put("after", 3).put("include", JSONArray(listOf("finished")))),
            event("rem.notices", JSONObject().put("after", 3)), event("rem.home", JSONObject().put("include", "x")))))
        assertTrue(dropQueuedNoticeFilters(state))
        assertEquals(2, state.getJSONArray("outbox").length())
        assertFalse(JSONObject(state.getJSONArray("outbox").getString(0)).getJSONObject("payload").getJSONObject("params").has("include"))
        assertFalse(dropQueuedNoticeFilters(state))
    }
}
