package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Board ticket keys in Rem's chat, the tickets on task cards and self-work batches, read as the desktop sends them. */
class TicketMentionsTest {
    private val fix = RemTicket("t5", "REM-5", "Fix sync", "in_progress", "rem", "")

    @Test fun keysAreWholeWordsAsOnTheDesktop() {
        assertEquals(listOf("REM-5", "AGE-18", "REM-12"), TicketMentions.keys("See REM-5. (AGE-18), REM-5 again and **REM-12**"))
        assertEquals(emptyList<String>(), TicketMentions.keys("xREM-5 REM-5a REM-5-2 a/REM-5 REM-5.2 rem-5 REM-0 R-5 REM- #REM-5 me@REM-5"))
        assertEquals(emptyList<String>(), TicketMentions.keys(""))
    }

    @Test fun onlyTicketsTheDesktopFoundBecomePills() {
        val found = TicketMentions.inText("REM-5 and REM-99 and UTF-8", mapOf("REM-5" to fix))
        assertEquals(listOf(0..4 to fix), found)
        assertEquals(emptyList<Pair<IntRange, RemTicket>>(), TicketMentions.inText("REM-5", emptyMap()))
        assertEquals("Ticket REM-5: Fix sync (In progress)", TicketMentions.label(fix))
        assertEquals("Ticket REM-6: Idea (Backlog, waiting for your approval)",
            TicketMentions.label(RemTicket("t6", "REM-6", "Idea", "backlog", "rem", "", proposed = true)))
    }

    @Test fun theDesktopsTicketsReachRemHomeAndTaskCardsBounded() {
        val view = DesktopViews.parse("rem.home", JSONObject()
            .put("tickets", JSONObject()
                .put("REM-5", JSONObject().put("ticket_id", "t5").put("key", "REM-5").put("title", "Fix sync").put("status", "in_progress").put("scope", "rem"))
                .put("AGE-18", JSONObject().put("ticket_id", "o18").put("title", "Eval").put("status", "done").put("scope", "organization").put("organization_id", "org"))
                .put("bad key", JSONObject().put("ticket_id", "x"))
                .put("REM-7", JSONObject().put("title", "No id")))
            .put("messages", JSONArray()
                .put(JSONObject().put("id", "q").put("role", "rem").put("body", "Started REM-5")
                    .put("self_work", JSONObject().put("kind", "ticket").put("batch", "p1"))
                    .put("work", JSONObject().put("task_id", "t").put("run_id", "r").put("thread_id", "w").put("title", "Fix sync").put("self_work", "ticket")
                        .put("tickets", JSONArray()
                            .put(JSONObject().put("ticket_id", "t5").put("key", "REM-5").put("title", "Fix sync").put("role", "worked"))
                            .put(JSONObject().put("ticket_id", "t9").put("key", "REM-9").put("title", "Odd").put("role", "nonsense").put("scope", "x"))
                            .put(JSONObject().put("key", "not a key")))))
                // A self-work marker without a card isn't a card in a batch.
                .put(JSONObject().put("id", "n").put("role", "rem").put("body", "x").put("self_work", JSONObject().put("batch", "p1"))))) as RemHome
        assertEquals(setOf("REM-5", "AGE-18"), view.tickets.keys)
        assertEquals("org/o18", view.tickets.getValue("AGE-18").uid)
        assertEquals("/t5", view.tickets.getValue("REM-5").uid)
        val work = view.messages[0].work!!
        assertEquals("ticket", work.selfWork)
        assertEquals(listOf("REM-5" to "worked", "REM-9" to "updated"), work.tickets.map { it.key to it.role })
        assertEquals("rem", work.tickets[1].scope)
        assertEquals(RemSelfWork("ticket", "p1"), view.messages[0].selfWork)
        assertNull(view.messages[1].selfWork)
        // An older desktop sends no tickets: nothing changes.
        assertEquals(emptyMap<String, RemTicket>(), (DesktopViews.parse("rem.home", JSONObject()) as RemHome).tickets)
    }

    @Test fun aCardListsTheLiveRunsTicketsElseTheSavedOnes() {
        val saved = RemTicketRef("t5", "REM-5", "Old", "rem", "", "worked")
        val work = RemWork("t", "r", "w", "Task", tickets = listOf(saved))
        val run = RemRun("r", "succeeded", "", "", "", false, false, "w", "")
        assertEquals(listOf(saved), TicketMentions.cardTickets(work, run))
        val renamed = saved.copy(title = "New")
        assertEquals(listOf(renamed), TicketMentions.cardTickets(work, run.copy(tickets = listOf(renamed))))
        // A key now naming another ticket isn't shown as this one.
        assertNull(TicketMentions.live(saved, mapOf("REM-5" to fix.copy(ticketId = "other"))))
        assertEquals(fix, TicketMentions.live(saved, mapOf("REM-5" to fix)))
        val task = RemTask("t", "Task", "develop", "enabled", 1, RemSchedule("once", "", "", emptyList(), 0), "", "",
            run.copy(id = "a", tickets = listOf(saved)), run.copy(id = "b", tickets = listOf(saved, RemTicketRef("t6", "REM-6", "B", "rem", "", "created"))))
        assertEquals(listOf("REM-5", "REM-6"), TicketMentions.taskTickets(task).map { it.key })
    }

    @Test fun aBatchSaysWhatStarted() {
        fun card(kind: String) = RemMessage(kind, "rem", "", "", RemWork("t", kind, "w", "T", selfWork = kind), "", selfWork = RemSelfWork(kind, "p"))
        assertEquals("Started 3 things while you were away: 1 ticket, 1 backlog ranking and 1 research run.",
            TicketMentions.batchLine(listOf(card("ticket"), card("research"), card("triage"))))
        assertEquals("Started 2 things while you were away: 2 tickets.", TicketMentions.batchLine(listOf(card("ticket"), card("ticket"))))
        assertTrue(TicketMentions.parseRefs(null).isEmpty())
    }
}
