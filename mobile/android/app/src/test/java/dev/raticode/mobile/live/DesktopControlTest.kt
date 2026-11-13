package dev.raticode.mobile.live

import dev.raticode.mobile.protocol.v2.MobileCapabilities
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.util.UUID

/** Using Raticode from the phone: strict result records, reassembly and the desktop's real projections. */
class DesktopControlTest {
    /** Produced by the desktop's projection code with disposable test data. */
    private val desktop = JSONObject(javaClass.classLoader!!.getResource("desktop-results.json")!!.readText())

    private fun result(payload: JSONObject, thread: String? = null) = JSONObject().put("type", "desktop.result")
        .put("request_id", UUID.randomUUID().toString()).put("thread_id", thread ?: JSONObject.NULL).put("payload", payload)

    private fun part(op: String = "rem.home", part: Int = 0, parts: Int = 1, body: String = "{}") =
        JSONObject().put("op", op).put("ok", true).put("part", part).put("parts", parts).put("body", body)

    @Test fun resultRecordsAreStrict() {
        MobileCapabilities.validateIncomingType("desktop.result")
        IncomingValidation.validate(result(part()))
        IncomingValidation.validate(result(JSONObject().put("op", "organizations.task").put("ok", false).put("part", 0).put("parts", 1)
            .put("body", "").put("code", "conflict").put("detail", "Task changed. Read it before updating.")))
        for (bad in listOf(part(op = "terminal.run"), part(parts = 0), part(part = 1, parts = 1), part(parts = DesktopViews.MAX_PARTS + 1),
            part(body = "x".repeat(24001)), part().put("path", "/etc"), part().put("code", "crash"))) {
            assertThrows(IllegalArgumentException::class.java) { IncomingValidation.validate(result(bad)) }
        }
        assertThrows(IllegalArgumentException::class.java) { IncomingValidation.validate(result(part(), UUID.randomUUID().toString())) }
        // The phone never accepts the requests it sends.
        assertThrows(IllegalArgumentException::class.java) { MobileCapabilities.validateIncomingType("desktop.action") }
    }

    @Test fun partsJoinOnceInAnyOrderAndConflictsFail() {
        val text = desktop.getJSONObject("rem.home").toString()
        val chunks = text.chunked(200)
        val parts = DesktopParts()
        chunks.indices.reversed().forEach { i ->
            val joined = parts.add("r1", part(part = i, parts = chunks.size, body = chunks[i]))
            if (i > 0) assertNull(joined) else assertEquals(text, joined.toString().let { JSONObject(it).toString() })
        }
        parts.add("r2", part(part = 0, parts = 2, body = "{\"a\":"))
        assertThrows(IllegalArgumentException::class.java) { parts.add("r2", part(part = 0, parts = 2, body = "{\"b\":")) }
        assertThrows(IllegalArgumentException::class.java) { parts.add("r2", part(part = 1, parts = 3, body = "1}")) }
        val bounded = DesktopParts(limit = 2)
        repeat(3) { bounded.add("old$it", part(part = 0, parts = 2, body = "{")) }
        // The oldest abandoned request was dropped, so its final part starts over.
        assertNull(bounded.add("old0", part(part = 1, parts = 2, body = "}")))
    }

    /** Android's org.json has no depth limit; a body nested thousands deep must be refused before it is parsed. */
    @Test fun deeplyNestedResultsAreRefusedBeforeParsing() {
        val deep = "[".repeat(20000) + "]".repeat(20000)
        val chunks = ("{\"a\":$deep}").chunked(24000)
        val parts = DesktopParts()
        chunks.dropLast(1).forEachIndexed { i, c -> assertNull(parts.add("deep", part(part = i, parts = chunks.size, body = c))) }
        val error = assertThrows(IllegalArgumentException::class.java) {
            parts.add("deep", part(part = chunks.lastIndex, parts = chunks.size, body = chunks.last()))
        }
        assertEquals("Result nested too deeply", error.message)
        // Brackets inside strings don't count, and the limit itself is allowed.
        val atLimit = "{\"s\":\"[[[[{{{{\\\"\"," + "\"a\":" + "[".repeat(DesktopParts.MAX_DEPTH - 1) + "]".repeat(DesktopParts.MAX_DEPTH - 1) + "}"
        assertNotNull(DesktopParts().add("ok", part(body = atLimit)))
        assertFalse(DesktopParts.nestingWithin("[".repeat(DesktopParts.MAX_DEPTH + 1), DesktopParts.MAX_DEPTH))
    }

    @Test fun actionsRefreshTheScreenTheyChange() {
        assertEquals("rem.home", DesktopViews.key("rem.task.create", JSONObject()))
        assertEquals("rem.thread:t1", DesktopViews.key("rem.instruct", JSONObject().put("thread_id", "t1")))
        assertEquals("organizations.read:o1", DesktopViews.key("organizations.task", JSONObject().put("organization_id", "o1")))
        assertEquals("settings.get", DesktopViews.key("settings.update", JSONObject()))
        assertTrue(DesktopViews.READS.none { it in DesktopViews.ACTIONS })
    }

    @Test fun remChatFilesAndImagesUseTheDesktopsChannel() {
        val channel = UUID.randomUUID().toString()
        val home = DesktopViews.parse("rem.home", JSONObject().put("files_thread", channel).put("media", true).put("messages", org.json.JSONArray()
            .put(JSONObject().put("id", "m1").put("role", "user").put("body", "See this")
                .put("attachments", org.json.JSONArray().put(JSONObject().put("file_id", "f1").put("name", "notes.txt").put("size", 12))))
            .put(JSONObject().put("id", "m2").put("role", "assistant").put("body", "Sent")
                .put("files", org.json.JSONArray().put(JSONObject().put("file_id", "f2").put("name", "report.html").put("size", 2048)))))) as RemHome
        assertEquals(channel, home.filesThread); assertTrue(home.media)
        assertEquals("notes.txt", home.messages[0].attachments.single().name)
        assertEquals(RemFile("f2", "report.html", 2048), home.messages[1].files.single())
        // A malformed channel is ignored, so the phone never uploads to an unknown thread.
        assertNull((DesktopViews.parse("rem.home", JSONObject().put("files_thread", "../x")) as RemHome).filesThread)
        assertFalse((DesktopViews.parse("overview", JSONObject()) as DesktopOverview).media)
        // Read state is sent to the desktop only when its overview says it accepts it.
        assertFalse((DesktopViews.parse("overview", JSONObject()) as DesktopOverview).threadRead)
        assertTrue((DesktopViews.parse("overview", JSONObject().put("thread_read", true)) as DesktopOverview).threadRead)
        // Image reads have their own screen key and are a known operation in desktop results.
        assertEquals(ChatImages.KEY + "https://example.com/cat.jpg", DesktopViews.key("media.image", JSONObject().put("url", "https://example.com/cat.jpg")))
        IncomingValidation.validate(result(part(op = "media.image")))
        assertTrue(ChatImages.fetchable("https://upload.example.org/cat.jpg"))
        for (bad in listOf("file:///sdcard/x.png", "content://x/y", "javascript:alert(1)", "https://user:pw@example.com/a.png", "https://" + "a".repeat(2050)))
            assertFalse(bad, ChatImages.fetchable(bad))
        val png = java.util.Base64.getEncoder().encodeToString(byteArrayOf(1, 2, 3))
        assertEquals(3, ChatImages.bytes(JSONObject().put("mime", "image/png").put("data", png)).size)
        assertThrows(IllegalArgumentException::class.java) { ChatImages.bytes(JSONObject().put("mime", "text/html").put("data", png)) }
    }

    @Test fun remChatAttachmentsStayWhileTheirMessageIsQueued() {
        val id = UUID.randomUUID().toString()
        val queued = JSONObject().put("outbox", org.json.JSONArray().put(JSONObject().put("type", "desktop.action")
            .put("payload", JSONObject().put("op", "rem.message").put("params", JSONObject().put("body", "").put("attachment_ids", org.json.JSONArray().put(id)))).toString()))
        assertTrue(LiveJournal.referencesFile(queued, id))
        assertFalse(LiveJournal.referencesFile(queued, UUID.randomUUID().toString()))
    }

    /** desktop-replies.json is a real rem.home result from the desktop's own code, captured with a reply in it. */
    @Test fun parsesTheDesktopsRepliesAsSent() {
        val json = JSONObject(javaClass.classLoader!!.getResource("desktop-replies.json")!!.readText())
        val home = DesktopViews.parse("rem.home", json) as RemHome
        assertTrue(home.replies)
        val (linked, missing) = home.messages.filter { it.role == "user" }
        assertEquals("rem-1", linked.refersTo)
        val reference = linked.referenced!!
        assertEquals(listOf("rem-1", "rem", "The deploy failed: see the log.", false), listOf(reference.id, reference.role, reference.body, reference.unavailable))
        assertEquals(home.messages.first { it.id == "rem-1" }.createdAt, reference.createdAt)
        assertEquals(RemReference("gone", unavailable = true), missing.referenced)
        // Rem's replies refer to nothing, and an older desktop says nothing about replies.
        assertTrue(home.messages.filter { it.role == "rem" }.all { it.refersTo.isEmpty() && it.referenced == null })
        assertFalse((DesktopViews.parse("rem.home", JSONObject()) as RemHome).replies)
        // A preview must belong to the message it names.
        val forged = JSONObject().put("messages", org.json.JSONArray().put(JSONObject().put("id", "u").put("role", "user").put("body", "x")
            .put("refers_to", "a").put("referenced", JSONObject().put("id", "b").put("body", "other"))))
        assertNull((DesktopViews.parse("rem.home", forged) as RemHome).messages.single().referenced)
    }

    @Test fun parsesTheDesktopsRealResults() {
        val overview = DesktopViews.parse("overview", desktop.getJSONObject("overview")) as DesktopOverview
        assertEquals(setOf("Notes", "Code"), overview.projects.map { it.second }.toSet())
        val home = DesktopViews.parse("rem.home", desktop.getJSONObject("rem.home")) as RemHome
        assertEquals("task1", home.messages[1].work?.taskId)
        val task = home.tasks.single()
        assertEquals("weekly", task.schedule.kind); assertEquals(listOf("monday"), task.schedule.weekdays)
        assertTrue(task.activeRun!!.active); assertEquals("Notes", task.project)
        val detail = DesktopViews.parse("rem.task:task1", desktop.getJSONObject("rem.task")) as RemTask
        assertEquals("Look for failed builds", detail.prompt); assertEquals(listOf("Report failures"), detail.criteria)
        assertEquals(2, detail.runs.size)
        val thread = DesktopViews.parse("rem.thread:thread1", desktop.getJSONObject("rem.thread")) as RemThread
        assertTrue(thread.running); assertEquals(listOf("t1"), thread.messages.map { it.id })
        @Suppress("UNCHECKED_CAST")
        val orgs = DesktopViews.parse("organizations.list", desktop.getJSONObject("organizations.list")) as List<OrganizationSummary>
        assertEquals(listOf("Notes"), orgs.single().projects)
        val org = DesktopViews.parse("organizations.read:x", desktop.getJSONObject("organizations.read")) as Organization
        val ship = org.tasks.single()
        assertEquals("Lead", ship.assignee); assertTrue(ship.needsApproval); assertEquals("todo", ship.status)
        assertEquals("CTO", org.employees.single().title)
        val settings = DesktopViews.parse("settings.get", desktop.getJSONObject("settings.get")) as DesktopSettings
        assertTrue(settings.available); assertEquals("dark", settings.values["appearance.theme"])
        assertEquals(12.5, (settings.values["terminal.fontSize"] as Number).toDouble(), 0.0)
        val provider = settings.items.first { it.key == "assistant.provider" }
        assertEquals("enum", provider.kind); assertTrue(provider.options.any { it.first == "codex" })
        assertFalse(settings.items.any { it.key.startsWith("browser.") })
        val usage = DesktopViews.parse("usage.overview", desktop.getJSONObject("usage.overview")) as DesktopUsage
        assertEquals(40.0, usage.accounts.single().windows.single().usedPercent!!, 0.0)
        assertEquals(12, usage.calls)
    }
}
