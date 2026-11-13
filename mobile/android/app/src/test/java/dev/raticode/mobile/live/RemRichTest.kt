package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

/** The phone's rich Rem parts follow the same rules as the desktop's remRich.js, highlight.js and remChart.js. */
class RemRichTest {
    private fun states(state: String, reachedCheck: Boolean = false) = RemRich.steps(state, reachedCheck).map { it.state.name }

    @Test fun longMessagesFoldToAThirdOfTheScreenLikeTheDesktop() {
        assertEquals(RemRich.Fold(282, 423), RemRich.collapseLimits(940))
        assertEquals(RemRich.Fold(240, 360), RemRich.collapseLimits(800))
        assertEquals(RemRich.Fold(168, 252), RemRich.collapseLimits(360))
        assertEquals(RemRich.Fold(360, 540), RemRich.collapseLimits(2160))
        assertEquals(RemRich.collapseLimits(800), RemRich.collapseLimits(0))
    }

    @Test fun remsNewestMessageIsTheOneThatOpensInFull() {
        fun message(id: String, role: String) = RemMessage(id, role, "Text", "", null, "")
        assertEquals("c", RemRich.latestRemMessageId(listOf(message("a", "rem"), message("b", "user"), message("c", "rem"), message("d", "user"))))
        assertNull(RemRich.latestRemMessageId(listOf(message("b", "user"))))
    }

    @Test fun aMessageTheDesktopHoldsWhileRemRepliesShowsAsWaiting() {
        val home = DesktopViews.parse("rem.home", JSONObject().put("turn_state", "replying").put("messages", JSONArray()
            .put(JSONObject().put("id", "a").put("role", "user").put("body", "What is running?").put("created_at", "t"))
            .put(JSONObject().put("id", "b").put("role", "user").put("body", "And what finished?").put("created_at", "t").put("state", "waiting")))) as RemHome
        assertEquals(listOf(false, true), home.messages.map { RemRich.waiting(it) })
        // Rem's own messages never wait.
        assertEquals(false, RemRich.waiting(RemMessage("c", "rem", "Hi", "", null, "waiting")))
    }

    @Test fun stepsFollowTheRunAndSayTheirStateInWords() {
        assertEquals(listOf("CURRENT", "TODO", "TODO", "TODO"), states("queued"))
        assertEquals(listOf("DONE", "CURRENT", "TODO", "TODO"), states("executing"))
        assertEquals(listOf("DONE", "DONE", "CURRENT", "TODO"), states("repairing"))
        assertEquals(listOf("DONE", "DONE", "DONE", "DONE"), states("succeeded"))
        assertEquals(listOf("DONE", "STOPPED", "TODO", "TODO"), states("failed"))
        assertEquals(listOf("DONE", "DONE", "STOPPED", "TODO"), states("blocked", reachedCheck = true))
        assertTrue(RemRich.steps("mystery").isEmpty())
        assertEquals("Work in progress", RemRich.steps("executing")[1].spoken)
    }

    @Test fun aQueuedRunWhoseWorkIsDoneIsAtCheckNotPlan() {
        // A retried review, a restart's recovery check or a wait for proof requeue finished work.
        fun steps(state: String, step: String) = RemRich.steps(state, step = step).map { it.state.name }
        assertEquals(listOf("DONE", "DONE", "CURRENT", "TODO"), steps("queued", "check"))
        assertEquals(listOf("DONE", "CURRENT", "TODO", "TODO"), steps("queued", "work"))
        assertEquals(listOf("CURRENT", "TODO", "TODO", "TODO"), steps("queued", "plan"))
        // An older desktop sends no step; the state decides, as before.
        assertEquals(listOf("CURRENT", "TODO", "TODO", "TODO"), steps("queued", ""))
        assertEquals(listOf("DONE", "DONE", "STOPPED", "TODO"), steps("blocked", "check"))
        assertEquals(listOf("DONE", "DONE", "DONE", "DONE"), steps("succeeded", "plan"))
        assertTrue(RemRich.steps("mystery", step = "check").isEmpty())
        // The desktop's word for the step reaches the run, so the chat card and the task page use it.
        val home = DesktopViews.parse("rem.home", JSONObject().put("messages", JSONArray()).put("tasks", JSONArray().put(JSONObject()
            .put("task_id", "t").put("title", "T").put("active_run", JSONObject().put("run_id", "r").put("state", "queued").put("step", "check"))))) as RemHome
        assertEquals("check", home.tasks.single().activeRun!!.step)
    }

    @Test fun elapsedTimeReadsLikeTheDesktop() {
        val start = "2026-11-01T10:00:00Z"
        assertEquals("20 s", RemRich.elapsed(start, Instant.parse("2026-11-01T10:00:20Z")))
        assertEquals("4 min", RemRich.elapsed(start, Instant.parse("2026-11-01T10:04:10Z")))
        assertEquals("1 h 5 min", RemRich.elapsed(start, Instant.parse("2026-11-01T11:05:00Z")))
        assertEquals("", RemRich.elapsed(""))
    }

    @Test fun checksCarryASymbolAndWords() {
        assertEquals("✓ 3 of 3 checks passed", RemRich.checks(RemChecks(3, 0, 3))!!.let { "${it.symbol} ${it.text}" })
        assertEquals("1 of 3 checks failed", RemRich.checks(RemChecks(1, 1, 3))!!.text)
        assertEquals("2 checks not run yet", RemRich.checks(RemChecks(0, 0, 2))!!.text)
        assertEquals("1 of 2 checks passed so far", RemRich.checks(RemChecks(1, 0, 2))!!.text)
        assertNull(RemRich.checks(RemChecks(0, 0, 0)))
    }

    @Test fun cardReportLinksLeaveTheTextAndEverythingElseStays() {
        val report = "/home/u/brain/report.html"
        val body = "Saved the report.\n\nSee [the guide](https://example.com).\n\n[report](<$report>)"
        val result = RemResult("T", "research", "succeeded", RemChecks(0, 0, 0), listOf(RemArtifact("report.html", "Report", "html", 10, report)), emptyList(), "")
        assertEquals("Saved the report.\n\nSee [the guide](https://example.com).", RemRich.bodyWithoutCardLinks(body, result))
        assertEquals("Inline [report](<$report>) stays.", RemRich.bodyWithoutCardLinks("Inline [report](<$report>) stays.", result))
        assertEquals(body, RemRich.bodyWithoutCardLinks(body, null))
    }

    @Test fun repliesQuoteCopyAndShowTheirTiming() {
        assertEquals("claude-sonnet-5-5 · 4.2 s", RemRich.replyMeta(RemMeta("claude_code", "claude-sonnet-5-5", 4.24)))
        assertEquals("codex", RemRich.replyMeta(RemMeta("codex", "cli-default", null)))
        val message = RemMessage("m", "rem", "Lead.", "", null, "", details = "| a |\n|---|\n| 1 |")
        assertEquals("Lead.\n\n| a |\n|---|\n| 1 |", RemRich.copyText(message))
    }

    @Test fun aReplyLinksAnyMessageAndShowsAShortPreview() {
        val at = "2026-11-03T10:00:00+00:00"
        assertEquals(RemReference("r1", "rem", "Done. See the report.", at),
            RemRich.replyTarget(RemMessage("r1", "rem", "**Done.** See [the report](</x.html>).", at, null, "", details = "hidden")))
        assertEquals(280, RemRich.replyTarget(RemMessage("r2", "rem", "x".repeat(400), at, null, "")).body.length)
        val update = RemRich.replyTarget(RemMessage("u", "rem", "", at, RemWork("t", "r", "th", "Audit"), "", kind = "task_update",
            files = listOf(RemFile("a", "a.png", 1), RemFile("b", "b.png", 1))))
        assertEquals(listOf("task_update", "Audit", "2"), listOf(update.kind, update.title, update.files.toString()))
        assertEquals("Rem · Task update" to "Task: Audit", RemRich.referenceLines(update))
        val file = RemRich.replyTarget(RemMessage("f", "user", "", at, null, "", attachments = listOf(RemFile("a", "a.png", 1))))
        assertEquals("You" to "1 file", RemRich.referenceLines(file))
        assertEquals("an earlier message" to "No longer in this chat", RemRich.referenceLines(RemReference("gone", unavailable = true)))
    }

    @Test fun theReplyLinkGoesOnlyToADesktopThatTakesIt() {
        fun sent(params: org.json.JSONObject) = params.keys().asSequence().sorted().associateWith { params.get(it).toString() }
        val home = RemHome("idle", "", "", 0, emptyList(), emptyList())
        assertEquals(mapOf("body" to "Why?"), sent(RemRich.messageParams(" Why? ", home, replyTo = "rem-1")))
        val current = home.copy(replies = true)
        assertEquals(mapOf("body" to "Why?", "refers_to" to "rem-1"), sent(RemRich.messageParams("Why?", current, replyTo = "rem-1")))
        for (bad in listOf("x".repeat(129), " ", null)) assertEquals(mapOf("body" to "Why?"), sent(RemRich.messageParams("Why?", current, replyTo = bad)))
        assertEquals(mapOf("body" to "Why?"), sent(RemRich.messageParams("Why?", null, replyTo = "rem-1")))
        // Screen text keeps its own rule.
        val both = RemRich.messageParams("What's this?", current.copy(screenContext = true), "Chrome" to "page", "rem-1")
        assertEquals("page", both.getJSONObject("screen").getString("text")); assertEquals("rem-1", both.getString("refers_to"))
        assertEquals(false, RemRich.messageParams("What's this?", current, "Chrome" to "page").has("screen"))
    }

    @Test fun presenceNamesWhatRemIsDoing() {
        val home = RemHome("replying", "", "", 0, emptyList(), emptyList(), turnLabel = "Reading earlier messages")
        assertEquals(RemRich.Presence.THINKING to "Reading earlier messages…", RemRich.presence(true, home, "Thinking…"))
        assertEquals(RemRich.Presence.OFFLINE, RemRich.presence(false, home, "").first)
        assertEquals(RemRich.Presence.ATTENTION, RemRich.presence(true, home.copy(turnState = "idle"), "1 thing needs you").first)
        assertEquals(RemRich.Presence.IDLE, RemRich.presence(true, home.copy(turnState = "idle"), "All caught up").first)
    }

    @Test fun followUpsBelongToTheNewestRemMessage() {
        val rem = RemMessage("a", "rem", "x", "", null, "", suggestions = listOf("Why?"))
        assertEquals("a", RemRich.followUpsFor(listOf(rem)))
        assertNull(RemRich.followUpsFor(listOf(rem, RemMessage("b", "user", "y", "", null, ""))))
    }

    @Test fun codeTokensKeepEveryCharacter() {
        val code = "const x = 'a'; // note\nreturn 42;"
        val tokens = CodeHighlight.tokenize(code, "js")
        assertEquals(code, tokens.joinToString("") { it.text })
        assertEquals(listOf("KEYWORD:const", "STRING:'a'", "COMMENT:// note", "KEYWORD:return", "NUMBER:42"),
            tokens.filter { it.kind != CodeHighlight.Kind.TEXT }.map { "${it.kind}:${it.text}" })
        assertEquals(listOf("COMMENT", "KEYWORD"), CodeHighlight.tokenize("# hi\nx = None", "py").filter { it.kind != CodeHighlight.Kind.TEXT }.map { it.kind.name })
        assertEquals("ts", CodeHighlight.language("TypeScript"))
        assertEquals(listOf("TAG", "TEXT", "TAG"), CodeHighlight.tokenize("<b>x</b>", "html").map { it.kind.name })
        assertEquals(listOf(CodeHighlight.Token(CodeHighlight.Kind.TEXT, "x")), CodeHighlight.tokenize("x", "unknown"))
    }

    @Test fun chartBlocksParseToABoundedChartOrFallBack() {
        val chart = RemChartSpec.parse("""{"type":"bar","title":"Runs","labels":["Mon","Tue"],"series":[{"name":"Done","values":[3,"x"]}]}""")!!
        assertEquals(listOf("Mon", "Tue"), chart.labels)
        assertEquals(listOf(3.0, null), chart.series.single().values)
        assertNull(RemChartSpec.parse("{not json"))
        assertNull(RemChartSpec.parse("""{"type":"pie","labels":["a"],"series":[{"values":[1]}]}"""))
        assertNull(RemChartSpec.parse(JSONObject().put("type", "line").put("labels", JSONArray(List(51) { "x" }))
            .put("series", JSONArray().put(JSONObject().put("values", JSONArray().put(1)))).toString()))
        val scale = RemChartSpec("bar", "", listOf("a", "b"), listOf(RemChartSpec.Series("s", listOf(3.0, 17.0)))).scale()
        assertEquals(0.0, scale.low, 0.0)
        assertTrue(scale.high >= 17 && 0.0 in scale.ticks)
        assertTrue(chart.summary.startsWith("Bar chart: Runs. 2 points for Done"))
    }

    @Test fun remHomeReadsRichFieldsAndToleratesAnOlderDesktop() {
        val report = "/home/u/brain/r.html"
        val json = JSONObject().put("turn_state", "replying").put("turn_label", "Starting a task").put("draft_reply", "Draft").put("rich_results", true)
            .put("messages", JSONArray().put(JSONObject().put("id", "m").put("role", "rem").put("body", "Done.\n\n[r](<$report>)")
                .put("details", "More").put("suggestions", JSONArray().put("Why?").put(" ").put("Next").put("Third").put("Fourth"))
                .put("meta", JSONObject().put("provider", "codex").put("model", "gpt").put("seconds", 2.5))
                .put("result", JSONObject().put("title", "T").put("checks", JSONObject().put("passed", 1).put("total", 2))
                    .put("artifacts", JSONArray().put(JSONObject().put("name", "r.html").put("href", report).put("size", 3)).put(JSONObject().put("name", "no-href")))
                    .put("sources", JSONArray().put(JSONObject().put("url", "https://example.com")).put(JSONObject().put("url", "javascript:alert(1)"))))))
            .put("tasks", JSONArray().put(JSONObject().put("task_id", "t").put("active_run", JSONObject().put("run_id", "r").put("state", "verifying")
                .put("phase_label", "Checking the work").put("checks", JSONObject().put("passed", 1).put("failed", 0).put("total", 2)))))
        val home = DesktopViews.parse("rem.home", json) as RemHome
        assertEquals("Starting a task", home.turnLabel)
        assertEquals("Draft", home.draftReply)
        assertTrue(home.richResults)
        val message = home.messages.single()
        assertEquals(listOf("Why?", "Next", "Third"), message.suggestions)
        assertEquals(2.5, message.meta!!.seconds!!, 0.0)
        assertEquals(listOf(report), message.result!!.artifacts.map { it.href })
        assertEquals(listOf("https://example.com"), message.result!!.sources.map { it.url })
        assertEquals(RemChecks(1, 0, 2), message.result!!.checks)
        val run = home.tasks.single().activeRun!!
        assertEquals("Checking the work", run.phaseLabel)
        assertEquals(RemChecks(1, 0, 2), run.checks)
        // An older desktop sends none of it; the message is still whole.
        val old = DesktopViews.parse("rem.home", JSONObject().put("messages", JSONArray().put(JSONObject().put("id", "o").put("role", "rem").put("body", "Hi")))) as RemHome
        assertEquals("", old.messages.single().details)
        assertNull(old.messages.single().result)
        assertTrue(!old.richResults && old.turnLabel.isEmpty())
        assertEquals("media.preview:https://x.test", DesktopViews.key("media.preview", JSONObject().put("url", "https://x.test")))
    }

    private fun task(active: RemRun? = null, last: RemRun? = null, state: String = "enabled") =
        RemTask("t1", "Check notes", "errand", state, 1, RemSchedule("daily", "", "09:00", emptyList(), 0), "", "", active, last)
    private fun run(id: String, state: String, retryable: Boolean = false) =
        RemRun(id, state, "", "", "", false, false, "thread", "", reviewRetryable = retryable)

    @Test fun actionChipsComeFromTheTaskRecordOnly() {
        val report = RemArtifact("r.html", "Report", "html", 1, "/r/r.html")
        val result = RemResult("T", "errand", "blocked", RemChecks(0, 0, 0), listOf(report), emptyList(), "")
        val update = RemMessage("u", "rem", "Done.", "", RemWork("t1", "r1", "thread", "T"), "", result = result, kind = "task_update")
        fun ids(message: RemMessage = update, tasks: List<RemTask>, live: Boolean = true, retry: Boolean = true) =
            RemRich.resultActions(message, tasks, live, retry).map { it.id }
        assertEquals(listOf("open", "run"), ids(tasks = listOf(task(last = run("r1", "succeeded")))))
        val interrupted = task(last = run("r1", "blocked", retryable = true))
        assertEquals(listOf("open", "retry", "run"), ids(tasks = listOf(interrupted)))
        assertEquals("r1", RemRich.resultActions(update, listOf(interrupted), true, true)[1].runId)
        // A desktop without the phone's Retry review action never gets that chip.
        assertEquals(listOf("open", "run"), ids(tasks = listOf(interrupted), retry = false))
        // An older update about another run, a running or missing task, or a stale card: no task controls.
        assertEquals(listOf("open", "run"), ids(update.copy(work = RemWork("t1", "r0", "thread", "T")), listOf(interrupted)))
        assertEquals(listOf("open"), ids(tasks = listOf(task(active = run("r2", "executing"), last = run("r1", "blocked", true)))))
        assertEquals(listOf("open"), ids(tasks = emptyList()))
        assertTrue(ids(tasks = listOf(interrupted), live = false).isEmpty())
        // Only task updates get chips, whatever the text says.
        assertTrue(ids(update.copy(kind = "", body = "Run again? Retry review?"), listOf(interrupted)).isEmpty())
    }

    @Test fun aDraftNamesItsAttemptSoAResentReadNeverGarblesIt() {
        val first = DesktopViews.parse("rem.draft", JSONObject().put("replying", true).put("start", 0).put("text", "I'll check")
            .put("length", 10).put("reset", false).put("attempt", 1)) as RemDraftDelta
        var draft = RemRich.applyDraft(RemRich.Draft(), first)
        assertEquals(RemRich.Draft("I'll check", 1), draft)
        // The next read says which attempt the phone's ten characters came from.
        RemRich.draftRead(draft).let { assertEquals(10, it.getInt("since")); assertEquals(1L, it.getLong("attempt")) }
        // Rem started over; the desktop sends the whole new text and its attempt.
        draft = RemRich.applyDraft(draft, RemDraftDelta(true, "", 0, "Starting a worker now.", 22, true, 2))
        assertEquals(RemRich.Draft("Starting a worker now.", 2), draft)
        draft = RemRich.applyDraft(draft, RemDraftDelta(true, "", 22, " It reports back.", 39, false, 2))
        assertEquals("Starting a worker now. It reports back.", draft.text)
        // A gap starts over, and an older desktop without attempts keeps working.
        assertEquals(RemRich.Draft(), RemRich.applyDraft(draft, RemDraftDelta(true, "", 5, "x", 6, false, 2)))
        RemRich.draftRead(RemRich.Draft()).let { assertEquals(0, it.getInt("since")); assertTrue(!it.has("attempt")) }
        assertNull((DesktopViews.parse("rem.draft", JSONObject().put("replying", true)) as RemDraftDelta).attempt)
        assertNull((DesktopViews.parse("rem.draft", JSONObject().put("attempt", JSONObject.NULL)) as RemDraftDelta).attempt)
    }

    @Test fun draftDeltasAndRetryFlagsAreRead() {
        val delta = DesktopViews.parse("rem.draft", JSONObject().put("replying", true).put("turn_label", "Writing the reply")
            .put("start", 16).put("text", " for today.").put("length", 27).put("reset", false)) as RemDraftDelta
        assertEquals(RemDraftDelta(true, "Writing the reply", 16, " for today.", 27, false), delta)
        assertEquals("rem.draft", DesktopViews.key("rem.draft", JSONObject().put("since", 3)))
        val overview = DesktopViews.parse("overview", JSONObject().put("draft_deltas", true).put("review_retry", true)) as DesktopOverview
        assertTrue(overview.draftDeltas && overview.reviewRetry)
        val home = DesktopViews.parse("rem.home", JSONObject()
            .put("messages", JSONArray().put(JSONObject().put("id", "u").put("role", "rem").put("kind", "task_update")))
            .put("tasks", JSONArray().put(JSONObject().put("task_id", "t").put("last_run", JSONObject().put("run_id", "r").put("review_retryable", true))))) as RemHome
        assertEquals("task_update", home.messages.single().kind)
        assertTrue(home.tasks.single().lastRun!!.reviewRetryable)
        assertTrue("rem.run.retry_review" in DesktopViews.ACTIONS && "rem.draft" in DesktopViews.READS)
    }

    @Test fun aPausedRunHoldsItsStepAndSaysPausedInWords() {
        val steps = RemRich.steps("paused", step = "work")
        assertEquals(listOf("DONE", "PAUSED", "TODO", "TODO"), steps.map { it.state.name })
        assertEquals("Work paused", steps[1].spoken)
        assertEquals(listOf("DONE", "DONE", "PAUSED", "TODO"), RemRich.steps("paused", step = "check").map { it.state.name })
    }

    @Test fun aSelfWorkReportListsWhatItStartedAndOlderDesktopsSendNone() {
        val report = JSONObject("""{"period_id":"p1","started_at":"2026-11-12T03:00:00Z","ended_at":"2026-11-12T11:00:00Z","ended_by":"window",
            "counts":{"started":3,"finished":2,"paused":0,"blocked":1,"tickets_created":1,"tickets_moved":2,"proposals":1,"odd":"x"},
            "items":[{"task_id":"t1","run_id":"r1","thread_id":"th1","title":"Fix login","kind":"ticket","outcome":"finished",
                      "tickets":[{"ticket_id":"12","key":"REM-12","title":"Login","scope":"rem","role":"worked"}]},
                     {"task_id":"","run_id":"r2","thread_id":"th2","title":"Research caching","kind":"research","outcome":"running"},
                     {"task_id":"t3","title":"Triage","kind":"triage","outcome":"blocked"}]}""")
        val home = DesktopViews.parse("rem.home", JSONObject().put("messages", JSONArray()
            .put(JSONObject().put("id", "self-work-period-p1").put("role", "rem").put("kind", "self_work_report").put("body", "Did REM-12.")
                .put("self_work_report", report))
            .put(JSONObject().put("id", "old").put("role", "rem").put("body", "Plain"))
            .put(JSONObject().put("id", "u").put("role", "user").put("body", "Hi").put("self_work_report", report)))) as RemHome
        val message = home.messages[0]
        assertEquals("self_work_report", message.kind)
        val parsed = message.selfWorkReport!!
        assertEquals("p1", parsed.periodId); assertEquals("window", parsed.endedBy)
        assertEquals(3, parsed.counts["started"]); assertEquals(1, parsed.counts["proposals"]); assertNull(parsed.counts["odd"])
        assertEquals(listOf("Fix login", "Research caching", "Triage"), parsed.items.map { it.title })
        assertEquals(listOf("REM-12"), parsed.items[0].tickets.map { it.key })
        assertEquals("th2", parsed.items[1].threadId); assertEquals("", parsed.items[1].taskId)
        assertEquals(listOf("Finished", "Still running", "Needs you"), parsed.items.map { dev.raticode.mobile.ui.RemTaskStatus.selfWorkOutcome(it.outcome).second.label })
        assertTrue(parsed.items.none { dev.raticode.mobile.ui.RemTaskStatus.selfWorkOutcome(it.outcome).second.spinning })
        // An older desktop sends no report, and only Rem's messages carry one.
        assertNull(home.messages[1].selfWorkReport)
        assertNull(home.messages[2].selfWorkReport)
    }

    @Test fun aSelfWorkReportKeepsAtMostThirtyItems() {
        val items = JSONArray().apply { repeat(40) { put(JSONObject().put("task_id", "t$it").put("title", "T$it").put("outcome", "finished")) } }
        val home = DesktopViews.parse("rem.home", JSONObject().put("messages", JSONArray().put(JSONObject().put("id", "m").put("role", "rem")
            .put("kind", "self_work_report").put("body", "").put("self_work_report", JSONObject().put("items", items))))) as RemHome
        assertEquals(30, home.messages.single().selfWorkReport!!.items.size)
        assertTrue(home.messages.single().selfWorkReport!!.counts.isEmpty())
    }
}
