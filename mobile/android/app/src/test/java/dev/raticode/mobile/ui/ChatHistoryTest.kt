package dev.raticode.mobile.ui

import dev.raticode.mobile.live.LiveClient.LiveMessage
import org.junit.Assert.*
import org.junit.Test

class ChatHistoryTest {
    private fun thought(id: String, group: String = "turn", trace: Map<String, String> = emptyMap()) = LiveMessage(id, "thread", "**Review**", "Rem", kind = "thought", groupId = group, trace = trace)

    @Test fun groupsThoughtsAndToolUpdatesWhileKeepingFinalAndTurnBoundaries() {
        val messages = listOf(thought("summary"), thought("start", trace = mapOf("id" to "tool", "kind" to "tool", "title" to "bash", "input" to "pwd")),
            thought("finish", trace = mapOf("id" to "tool", "kind" to "tool", "title" to "Tool result", "output" to "/project", "status" to "completed")),
            LiveMessage("final", "thread", "Done", "Rem", kind = "final"), thought("next", "next-turn"), LiveMessage("empty", "thread", "", "Rem"))
        val items = buildChatItems(messages)
        assertEquals(3, items.size)
        assertTrue(items[0].thoughts)
        assertEquals("final", items[1].id)
        assertEquals(2, thoughtEntries(items[0].messages).size)
        val tool = thoughtEntries(items[0].messages).last()
        assertEquals("bash", tool.trace["title"])
        assertEquals("pwd", tool.trace["input"])
        assertEquals("/project", tool.trace["output"])
        assertTrue(items[2].thoughts)
    }

    @Test fun fragmentsJoinWithoutDuplicateAttachmentOrDroppingAttachmentOnlyMessages() {
        val first = LiveMessage("part-0", "thread", "First ", "Rem", sourceId = "reply")
        val second = first.copy(id = "part-4000", text = "second", offset = 6)
        val file = LiveMessage("upload", "thread", "", "Completed", attachments = listOf(dev.raticode.mobile.live.LiveClient.Attachment("file", "README.md", 42)))
        val items = buildChatItems(listOf(first, second, file))
        assertEquals(2, items.size)
        assertEquals("First second", items[0].messages.first().text)
        assertEquals("README.md", items[1].messages.first().attachments.single().name)
    }
    @Test fun duplicateFinalOutputAndTokenMetadataDoNotBecomeExtraThoughtEntries() {
        val answer = LiveMessage("final", "thread", "A final answer", "Rem", kind = "final")
        val duplicate = thought("echo").copy(text = "**A final answer**")
        assertEquals(listOf("final"), buildChatItems(listOf(duplicate, answer)).map { it.id })
        assertTrue(thoughtEntries(listOf(thought("metadata").copy(text = "tokens used: 1,234"))).isEmpty())
    }


    @Test fun consecutiveThoughtsFormOneGroupLikeDesktopEvenAcrossGroupIdsAndLegacyMessages() {
        val items = buildChatItems(listOf(thought("a", "first"), thought("b", "second"), thought("c", ""), thought("d", ""),
            LiveMessage("final", "thread", "Answer", "Rem", kind = "final")))
        assertEquals(listOf(true, false), items.map { it.thoughts })
        assertEquals(listOf("a", "b", "c", "d"), items[0].messages.map { it.id })
    }

    @Test fun groupsKnowTheReplyThatFollowsThemSoTheyStayOpenUntilItArrives() {
        val user = LiveMessage("ask", "thread", "Do it", "Completed")
        val items = buildChatItems(listOf(user, thought("t1"), LiveMessage("summary", "thread", "", "Rem", kind = "turn-summary"),
            LiveMessage("reply", "thread", "Done", "Rem", kind = "final"), LiveMessage("again", "thread", "More", "Queued on phone"), thought("t2", "next")))
        val groups = items.filter { it.thoughts }
        assertEquals("reply", groups[0].replyId)
        assertNull("The latest group has no reply yet, so it stays open", groups[1].replyId)
        val errorOnly = buildChatItems(listOf(thought("t3"), LiveMessage("err", "thread", "Failed", "Rem", kind = "error")))
        assertNull(errorOnly.first().replyId)
    }

    @Test fun desktopRestartNoticeIsHiddenButOtherSystemNotesStay() {
        val items = buildChatItems(listOf(LiveMessage("x", "thread", "The previous process was interrupted. Resuming.", "Desktop"),
            LiveMessage("y", "thread", "Model changed to gpt-5", "Desktop")))
        assertEquals(listOf("y"), items.map { it.id })
    }

    @Test fun toolUpdatesMergeOnlyWithinTheirOwnGroup() {
        val tool = mapOf("id" to "call-1", "kind" to "tool", "title" to "bash")
        val entries = thoughtEntries(listOf(thought("a", "first", tool), thought("b", "first", tool + ("output" to "ok")), thought("c", "second", tool)))
        assertEquals(2, entries.size)
        assertEquals("ok", entries[0].trace["output"])
    }

    @Test fun turnTimingMatchesDesktopWording() {
        val zone = java.time.ZoneId.of("UTC"); val locale = java.util.Locale.US
        val quick = turnTiming("2026-11-01T09:05:00Z", 400, zone, locale)
        assertTrue(quick, quick.startsWith("9:05") && quick.endsWith(" · Ran for <1s"))
        assertTrue(turnTiming("2026-11-01T09:05:00Z", 62_400, zone, locale).endsWith(" · Ran for 1m 2s"))
        assertEquals("Completed · Ran for 5s", turnTiming("", 5_000, zone, locale))
        assertEquals("Completed · Ran for <1s", turnTiming("not a date", null, zone, locale))
    }

    @Test fun finishedMessagesDropTheirDeliveryNoteButPendingOnesKeepIt() {
        assertNull(deliveryNote("Completed"))
        assertNull(deliveryNote("completed"))
        assertEquals("Queued on phone", deliveryNote("Queued on phone"))
        assertEquals("Desktop rejected request", deliveryNote("Desktop rejected request"))
    }

    @Test fun hiddenContextBetweenThoughtsNeitherShowsNorSplitsTheGroup() {
        val items = buildChatItems(listOf(thought("a"), LiveMessage("memory", "thread", "Earlier: compacted notes", "Rem", kind = "memory"),
            LiveMessage("context", "thread", "Prior turn", "Desktop", kind = "continuation-context"), thought("b"),
            LiveMessage("final", "thread", "Answer", "Rem", kind = "final")))
        assertEquals(listOf(true, false), items.map { it.thoughts })
        assertEquals(listOf("a", "b"), items[0].messages.map { it.id })
        assertTrue(items.none { item -> item.messages.any { it.kind in setOf("memory", "continuation-context") } })
    }
}
