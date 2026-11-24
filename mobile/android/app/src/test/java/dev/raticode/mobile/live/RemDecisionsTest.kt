package dev.raticode.mobile.live

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The phone reads Rem's decisions and task-update questions from the fields the desktop sends, and sends changes it can check. */
class RemDecisionsTest {
    private val list = JSONObject("""{"revision":7,"waiting":1,"provisional":1,"confirmed":1,"more":2,"decisions":[
        {"id":"D-3","topic":"checks.fast","question":"Which command runs the fast checks?","answer":"npm test","recommended":"npm test",
         "why":"package.json defines it.","status":"pending","project":"Site","decided_by":"","history":[{"at":"t","actor":"rem","action":"requested"}],
         "evidence":[{"kind":"quote","ref":"package.json","excerpt":"vitest"},{"kind":"command","ref":"npm test"},{"kind":"file","ref":""}],
         "alternatives":[{"answer":"make check","why_not":"No Makefile."},{"answer":""}]},
        {"id":"D-4","question":"Integration branch?","answer":"dev","status":"provisional","decided_by":"rem","decided_at":"2026-11-19T10:00:00+00:00"},
        {"id":"D-1","question":"Where do decisions go?","answer":"docs/adr","status":"confirmed","decided_by":"user","decided_at":"2026-11-02T10:00:00+00:00"},
        {"id":"D-5","question":"Which branch?","answer":"dev","status":"confirmed","decided_by":"user","decided_at":"2026-11-20T10:00:00+00:00",
         "project":"Raticode","carried_over":true,"last_check":null},
        {"id":"D-6","question":"Which check?","answer":"make check","status":"confirmed","decided_by":"user","decided_at":"2026-11-20T10:00:00+00:00",
         "project":"Raticode","last_check":{"passed":false,"exit_code":2,"ran_at":"2026-11-21T08:00:00+00:00","seconds":4.5}},
        {"id":"D-2","question":"Old","answer":"x","status":"superseded","superseded_by":"D-1"},
        {"id":"D-0","question":"Nope","status":"rejected","history":[{"actor":"user","action":"rejected","note":"Not now"}]},
        {"question":"no id"}]}""")

    @Test fun aWaitingQuestionSaysWhichTicketAskedItAndWhatWaits() {
        val parsed = DesktopViews.parse(RemDecisions.READ, JSONObject("""{"revision":3,"decisions":[
            {"id":"D-5","question":"Which check?","answer":"npm test","status":"pending","asked":{"by":"run","label":"REM-1","ticket":"REM-1",
             "task_id":"task-1","task_title":"Place work on computers","at":"2026-11-21T12:00:00+00:00",
             "waiting":[{"run_id":"run-1","task_id":"task-1","ticket":"REM-1","label":"REM-1"}],"went_on":[],"opens":"task-1"}},
            {"id":"D-6","question":"Which branch?","answer":"dev","status":"pending","asked":{"label":"Rem's chat","at":"2026-11-21T12:00:00+00:00",
             "waiting":[],"went_on":[{"run_id":"run-2","task_id":"task-2","ticket":"REM-2","label":"REM-2"},
             {"run_id":"run-3","task_id":"task-3","ticket":"","label":"Tidy the docs"}],"opens":"task-2"}},
            {"id":"D-1","question":"Old","answer":"x","status":"confirmed"}]}""")) as RemDecisionList
        val (asked, chat, decided) = parsed.decisions
        val line = RemDecisions.askedLine(asked)
        assertTrue(line, line.startsWith("Asked by REM-1 · "))
        assertTrue(line, line.endsWith(" · REM-1 waits for this"))
        assertEquals("task-1", asked.asked?.opens)
        assertEquals("Place work on computers", asked.asked?.taskTitle)
        assertTrue(RemDecisions.askedLine(chat).endsWith(" · REM-2 and Tidy the docs went on without it"))
        assertEquals("", RemDecisions.askedLine(decided))
        assertEquals("21 Nov", RemDecisions.shortDay("2026-11-21T12:00:00"))
        assertEquals("", RemDecisions.shortDay("soon"))
    }

    @Test fun aWrittenRecordIsRetiredNotForgotten() {
        val parsed = DesktopViews.parse(RemDecisions.READ, JSONObject("""{"revision":3,"decisions":[
            {"id":"D-9","question":"Which database?","answer":"SQLite","status":"confirmed","remember_as":"record","record":{},
             "record_path":".raticode/decisions/0009-which-database.md"},
            {"id":"D-10","question":"Which queue?","answer":"Redis","status":"confirmed","remember_as":"record","record":{}},
            {"id":"D-11","question":"Which check?","answer":"make check","status":"confirmed"},
            {"id":"D-12","question":"Which cache?","answer":"None","status":"rejected","remember_as":"record","record":{},
             "record_path":".raticode/decisions/0012-which-cache.md","history":[{"actor":"user","action":"retired"}]}]}""")) as RemDecisionList
        val (written, unwritten, fact, retired) = parsed.decisions
        assertEquals("Retire", RemDecisions.removalLabel(written))
        assertEquals("Forget", RemDecisions.removalLabel(unwritten))
        assertEquals("Forget", RemDecisions.removalLabel(fact))
        assertTrue(RemDecisions.written(retired))
        assertEquals("Retired", RemDecisions.closedLabel(retired))
        assertEquals("Retired", RemDecisions.historyRows(retired).first().action)
        // Retire is its own action, since it commits the record's file; the desktop refuses forget for one.
        assertEquals("retire", RemDecisions.removalAction(written))
        assertEquals("forget", RemDecisions.removalAction(unwritten))
        assertEquals("retire", RemDecisions.changeParams(RemDecisions.removalAction(written), written.id, 3)!!.optString("action"))
    }

    @Test fun anAskAgainQuestionShowsTheCurrentAnswerAndKeepsTheDecision() {
        val parsed = DesktopViews.parse(RemDecisions.READ, JSONObject("""{"revision":4,"decisions":[
            {"id":"D-8","topic":"deploy","question":"Where does it deploy?","answer":"","recommended":"","status":"pending",
             "supersedes":"D-2","reopened":"You asked again.","asked_again":true,"current_answer":"Netlify"},
            {"id":"D-9","topic":"checks.fast","question":"Which check?","answer":"make check","recommended":"make check","status":"queued",
             "supersedes":"D-3","reopened":"You asked again.","asked_again":true,"current_answer":"npm test","fresh":true},
            {"id":"D-2","topic":"deploy","question":"Where does it deploy?","answer":"Netlify","status":"confirmed","decided_by":"user"},
            {"id":"D-7","question":"Which check?","status":"pending","reopened":"AGENTS.md changed since it was decided.",
             "current_answer":"ignored without asked_again"}]}""")) as RemDecisionList
        val (asked, fresh, kept, stale) = parsed.decisions
        assertTrue(asked.askedAgain)
        assertEquals("Netlify", asked.currentAnswer)
        assertEquals("You asked again", RemDecisions.reopenedLine(asked))
        assertEquals("You asked again. Rem read the project's files again.", RemDecisions.reopenedLine(fresh))
        assertEquals("Asked again: AGENTS.md changed since it was decided.", RemDecisions.reopenedLine(stale))
        assertEquals("", stale.currentAnswer)
        assertFalse(kept.askedAgain)
        assertEquals("", RemDecisions.reopenedLine(kept))
        // Only a topic a project scan finds promises a fresh look; every one says the answer stays until you choose.
        assertTrue("files again" in RemDecisions.askAgainDescription("checks.fast"))
        assertFalse("files again" in RemDecisions.askAgainDescription("deploy"))
        assertTrue(RemDecisions.askAgainDescription("deploy").endsWith("This answer stays in force until you choose."))
    }

    @Test fun decisionRequestsShareOneScreen() {
        assertTrue(RemDecisions.READ in DesktopViews.READS)
        assertTrue(RemDecisions.CHANGE in DesktopViews.ACTIONS)
        assertFalse(RemDecisions.CHANGE in DesktopViews.READS)
        assertEquals(RemDecisions.READ, DesktopViews.key(RemDecisions.CHANGE, JSONObject().put("id", "D-3")))
        assertEquals(RemDecisions.READ, DesktopViews.key(RemDecisions.READ, JSONObject()))
    }

    @Test fun listParsesIntoWaitingDecidedAndHistory() {
        val parsed = DesktopViews.parse(RemDecisions.READ, list) as RemDecisionList
        assertEquals(7L, parsed.revision)
        assertEquals(2, parsed.more)
        // An entry without an ID can't be changed, so it isn't shown.
        assertEquals(listOf("D-3", "D-4", "D-1", "D-5", "D-6", "D-2", "D-0"), parsed.decisions.map { it.id })
        val groups = RemDecisions.grouped(parsed)
        // Rem's own auto-mode answer waits for the user beside the requests.
        assertEquals(listOf("D-3", "D-4"), groups[RemDecisions.Section.WAITING]!!.map { it.id })
        assertEquals(listOf("D-1", "D-5", "D-6"), groups[RemDecisions.Section.DECIDED]!!.map { it.id })
        assertEquals(listOf("D-2", "D-0"), groups[RemDecisions.Section.HISTORY]!!.map { it.id })
        val first = parsed.decisions.first()
        assertEquals("Site", first.project)
        // Empty evidence and alternatives are dropped.
        assertEquals(2, first.evidence.size)
        assertEquals(1, first.alternatives.size)
        assertEquals("quote package.json · ran npm test", RemDecisions.evidenceLine(first.evidence))
        assertEquals("quote package.json · +1 more", RemDecisions.evidenceLine(first.evidence, max = 1))
        assertEquals("Replaced by D-1", RemDecisions.closedLabel(parsed.decisions[5]))
        assertEquals("Turned down", RemDecisions.closedLabel(parsed.decisions[6]))
        // A decision carried over from what Raticode already did, and Raticode's own last check run.
        assertEquals("Carried over on 2026-11-20 · Raticode · D-5", RemDecisions.decidedLine(parsed.decisions[3]))
        assertEquals("", RemDecisions.checkLine(parsed.decisions[3]))
        assertEquals("Raticode's last run of it failed (exit 2) on 2026-11-21", RemDecisions.checkLine(parsed.decisions[4]))
        assertEquals(RemDecisionCheck(false, 2, "2026-11-21T08:00:00+00:00"), parsed.decisions[4].lastCheck)
        assertEquals("You decided on 2026-11-02 · About you · D-1", RemDecisions.decidedLine(parsed.decisions[2]))
        assertEquals("Rem decided (auto mode) on 2026-11-19 · About you · D-4", RemDecisions.decidedLine(parsed.decisions[1]))
    }

    @Test fun aTappedAlertOpensItsDecisionsSectionWithItFirst() {
        val parsed = DesktopViews.parse(RemDecisions.READ, list) as RemDecisionList
        assertEquals(RemDecisions.Section.WAITING, RemDecisions.sectionOf(parsed, "D-4"))
        // Answered since the alert: it opens where it is now.
        assertEquals(RemDecisions.Section.DECIDED, RemDecisions.sectionOf(parsed, "D-6"))
        assertEquals(null, RemDecisions.sectionOf(parsed, "D-99"))
        val decided = RemDecisions.grouped(parsed)[RemDecisions.Section.DECIDED]!!
        assertEquals(listOf("D-6", "D-1", "D-5"), RemDecisions.focusedFirst(decided, "D-6").map { it.id })
        assertEquals(listOf("D-1", "D-5", "D-6"), RemDecisions.focusedFirst(decided, null).map { it.id })
    }

    @Test fun queuedQuestionsShowAsNMoreQueuedUnderWaitingNotAsCards() {
        val json = JSONObject("""{"revision":9,"waiting":1,"queued":2,"decisions":[
            {"id":"D-3","question":"Which command runs the fast checks?","status":"pending"},
            {"id":"D-8","question":"Which port?","recommended":"8080","status":"queued"},
            {"id":"D-7","question":"Which region?","recommended":"eu","status":"queued"},
            {"id":"D-1","question":"Where do decisions go?","answer":"docs/adr","status":"confirmed"}]}""")
        val parsed = DesktopViews.parse(RemDecisions.READ, json) as RemDecisionList
        assertEquals(2, parsed.queued)
        assertEquals("2 more queued", RemDecisions.queuedLine(parsed))
        val groups = RemDecisions.grouped(parsed)
        // Neither a waiting card nor history: a queued question is only counted.
        assertEquals(listOf("D-3"), groups[RemDecisions.Section.WAITING]!!.map { it.id })
        assertEquals(listOf("D-1"), groups[RemDecisions.Section.DECIDED]!!.map { it.id })
        assertTrue(groups[RemDecisions.Section.HISTORY]!!.isEmpty())
        assertNull(RemDecisions.section(parsed.decisions[1]))
        // An older desktop sends no count, and nothing is queued.
        assertEquals("", RemDecisions.queuedLine(DesktopViews.parse(RemDecisions.READ, list) as RemDecisionList))
        // A task update's card for it says it is queued, with no buttons, until it moves up.
        val card = RemDecisionCard("D-8", "Which port?", "8080", status = "queued")
        assertEquals(RemDecisions.CardState.Queued, RemDecisions.cardState(card, "queued"))
        assertEquals(RemDecisions.CardState.Open(false), RemDecisions.cardState(card, "pending"))
    }

    @Test fun planQuestionsGroupUnderThePlanTitleWithNOfMAnswered() {
        val json = JSONObject("""{"revision":12,"waiting":4,"queued":3,"decisions":[
            {"id":"D-20","question":"Where do offline notes live?","recommended":"SQLite","status":"pending","plan":"P-1"},
            {"id":"D-21","question":"How do conflicts merge?","recommended":"Last write wins","status":"pending","plan":"P-1"},
            {"id":"D-5","question":"Which command runs the fast checks?","recommended":"make check","status":"pending"},
            {"id":"D-22","question":"Which sync port?","recommended":"8443","status":"queued","plan":"P-1"},
            {"id":"D-23","question":"Who sees sync errors?","recommended":"The user","status":"queued","plan":"P-1"},
            {"id":"D-9","question":"Which region?","recommended":"eu","status":"queued"},
            {"id":"D-18","question":"Sync on metered data?","answer":"No","status":"confirmed","plan":"P-1"},
            {"id":"D-30","question":"Old plan question","answer":"Yes","status":"confirmed","plan":"P-0"}],
            "plans":[{"id":"P-1","title":"Offline sync","goal":"Notes keep working offline","project":"Notes","status":"asking",
                "answered":1,"total":5,"waiting":2,"queued":2},
              {"id":"P-0","title":"","project":"Notes","status":"handed_back","answered":1,"total":1},
              {"title":"No ID"}]}""")
        val parsed = DesktopViews.parse(RemDecisions.READ, json) as RemDecisionList
        assertEquals(listOf("P-1", "P-0"), parsed.plans.map { it.id })
        assertEquals("Plan P-0", parsed.plans[1].title)
        assertEquals("P-1", parsed.decisions[0].plan)
        assertEquals("", parsed.decisions[2].plan)
        val (group) = RemDecisions.planGroups(parsed)
        assertEquals("Offline sync", group.plan.title)
        assertEquals(listOf("D-20", "D-21"), group.waiting.map { it.id })
        assertEquals("1 of 5 answered", RemDecisions.planLine(group.plan))
        assertEquals("2 more come up as you answer", RemDecisions.planQueuedLine(group.plan))
        // The question that belongs to no plan stays on its own, and only its queue is counted under Waiting.
        val waiting = RemDecisions.grouped(parsed)[RemDecisions.Section.WAITING]!!
        assertEquals(listOf("D-5"), RemDecisions.looseWaiting(parsed, waiting).map { it.id })
        assertEquals("1 more queued", RemDecisions.queuedLine(parsed))
        // A plan handed back to Rem groups nothing: its answers are ordinary decisions now.
        assertEquals(listOf("D-18", "D-30"), RemDecisions.grouped(parsed)[RemDecisions.Section.DECIDED]!!.map { it.id })
        // An older desktop sends no plans: every waiting question is listed on its own.
        val older = DesktopViews.parse(RemDecisions.READ, JSONObject(json.toString()).apply { remove("plans") }) as RemDecisionList
        assertTrue(RemDecisions.planGroups(older).isEmpty())
        assertEquals(3, RemDecisions.looseWaiting(older, RemDecisions.grouped(older)[RemDecisions.Section.WAITING]!!).size)
        assertEquals("3 more queued", RemDecisions.queuedLine(older))
        assertEquals("", RemDecisions.planQueuedLine(RemDecisionPlan("P-3", "x")))
    }

    @Test fun changesCarryTheRevisionAndOnlyTheirOwnFields() {
        val use = RemDecisions.changeParams("use", "D-3", 7)!!
        assertEquals(setOf("action", "id", "expected_revision"), use.keys().asSequence().toSet())
        assertEquals(7L, use.getLong("expected_revision"))
        val change = RemDecisions.changeParams("change", "D-3", 7, answer = "  pnpm test  ", reason = "ignored")!!
        assertEquals("pnpm test", change.getString("answer"))
        assertFalse(change.has("reason"))
        // Change and Edit need words; a reason is optional and only goes with Turn down.
        assertNull(RemDecisions.changeParams("change", "D-3", 7, answer = " "))
        assertNull(RemDecisions.changeParams("edit", "D-3", 7))
        assertEquals("Not now", RemDecisions.changeParams("reject", "D-3", 7, answer = "Not now", reason = "Not now")!!.getString("reason"))
        assertFalse(RemDecisions.changeParams("reject", "D-3", 7, reason = " ")!!.has("reason"))
        assertFalse(RemDecisions.changeParams("forget", "D-3", 7, answer = "x")!!.has("answer"))
        // Look again scans the decision's project; the phone never names a folder.
        assertEquals(setOf("action", "id", "expected_revision"), RemDecisions.changeParams("scan", "D-5", 7)!!.keys().asSequence().toSet())
        // Adding a decision names a project folder, so it stays on the desktop.
        assertNull(RemDecisions.changeParams("add", "D-3", 7, answer = "x"))
        assertNull(RemDecisions.changeParams("use", "", 7))
        assertEquals(RemDecisions.ANSWER_MAX, RemDecisions.changeParams("change", "D-3", 7, answer = "x".repeat(2000))!!.getString("answer").length)
    }

    private val records = JSONObject("""{"revision":11,"decisions":[
        {"id":"D-6","question":"These decisions conflict: D-3, D-4. Replace them with one?","recommended":"Store sessions in SQLite.","status":"pending",
         "remember_as":"record","record":{"context":"c","applies_to":["src/sessions/**"]},"supersedes_all":["D-3","D-4",7,""],
         "conflict":"D-3 keeps Redis while D-4 removes it.","record_path":"","record_error":""},
        {"id":"D-4","question":"Where do sessions live?","answer":"Store sessions in SQLite.","why":"One file to back up.","status":"confirmed",
         "decided_by":"user","project":"Site","remember_as":"record","record_path":"docs/decisions/0004-sessions.md","edited_outside":true,
         "record":{"context":"Sessions lived in Redis.","options":"Keep Redis.","consequences":"One database.","check":"No Redis client.",
           "applies_to":["src/sessions/**","","x"],"forbids":["src/**/redis*.py"],"signals":["redis"]}},
        {"id":"D-5","question":"Which ADR folder?","answer":"docs/decisions","status":"confirmed","project":"Site","remember_as":"record",
         "record":{},"record_error":"The project isn't a git repository."},
        {"id":"D-2","question":"Which command runs the fast checks?","answer":"npm test","status":"confirmed","project":"Site",
         "remember_as":"fact","record":{"context":"not a record"},"record_path":"docs/x.md"},
        {"id":"D-1","question":"How should Rem write?","answer":"Plainly.","status":"confirmed"}]}""")

    @Test fun recordsParseWithTheirScopeFileAndConflict() {
        val parsed = DesktopViews.parse(RemDecisions.READ, records) as RemDecisionList
        val (fix, record, unwritten, fact, memory) = parsed.decisions
        // A conflict's fix: what it replaces (IDs only) and why.
        assertEquals(listOf("D-3", "D-4"), fix.supersedesAll)
        assertEquals("Replaces D-3, D-4", RemDecisions.replacesLine(fix.supersedesAll))
        assertEquals("", RemDecisions.replacesLine(emptyList()))
        assertEquals("D-3 keeps Redis while D-4 removes it.", fix.conflict)
        assertTrue(fix.isRecord)
        // A record in force: its parts, the file in the repository, and that it was edited outside Raticode.
        assertEquals("record", record.rememberAs)
        assertEquals(RemDecisionRecord("Sessions lived in Redis.", "Keep Redis.", "One database.", "No Redis client.",
            listOf("src/sessions/**", "x"), listOf("src/**/redis*.py"), listOf("redis")), record.record)
        assertEquals("docs/decisions/0004-sessions.md", record.recordPath)
        assertTrue(record.editedOutside)
        assertEquals("The project isn't a git repository.", unwritten.recordError)
        assertEquals(RemDecisionRecord(), unwritten.record)
        // A fact carries no record, whatever came with it; a decision about you is neither.
        assertFalse(fact.isRecord)
        assertNull(fact.record)
        assertEquals("fact", memory.rememberAs)
        assertFalse(memory.isRecord)
        // Decided splits into how Rem works here and the design decisions.
        val decided = RemDecisions.grouped(parsed)[RemDecisions.Section.DECIDED]!!
        val (facts, designs) = RemDecisions.decidedParts(decided)
        assertEquals(listOf("D-2", "D-1"), facts.map { it.id })
        assertEquals(listOf("D-4", "D-5"), designs.map { it.id })
    }

    @Test fun theHistoryListsEachEntrysDateWhoAndWhatNewestFirst() {
        val parsed = RemDecisions.parse(JSONObject("""{"revision":3,"decisions":[
            {"id":"D-7","question":"Where do logs go?","answer":"One file per run","status":"confirmed","remember_as":"record","record":{},
             "record_path":"docs/decisions/0007-logs.md","history":[
               {"at":"2026-11-17T09:00:00+00:00","actor":"rem","action":"requested"},
               {"at":"2026-11-18T10:00:00+00:00","actor":"user","action":"confirmed","note":"user"},
               {"at":"2026-11-19T11:00:00+00:00","actor":"user","action":"edited","note":"Clearer wording"},
               {"at":"2026-11-20T12:00:00+00:00","actor":"raticode","action":"kept the approved text","note":"A Rem run changed docs/decisions/0007-logs.md; only the user changes decisions."},
               {"at":"2026-11-21T13:00:00+00:00","actor":"user","action":"superseded","note":"D-9"},
               {"at":"2026-11-21T14:00:00+00:00","actor":"worker","action":"looked twice"},
               {"actor":"user"}]},
            {"id":"D-1","question":"How should Rem write?","answer":"Plainly.","status":"confirmed"}]}"""))
        val (record, plain) = parsed.decisions
        assertEquals(listOf(
            RemDecisions.HistoryRow("2026-11-21", "Worker", "Looked twice", ""),
            RemDecisions.HistoryRow("2026-11-21", "You", "Replaced by D-9", ""),
            RemDecisions.HistoryRow("2026-11-20", "Raticode", "Kept the approved text", "A Rem run changed docs/decisions/0007-logs.md; only the user changes decisions."),
            RemDecisions.HistoryRow("2026-11-19", "You", "Edited", "Clearer wording"),
            RemDecisions.HistoryRow("2026-11-18", "You", "Confirmed", ""),
            RemDecisions.HistoryRow("2026-11-17", "Rem", "Asked", ""),
        ), RemDecisions.historyRows(record))
        assertEquals(emptyList<RemDecisions.HistoryRow>(), RemDecisions.historyRows(plain))
        // The desktop sends at most ten entries; the phone keeps the newest ten of whatever arrives.
        val long = RemDecisions.parse(JSONObject().put("revision", 1).put("decisions", org.json.JSONArray().put(JSONObject()
            .put("id", "D-2").put("question", "q").put("status", "confirmed").put("history", org.json.JSONArray((1..14).map {
                JSONObject().put("at", "2026-11-%02dT00:00:00Z".format(it)).put("actor", "rem").put("action", "rechecked")
            })))))
        val rows = RemDecisions.historyRows(long.decisions.single())
        assertEquals(10, rows.size)
        assertEquals("2026-11-14", rows.first().date)
        assertEquals("Checked again", rows.first().action)
    }

    @Test fun aRecordARemRunChangedSaysTheFileDiffersInsteadOfNotInTheRepo() {
        val parsed = RemDecisions.parse(JSONObject("""{"revision":4,"decisions":[
            {"id":"D-7","question":"Where do logs go?","answer":"One file per run","status":"confirmed","remember_as":"record","record":{},
             "record_path":"docs/decisions/0007-logs.md","record_restore":true,"record_error":"The landing branch checkout has uncommitted changes."},
            {"id":"D-8","question":"Where do caches go?","answer":"In the data folder","status":"confirmed","remember_as":"record","record":{},
             "record_path":"","record_error":"The landing branch checkout has uncommitted changes."},
            {"id":"D-9","question":"Which check?","answer":"make check","status":"confirmed","remember_as":"fact","record_restore":true},
            {"id":"D-10","question":"Old","answer":"x","status":"confirmed","remember_as":"record","record":{},"record_path":"docs/decisions/0010-old.md"}]}"""))
        val (restoring, unwritten, fact, clean) = parsed.decisions
        assertTrue(restoring.recordRestore)
        assertEquals("File differs" to "The file differs from the approved text; Raticode puts it back when the checkout is clean",
            RemDecisions.fileNote(restoring))
        assertEquals("Not in the repo yet" to "The landing branch checkout has uncommitted changes.", RemDecisions.fileNote(unwritten))
        assertFalse("only a design decision has a file", fact.recordRestore)
        assertNull(RemDecisions.fileNote(fact))
        assertNull(RemDecisions.fileNote(clean))
        // An older desktop doesn't send the flag.
        assertFalse(clean.recordRestore)
    }

    @Test fun recordPathsShowHowManyTrackedFilesTheyMatch() {
        val parsed = RemDecisions.parse(JSONObject("""{"revision":3,"decisions":[
            {"id":"D-4","question":"Release flow?","answer":"dev to stage","status":"confirmed","remember_as":"record",
             "record":{"applies_to":["github/workflows/**","docs/releasing.md"],"forbids":["vendor/**","secrets/**"]},
             "path_counts":{"branch":"dev","applies_to":{"github/workflows/**":0,"docs/releasing.md":1},
               "forbids":{"vendor/**":0,"secrets/**":3,"bad":-1,"worse":"7"}}},
            {"id":"D-7","question":"Old desktop","answer":"x","status":"confirmed","remember_as":"record","record":{"applies_to":["src/**"]}},
            {"id":"D-8","question":"A fact","answer":"y","status":"confirmed","remember_as":"fact","path_counts":{"branch":"dev"}}]}"""))
        val (record, older, fact) = parsed.decisions
        val counts = record.pathCounts!!
        assertEquals("dev", counts.branch)
        assertEquals(mapOf("github/workflows/**" to 0, "docs/releasing.md" to 1), counts.appliesTo)
        // A negative or non-number count is left out rather than shown wrong.
        assertEquals(mapOf("vendor/**" to 0, "secrets/**" to 3), counts.forbids)
        assertNull("an older desktop sends no counts", older.pathCounts)
        assertNull("only a record has path counts", fact.pathCounts)
        // An Applies to path that matches nothing and a Never path that matches files are the warnings.
        assertEquals(RemDecisions.CountNote("matches no files", true), RemDecisions.countNote(false, counts.appliesTo["github/workflows/**"]))
        assertEquals(RemDecisions.CountNote("1 file", false), RemDecisions.countNote(false, counts.appliesTo["docs/releasing.md"]))
        assertEquals(RemDecisions.CountNote("matches 3 files that exist now", true), RemDecisions.countNote(true, counts.forbids["secrets/**"]))
        assertEquals(RemDecisions.CountNote("matches 1 file that exists now", true), RemDecisions.countNote(true, 1))
        assertEquals(RemDecisions.CountNote("no files now", false), RemDecisions.countNote(true, counts.forbids["vendor/**"]))
        assertEquals(RemDecisions.CountNote("", false), RemDecisions.countNote(false, null))
        // The Edit form warns about the typed paths it has counts for, and never about one typed since.
        assertEquals(listOf("Applies to github/workflows/** matches no files on dev.", "Never touch secrets/** matches 3 files that exist now."),
            RemDecisions.pathWarnings(counts, "github/workflows/**, docs/releasing.md, .github/workflows/**", "vendor/**, secrets/**"))
        assertEquals(emptyList<String>(), RemDecisions.pathWarnings(null, "github/workflows/**", "secrets/**"))
    }

    @Test fun taskUpdateCardsShowWhatAConflictReplaces() {
        val card = RemDecisions.parseCard(JSONObject("""{"id":"D-6","question":"Replace them?","recommended":"SQLite","status":"pending",
            "supersedes_all":["D-3","D-4"],"conflict":"They disagree."}"""))!!
        assertEquals(listOf("D-3", "D-4"), card.supersedesAll)
        assertEquals("They disagree.", card.conflict)
    }

    @Test fun recordEditSendsOnlyWhatChanged() {
        val record = (DesktopViews.parse(RemDecisions.READ, records) as RemDecisionList).decisions[1]
        val shown = RemDecisions.draft(record)
        assertEquals("src/sessions/**, x", shown.appliesTo)
        // Nothing changed: nothing to send.
        assertNull(RemDecisions.recordChangeParams("edit", record, 11, shown))
        val edit = RemDecisions.recordChangeParams("edit", record, 11,
            shown.copy(consequences = "  One database, backed up.  ", appliesTo = "src/sessions/**,\nmigrations/**, src/sessions/**"), note = " Phone ")!!
        assertEquals(setOf("action", "id", "expected_revision", "consequences", "applies_to", "note"), edit.keys().asSequence().toSet())
        assertEquals("edit", edit.getString("action"))
        assertEquals(11L, edit.getLong("expected_revision"))
        assertEquals("One database, backed up.", edit.getString("consequences"))
        assertEquals(listOf("src/sessions/**", "migrations/**"), edit.getJSONArray("applies_to").let { a -> (0 until a.length()).map(a::getString) })
        assertEquals("Phone", edit.getString("note"))
        // Clearing a list sends it empty; the answer can't be cleared.
        assertEquals(0, RemDecisions.recordChangeParams("edit", record, 11, shown.copy(forbids = " "))!!.getJSONArray("forbids").length())
        assertNull(RemDecisions.recordChangeParams("edit", record, 11, shown.copy(answer = " ", context = "new")))
        // A list too long is refused here, not cut.
        assertEquals("Keep it to 10 entries.", RemDecisions.listProblem((1..11).joinToString(",") { "p$it" }))
        assertEquals("Keep each entry to 200 characters.", RemDecisions.listProblem("x".repeat(201)))
        assertNull(RemDecisions.listProblem("a, b"))
        assertNull(RemDecisions.recordChangeParams("edit", record, 11, shown.copy(forbids = (1..11).joinToString(",") { "p$it" })))
        assertEquals(RemDecisions.SECTION_MAX, RemDecisions.recordChangeParams("edit", record, 11, shown.copy(context = "x".repeat(4000)))!!
            .getString("context").length)
        assertEquals(RemDecisions.CHECK_MAX, RemDecisions.recordChangeParams("edit", record, 11, shown.copy(check = "x".repeat(4000)))!!
            .getString("check").length)
    }

    @Test fun replaceMakesANewDecisionInPlaceOfOneInForce() {
        val parsed = DesktopViews.parse(RemDecisions.READ, records) as RemDecisionList
        val record = parsed.decisions[1]
        val start = RemDecisions.draft(record, answer = "")
        // Replace with… needs the new decision.
        assertNull(RemDecisions.recordChangeParams("replace", record, 11, start))
        val replace = RemDecisions.recordChangeParams("replace", record, 11,
            start.copy(answer = "Store sessions in Postgres.", why = "We run it already.", options = "Keep SQLite."))!!
        assertEquals(setOf("action", "id", "expected_revision", "answer", "why", "options"), replace.keys().asSequence().toSet())
        assertEquals("replace", replace.getString("action"))
        assertEquals("D-4", replace.getString("id"))
        assertEquals("Store sessions in Postgres.", replace.getString("answer"))
        // Only a decision in force can be replaced, and only Edit and Replace with… are built here.
        assertNull(RemDecisions.recordChangeParams("replace", parsed.decisions[0], 11, start.copy(answer = "x")))
        assertNull(RemDecisions.recordChangeParams("use", record, 11, start.copy(answer = "x")))
    }

    private fun home(json: String) = DesktopViews.parse("rem.home", JSONObject(json)) as RemHome

    @Test fun taskUpdateCardsReadTheirStatusNow() {
        val view = home("""{"decisions":true,"decisions_revision":8,"decisions_waiting":1,"messages":[
            {"id":"u1","role":"rem","kind":"task_update","body":"Waiting on your choice","work":{"task_id":"t","run_id":"r","thread_id":"th","title":"Checks",
              "decision":{"id":"D-3","question":"Which command?","recommended":"npm test","why":"package.json","status":"pending","live_status":"confirmed",
                "evidence":[{"kind":"file","ref":"package.json"}]}}},
            {"id":"u2","role":"rem","kind":"task_update","body":"Rem couldn't settle this","work":{"task_id":"t","run_id":"r2","thread_id":"th","title":"Deploy",
              "decision":{"fallback":true,"title":"Rem couldn't settle this. Here is what it found.","question":"Deploy now?","found":"deploy.sh","candidate":""}}},
            {"id":"u3","role":"rem","body":"Plain","work":{"task_id":"t","run_id":"r3","thread_id":"th","title":"Other"}}]}""")
        assertTrue(view.decisions)
        assertEquals(8L, view.decisionsRevision)
        assertEquals(1, view.decisionsWaiting)
        val card = view.messages[0].work!!.decision!!
        assertEquals("D-3", card.id)
        assertEquals("confirmed", card.liveStatus)
        // Answered since: the chat's status wins over the card's, so no buttons.
        val (status, revision) = RemDecisions.live(card, view, null)
        assertEquals("confirmed" to 8L, status to revision)
        assertEquals(RemDecisions.CardState.Decided(false), RemDecisions.cardState(card, status))
        // A newer list (after Use this on the Decisions screen) wins over an older chat read.
        val newer = RemDecisionList(9, 1, 0, 0, listOf((DesktopViews.parse(RemDecisions.READ, list) as RemDecisionList).decisions[0]))
        assertEquals("pending" to 9L, RemDecisions.live(card, view, newer))
        assertEquals(RemDecisions.CardState.Open(false), RemDecisions.cardState(card, "pending"))
        assertEquals(RemDecisions.CardState.Open(true), RemDecisions.cardState(card, "provisional"))
        assertEquals(RemDecisions.CardState.TurnedDown, RemDecisions.cardState(card, "rejected"))
        // Forgotten on the desktop: the newer list doesn't have it.
        val forgotten = RemDecisionList(10, 0, 0, 0, emptyList())
        assertEquals(null to 10L, RemDecisions.live(card, view, forgotten))
        assertEquals(RemDecisions.CardState.Gone, RemDecisions.cardState(card, null))
        // Forgotten while it waited: the desktop marks the card withdrawn, and the empty live status keeps it so.
        val withdrawn = card.copy(status = "withdrawn", liveStatus = "")
        assertEquals(null to 8L, RemDecisions.live(withdrawn, view, null))
        assertEquals(RemDecisions.CardState.Withdrawn, RemDecisions.cardState(withdrawn, null))
        assertEquals(RemDecisions.CardState.Withdrawn, RemDecisions.cardState(withdrawn.copy(liveStatus = null), "withdrawn"))

        val fallback = view.messages[1].work!!.decision!!
        assertTrue(fallback.fallback)
        assertEquals("Deploy now?", fallback.question)
        assertEquals("deploy.sh", fallback.found)
        assertEquals(RemDecisions.CardState.Fallback, RemDecisions.cardState(fallback, "pending"))
        assertNull(view.messages[2].work!!.decision)
    }

    @Test fun anOlderDesktopShowsTheCardAsWrittenWithoutButtons() {
        val view = home("""{"messages":[{"id":"u1","role":"rem","work":{"task_id":"t","run_id":"r","thread_id":"th","title":"Checks",
            "decision":{"id":"D-3","question":"Which command?","recommended":"npm test","status":"pending"}}}]}""")
        assertFalse(view.decisions)
        val card = view.messages[0].work!!.decision!!
        assertNull(card.liveStatus)
        // No revision to send, so the card offers no change.
        assertEquals("pending" to null, RemDecisions.live(card, view, null))
        assertFalse((DesktopViews.parse("overview", JSONObject()) as DesktopOverview).remDecisions)
        assertTrue((DesktopViews.parse("overview", JSONObject("""{"rem_decisions":true}""")) as DesktopOverview).remDecisions)
    }

    @Test fun aChangeAskedForInChatShowsBeforeAndAfterAndSendsOnlyItsId() {
        val view = home("""{"messages":[
            {"id":"q","role":"user","body":"Update D-7","decision_edits":[{"id":"E-9","decision":"D-7","status":"waiting"}]},
            {"id":"r","role":"rem","body":"Check it below.","decision_edits":[
                {"id":"E-2","decision":"D-7","action":"edit","question":"Where does local data live?","project":"Site","status":"waiting",
                 "changes":[{"field":"answer","label":"Decision","before":"SQLite","after":"Postgres"},{"field":"","label":"x"},
                            {"field":"consequences","label":"Consequences","before":"","after":"Run a server."}]},
                {"id":"E-1","decision":"D-6","action":"replace","status":"applied","result":"D-9","changes":[]},
                {"id":"E-0","decision":"D-5","status":"bogus"},
                {"decision":"D-4","status":"waiting"}]}]}""")
        // Only Rem's replies carry cards; a user's message never does.
        assertTrue(view.messages[0].decisionEdits.isEmpty())
        val (waiting, applied) = view.messages[1].decisionEdits
        assertEquals(2, view.messages[1].decisionEdits.size)
        assertEquals("Change D-7?", RemDecisions.editTitle(waiting))
        assertTrue(waiting.waiting)
        assertEquals(listOf("Decision" to ("SQLite" to "Postgres"), "Consequences" to ("" to "Run a server.")),
            waiting.changes.map { it.label to (it.before to it.after) })
        assertEquals("", RemDecisions.editSettled(waiting))
        assertEquals("Replace D-6?", RemDecisions.editTitle(applied))
        assertEquals("Applied: D-9 replaces D-6", RemDecisions.editSettled(applied))
        val ok = RemDecisions.editParams("E-2", true, 12)!!
        assertEquals(setOf("action", "id", "expected_revision"), ok.keys().asSequence().toSet())
        assertEquals("confirm_edit", ok.getString("action"))
        assertEquals("E-2", ok.getString("id"))
        assertEquals(12L, ok.getLong("expected_revision"))
        assertEquals("cancel_edit", RemDecisions.editParams("E-2", false, 12)!!.getString("action"))
        assertNull(RemDecisions.editParams(" ", true, 12))
    }

    @Test fun theDecisionsScreenListsOnlyChangesStillWaiting() {
        val listed = RemDecisions.parse(JSONObject("""{"revision":3,"decisions":[],"edits":[
            {"id":"E-2","decision":"D-7","status":"waiting","changes":[]},{"id":"E-1","decision":"D-6","status":"cancelled"}]}"""))
        assertEquals(listOf("E-2"), listed.edits.map { it.id })
        assertEquals("Cancelled. Nothing changed.", RemDecisions.editSettled(RemDecisionEdit("E-1", "D-6", "edit", "", "", emptyList(), "cancelled")))
        assertTrue(RemDecisions.parse(JSONObject("""{"revision":1,"decisions":[]}""")).edits.isEmpty())
    }
}
